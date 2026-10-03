"""A lesson update memory refuses keeps the lesson it would have replaced, and counts nothing.

``write_lesson`` retired the lessons a new one replaces before it asked whether the new one could
be kept. Each was pointed at the new lesson's key and taken out of recall, and only then did the
store's own rules judge the new lesson (the wording memory refuses, its size limit, the confidence
a lesson needs), or a lesson memory already held turn out to cover it. A refused update left the
old lesson replaced by a lesson that does not exist: it dropped out of recall and out of every
prompt, and Memory listed neither. Now a lesson is authorised and validated before anything is
changed or counted, and the lessons it replaces are retired in the same transaction that stores it.

Its dedup also counted a sighting of a lesson memory already held before anything asked whether the
work may change memory, so a Temporary chat's work, or an app's that was not given your memory,
raised the confidence of a lesson it could never have written. Such work is refused first now.

What an earlier version left replaced by a lesson that was never kept comes back when memory opens.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personalclaw import memory_writes, supply_chain, vector_memory
from personalclaw.learning.lesson_confidence import get_store
from personalclaw.memory_lint import lint_memory
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore

#: What she taught, and an update that says the same and more, so it would replace it.
TAUGHT = "Keep the seed trays on the north bench"
UPDATE = "Keep the seed trays on the north bench, out of the wind"
#: An update past the size memory keeps for one lesson.
TOO_LONG = TAUGHT + ", " + "and check the soil along every row of the allotment, " * 90
#: The words the stand-in rules below refuse. Which wording the store's own rule refuses is that
#: rule's business; what this file pins is what a refusal leaves behind.
REFUSED_WORDING = "out of the wind"
#: The key the code before the fix left her lesson pointing at: no lesson was ever kept under it.
NEVER_KEPT = "lesson.0123456789ab"

#: Work whose writes memory refuses: a Temporary chat's, and an app's that was not given your
#: memory (here, one that is not installed at all).
REFUSING: dict[str, Callable[[], AbstractContextManager[None]]] = {
    "temporary chat": lambda: memory_writes.derived_from(
        "dashboard:chat-1-1790800000", memory_mode="temporary"
    ),
    "app not given memory": lambda: memory_writes.derived_from(
        "dashboard:chat-2-1790800100", app="allotment-planner"
    ),
}


def _open(db: Path, **kwargs) -> VectorMemoryStore:
    store = VectorMemoryStore(db_path=db, **kwargs)
    store.init()
    return store


@pytest.fixture
def vs(tmp_path):
    store = _open(tmp_path / "memory.db")
    yield store
    store.close()


@pytest.fixture
def svc(vs):
    return MemoryService.over_vector_store(vs)


def _row(vs: VectorMemoryStore, text: str) -> dict | None:
    """The lesson row that holds *text*, live or not."""
    row = vs.db.execute(
        "SELECT * FROM semantic_memory WHERE key LIKE 'lesson.%' AND value_json = ?",
        (json.dumps(text),),
    ).fetchone()
    return dict(row) if row else None


def _recalled(svc: MemoryService, query: str = "seed trays north bench") -> list[str]:
    """The lessons ``memory_recall`` answers *query* with."""
    return [lesson["text"] for lesson in svc.recall_lessons(query_text=query)]


def _evidence(vs: VectorMemoryStore, text: str):
    row = _row(vs, text)
    assert row is not None, f"no lesson holds {text!r}"
    return get_store(vs.db_path.parent).evidence_for(row["key"])


def _kept_as_it_was(vs: VectorMemoryStore, svc: MemoryService, *, refused: str) -> None:
    """Her lesson is live, replaced by nothing, recalled, in the prompt, and seen once; the
    refused update is nowhere."""
    row = _row(vs, TAUGHT)
    assert row is not None and row["is_deleted"] == 0, "her lesson was retired"
    assert row["superseded_by"] is None and row["invalidated_at"] is None
    assert _recalled(svc) == [TAUGHT]
    assert TAUGHT in vs.get_lessons_context()
    assert _evidence(vs, TAUGHT).observations == 1, "her lesson's sightings moved"
    assert _row(vs, refused) is None, "the refused update was stored"
    retired = [
        e
        for e in vs.get_events(limit=50)
        if e["event_type"] == "supersede" and e["memory_key"] == row["key"]
    ]
    assert retired == [], "the history says her lesson was replaced"


def _attempt(write: Callable[[], object]) -> object:
    """What *write* returned, or the refusal it raised."""
    try:
        return write()
    except memory_writes.MemoryWriteRefused as refused:
        return refused


def _scan_flags_the_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """The write scan an untrusted source's text passes reads the update as dangerous."""
    scan = supply_chain.default_scanner.scan_text

    def stand_in(text: str, *, surface: str = "manifest") -> supply_chain.ScanReport:
        if REFUSED_WORDING not in text:
            return scan(text, surface=surface)
        finding = supply_chain.Finding(
            surface=surface,
            severity=supply_chain.Verdict.DANGEROUS,
            rule="stand_in_rule",
            path="",
            evidence=REFUSED_WORDING,
        )
        return supply_chain.ScanReport(verdict=supply_chain.Verdict.DANGEROUS, findings=[finding])

    monkeypatch.setattr(supply_chain.default_scanner, "scan_text", stand_in)


