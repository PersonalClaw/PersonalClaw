"""A sidecar app's engine installs from the UI, into the app's own Python environment.

What stood in the way on main, measured by the apps/models lane on Voice Clone TTS:

* nothing could install the engine but a shell: no surface called the install route, and the
  one hook an app has (``setup.onInstall``) is capped at 60 seconds;
* the install route installed the manifest's ``pythonDependencies`` — the same list the app
  installer puts in the gateway's own ``app-python`` — so an app could not declare its engine
  anywhere without also putting torch next to core;
* a long pip install showed nothing until it ended, and cancelling it stopped nothing.

The engine is now ``dependencies.sidecarDependencies``, installed only into ``apps/<app>/venv``
by Install engine, with pip's output read as it is written, a budget in hours, a cancel that
stops pip, and an update or removal of the app held until it is done.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import app_manager, manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.dashboard import model_downloads as M
from personalclaw.local_models import sidecar
from tests.test_app_python_packages import _wheel

APP = "clone-voice"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def rechecked(monkeypatch) -> list[str]:
    """The apps a finished install asked to be measured again, in place of the real measuring."""
    from personalclaw.providers import availability

    asked: list[str] = []
    monkeypatch.setattr(
        availability, "get_availability_board", lambda: SimpleNamespace(recheck=asked.append)
    )
    return asked


def _manifest(**extra) -> dict:
    return {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Clone Voice",
        "description": "speaks in a cloned voice",
        "provider": {
            "type": "model",
            "implementation": "provider:create_provider",
            "execution": "sidecar",
        },
        **extra,
    }


def _installed(manifest: dict) -> Path:
    """The app on disk the way the installer leaves it — enough for the engine install."""
    live = manager.app_dir(APP)
    (live / "data").mkdir(parents=True, exist_ok=True)
    (live / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return live


def _stub_python(venv: Path, body: str) -> Path:
    """A venv whose interpreter is a shell script: pip is whatever *body* does."""
    python = sidecar.venv_python(venv)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\n" + textwrap.dedent(body), encoding="utf-8")
    python.chmod(0o755)
    (venv / sidecar._MARKER).write_text("{}\n", encoding="utf-8")
    return python


def _wait(pred, *, timeout: float = 20.0, what: str = "condition") -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"{what} never held within {timeout}s")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); reap-state is the parent's business, so ask ps.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


# ── what the engine is ───────────────────────────────────────────────────────────────────────


def test_the_engine_is_its_own_list_not_the_gateways_packages():
    """On main the engine install read ``pythonDependencies``: the list that goes into the
    gateway's own ``app-python``. An app that needs a package in the gateway (the weights
    download) and an engine in its child could not say both."""
    _installed(
        _manifest(
            dependencies={
                "pythonDependencies": ["huggingface-hub>=0.23"],
                "sidecarDependencies": ["omnivoice>=0.2"],
            }
        )
    )

    install = sidecar.SidecarInstall.for_app(APP)

    assert install is not None
    assert install.requirements == ["omnivoice>=0.2"]
    assert install.status()["requirements"] == ["omnivoice>=0.2"]


def test_a_requirement_pip_would_read_as_an_option_never_reaches_pip(tmp_path):
    """The list becomes pip's argv. A manifest carrying an option is refused at install, and
    the installer checks again and ends pip's options with ``--`` before the list."""
    bad = AppManifest.from_dict(
        _manifest(dependencies={"sidecarDependencies": ["--index-url=https://example.invalid/"]})
    )
    assert any("not a requirement pip can read" in e for e in bad.validate()), bad.validate()

    record = tmp_path / "argv.txt"
    venv = tmp_path / "venv"
    _stub_python(venv, f'printf "%s\\n" "$@" > {record}\n')
    install = sidecar.SidecarInstall(
        APP, requirements=["--index-url=https://example.invalid/"], venv=venv
    )
    assert install.run() is False
    assert "not a requirement pip can read" in install.error
    assert not record.exists(), "pip ran with an option for a requirement"

    fine = sidecar.SidecarInstall(APP, requirements=["omnivoice>=0.2"], venv=venv)
    assert fine.run() is True
    argv = record.read_text().split("\n")
    assert argv[argv.index("--") + 1 :] == ["omnivoice>=0.2", ""]


