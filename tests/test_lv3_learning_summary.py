"""LV-3 (LEARNING-VISIBILITY T2.3): the learning summary block.

The atom's ``done_when`` is "the fallback skills-page header shows the learning block with
real counts and names". "Real" is the whole load: a block that renders zeros because it
reads a key nothing writes is the defect this file exists to prevent. So every group is
seeded through its **production writer** — ``create_auto_skill`` / ``update_auto_skill``
for skill provenance, ``overlays.apply_overlay`` for a sidecar refinement,
``proposals.enqueue`` for the pending queue, ``upsert_facet`` and ``write_lesson`` for the
facts — and then read back through ``compose_learning_summary``.

Every store is redirected under ``tmp_path``; the redirect is ASSERTED rather than
assumed, because ``skills_dir()`` resolves ``config_dir`` through the loader module and a
patch that missed it would write into the real ``~/.personalclaw``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from personalclaw.learning_summary import (
    _MAX_NAMES,
    LearningSummary,
    compose_learning_summary,
)
from personalclaw.skills import loader as loader_mod
from personalclaw.skills import overlays, proposals
from personalclaw.skills.loader import AutoSkillProvenance, SkillsLoader

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _iso_real(days_ago: float) -> str:
    """Relative to the REAL clock, for the HTTP tests.

    The endpoint takes no `now` — it is the production path and reads the wall clock. A
    fixture stamped relative to the frozen `NOW` above therefore drifts out of the window
    as the calendar advances, which is a test that passes today and fails silently in a
    week. Measured: it already did, five days after `NOW`.
    """
    return (datetime.now(tz=timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every learning store under ``tmp_path``, with the redirect PROVEN.

    ``skills_dir()`` calls the loader module's ``config_dir`` at call time, and
    ``proposals``/``overlays`` both resolve through it, so one patch covers all three —
    but only if it actually took. The assertion is the point: a silent miss here would
    make every test below write to the real home and still pass.
    """
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    import personalclaw.skills.marketplace as mp

    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])
    assert loader_mod.skills_dir() == tmp_path / "skills"
    assert str(overlays.overlays_dir()).startswith(str(tmp_path))
    return tmp_path


def _loader() -> SkillsLoader:
    return SkillsLoader(install_builtins=False)


def _create(slug: str, *, created_at: str) -> str:
    """A new auto skill through the production writer."""
    name = _loader().create_auto_skill(
        slug,
        description=f"what {slug} does",
        triggers=slug,
        procedure_md=f"1. do {slug}",
        provenance=AutoSkillProvenance(session_key="sess:1", created_at=created_at),
    )
    assert name, f"create_auto_skill refused {slug!r}"
    return name


def _refine(name: str, *, refined_at: str) -> None:
    """A frontmatter refinement through the production writer (the ``history.py`` seam)."""
    ok = _loader().update_auto_skill(
        name,
        description=f"{name} refined",
        triggers=name,
        procedure_md="1. do it better",
        provenance=AutoSkillProvenance(
            session_key="sess:2", created_at=refined_at, refined_at=refined_at
        ),
    )
    assert ok, f"update_auto_skill refused {name!r}"


def _enqueue(slug: str, *, kind: str = "new", refine_target: str = "") -> None:
    prop = proposals.enqueue(
        slug=slug,
        description=f"proposed {slug}",
        triggers=slug,
        procedure_md=f"1. {slug}",
        session_key="sess:3",
        created_at=_iso(1),
        kind=kind,
        refine_target=refine_target,
        source_excerpt="",
    )
    assert prop is not None, f"enqueue refused {slug!r}"


def _memory(tmp_path):
    """A real vector store — the backing for both facets and lessons."""
    from personalclaw.vector_memory import VectorMemoryStore

    vs = VectorMemoryStore(db_path=tmp_path / "memory.db", embedding_dim=3)
    vs.init()
    vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return vs


# ── counts + names: the two must not be derived from each other ────────────────────


def test_counts_are_exact_while_names_are_a_bounded_sample(home):
    """`count` is the true group size; `names` is capped. Neither derives from the other.

    The expected count is the loop bound — an independent number — not a second call to
    the thing under test. Deriving it from ``list_pending()`` would let a truncation bug
    agree with itself.
    """
    seeded = 12
    assert seeded > _MAX_NAMES, "the fixture must exceed the cap or this proves nothing"
    for i in range(seeded):
        _enqueue(f"proposal-{i:02d}")

    summary = compose_learning_summary(now=NOW)

    assert summary.pending_proposals.count == seeded
    assert len(summary.pending_proposals.names) == _MAX_NAMES
    assert summary.total == seeded


