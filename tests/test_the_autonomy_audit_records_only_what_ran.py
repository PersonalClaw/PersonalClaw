"""The security log's record of an action that ran on its own, and the rung the ladder shows for it.

`guardrails.autonomy_executed` is the audit row of an action that ran with nobody watching. Two
things made it untrue:

1. **A pass that did nothing was recorded as one that ran.** The heartbeat queue's trigger fires
   every minute; a pass with no task to run wrote "executed" for an agent turn that never started,
   about 1,440 rows a day, burying the runs that did something. A result that says it did nothing
   (``outcome="skip"``) now writes no row. A command or script is the exception: running it IS the
   effect, so a script that answers ``skip`` is still recorded as having run, and its own answer
   is never what keeps it out of the log.
2. **The rung in the log was not the rung on the ladder.** An unattended run is narrowed to "runs
   with undo", a rung that keeps a handle you can take back. Neither a command nor an agent turn has
   one, so the log said "rung=auto_with_undo reversal=none" for every unattended run of them while
   the Guardrails panel said they run on their own and that nothing had ever run with undo. The
   narrowing now applies only to an action that can be undone, and the panel shows the rung an
   unattended run takes, from the same route the dispatch seams use.

Driven through the real store-trigger dispatch (`GatewayOrchestrator._fire_store_trigger`, the path
every clock, file, webhook and event trigger takes) and the real hook dispatch (`run_script_hook`),
with the real heartbeat, script and task providers: only the agent turn and the script sandbox are
stand-ins.
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.action_providers.heartbeat_tasks_provider import HEARTBEAT_TASKS_TRIGGER_ID
from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import rungs as rg
from personalclaw.guardrails.policy import unattended_dispatch_key

EXECUTED = "guardrails.autonomy_executed"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A throwaway home for the SEL, the run history, tasks, grants and the reversal store.

    ``PERSONALCLAW_HOME`` as well as the patched ``config_dir``: the SEL singleton and the native
    task store resolve their directory from the environment, so patching ``config_dir`` alone would
    leave their writes in the real home.
    """
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(root))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: root)
    cfg = root / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    from personalclaw import sel as sel_mod

    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    yield root
    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False


@pytest.fixture(autouse=True)
def _restore_provider_registry():
    from personalclaw.action_providers.registry import _providers

    before = dict(_providers)
    yield
    _providers.clear()
    _providers.update(before)


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """HEARTBEAT.md in a scratch workspace, and a stand-in for the gateway's heartbeat turn that
    records every task it is handed."""
    import personalclaw.heartbeat as hb

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(hb, "heartbeat_path", lambda: workspace / "HEARTBEAT.md")
    hb.ensure_heartbeat_file()
    handed: list[str] = []

    async def _turn(task: str, deliver: str) -> str:
        handed.append(task)
        return "done"

    hb.set_task_runner(_turn)
    yield types.SimpleNamespace(path=workspace / "HEARTBEAT.md", handed=handed)
    hb.set_task_runner(None)


def _queue_allowed_task(queue: Any, text: str) -> None:
    import personalclaw.heartbeat as hb

    queue.path.write_text(queue.path.read_text(encoding="utf-8") + f"- {text}\n", encoding="utf-8")
    hb.allow(text)


def _fire(provider: str, *, trigger_id: str, config: dict | None = None) -> Any:
    """One clock fire of a granted trigger whose action is *provider*, through the real dispatch."""
    from personalclaw.gateway import GatewayOrchestrator

    trigger = types.SimpleNamespace(
        id=trigger_id,
        kind="clock",
        workflow={"inline": {"provider": provider, "config": dict(config or {})}},
        capabilities={"providers": [provider]},
    )
    asyncio.run(object.__new__(GatewayOrchestrator)._fire_store_trigger(trigger, {"kind": "clock"}))
    return trigger


