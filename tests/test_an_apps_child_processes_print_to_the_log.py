"""What an app's backend, background worker and engine print reaches the gateway's log, masked
and bounded, and its last lines outlive the process on the app's panel.

They ran with stdout and stderr discarded, so a crashing app left no clue anywhere: not in
``gateway.log``, not in Settings › Diagnostics, not on the app's panel, which said only "not
running". Each line is now relayed (``child_output``): tagged with the app, the process and its
pid; masked by shape and, for every credential the child was handed, by value; kept on one line;
and held to a burst and then a rate per process, with what the log did not take counted.

Claims about processes are made with real processes, as the supervisors' own suites make them.
Every wait polls to a deadline; nothing sleeps in place of a condition.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from personalclaw import log_sinks
from personalclaw.apps import backend_runtime, manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.worker_runtime import WorkerSupervisor
from personalclaw.local_models.sidecar import (
    SidecarCrashed,
    SidecarInstall,
    SidecarRunner,
    run_once,
)
from tests.test_app_api import _app_src, _client, _consented_install

#: The documented example access key: a credential shape the log's mask knows.
_KEY = "AKIAIOSFODNN7EXAMPLE"

#: A spawn goes through a fresh interpreter and the ceiling shim; under a loaded xdist run that
#: can take many seconds, so the deadlines are generous (the supervisors' suites use the same).
_WAIT_SECS = 90.0

#: How long a line may take to be relayed once the process that printed it has ended.
_RELAY_SECS = 30.0


def _wait_for(predicate: Callable[[], bool], *, what: str, timeout: float = _WAIT_SECS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Apps, their data and their secrets live under a tmp home."""
    from personalclaw.config import loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def gateway_log(tmp_path: Path) -> Iterator[Path]:
    """The gateway's real ``gateway.log`` sink (``cli._gateway_log_handler`` attached through
    ``log_sinks``, which also decides what every sink shows), at the INFO level an owner can
    choose in Settings › Diagnostics. Detached, and the level put back, afterwards."""
    from personalclaw.cli import _gateway_log_handler

    path = tmp_path / "gateway.log"
    handler = _gateway_log_handler(path)
    root = logging.getLogger()
    was_level, was_root = log_sinks.level(), root.level
    log_sinks.attach(handler)
    log_sinks.set_level(logging.INFO)
    try:
        yield path
    finally:
        log_sinks.detach(handler)
        handler.close()
        log_sinks.set_level(was_level)
        root.setLevel(was_root)


def _lines_about(log: Path, label: str) -> list[str]:
    """The lines of *log* the relay wrote for *label* (``app <name> <process>``)."""
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if label in line]


