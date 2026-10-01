"""A task worker that wrote its finding, or finished its task, is not asked again.

Measured on a parallel Code loop: every task worker's turn was followed by three forced re-prompts.
The cycle driver counted a worker's findings by reading its session key ``loop-<id>-t-<task>`` up
to the LAST dash, which names a loop that does not exist (a task id has a dash of its own), so it
counted nothing, ever. Each re-prompt then told the worker to write ``findings/cycle_NNN.json``,
while its own prompt names ``findings/task_<id>_NNN.json``: finished tasks wrote stage findings
into their own checkout, asked their owner to approve "Cycle 3 — re-check, no changes", and spent
the loop's cycles. And the re-prompts were silent: nothing on the loop's page said they happened.

Now one parser reads a worker's key (``manager.worker_ids``), a worker's new finding is counted the
moment its turn ends, a task worker whose task is done is not asked again, the re-prompt names the
file the worker's own prompt names (``files.finding_file``), and each re-prompt is told to the
loop's page: which worker, why, and how many asks are left.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds, manager, store, tasks_link
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.loop.manager import session_key, task_session_key


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("personalclaw.triggers.nudge._INSTANCE", None)
    kinds.ensure_loaded()
    return tmp_path


def _code_loop() -> tuple[Loop, str]:
    loop = store.create(
        Loop(
            id="",
            name="digest titles",
            kind="code",
            task="add a Fixed line for the digest titles",
            plan=[
                {"stage": "implementation", "title": "Impl", "tasks": [{"title": "CHANGELOG.md"}]}
            ],
            phase_status={"implementation": "active"},
        )
    )
    tasks_link.provision(loop.id)
    asyncio.run(tasks_link.seed_phase_tasks(loop.id))
    loop = store.update_status(loop.id, LoopStatus.RUNNING)
    from personalclaw.tasks import registry

    tasks, _ = asyncio.run(registry.collect_tasks(provider_filter="native"))
    lists = {str(v) for v in (loop.task_list_ids or {}).values()}
    (task,) = [t for t in tasks if t.task_list_id in lists]
    return loop, task.id


def _write_task_finding(loop_id: str, task_id: str) -> None:
    d = loop_files.loop_dir(loop_id)
    (d / "findings" / f"task_{task_id}_001.json").write_text(
        json.dumps({"cycle": 1, "task_id": task_id, "summary": "added the line"})
    )


# ── one reading of a worker's key ─────────────────────────────────────────────


def test_a_workers_key_names_its_loop_and_its_task():
    assert manager.worker_ids("loop-5e1d0c47") == ("5e1d0c47", "")
    assert manager.worker_ids("loop-5e1d0c47-t-2b9c41fe") == ("5e1d0c47", "t-2b9c41fe")
    key = task_session_key("5e1d0c47", "t-2b9c41fe")
    assert manager.worker_ids(key) == ("5e1d0c47", "t-2b9c41fe")
    # The planner is not a worker, and nothing else is.
    assert manager.worker_ids("loop-plan-5e1d0c47") == ("", "")
    assert manager.worker_ids("dashboard-notes") == ("", "")
    assert manager.worker_ids("loop-not-a-loop") == ("", "")


def test_a_task_workers_finding_is_counted_the_moment_its_turn_ends():
    loop, tid = _code_loop()
    key = task_session_key(loop.id, tid)
    assert manager.worker_finding_count(key) == 0

    _write_task_finding(loop.id, tid)  # no watchdog poll in between

    assert manager.worker_finding_count(key) == 1
    assert manager.worker_finding_count(session_key(loop.id)) == 1, "the loop counts it too"


# ── the re-prompt names the file the worker's own prompt names ─────────────────


def test_a_task_workers_re_prompt_names_its_own_finding_file_in_the_loops_folder():
    loop, tid = _code_loop()
    d = str(loop_files.loop_dir(loop.id))
    prompt = manager._task_cycle_nudge(
        loop, type("T", (), {"id": tid, "title": "CHANGELOG.md"})(), "/x/worktree", d
    )
    again = manager.cycle_reprompt(task_session_key(loop.id, tid))

    owed = f"{d}/findings/task_{tid}_NNN.json"
    assert owed in prompt and owed in again
    assert "cycle_NNN" not in again, "a task worker was told to write a stage finding"


@pytest.mark.parametrize("kind", ["code", "goal", "design"])
def test_a_stage_workers_re_prompt_names_the_file_its_cycle_prompt_names(kind):
    loop = store.create(Loop(id="", name="L", kind=kind, task="write the packing note"))
    d = str(loop_files.loop_dir(loop.id))
    prompt = kinds.get(kind).cycle_nudge(loop, d)
    again = manager.cycle_reprompt(session_key(loop.id))

    owed = loop_files.finding_file(d)
    assert owed == f"{d}/findings/cycle_NNN.json"
    assert owed in prompt and owed in again


# ── the cycle driver ──────────────────────────────────────────────────────────


class _Session:
    def __init__(self, key: str) -> None:
        self.key = key
        self._app = "loop"
        self.task: Any = None
        self._last_turn_errored = False
        self._suppress_autonudge_rearm = False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def append(self, role: str, content: str, cls: str = "") -> None:
        return None


class _Row:
    def __init__(self, key: str) -> None:
        self.id, self.session_name, self.active, self.cycle_count = "N1", key, True, 0


class _Nudges:
    """Stands in for `AutoNudgeService` so the gateway hands it the REAL `_fire` callback."""

    last: "_Nudges | None" = None

    def __init__(self, *, base_dir: Any, on_fire: Any) -> None:
        self.on_fire = on_fire
        self.rows: dict[str, _Row] = {}
        _Nudges.last = self

    async def start(self) -> None:
        return None

    def subscribe(self, _observer: Any) -> None:
        return None

    def get_by_session(self, name: str) -> _Row | None:
        return self.rows.get(name)

    async def remove(self, loop_id: str) -> None:
        return None

    def notify_turn_complete(self, name: str, *, errored: bool = False) -> None:
        return None


def _drive_one_cycle(key: str, on_turn, watchdog=None) -> tuple[list[str], MagicMock]:
    """Fire one cycle of worker *key* through the gateway's REAL cycle driver; return the turns
    it ran and the loop page's event hub."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg, no_dashboard=False, no_crons=True, no_open=True)
    session = _Session(key)
    dstate = MagicMock()
    dstate._sessions = {key: session}
    dstate._background_tasks = set()
    dstate.waiting_on_owner.return_value = False
    dstate.sessions.get_provider.return_value = MagicMock(start_fresh_turn_session=AsyncMock())
    orch.dashboard_state = dstate
    turns: list[str] = []

    async def _fake_run_chat(_state: Any, sess: Any, msg: str) -> None:
        turns.append(msg)
        await on_turn(sess)

    nudge = MagicMock(id="N1", session_name=key, message="work the task", stop_sentinel_path="")
    nudge.cycle_count = 0

    async def _go() -> None:
        with (
            patch("personalclaw.gateway.autonudge_enabled", return_value=True),
            patch("personalclaw.gateway.AutoNudgeService", _Nudges),
            patch("personalclaw.dashboard.chat.run_chat", _fake_run_chat),
        ):
            await orch._init_autonudge()
            # The supervisor the gateway started polls on its own; this drives one cycle alone.
            if orch.loop_watchdog is not None:
                await orch.loop_watchdog.stop()
            orch.loop_watchdog = watchdog
            svc = _Nudges.last
            svc.rows[key] = _Row(key)
            with patch("personalclaw.triggers.nudge._INSTANCE", svc):
                assert await svc.on_fire(nudge) is True
                await asyncio.wait_for(session.task, timeout=20)
                await asyncio.sleep(0)  # the turn's done-callbacks run

    asyncio.run(_go())
    return turns, dstate.loop_sse.return_value