# ── each check that refuses an update leaves her lesson as it was ───────────────────────────


def _the_write_scan(tmp_path, monkeypatch):
    _scan_flags_the_update(monkeypatch)
    vs = _open(tmp_path / "memory.db")

    def attempt(svc):
        assert svc.write_lesson(UPDATE, source="consolidation") is False

    return vs, UPDATE, attempt


def _refused_work(work: str):
    def arm(tmp_path, monkeypatch):
        vs = _open(tmp_path / "memory.db")

        def attempt(svc):
            with REFUSING[work]():
                refused = _attempt(lambda: svc.write_lesson(UPDATE, source="consolidation"))
            assert isinstance(refused, memory_writes.MemoryWriteRefused), refused

        return vs, UPDATE, attempt

    return arm


def _the_wording_memory_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(vector_memory, "_contains_injection", lambda text: REFUSED_WORDING in text)
    vs = _open(tmp_path / "memory.db")

    def attempt(svc):
        assert svc.write_lesson(UPDATE) is False

    return vs, UPDATE, attempt


def _the_size_limit(tmp_path, monkeypatch):
    vs = _open(tmp_path / "memory.db")

    def attempt(svc):
        assert svc.write_lesson(TOO_LONG) is False

    return vs, TOO_LONG, attempt


def _the_confidence_a_lesson_needs(tmp_path, monkeypatch):
    # She asks memory to keep only what it is surer of than a learned lesson's 0.9.
    vs = _open(tmp_path / "memory.db", confidence_threshold=0.95)

    def attempt(svc):
        assert svc.write_lesson(UPDATE, source="consolidation") is False

    return vs, UPDATE, attempt


REFUSALS = {
    "the write scan": _the_write_scan,
    "a temporary chat's work": _refused_work("temporary chat"),
    "an app's work without your memory": _refused_work("app not given memory"),
    "the wording memory refuses": _the_wording_memory_refuses,
    "the size limit": _the_size_limit,
    "the confidence a lesson needs": _the_confidence_a_lesson_needs,
}


@pytest.mark.parametrize("check", sorted(REFUSALS))
def test_an_update_a_check_refuses_leaves_the_lesson_it_would_replace_recallable(
    check, tmp_path, monkeypatch
):
    """🔴 Red on integration for the store's own rules (its wording, its size limit, the confidence
    a lesson needs): her lesson was retired toward the refused update's key and recalled nowhere.
    The scan and the two kinds of refused work were refused before the retirement was written."""
    vs, refused, attempt = REFUSALS[check](tmp_path, monkeypatch)
    try:
        svc = MemoryService.over_vector_store(vs)
        assert svc.write_lesson(TAUGHT) is True

        attempt(svc)

        _kept_as_it_was(vs, svc, refused=refused)
    finally:
        vs.close()


def test_an_update_memory_keeps_retires_the_lesson_it_replaces(vs, svc):
    assert svc.write_lesson(TAUGHT) is True

    assert svc.write_lesson(UPDATE) is True

    old, new = _row(vs, TAUGHT), _row(vs, UPDATE)
    assert old is not None and new is not None
    assert old["is_deleted"] == 1 and old["superseded_by"] == new["key"]
    assert new["is_deleted"] == 0 and new["superseded_by"] is None
    assert _recalled(svc) == [UPDATE]
    assert vs.get_supersession_chain(old["key"])[-1]["key"] == new["key"]
    # Her sighting of the first came with it, beside its own.
    assert _evidence(vs, UPDATE).observations == 2
    events = [(e["event_type"], e["memory_key"], e["new_value"]) for e in vs.get_events(limit=20)]
    assert ("supersede", old["key"], new["key"]) in events


