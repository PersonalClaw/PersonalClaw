"""EI-6 SC5, the SPAWN half — a durable worker is actually OPENED, survives the gateway,
and the shipped recovery machinery reattaches to it.

`test_ei6_runner_lifecycle.py` proved the READER: a recovery sweep that recognises a live
durable session and suspends instead of tombstoning. What it could not prove — because
nothing produced one — is that core ever PLACES work in such a session. This suite closes
that gap at the seam that now exists: `tmux_substrate.new_session` (the one writer of
durable sessions) and `provisioning.run_step`'s durable branch (the run-worker launch that
uses it, gated on `durable_sessions_enabled()` with the bare subprocess as the
unconditional fallback).

**The same honesty statement as the sibling suite.** ``tmux`` is not installed on the
machine this was written on, so the daemon is substituted by a shim executable literally
named ``tmux`` on ``PATH`` — but the substitution is one level below the code under test
and it defers to the OS for the one question that matters:

* ``new-session`` REALLY spawns the given command, detached (``start_new_session``), with
  the given cwd — and then the shim process exits, so the worker's survival is genuine
  reparenting, not a fixture flag;
* ``has-session``/``list-panes`` answer via ``os.kill(pid, 0)`` — the kernel's liveness;
* ``kill-session`` really kills the process group.

The production argv composition, the subprocess spawns, the exit-code contracts, the
rc/out file plumbing and the whole sweep chain run verbatim. The one class that needs the
real daemon (`TestSC5RealTmux`) is skipif-gated on the binary and runs the same clauses
against a real tmux server wherever one exists.

The dead legs rely on the shim's children being reparented to init/launchd when the shim
exits — the reaper that keeps ``kill(pid, 0)`` honest (no zombie can linger under a parent
that is already gone). The sibling suite had to ``wait()`` by hand precisely because ITS
workers stayed children of the test process; these do not.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import pytest

from personalclaw import tmux_substrate
from personalclaw.agents import runner_lifecycle
from personalclaw.workflows import containers, provisioning, store, worktrees
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

pytestmark = pytest.mark.anyio

#: Whether a REAL tmux binary exists on this machine's PATH — captured at import, BEFORE
#: any fixture prepends the shim, so the skip decision is about the machine and can never
#: be satisfied by our own fake.
_REAL_TMUX = shutil.which("tmux") is not None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


SPEC = {
    "name": "ei6ds",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [{"kind": "transform", "id": "a", "config": {"expr": "1"}}],
    },
}


# ── the tmux shim (superset of the sibling suite's: it can also CREATE and KILL) ─────────

_SHIM = '''#!{python}
import os, signal, subprocess, sys
# argv shape produced by tmux_substrate._argv: ["-L", <socket>, <subcommand>, ...]
argv = sys.argv[1:]
if argv[:1] == ["-L"]:
    argv = argv[2:]
sub = argv[0] if argv else ""
root = os.environ["PCLAW_SHIM_SESSIONS"]


def live():
    """(name, pid, cwd) for every registered session whose pid is ALIVE right now."""
    out = []
    for name in sorted(os.listdir(root)):
        pid, _, cwd = open(os.path.join(root, name)).read().partition("\\t")
        try:
            os.kill(int(pid), 0)          # the kernel is the only source of truth here
        except (OSError, ValueError):
            continue
        out.append((name, int(pid), cwd))
    return out


if sub == "new-session":
    if os.environ.get("PCLAW_SHIM_FAIL_NEW"):
        sys.exit(1)                       # the "tmux refused" leg for the fallback tests
    args = argv[1:]
    name = cwd = ""
    env = dict(os.environ)
    i = 0
    while i < len(args):
        if args[i] == "-d":
            i += 1
        elif args[i] == "-s":
            name = args[i + 1]; i += 2
        elif args[i] == "-c":
            cwd = args[i + 1]; i += 2
        elif args[i] == "-e":
            k, _, v = args[i + 1].partition("="); env[k] = v; i += 2
        else:
            break
    command = args[i:]
    if not name or not command:
        sys.exit(1)
    if any(n == name for n, _, _ in live()):
        sys.exit(1)                       # duplicate session: real tmux refuses too
    proc = subprocess.Popen(
        command, cwd=cwd or None, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,           # its own group; this shim exits and init reaps
    )
    open(os.path.join(root, name), "w").write(str(proc.pid) + "\\t" + cwd)
    sys.exit(0)
if sub == "kill-session":
    want = argv[argv.index("-t") + 1].lstrip("=")
    for n, pid, _ in live():
        if n == want:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                os.remove(os.path.join(root, n))
            except OSError:
                pass
            sys.exit(0)
    sys.exit(1)
if sub == "has-session":
    want = argv[argv.index("-t") + 1].lstrip("=")
    sys.exit(0 if any(n == want for n, _, _ in live()) else 1)
if sub == "list-panes":
    for n, _, cwd in live():
        print(n + "\\t" + cwd)
    sys.exit(0)
if sub == "list-sessions":
    for n, _, _ in live():
        print(n)
    sys.exit(0)
sys.exit(0)
'''


@pytest.fixture
def tmux_shim(tmp_path, monkeypatch):
    """Put the kernel-backed ``tmux`` on PATH; yield the session-registry dir.

    Also pins ``PYTHONPATH`` to the ABSOLUTE ``src`` dir: the ceiling shim a durable step
    execs (``python -m personalclaw._spawn_exec_shim``) starts with the WORKER's cwd, so a
    relative ``PYTHONPATH=src`` from the pytest invocation would resolve against the tmp
    workspace and the import would fail — a test-harness artefact, not a production one
    (the gateway runs from an installed package).
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    shim = bindir / "tmux"
    shim.write_text(_SHIM.format(python=sys.executable), encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PCLAW_SHIM_SESSIONS", str(sessions))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parent.parent / "src"))
    try:
        yield sessions
    finally:
        # Scoped to the pids THIS registry recorded — never a pattern kill. The shim's
        # workers are process-group leaders (start_new_session), so killpg reaches a
        # worker's own children too.
        for record in sessions.glob("*"):
            try:
                pid = int(record.read_text(encoding="utf-8").partition("\t")[0])
                os.killpg(pid, signal.SIGKILL)
            except (OSError, ValueError):
                continue


@pytest.fixture
def durable_on(monkeypatch):
    """``agent.durable_sessions`` on, without writing a real config file."""

    def _flip(enabled: bool = True) -> None:
        monkeypatch.setattr(
            runner_lifecycle,
            "durable_sessions_enabled",
            lambda: enabled and tmux_substrate.tmux_available(),
        )

    _flip(True)
    return _flip


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Everything under a tmp home; the real ``~/.personalclaw`` is never touched.

    Same three bindings as the sibling suite, for the same measured reason: ``store`` and
    ``leases`` imported ``config_dir`` by name at module load.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


def _run(**kw) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name="ei6ds", status=RunStatus.RUNNING, **kw))
    store.write_spec(run.id, SPEC)
    store.save(run)
    return run