def _install_worker_app(home: Path, app: str, body: str) -> AppManifest:
    """An installed, enabled app that holds ``backgroundTasks`` and ships *body* as its
    worker (the real declaration: ``worker.py`` beside ``app.json``)."""
    appdir = home / "apps" / app
    appdir.mkdir(parents=True, exist_ok=True)
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": app,
                "version": "1.0.0",
                "displayName": app,
                "description": "worker fixture",
                "permissions": {"backgroundTasks": True},
            }
        ),
        encoding="utf-8",
    )
    (appdir / "installed.json").write_text(
        json.dumps({"name": app, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )
    (appdir / "worker.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return AppManifest.from_json_file(appdir / "app.json")


@pytest.fixture
def workers() -> Iterator[WorkerSupervisor]:
    supervisor = WorkerSupervisor()
    try:
        yield supervisor
    finally:
        supervisor.stop_all()


# ══ a worker ══


def test_a_workers_lines_reach_the_gateway_log_masked_and_its_last_lines_outlive_it(
    tmp_path: Path, gateway_log: Path, workers: WorkerSupervisor
) -> None:
    """Both streams, tagged with the app and the worker; a key it printed masked; the reason
    it exited in one line of its own; and, after it is gone, its last lines on its panel."""
    manifest = _install_worker_app(
        tmp_path,
        "feedwatch",
        f"""
        import sys
        print("polling 3 feeds", flush=True)
        print("signed in with {_KEY}", file=sys.stderr, flush=True)
        raise RuntimeError("the feed host refused the connection")
        """,
    )
    (rec,) = workers.start(manifest)
    _wait_for(lambda: not rec.is_alive(), what="the worker to exit")
    label = "app feedwatch worker 'worker'"
    _wait_for(
        lambda: any("exited with code 1" in line for line in _lines_about(gateway_log, label)),
        what="the worker's exit to reach gateway.log",
        timeout=_RELAY_SECS,
    )

    written = gateway_log.read_text(encoding="utf-8")
    lines = _lines_about(gateway_log, label)
    pid = f"(pid {rec.pid})"
    assert any(
        f"INFO personalclaw.child_output: {label} {pid} stdout: polling 3 feeds" in line
        for line in lines
    ), lines
    assert any(
        f"WARNING personalclaw.child_output: {label} {pid} stderr: signed in with "
        "[REDACTED: credential]" in line
        for line in lines
    ), lines
    assert any(f"{pid} stderr: Traceback (most recent call last):" in line for line in lines)
    assert any(
        f"{label} {pid} exited with code 1: RuntimeError: the feed host refused the connection"
        in line
        for line in lines
    ), lines
    assert _KEY not in written, "a key the worker printed reached gateway.log"

    (row,) = workers.report("feedwatch")
    assert row["running"] is False and row["state"] == "running", row
    ended = row["exit"]
    assert ended is not None, "the worker's last run is not on its panel"
    assert (ended["exitCode"], ended["ended"]) == (1, "exited with code 1"), ended
    assert ended["cause"] == "RuntimeError: the feed host refused the connection", ended
    assert ended["lines"][0] == "polling 3 feeds", ended["lines"]
    assert "signed in with [REDACTED: credential]" in ended["lines"], ended["lines"]
    assert ended["lines"][-1] == "RuntimeError: the feed host refused the connection"
    assert not any(_KEY in line for line in ended["lines"]), ended["lines"]


def test_a_flooding_worker_is_bounded_and_never_waits_on_its_pipe(
    tmp_path: Path, gateway_log: Path, workers: WorkerSupervisor
) -> None:
    """Fifty thousand lines as fast as it can write them: far more than a pipe holds, so a child
    whose output were not drained would stop at its first full pipe. The log takes the burst,
    then counts the rest in a note; the panel still has the true last lines."""
    from personalclaw import child_output

    flood = 50_000
    manifest = _install_worker_app(
        tmp_path,
        "chatterbox",
        f"""
        import sys
        for i in range({flood}):
            sys.stderr.write(f"progress line {{i}}\\n")
        sys.stderr.write("the last line\\n")
        """,
    )
    (rec,) = workers.start(manifest)
    _wait_for(lambda: rec.proc is not None and rec.proc.poll() is not None, what="the flood to end")
    label = "app chatterbox worker 'worker'"
    _wait_for(
        lambda: any(f"(pid {rec.pid}) exited" in line for line in _lines_about(gateway_log, label)),
        what="the flood's exit to reach gateway.log",
        timeout=_RELAY_SECS,
    )

    lines = _lines_about(gateway_log, label)
    relayed = [line for line in lines if " stderr: " in line]
    notes = [line for line in lines if "more lines it printed are not in the log" in line]
    assert len(relayed) <= child_output.LOG_BURST + 2, f"the log took {len(relayed)} lines"
    assert notes, "the lines the log did not take were not counted"
    not_logged = sum(int(m.group(1)) for n in notes if (m := re.search(r": (\d+) more lines", n)))
    assert len(relayed) + not_logged == flood + 1, (len(relayed), not_logged)
    assert len(lines) <= child_output.LOG_BURST + 10, f"{len(lines)} records for one flood"

    (row,) = workers.report("chatterbox")
    assert row["exit"]["exitCode"] == 0 and row["exit"]["lines"][-1] == "the last line", row
    assert len(row["exit"]["lines"]) == child_output.TAIL_LINES


# ══ a backend, end to end through the gateway's routes ══

_CRASHING_BACKEND = """
import os, sys
print("serving with " + os.environ["PERSONALCLAW_APP_SECRET"], file=sys.stderr, flush=True)
import feedparser_not_installed_here
"""


@pytest.mark.asyncio
async def test_a_backend_that_crashes_says_why_on_its_panel_and_in_the_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The app's panel said "not running" and nothing else, and the Doctor sent the owner to
    Live logs, which held none of the backend's lines. Its proxy secret, which it was handed
    and printed, has no shape a mask could recognise: it is masked by value."""
    import asyncio

    from personalclaw.apps.app_secret import read_app_secret
    from personalclaw.resilience import doctor

    monkeypatch.delenv(backend_runtime.SKIP_ENV, raising=False)
    caplog.set_level(logging.WARNING)
    async with _client(tmp_path) as client:
        src = _app_src(
            tmp_path,
            "brokenfeed",
            backend={"entryPoint": "backend/server.py", "type": "python"},
            files={"backend/server.py": _CRASHING_BACKEND},
        )
        assert (await _consented_install(client, src)).status == 201
        supervisor = backend_runtime.get_backend_supervisor()
        for _ in range(int(_WAIT_SECS * 10)):
            if supervisor.get("brokenfeed") is None:
                break
            await asyncio.sleep(0.1)
        assert supervisor.get("brokenfeed") is None, "the backend never stopped"
        detail: dict = {}
        for _ in range(int(_RELAY_SECS * 10)):
            detail = await (await client.get("/api/apps/brokenfeed")).json()
            if detail.get("backendExit"):
                break
            await asyncio.sleep(0.1)
        ended = detail.get("backendExit")
        assert ended, f"the panel never said why the backend stopped: {detail}"
        assert detail["backendRunning"] is False
        cause = "ModuleNotFoundError: No module named 'feedparser_not_installed_here'"
        assert (ended["exitCode"], ended["cause"]) == (1, cause), ended
        assert ended["lines"][0] == "serving with [REDACTED: credential]", ended["lines"]
        assert ended["lines"][-1] == cause, ended["lines"]

        listed = (await (await client.get("/api/apps")).json())["apps"]
        (row,) = [a for a in listed if a["name"] == "brokenfeed"]
        assert row["backendExit"] == ended and row["workers"] == [], row

        secret = read_app_secret("brokenfeed")
        assert secret and len(secret) >= 8, "the backend was started without its secret"
        assert secret not in json.dumps(detail) and secret not in caplog.text
        assert f"app brokenfeed backend (pid {ended['pid']}) exited with code 1: {cause}" in (
            caplog.text
        )

        probe = await doctor._probe_apps(doctor.DoctorContext(home=tmp_path))
        assert not probe.ok
        assert probe.detail == f"1 backend not running (brokenfeed exited with code 1: {cause})"
        assert probe.evidence["backends"]["brokenfeed"]["exit"] == ended


def test_a_backend_stopped_on_purpose_leaves_no_exit_to_report(tmp_path: Path) -> None:
    """A stop is PersonalClaw's own doing: the panel does not say the backend failed, and the
    log does not say it exited."""
    from personalclaw.apps import app_manager

    src = Path(
        _app_src(
            tmp_path,
            "steady",
            backend={"entryPoint": "backend/server.py", "type": "python"},
            files={
                "backend/server.py": "import sys, time\nprint('up', file=sys.stderr, flush=True)\n"
                "while True:\n    time.sleep(0.05)\n"
            },
        )
    )
    app_manager.install(src, confirm=True)
    supervisor = backend_runtime.BackendSupervisor()
    manifest = AppManifest.from_json_file(manager.app_dir("steady") / "app.json")
    rb = supervisor.start(manifest)
    assert rb is not None and rb.output is not None
    try:
        _wait_for(lambda: rb.output.lines() == ["up"], what="the backend to say it is up")
        assert supervisor.last_exit("steady") is None, "a running backend has no exit"
    finally:
        assert supervisor.stop("steady")
    _wait_for(lambda: rb.output.exit is not None, what="the stopped backend's end to be seen")
    assert rb.output.exit.stopped is True
    assert rb.output.report() is None and supervisor.last_exit("steady") is None


# ══ an engine (the sidecar child), and its install ══

_ENGINE = f"""
import os
import sys


def load(**kw):
    return {{}}


def call(method, payload):
    if method == "say":
        print("loaded with {_KEY}", file=sys.stderr, flush=True)
        return {{"said": True}}
    if method == "die":
        print("the model file is truncated", file=sys.stderr, flush=True)
        os._exit(3)
    if method == "leak":
        print("token {_KEY}", file=sys.stderr, flush=True)
        os._exit(3)
    return {{}}
"""


def test_an_engines_lines_reach_the_log_masked_and_name_its_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    worker = tmp_path / "engine.py"
    worker.write_text(_ENGINE, encoding="utf-8")
    runner = SidecarRunner(app="fixture-engine", worker=worker, venv=tmp_path / "venv")
    caplog.set_level(logging.WARNING)
    try:
        assert runner.call("call", {"method": "say"}) == {"said": True}
        _wait_for(
            lambda: any("loaded with" in line for line in runner.log_tail),
            what="the engine's line to be kept",
        )
        assert runner.log_tail == ["loaded with [REDACTED: credential]"], runner.log_tail
        with pytest.raises(SidecarCrashed) as crashed:
            runner.call("call", {"method": "die"})
    finally:
        runner.stop()
    assert "loaded with [REDACTED: credential]" in crashed.value.detail, crashed.value.detail
    assert _KEY not in crashed.value.detail and _KEY not in "\n".join(runner.log_tail)
    assert "app fixture-engine engine (pid " in caplog.text
    assert "stderr: loaded with [REDACTED: credential]" in caplog.text
    assert _KEY not in caplog.text


@pytest.mark.asyncio
async def test_a_one_call_engines_lines_reach_the_log_masked_and_name_its_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    worker = tmp_path / "engine.py"
    worker.write_text(_ENGINE, encoding="utf-8")
    caplog.set_level(logging.WARNING)
    with pytest.raises(SidecarCrashed) as crashed:
        await run_once("fixture-engine", worker, "leak")
    assert crashed.value.reason == "exit_3"
    assert crashed.value.detail == "token [REDACTED: credential]", crashed.value.detail
    assert "stderr: token [REDACTED: credential]" in caplog.text
    assert "exited with code 3: token [REDACTED: credential]" in caplog.text
    assert _KEY not in caplog.text


def test_an_engine_installs_output_is_shown_masked(tmp_path: Path) -> None:
    """The install's status shows its last lines on the engine's card: an index address with a
    login in it is shown without the login."""
    install = SidecarInstall("fixture-engine", venv=tmp_path / "venv")
    said = "Looking in indexes: https://ada:hunter2-example@pypi.example.com/simple"
    install._run([sys.executable, "-c", f"print({said!r})"], label="pip", timeout=_WAIT_SECS)
    assert install.log_tail == [
        "Looking in indexes: https://[REDACTED: url credential]@pypi.example.com/simple"
    ], install.log_tail
    assert "hunter2-example" not in json.dumps(install.status())
