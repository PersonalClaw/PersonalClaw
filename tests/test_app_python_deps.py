"""Per-app python-dependency mechanism (dep-shedding completion).

Core ships lean; an app declares the heavy libs it needs via
``manifest.dependencies.pythonDependencies`` and the installer pip-installs them
into the shared venv. A newly-installed dep ⇒ the gateway must restart to import
it (surfaced via ``InstallResult.restart_required``).
"""

from __future__ import annotations

from personalclaw.apps import app_manager
from personalclaw.apps.manifest import AppManifest


def _manifest(deps: list[str]) -> AppManifest:
    return AppManifest.from_dict({
        "name": "dep-app",
        "version": "1.0.0",
        "dependencies": {"pythonDependencies": deps},
        "provider": {"type": "tool", "implementation": "provider:make"},
    })


def test_manifest_parses_and_roundtrips_python_deps():
    m = _manifest(["faster-whisper>=1.0", "numpy>=1.21,<2"])
    assert m.dependencies.pythonDependencies == ["faster-whisper>=1.0", "numpy>=1.21,<2"]
    rt = AppManifest.from_dict(m.to_dict())
    assert rt.dependencies.pythonDependencies == m.dependencies.pythonDependencies


def test_no_deps_is_noop_no_restart():
    assert app_manager._install_python_deps(_manifest([])) is False


def test_already_satisfied_dep_needs_no_restart():
    # pytest itself is installed in the test venv → already satisfied → no restart.
    assert app_manager._install_python_deps(_manifest(["pytest"])) is False


def test_missing_dep_triggers_pip_and_restart(monkeypatch):
    # Stub the pip subprocess so the test never hits the network; assert the
    # installer reports restart_required for a package that isn't present.
    calls: list[list[str]] = []

    class _OK:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _OK()

    monkeypatch.setattr(app_manager.subprocess, "run", fake_run)
    result = app_manager._install_python_deps(_manifest(["totally-not-a-real-pkg-xyz==9.9.9"]))
    assert result is True
    assert calls and "pip" in calls[0] and "install" in calls[0]
    assert "totally-not-a-real-pkg-xyz==9.9.9" in calls[0]


def test_pip_failure_raises_lifecycle_error(monkeypatch):
    class _Fail:
        returncode = 1
        stdout = ""
        stderr = "could not find a version"

    monkeypatch.setattr(app_manager.subprocess, "run", lambda cmd, **kw: _Fail())
    import pytest
    with pytest.raises(app_manager.AppLifecycleError):
        app_manager._install_python_deps(_manifest(["totally-not-a-real-pkg-xyz==9.9.9"]))
