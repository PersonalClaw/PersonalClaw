"""LV-4 (LEARNING-VISIBILITY T2.4/T2.5): the periodic identity report.

The atom's ``done_when`` is four measurable claims, and each has its own section below:

1. *"counts byte-match store contents"* — every expected count here is derived from the
   STORE, independently of the code under test: raw SQL over ``semantic_memory`` for
   facets and lessons, a directory listing for skills, and the proposal JSON files read by
   this file rather than by ``list_pending()``. A count compared against a constant the
   same author wrote proves only that the author was consistent.
2. *"zero writes to any learning store (inspected before/after)"* — proved by observation,
   with a SHA-256 witness written here (never a helper the production path also calls) over
   the whole ``skills/`` tree plus ``memory.db``'s bytes. The witness carries its own
   vacuity floor: it must be shown to DETECT a write before its silence means anything.
3. *"no-model fixture still produces the deterministic sections"* — both no-model shapes
   (a falsy completion and a raised provider error), asserted against a byte-identical
   comparison with the deterministic compose, plus the working-model control that proves
   the comparison is not vacuous.
4. *"quiet-hours suppresses the ping but not the artifact"* — both directions from ONE
   clock read, so the suppressing and delivering windows differ only in where they sit
   relative to now. A suppression test that would pass with suppression removed is worth
   nothing, so the delivering window IS the vacuity floor.

Every store is redirected under ``tmp_path`` and **every redirect is asserted**. Four of
the five stores bind ``config_dir`` at IMPORT (``inbox``, ``artifacts.native``,
``providers.entity_routes``, and the loader itself), so patching the loader alone would
leave three of them writing into the real ``~/.personalclaw`` — which matters unusually
much in this file, whose subject is a function that must not write.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw import learning_report as LR
from personalclaw.skills import loader as loader_mod
from personalclaw.skills import proposals
from personalclaw.skills.loader import AutoSkillProvenance, SkillsLoader

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every store this report touches, under ``tmp_path``, with each redirect PROVEN.

    The assertions are the point. ``skills.loader`` resolves ``config_dir`` lazily, but
    ``inbox``, ``artifacts.native`` and ``providers.entity_routes`` each did
    ``from personalclaw.config.loader import config_dir`` at module scope, so their name is
    already bound and a single loader patch does not reach them.
    """
    import personalclaw.artifacts.native as native_mod
    import personalclaw.config.loader as loader_pkg
    import personalclaw.dashboard.state as state_mod
    import personalclaw.inbox as inbox_mod
    import personalclaw.providers.entity_routes as entity_mod
    import personalclaw.skills.marketplace as mp

    for mod in (loader_mod, loader_pkg, inbox_mod, native_mod, entity_mod, state_mod):
        monkeypatch.setattr(mod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])

    assert loader_mod.skills_dir() == tmp_path / "skills"
    assert str(proposals._proposals_dir()).startswith(str(tmp_path))
    assert str(entity_mod._entity_settings_path("notifications")).startswith(str(tmp_path))
    assert str(inbox_mod.InboxStore()._path).startswith(str(tmp_path))
    assert str(native_mod.NativeArtifactProvider()._root).startswith(str(tmp_path))
    # `dashboard.state` is the FIFTH import-bound store, and the one that bites hardest here:
    # `DashboardState.__init__` calls `_load_notifications()`, so without this patch a fresh
    # state starts pre-loaded with the developer's REAL `~/.personalclaw` notification log —
    # measured, six rows — and `_persist_notification` appends the test's own deliveries to
    # it. Every `_notification_log == []` assertion below depends on this line.
    assert str(state_mod._notifications_path()).startswith(str(tmp_path))
    return tmp_path


@pytest.fixture
def artifacts(home, monkeypatch):
    """The artifact provider, pinned to ``tmp_path`` in the module-level registry.

    ``registry._providers`` is a process-wide dict: whichever test first calls
    ``get_provider()`` mints the native provider and its root is frozen from whatever
    ``config_dir`` said THEN. Patching ``native.config_dir`` cannot undo that, so the entry
    is replaced through ``monkeypatch.setitem`` (restored automatically) rather than left
    for the next test to inherit.
    """
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.native import NativeArtifactProvider

    provider = NativeArtifactProvider(root=home / "artifacts")
    monkeypatch.setitem(registry._providers, "native", provider)
    assert registry.get_provider() is provider
    return provider