def test_pending_is_unwindowed_and_a_refine_names_its_target(home):
    """A refine proposal labels the skill it would change, not its own slug.

    "pending" carries no window on purpose — an old proposal is more interesting, not
    less — so a five-week-old one must still show up under the default 7-day window.
    """
    _create("existing-flow", created_at=_iso(200))
    proposals.enqueue(
        slug="unused-slug",
        description="tighten it",
        triggers="x",
        procedure_md="1. x",
        session_key="sess:9",
        created_at=_iso(35),
        kind="refine",
        refine_target="auto/existing-flow",
        source_excerpt="",
    )

    summary = compose_learning_summary(now=NOW)

    assert summary.pending_proposals.count == 1
    assert summary.pending_proposals.names == ["auto/existing-flow (refine)"]


# ── new vs refined: distinguished by provenance, never double-counted ──────────────


def test_new_and_refined_are_split_by_provenance(home):
    """One skill created in the window, one refined in it — each in exactly one group."""
    _create("fresh-thing", created_at=_iso(2))
    _create("old-thing", created_at=_iso(120))
    _refine("auto/old-thing", refined_at=_iso(3))

    summary = compose_learning_summary(now=NOW)

    assert summary.new_skills.names == ["auto/fresh-thing"]
    assert summary.refined_skills.names == ["auto/old-thing"]
    assert summary.new_skills.count == 1 and summary.refined_skills.count == 1


def test_a_skill_created_and_refined_in_one_window_counts_once(home):
    """The refine seam stamps `refined_at` beside `created_at` (``history.py:1863``).

    Without the new-wins-and-stops rule that skill would be reported as one new thing
    AND one refinement — two events for one artifact, on a block whose whole job is to
    be countable.
    """
    _create("same-week", created_at=_iso(2))
    _refine("auto/same-week", refined_at=_iso(1))

    summary = compose_learning_summary(now=NOW)

    assert summary.new_skills.names == ["auto/same-week"]
    assert summary.refined_skills.count == 0
    assert summary.total == 1


def test_an_overlay_refinement_is_seen_though_skill_md_is_untouched(home):
    """Accepted `kind="refine"` proposals overlay a sidecar and never rewrite SKILL.md.

    So frontmatter alone cannot see them: `refined_at` stays empty forever on that path.
    A block that read only frontmatter would report zero refinements on a home where
    every refinement had been accepted — the exact inert-control shape.
    """
    name = _create("overlaid", created_at=_iso(90))
    before = (home / "skills" / name / "SKILL.md").read_bytes()
    overlays.apply_overlay(
        name, description="sharper", procedure_md="1. sharper", created_at=_iso(2)
    )

    summary = compose_learning_summary(now=NOW)

    assert summary.refined_skills.names == [name]
    # The base bytes are the falsification target of the overlay design itself.
    assert (home / "skills" / name / "SKILL.md").read_bytes() == before


# ── facts: facets + lessons, each through its live writer ──────────────────────────


def test_facts_read_facets_and_lessons_from_their_live_writers(home, tmp_path):
    """A facet written by `upsert_facet` and a lesson written by `write_lesson` both land.

    Those are the two production writers: `after_turn_review.py:181` calls the first and
    `/api/lessons` + the same review call the second. The facet carries its live state
    from the SAME derivation the ambient PROFILE block uses.
    """
    from personalclaw.memory_service import MemoryService
    from personalclaw.preference_facets import upsert_facet

    vs = _memory(tmp_path)
    upsert_facet(vs, "style", "prefers terse replies", cue="explicit", now=NOW - timedelta(days=1))
    assert (
        MemoryService.over_vector_store(vs).write_lesson("always run make lint", category="process")
        is True
    )

    summary = compose_learning_summary(now=NOW, vs=vs)

    joined = " | ".join(summary.facts.names)
    assert summary.facts.count == 2, joined
    assert "prefers terse replies (Active)" in joined
    assert "always run make lint" in joined


def test_no_vector_store_yields_no_facts_and_still_reports_skills(home):
    """The honest degrade for an API-only home: facts empty, skill groups intact.

    Not an error and not a fabricated zero — there is genuinely nothing to read, and the
    skill/proposal halves do not live in memory so they must survive.
    """
    _create("standalone", created_at=_iso(1))

    summary = compose_learning_summary(now=NOW, vs=None)

    assert summary.facts.count == 0
    assert summary.new_skills.count == 1


# ── the window is a real filter (the vacuity half) ─────────────────────────────────


def test_everything_outside_the_window_is_excluded(home, tmp_path):
    """The same seed, read from a `now` a year later, reports only the unwindowed group.

    This is the vacuity assertion for every windowed group at once: if the cutoff were
    ignored, the counts here would match the in-window test's. `pending` stays at 1
    because it is deliberately unwindowed.
    """
    from personalclaw.memory_service import MemoryService
    from personalclaw.preference_facets import upsert_facet

    vs = _memory(tmp_path)
    _create("fresh-thing", created_at=_iso(2))
    _create("old-thing", created_at=_iso(120))
    _refine("auto/old-thing", refined_at=_iso(3))
    _enqueue("still-pending")
    upsert_facet(vs, "style", "prefers terse replies", cue="explicit", now=NOW - timedelta(days=1))
    MemoryService.over_vector_store(vs).write_lesson("always run make lint", category="process")

    inside = compose_learning_summary(now=NOW, vs=vs)
    assert (inside.new_skills.count, inside.refined_skills.count, inside.facts.count) == (1, 1, 2)

    outside = compose_learning_summary(now=NOW + timedelta(days=365), vs=vs)
    assert (outside.new_skills.count, outside.refined_skills.count, outside.facts.count) == (
        0,
        0,
        0,
    )
    assert outside.pending_proposals.count == 1
    assert outside.total == 1