def _write_step(ws: Path, body: str, name: str = "step.py") -> str:
    """A step script in the workspace; returns the run_step command string for it."""
    (ws / name).write_text(body, encoding="utf-8")
    return f"{sys.executable} {name}"


async def _wait_for(predicate, *, timeout: float = 10.0, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


# ── the spawn function itself ────────────────────────────────────────────────────────────


class TestNewSession:
    async def test_spawn_opens_a_session_the_probes_see_and_kill_session_removes_it(
        self, tmux_shim, tmp_path
    ):
        """The unit contract: after `new_session` the daemon holds a REAL detached worker
        under the deterministic name, and `kill_session` takes it back down.

        Asserted through the same probes the recovery sweep uses (`has_session`,
        `pane_paths_sync`) — the writer is only correct if THAT reader sees its work.
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        name = tmux_substrate.durable_session_name("proj", "r1", "wf")
        ok = await tmux_substrate.new_session(
            name,
            workspace=str(ws),
            command=[sys.executable, "-c", "import time; time.sleep(120)"],
        )
        assert ok, "new_session reported failure for a spawn that should succeed"
        assert await tmux_substrate.has_session(name)
        assert (name, str(ws)) in tmux_substrate.pane_paths_sync(), (
            "the worker is not registered at the workspace cwd — the sweep's second "
            "recognition leg (pane cwd inside the run's workspace) could never find it"
        )
        await tmux_substrate.kill_session(name)
        assert not await tmux_substrate.has_session(name)

    async def test_a_name_the_sweep_could_not_recompute_is_REFUSED_not_rewritten(
        self, tmux_shim, tmp_path
    ):
        """Silently sanitizing here would open a session under a name no recomputing reader
        derives — a worker the sweep can never find, i.e. durability that lies."""
        cmd = [sys.executable, "-c", "pass"]
        assert not await tmux_substrate.new_session(
            "has space", workspace=str(tmp_path), command=cmd
        )
        assert not await tmux_substrate.new_session("has.dot", workspace=str(tmp_path), command=cmd)
        assert not await tmux_substrate.new_session("", workspace=str(tmp_path), command=cmd)
        assert not await tmux_substrate.new_session("pclaw-ok", workspace=str(tmp_path), command=[])
        assert list(tmux_shim.glob("*")) == [], "a refused spawn still created a session"

    async def test_missing_tmux_answers_false_and_never_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        assert (
            await tmux_substrate.new_session("pclaw-x", workspace=str(tmp_path), command=["true"])
            is False
        )

    async def test_env_entries_ride_into_the_worker(self, tmux_shim, tmp_path):
        """The `-e K=V` plumbing: what run_step forwards (PATH/PYTHONPATH/step env) must
        actually reach the worker's environment."""
        ws = tmp_path / "envws"
        ws.mkdir()
        ok = await tmux_substrate.new_session(
            "pclaw-envtest",
            workspace=str(ws),
            command=[
                sys.executable,
                "-c",
                "import os; open('mark', 'w').write(os.environ.get('PCLAW_MARK', ''))",
            ],
            env={"PCLAW_MARK": "42"},
        )
        assert ok
        await _wait_for(lambda: (ws / "mark").exists(), what="the env-marker file")
        assert (ws / "mark").read_text(encoding="utf-8") == "42"


# ── the run-worker launch seam (run_step's durable branch) ──────────────────────────────


class TestDurableStepSeam:
    async def test_a_setup_step_executes_INSIDE_the_durable_session(
        self, tmux_shim, durable_on, tmp_path
    ):
        """The wiring, end to end: run_step routes through tmux, the step's effect lands,
        its output and exit code come back, and the rc file proves WHICH path ran it —
        the bare path writes no rc, so its presence cannot be a false positive."""
        ws = tmp_path / "ws"
        ws.mkdir()
        step = _write_step(ws, "open('done.txt', 'w').write('yo')\nprint('hello-out')\n")
        ok, detail = await provisioning.run_step(
            step, ws, env={}, durable_session="pclaw-proj-r1-ei6ds"
        )
        assert ok, f"the durable step failed: {detail}"
        assert "hello-out" in detail, "the step's stdout did not come back through the out file"
        assert (ws / "done.txt").exists(), "the step's own effect is missing — it never ran"
        rc = (ws / worktrees.setup_marker(step)).with_suffix(".rc")
        assert (
            rc.exists() and rc.read_text(encoding="utf-8").strip() == "0"
        ), "no rc file — the step ran as a bare subprocess, so the durable seam is inert"
        assert (
            tmux_shim / "pclaw-proj-r1-ei6ds"
        ).exists(), "no session was ever registered — tmux was not where the step ran"

    async def test_tmux_refusing_the_spawn_falls_back_to_the_bare_subprocess(
        self, tmux_shim, durable_on, tmp_path, monkeypatch
    ):
        """The fail-open clause: durability must never break a run. tmux says no → the step
        still runs, exactly as it did before this feature existed."""
        monkeypatch.setenv("PCLAW_SHIM_FAIL_NEW", "1")
        ws = tmp_path / "ws"
        ws.mkdir()
        step = _write_step(ws, "open('done.txt', 'w').write('yo')\nprint('bare-out')\n")
        ok, detail = await provisioning.run_step(
            step, ws, env={}, durable_session="pclaw-proj-r2-ei6ds"
        )
        assert ok and "bare-out" in detail
        assert (ws / "done.txt").exists()
        rc = (ws / worktrees.setup_marker(step)).with_suffix(".rc")
        assert not rc.exists(), "an rc file exists — the durable path claims to have run it"
        assert list(tmux_shim.glob("*")) == []

    async def test_tmux_MISSING_takes_the_bare_path_via_the_gate(
        self, durable_on, tmp_path, monkeypatch
    ):
        """The other fallback flavour: no binary → `durable_sessions_enabled()` is False →
        not one tmux subprocess is attempted (there is no tmux to attempt it with)."""
        monkeypatch.setenv(
            "PATH", os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if p)
        )
        # Strip any tmux from PATH by pointing at an empty bindir + the python's own dir
        # (run_step needs to resolve sys.executable's binary through an absolute path, so
        # PATH content is irrelevant to the step itself).
        empty = tmp_path / "emptybin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert not tmux_substrate.tmux_available()
        ws = tmp_path / "ws"
        ws.mkdir()
        step = _write_step(ws, "open('done.txt', 'w').write('yo')\n")
        ok, _ = await provisioning.run_step(step, ws, env={}, durable_session="pclaw-proj-r3-ei6ds")
        assert ok and (ws / "done.txt").exists()

    async def test_the_flag_off_never_touches_tmux(self, tmux_shim, durable_on, tmp_path):
        """VACUITY FLOOR for the seam test above: prove the durable branch is the flag's
        doing, not an unconditional rewrite of run_step."""
        durable_on(False)
        ws = tmp_path / "ws"
        ws.mkdir()
        step = _write_step(ws, "open('done.txt', 'w').write('yo')\n")
        ok, _ = await provisioning.run_step(step, ws, env={}, durable_session="pclaw-proj-r4-ei6ds")
        assert ok and (ws / "done.txt").exists()
        assert list(tmux_shim.glob("*")) == [], "the flag was off and tmux was still used"

    async def test_a_failing_step_reports_its_exit_code_and_output(
        self, tmux_shim, durable_on, tmp_path
    ):
        """The bare path's `(False, "exited N: …")` contract survives the reroute — the
        caller (`_run_setup`) records that string on the run, so its shape is load-bearing."""
        ws = tmp_path / "ws"
        ws.mkdir()
        step = _write_step(ws, "import sys\nprint('boom')\nsys.exit(3)\n")
        ok, detail = await provisioning.run_step(
            step, ws, env={}, durable_session="pclaw-proj-r5-ei6ds"
        )
        assert not ok
        assert "exited 3" in detail and "boom" in detail

    async def test_provision_hands_the_name_only_to_an_ISOLATED_workspace(
        self, tmux_shim, durable_on, tmp_path
    ):
        """The pass-through wire (`provision` → `_run_setup` → `run_step`), both directions.

        An isolated (scratch) workspace's setup runs durably; an in-place one runs bare even
        with the name supplied — an in-place run's substrate IS the gateway process, so a
        durable session for it would make the sweep suspend a run whose work was never
        separable. Without this test the parameter is a wire no test would notice cut.
        """
        from personalclaw.workflows.workspace import Mode, WorkspaceSpec

        run_dir = tmp_path / "rundir"
        run_dir.mkdir()
        out = await provisioning.provision(
            WorkspaceSpec(mode=Mode.SCRATCH, setup="touch done.txt"),
            run_id="r-iso",
            run_dir=run_dir,
            durable_session="pclaw-proj-riso-ei6ds",
        )
        assert out.setup_ran == ["touch done.txt"] and out.isolated
        ws = Path(out.path)
        assert (ws / "done.txt").exists()
        rc = (ws / worktrees.setup_marker("touch done.txt")).with_suffix(".rc")
        assert rc.exists(), "the isolated workspace's setup did not go through the durable seam"
        assert (tmux_shim / "pclaw-proj-riso-ei6ds").exists()

        inplace = tmp_path / "inplace"
        inplace.mkdir()
        out2 = await provisioning.provision(
            WorkspaceSpec(mode=Mode.IN_PLACE, setup="touch bare.txt"),
            run_id="r-inp",
            workspace_dir=str(inplace),
            durable_session="pclaw-proj-rinp-ei6ds",
        )
        assert not out2.isolated and (inplace / "bare.txt").exists()
        assert not (
            tmux_shim / "pclaw-proj-rinp-ei6ds"
        ).exists(), "an in-place run was given a durable worker its substrate cannot honour"

    async def test_the_controller_names_the_session_the_sweep_recomputes(
        self, tmux_shim, durable_on, tmp_path
    ):
        """The LAST link: `controller._provision_workspace` passes the run's OWN derived name.

        Driven through the controller rather than by calling `provision` with a hand-built
        name, because the defect this catches is precisely the hand-off — a controller that
        passes nothing (or a name derived differently from the sweep's) leaves every clause
        above true and SC5 still impossible.
        """
        spec = {
            "name": "ei6ds",
            "workspace": {"mode": "scratch", "setup": "touch done.txt"},
            "root": SPEC["root"],
        }
        run = _run(project_id="proj")
        controller = RunController(store.get(run.id), spec, services=EngineServices())
        assert await controller._provision_workspace()
        name = containers.durable_worker_name(store.get(run.id))
        assert (tmux_shim / name).exists(), (
            "the controller did not open the setup's durable session under the name the "
            "recovery sweep recomputes"
        )
        ws = Path(str((store.get(run.id).extra or {}).get("worktree_path", "")))
        assert (ws / "done.txt").exists()

    async def test_a_surviving_step_from_a_previous_life_is_adopted_not_doubled(
        self, tmux_shim, durable_on, tmp_path
    ):
        """Adopt-before-spawn: a session already holding this run's name is a previous
        gateway life's step still executing. The new life must WAIT for it — two copies of
        one `npm install` interleaving in a single tree is the corruption the workspace
        lock exists to prevent — and the survivor must finish, not be killed."""
        ws = tmp_path / "ws"
        ws.mkdir()
        name = "pclaw-proj-r6-ei6ds"
        assert await tmux_substrate.new_session(
            name,
            workspace=str(ws),
            command=[
                sys.executable,
                "-c",
                "import time; time.sleep(0.6); open('first', 'w').write('1')",
            ],
        )
        started = time.monotonic()
        step = _write_step(ws, "open('second', 'w').write('2')\n")
        ok, _ = await provisioning.run_step(step, ws, env={}, durable_session=name)
        assert ok
        assert (ws / "first").exists(), "the surviving worker was cut down instead of adopted"
        assert (ws / "second").exists()
        assert (
            time.monotonic() - started >= 0.5
        ), "run_step did not wait for the survivor — the two ran concurrently"