_EMBED_DIM = 16


def _embed(text: str) -> list[float]:
    """A one-hot embedding keyed on the text's own hash.

    A CONSTANT stub (``lambda t: [1.0, 0.0, 0.0]``) makes every pair of lessons 100%
    cosine-similar, and ``write_lesson`` drops a >85%-similar duplicate — so a fixture
    seeding two lessons silently stored one and the count assertions below would have been
    measuring the stub. Measured: the second ``write_lesson`` returned False. One-hot on a
    hash keeps the embedding path exercised while distinct texts stay orthogonal.
    """
    idx = int(hashlib.sha256(text.encode()).hexdigest(), 16) % _EMBED_DIM
    return [1.0 if i == idx else 0.0 for i in range(_EMBED_DIM)]


def _memory(tmp_path):
    """A real vector store — the backing for both facets and lessons."""
    from personalclaw.vector_memory import VectorMemoryStore

    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    vs = VectorMemoryStore(db_path=Path(tmp_path) / "memory.db", embedding_dim=_EMBED_DIM)
    vs.init()
    vs.embed_fn = _embed
    return vs


def _state(tmp_path):
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.memory import MemoryStore

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    mem = MemoryStore(workspace=ws)
    mem.init()
    mem.vector_store = _memory(tmp_path)
    cb = MagicMock()
    cb.memory = mem
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, context_builder=cb)
    return state, mem.vector_store


def _req(state, *, query=None, session_key="dashboard:ui"):
    req = MagicMock()
    req.app = {"state": state}
    req.headers = {"X-Session-Key": session_key}
    req.query = query or {}
    req.get = lambda k, d=None: {"user": "owner"}.get(k, d)
    return req


# ── seeding, always through the production writer ──────────────────────────────────


def _create(slug: str, *, created_at: str) -> str:
    name = SkillsLoader(install_builtins=False).create_auto_skill(
        slug,
        description=f"what {slug} does",
        triggers=slug,
        procedure_md=f"1. do {slug}",
        provenance=AutoSkillProvenance(session_key="sess:1", created_at=created_at),
    )
    assert name, f"create_auto_skill refused {slug!r}"
    return name


def _enqueue(slug: str) -> None:
    prop = proposals.enqueue(
        slug=slug,
        description=f"proposed {slug}",
        triggers=slug,
        procedure_md=f"1. {slug}",
        session_key="sess:3",
        created_at=_iso(1),
        kind="new",
        refine_target="",
        source_excerpt="",
    )
    assert prop is not None, f"enqueue refused {slug!r}"


def _facet(vs, text: str, *, days_ago: float = 1.0, cls: str = "style") -> None:
    from personalclaw.preference_facets import upsert_facet

    upsert_facet(vs, cls, text, cue="explicit", now=NOW - timedelta(days=days_ago))


def _lesson(vs, rule: str) -> None:
    from personalclaw.memory_service import MemoryService

    assert MemoryService.over_vector_store(vs).write_lesson(rule, category="process")


# ── independent store counts (never a second call to the code under test) ──────────


def _store_facet_count(vs) -> int:
    return int(
        vs.db.execute(
            "SELECT COUNT(*) FROM semantic_memory "
            "WHERE is_deleted = 0 AND key LIKE 'pref.facet.%'"
        ).fetchone()[0]
    )


def _store_lesson_count(vs) -> int:
    return int(
        vs.db.execute(
            "SELECT COUNT(*) FROM semantic_memory WHERE is_deleted = 0 AND key LIKE 'lesson.%'"
        ).fetchone()[0]
    )


def _store_auto_skill_count(home: Path) -> int:
    root = home / "skills" / "auto"
    return len([d for d in root.iterdir() if (d / "SKILL.md").is_file()]) if root.is_dir() else 0