def test_an_engine_needs_a_sidecar_provider_to_go_into():
    plain = _manifest(dependencies={"sidecarDependencies": ["omnivoice"]})
    plain["provider"] = {"type": "model", "implementation": "provider:create_provider"}
    errors = AppManifest.from_dict(plain).validate()
    assert any('provider.execution: "sidecar"' in e for e in errors), errors
    assert (
        AppManifest.from_dict(
            _manifest(dependencies={"sidecarDependencies": ["omnivoice"]})
        ).validate()
        == []
    )


# ── installing it for real ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_install_engine_puts_the_engine_where_the_sidecar_runs_it(tmp_path):
    """Real venv, real pip, an offline wheel: the job ends ``done`` and the sidecar's own
    interpreter imports the engine, which neither the gateway nor ``app-python`` has."""
    wheel = _wheel(tmp_path / "wheels", "pclaw-fixture-engine", "1.0")
    spec = f"pclaw-fixture-engine @ {wheel.as_uri()}"
    live = _installed(_manifest(dependencies={"sidecarDependencies": [spec]}))
    reg = M.ModelDownloadRegistry()

    job, err = reg.start_install(APP)
    assert err is None and job is not None
    await _settle(lambda: reg.install_job(APP).state in ("done", "error"), timeout=240)

    status = reg.install(APP).status()
    assert reg.install_job(APP).state == "done", status
    assert [s["status"] for s in status["steps"]] == ["done", "done", "skipped"]
    assert status["installed"] and status["managed"]
    engine = subprocess.run(
        [
            str(sidecar.venv_python(live / "venv")),
            "-c",
            "import pclaw_fixture_engine as e; print(e.VERSION)",
        ],
        capture_output=True,
        text=True,
    )
    assert engine.stdout.strip() == "1.0", engine.stderr
    gateway = subprocess.run(
        [sys.executable, "-c", "import pclaw_fixture_engine"], capture_output=True, text=True
    )
    assert gateway.returncode != 0, "the engine landed where the gateway imports from"
    assert not list(
        (tmp_path / "app-python").rglob("pclaw_fixture_engine")
    ), "it went into app-python"


# ── what the user sees while it runs ─────────────────────────────────────────────────────────


def test_pips_output_shows_while_it_is_still_running(tmp_path):
    """A twenty-minute pip used to show nothing until it ended: the log tail was read after
    the process exited."""
    release = tmp_path / "release"
    venv = tmp_path / "venv"
    _stub_python(
        venv,
        f"""\
        echo "Collecting omnivoice>=0.2"
        echo "Downloading torch-2.5.1-cp313-none-macosx_11_0_arm64.whl (63.5 MB)"
        while [ ! -f {release} ]; do sleep 0.05; done
        echo "Successfully installed omnivoice-0.2.0 torch-2.5.1"
        """,
    )
    install = sidecar.SidecarInstall(APP, requirements=["omnivoice>=0.2"], venv=venv)
    runner = threading.Thread(target=install.run_one, args=("deps",), daemon=True)
    runner.start()
    try:
        _wait(
            lambda: any("Downloading torch" in line for line in install.log_tail),
            what="pip's output in the log while pip runs",
        )
        assert runner.is_alive(), "the step finished before the log was read"
        step = next(s for s in install.status()["steps"] if s["name"] == "deps")
        assert step["status"] == "running" and step["started_at"] > 0
    finally:
        release.write_text("go")
        runner.join(timeout=20)
    assert install.status()["steps"][1]["status"] == "done"
    assert install.log_tail[-1] == "Successfully installed omnivoice-0.2.0 torch-2.5.1"


def test_the_engines_pip_keeps_no_cache_in_the_users_home(tmp_path):
    """An engine installs into the app's own folder, so pip must not keep a copy in the user's
    home either: pip's cache is ``~/Library/Caches/pip`` (``~/.cache/pip``), and an app package
    install was measured filling it. pip is told through its environment, which the
    pip it runs to build an sdist's build requirements inherits too, where a flag would not."""
    venv = tmp_path / "venv"
    _stub_python(venv, 'echo "PIP_NO_CACHE_DIR=${PIP_NO_CACHE_DIR:-unset}"\n')
    install = sidecar.SidecarInstall(APP, requirements=["omnivoice>=0.2"], venv=venv)

    assert install.run_one("deps"), install.status()
    assert "PIP_NO_CACHE_DIR=1" in install.log_tail, install.log_tail


