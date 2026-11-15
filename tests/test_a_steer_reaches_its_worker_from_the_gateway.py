"""The owner's steer reaches its loop's worker from the gateway, in the cycle's own message.

A steer is written to its loop's folder: ``guidance.txt`` for the stage worker,
``guidance_<task>.txt`` for one task's worker. The gateway hands it over as it delivers the next
cycle's message (the autonudge fire), inside that message, and records the hand-over. It sends it
again with each cycle until a finding written after the hand-over credits it, so a steer delivered
into a turn that failed is not lost. The worker is never told to delete or move the file: the file
is the gateway's, and no worker may change it (``owner_only.LOOP``). A steer is "applied" only once
it was delivered, and a skipped fire takes nothing.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_a_cycle_she_declined_is_not_asked_again import _tmp_home  # noqa: F401 — autouse
from test_a_cycle_she_declined_is_not_asked_again import _poll, _says
from test_a_loop_waiting_on_you_spends_nothing import FIRE, _found, _loop, _worker

from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds, manager

STEER = "Use the archive folder too, and keep the old names."


def _steers(loop_id: str, text: str = STEER, task_id: str = "") -> None:
    """The owner's steer, written as ``manager.nudge`` writes it: the steer file, and its entry
    in the steer history at the cycle it was sent."""
    if task_id:
        loop_files.write_task_guidance(loop_id, task_id, text)
    else:
        loop_files.write_guidance(loop_id, text)
    loop_files.append_nudge(
        loop_id, text, sent_at_cycle=loop_files.cycles_completed(loop_id), task_id=task_id
    )


def _history(loop_id: str) -> list[dict]:
    return loop_files.get_nudges(loop_id)


def _polled(loop) -> None:
    """One watchdog poll, as its own task runs it: on a thread of its own, since the cycle
    driver's steps run inside its event loop."""
    poll = threading.Thread(target=_poll, args=(loop,))
    poll.start()
    poll.join(timeout=60)


def _delivered(worker) -> list[str]:
    """Every message the cycle driver delivered to the worker's chat: each cycle's, and each
    re-prompt's."""
    return [m["content"] for m in worker.session.messages if m.get("role") == "nudge"]


# ── the stage worker ────────────────────────────────────────────────────────────────────────


def test_a_steer_sent_while_a_cycle_runs_reaches_the_next_cycle(tmp_path):
    """🔴 Before: the cycle's finding landed, the watchdog's poll cleared the steer file and marked
    the steer applied, and the next cycle's message never carried it."""
    loop = _loop(attended=False)
    worker = _worker(
        tmp_path,
        loop,
        [
            [lambda: _steers(loop.id), lambda: _found(loop.id, 1), *_says("Listed them.")],
            [lambda: _found(loop.id, 2), *_says("Counted them.")],
        ],
    )

    answered = worker.drive([FIRE, lambda: _polled(loop), FIRE])

    assert answered == [True, True], answered
    cycles = [m for m in _delivered(worker) if m.startswith("[auto-nudge cycle")]
    assert len(cycles) == 2, cycles
    assert STEER not in cycles[0]
    assert STEER in cycles[1], "the next delivered message carries the steer"
    assert "USER STEERING" in cycles[1]
    (entry,) = _history(loop.id)
    assert entry.get("delivered_at"), "the hand-over is on the record"


def test_a_steer_is_applied_only_once_it_was_delivered(tmp_path):
    """🔴 Before: a finding of the cycle the steer arrived in marked it applied, delivered or not."""
    loop = _loop(attended=False)
    worker = _worker(
        tmp_path,
        loop,
        [[lambda: _steers(loop.id), lambda: _found(loop.id, 1), *_says("Listed them.")]],
    )

    worker.drive([FIRE, lambda: _polled(loop)])

    (entry,) = _history(loop.id)
    assert entry.get("applied_cycle") is None and not entry.get("delivered_at"), entry
    assert loop_files.read_guidance(loop.id) == STEER, "the steer waits for the next fire"


def test_a_delivered_steer_is_sent_again_until_a_finding_credits_it(tmp_path):
    """A turn that took the steer and wrote no finding (it failed, or timed out) does not lose
    it: each later cycle carries it again, until a finding written after the hand-over credits
    it, and the next one does not."""
    loop = _loop(attended=False)
    _found(loop.id, 1)
    _steers(loop.id)
    key = manager.session_key(loop.id)

    handed = [manager.steer_message(key) for _ in range(3)]
    loop_files.credit_delivered_steers(loop.id, cycle=1)
    still = manager.steer_message(key)
    _found(loop.id, 2)
    loop_files.credit_delivered_steers(loop.id, cycle=2)

    assert all(STEER in h for h in handed) and STEER in still, "a finding before it credits nothing"
    assert manager.steer_message(key) == ""
    (entry,) = _history(loop.id)
    assert entry.get("applied_cycle") == 2, entry


def test_a_skipped_fire_takes_nothing(tmp_path):
    """A fire that starts no turn (the session is mid-turn) leaves the steer where it is."""
    loop = _loop(attended=False)
    worker = _worker(tmp_path, loop, [[*_says("Listed them.")]])
    _steers(loop.id)
    worker.session._suppress_autonudge_rearm = True

    answered = worker.drive([FIRE])

    assert answered == [False], answered
    assert loop_files.read_guidance(loop.id) == STEER
    (entry,) = _history(loop.id)
    assert not entry.get("delivered_at"), entry