def _store_pending_proposal_count(home: Path) -> int:
    # `.proposals`, dotted. Spelled literally so this count is independent of the module —
    # and asserted against the module's constant so a rename fails LOUDLY instead of reading
    # as an honest zero, which is exactly how the first draft of this helper passed nothing.
    assert proposals._PROPOSALS_DIRNAME == ".proposals", "the proposal store moved"
    d = home / "skills" / ".proposals"
    if not d.is_dir():
        return 0
    n = 0
    for p in d.glob("*.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("status") == "pending":
                n += 1
        except (OSError, ValueError):
            continue
    return n


# ── 1. counts byte-match store contents ────────────────────────────────────────────


def test_every_section_count_matches_what_the_store_holds(home, tmp_path):
    """Four counts, four independent derivations straight out of the store.

    Deliberately asymmetric group sizes (3/2/4/2): equal sizes would let a section wired to
    the wrong gather still agree with its expected value.
    """
    vs = _memory(tmp_path)
    for i in range(3):
        _facet(vs, f"prefers thing {i}")
    # Semantically distinct on purpose: `write_lesson` dedups on >50% topic-word overlap
    # within a scope bucket, so "always do step 0"/"step 1" collapse to ONE row — measured.
    for rule in ("always run make lint before committing", "sqlite beats json for tables"):
        _lesson(vs, rule)
    for i in range(4):
        _create(f"skill-{i}", created_at=_iso(3))
    for i in range(2):
        _enqueue(f"proposal-{i}")

    report = LR.compose_identity_report(now=NOW, vs=vs)

    assert (_store_facet_count(vs), _store_lesson_count(vs)) == (3, 2), "fixture drift"
    assert report.facets.count == _store_facet_count(vs)
    assert report.lessons.count == _store_lesson_count(vs)
    assert report.skills.count == _store_auto_skill_count(home)
    assert report.proposals.count == _store_pending_proposal_count(home)
    assert report.total == 3 + 2 + 4 + 2


def test_memory_figures_are_the_stores_own_counts_verbatim(home, tmp_path):
    """The memory subset is not re-derived — it is ``memory_stats()``'s own numbers."""
    vs = _memory(tmp_path)
    _facet(vs, "prefers terse replies")
    _lesson(vs, "always run make lint")

    report = LR.compose_identity_report(now=NOW, vs=vs)
    stats = vs.memory_stats()

    assert report.memory, "no memory figures were gathered at all"
    for key, value in report.memory.items():
        assert value == stats[key], f"{key} drifted from the store's own count"


def test_a_count_is_exact_while_the_item_list_is_a_bounded_sample(home, tmp_path):
    """``count`` is the full group size; ``items`` is capped; neither derives from the other.

    The expected count is the loop bound — an independent number — so a truncation bug
    cannot agree with itself.
    """
    seeded = LR._MAX_ITEMS + 5
    assert seeded > LR._MAX_ITEMS, "the fixture must exceed the cap or this proves nothing"
    vs = _memory(tmp_path)
    for i in range(seeded):
        _facet(vs, f"prefers variant {i:03d}")

    report = LR.compose_identity_report(now=NOW, vs=vs)

    assert report.facets.count == seeded == _store_facet_count(vs)
    assert len(report.facets.items) == LR._MAX_ITEMS
    assert report.facets.truncated == 5
    assert f"showing {LR._MAX_ITEMS} of {seeded}" in LR.render_markdown(report)


def test_only_learned_skills_are_counted_not_bundled_ones(home, tmp_path):
    """The report describes what this system LEARNED.

    A hand-authored skill outside ``auto/`` is written to the same tree and must NOT appear
    — counting it would inflate "skills I built" with things the user installed. The
    ``auto/`` sibling is the control that proves the filter is not simply matching nothing.
    """
    _create("learned-thing", created_at=_iso(3))
    hand = home / "skills" / "hand-written"
    hand.mkdir(parents=True)
    (hand / "SKILL.md").write_text(
        "---\nname: hand-written\ndescription: mine\n---\n1. do it\n", encoding="utf-8"
    )

    report = LR.compose_identity_report(now=NOW, vs=None)

    assert [s["name"] for s in report.skills.items] == ["auto/learned-thing"]
    assert report.skills.count == 1


def test_the_facet_state_comes_from_the_decay_not_the_stored_score(home, tmp_path):
    """A facet stored at high stability but never reinforced reads as faded.

    The report must not claim a preference is shaping replies when the surfacing path has
    already stopped trusting it. The fresh facet is the vacuity floor: without it, a bug
    that reported every facet as Dropped would pass.
    """
    vs = _memory(tmp_path)
    _facet(vs, "fresh preference", days_ago=0)
    _facet(vs, "ancient preference", days_ago=400)

    by_text = {f["text"]: f for f in LR.compose_identity_report(now=NOW, vs=vs).facets.items}

    assert by_text["fresh preference"]["state"] == "Active"
    assert by_text["ancient preference"]["state"] == "Dropped"
    assert by_text["ancient preference"]["stability"] < by_text["fresh preference"]["stability"]


def test_no_memory_store_degrades_to_the_non_memory_sections(home):
    """An API-only home: facets and lessons read empty because there is nothing to read."""
    _create("standalone", created_at=_iso(1))

    report = LR.compose_identity_report(now=NOW, vs=None)

    assert (report.facets.count, report.lessons.count) == (0, 0)
    assert report.skills.count == 1
    assert report.memory == {}


def test_the_window_is_clamped_to_the_declared_bounds(home):
    assert LR.compose_identity_report(window_days=0, now=NOW).window_days == LR.MIN_WINDOW_DAYS
    assert LR.compose_identity_report(window_days=99999, now=NOW).window_days == LR.MAX_WINDOW_DAYS


# ── 2. zero writes to any learning store, inspected before/after ───────────────────


def _witness(home: Path) -> dict[str, str]:
    """A SHA-256 map over every learning store. Written HERE, on purpose.

    Not ``atomic_write``, not a repo snapshot helper, not the report's own reader: a witness
    that shares code with its subject can certify itself. ``skills/`` covers the skill tree,
    the proposal records, the sidecar overlays and the usage counter; ``memory.db`` covers
    facets and lessons. The inbox is deliberately EXCLUDED and the exclusion is measured in
    ``test_the_witness_ignores_the_inbox_because_list_pending_backfills_it``.
    """
    out: dict[str, str] = {}
    for root in (home / "skills",):
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(home))] = hashlib.sha256(p.read_bytes()).hexdigest()
    for db in sorted(home.glob("memory.db*")):
        # `-shm` is EXCLUDED: it is SQLite's shared-memory coordination file, rebuilt from
        # the WAL on every connection, so its bytes move on a pure read. Measured here — it
        # differed across a read-only compose. `-wal` and `-journal` ARE witnessed, because
        # those carry uncheckpointed DATA and a write that only reached the WAL is still a
        # write.
        if db.name.endswith("-shm"):
            continue
        out[str(db.relative_to(home))] = hashlib.sha256(db.read_bytes()).hexdigest()
    return out


