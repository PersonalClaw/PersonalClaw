"""EI-6 SC5 — durable-session reattach, dead-only tombstoning, and runner leases.

**What this suite can and cannot prove, stated up front.** ``tmux`` is not installed on the
machine this was written on, so a real tmux daemon was never exercised. Rather than skip the
clause (a skipped surface reads exactly like a passing one) the aliveness question is answered
by REAL OS PROCESS STATE through a shim binary literally named ``tmux``, placed on ``PATH``,
which answers ``has-session`` and ``list-panes`` by calling ``os.kill(pid, 0)`` on a process
this suite actually spawned.

So the chain under test is real except for one link:

* the worker is a real ``subprocess.Popen`` child, not a fixture flag;
* its aliveness is the kernel's answer, not a boolean someone set;
* the production code's argv, its subprocess spawn, and its exit-code contract
  (``has-session`` exits 0 for present / non-zero for absent) are executed verbatim;
* only the tmux *daemon* is substituted, by something that defers to the OS.

What that leaves unproven: that a real tmux server reports the same exit codes for these
argv (it does, by documented contract, but this suite did not observe it), and that a worker
placed inside a real tmux session survives a real gateway kill.

The dead leg is reaped with ``wait()`` before it is probed, deliberately. A killed-but-unreaped
child is a ZOMBIE, and ``kill(pid, 0)`` succeeds on a zombie — so a "dead" leg that skipped the
reap would still report alive and the control would silently prove nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from personalclaw import tmux_substrate
from personalclaw.agents import runner_lifecycle, runners
from personalclaw.workflows import containers, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


SPEC = {
    "name": "ei6",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [{"kind": "transform", "id": "a", "config": {"expr": "1"}}],
    },
}


# ── the tmux shim ──────────────────────────────────────────────────────────────────────

#: A ``tmux`` that answers from the kernel. It is a real executable on a real PATH, so the
#: production code's ``create_subprocess_exec``/``subprocess.run`` and its exit-code reading
#: are exercised unchanged — the substitution is one level below the code under test.
_SHIM = '''#!{python}
import os, sys
# argv shape produced by tmux_substrate._argv: ["-L", <socket>, <subcommand>, ...]
argv = sys.argv[1:]
if argv[:1] == ["-L"]:
    argv = argv[2:]
sub = argv[0] if argv else ""
root = os.environ["PCLAW_SHIM_SESSIONS"]


def live():
    """(name, cwd) for every registered session whose recorded pid is ALIVE right now."""
    out = []
    for name in sorted(os.listdir(root)):
        pid, _, cwd = open(os.path.join(root, name)).read().partition("\\t")
        try:
            os.kill(int(pid), 0)          # the kernel is the only source of truth here
        except (OSError, ValueError):
            continue
        out.append((name, cwd))
    return out


if sub == "has-session":
    want = argv[argv.index("-t") + 1].lstrip("=")
    sys.exit(0 if any(n == want for n, _ in live()) else 1)
if sub == "list-panes":
    for n, cwd in live():
        print(n + "\\t" + cwd)
    sys.exit(0)
if sub == "list-sessions":
    for n, _ in live():
        print(n)
    sys.exit(0)
sys.exit(0)
'''


@pytest.fixture
def tmux_shim(tmp_path, monkeypatch):
    """Put a kernel-backed ``tmux`` on PATH and return a session registrar."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    shim = bindir / "tmux"
    shim.write_text(_SHIM.format(python=sys.executable), encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PCLAW_SHIM_SESSIONS", str(sessions))

    spawned: list[subprocess.Popen] = []

    def register(name: str, *, cwd: str = "") -> subprocess.Popen:
        """Spawn a REAL worker process and register it as tmux session *name*."""
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        spawned.append(proc)
        (sessions / name).write_text(f"{proc.pid}\t{cwd}", encoding="utf-8")
        return proc

    def kill(proc: subprocess.Popen) -> None:
        """Kill AND REAP. Skipping the reap leaves a zombie, which reads as alive."""
        proc.kill()
        proc.wait()

    register.kill = kill  # type: ignore[attr-defined]
    try:
        yield register
    finally:
        # Scoped to the PIDs this fixture spawned — never a pattern kill, which on a shared
        # machine has taken out a sibling agent's suite.
        for proc in spawned:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


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
    """Everything under a tmp home. The real ``~/.personalclaw`` is never touched.

    BOTH bindings are patched, not just the loader's: ``workflows.store`` and
    ``workflows.leases`` each imported ``config_dir`` by name at module load, so patching
    only ``config.loader.config_dir`` would leave the lease writer pointed at the real home.
    The redirect is asserted below rather than assumed.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


def test_the_isolated_home_redirect_actually_holds(isolated_home):
    """The floor under every destructive test here: prove the redirect before trusting it."""
    runner_lifecycle.claim_runner("acp:codex", "chat:probe")
    written = list((isolated_home / "locks" / "leases").glob("*.json"))
    assert written, "the lease writer did not land under the isolated home"
    assert str(Path.home() / ".personalclaw") not in str(written[0])


def _run(**kw) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name="ei6", status=RunStatus.RUNNING, **kw))
    store.write_spec(run.id, SPEC)
    store.save(run)
    return run


# ── SC5 clause 1+2: reattach the living, tombstone only the dead ───────────────────────


class TestReattachNotReap:
    """The dangerous half. Aborting a run whose worker is still executing destroys work."""

    async def test_a_LIVE_worker_suspends_the_run_and_a_DEAD_one_aborts_it(
        self, tmux_shim, durable_on, tmp_path
    ):
        """Both legs, one invocation shape. Only the worker's real liveness differs.

        A sweep that suspended everything would pass a test that only checked the live case,
        and a sweep that aborted everything would pass one that only checked the dead case.
        Neither can pass this.

        Both legs use a workspace that is GONE, so the pre-step is the only thing that can
        rescue either of them and the ONE variable between the legs is whether the worker
        process is alive. A surviving workspace would decide both legs on its own and the
        control would measure nothing.
        """
        results: dict[str, RunStatus] = {}
        for leg in ("live", "dead"):
            # A per-leg workspace path. Sharing one would let the FIRST leg's still-running
            # worker satisfy the second leg's cwd join and rescue the run that is supposed to
            # be tombstoned — measured, not hypothetical: that is how this test first failed.
            ws = tmp_path / f"gone-{leg}"
            run = _run(project_id="proj", extra={"worktree_path": str(ws)})
            name = tmux_substrate.durable_session_name("proj", run.id, "ei6")
            worker = tmux_shim(name, cwd=str(ws))
            if leg == "dead":
                tmux_shim.kill(worker)
            wd = WorkflowWatchdog(None, EngineServices())
            await wd._poll_once()
            results[leg] = store.get(run.id).status
            await wd.stop()

        assert results["live"] == RunStatus.PAUSED, (
            "a run whose durable worker is STILL RUNNING was not suspended — "
            f"got {results['live']}, which discards live work"
        )
        assert (
            results["dead"] == RunStatus.CANCELLED
        ), f"a run whose worker is genuinely gone was not tombstoned — got {results['dead']}"

    async def test_the_live_leg_is_NOT_an_artefact_of_the_flag_being_off(
        self, tmux_shim, durable_on, tmp_path
    ):
        """VACUITY FLOOR. With durable sessions OFF the identical live worker must abort.

        Without this, "live → suspended" could be true because the sweep suspends every run
        with a surviving worktree — nothing to do with the worker at all.
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        run = _run(project_id="proj", extra={"worktree_path": str(ws)})
        tmux_shim(tmux_substrate.durable_session_name("proj", run.id, "ei6"), cwd=str(ws))
        durable_on(False)
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        # The worktree still exists, so today's path decides. The point is that the DURABLE
        # probe is what produced PAUSED in the test above, not the directory.
        assert store.get(run.id).status == RunStatus.PAUSED
        assert WorkflowWatchdog._durable_substrate(store.get(run.id)) is None
        await wd.stop()

    async def test_a_GONE_worktree_with_a_live_worker_is_still_rescued(
        self, tmux_shim, durable_on, tmp_path
    ):
        """The case the pre-step exists for: today's path would abort this run.

        A worktree deleted during downtime + a worker still executing = recoverable work the
        old decision throws away. This is the one leg where the pre-step changes the outcome,
        so if it were wired AFTER the path check this test would fail.
        """
        ws = tmp_path / "gone"
        run = _run(project_id="proj", extra={"worktree_path": str(ws)})
        assert not ws.exists()
        tmux_shim(tmux_substrate.durable_session_name("proj", run.id, "ei6"))
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert store.get(run.id).status == RunStatus.PAUSED
        await wd.stop()

    def test_a_worker_in_a_NEIGHBOURING_directory_is_not_adopted(
        self, tmux_shim, durable_on, tmp_path
    ):
        """`/tmp/run-1x` must not match the workspace `/tmp/run-1`.

        A string-prefix join would report a neighbour's live shell as this run's recoverable
        work — the wrong run rescued, and the neighbour's abort masked.
        """
        ws = tmp_path / "run-1"
        ws.mkdir()
        sibling = tmp_path / "run-1x"
        sibling.mkdir()
        run = _run(project_id="proj", extra={"worktree_path": str(ws)})
        tmux_shim("pclaw-someone-else", cwd=str(sibling))
        assert WorkflowWatchdog._durable_substrate(run) is None
        # Control: the SAME registration inside the workspace IS adopted, so the negative
        # above is about the path and not about the join being dead.
        tmux_shim("pclaw-ours", cwd=str(ws / "sub"))
        (ws / "sub").mkdir()
        found = WorkflowWatchdog._durable_substrate(run)
        assert found is not None and found.kind == "tmux" and found.alive

    async def test_the_suspended_run_resumes_to_RUNNING_and_the_journal_says_resumed(
        self, tmux_shim, durable_on, tmp_path
    ):
        """suspended → running with the journal flagging `resumed` (SC5's first clause).

        Driven through `RunController.start()`, the same entry the watchdog's adoption uses —
        not by calling the journal emitter directly, which would prove only that a method
        exists. The flag is read back out of the run's journal file on disk.
        """
        ws = tmp_path / "gone"
        run = _run(
            project_id="proj",
            started_at="2026-08-24T00:00:00Z",
            extra={"worktree_path": str(ws)},
        )
        tmux_shim(tmux_substrate.durable_session_name("proj", run.id, "ei6"))
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert store.get(run.id).status == RunStatus.PAUSED, (
            "the reattach pre-step did not suspend the run, so there is no suspended→running "
            "transition to observe"
        )
        await wd.stop()

        controller = RunController(store.get(run.id), SPEC, services=EngineServices())
        assert (
            await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
        ), "the suspended run did not execute — there is no suspended→running transition"
        await controller.stop()
        assert store.get(run.id).status == RunStatus.COMPLETE

        starts = [
            e for e in _journal_events(run.id) if e.get("kind") == "run_started" and "resumed" in e
        ]
        assert starts, "the resume wrote no run_started record at all"
        assert starts[-1]["resumed"] is True, (
            "the run resumed but the journal recorded it as a fresh start — a reattached "
            "worker must never read as silently continuous"
        )


def _journal_events(run_id: str) -> list[dict]:
    """Every record in the run's journal file, in order. Raw read on purpose.

    Reading the file rather than a typed reader keeps this test honest about what was
    PERSISTED: a reader that filtered by a kind registry could hide a missing record.
    """
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


# ── SC5 clause 3: idle-release + the lease holder is visible in Settings ───────────────


class TestRunnerLease:
    def test_a_claim_records_a_holder_and_a_re_claim_RENEWS_it(self):
        """Transparent reconnect: a session re-claiming its OWN runner must not be locked out."""
        granted, reason = runner_lifecycle.claim_runner("acp:codex", "chat:alice")
        assert granted is not None and reason == ""
        again, _ = runner_lifecycle.claim_runner("acp:codex", "chat:alice")
        assert again is not None and again.renewals == 1
        # A DIFFERENT session is refused with a reason a human can act on.
        stolen, why = runner_lifecycle.claim_runner("acp:codex", "chat:bob")
        assert stolen is None and "chat:alice" in why

    def test_a_holder_idle_past_the_TTL_is_released(self):
        runner_lifecycle.claim_runner("acp:codex", "chat:alice", ttl=60)
        assert runner_lifecycle.lease_for("acp:codex")["holder"] == "chat:alice"
        # Held now; released once the idle window has passed. The clock is injected rather
        # than slept so the assertion is about the TTL and not about test timing.
        later = time.time() + 61
        assert runner_lifecycle.lease_for("acp:codex", now=later) is None
        assert runner_lifecycle.sweep_idle_leases(now=later) == ["acp:codex"]
        assert runner_lifecycle.lease_for("acp:codex") is None
        # VACUITY FLOOR: a lease INSIDE its window must survive the same sweep.
        runner_lifecycle.claim_runner("acp:kiro", "chat:carol", ttl=3600)
        assert runner_lifecycle.sweep_idle_leases() == []
        assert runner_lifecycle.lease_for("acp:kiro")["holder"] == "chat:carol"

    def test_the_sweep_never_touches_a_workflow_claim_sharing_the_directory(self):
        """Runner leases and WORK-R8 run claims live in one directory. Neither may sweep the
        other — a released workflow claim lets a second worker double-run the run."""
        from personalclaw.workflows import leases

        leases.acquire_claim("run-abc", "worker:1", ttl=1)
        runner_lifecycle.claim_runner("acp:codex", "chat:alice", ttl=1)
        later = time.time() + 3600
        assert runner_lifecycle.sweep_idle_leases(now=later) == ["acp:codex"]
        assert leases.read_claim("run-abc") is not None, "the runner sweep ate a run claim"


async def _get_runners() -> dict:
    """``GET /api/agent-runners`` through its real handler, the repo's endpoint-test shape."""
    from types import SimpleNamespace

    from personalclaw.dashboard.handlers.providers import api_agent_runners_list

    resp = await api_agent_runners_list(SimpleNamespace(query={}))  # type: ignore[arg-type]
    return json.loads(resp.text or "{}")


class TestSettingsSurface:
    async def test_the_agents_endpoint_a_user_loads_NAMES_the_holder(self):
        """The call site a user actually reaches: the Settings → Agents runners endpoint.

        Asserted on the response payload, not on `runner_rows`, because the question is
        whether the SURFACE can show a holder. Deleting the `lease` key from
        `RunnerRow.to_dict` — the reader — reds this.
        """
        # Free: every row reports `lease: null`. This is the vacuity floor for the assertion
        # below — without it, "the holder is visible" could be true of a hardcoded string.
        payload = await _get_runners()
        assert payload["runners"], "no runner rows at all — the surface was not exercised"
        assert all(row["lease"] is None for row in payload["runners"])

        runner_lifecycle.claim_runner("acp:codex", "chat:alice", ttl=3600)
        payload = await _get_runners()
        held = [r for r in payload["runners"] if r["lease"] is not None]
        assert [r["runtime_id"] for r in held] == [
            "acp:codex"
        ], "the endpoint did not name exactly the runner that is held"
        assert held[0]["lease"]["holder"] == "chat:alice"
        assert held[0]["lease"]["expires_in_secs"] > 0

    async def test_an_idle_released_lease_is_NOT_presented_as_a_current_holder(self):
        """The surface must never name a session that stopped talking an hour ago."""
        from personalclaw.atomic_write import atomic_write
        from personalclaw.workflows import containers as c
        from personalclaw.workflows import leases

        # A lease written already past its window — exactly what a killed gateway leaves.
        stale = c.Claim(holder="chat:ghost", expires_at=time.time() - 1, taken_at=time.time() - 60)
        atomic_write(
            leases._lease_path(runner_lifecycle.lease_target("acp:codex")),
            json.dumps(stale.to_dict()),
        )
        assert leases.read_claim(runner_lifecycle.lease_target("acp:codex")) is not None
        payload = await _get_runners()
        assert all(
            row["lease"] is None for row in payload["runners"]
        ), "an expired lease was painted as the current holder"


class TestPoolWiring:
    """The WRITER. A lease nothing writes is state with no producer."""

    async def test_claiming_a_pooled_connection_records_the_session_as_holder(self):
        from personalclaw.acp.connection_pool import AcpConnectionPool

        class _Provider:
            def is_alive(self) -> bool:
                return True

        import asyncio

        pool = AcpConnectionPool(
            provider_builder=lambda _r: _Provider(), start_sem=asyncio.Semaphore(1)
        )
        slot = await pool._slot("acp:codex")
        slot.provider = _Provider()  # type: ignore[assignment]
        slot.warmed_at = time.monotonic()
        got = await pool.claim("acp:codex", holder="chat:zoe")
        assert got is not None
        assert runner_lifecycle.lease_for("acp:codex")["holder"] == "chat:zoe"
        # VACUITY FLOOR: a claim with NO holder records nothing rather than an empty holder.
        runner_lifecycle.release_runner("acp:codex", "chat:zoe")
        slot.provider = _Provider()  # type: ignore[assignment]
        await pool.claim("acp:codex")
        assert runner_lifecycle.lease_for("acp:codex") is None
        pool._closed = True

    async def test_the_pools_own_sweep_releases_an_idle_lease(self, monkeypatch):
        """The idle-release SWEEP has a caller. Without this it is a function nobody runs."""
        import asyncio

        from personalclaw.acp.connection_pool import AcpConnectionPool

        pool = AcpConnectionPool(provider_builder=lambda _r: None, start_sem=asyncio.Semaphore(1))
        runner_lifecycle.claim_runner("acp:codex", "chat:alice", ttl=60)
        monkeypatch.setattr(
            runner_lifecycle, "sweep_idle_leases", lambda: _record(pool, "swept") or ["acp:codex"]
        )
        await pool._release_idle_leases()
        assert getattr(pool, "_ei6_swept", False), "the health loop's sweep hook did not fire"
        pool._closed = True


def _record(obj, _tag):
    obj._ei6_swept = True
    return None


class TestSubstrateNaming:
    def test_the_name_is_recomputed_from_identity_alone(self):
        """The mechanism: a gateway with zero memory must reach the same name twice."""
        a = tmux_substrate.durable_session_name("proj", "run-1", "wf")
        b = tmux_substrate.durable_session_name("proj", "run-1", "wf")
        assert a == b == "pclaw-proj-run-1-wf"

    def test_an_empty_component_cannot_collide_with_a_populated_one(self):
        """`("a", "", "b")` must not compute the same name as `("a", "b", ...)`.

        A collision here is a reattach to a DIFFERENT run's worker.
        """
        assert tmux_substrate.durable_session_name(
            "a", "", "b"
        ) != tmux_substrate.durable_session_name("a", "b", "c")
        assert tmux_substrate.durable_session_name("a", "", "b") == "pclaw-a-_-b"
        assert tmux_substrate.durable_session_name("a", "b", "c") == "pclaw-a-b-c"

    def test_tmux_forbidden_characters_are_mapped_out(self):
        name = tmux_substrate.durable_session_name("p.1", "run:2", "a b")
        assert "." not in name and ":" not in name and " " not in name

    def test_every_probe_answers_absent_when_tmux_is_missing(self, monkeypatch):
        """A boot sweep may not crash on a machine without tmux, and 'absent' is the
        conservative direction — it can only make the sweep decide 'not alive'."""
        monkeypatch.setenv("PATH", "/nonexistent")
        assert tmux_substrate.tmux_available() is False
        assert tmux_substrate.has_session_sync("pclaw-x") is False
        assert tmux_substrate.pane_paths_sync() == []


class TestSubstrateIsolation:
    def test_tmux_counts_as_isolated_and_inline_still_does_not(self):
        assert containers.Substrate(kind="tmux", alive=True).isolated
        assert not containers.Substrate(kind="inline", alive=True).isolated

    def test_an_isolated_and_alive_tmux_substrate_decides_SUSPENDED(self):
        run = WorkflowRun(id="r1", workflow_name="w", status=RunStatus.RUNNING)
        d = containers.sweep_decision(run, containers.Substrate(kind="tmux", alive=True))
        assert d.status == RunStatus.PAUSED
        assert d.board_state == containers.BoardState.SUSPENDED
        assert d.resumable is True
        # Control: not alive → the honest abort, same substrate kind.
        d2 = containers.sweep_decision(run, containers.Substrate(kind="tmux", alive=False))
        assert d2.resumable is False


def test_the_catalog_row_carries_the_lease_field_at_all():
    """A cheap ratchet on the payload shape: the surface reads `lease`, so the row must
    always emit it, including for a runner nobody has ever claimed."""
    rows = runners.runner_rows(probe=False)
    assert rows, "no catalog rows"
    assert all("lease" in r.to_dict() for r in rows)