def test_a_lesson_memory_already_holds_is_counted_once_and_retires_nothing(vs, svc):
    """🔴 Red on integration: a lesson the repeat shared a topic with was retired toward the
    repeat's key, and then a lesson that already says the repeat ended the pass, so it was left
    replaced by a lesson that was never kept."""
    says_it = "Mulch the roses in late autumn after pruning the climbing varieties carefully"
    shares_a_topic = (
        "Feed the roses monthly with tomato fertiliser during summer evenings near walls"
    )
    assert svc.write_lesson(says_it) is True
    assert svc.write_lesson(shares_a_topic) is True  # the newest, so the pass reads it first

    assert svc.write_lesson("Mulch the roses") is False

    for text in (says_it, shares_a_topic):
        row = _row(vs, text)
        assert row is not None and row["is_deleted"] == 0, f"{text!r} was retired"
        assert row["superseded_by"] is None
    assert _row(vs, "Mulch the roses") is None
    assert _evidence(vs, says_it).observations == 2, "the repeat is a sighting of the one it says"
    assert _evidence(vs, shares_a_topic).observations == 1


@pytest.mark.parametrize("work", sorted(REFUSING))
def test_work_that_may_change_no_memory_counts_no_sighting(work, vs, svc):
    """🔴 Red on integration: the repeat counted a sighting of her lesson, and three such repeats
    lift a learned lesson into every prompt."""
    assert svc.write_lesson(TAUGHT, source="consolidation") is True

    with REFUSING[work]():
        refused = _attempt(lambda: svc.write_lesson(TAUGHT, source="consolidation"))

    assert _evidence(vs, TAUGHT).observations == 1, "a sighting was counted for work kept nowhere"
    assert isinstance(refused, memory_writes.MemoryWriteRefused), refused


def test_a_lesson_is_never_retired_toward_one_that_is_not_kept(vs, svc):
    """🔴 Red on integration: the store pointed her lesson at a key that holds nothing."""
    assert svc.write_lesson(TAUGHT) is True
    key = _row(vs, TAUGHT)["key"]

    assert svc.supersede_semantic(key, NEVER_KEPT, "user_explicit") is False

    _kept_as_it_was(vs, svc, refused=UPDATE)


def _fails_on(statement: str) -> Callable[..., None]:
    """A statement check that fails the statement starting with *statement*, as a full disk
    fails a write: the store's own transaction is what is under test, so its refusal rule is not
    needed here."""

    def check(sql: str, *, script: bool = False) -> None:
        if " ".join(sql.split()).upper().startswith(statement):
            raise sqlite3.OperationalError("database or disk is full")

    return check


@pytest.mark.parametrize(
    "failing",
    [
        # 🔴 Red on integration: her lesson's retirement was committed before this write ran.
        "INSERT INTO SEMANTIC_MEMORY",
        "UPDATE SEMANTIC_MEMORY SET IS_DELETED = 1, SUPERSEDED_BY",
    ],
    ids=["the update's own row", "the retirement of hers"],
)
def test_an_update_whose_write_fails_part_way_changes_nothing(failing, vs, svc, monkeypatch):
    assert svc.write_lesson(TAUGHT) is True
    monkeypatch.setattr(vs.db, "statement_check", _fails_on(failing))

    with pytest.raises(sqlite3.OperationalError):
        svc.write_lesson(UPDATE)

    _kept_as_it_was(vs, svc, refused=UPDATE)


def test_a_lesson_taught_again_after_its_replacement_was_removed_stays_through_the_sweep(vs, svc):
    """🔴 Red on integration: taught again, it came back still marked replaced, and the Health
    sweep that removes lessons replaced long ago deleted it outright."""
    assert svc.write_lesson(TAUGHT) is True
    assert svc.write_lesson(UPDATE) is True
    assert svc.delete_lesson(REFUSED_WORDING) is True  # she removes the update
    assert svc.write_lesson(TAUGHT) is True  # and teaches the first again

    lint_memory(vs, now=datetime.now(tz=timezone.utc) + timedelta(days=120))

    row = _row(vs, TAUGHT)
    assert row is not None and row["is_deleted"] == 0
    assert row["superseded_by"] is None
    assert _recalled(svc) == [TAUGHT]