# ── SC5 end to end: the spawn side produces the session the sweep reattaches to ─────────


class TestSC5SpawnToReattach:
    async def test_gateway_death_midstep_suspends_then_resumes_with_the_journal_flagging_it(
        self, tmux_shim, durable_on, tmp_path
    ):
        """SC5's whole first clause, with the session coming from the PRODUCTION spawn.

        A long setup step is launched through `run_step`'s durable branch; the gateway's
        death is simulated by abandoning the awaiting task (the poll dies, the tmux-owned
        worker does not); the recovery sweep then finds the still-alive session by the
        RECOMPUTED name and suspends; the resume flips suspended→running with the journal's
        existing `resumed` flag. Before this change the identical sweep found nothing,
        because nothing ever produced the session — the reader finally has its writer.
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        run = _run(
            project_id="proj",
            started_at="2026-09-05T00:00:00Z",
            extra={"worktree_path": str(tmp_path / "gone")},
        )
        name = containers.durable_worker_name(run)
        step = _write_step(ws, "import time\ntime.sleep(120)\n", name="long_step.py")
        task = asyncio.ensure_future(provisioning.run_step(step, ws, env={}, durable_session=name))
        try:
            await _wait_for(
                lambda: tmux_substrate.has_session(name), what="the durable worker to spawn"
            )
        finally:
            task.cancel()  # the gateway dies; the handle is simply abandoned
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await tmux_substrate.has_session(
            name
        ), "the worker died with the gateway — nothing durable was ever spawned"

        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert (
            store.get(run.id).status == RunStatus.PAUSED
        ), "the sweep did not reattach to the still-alive worker the spawn side created"
        await wd.stop()

        controller = RunController(store.get(run.id), SPEC, services=EngineServices())
        assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
        await controller.stop()
        starts = [
            e for e in _journal_events(run.id) if e.get("kind") == "run_started" and "resumed" in e
        ]
        assert (
            starts and starts[-1]["resumed"] is True
        ), "the reattached run resumed but the journal did not flag it `resumed`"
        await tmux_substrate.kill_session(name)

    async def test_a_genuinely_dead_session_is_tombstoned(self, tmux_shim, durable_on, tmp_path):
        """SC5's second clause with a session that EXISTED and died: the spawn side must not
        make the sweep credulous — only the living rescue a run."""
        run = _run(project_id="proj", extra={"worktree_path": str(tmp_path / "gone")})
        name = containers.durable_worker_name(run)
        ws = tmp_path / "ws"
        ws.mkdir()
        assert await tmux_substrate.new_session(
            name, workspace=str(ws), command=[sys.executable, "-c", "pass"]
        )
        await _wait_for(lambda: not tmux_substrate.has_session_sync(name), what="the worker to die")
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert (
            store.get(run.id).status == RunStatus.CANCELLED
        ), "a run whose worker is genuinely gone was not tombstoned"
        await wd.stop()

    async def test_teardown_kills_the_runs_durable_worker(self, tmux_shim, durable_on, tmp_path):
        """The other end of the lifecycle: a deleted run must not leave a detached worker
        running in a directory teardown is about to remove."""
        run = _run(project_id="proj", extra={})
        name = containers.durable_worker_name(run)
        ws = tmp_path / "ws"
        ws.mkdir()
        assert await tmux_substrate.new_session(
            name,
            workspace=str(ws),
            command=[sys.executable, "-c", "import time; time.sleep(120)"],
        )
        await provisioning.teardown(run)
        assert not await tmux_substrate.has_session(
            name
        ), "teardown left the durable worker running"


def _journal_events(run_id: str) -> list[dict]:
    """Raw journal read, same shape (and same honesty argument) as the sibling suite's."""
    from personalclaw.workflows import journal as journal_mod

    path = Path(store.run_dir(run_id)) / journal_mod.JOURNAL_FILE
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