def test_the_window_is_clamped_to_the_declared_bounds(home):
    """`days=0` and `days=9999` clamp rather than raising or reading everything."""
    assert compose_learning_summary(window_days=0, now=NOW).window_days == 1
    assert compose_learning_summary(window_days=9999, now=NOW).window_days == 90


# ── propose-don't-write ────────────────────────────────────────────────────────────


def _snapshot(root) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_composing_the_block_writes_nothing(home, tmp_path):
    """Opening the panel must not change what it reports.

    Byte-for-byte over every file in the home, not just the ones the composer names — a
    per-store check would miss a store that grew a cache file on read.
    """
    from personalclaw.memory_service import MemoryService
    from personalclaw.preference_facets import upsert_facet

    vs = _memory(tmp_path)
    _create("fresh-thing", created_at=_iso(2))
    _enqueue("still-pending")
    upsert_facet(vs, "style", "prefers terse replies", cue="explicit", now=NOW - timedelta(days=1))
    MemoryService.over_vector_store(vs).write_lesson("always run make lint", category="process")

    before = _snapshot(home / "skills")
    assert before, "the snapshot must see files or this proves nothing"

    summary = compose_learning_summary(now=NOW, vs=vs)
    assert summary.total > 0

    assert _snapshot(home / "skills") == before


# ── the HTTP surface the block actually reads ──────────────────────────────────────


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


@pytest.mark.asyncio
async def test_endpoint_serves_the_block_with_real_counts(home, tmp_path):
    """GET /api/learning/summary — the payload the skills-page block renders.

    Asserted on the SHAPE THE FRONTEND READS (`new_skills.count`, `names`), not on an
    internal dataclass: the block's contract is the JSON, and a rename inside
    `LearningSummary` that left the payload alone must not red here.
    """
    from personalclaw.dashboard.handlers.learning import api_learning_summary
    from personalclaw.preference_facets import upsert_facet

    state, vs = _state(tmp_path)
    _create("fresh-thing", created_at=_iso_real(2))
    _enqueue("still-pending")
    upsert_facet(vs, "style", "prefers terse replies", cue="explicit")

    resp = await api_learning_summary(_req(state, query={"days": "7"}))
    body = json.loads(resp.body)

    assert resp.status == 200
    assert body["window_days"] == 7
    assert body["new_skills"] == {"count": 1, "names": ["auto/fresh-thing"]}
    assert body["pending_proposals"]["count"] == 1
    assert body["facts"]["count"] == 1
    assert body["total"] == 3


@pytest.mark.asyncio
async def test_endpoint_404s_when_learning_is_disabled(home, tmp_path, monkeypatch):
    """Learning off ⇒ 404, so the block is ABSENT rather than four honest-looking zeros.

    Zeros there would assert "nothing was learned"; the truth is "nothing is tracked".
    """
    import personalclaw.dashboard.handlers.learning as mod

    state, _vs = _state(tmp_path)
    _create("fresh-thing", created_at=_iso_real(2))
    monkeypatch.setattr(mod, "_enabled", lambda: False)

    resp = await mod.api_learning_summary(_req(state))
    assert resp.status == 404


@pytest.mark.asyncio
async def test_a_temporary_session_gets_the_block_without_the_facts(home, tmp_path, monkeypatch):
    """A `blocks_reads` session sees skills/proposals but no memory content.

    Matches `/api/lessons`, which returns an empty list for the same caller. The skill and
    proposal groups are not memory, so hiding them too would be a different lie.
    """
    import personalclaw.dashboard.handlers._shared as shared
    from personalclaw.dashboard.handlers.learning import api_learning_summary
    from personalclaw.preference_facets import upsert_facet

    state, vs = _state(tmp_path)
    _create("fresh-thing", created_at=_iso_real(2))
    upsert_facet(vs, "style", "prefers terse replies", cue="explicit")

    # Control: the same request WITH reads allowed sees the facet — so the assertion
    # below measures the block, not an empty store.
    allowed = json.loads((await api_learning_summary(_req(state))).body)
    assert allowed["facts"]["count"] == 1

    monkeypatch.setattr(shared, "_blocks_reads_session", lambda *_a, **_k: True)
    blocked = json.loads((await api_learning_summary(_req(state))).body)

    assert blocked["facts"]["count"] == 0
    assert blocked["new_skills"]["count"] == 1


def test_default_summary_is_empty_so_an_empty_home_renders_nothing(home):
    """A fresh home totals zero — which is what makes the block hide itself."""
    assert LearningSummary().total == 0
    assert compose_learning_summary(now=NOW).total == 0