# ── what an earlier version left ───────────────────────────────────────────────────────────


def _left_the_old_way(db: Path, *, then: str = "") -> str:
    """Her lesson as the code before the fix left it after a refused update: retired toward the
    update's key, which holds nothing, with its sightings moved onto that key. *then* is a lesson
    she taught afterwards, which nothing retired hers toward. Returns her lesson's key."""
    store = _open(db)
    try:
        assert store.write_lesson(TAUGHT) is True
        key = _row(store, TAUGHT)["key"]
        now = datetime.now(tz=timezone.utc).isoformat()
        store.db.execute(
            "UPDATE semantic_memory SET is_deleted = 1, superseded_by = ?, invalidated_at = ?, "
            "updated_at = ? WHERE key = ?",
            (NEVER_KEPT, now, now, key),
        )
        store.db.commit()
        get_store(db.parent).carry_forward(key, NEVER_KEPT)
        if then:
            assert store.write_lesson(then) is True
        return key
    finally:
        store.close()


def _repairs(vs: VectorMemoryStore, key: str) -> list[dict]:
    return [
        e
        for e in vs.get_events(limit=50)
        if e["memory_key"] == key
        and e["event_type"] in ("restore", "supersede")
        and e["source"] == "repair"
    ]


def test_a_lesson_left_replaced_by_one_never_kept_comes_back_when_memory_opens(tmp_path):
    """🔴 Red on integration: nothing ever brought it back."""
    db = tmp_path / "memory.db"
    key = _left_the_old_way(db)

    vs = _open(db)
    try:
        svc = MemoryService.over_vector_store(vs)
        _kept_as_it_was(vs, svc, refused=UPDATE)
        assert _evidence(vs, TAUGHT).human_authored, "it came back as hers"
        assert [e["event_type"] for e in _repairs(vs, key)] == ["restore"]
        assert _repairs(vs, key)[0]["old_value"] == NEVER_KEPT
    finally:
        vs.close()

    again = _open(db)
    try:
        assert [e["event_type"] for e in _repairs(again, key)] == ["restore"], "restored twice"
        _kept_as_it_was(again, MemoryService.over_vector_store(again), refused=UPDATE)
    finally:
        again.close()


def test_a_lesson_left_replaced_by_one_never_kept_points_at_the_one_she_taught_since(tmp_path):
    """She taught the update again and memory kept it: her first lesson stays retired, now pointing
    at the lesson that says it and more, rather than coming back beside it."""
    db = tmp_path / "memory.db"
    key = _left_the_old_way(db, then=UPDATE)

    vs = _open(db)
    try:
        svc = MemoryService.over_vector_store(vs)
        new = _row(vs, UPDATE)
        assert new is not None and new["is_deleted"] == 0
        old = _row(vs, TAUGHT)
        assert old["is_deleted"] == 1 and old["superseded_by"] == new["key"]
        assert _recalled(svc) == [UPDATE]
        assert _evidence(vs, UPDATE).observations == 2, "her first lesson's sighting came with it"
        assert [e["event_type"] for e in _repairs(vs, key)] == ["supersede"]
    finally:
        vs.close()


def test_a_lesson_replaced_by_one_memory_keeps_or_by_one_she_removed_stays_retired(tmp_path):
    db = tmp_path / "memory.db"
    store = _open(db)
    try:
        svc = MemoryService.over_vector_store(store)
        assert svc.write_lesson(TAUGHT) is True
        assert svc.write_lesson(UPDATE) is True  # kept: hers points at it
        assert svc.write_lesson("Sow the beans in April") is True
        assert svc.write_lesson("Sow the beans in April, two to a pot") is True
        assert svc.delete_lesson("two to a pot") is True  # she removed what replaced it
    finally:
        store.close()

    vs = _open(db)
    try:
        for text in (TAUGHT, "Sow the beans in April"):
            row = _row(vs, text)
            assert row["is_deleted"] == 1 and row["superseded_by"], f"{text!r} came back"
            assert _repairs(vs, row["key"]) == []
    finally:
        vs.close()
