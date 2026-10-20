"""Every event in a run's journal has an id of its own, whoever writes it and whenever.

A run's journal is written by more than the controller driving it. The digest's step writes what it
dropped, refused and carried beside the engine; an answer to the digest writes its row after the run
ended; a published artifact opens its question mid-run; a run picked up again after a restart gets a
controller of its own. Each builds its own writer for the run. Measured on the tree before this was
written: every new writer counted from 1, so two answers given after a digest ended both took
``<run>-evt-1``, the id of the run's own first record, and a reader keyed on the id (an outcome
question's answer, the routing feedback's count) took two events for one.

Each event below goes through the door that writes it in the product: the answer's own row writer,
the publish step's outcome question, the controller, each producer's writer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

from personalclaw.config import loader as config_loader
from personalclaw.ledger import JOURNAL_FILE, outcomes
from personalclaw.ledger.kinds import JUDGE_VERDICT, TRIAGE_REPLY
from personalclaw.workflows import journal, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

RUN = "d1e2f3a4"
STEP = "root.children[0]"


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("event_id") or "") for row in rows]


def _ended_digest() -> list[dict[str, Any]]:
    """A digest run as its engine leaves it: started, its one step done, finished. Its rows."""
    store.create(WorkflowRun(id=RUN, workflow_name="morning-triage", status=RunStatus.COMPLETE))
    own = journal.Journal(RUN)
    own.run_started("morning-triage", inputs={}, spec_version=1)
    own.step_started(STEP, "triage", epoch=0, lane="action")
    own.write(journal.STEP_COMPLETED, instance_path=STEP, node_id="triage", epoch=0, state="done")
    own.run_finished("complete")
    return store.read_jsonl(RUN, JOURNAL_FILE)


def _answer(ordinal: str) -> bool:
    """Her "no" to one proposal, recorded as the card's route and a channel reply both record it."""
    from personalclaw.proactive.answer import write_reply_row

    return write_reply_row(
        RUN, ordinal, verb="no", outcome="declined", detail="", answered_by="you"
    )


# ── a writer that comes late ─────────────────────────────────────────────────────────────────


def test_an_answer_given_after_the_run_ended_takes_the_next_id() -> None:
    before = _ended_digest()

    assert _answer("1")

    rows = store.read_jsonl(RUN, JOURNAL_FILE)
    reply = rows[-1]
    assert reply["kind"] == TRIAGE_REPLY
    assert (reply["seq"], reply["event_id"]) == (len(before) + 1, f"{RUN}-evt-{len(before) + 1}")
    assert reply["event_id"] not in _ids(before)
    # The ledger every reader reads (the card's journal, the run page) holds it under that id.
    assert journal.ledger(RUN)[-1] == reply


def test_every_answer_after_the_run_ended_takes_a_number_of_its_own() -> None:
    before = _ended_digest()

    for ordinal in ("1", "2", "3"):
        assert _answer(ordinal)

    rows = store.read_jsonl(RUN, JOURNAL_FILE)
    assert len(set(_ids(rows))) == len(rows) == len(before) + 3
    # In the order they were given, so the card lists them in that order.
    assert [row["seq"] for row in rows] == list(range(1, len(rows) + 1))
    assert [row["item_ordinal"] for row in rows if row["kind"] == TRIAGE_REPLY] == ["1", "2", "3"]


_TWO_STEPS = {
    "name": "notes",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": {"n": 7}}},
            {"kind": "transform", "id": "final", "config": {"expr": "got {{nodes.seed.output.n}}"}},
        ],
    },
}


@pytest.mark.anyio
async def test_a_run_driven_again_after_a_restart_keeps_numbering_its_journal() -> None:
    """The run is driven twice, the second time by the controller a restart builds for it."""
    run = store.create(WorkflowRun(id="", workflow_name="notes"))
    store.write_spec(run.id, _TWO_STEPS)
    for _drive in range(2):
        again = store.get(run.id)
        assert again is not None
        again.status = RunStatus.RUNNING
        controller = RunController(again, _TWO_STEPS, services=EngineServices())
        for inst in controller.instances.values():
            inst.state = InstanceState.PENDING
        await controller.run_to_completion(timeout=20)

    rows = store.read_jsonl(run.id, JOURNAL_FILE)
    assert [row["kind"] for row in rows].count(journal.RUN_STARTED) == 2
    assert len(set(_ids(rows))) == len(rows)
    assert [row["seq"] for row in rows] == list(range(1, len(rows) + 1))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _run_ledger() -> tuple[Callable[[], Any], Callable[[], list[dict[str, Any]]]]:
    store.create(WorkflowRun(id=RUN, workflow_name="notes", status=RunStatus.RUNNING))
    return (lambda: journal.Journal(RUN)), (lambda: store.read_jsonl(RUN, JOURNAL_FILE))


def _loop_ledger() -> tuple[Callable[[], Any], Callable[[], list[dict[str, Any]]]]:
    from personalclaw.loop import files as loop_files
    from personalclaw.loop.journal import LoopJournal

    loop_id = "abcd1234"
    return (lambda: LoopJournal(loop_id)), (lambda: loop_files.read_jsonl(loop_id, JOURNAL_FILE))


def _tile_ledger() -> tuple[Callable[[], Any], Callable[[], list[dict[str, Any]]]]:
    from personalclaw.dashboard import tile_refresh

    key = tile_refresh.tile_key("overview", "artifact:weekly-notes")
    return (lambda: tile_refresh.TileLedger(key)), (
        lambda: tile_refresh._STORE.read_jsonl(key, JOURNAL_FILE)
    )