def test_a_task_worker_that_wrote_its_finding_gets_no_re_prompt():
    loop, tid = _code_loop()

    async def _writes_its_finding(_sess):
        _write_task_finding(loop.id, tid)

    turns, hub = _drive_one_cycle(task_session_key(loop.id, tid), _writes_its_finding)

    assert len(turns) == 1, f"the worker was asked {len(turns) - 1} more time(s)"
    assert not [c for c in hub.publish.call_args_list if c.args[1] == "reprompt"]


@pytest.mark.parametrize("status", ["done", "cancelled"])
def test_a_finished_task_is_never_re_prompted(status):
    """A hard rule: each forced re-check of a finished task is a whole model turn over its context
    (measured at $0.64 and $1.64 a turn on a large model) and, on an Attended loop, an approval."""
    loop, tid = _code_loop()

    async def _finishes_it(_sess):
        from personalclaw.tasks import registry

        await registry.update_task(tid, provider_name="native", status=status)

    turns, hub = _drive_one_cycle(task_session_key(loop.id, tid), _finishes_it)

    assert len(turns) == 1, "a finished task was asked to re-check its work"
    assert not [c for c in hub.publish.call_args_list if c.args[1] == "reprompt"]


def test_a_task_already_done_before_its_turn_is_not_re_prompted_either():
    loop, tid = _code_loop()
    from personalclaw.tasks import registry

    asyncio.run(registry.update_task(tid, provider_name="native", status="done"))

    async def _writes_nothing(_sess):
        return None

    turns, _hub = _drive_one_cycle(task_session_key(loop.id, tid), _writes_nothing)

    assert len(turns) == 1