def _seed_everything(home: Path, tmp_path: Path):
    vs = _memory(tmp_path)
    _facet(vs, "prefers terse replies")
    _lesson(vs, "always run make lint")
    _create("fresh-thing", created_at=_iso(2))
    _enqueue("still-pending")
    return vs


def test_the_witness_detects_a_write_so_its_silence_means_something(home, tmp_path):
    """The vacuity floor for every zero-write assertion below.

    A hasher that returned a constant, or a walk that matched no files, would make the
    real checks pass unconditionally. So: prove the witness is non-empty, then perform one
    byte-sized write into a learning store and prove the witness NOTICES.
    """
    _seed_everything(home, tmp_path)

    before = _witness(home)
    assert before, "the witness saw no files at all — every check below would be vacuous"
    assert any(k.endswith("memory.db") for k in before), "the memory store is not witnessed"

    (home / "skills" / "auto" / "fresh-thing" / "SKILL.md").write_text("tampered", encoding="utf-8")

    assert _witness(home) != before


def test_composing_the_report_writes_to_no_learning_store(home, tmp_path):
    """The atom's headline claim, proved by observation rather than by intent.

    Hashes before, hashes after, byte-for-byte. A composer that wrote and then restored
    would still fail: the SQLite journal/WAL siblings are witnessed too, and a restored
    file rarely round-trips to the same bytes.
    """
    vs = _seed_everything(home, tmp_path)

    before = _witness(home)
    report = LR.compose_identity_report(now=NOW, vs=vs)
    assert report.total > 0, "an empty report would prove nothing about writing"

    assert _witness(home) == before


def test_rendering_and_a_second_compose_are_also_write_free(home, tmp_path):
    """Two composes plus a render, because a lazy cache would only write on the first."""
    vs = _seed_everything(home, tmp_path)
    LR.render_markdown(LR.compose_identity_report(now=NOW, vs=vs))

    before = _witness(home)
    LR.render_markdown(LR.compose_identity_report(now=NOW, vs=vs))

    assert _witness(home) == before


