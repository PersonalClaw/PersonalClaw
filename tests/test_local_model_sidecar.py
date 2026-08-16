"""LMMV-5 — sidecar isolation, resumable installs, and the residency surface.

Success Criterion 1 is the reason this file exists, so it is proven the only way it can
be: with a **real child process that is really killed mid-call**. Nothing here simulates a
crash by returning a failure — a mocked crash would pass over the exact code that decides
whether a half-written frame is believed.

Four properties get pointed tests because each one is a bug that shipped somewhere before:

* a killed child raises a typed ``SidecarCrashed`` and the *next* call brings a new
  generation up, so search recovers with no gateway restart;
* a frame truncated by the kill is DISCARDED, never read as a complete result;
* a reply from generation N-1 arriving after a restart is fenced — request ids restart at 1
  in every child, so without the fence a zombie's answer satisfies a live request;
* the watchdog's decision is data, so a test asserts the outcome instead of sleeping.

Every filesystem path here is under ``tmp_path``; nothing reaches a real model dir, a real
venv, or ``~/.personalclaw``.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import signal
import textwrap
import threading
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard import model_downloads as M
from personalclaw.dashboard.handlers import model_downloads as H
from personalclaw.local_models import residency, sidecar
from personalclaw.local_models.provider import LocalModel, LocalModelProvider
from personalclaw.local_models.sidecar import SidecarCrashed, SidecarRunner, SidecarWorkerError

# ── the fixture worker: real app-side code, loaded by path into the child ──

_WORKER = '''
"""A fixture sidecar worker. Stdlib only, exactly like a real app's worker."""
import os
import time

_model = None


def load(**kw):
    global _model
    _model = kw.get("model", "fixture-model")
    return {"loaded": _model}


def call(method, payload):
    if method == "encode":
        if payload.get("hang"):
            time.sleep(600)  # the test kills us mid-encode
        return {"vector": [0.1, 0.2, 0.3], "model": _model}
    if method == "dribble":
        # Write HALF a frame straight to the protocol fd, then hang. This is what a
        # native library crashing mid-write leaves in the pipe.
        os.write(1, b'{"id": "1:1", "ok": true, "result": {"vector": [0.1, 0.2')
        time.sleep(600)
    if method == "half_frame":
        # The DANGEROUS shape: a frame that is COMPLETE, VALID JSON and carries the id the
        # parent is waiting on — but has no terminating newline, because the process died
        # before writing it. Only the newline rule can tell this from a real reply.
        os.write(1, b'{"id": "1:1", "ok": true, "result": {"vector": ["HALF"]}}')
        time.sleep(600)
    if method == "chatty":
        print("loky: forking 4 workers")  # a stray print must not corrupt the protocol
        return {"ok": True}
    if method == "boom":
        raise RuntimeError("worker exploded")
    return {"echo": payload}


def unload():
    global _model
    _model = None