def test_a_task_that_cannot_be_read_is_not_re_prompted(monkeypatch):
    """A re-prompt is sent only for work known to be owed."""
    loop, tid = _code_loop()

    async def _unreadable(*_a, **_k):
        raise OSError("the task store is unreadable")

    monkeypatch.setattr("personalclaw.tasks.registry.get_task", _unreadable)

    due, _title = asyncio.run(manager.reprompt_due(task_session_key(loop.id, tid), 0))

    assert due is False


def test_a_worker_asked_again_is_told_what_it_owes_and_the_page_hears_each_ask():
    loop, tid = _code_loop()

    async def _writes_nothing(_sess):
        return None

    turns, hub = _drive_one_cycle(task_session_key(loop.id, tid), _writes_nothing)

    assert len(turns) == 4, "one turn and three re-prompts"
    d = str(loop_files.loop_dir(loop.id))
    assert all(f"{d}/findings/task_{tid}_NNN.json" in t for t in turns[1:])
    asks = [c.args[2] for c in hub.publish.call_args_list if c.args[1] == "reprompt"]
    assert [(a["attempt"], a["of"], a["left"]) for a in asks] == [(1, 3, 2), (2, 3, 1), (3, 3, 0)]
    assert {a["task_id"] for a in asks} == {tid} and {a["title"] for a in asks} == {"CHANGELOG.md"}
    assert {a["file"] for a in asks} == {f"task_{tid}_NNN.json"}
    assert all(c.args[0] == f"loop:{loop.id}" for c in hub.publish.call_args_list)


def test_a_turn_reports_its_outcome_for_the_stage_worker_only():
    """The watchdog counts a loop's stage worker's failed turns; a task worker's own nudge loop
    switches off after its turns keep failing. Read through the one parser, a task worker's turn
    is reported for neither its loop nor a loop id with the task stuck on."""
    loop, tid = _code_loop()

    async def _writes_its_finding(_sess):
        _write_task_finding(loop.id, tid)

    watchdog = MagicMock()
    _drive_one_cycle(task_session_key(loop.id, tid), _writes_its_finding, watchdog)
    assert watchdog.record_turn_outcome.call_args_list == []

    _drive_one_cycle(session_key(loop.id), _writes_its_finding, watchdog)
    assert [(c.args, c.kwargs) for c in watchdog.record_turn_outcome.call_args_list] == [
        ((loop.id,), {"ok": True})
    ]