def test_the_witness_ignores_the_inbox_because_list_pending_backfills_it(home, tmp_path):
    """A named, measured exclusion — not a convenient blind spot.

    ``proposals.list_pending()`` calls ``backfill_inbox_items()``, which raises an inbox row
    for any pending proposal that lacks one. That write is inherited from the shared read
    path (LV-3's block calls the same function) and the inbox is not a learning store, so
    the criterion's "any learning store" is satisfied. This asserts the boundary explicitly
    so nobody later widens the witness and mistakes a known inbox write for a regression.
    """
    from personalclaw.skills import proposals as prop_mod

    _seed_everything(home, tmp_path)
    calls: list[int] = []
    real = prop_mod.backfill_inbox_items
    prop_mod.backfill_inbox_items = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    try:
        LR.compose_identity_report(now=NOW, vs=None)
    finally:
        prop_mod.backfill_inbox_items = real

    assert calls, "list_pending no longer backfills — the exclusion above may be stale"
    assert not any("inbox" in k for k in _witness(home)), "the witness must not cover the inbox"


# ── 3. the no-model floor is real and named ────────────────────────────────────────


def _patch_model(monkeypatch, result):
    """Replace the ONE model call. ``result`` is returned, or raised when an exception."""
    calls: list[str] = []

    async def fake(prompt, **kwargs):
        calls.append(prompt)
        if isinstance(result, BaseException):
            raise result
        return result

    import personalclaw.llm_helpers as helpers

    monkeypatch.setattr(helpers, "one_shot_completion", fake)
    return calls


@pytest.mark.asyncio
async def test_no_model_still_produces_every_deterministic_section(home, tmp_path, monkeypatch):
    """The floor, both shapes, against a byte-identical comparison.

    ``one_shot_completion`` returns a FALSY value rather than raising when nothing
    resolves, so an ``except`` alone would never fire — both are exercised. The comparison
    is the payload minus the narrative fields, so it fails if any figure moved.
    """
    vs = _seed_everything(home, tmp_path)
    deterministic = LR.compose_identity_report(now=NOW, vs=vs).to_payload()
    numeric = {k: v for k, v in deterministic.items() if k not in ("narrative", "markdown")}

    for outcome in ("", RuntimeError("no provider resolved")):
        calls = _patch_model(monkeypatch, outcome)
        report = await LR.build_identity_report(now=NOW, vs=vs)

        assert calls, "the narrative pass never ran, so the floor was not exercised"
        assert report.narrative_status == LR.NARRATIVE_UNAVAILABLE
        assert report.narrative == ""
        got = {
            k: v
            for k, v in report.to_payload().items()
            if k not in ("narrative", "markdown", "narrative_status")
        }
        assert got == {k: v for k, v in numeric.items() if k != "narrative_status"}
        # NAMED in the document, not swallowed into a blank page.
        assert "No model was available to summarise this period" in LR.render_markdown(report)


@pytest.mark.asyncio
async def test_a_working_model_adds_prose_and_changes_no_figure(home, tmp_path, monkeypatch):
    """The control that makes the floor test above non-vacuous.

    If the sections were empty or constant regardless, the comparison would hold either
    way. Here a model DOES answer: the narrative appears and every figure is unchanged.
    """
    vs = _seed_everything(home, tmp_path)
    deterministic = LR.compose_identity_report(now=NOW, vs=vs)
    _patch_model(monkeypatch, "You like short answers and lint-clean commits.")

    report = await LR.build_identity_report(now=NOW, vs=vs)

    assert report.narrative_status == LR.NARRATIVE_WRITTEN
    assert "short answers" in report.narrative
    assert (report.facets.count, report.lessons.count, report.skills.count) == (
        deterministic.facets.count,
        deterministic.lessons.count,
        deterministic.skills.count,
    )
    assert "In a sentence" in LR.render_markdown(report)


@pytest.mark.asyncio
async def test_an_empty_record_spends_no_model_call(home, monkeypatch):
    """Nothing to narrate ⇒ SKIPPED, which is a different fact from UNAVAILABLE."""
    calls = _patch_model(monkeypatch, "should never be reached")

    report = await LR.build_identity_report(now=NOW, vs=None)

    assert report.total == 0
    assert report.narrative_status == LR.NARRATIVE_SKIPPED
    assert not calls