def test_a_steer_reaches_a_re_prompt_on_a_fresh_session(tmp_path):
    """The re-prompt for a missing finding on a fresh session re-sends the cycle's whole message,
    so the steer handed over in it is in the re-prompt too."""
    loop = _loop(attended=False)
    worker = _worker(
        tmp_path,
        loop,
        [[*_says("I forgot the file.")], [lambda: _found(loop.id, 1), *_says("Wrote it.")]],
    )
    worker.state.sessions.get_provider.return_value = SimpleNamespace(
        start_fresh_turn_session=AsyncMock()
    )
    _steers(loop.id)

    worker.drive([FIRE])

    delivered = _delivered(worker)
    assert len(delivered) >= 2, delivered
    assert all(STEER in m for m in delivered), delivered


# ── a task's worker ─────────────────────────────────────────────────────────────────────────


def _task_key(loop_id: str, task_id: str) -> str:
    return manager.task_session_key(loop_id, task_id)


def test_a_tasks_steer_is_handed_over_once_and_kept_until_credited(tmp_path):
    loop = _loop(attended=False)
    task = "t-9f3e2a1b"
    _steers(loop.id, task_id=task)

    first = manager.steer_message(_task_key(loop.id, task))
    again = manager.steer_message(_task_key(loop.id, task))

    assert STEER in first and "USER STEERING" in first
    assert again == first, "sent again until a finding of its own credits it"
    assert loop_files.read_task_guidance(loop.id, task) == "", "the gateway took the file"
    (entry,) = _history(loop.id)
    assert entry.get("delivered_at") and entry.get("task_id") == task, entry

    finding = loop_files.loop_dir(loop.id) / "findings" / f"task_{task}_001.json"
    finding.parent.mkdir(parents=True, exist_ok=True)
    finding.write_text(json.dumps({"cycle": 1, "task_id": task, "summary": "done"}))
    loop_files.record_cycle_findings(loop.id)
    loop_files.credit_delivered_steers(loop.id, cycle=1)

    assert manager.steer_message(_task_key(loop.id, task)) == ""
    (entry,) = _history(loop.id)
    assert entry.get("applied_cycle") == 1, entry


def test_another_tasks_finding_does_not_credit_a_tasks_steer(tmp_path):
    loop = _loop(attended=False)
    mine, other = "t-9f3e2a1b", "t-0c1d2e3f"
    _steers(loop.id, task_id=mine)
    manager.steer_message(_task_key(loop.id, mine))
    finding = loop_files.loop_dir(loop.id) / "findings" / f"task_{other}_001.json"
    finding.parent.mkdir(parents=True, exist_ok=True)
    finding.write_text(json.dumps({"cycle": 1, "task_id": other, "summary": "done"}))
    loop_files.record_cycle_findings(loop.id)
    loop_files.credit_delivered_steers(loop.id, cycle=1)

    assert STEER in manager.steer_message(_task_key(loop.id, mine))


def test_a_task_workers_fixed_message_carries_no_steer(tmp_path):
    """🔴 Before: the steer pending at spawn was written into the message the worker's nudge
    repeats every cycle, so it was sent again for the worker's whole life."""
    loop = _loop(attended=False)
    task = SimpleNamespace(id="t-9f3e2a1b", title="Rename the migrations", description="")
    _steers(loop.id, task_id=task.id)
    folder = str(loop_files.loop_dir(loop.id))

    message = manager._task_cycle_nudge(loop, task, "/tmp/checkout", folder)

    assert STEER not in message
    assert loop_files.read_task_guidance(loop.id, task.id) == STEER, "left for the first fire"


# ── what every worker is told ───────────────────────────────────────────────────────────────


#: A sentence that tells a worker to take a steer file out of the loop's folder.
_TAKES_THE_FILE = re.compile(
    r"(delete|remove|rename|move|unlink|consume)[^.\n]{0,80}guidance"
    r"|guidance[^.\n]{0,80}(delete|remove|rename|move|unlink|consumed)",
    re.IGNORECASE,
)


def _cycle_prompts(loop_dir: str) -> dict[str, str]:
    from personalclaw.agents.defaults import CODER_SYSTEM_PROMPT, LOOP_WORKER_SYSTEM_PROMPT
    from personalclaw.loop.loop import Loop

    kinds.ensure_loaded()
    told = {
        "the loop worker's instructions": LOOP_WORKER_SYSTEM_PROMPT,
        "the coder's instructions": CODER_SYSTEM_PROMPT,
    }
    for kind in ("goal", "code", "design", "general", "research"):
        strat = kinds.get_or_none(kind)
        if strat is None:
            continue
        loop = Loop(id="ab12cd34", name="n", kind=kind, task="Count the migrations.")
        told[f"the {kind} cycle message"] = strat.cycle_nudge(loop, loop_dir)
    skill = Path(__import__("personalclaw").__file__).parent / "skills/bundled/loop-worker/SKILL.md"
    told["the loop-worker skill"] = skill.read_text()
    return told


def test_no_worker_is_told_to_delete_or_move_a_steer_file(tmp_path, monkeypatch):
    """🔴 Before: every loop worker's prompt told it to apply the steer file and delete it."""
    loop = _loop(attended=False)
    task = SimpleNamespace(id="t-9f3e2a1b", title="Rename the migrations", description="")
    folder = str(loop_files.loop_dir(loop.id))
    told = _cycle_prompts(folder)
    told["the task worker's message"] = manager._task_cycle_nudge(loop, task, "/tmp/c", folder)
    import personalclaw.prompt_providers.runtime as prompts

    monkeypatch.setattr(prompts, "render_use_case_prompt", lambda *_a, **_kw: None)
    told["the task worker's message, unrendered"] = manager._task_cycle_nudge(
        loop, task, "/tmp/c", folder
    )

    takes = {
        where: m.group(0) for where, text in told.items() if (m := _TAKES_THE_FILE.search(text))
    }
    assert takes == {}, takes
    for where in ("the task worker's message", "the task worker's message, unrendered"):
        assert "USER STEERING" in told[where], where