@pytest.mark.parametrize(
    "ledger", [_run_ledger, _loop_ledger, _tile_ledger], ids=lambda f: f.__name__
)
def test_a_new_writer_continues_the_ledger_it_opens(ledger: Callable[..., Any]) -> None:
    """A workflow run's ledger, a loop's and a dashboard tile's: one writer, then another, then
    the first again, as the engine goes on after a step's provider wrote beside it."""
    writer, read = ledger()
    first = writer()
    first.write(journal.STEP_STARTED, node_id="first", epoch=0)
    first.write(journal.STEP_COMPLETED, node_id="first", epoch=0)

    late = writer().write(journal.STEP_STARTED, node_id="late", epoch=0)
    again = first.write(journal.STEP_STARTED, node_id="again", epoch=0)

    rows = read()
    assert [row["node_id"] for row in rows] == ["first", "first", "late", "again"]
    assert (late["seq"], again["seq"]) == (3, 4)
    assert len(set(_ids(rows))) == 4


# ── two writers at once ──────────────────────────────────────────────────────────────────────


def test_two_answers_at_once_take_two_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two answers to one digest at the same moment: a tap on the card, a reply on her channel.

    The first answer is held between taking its number and writing its line, until the second has
    written or is plainly waiting for it."""
    before = _ended_digest()
    real_append = store.append_jsonl
    numbered = threading.Event()
    second_written = threading.Event()

    def append(run_id: str, filename: str, record: dict[str, Any]) -> None:
        if filename == JOURNAL_FILE and record.get("item_ordinal") == "1":
            numbered.set()
            second_written.wait(timeout=0.5)
        real_append(run_id, filename, record)

    monkeypatch.setattr(store, "append_jsonl", append)
    recorded: list[bool] = []
    first = threading.Thread(target=lambda: recorded.append(_answer("1")))
    first.start()
    assert numbered.wait(timeout=10), "the first answer never reached its write"
    recorded.append(_answer("2"))
    second_written.set()
    first.join(timeout=10)

    assert recorded == [True, True]
    rows = store.read_jsonl(RUN, JOURNAL_FILE)
    replies = [row for row in rows if row["kind"] == TRIAGE_REPLY]
    assert sorted(row["item_ordinal"] for row in replies) == ["1", "2"]
    assert len(set(_ids(rows))) == len(rows)
    assert sorted(row["seq"] for row in replies) == [len(before) + 1, len(before) + 2]
    # The file holds them in the order of their numbers, the order every reader takes them in.
    assert [row["seq"] for row in rows] == list(range(1, len(rows) + 1))
    assert _ids(journal.ledger(RUN)) == [
        row["event_id"] for row in rows if row["kind"] in journal.LEDGER_KINDS
    ]


# ── what the readers see ─────────────────────────────────────────────────────────────────────


def test_two_questions_a_run_opens_are_answered_apart() -> None:
    """Two artifacts the run published, each with its question: did anyone read it? One is
    answered; the other is still open."""
    from personalclaw.workflows.publish_seam import _open_publish_outcome

    store.create(WorkflowRun(id=RUN, workflow_name="weekly-report", status=RunStatus.RUNNING))
    own = journal.Journal(RUN)
    own.run_started("weekly-report", inputs={}, spec_version=1)
    own.step_started(STEP, "publish", epoch=0, lane="action")
    for slug in ("weekly-notes", "weekly-chart"):
        _open_publish_outcome(RUN, "publish", {"slug": slug, "artifact": slug, "action": "create"})

    asked = outcomes.open_questions(journal.ledger(RUN))
    assert sorted(q.record["slug"] for q in asked) == ["weekly-chart", "weekly-notes"]
    notes = next(q for q in asked if q.record["slug"] == "weekly-notes")
    journal.Journal(RUN).resolve_outcome(
        pending_event_id=notes.event_id,
        producer=notes.producer,
        subject=notes.subject,
        metric=notes.metric,
        baseline=notes.baseline,
        measured=1.0,
        score=0.0,
        resolution=outcomes.MEASURED,
    )

    left = outcomes.open_questions(journal.ledger(RUN))
    assert [q.record["slug"] for q in left] == ["weekly-chart"]


def test_a_verdict_a_second_writer_records_is_counted_beside_the_first() -> None:
    """The run's gate is judged twice, the second time by the controller a restart built for it.
    The routing feedback counts both verdicts, not one."""
    from personalclaw.routing import feedback
    from personalclaw.routing.stats import ref_of

    store.create(WorkflowRun(id=RUN, workflow_name="essay", status=RunStatus.RUNNING))
    verdict = {
        "instance_path": "gate",
        "node_id": "gate",
        "epoch": 0,
        "use_case": "reasoning",
        "query_class": "summarize",
        "provider": "fixture-provider",
        "model": "fixture-model",
    }
    journal.Journal(RUN).write(JUDGE_VERDICT, verdict="PASS", **verdict)
    journal.Journal(RUN).write(JUDGE_VERDICT, verdict="REJECT", **verdict)

    cell = ("reasoning", "summarize", ref_of("fixture-provider", "fixture-model"))
    assert feedback.feedback_index(home=config_loader.config_dir())[cell] == (0.5, 2)