def test_the_model_is_never_shown_a_count(home, tmp_path):
    """The narrative cannot misquote a figure it was never given.

    Asserted on the facts payload rather than on the prose: the seeded group sizes are
    absent from what the model reads, and the fenced marker is present, so the record
    arrives as data rather than as instructions.
    """
    from personalclaw.security import fence_untrusted

    vs = _memory(tmp_path)
    for i in range(7):
        _facet(vs, f"prefers style {i}")
    report = LR.compose_identity_report(now=NOW, vs=vs)

    facts = LR._narrative_facts(report)

    assert "prefers style 0" in facts, "the record reached the model empty"
    assert "7" not in facts.replace("prefers style 7", "")
    assert str(report.facets.count) not in facts.replace("prefers style 7", "")
    assert "untrusted_content" in fence_untrusted(facts, source="learning")


# ── 4. delivery: artifact first, then one attention item ───────────────────────────


def _notifications(home: Path, settings: dict) -> None:
    import personalclaw.providers.entity_routes as entity_mod

    entity_mod._save_entity_settings("notifications", settings)
    assert entity_mod.load_notifications_settings()["quiet_hours_enabled"] == settings.get(
        "quiet_hours_enabled", False
    )


def _window_around(offset_hours: int) -> tuple[str, str]:
    """A two-hour quiet window centred ``offset_hours`` from LOCAL now.

    ``DashboardState.notify`` calls ``notification_allowed`` without a ``now``, so the gate
    reads the local wall clock. Both directions are derived from ONE read of that clock, so
    the suppressing and delivering cases differ only in where the window sits.
    """
    centre = datetime.now() + timedelta(hours=offset_hours)
    start = (centre - timedelta(hours=1)).strftime("%H:%M")
    end = (centre + timedelta(hours=1)).strftime("%H:%M")
    return start, end


@pytest.mark.asyncio
async def test_delivery_writes_the_artifact_and_links_it_from_the_inbox(
    home, artifacts, tmp_path, monkeypatch
):
    """The full delivery: a versioned artifact, and one inbox row that points at it."""
    from personalclaw.inbox import InboxStore

    state, vs = _state(tmp_path)
    _facet(vs, "prefers terse replies")
    _create("fresh-thing", created_at=_iso(2))
    _patch_model(monkeypatch, "You favour brevity.")

    delivery = await LR.deliver_identity_report(state, vs=vs, now=NOW)

    assert delivery.artifact_slug == LR.ARTIFACT_SLUG
    assert delivery.artifact_version >= 1
    stored = artifacts.get(LR.ARTIFACT_SLUG)
    assert stored is not None and "How I've adapted to you" in stored.content
    assert "You favour brevity." in stored.content

    inbox = InboxStore()
    inbox.load()
    rows = [i for i in inbox.items.values() if i.item_kind == "report"]
    assert len(rows) == 1
    assert rows[0].refs["artifact"] == LR.ARTIFACT_SLUG
    assert delivery.inbox_item_id == rows[0].id


@pytest.mark.asyncio
async def test_quiet_hours_suppresses_the_ping_but_not_the_artifact(
    home, artifacts, tmp_path, monkeypatch
):
    """Both directions, one clock read. The delivering case IS the vacuity floor.

    A suppression assertion that would also hold with suppression removed proves nothing,
    so the same fixture runs twice: once with the quiet window over now (no notification,
    artifact and inbox row both durable) and once with it hours away (notification
    delivered). If the second leg did not deliver, the first leg's silence would be
    meaningless.
    """
    from personalclaw.inbox import InboxStore

    state, vs = _state(tmp_path)
    _facet(vs, "prefers terse replies")
    _patch_model(monkeypatch, "")

    start, end = _window_around(0)
    _notifications(
        home,
        {"quiet_hours_enabled": True, "quiet_hours_start": start, "quiet_hours_end": end},
    )
    quiet = await LR.deliver_identity_report(state, vs=vs, now=NOW)

    assert state._notification_log == [], "quiet hours did not suppress the ping"
    assert quiet.artifact_slug == LR.ARTIFACT_SLUG, "quiet hours must not lose the artifact"
    assert artifacts.get(LR.ARTIFACT_SLUG) is not None
    inbox = InboxStore()
    inbox.load()
    assert [i for i in inbox.items.values() if i.item_kind == "report"], "the durable row was lost"

    # The floor: move the window off now and the SAME call delivers. Same state, same store,
    # same function — only the window moved, so a green here cannot come from a different
    # fixture. `now` advances a month because the dedup key is the calendar month: without
    # that, the second delivery would be swallowed as a duplicate and the floor would read
    # like quiet hours were still suppressing.
    state._notification_log.clear()
    start, end = _window_around(6)
    _notifications(
        home,
        {"quiet_hours_enabled": True, "quiet_hours_start": start, "quiet_hours_end": end},
    )
    await LR.deliver_identity_report(state, vs=vs, now=NOW + timedelta(days=40))

    assert [n["kind"] for n in state._notification_log] == [LR.NOTIFY_KIND]