def _executed_rows() -> list[dict]:
    from personalclaw.sel import sel

    return [e for e in sel().recent(500) if e.get("operation") == EXECUTED]


def _run_statuses(home: Path, trigger_id: str) -> list[str]:
    from personalclaw.schedule_history import ScheduleRunStore

    runs, _total = asyncio.run(ScheduleRunStore(home).list_for_job(trigger_id, 0, 20))
    return [str(r.get("status", "")) for r in runs]


def _ladder() -> dict:
    """``GET /api/autonomy`` — what the Guardrails panel renders."""
    from personalclaw.dashboard.handlers import autonomy as api_h

    resp = asyncio.run(api_h.api_autonomy(make_mocked_request("GET", "/api/autonomy")))
    return json.loads(resp.body.decode())


def _row(view: dict, key: str) -> dict:
    return next(t for t in view["types"] if t["key"] == key)


# ── a pass that did nothing is not an action that ran ──────────────────────────


def test_a_heartbeat_pass_with_nothing_to_run_writes_no_executed_row(home, queue):
    """The every-minute pass over an empty queue: no turn started, so the log says nothing.

    The pass is not lost: its run is in the trigger's history as what it was, a no-op.
    """
    _fire("heartbeat-tasks", trigger_id=HEARTBEAT_TASKS_TRIGGER_ID)
    _fire("heartbeat-tasks", trigger_id=HEARTBEAT_TASKS_TRIGGER_ID)

    assert queue.handed == [], "an empty queue must not start a turn"
    assert _run_statuses(home, HEARTBEAT_TASKS_TRIGGER_ID) == ["skipped_noop", "skipped_noop"]
    assert _executed_rows() == [], "a pass that ran no task was recorded as an executed action"


def test_a_heartbeat_pass_that_ran_a_task_is_recorded_once_at_the_rung_the_ladder_shows(
    home, queue
):
    """The positive half, and the control that makes the empty-queue zero mean something: the same
    fire with one allowed task writes exactly one row, naming the automation and the rung the panel
    shows for an agent turn — the one with no undo, because a turn cannot be taken back."""
    _queue_allowed_task(queue, "Summarise the new files in the reading folder")

    _fire("heartbeat-tasks", trigger_id=HEARTBEAT_TASKS_TRIGGER_ID)

    assert queue.handed == ["Summarise the new files in the reading folder"]
    rows = _executed_rows()
    assert len(rows) == 1, rows
    assert rows[0]["caller_identity"] == "autonomy:action.spawn_turn"
    shown = _row(_ladder(), "action.spawn_turn")["resolved_rung"]
    assert shown == au.RUNG_AUTONOMOUS
    assert f"rung={shown} " in rows[0]["resources"], rows[0]["resources"]
    assert "reversal=none" in rows[0]["resources"]
    assert f"trigger={HEARTBEAT_TASKS_TRIGGER_ID}" in rows[0]["resources"]


def test_a_hook_whose_action_had_nothing_to_do_writes_no_executed_row(home, queue):
    """The hook dispatch is the other seam that records an execution, and it gets the same rule:
    a parentless hook fire is unattended, a no-op writes nothing, and a real run writes one row."""
    from personalclaw.hooks import ScriptHook, run_script_hook

    hook = ScriptHook(
        id="h-queue",
        name="Run the queue on stop",
        event="Stop",
        provider="heartbeat-tasks",
        capabilities={"providers": ["heartbeat-tasks"]},
    )

    asyncio.run(run_script_hook(hook))
    assert hook.last_status == "ok"
    assert _executed_rows() == [], "a hook action that did nothing was recorded as executed"

    _queue_allowed_task(queue, "Tidy the downloads folder")
    asyncio.run(run_script_hook(hook))
    assert queue.handed == ["Tidy the downloads folder"]
    rows = _executed_rows()
    assert len(rows) == 1 and "hook=h-queue" in rows[0]["resources"], rows