@pytest.fixture
def one_install(monkeypatch, tmp_path):
    """A registry whose install for the app is this one object, so a test drives the job
    through the registry on an install whose pip it wrote."""
    install = sidecar.SidecarInstall(APP, requirements=["omnivoice>=0.2"], venv=tmp_path / "venv")
    monkeypatch.setattr(M.ModelDownloadRegistry, "install", lambda self, provider: install)
    return M.ModelDownloadRegistry(), install


@pytest.mark.asyncio
async def test_cancel_stops_pip_and_the_next_install_resumes(tmp_path, one_install):
    """On main a cancel detached the job and left pip running on its worker thread."""
    reg, install = one_install
    pidfile = tmp_path / "pip.pid"
    _stub_python(install.venv, f'echo $$ > {pidfile}\necho "Downloading torch"\nsleep 60\n')
    job, _ = reg.start_install(APP)
    await _settle(lambda: pidfile.exists() and pidfile.read_text().strip().isdigit())
    pid = int(pidfile.read_text())

    assert reg.cancel(job.id) is True
    await _settle(lambda: not _alive(pid), timeout=15)

    await _settle(lambda: install.status()["steps"][1]["status"] == "cancelled")
    assert install.reason == "cancelled"
    assert "Install engine" in install.remediation
    assert not (install.venv / ".personalclaw-deps.json").exists(), "a cancelled pip left a receipt"

    _stub_python(install.venv, 'echo "Successfully installed omnivoice-0.2.0"\n')
    again, err = reg.start_install(APP)
    assert err is None
    await _settle(lambda: reg.install_job(APP).state == "done")
    status = install.status()
    assert status["installed"] and status["error"] == "" and status["reason"] == ""
    assert [s["status"] for s in status["steps"]] == ["skipped", "done", "skipped"]


def test_a_pip_past_its_budget_is_stopped_and_says_so(tmp_path, monkeypatch):
    """The budget is hours, because an engine is torch-sized; past it, pip is killed with its
    children, not left running behind a failed step."""
    assert sidecar.DEPS_TIMEOUT_SECS >= 60 * 60
    monkeypatch.setattr(sidecar, "DEPS_TIMEOUT_SECS", 1)
    pidfile = tmp_path / "pip.pid"
    venv = tmp_path / "venv"
    _stub_python(venv, f"echo $$ > {pidfile}\nsleep 60\n")
    install = sidecar.SidecarInstall(APP, requirements=["omnivoice>=0.2"], venv=venv)

    assert install.run_one("deps") is False

    assert install.reason == "timeout"
    assert install.error == "pip was still running after 1 second and was stopped (timed out)"
    _wait(lambda: not _alive(int(pidfile.read_text())), timeout=10, what="the timed-out pip gone")


@pytest.mark.asyncio
async def test_an_install_after_a_failed_one_does_not_keep_showing_the_failure(one_install):
    """On main a run's error stayed on the install after the next run succeeded, so the
    status read ``done`` beside "No matching distribution"."""
    reg, install = one_install
    _stub_python(
        install.venv, 'echo "ERROR: No matching distribution found for omnivoice" >&2\nexit 1\n'
    )
    reg.start_install(APP)
    await _settle(lambda: reg.install_job(APP).state == "error")
    failed = install.status()
    assert failed["reason"] == "pip_failed" and "No matching distribution" in failed["error"]

    _stub_python(install.venv, 'echo "Successfully installed omnivoice-0.2.0"\n')
    reg.start_install(APP)
    await _settle(lambda: reg.install_job(APP).state == "done")

    status = install.status()
    assert (status["error"], status["reason"], status["remediation"]) == ("", "", "")


# ── the registry keeps up with the app ───────────────────────────────────────────────────────