@pytest.mark.asyncio
async def test_a_second_delivery_in_the_same_month_reuses_the_row_and_does_not_re_ping(
    home, artifacts, tmp_path, monkeypatch
):
    """The month is the idempotency key, so a re-run cannot stack rows or double-notify.

    A monthly job firing twice is a real event (the boot sweep re-arms an overdue trigger;
    a user can run one by hand). The next-month leg is the vacuity floor: if the dedup key
    were a constant, that delivery would also be swallowed.
    """
    from personalclaw.inbox import InboxStore

    state, vs = _state(tmp_path)
    _facet(vs, "prefers terse replies")
    _patch_model(monkeypatch, "")

    first = await LR.deliver_identity_report(state, vs=vs, now=NOW)
    second = await LR.deliver_identity_report(state, vs=vs, now=NOW + timedelta(days=1))

    assert second.inbox_item_id == first.inbox_item_id
    assert len(state._notification_log) == 1

    later = await LR.deliver_identity_report(state, vs=vs, now=NOW + timedelta(days=40))
    assert later.inbox_item_id != first.inbox_item_id
    assert len(state._notification_log) == 2

    inbox = InboxStore()
    inbox.load()
    assert len([i for i in inbox.items.values() if i.item_kind == "report"]) == 2


@pytest.mark.asyncio
async def test_an_unchanged_home_does_not_mint_a_new_artifact_version(
    home, artifacts, tmp_path, monkeypatch
):
    """Nothing learned since last time ⇒ no version. The store does not dedupe a no-op
    update, so an unconditional snapshot would prune real history off the far end."""
    state, vs = _state(tmp_path)
    _facet(vs, "prefers terse replies")
    _patch_model(monkeypatch, "")

    first = await LR.deliver_identity_report(state, vs=vs, now=NOW)
    again = await LR.deliver_identity_report(state, vs=vs, now=NOW)

    assert again.artifact_version == first.artifact_version

    _lesson(vs, "always run make lint")
    changed = await LR.deliver_identity_report(state, vs=vs, now=NOW + timedelta(days=40))
    assert changed.artifact_version > first.artifact_version


# ── the HTTP surface, and that it is REACHABLE ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_get_endpoint_serves_the_report_and_spends_no_model_call(
    home, tmp_path, monkeypatch
):
    """A panel mounting must not cost a model call, so GET is deterministic-only."""
    from personalclaw.dashboard.handlers.learning import api_learning_identity_report

    state, vs = _state(tmp_path)
    _facet(vs, "prefers terse replies")
    _create("fresh-thing", created_at=_iso(2))
    calls = _patch_model(monkeypatch, "should never be reached")

    resp = await api_learning_identity_report(_req(state, query={"days": "30"}))
    body = json.loads(resp.body)

    assert resp.status == 200
    assert not calls, "GET spent a model call"
    assert body["facets"]["count"] == 1
    assert body["skills"]["count"] == 1
    assert body["narrative_status"] == LR.NARRATIVE_SKIPPED
    assert body["period"]["window_days"] == 30
    assert "How I've adapted to you" in body["markdown"]


@pytest.mark.asyncio
async def test_the_post_endpoint_delivers_and_returns_the_artifact_ref(
    home, artifacts, tmp_path, monkeypatch
):
    from personalclaw.dashboard.handlers.learning import api_learning_identity_report_deliver

    state, vs = _state(tmp_path)
    _facet(vs, "prefers terse replies")
    _patch_model(monkeypatch, "You favour brevity.")

    resp = await api_learning_identity_report_deliver(_req(state))
    body = json.loads(resp.body)

    assert resp.status == 200
    assert body["artifact_slug"] == LR.ARTIFACT_SLUG
    assert body["inbox_item_id"]
    assert body["report"]["narrative_status"] == LR.NARRATIVE_WRITTEN


