"""Every steer the owner sends reaches the loop's workers, in the order it was sent, the later one
winning, and a steer to the whole loop reaches its task workers that start after it.

The gateway hands the owner's steer to a loop worker in its next cycle's message
(``loop_files.hand_over_steer``). Two gaps were left:

* A steer was written over the one already waiting (``guidance.txt``, ``guidance_<task>.txt``), so
  of two sent before the worker's next cycle only the second reached it, though the history said
  both had. And a worker given several steers was not told which one wins where they disagree:
  a dogfood run's worker asked exactly that.
* A steer to the whole loop of a parallel Code loop reached only the task workers running at that
  moment. A task that started later never had it: in the run that found it, a steer kept one task
  off a destructive cleanup, and each later task of the loop needed the same steer.

Now each steer waits beside the others, with when it was sent; two or more are handed over oldest
first, each with its time, under the words that the later one is the one to follow. A task worker
is given the loop's own steers as it starts, once.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_a_cycle_she_declined_is_not_asked_again import _tmp_home  # noqa: F401 — autouse
from test_a_loop_waiting_on_you_spends_nothing import _found, _loop
from test_a_steer_reaches_its_worker_from_the_gateway import _history, _steers, _task_key

from personalclaw.loop import files as loop_files
from personalclaw.loop import manager

FIRST = "Keep the old file names."
SECOND = "Rename the files after all: use the new names."
LOOP_WIDE = "Never delete the fixtures folder, whatever a task's cleanup says."


def test_two_steers_sent_before_the_next_cycle_both_reach_it_the_later_winning():
    """🔴 Red before: the second was written over the first, and only it reached the worker."""
    loop = _loop(attended=False)
    _steers(loop.id, FIRST)
    _steers(loop.id, SECOND)

    said = manager.steer_message(manager.session_key(loop.id))

    assert FIRST in said and SECOND in said, said
    assert said.index(FIRST) < said.index(SECOND), "oldest first"
    assert "follow the later one" in said
    assert "(sent " in said
    assert all(entry.get("delivered_at") for entry in _history(loop.id))


def test_once_a_finding_credits_them_neither_is_shown_again():
    loop = _loop(attended=False)
    _found(loop.id, 1)
    _steers(loop.id, FIRST)
    _steers(loop.id, SECOND)
    key = manager.session_key(loop.id)
    manager.steer_message(key)
    _found(loop.id, 2)

    loop_files.credit_delivered_steers(loop.id, cycle=2)

    assert manager.steer_message(key) == ""
    assert [entry.get("applied_cycle") for entry in _history(loop.id)] == [2, 2]


def test_one_steer_is_handed_over_in_its_own_words():
    loop = _loop(attended=False)
    _steers(loop.id, FIRST)

    said = manager.steer_message(manager.session_key(loop.id))

    assert said.endswith(f"\n{FIRST}"), said
    assert "follow the later one" not in said


def test_a_steer_file_written_before_steers_were_kept_one_a_line_is_one_steer():
    loop = _loop(attended=False)
    folder = loop_files.loop_dir(loop.id)
    assert folder is not None
    (folder / "guidance.txt").write_text("Use the archive folder too.\nAnd keep the old names.\n")

    said = manager.steer_message(manager.session_key(loop.id))

    assert said.endswith("\nUse the archive folder too.\nAnd keep the old names."), said


def test_a_full_steer_file_keeps_the_steers_sent_last_not_the_lines_written_last():
    """🔴 Red before: the file was cut by position, so a steer written last but sent long before
    the others (a file put back from an older copy) was kept, and a newer one was lost."""
    loop = _loop(attended=False)
    folder = loop_files.loop_dir(loop.id)
    assert folder is not None
    kept = loop_files._PENDING_KEPT
    newer = [{"text": f"steer {n}", "sent_at": 1000.0 + n} for n in range(kept)]
    oldest = {"text": "the oldest steer", "sent_at": 1.0}
    (folder / "guidance.txt").write_text("".join(json.dumps(s) + "\n" for s in [*newer, oldest]))

    loop_files.write_guidance(loop.id, SECOND)

    texts = [s["text"] for s in loop_files._read_pending(folder / "guidance.txt")]
    assert texts == [*(f"steer {n}" for n in range(1, kept)), SECOND], texts


# ── a parallel loop's tasks ─────────────────────────────────────────────────────────────────


async def _spawned(loop, task_id: str) -> None:
    """The real ``spawn_task_worker`` for *task_id*, on a gateway and a cycle service of the
    test's own: a new task's worker starting."""
    session = SimpleNamespace()
    state = SimpleNamespace(
        get_or_create_session=lambda **_kw: session, push_sessions_update=lambda: None
    )
    svc = SimpleNamespace(get_by_session=lambda _key: None, add=AsyncMock())
    task = SimpleNamespace(id=task_id, title="Tidy the migrations", description="")
    await manager.spawn_task_worker(state, svc, loop, task, "/tmp/checkout")
    svc.add.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_loop_steer_reaches_a_task_that_starts_after_it_once():
    """🔴 Red before: the steer went only to the task workers running when it was sent, and a
    task that started later ran without it."""
    loop = _loop(attended=False)
    later = "t-5a6b7c8d"
    _steers(loop.id, LOOP_WIDE)  # sent to the whole loop, before this task's worker started

    await _spawned(loop, later)
    first = manager.steer_message(_task_key(loop.id, later))

    assert LOOP_WIDE in first and "USER STEERING" in first, first
    task_finding = loop_files.loop_dir(loop.id) / "findings" / f"task_{later}_001.json"
    task_finding.parent.mkdir(parents=True, exist_ok=True)
    task_finding.write_text(json.dumps({"cycle": 1, "task_id": later, "summary": "done"}))
    loop_files.credit_delivered_steers(loop.id, cycle=1)
    assert manager.steer_message(_task_key(loop.id, later)) == "", "not shown to it again"


def test_a_loop_steer_already_waiting_for_a_task_is_not_given_twice():
    loop = _loop(attended=False)
    task = "t-5a6b7c8d"
    loop_files.write_task_guidance(loop.id, task, LOOP_WIDE)  # the fan-out to a running task
    _steers(loop.id, LOOP_WIDE)

    assert loop_files.seed_task_steers(loop.id, task) == 0
    assert loop_files.read_task_guidance(loop.id, task) == LOOP_WIDE


def test_a_tasks_own_steer_and_the_loops_reach_it_in_the_order_they_were_sent():
    loop = _loop(attended=False)
    task = "t-5a6b7c8d"
    _steers(loop.id, LOOP_WIDE)
    loop_files.seed_task_steers(loop.id, task)
    _steers(loop.id, SECOND, task_id=task)

    said = manager.steer_message(_task_key(loop.id, task))

    assert said.index(LOOP_WIDE) < said.index(SECOND), said
    assert "follow the later one" in said