# ── the shared run→name derivation ───────────────────────────────────────────────────────


class TestDurableWorkerName:
    def test_the_spawn_side_and_the_sweep_derive_the_same_name(self):
        """`containers.durable_worker_name` is the ONE derivation both halves call; this
        pins its fallbacks so neither half can drift to a name the other never computes."""
        from types import SimpleNamespace

        run = SimpleNamespace(project_id="proj", id="r-1", workflow_name="wf")
        assert containers.durable_worker_name(run) == tmux_substrate.durable_session_name(
            "proj", "r-1", "wf"
        )
        bare = SimpleNamespace(project_id="", id="r-2", workflow_name="")
        assert containers.durable_worker_name(bare) == "pclaw-default-r-2-run"


# ── SC5 against a REAL tmux daemon (skipped where none is installed) ────────────────────


@pytest.mark.skipif(not _REAL_TMUX, reason="tmux is not installed on this machine")
class TestSC5RealTmux:
    """The same clauses with the real daemon: skipped honestly where tmux is absent, and
    the shim classes above keep the logic railed on those machines.

    THE SKIP WAS NEVER A CEILING — it was an UNASKED QUESTION. "tmux is absent" was read
    for weeks as "SC5 cannot be validated", but nobody had asked a package manager: the
    binary installs in seconds (`brew install tmux` / `apt-get install -y tmux`) and every
    clause below then passes against a real daemon. Absent is not unavailable. The stock
    GitHub runner images genuinely do not ship tmux (checked against the Ubuntu-24.04 and
    macOS-15-arm64 image manifests — zero mentions), which is why `.github/workflows/ci.yml`
    now INSTALLS it and asserts `tmux -V` in the same step: on that job the skipif can no
    longer fire, so a regression here reds CI instead of vanishing into a skip count.
    """

    async def test_live_reattaches_and_dead_tombstones_against_a_real_daemon(
        self, durable_on, tmp_path
    ):
        ws = tmp_path / "scratch"
        ws.mkdir()
        live_run = _run(project_id="proj", extra={"worktree_path": str(tmp_path / "gone-live")})
        dead_run = _run(project_id="proj", extra={"worktree_path": str(tmp_path / "gone-dead")})
        live_name = containers.durable_worker_name(live_run)
        dead_name = containers.durable_worker_name(dead_run)
        try:
            # The live worker comes from the PRODUCTION seam: a long marker command through
            # run_step's durable branch, abandoned mid-flight (the gateway "dies").
            task = asyncio.ensure_future(
                provisioning.run_step("sleep 60", ws, env={}, durable_session=live_name)
            )
            try:
                await _wait_for(
                    lambda: tmux_substrate.has_session(live_name),
                    what="the real durable worker",
                )
            finally:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # The dead worker existed and was killed — only genuinely dead sessions tombstone.
            assert await tmux_substrate.new_session(
                dead_name, workspace=str(ws), command=["sleep", "60"]
            )
            await tmux_substrate.kill_session(dead_name)
            await _wait_for(
                lambda: not tmux_substrate.has_session_sync(dead_name),
                what="the killed session to leave the server",
            )

            wd = WorkflowWatchdog(None, EngineServices())
            await wd._poll_once()
            await wd.stop()
            assert store.get(live_run.id).status == RunStatus.PAUSED
            assert store.get(dead_run.id).status == RunStatus.CANCELLED
        finally:
            await tmux_substrate.kill_session(live_name)
            await tmux_substrate.kill_session(dead_name)

    async def test_the_reattached_run_RESUMES_and_the_journal_flags_it_against_a_real_daemon(
        self, durable_on, tmp_path
    ):
        """SC5's first clause to its END against the real daemon: suspended → **running**,
        with the journal flagging `resumed`.

        The sibling above stops at `suspended`, and `TestSC5SpawnToReattach` carries the
        resume half only against the shim. That left the one transition SC5 names in full —
        "run resumes suspended→running, journal flags `resumed`" — proven against a
        substitute daemon and never against tmux itself. The shim is a faithful stand-in for
        `has-session`/`list-panes`, but the whole point of a durable session is that a
        PROCESS THE GATEWAY DOES NOT OWN keeps the work alive, and only the real server
        reparents that way. So this runs the same chain — production spawn seam → abandoned
        awaiter → boot sweep → controller resume → journal read — with tmux 3.x driving it.
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        run = _run(
            project_id="proj",
            started_at="2026-09-06T00:00:00Z",
            extra={"worktree_path": str(tmp_path / "gone")},
        )
        name = containers.durable_worker_name(run)
        try:
            # `sleep 120` through run_step's durable branch: the worker is tmux's child, not
            # ours, so cancelling the awaiter is a real gateway death and not a fixture flag.
            task = asyncio.ensure_future(
                provisioning.run_step("sleep 120", ws, env={}, durable_session=name)
            )
            try:
                await _wait_for(
                    lambda: tmux_substrate.has_session(name),
                    what="the real durable worker to spawn",
                )
            finally:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await tmux_substrate.has_session(
                name
            ), "the real worker died with the gateway — the session was not durable"

            wd = WorkflowWatchdog(None, EngineServices())
            await wd._poll_once()
            await wd.stop()
            assert (
                store.get(run.id).status == RunStatus.PAUSED
            ), "the sweep did not reattach to the still-alive real worker"

            controller = RunController(store.get(run.id), SPEC, services=EngineServices())
            assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
            await controller.stop()
            starts = [
                e
                for e in _journal_events(run.id)
                if e.get("kind") == "run_started" and "resumed" in e
            ]
            assert (
                starts and starts[-1]["resumed"] is True
            ), "the run resumed off a real durable session but the journal did not flag it"
        finally:
            await tmux_substrate.kill_session(name)