@pytest.mark.asyncio
async def test_a_bad_days_parameter_is_a_400_not_a_500(home, tmp_path):
    from personalclaw.dashboard.handlers.learning import api_learning_identity_report

    state, _vs = _state(tmp_path)
    resp = await api_learning_identity_report(_req(state, query={"days": "soon"}))
    assert resp.status == 400


@pytest.mark.asyncio
async def test_both_endpoints_404_when_learning_is_disabled(home, tmp_path, monkeypatch):
    """Learning off ⇒ ABSENT, not a page of honest-looking zeros."""
    import personalclaw.dashboard.handlers.learning as mod

    state, _vs = _state(tmp_path)
    monkeypatch.setattr(mod, "_enabled", lambda: False)

    assert (await mod.api_learning_identity_report(_req(state))).status == 404
    assert (await mod.api_learning_identity_report_deliver(_req(state))).status == 404


@pytest.mark.asyncio
async def test_a_temporary_session_gets_the_report_without_memory_content(
    home, tmp_path, monkeypatch
):
    """A `blocks_reads` session sees skills and proposals but no facets or lessons.

    Matches `/api/lessons` and `/api/learning/summary`. The control run WITH reads allowed
    is the vacuity floor: without it, an empty store would produce the same assertion.
    """
    import personalclaw.dashboard.handlers._shared as shared
    from personalclaw.dashboard.handlers.learning import api_learning_identity_report

    state, vs = _state(tmp_path)
    _facet(vs, "prefers terse replies")
    _create("fresh-thing", created_at=_iso(2))

    allowed = json.loads((await api_learning_identity_report(_req(state))).body)
    assert allowed["facets"]["count"] == 1

    monkeypatch.setattr(shared, "_blocks_reads_session", lambda *_a, **_k: True)
    blocked = json.loads((await api_learning_identity_report(_req(state))).body)

    assert blocked["facets"]["count"] == 0
    assert blocked["lessons"]["count"] == 0
    assert blocked["skills"]["count"] == 1


def test_the_identity_report_routes_are_registered_not_merely_defined():
    """A defined handler is not a reachable one.

    Every other test here calls the handlers directly, so all of them keep passing if the
    ``app.router.add_*`` lines are deleted — the panel would 404 in a real gateway while
    this file stayed green. The vacuity floor is the sibling assertion: a path this module
    does NOT register must be absent from the same collected sets, so a matcher that
    accepts everything fails here.
    """
    from aiohttp import web as _web

    from personalclaw.dashboard.handlers import learning as _learning

    app = _web.Application()
    _learning.register_learning_routes(app)
    by_method: dict[str, set[str]] = {"GET": set(), "POST": set()}
    for route in app.router.routes():
        if route.resource is not None and route.method in by_method:
            by_method[route.method].add(route.resource.canonical)

    assert "/api/learning/identity-report" in by_method["GET"]
    assert "/api/learning/identity-report" in by_method["POST"]
    assert "/api/learning/definitely-not-a-route" not in by_method["GET"]
    assert "/api/learning/definitely-not-a-route" not in by_method["POST"]


def test_the_notification_pair_is_registered_so_it_keeps_its_own_severity():
    """An unregistered pair falls open to system/generic — the registry's own 🪤 case.

    Asserted on the OUTCOME (its own wire string and an info severity, which is what makes
    quiet hours apply) rather than on the registration line, plus the floor that an
    unregistered pair really does collapse.
    """
    import personalclaw.notification_kinds as nk
    from personalclaw.providers.entity_routes import _KIND_SEVERITY

    resolved = nk.resolve_kind(LR.NOTIFY_SOURCE, LR.NOTIFY_KIND)

    assert (resolved.source, resolved.kind) == (LR.NOTIFY_SOURCE, LR.NOTIFY_KIND)
    assert resolved.attention is True
    assert nk.kind_for_legacy_pair(LR.NOTIFY_SOURCE, LR.NOTIFY_KIND) == LR.NOTIFY_KIND
    # Below SEV_ERROR, which is what `notification_allowed` requires for quiet hours to bite.
    assert _KIND_SEVERITY.get(LR.NOTIFY_KIND, nk.SEV_INFO) < nk.SEV_ERROR
    # The floor: an unregistered sibling DOES collapse, so the assertion above is not
    # something every pair satisfies.
    assert nk.resolve_kind("learning", "not-a-registered-kind").kind == nk.GENERIC_KIND