def test_a_script_that_answers_skip_is_still_recorded_as_having_run(home, monkeypatch):
    """Running a script IS its effect, so the script's own ``skip`` shapes its history row and its
    delivery but never removes the record that it ran."""
    import personalclaw.schedule_script as script_mod

    ran: list[str] = []

    def _sandbox(script: str, job_id: str, message: str, timeout: int) -> dict:
        ran.append(script)
        return {"status": "skip", "message": "nothing new since the last check"}

    monkeypatch.setattr(script_mod, "run_script_sandboxed", _sandbox)
    trigger = _fire("run-script", trigger_id="t-nightly-check", config={"script": "print(1)"})

    assert ran == ["print(1)"]
    assert _run_statuses(home, trigger.id) == ["skipped_noop"]
    rows = _executed_rows()
    assert len(rows) == 1, "a script that ran left no record because it answered skip"
    assert rows[0]["caller_identity"] == "autonomy:action.execute_code"
    shown = _row(_ladder(), "action.execute_code")["resolved_rung"]
    assert shown == au.RUNG_AUTONOMOUS
    assert f"rung={shown} " in rows[0]["resources"], rows[0]["resources"]


# ── one rung, from the route the seams use, and an undo wherever it promises one ──


def test_an_unattended_task_keeps_its_undo_and_the_ladder_says_it_runs_with_undo(home):
    """The rung that promises an undo is still reached by the action that can keep it: a task an
    automation files while nobody watches can be taken back, the log says so, and the panel shows
    the same rung and lists the undo."""
    trigger = _fire("create-task", trigger_id="t-follow-up", config={"title_template": "Follow up"})

    rows = _executed_rows()
    assert len(rows) == 1, rows
    assert f"rung={au.RUNG_AUTO_WITH_UNDO} " in rows[0]["resources"]
    assert "reversal=task:native:" in rows[0]["resources"]
    assert f"trigger={trigger.id}" in rows[0]["resources"]

    view = _ladder()
    row = _row(view, "action.create_task")
    assert row["resolved_rung"] == au.RUNG_AUTO_WITH_UNDO
    assert rg.rung_label(au.RUNG_AUTO_WITH_UNDO) in row["authority"], row["authority"]
    assert "nobody watching" in row["authority"], row["authority"]
    pending = [r for r in view["reversals"] if not r["reversed_at"]]
    assert [r["action_type"] for r in pending] == ["action.create_task"]


def test_every_action_on_the_ladder_shows_the_rung_its_unattended_fire_runs_at(home):
    """One source: for every declared type and every provider it governs, the rung the panel shows
    is the rung the dispatch seam routes an unattended fire to, so the rung the log records."""
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    view = _ladder()
    dispatch = unattended_dispatch_key("trigger:t-probe")
    compared = 0
    for row in view["types"]:
        for provider in row["providers"]:
            routed = rg.route_provider_action(provider, session_key=dispatch).rung
            assert routed == row["resolved_rung"], (row["key"], provider, routed)
            compared += 1
    assert compared >= 20, "the sweep must cover the declared providers"


def test_no_action_that_cannot_be_undone_runs_on_the_with_undo_rung(home):
    """Wherever an unattended fire lands on "runs with undo", its action can keep an undo."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    dispatch = unattended_dispatch_key("trigger:t-probe")
    with_undo: list[str] = []
    for spec in au.registered_action_types():
        for name in spec.providers:
            if rg.route_provider_action(name, session_key=dispatch).rung != au.RUNG_AUTO_WITH_UNDO:
                continue
            with_undo.append(name)
            assert any(
                getattr(get_action_provider(p), "reversal_kinds", ()) for p in spec.providers
            ), f"{name} ({spec.key}) runs with undo and has nothing to undo"
    assert "create-task" in with_undo and "inbox-op" in with_undo, with_undo
    assert "bash" not in with_undo and "heartbeat-tasks" not in with_undo, with_undo