def test_the_install_follows_an_update_that_changes_the_engine():
    _installed(_manifest(dependencies={"sidecarDependencies": ["omnivoice>=0.2"]}))
    reg = M.ModelDownloadRegistry()
    assert reg.install(APP).requirements == ["omnivoice>=0.2"]

    _installed(_manifest(dependencies={"sidecarDependencies": ["omnivoice>=0.3", "vocos"]}))

    assert reg.install(APP).requirements == ["omnivoice>=0.3", "vocos"]


@pytest.mark.asyncio
async def test_a_finished_install_measures_the_app_again(rechecked, tmp_path):
    """The card read "The engine is not installed" until something re-measured it."""
    live = _installed(_manifest(dependencies={"sidecarDependencies": ["omnivoice>=0.2"]}))
    _stub_python(live / "venv", 'echo "Successfully installed omnivoice-0.2.0"\n')
    reg = M.ModelDownloadRegistry()
    reg.start_install(APP)
    await _settle(lambda: reg.install_job(APP).state == "done")
    assert rechecked == [APP]


@pytest.mark.asyncio
async def test_the_status_names_what_it_installs_and_the_job_to_cancel():
    from personalclaw.dashboard.handlers import model_downloads as H

    _installed(_manifest(dependencies={"sidecarDependencies": ["omnivoice>=0.2"]}))
    reg = M.ModelDownloadRegistry()
    req = SimpleNamespace(
        match_info={"provider": APP}, app={"state": SimpleNamespace(model_downloads=lambda: reg)}
    )

    idle = json.loads((await H.api_sidecar_install_status(req)).body)
    assert idle["requirements"] == ["omnivoice>=0.2"]
    assert idle["job"]["state"] == "idle" and idle["job"]["id"] == ""


# ── an update or a removal waits for it ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_update_or_a_removal_waits_for_a_running_engine_install(tmp_path, monkeypatch):
    from personalclaw.apps import backend_runtime
    from personalclaw.dashboard.handlers.apps import register_app_routes

    src = tmp_path / "v1" / APP
    src.mkdir(parents=True)
    plain = {"name": APP, "version": "1.0.0", "displayName": "Clone Voice", "description": "x"}
    (src / "app.json").write_text(json.dumps(plain), encoding="utf-8")
    assert app_manager.install(src, confirm=True).ok
    v2 = tmp_path / "v2" / APP
    v2.mkdir(parents=True)
    (v2 / "app.json").write_text(json.dumps({**plain, "version": "2.0.0"}), encoding="utf-8")

    release = tmp_path / "release"
    venv = manager.app_dir(APP) / "venv"
    _stub_python(venv, f"while [ ! -f {release} ]; do sleep 0.05; done\n")
    install = sidecar.SidecarInstall(APP, requirements=["omnivoice>=0.2"], venv=venv)
    monkeypatch.setattr(M.ModelDownloadRegistry, "install", lambda self, provider: install)
    reg = M.ModelDownloadRegistry()
    backend_runtime._supervisor = backend_runtime.BackendSupervisor()
    app = web.Application()
    register_app_routes(app)
    app["state"] = SimpleNamespace(model_downloads=lambda: reg)
    async with TestClient(TestServer(app)) as client:
        try:
            reg.start_install(APP)
            await _settle(lambda: install.status()["steps"][1]["status"] == "running")

            update = await client.post(f"/api/apps/{APP}/update", json={"source": str(v2)})
            body = await update.json()
            assert update.status == 409, body
            assert body["reason"] == "engine_installing" and body["ok"] is False
            assert body["error"].startswith("Clone Voice is installing its engine")
            assert manager._read_installed(APP).version == "1.0.0"

            for flag in ("remove=1", "force=1"):
                removal = await client.delete(f"/api/apps/{APP}?{flag}")
                assert removal.status == 409, flag
            assert manager.app_dir(APP).is_dir()
            # Turning it off leaves the folder where pip is writing, so it does not wait.
            assert (await client.delete(f"/api/apps/{APP}")).status == 200

            release.write_text("go")
            await _settle(lambda: reg.install_job(APP).state == "done")
            after = await client.post(f"/api/apps/{APP}/update", json={"source": str(v2)})
            assert after.status == 200, await after.json()
        finally:
            release.write_text("go")
            backend_runtime.get_backend_supervisor().stop_all()


async def _settle(pred, *, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition never held within {timeout}s")
