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


@pytest.mark.parametrize("uv_tool", [True, False], ids=["uv-tool-install", "other-install"])
def test_an_environment_without_pip_refuses_naming_the_fix(monkeypatch, tmp_path, uv_tool):
    """pip is one of PersonalClaw's own dependencies, so an environment without it is an
    incomplete install, and the sentence says how to complete it.

    It surfaced first as ``No module named pip``, which named nothing to act on, and then as
    advice a Debian or Ubuntu system Python cannot follow: ``python -m ensurepip`` (stripped
    there), or the distribution's ``python3-pip``, which installs into the system's own packages
    and never into this environment. uv is on PATH here and is no way out: its ``--prefix``
    treats nothing as installed, so the install refuses before it starts anything."""
    from personalclaw import _installer

    monkeypatch.setattr(_installer, "_have_uv", lambda: True)
    monkeypatch.setattr(_installer, "_have_pip", lambda: False)
    if uv_tool:
        (tmp_path / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    monkeypatch.setattr("personalclaw.python_support.environment_root", lambda: tmp_path)

    def unreachable(cmd, **kw):  # pragma: no cover — must fail before spawning
        raise AssertionError(f"started {cmd} with no pip to install with")

    monkeypatch.setattr(app_manager.subprocess, "run", unreachable)
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(_manifest(["totally-not-a-real-pkg-xyz==9.9.9"]))

    fix = (
        "Reinstall PersonalClaw (`uv tool upgrade --reinstall personalclaw`) to put it back"
        if uv_tool
        else "Reinstall PersonalClaw into that environment to put it back"
    )
    assert str(ei.value) == (
        f"Couldn't install dep-app's Python packages: {sys.executable} has no `pip` module, "
        f"though pip is one of PersonalClaw's own dependencies. {fix}, then try again."
    )
    assert ei.value.log_excerpt == ""
