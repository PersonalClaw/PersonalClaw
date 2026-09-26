"""Per-app python-dependency mechanism (dep-shedding completion).

Core ships lean; an app declares the heavy libs it needs via
``manifest.dependencies.pythonDependencies`` and the installer pip-installs them into
``<home>/app-python`` (``apps/app_python.py``), which the gateway loads after its own
packages. ``tests/test_app_python_packages.py`` holds that contract end to end; this file
keeps the manifest round-trip and the installer-resolution cases.
"""

from __future__ import annotations

import sys

import pytest

from personalclaw.apps import app_manager
from personalclaw.apps.manifest import AppManifest


def _manifest(deps: list[str]) -> AppManifest:
    return AppManifest.from_dict(
        {
            "name": "dep-app",
            "version": "1.0.0",
            "dependencies": {"pythonDependencies": deps},
            "provider": {"type": "tool", "implementation": "provider:make"},
        }
    )


def test_manifest_parses_and_roundtrips_python_deps():
    m = _manifest(["faster-whisper>=1.0", "numpy>=1.21,<2"])
    assert m.dependencies.pythonDependencies == ["faster-whisper>=1.0", "numpy>=1.21,<2"]
    rt = AppManifest.from_dict(m.to_dict())
    assert rt.dependencies.pythonDependencies == m.dependencies.pythonDependencies


def test_no_deps_is_noop_no_restart():
    assert app_manager._install_python_deps(_manifest([])) == []


def test_already_satisfied_dep_needs_no_restart():
    # pytest itself is installed in the test venv → already satisfied → no restart.
    assert app_manager._install_python_deps(_manifest(["pytest"])) == []


def test_pip_failure_raises_lifecycle_error(monkeypatch):
    class _Fail:
        returncode = 1
        stdout = ""
        stderr = "could not find a version"

    monkeypatch.setattr(app_manager.subprocess, "run", lambda cmd, **kw: _Fail())

    with pytest.raises(app_manager.AppLifecycleError):
        app_manager._install_python_deps(_manifest(["totally-not-a-real-pkg-xyz==9.9.9"]))


# ── installer resolution for app packages (issues #46, #51) ─────────────────────────


def test_a_pip_less_venv_runs_pip_from_the_bundled_wheel_never_uv(monkeypatch):
    """The #46 repro, kept honest for the new target. A uv-created venv (`uv tool install
    personalclaw`, the recommended install) ships no pip module. App packages install into a
    separate --prefix resolved AGAINST the running environment, which uv's --prefix does not do
    (it treats nothing as installed), so the installer runs the pip wheel this Python's ensurepip
    bundles — even though uv is right there on PATH."""
    from personalclaw import _installer

    monkeypatch.setattr(_installer, "_have_uv", lambda: True)
    monkeypatch.setattr(_installer, "_have_pip", lambda: False)

    calls: list[list[str]] = []

    class _OK:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        app_manager.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _OK())[1]
    )
    # pip "succeeds" without installing, so the post-install check reports it — the argv is
    # what this test is about.
    with pytest.raises(app_manager.AppLifecycleError, match="still cannot be found"):
        app_manager._install_python_deps(_manifest(["totally-not-a-real-pkg-xyz==9.9.9"]))
    argv = calls[0]
    assert argv[0] == sys.executable and argv[1].endswith(".whl/pip"), argv
    assert argv[2] == "install" and "uv" not in argv
    assert "--disable-pip-version-check" in argv


def test_no_installer_raises_actionable_lifecycle_error(monkeypatch):
    """Previously surfaced as ``pip install failed …: No module named pip``, which named
    nothing the user could act on."""
    from personalclaw import _installer

    monkeypatch.setattr(_installer, "_have_uv", lambda: False)
    monkeypatch.setattr(_installer, "_have_pip", lambda: False)
    monkeypatch.setattr(_installer, "_bundled_pip_wheel", lambda: None)

    def unreachable(cmd, **kw):  # pragma: no cover — must fail before spawning
        raise AssertionError("attempted a subprocess with no installer available")

    monkeypatch.setattr(app_manager.subprocess, "run", unreachable)
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(_manifest(["totally-not-a-real-pkg-xyz==9.9.9"]))
    message = str(ei.value)
    assert message.startswith("Couldn't install dep-app's Python packages")
    assert "ensurepip" in message and sys.executable in message
    assert ei.value.log_excerpt == ""