'''


@pytest.fixture
def worker(tmp_path: Path) -> Path:
    path = tmp_path / "worker.py"
    path.write_text(_WORKER, encoding="utf-8")
    return path


@pytest.fixture
def runner(tmp_path: Path, worker: Path):
    """A runner with no venv (so it uses this interpreter) and a short call timeout."""
    r = SidecarRunner(
        app="fixture-embed",
        worker=worker,
        venv=tmp_path / "venv",
        restart_max=3,
        call_timeout=30.0,
    )
    yield r
    r.stop()


def _wait_dead(r: SidecarRunner, timeout: float = 5.0) -> None:
    """Block until the child is reaped, so `is_alive()` reflects the kill."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not r.is_alive():
            return
        time.sleep(0.02)
    raise AssertionError("child never died")


def _kill_soon(pid: int, delay: float = 0.4) -> threading.Timer:
    """Kill *pid* from another thread while the main thread blocks inside a call."""

    def _kill() -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone — the kill it was arming for happened another way

    timer = threading.Timer(delay, _kill)
    timer.daemon = True
    timer.start()
    return timer


# ── the protocol: five verbs against a real child ──


def test_the_five_verbs_round_trip_against_a_real_child(runner):
    assert runner.call("ping")["pong"] is True
    assert runner.call("load", {"model": "L6-v2"})["loaded"] == "L6-v2"
    assert runner.call("call", {"method": "encode"})["vector"] == [0.1, 0.2, 0.3]
    stat = runner.stat()
    assert stat["rss_mb"] > 0  # a live python process always has resident pages
    assert runner.call("unload")["unloaded"] is True
    assert runner.generation == 1  # one child served all five


def test_an_unknown_verb_is_refused_rather_than_guessed(runner):
    with pytest.raises(SidecarWorkerError) as caught:
        runner.call("teleport")
    assert caught.value.reason == "bad_request"
    assert runner.is_alive()  # a bad request is not a crash


def test_a_stray_print_from_a_native_lib_does_not_corrupt_the_protocol(runner):
    """The loky-style progress print goes to stderr, and the frame still parses."""
    assert runner.call("call", {"method": "chatty"})["ok"] is True
    assert runner.call("ping")["pong"] is True  # the stream is still in sync


def test_a_worker_exception_is_typed_and_does_not_burn_a_restart(runner):
    runner.ensure_started()
    with pytest.raises(SidecarWorkerError) as caught:
        runner.call("call", {"method": "boom"})
    assert caught.value.reason == "worker_error"
    assert runner.is_alive()
    assert runner.health()["consecutive_failures"] == 0
    assert runner.restarts == 0


# ── Success Criterion 1: killed mid-encode ──


def test_killed_mid_encode_raises_typed_crash_and_recovers_without_a_restart(runner):
    """The whole point of the sidecar, end to end.

    The gateway (this process) survives, the caller gets a typed ``SidecarCrashed``
    naming the signal, and the NEXT call brings generation 2 up and returns a real
    result — "search recovers without a restart".
    """
    runner.ensure_started()
    pid = runner.health()["pid"]
    _kill_soon(pid)

    with pytest.raises(SidecarCrashed) as caught:
        runner.call("call", {"method": "encode", "payload": {"hang": True}})

    assert caught.value.reason == f"signal_{int(signal.SIGKILL)}"
    assert caught.value.typed_reason == "sidecar_crashed:signal_9"
    assert caught.value.generation == 1
    assert os.getpid() != pid  # the child died; we are still here

    # Recovery: no restart, no re-registration, just the next call.
    assert runner.call("call", {"method": "encode"})["vector"] == [0.1, 0.2, 0.3]
    assert runner.generation == 2
    assert runner.restarts == 1


def test_a_frame_truncated_by_the_kill_is_never_read_as_a_result(runner):
    """A half-written reply must not become a plausible half-answer.

    The worker writes an unterminated JSON fragment to the protocol fd and hangs; the
    kill leaves exactly that in the pipe. The caller must get a crash, and the runner must
    say it threw the fragment away.
    """
    runner.ensure_started()
    _kill_soon(runner.health()["pid"], delay=0.6)

    with pytest.raises(SidecarCrashed):
        runner.call("call", {"method": "dribble"})

    assert any("truncated frame discarded" in line for line in runner.log_tail)


def test_a_valid_frame_with_no_terminating_newline_is_still_refused(runner):
    """The case only the newline rule can catch.

    An unterminated fragment that happens to be *valid JSON carrying the pending request
    id* is indistinguishable from a real reply by every other check — JSON parsing accepts
    it and the id matches. Believing it would hand the caller a fabricated result from a
    process that died before it finished writing. The caller must get the crash.
    """
    runner.ensure_started()
    _kill_soon(runner.health()["pid"], delay=0.6)

    with pytest.raises(SidecarCrashed) as caught:
        result = runner.call("call", {"method": "half_frame"})
        raise AssertionError(f"a half-written frame was believed: {result!r}")

    assert caught.value.reason == f"signal_{int(signal.SIGKILL)}"


def test_a_timeout_is_typed_and_reaps_the_hung_child(tmp_path, worker):
    r = SidecarRunner(app="slow", worker=worker, venv=tmp_path / "venv", call_timeout=0.5)
    try:
        with pytest.raises(SidecarCrashed) as caught:
            r.call("call", {"method": "encode", "payload": {"hang": True}})
        assert caught.value.reason == "timeout"
        _wait_dead(r)  # a hung child is not left behind
    finally:
        r.stop()


# ── the generation fence ──


def test_a_reply_from_the_previous_generation_is_discarded(runner):
    """The bug the counter exists for, in its exact interleaving.

    Request ids restart at 1 in every child, so a zombie from generation 1 can produce a
    reply whose id matches what the LIVE child is about to answer. The only thing that can
    tell them apart is the generation, so that is the only fence under test here: the
    frame carries the CURRENT id and must still be refused.
    """
    runner.ensure_started()
    old_generation = runner.generation
    os.kill(runner.health()["pid"], signal.SIGKILL)
    _wait_dead(runner)
    runner.ensure_started()
    assert runner.generation == old_generation + 1

    live_id = f"{runner.generation}:1"  # what the live child's first reply will be
    zombie = {"id": live_id, "ok": True, "result": {"vector": ["ZOMBIE"]}}
    assert runner.deliver(old_generation, zombie) is False
    assert runner.stale_replies == 1

    # The caller gets the LIVE child's answer, not the dead one's.
    assert runner.call("call", {"method": "encode"})["vector"] == [0.1, 0.2, 0.3]


def test_request_ids_carry_their_generation(runner):
    """So a zombie's reply cannot collide with a live request by sequence number alone."""
    runner.ensure_started()
    runner.call("ping")
    os.kill(runner.health()["pid"], signal.SIGKILL)
    _wait_dead(runner)
    runner.call("ping")  # respawns as generation 2, sequence restarts at 1
    assert runner.generation == 2
    # Same sequence number, different generation → different id namespace.
    assert runner.deliver(1, {"id": "1:1", "ok": True, "result": "stale"}) is False


def test_a_stat_frame_is_recorded_not_queued_as_a_reply(runner):
    """A stat frame answers no request; delivering one must not satisfy a pending call."""
    runner.ensure_started()
    assert runner.deliver(runner.generation, {"stat": {"rss_mb": 512.5, "pid": 1}}) is True
    assert runner.last_stat["rss_mb"] == 512.5
    assert runner.call("ping")["pong"] is True  # the stat frame did not answer this


# ── the watchdog, as data ──


def test_watchdog_decisions_are_inspectable(runner):
    assert runner.watchdog_sweep() == {
        "app": "fixture-embed",
        "action": "noop",
        "reason": "never_started",
    }
    runner.ensure_started()
    assert runner.watchdog_sweep()["reason"] == "alive"

    os.kill(runner.health()["pid"], signal.SIGKILL)
    _wait_dead(runner)
    decision = runner.watchdog_sweep()
    assert decision["action"] == "respawned"
    assert decision["generation"] == 2
    assert runner.is_alive()


def test_the_restart_budget_stops_an_endless_respawn_loop(tmp_path, worker):
    """A genuinely broken child must produce one honest error, not a busy-loop."""
    r = SidecarRunner(app="brittle", worker=worker, venv=tmp_path / "venv", restart_max=0)
    try:
        r.ensure_started()
        _kill_soon(r.health()["pid"], delay=0.3)
        with pytest.raises(SidecarCrashed):
            r.call("call", {"method": "encode", "payload": {"hang": True}})
        assert r.health()["budget_exhausted"] is True
        assert r.watchdog_sweep()["action"] == "budget_exhausted"
        with pytest.raises(SidecarCrashed) as caught:
            r.call("ping")
        assert caught.value.reason == "restart_budget_exhausted"
    finally:
        r.stop()


def test_restart_max_defaults_to_the_config_value(monkeypatch):
    """The knob is READ, not just declared (LMMV §9)."""
    monkeypatch.setattr(sidecar, "_restart_max_default", lambda: 7)
    r = SidecarRunner(app="x", worker=Path("/nonexistent/worker.py"))
    assert r.restart_max == 7


def test_the_config_default_reads_the_local_models_section():
    from personalclaw.config.loader import AppConfig

    assert AppConfig().local_models.sidecar_restart_max == sidecar._restart_max_default()


@pytest.mark.asyncio
async def test_acall_runs_the_blocking_protocol_off_the_event_loop(runner):
    assert (await runner.acall("ping"))["pong"] is True


def test_stopping_is_idempotent_and_leaves_no_child(runner):
    runner.ensure_started()
    runner.stop()
    runner.stop()
    assert runner.is_alive() is False


# ── execution mode: the default must stay in-process ──


def test_execution_defaults_to_in_process():
    """A sidecar default would silently change the runtime of every installed provider."""
    from personalclaw.apps.manifest import EXECUTION_IN_PROCESS, ProviderConfig

    assert EXECUTION_IN_PROCESS == "in-process"
    assert ProviderConfig().execution == "in-process"
    assert ProviderConfig.from_dict({"type": "model", "implementation": "p:make"}).execution == (
        "in-process"
    )


def test_execution_round_trips_and_an_in_process_manifest_grows_no_key():
    from personalclaw.apps.manifest import ProviderConfig

    plain = ProviderConfig(type="model", implementation="p:make")
    assert "execution" not in plain.to_dict()
    side = ProviderConfig.from_dict(
        {"type": "model", "implementation": "p:make", "execution": "sidecar"}
    )
    assert side.execution == "sidecar"
    assert side.to_dict()["execution"] == "sidecar"
    assert ProviderConfig.from_dict(side.to_dict()).execution == "sidecar"


def test_an_unknown_execution_mode_is_a_validation_error():
    from personalclaw.apps.manifest import ProviderConfig

    errors = ProviderConfig(type="model", implementation="p:make", execution="docker").validate()
    assert any("provider.execution must be one of" in e for e in errors)


def test_provider_types_and_the_handler_set_are_untouched():
    """§9: execution is a FIELD on the model type, never a new provider type."""
    from personalclaw.apps.manifest import PROVIDER_TYPES

    assert "sidecar" not in PROVIDER_TYPES
    assert "in-process" not in PROVIDER_TYPES


# ── every spawn in the module carries a ceiling (AST, not a description) ──


def test_every_spawn_in_sidecar_py_is_ceiling_wrapped():
    """Asserts the CODE, not a claim about it.

    ``test_spawn_ceiling_audit`` checks that each site is *described* as ceiling-wrapped in
    an allowlist; a description can be wrong. This walks the AST and requires the first
    positional argument of every ``Popen``/``run`` in this module to be a
    ``spawn_shim_argv(...)`` result, so a raw argv reds here even if the allowlist still
    says otherwise.
    """
    source = Path(sidecar.__file__).read_text(encoding="utf-8")
    spawns = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        callee = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if callee not in ("Popen", "run"):
            continue
        spawns += 1
        first = node.args[0] if node.args else None
        assert (
            isinstance(first, ast.Name) and first.id == "launch"
        ), f"spawn at line {node.lineno} does not pass a spawn_shim_argv-wrapped argv"
    assert spawns == 2, f"expected the child spawn + the install spawn, found {spawns}"


def test_the_child_harness_imports_no_core_package():
    """It runs in the app's venv, where ``personalclaw`` is not installed."""
    child = Path(sidecar.__file__).with_name("_sidecar_child.py")
    tree = ast.parse(child.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name.startswith("personalclaw")]


# ── resumable install jobs (§3.2) ──


def _fake_venv(root: Path, *, exit_code: int = 0, marker: bool = True) -> Path:
    """A venv whose ``bin/python`` is a stub script, so no real pip ever runs."""
    venv = root / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    python = venv / "bin" / "python"
    python.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    python.chmod(0o755)
    if marker:
        (venv / sidecar._MARKER).write_text("{}\n", encoding="utf-8")
    return venv


def test_install_steps_skip_the_work_that_is_already_done(tmp_path):
    venv = _fake_venv(tmp_path)
    install = sidecar.SidecarInstall("fixture-embed", requirements=[], venv=venv)
    assert install.run() is True
    statuses = {s["name"]: s["status"] for s in install.status()["steps"]}
    assert statuses == {"venv": "skipped", "deps": "skipped", "weights": "skipped"}
    assert install.installed is True
    assert install.managed is True


def test_an_install_is_idempotent_across_runs(tmp_path):
    venv = _fake_venv(tmp_path)
    install = sidecar.SidecarInstall("fixture-embed", requirements=["numpy"], venv=venv)
    assert install.run() is True  # the stub python exits 0 → receipt written
    assert json.loads((venv / ".personalclaw-deps.json").read_text()) == ["numpy"]
    again = sidecar.SidecarInstall("fixture-embed", requirements=["numpy"], venv=venv)
    assert again.run() is True
    assert [s["status"] for s in again.status()["steps"]][1] == "skipped"


def test_a_killed_pip_leaves_no_receipt_so_the_step_re_runs(tmp_path):
    """Resumability, at the one place it can silently break."""
    venv = _fake_venv(tmp_path, exit_code=1)
    install = sidecar.SidecarInstall("fixture-embed", requirements=["numpy"], venv=venv)
    assert install.run() is False
    assert not (venv / ".personalclaw-deps.json").exists()
    assert install.reason == "pip_failed"
    assert install.remediation  # the actionable next step, distinct from `error`
    assert install.installed is False
    assert install.log_tail == [] or isinstance(install.log_tail, list)


def test_the_weights_step_reads_the_shared_layout_probe(tmp_path):
    cache = tmp_path / "cache"
    (cache / "L6-v2").mkdir(parents=True)
    (cache / "L6-v2" / "model.safetensors").write_bytes(b"x" * 4096)
    install = sidecar.SidecarInstall(
        "fixture-embed",
        requirements=[],
        venv=_fake_venv(tmp_path),
        cache_root=cache,
        model="L6-v2",
    )
    assert install.run() is True
    assert install.status()["steps"][2]["status"] == "done"

    missing = sidecar.SidecarInstall(
        "fixture-embed",
        requirements=[],
        venv=_fake_venv(tmp_path),
        cache_root=cache,
        model="absent-model",
    )
    missing.run()
    assert missing.status()["steps"][2]["status"] == "pending"


def test_a_real_venv_is_created_and_marked_managed(tmp_path):
    """One end-to-end venv creation — the step that a stub can't prove."""
    install = sidecar.SidecarInstall("fixture-embed", requirements=[], venv=tmp_path / "venv")
    assert install.run_one("venv") is True
    assert sidecar.venv_python(tmp_path / "venv").is_file()
    assert install.managed is True


def test_an_unmanaged_venv_is_never_deleted(tmp_path):
    venv = _fake_venv(tmp_path, marker=False)
    install = sidecar.SidecarInstall("fixture-embed", venv=venv)
    assert install.managed is False
    assert install.delete() is False
    assert venv.exists()


def test_a_managed_venv_is_deleted(tmp_path):
    venv = _fake_venv(tmp_path)
    install = sidecar.SidecarInstall("fixture-embed", venv=venv)
    assert install.delete() is True
    assert not venv.exists()


def test_for_app_ignores_an_in_process_provider(tmp_path, monkeypatch):
    from personalclaw.apps import manager as app_manager

    app_root = tmp_path / "apps"
    monkeypatch.setattr(app_manager, "apps_dir", lambda: app_root)
    (app_root / "plain").mkdir(parents=True)
    (app_root / "plain" / "app.json").write_text(
        json.dumps(
            {
                "name": "plain",
                "version": "1.0.0",
                "displayName": "Plain",
                "description": "in-process",
                "provider": {"type": "model", "implementation": "provider:make"},
            }
        ),
        encoding="utf-8",
    )
    assert sidecar.SidecarInstall.for_app("plain") is None

    (app_root / "isolated").mkdir(parents=True)
    (app_root / "isolated" / "app.json").write_text(
        json.dumps(
            {
                "name": "isolated",
                "version": "1.0.0",
                "displayName": "Isolated",
                "description": "sidecar",
                "provider": {
                    "type": "model",
                    "implementation": "provider:make",
                    "execution": "sidecar",
                },
                "dependencies": {"pythonDependencies": ["sentence-transformers>=3"]},
            }
        ),
        encoding="utf-8",
    )
    found = sidecar.SidecarInstall.for_app("isolated")
    assert found is not None
    assert found.requirements == ["sentence-transformers>=3"]


# ── the install job rides the existing download registry ──


@pytest.fixture
def install_registry(tmp_path, monkeypatch):
    """A download registry whose sidecar install is a stubbed, instant one."""
    reg = M.ModelDownloadRegistry()
    install = sidecar.SidecarInstall("fixture-embed", requirements=[], venv=_fake_venv(tmp_path))
    monkeypatch.setattr(M.ModelDownloadRegistry, "install", lambda self, provider: install)
    return reg, install


async def _settle():
    for _ in range(40):
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_the_install_job_is_a_sidecar_install_kind_on_the_one_registry(install_registry):
    reg, _ = install_registry
    job, err = reg.start_install("fixture-embed")
    assert err is None and job is not None
    assert job.kind == "sidecar-install"
    await _settle()
    assert reg.install_job("fixture-embed").state == "done"
    assert reg.install_job("fixture-embed").progress == 1.0


@pytest.mark.asyncio
async def test_a_second_start_returns_the_in_flight_job(install_registry):
    reg, _ = install_registry
    first, _ = reg.start_install("fixture-embed")
    second, _ = reg.start_install("fixture-embed")
    assert first.id == second.id
    await _settle()


@pytest.mark.asyncio
async def test_a_failed_install_reports_the_typed_reason_on_the_job(tmp_path, monkeypatch):
    reg = M.ModelDownloadRegistry()
    install = sidecar.SidecarInstall(
        "fixture-embed", requirements=["numpy"], venv=_fake_venv(tmp_path, exit_code=1)
    )
    monkeypatch.setattr(M.ModelDownloadRegistry, "install", lambda self, provider: install)
    job, err = reg.start_install("fixture-embed")
    assert err is None
    await _settle()
    assert reg.install_job("fixture-embed").state == "error"
    assert reg.install_job("fixture-embed").reason == "pip_failed"


def test_starting_an_install_for_an_in_process_provider_is_refused():
    reg = M.ModelDownloadRegistry()
    job, err = reg.start_install("not-a-sidecar-app")
    assert job is None
    assert "no sidecar provider" in err


# ── HTTP surface ──


def _req(method, path, reg, *, body=None, match_info=None):
    app = web.Application()

    class _State:
        def model_downloads(self):
            return reg

    app["state"] = _State()
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def _payload(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_install_status_carries_steps_log_tail_and_remediation(install_registry):
    reg, _ = install_registry
    await H.api_sidecar_install_start(
        _req(
            "POST",
            "/api/models/sidecar/fixture-embed/install",
            reg,
            match_info={"provider": "fixture-embed"},
        )
    )
    await _settle()
    resp = await H.api_sidecar_install_status(
        _req(
            "GET",
            "/api/models/sidecar/fixture-embed/install/status",
            reg,
            match_info={"provider": "fixture-embed"},
        )
    )
    body = _payload(resp)
    assert body["provider"] == "fixture-embed"
    assert body["installed"] is True
    assert body["managed"] is True
    assert body["install_dir"].endswith("venv")
    job = body["job"]
    assert job["state"] == "done"
    assert [s["name"] for s in job["steps"]] == ["venv", "deps", "weights"]
    assert set(job) >= {"state", "steps", "log_tail", "error", "remediation", "weights_progress"}


@pytest.mark.asyncio
async def test_install_status_404s_for_a_provider_with_no_sidecar():
    reg = M.ModelDownloadRegistry()
    resp = await H.api_sidecar_install_status(
        _req("GET", "/x", reg, match_info={"provider": "plain-app"})
    )
    assert resp.status == 404


@pytest.mark.asyncio
async def test_delete_refuses_with_409_while_the_install_runs(tmp_path, monkeypatch):
    """Deleting the tree under a live pip is how a half-installed venv is born."""
    reg = M.ModelDownloadRegistry()
    install = sidecar.SidecarInstall("fixture-embed", requirements=[], venv=_fake_venv(tmp_path))
    monkeypatch.setattr(M.ModelDownloadRegistry, "install", lambda self, provider: install)

    started = threading.Event()
    release = threading.Event()

    def _slow(name):
        started.set()
        release.wait(5)
        return True

    monkeypatch.setattr(install, "run_one", _slow)
    reg.start_install("fixture-embed")
    for _ in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0.02)

    resp = await H.api_sidecar_install_delete(
        _req("DELETE", "/x", reg, match_info={"provider": "fixture-embed"})
    )
    assert resp.status == 409
    assert _payload(resp)["reason"] == "install_running"
    release.set()
    await _settle()


@pytest.mark.asyncio
async def test_delete_refuses_an_unmanaged_venv(tmp_path, monkeypatch):
    reg = M.ModelDownloadRegistry()
    install = sidecar.SidecarInstall("fixture-embed", venv=_fake_venv(tmp_path, marker=False))
    monkeypatch.setattr(M.ModelDownloadRegistry, "install", lambda self, provider: install)
    resp = await H.api_sidecar_install_delete(
        _req("DELETE", "/x", reg, match_info={"provider": "fixture-embed"})
    )
    assert resp.status == 400
    assert _payload(resp)["reason"] == "unmanaged_venv"


@pytest.mark.asyncio
async def test_delete_removes_a_managed_venv(tmp_path, monkeypatch):
    reg = M.ModelDownloadRegistry()
    venv = _fake_venv(tmp_path)
    install = sidecar.SidecarInstall("fixture-embed", venv=venv)
    monkeypatch.setattr(M.ModelDownloadRegistry, "install", lambda self, provider: install)
    resp = await H.api_sidecar_install_delete(
        _req("DELETE", "/x", reg, match_info={"provider": "fixture-embed"})
    )
    assert resp.status == 200
    assert not venv.exists()


# ── residency: loaded models, pressure, unload ──


class _FakeEmbedder(LocalModelProvider):
    """An in-process provider that declares where its loaded model lives."""

    _MODEL_ATTRS = ("_model",)

    def __init__(self) -> None:
        self._model: object | None = None

    @property
    def name(self) -> str:
        return "native"

    @property
    def display_name(self) -> str:
        return "Fake Embedder"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[LocalModel]:
        return [LocalModel(name="L6-v2", capabilities=["embedding"])]

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True


class _Loaded:
    name = "L6-v2"


@pytest.fixture
def registered_fake(monkeypatch):
    """Register a fake provider under an APP name, and clean the global registry up."""
    from personalclaw.local_models import registry as reg_mod

    provider = _FakeEmbedder()
    reg_mod.register_provider(provider, capabilities=["embedding"], name="fake-embed")
    yield provider
    reg_mod.unregister_provider("fake-embed")


def test_the_abc_default_reports_declared_attributes_as_resident():
    provider = _FakeEmbedder()
    assert provider.loaded_models() == []
    provider._model = _Loaded()
    assert provider.loaded_models() == [{"model": "L6-v2", "attr": "_model"}]


def test_unload_is_idempotent_and_honest_about_freeing_nothing():
    provider = _FakeEmbedder()
    assert provider.unload() is False  # nothing was resident; don't pretend
    provider._model = _Loaded()
    assert provider.unload() is True
    assert provider.loaded_models() == []
    assert provider.unload() is False


@pytest.mark.asyncio
async def test_ensure_ready_separates_ready_from_unavailable():
    provider = _FakeEmbedder()
    assert await provider.ensure_ready() == (True, "ready")

    class _Down(_FakeEmbedder):
        async def is_available(self) -> bool:
            return False

    assert await _Down().ensure_ready() == (False, "unavailable")

    class _Broken(_FakeEmbedder):
        async def is_available(self) -> bool:
            raise RuntimeError("import failed")

    assert await _Broken().ensure_ready() == (False, "unavailable")


def test_a_model_resident_after_a_binding_switch_shows_inactive(registered_fake, monkeypatch):
    """Success Criterion 8's attribution half — the reclaimable case."""
    registered_fake._model = _Loaded()

    monkeypatch.setattr(residency, "_bound_refs", lambda: {"fake-embed:L6-v2"})
    rows = residency.loaded_occupants()
    assert rows == [
        {
            "provider": "fake-embed",
            "model": "L6-v2",
            "kind": "in-process",
            "rss_mb": None,
            "is_active": True,
        }
    ]

    # The user binds a different model. The old one is still in RAM.
    monkeypatch.setattr(residency, "_bound_refs", lambda: {"fake-embed:bge-large"})
    assert residency.loaded_occupants()[0]["is_active"] is False


def test_attribution_accepts_either_spelling_of_the_provider_name(registered_fake, monkeypatch):
    """The registry keys on the APP name while a ref may carry the provider's own name."""
    registered_fake._model = _Loaded()
    monkeypatch.setattr(residency, "_bound_refs", lambda: {"native:L6-v2"})
    assert residency.loaded_occupants()[0]["is_active"] is True


def test_memory_pressure_reports_a_real_snapshot_with_the_threshold():
    snapshot = residency.memory_pressure(warn_pct=90)
    assert snapshot["warn_pct"] == 90
    assert snapshot["source"] in ("vm_stat", "meminfo", "unavailable")
    if snapshot["source"] != "unavailable":
        assert snapshot["total_mb"] > 0
        assert 0 <= snapshot["used_pct"] <= 100
        assert snapshot["used_mb"] + snapshot["available_mb"] == snapshot["total_mb"]


def test_pressure_never_warns_on_unknown_memory(monkeypatch):
    """A false alarm about memory is worse than no alarm."""
    monkeypatch.setattr(
        residency, "_darwin_memory", lambda: (_ for _ in ()).throw(OSError("no sysctl"))
    )
    monkeypatch.setattr(
        residency, "_linux_memory", lambda: (_ for _ in ()).throw(OSError("no meminfo"))
    )
    # warn_pct=0 is the case that actually exercises the guard: `0 >= 0` is True, so
    # without the "did we measure anything" clause an unreadable host would warn forever.
    for threshold in (0, 1, 85):
        snapshot = residency.memory_pressure(warn_pct=threshold)
        assert snapshot["source"] == "unavailable"
        assert snapshot["warn"] is False, f"warned on unmeasured memory at warn_pct={threshold}"
        assert snapshot["total_mb"] == 0


def test_pressure_warns_at_the_threshold(monkeypatch):
    monkeypatch.setattr(residency.sys, "platform", "linux")
    monkeypatch.setattr(residency, "_linux_memory", lambda: (16000, 1000))
    assert residency.memory_pressure(warn_pct=90)["warn"] is True
    assert residency.memory_pressure(warn_pct=95)["warn"] is False


def test_the_pressure_threshold_comes_from_config(monkeypatch):
    from personalclaw.config.loader import AppConfig

    assert AppConfig().local_models.pressure_warn_pct == residency._warn_pct_default()


@pytest.mark.asyncio
async def test_unload_frees_the_model_and_returns_a_fresh_pressure_snapshot(registered_fake):
    registered_fake._model = _Loaded()
    result = await residency.unload_provider("fake-embed")
    assert result["ok"] is True
    assert result["freed"] is True
    assert result["kind"] == "in-process"
    assert "used_pct" in result["pressure"]
    assert residency.loaded_occupants() == []
    # Idempotent: a second unload frees nothing and says so.
    assert (await residency.unload_provider("fake-embed"))["freed"] is False


@pytest.mark.asyncio
async def test_unloading_an_unknown_provider_is_a_404_shaped_result():
    result = await residency.unload_provider("nope")
    assert result["ok"] is False
    assert "Unknown provider" in result["error"]


@pytest.mark.asyncio
async def test_a_sidecar_row_carries_child_reported_rss(registered_fake, worker, tmp_path):
    """The widget's RSS number comes from the CHILD, not a guess about the gateway."""
    from personalclaw.local_models import sidecar as S

    runner = SidecarRunner(app="fake-embed", worker=worker, venv=tmp_path / "venv")
    S.register_runner(runner)
    try:
        registered_fake._model = _Loaded()
        runner.stat()  # a real stat frame from a real child
        rows = residency.loaded_occupants()
        assert rows[0]["kind"] == "sidecar"
        assert rows[0]["rss_mb"] > 0
        assert rows[0]["generation"] == 1
        assert rows[0]["pid"] == runner.health()["pid"]
    finally:
        S.unregister_runner("fake-embed")


@pytest.mark.asyncio
async def test_a_dead_sidecar_holds_nothing(registered_fake, worker, tmp_path):
    from personalclaw.local_models import sidecar as S

    runner = SidecarRunner(app="fake-embed", worker=worker, venv=tmp_path / "venv")
    S.register_runner(runner)
    try:
        registered_fake._model = _Loaded()
        runner.ensure_started()
        runner.stop()
        assert residency.loaded_occupants() == []  # no stale row from a dead generation
    finally:
        S.unregister_runner("fake-embed")


@pytest.mark.asyncio
async def test_the_residency_snapshot_reports_readiness_per_provider(registered_fake):
    snapshot = await residency.residency_snapshot()
    entry = next(p for p in snapshot["providers"] if p["provider"] == "fake-embed")
    assert entry == {
        "provider": "fake-embed",
        "display_name": "Fake Embedder",
        "ok": True,
        "state": "ready",
        "kind": "in-process",
        "sidecar": None,
    }
    assert "pressure" in snapshot and "loaded" in snapshot


@pytest.mark.asyncio
async def test_a_broken_provider_does_not_blank_the_widget(monkeypatch):
    from personalclaw.local_models import registry as reg_mod

    class _Exploding(_FakeEmbedder):
        def loaded_models(self):
            raise RuntimeError("reflection failed")

    reg_mod.register_provider(_Exploding(), capabilities=["embedding"], name="broken-embed")
    try:
        assert residency.loaded_occupants() == []  # skipped, not raised
    finally:
        reg_mod.unregister_provider("broken-embed")


@pytest.mark.asyncio
async def test_the_loaded_and_unload_endpoints(registered_fake):
    reg = M.ModelDownloadRegistry()
    registered_fake._model = _Loaded()
    resp = await H.api_models_loaded(_req("GET", "/api/models/loaded", reg))
    body = _payload(resp)
    assert any(row["provider"] == "fake-embed" for row in body["loaded"])

    resp = await H.api_models_unload(
        _req("POST", "/api/models/unload", reg, body={"provider": "fake-embed"})
    )
    assert resp.status == 200
    assert _payload(resp)["freed"] is True

    resp = await H.api_models_unload(_req("POST", "/api/models/unload", reg, body={}))
    assert resp.status == 400

    resp = await H.api_models_unload(
        _req("POST", "/api/models/unload", reg, body={"provider": "ghost"})
    )
    assert resp.status == 404


# ── the fixture worker is real app-side code; keep it honest ──


def test_the_fixture_worker_is_valid_python():
    ast.parse(textwrap.dedent(_WORKER))
