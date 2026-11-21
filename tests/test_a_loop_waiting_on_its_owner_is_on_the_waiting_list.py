"""A loop that waits on its owner is on the waiting list, whatever time its worker wrote.

A loop worker writes its own question (``questions.json``), and ``pending_question`` kept any
``ts`` it wrote, so a worker that wrote its time in words (``"2026-11-12T18:30:00"``) left a ``ts``
the waiting list read with a bare ``float()``. That raised, and every loop's question left the list
at once (``GET /api/waiting``, Needs you, the Inbox), named unreadable, while the loop's own pause
(``manager.question_holds``) read that same question as one that holds. Seen on a dogfood run: a
Code loop sat in ``needs_input`` with a worker's question and the list showed nothing for it.

Now the question's time is a number, its file's when the written one isn't, so the list and the
pause read one time; and the list reads each loop on its own, so one it cannot read leaves every
other loop's question listed and is said. A loop a stage gate paused for its owner (Blocked, the
gate's message its reason) is listed with that message and a steer as its answer, and leaves the
list once steered.

Driven through the real loop store and question files in a home of the test's own, and the real
``waiting.read``.
"""

from __future__ import annotations

import json
import os

import pytest

from personalclaw import waiting
from personalclaw.loop import files as loop_files
from personalclaw.loop import store as loops
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.loop.manager import question_holds

WORDS_TIME = "2026-11-12T18:30:00"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


def _asking(name: str, question: str, *, ts: object = None) -> str:
    """A loop waiting on its owner's answer to *question*, written as a worker writes it: the
    file itself, with *ts* when given."""
    loop = loops.create(Loop(id="", name=name, kind="code", task="t", attended=True))
    loops.update_status(loop.id, LoopStatus.RUNNING)
    asked: dict[str, object] = {"question": question}
    if ts is not None:
        asked["ts"] = ts
    folder = loop_files.loop_dir(loop.id)
    assert folder is not None
    (folder / "questions.json").write_text(json.dumps(asked), encoding="utf-8")
    loops.update_status(loop.id, LoopStatus.NEEDS_INPUT)
    return loop.id


def _listed() -> tuple[dict[str, dict], list[str]]:
    items, unreadable = waiting.read(waiting.Snapshot())
    listed = {
        i["id"].removeprefix("loop_question:"): i for i in items if i["kind"] == "loop_question"
    }
    return listed, unreadable


def test_a_question_whose_worker_wrote_its_time_in_words_is_listed_beside_the_others():
    """🔴 Red before: the bare float() raised, every loop's question left the list, and the list
    named loop questions unreadable."""
    worded = _asking("Fix the README", "May I rewrite the intro?", ts=WORDS_TIME)
    other = _asking("Sort the inbox", "May I archive it?", ts=1_763_000_000.0)

    listed, unreadable = _listed()

    assert set(listed) == {worded, other}, listed
    assert listed[worded]["brief"]["question"] == "May I rewrite the intro?"
    assert "loop_question" not in unreadable


def test_the_list_and_the_loops_pause_read_one_time_for_it():
    """The worded time is read as the file's, a number, so the pause that holds the loop and the
    list that shows it agree."""
    worded = _asking("Fix the README", "May I rewrite the intro?", ts=WORDS_TIME)
    folder = loop_files.loop_dir(worded)
    assert folder is not None
    written_at = os.stat(folder / "questions.json").st_mtime

    question = loop_files.pending_question(worded)

    assert question is not None and question["ts"] == written_at
    assert question_holds(question, attended=True, since=written_at - 1)
    assert not question_holds(question, attended=True, since=written_at + 1)


@pytest.mark.parametrize("ts", [None, True, float("nan"), "soon", [1]], ids=repr)
def test_any_time_that_is_not_a_number_is_read_as_its_files(ts):
    loop_id = _asking("Fix the README", "May I?", ts=ts)

    question = loop_files.pending_question(loop_id)

    assert question is not None and isinstance(question["ts"], float)
    assert question["ts"] > 1_700_000_000


def test_a_loop_whose_question_cannot_be_read_leaves_the_others_listed_and_is_said(monkeypatch):
    broken = _asking("Fix the README", "May I?")
    fine = _asking("Sort the inbox", "May I archive it?")
    reads = waiting._loop_question

    def read(loop, now):
        if loop.id == broken:
            raise OSError(5, "Input/output error")
        return reads(loop, now)

    monkeypatch.setattr(waiting, "_loop_question", read)

    listed, unreadable = _listed()

    assert set(listed) == {fine}
    assert unreadable.count("loop_question") == 1


@pytest.mark.parametrize("broken_how", ["closed", "not json", "not a question"])
def test_a_question_file_that_cannot_be_read_is_said_and_leaves_the_others_listed(broken_how):
    """🔴 Red before: the file read as no question, so the loop was listed waiting on nothing
    (an empty question, no time) and the list said it had read everything."""
    broken = _asking("Fix the README", "May I?")
    fine = _asking("Sort the inbox", "May I archive it?")
    folder = loop_files.loop_dir(broken)
    assert folder is not None
    asked = folder / "questions.json"
    if broken_how == "closed":
        os.chmod(asked, 0)
    else:
        asked.write_text("{not json" if broken_how == "not json" else "[1, 2]", encoding="utf-8")
    try:
        listed, unreadable = _listed()
    finally:
        os.chmod(asked, 0o600)

    assert set(listed) == {fine}, listed
    assert unreadable.count("loop_question") == 1


def test_a_loop_its_stage_gate_paused_is_listed_with_the_gates_words_and_a_steer():
    held = (
        "Every task of stage 'Checks' is done, but its gate can't confirm every exit criterion "
        "from what it observed, and another cycle of its workers would show it nothing new, so "
        "the loop waits for you."
    )
    loop = loops.create(Loop(id="", name="Ship the fix", kind="code", task="t", attended=True))
    loops.update_status(loop.id, LoopStatus.RUNNING)
    loops.update_status(loop.id, LoopStatus.BLOCKED, error_message=held)

    listed, _ = _listed()

    item = listed[loop.id]
    assert item["brief"]["status"] == "blocked"
    assert item["brief"]["why"].startswith("Every task of stage 'Checks' is done")
    assert item["answer"]["path"].endswith(f"/api/loops/{loop.id}/nudge")
    loops.update_status(loop.id, LoopStatus.RUNNING)  # a steer arms it again
    assert loop.id not in _listed()[0]
