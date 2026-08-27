"""LEARN-R4's `surfacing_events` table, its writer, and the call site that reaches it.

**The defect this file guards against is the one it was built to fix.** `surfacing_events` was
named by three consumers and created by none — no schema, no reader, no writer. Shipping a schema
with no reachable writer would reproduce that exact shape one layer down, so the anchor test here
does not test the store: it drives the REAL ``ContextBuilder.build_message`` path and asserts a row
lands, and its companion asserts that a turn which surfaces nothing writes nothing. A test that
reads zero rows from an empty table passes for the wrong reason, so every count assertion below has
a positive and a negative through the same code path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.context import ContextBuilder
from personalclaw.learning import measure
from personalclaw.learning.surfacing_events import (
    DEFAULT_RETENTION_DAYS,
    SurfacingEvent,
    SurfacingEventStore,
)
from personalclaw.memory import MemoryStore
from personalclaw.skills.allocation import SkillRequest, allocate_skills
from personalclaw.skills.loader import SkillsLoader

# Over the 200-token budget the refusal test sets, and no larger. Sized to the assertion on
# purpose: a 42,000-token fixture makes the same point and spends two minutes of tiktoken doing
# it, and the oversized-skill stress case is already covered by `test_skill_allocation`.
_OVERSIZED_BODY = "\n".join(
    f"Step {i}. Do the {i}th thing very carefully and at length, describing every nuance."
    for i in range(1, 41)
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """Redirect the store's default home into tmp, and PROVE the redirect before any write.

    This fixture creates a database, so an unpatched run would create it in the real
    ``~/.personalclaw``. Both bindings are patched — ``config/__init__.py`` binds the name at
    import time, so patching only ``config.loader`` leaves a live alias — and
    ``staging._default_home`` is patched too because that is the function the store actually
    calls. The assertion at the end is the point: a redirect nobody checks is a redirect that
    silently stops working.
    """
    target = tmp_path / "home"
    target.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: target)
    monkeypatch.setattr("personalclaw.config.config_dir", lambda: target)
    monkeypatch.setattr("personalclaw.learning.staging._default_home", lambda: target)

    probe = SurfacingEventStore()
    try:
        assert probe.path.parent == target, (
            f"the store still resolves to {probe.path.parent} — this test would have written to "
            "the real home"
        )
    finally:
        probe.close()
    return target


def _events(store: SurfacingEventStore) -> list[SurfacingEvent]:
    return store.read(days=None)


def _write_skill(base: Path, name: str, frontmatter: str, body: str) -> None:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


def _loader(tmp_path: Path) -> SkillsLoader:
    return SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False)


# ── The table exists ──


def test_the_table_is_created_on_first_use(home):
    """The schema declaration is reached, and it is the thing that creates the table."""
    store = SurfacingEventStore()
    try:
        assert store.record([SurfacingEvent(kind="skill", entity="a")]) == 1
        with store._staging._cursor() as cur:
            names = {
                row[0]
                for row in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table';"
                ).fetchall()
            }
        assert "surfacing_events" in names
    finally:
        store.close()


def test_opening_an_existing_database_is_a_no_op(home):
    """`IF NOT EXISTS` is the whole migration story — a second open must not lose rows.

    This is the house pattern's load-bearing property: the project ships no migration
    machinery, so re-declaring the schema on every open has to be safe.
    """
    first = SurfacingEventStore()
    try:
        first.record([SurfacingEvent(kind="skill", entity="kept")])
    finally:
        first.close()

    second = SurfacingEventStore()
    try:
        assert [e.entity for e in _events(second)] == ["kept"]
    finally:
        second.close()


# ── Writer and reader ──


def test_a_recorded_event_reads_back_whole(home):
    store = SurfacingEventStore()
    try:
        store.record(
            [
                SurfacingEvent(
                    kind="skill",
                    entity="deploy",
                    arm="skill_surfaced",
                    confidence=0.72,
                    used=True,
                    query="ship the thing",
                    session="dashboard:1",
                )
            ]
        )
        (event,) = _events(store)
        assert event.kind == "skill"
        assert event.entity == "deploy"
        assert event.arm == "skill_surfaced"
        assert event.confidence == pytest.approx(0.72)
        assert event.used is True
        assert event.query == "ship the thing"
        assert event.session == "dashboard:1"
        assert event.created_ts > 0, "the prune has nothing to compare against without a stamp"
    finally:
        store.close()


def test_an_empty_store_reads_empty_and_a_written_one_does_not(home):
    """The vacuity pair. The first assertion alone would pass against a store that never writes."""
    store = SurfacingEventStore()
    try:
        assert _events(store) == []
        store.record([SurfacingEvent(kind="skill", entity="a")])
        assert len(_events(store)) == 1
    finally:
        store.close()


def test_an_event_with_no_kind_is_dropped_rather_than_bucketed(home):
    """Storing it would pad an "unknown" bucket nobody wrote — see `from_dict`."""
    store = SurfacingEventStore()
    try:
        assert store.record([SurfacingEvent(kind="", entity="nameless")]) == 0
        assert _events(store) == []
        assert store.record([SurfacingEvent(kind="skill", entity="real")]) == 1
    finally:
        store.close()


@pytest.mark.parametrize("garbage", [None, "nope", 7, {}, {"entity": "no kind"}])
def test_from_dict_is_tolerant_of_unusable_rows(garbage):
    """Tolerant like `per_arm_precision` is: one bad historical row must not kill the report."""
    assert SurfacingEvent.from_dict(garbage) is None


def test_from_dict_survives_a_non_numeric_confidence():
    event = SurfacingEvent.from_dict({"kind": "skill", "confidence": "high"})
    assert event is not None
    assert event.confidence == 0.0


# ── The aggregator's contract ──


def test_to_dict_feeds_per_arm_precision_without_a_mapping_layer(home):
    """The consumer that NAMED this table can read it. Asserts real numbers, not a shape.

    `per_arm_precision` destructures `kind`/`arm`/`used`; if `to_dict` ever renamed one, the
    report would silently collapse into a single unattributed bucket rather than fail.
    """
    store = SurfacingEventStore()
    try:
        store.record(
            [
                SurfacingEvent(kind="skill", entity="a", arm="skill_forced", used=True),
                SurfacingEvent(kind="skill", entity="b", arm="skill_forced", used=False),
                SurfacingEvent(kind="skill", entity="c", arm="skill_surfaced", used=True),
            ]
        )
        stats = {
            (s.kind, s.arm): s
            for s in measure.per_arm_precision([e.to_dict() for e in _events(store)])
        }
    finally:
        store.close()

    assert set(stats) == {("skill", "skill_forced"), ("skill", "skill_surfaced")}
    forced = stats[("skill", "skill_forced")]
    assert (forced.surfaced, forced.used) == (2, 1)
    assert forced.precision == pytest.approx(0.5)
    surfaced_arm = stats[("skill", "skill_surfaced")]
    assert (surfaced_arm.surfaced, surfaced_arm.used) == (1, 1)


# ── The call site: a real surfacing lands a row ──


def test_a_real_turn_lands_a_surfacing_event(home, tmp_path):
    """**The anchor.** Drives the production entry point a user's turn goes through.

    Not `allocate_skills` directly: the gap being closed was a writer nobody reached, so the
    assertion has to run through `ContextBuilder.build_message` — the same call the chat path
    makes — or it would prove only that a function works when called.
    """
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(
        skills,
        "monster",
        "name: monster\ndescription: How to tame a monster in three moves.",
        "# Monster\nOne. Two. Three.\n",
    )
    builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=skills, install_builtins=False),
    )

    msg, _ = builder.build_message(
        "please handle the monster",
        is_new_session=True,
        agent="loop-worker",
        force_skill_ids=["monster"],
    )
    assert "please handle the monster" in msg, "premise: the turn assembled"

    store = SurfacingEventStore()
    try:
        events = _events(store)
    finally:
        store.close()

    assert [e.entity for e in events] == [
        "monster"
    ], "a real surfacing wrote no row — the writer is not reached from production"
    (event,) = events
    assert event.kind == "skill"
    assert event.arm == "skill_forced", "the arm the allocator actually used must be recorded"
    assert event.used is True, "its body reached the prompt, so the mechanical use is True"
    assert event.query == "please handle the monster", "the benchmark miner needs the query text"


def test_a_turn_that_surfaces_nothing_writes_nothing(home, tmp_path):
    """The negative through the SAME path. Without it, the anchor above could pass on a
    writer that appends a row on every turn regardless of what was surfaced."""
    builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    msg, _ = builder.build_message(
        "nothing here resembles a skill", is_new_session=True, agent="loop-worker"
    )
    assert "nothing here resembles a skill" in msg, "premise: the turn assembled"

    store = SurfacingEventStore()
    try:
        assert _events(store) == []
    finally:
        store.close()


# ── `used` is derived mechanically, not asserted ──


def test_used_follows_what_reached_the_prompt(home, tmp_path):
    """A refused skill records `used=False`; an admitted one records `used=True`.

    §2.5 allows `used` to come only from a mechanical observation, and
    `SkillAllocation.loaded` is that observation — REFUSED is excluded there because crediting
    a use to a skill the agent never saw would train the ranker on the allocator's failures.
    """
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "small", "name: small\ndescription: A tiny skill.", "Do the thing.\n")
    # No `description` and no `resources:`, so there is no L1 summary to degrade to and the
    # oversized body is the only thing on offer — the one shape that reaches REFUSED. A skill
    # WITH a description gets REDUCED instead, which counts as loaded (its summary really is in
    # the prompt) and would make this a test of the wrong state.
    _write_skill(skills, "huge", "name: huge", _OVERSIZED_BODY)
    loader = _loader(tmp_path)

    result = allocate_skills(
        loader,
        [
            SkillRequest(name="small", content=loader.load_skill("small") or "", score=0.9),
            SkillRequest(name="huge", content=loader.load_skill("huge") or "", score=0.8),
        ],
        query="do the thing",
        budget_tokens=200,
        session="test:1",
    )
    assert "small" in result.loaded, "premise: the small skill loaded"
    assert "huge" not in result.loaded, "premise: the oversized skill did not"

    store = SurfacingEventStore()
    try:
        by_entity = {e.entity: e for e in _events(store)}
    finally:
        store.close()

    assert set(by_entity) == {"small", "huge"}, (
        "every OFFER needs a row: precision is used ÷ surfaced, and dropping the losers would "
        "make the denominator the numerator"
    )
    assert by_entity["small"].used is True
    assert by_entity["huge"].used is False
    assert by_entity["huge"].session == "test:1", "the session the caller passed must be stored"


def test_the_recorded_confidence_is_the_score_the_candidate_competed_on(home, tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "small", "name: small\ndescription: A tiny skill.", "Do it.\n")
    loader = _loader(tmp_path)

    allocate_skills(
        loader,
        [SkillRequest(name="small", content=loader.load_skill("small") or "", score=0.37)],
        query="q",
    )
    store = SurfacingEventStore()
    try:
        (event,) = _events(store)
    finally:
        store.close()
    assert event.confidence == pytest.approx(0.37), (
        "a threshold proposal is about the scores actually seen, so a constant here would make "
        "`propose_thresholds` untunable"
    )


# ── Retention ──


def test_prune_drops_old_events_and_keeps_fresh_ones(home):
    """The vacuity pair for the prune: a DELETE that removes everything also "passes"."""
    day = 86400.0
    now = 1_000_000.0
    store = SurfacingEventStore()
    try:
        store.record(
            [SurfacingEvent(kind="skill", entity="ancient", created_ts=now - 200 * day)],
            now=now,
        )
        store.record(
            [SurfacingEvent(kind="skill", entity="recent", created_ts=now - 3 * day)],
            now=now,
        )
        assert len(_events(store)) == 2, "premise: both rows are present before the prune"

        removed = store.prune(retention_days=DEFAULT_RETENTION_DAYS, now=now)
        assert removed == 1
        assert [e.entity for e in _events(store)] == ["recent"]
    finally:
        store.close()


def test_the_retention_window_is_the_ninety_days_the_plan_states():
    """§2.5: "Events prune at 90d on the curator tick." Pinned so a silent edit is visible."""
    assert DEFAULT_RETENTION_DAYS == 90


def test_the_curator_tick_prunes_surfacing_events():
    """An AST assertion on the tick, following `test_learning_promotion_wire`'s precedent.

    §2.5 puts the prune "on the curator tick", so the wire is part of the deliverable. Read from
    the syntax tree rather than the file's text because a text scan also matches the word in a
    comment, and would keep passing over a prune that had been commented out.
    """
    import ast

    tree = ast.parse(Path("src/personalclaw/history.py").read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_learning_curator"
    )
    called = {
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    constructed = {
        node.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    assert (
        "SurfacingEventStore" in constructed
    ), "the curator tick no longer constructs the store — the 90d prune has no trigger"
    assert "prune" in called, "the store is constructed on the tick but never pruned"


# ── Reading window ──


def test_the_read_window_excludes_events_outside_it(home):
    day = 86400.0
    now = 1_000_000.0
    store = SurfacingEventStore()
    try:
        store.record([SurfacingEvent(kind="skill", entity="old", created_ts=now - 30 * day)])
        store.record([SurfacingEvent(kind="skill", entity="new", created_ts=now - 1 * day)])
        assert len(store.read(days=None, now=now)) == 2, "premise: both rows exist"
        assert [e.entity for e in store.read(days=7, now=now)] == ["new"]
    finally:
        store.close()


def test_reading_a_kind_filters_rather_than_returning_everything(home):
    store = SurfacingEventStore()
    try:
        store.record(
            [
                SurfacingEvent(kind="skill", entity="s"),
                SurfacingEvent(kind="lesson", entity="l"),
            ]
        )
        assert len(_events(store)) == 2, "premise: both kinds are stored"
        assert [e.entity for e in store.read(kind="skill", days=None)] == ["s"]
    finally:
        store.close()


# ── The panel reader ──


def test_the_health_panel_reads_the_table_that_is_written(home):
    """`_precision_from_events` returns real numbers, and `(None, 0, 0)` only when empty.

    The function it replaced read `usage.UsageStore`, which has no production writer, so it
    returned `(None, 0, 0)` on every box. This asserts the replacement is not merely a
    different way of reporting nothing.
    """
    from personalclaw.dashboard.handlers.learning import _precision_from_events

    assert _precision_from_events(7) == (None, 0, 0), "premise: nothing recorded yet"

    store = SurfacingEventStore()
    try:
        store.record(
            [
                SurfacingEvent(kind="skill", entity="a", arm="skill_forced", used=True),
                SurfacingEvent(kind="skill", entity="b", arm="skill_forced", used=False),
            ]
        )
    finally:
        store.close()

    precision, surfaced, used = _precision_from_events(7)
    assert (surfaced, used) == (2, 1)
    assert precision == pytest.approx(0.5)
