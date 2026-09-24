"""The self-model observer — the call site S72 left unbuilt (LEARN-R21 / §2.6 — WF2LEA-8).

S72 shipped the pure self-model decisions and recorded that it builds no store; S80 mapped the
`self_model` allocator slot and recorded it had NO live producer. This suite covers the four things
WF2LEA-8's `done_when` names, each against the REAL `MemoryService`/`VectorMemoryStore` and the REAL
staging + proposal stores (monkeypatched to a tmp home), not hand-built state:

* an observer records (route, tools, outcome, reaction) into the staging log after significant
  turns;
* live `user.selfmodel.*` entries are read/written via MemoryService within the caps;
* reinforced habits file `lesson_batch` PROPOSALS, never self-installed;
* the compact snapshot reaches the §2.4 allocator's `self_model` slot on the chat path.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from personalclaw.learning import self_model_observer as obs
from personalclaw.learning.self_model import CAPS, KEY_PREFIX, Facet
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def svc():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return MemoryService.over_vector_store(store)


@pytest.fixture
def staging(tmp_path):
    from personalclaw.learning.staging import StagingStore

    s = StagingStore(tmp_path)
    yield s
    s.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the proposal store + inbox side effects at a tmp home (like the proposals suite)."""
    from personalclaw.learning import proposals as P

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    return tmp_path


def _run_turn(
    svc,
    staging,
    *,
    session="dashboard:s1",
    route="PersonalClaw",
    tools=("edit_file",),
    succeeded=True,
    correction=False,
):
    return obs.observe_turn(
        svc,
        session_key=session,
        route=route,
        tools=tools,
        succeeded=succeeded,
        correction=correction,
        staging_store=staging,
    )


# ── the pattern signature ──


def test_the_pattern_is_route_plus_the_sorted_tool_set():
    """Habits accrete across turns only if the same work yields the same key — content would never
    recur to cross a threshold."""
    assert (
        obs.observed_pattern("direct", ("read_file", "edit_file")) == "direct + edit_file,read_file"
    )
    assert obs.observed_pattern("", ()) == "direct + no-tools"


# ── clause 1: the observer stages the full tuple after significant turns ──


def test_the_first_turn_parks_and_stages_nothing(svc, staging):
    """A turn's reaction is not observable until the user's NEXT move — first turn only parks."""
    report = _run_turn(svc, staging)
    assert report["resolved"] is False
    assert report["staged"] is False
    assert staging.pending() == []
    # …but the pending row exists for the next turn to resolve.
    assert svc.get_semantic(obs._pending_key("dashboard:s1")) is not None


def test_the_next_turn_resolves_and_stages_the_full_tuple(svc, staging):
    """The parked (route, tools, outcome) plus THIS turn's reaction is one staged observation."""
    _run_turn(svc, staging)  # park
    report = _run_turn(svc, staging)  # resolve the parked turn
    assert report["resolved"] is True
    assert report["staged"] is True
    (entry,) = staging.pending()
    assert entry.kind == "self_model"
    # The staged row carries the full tuple in its meta — route, tools, outcome, reaction.
    assert entry.meta["route"] == "PersonalClaw"
    assert entry.meta["tools"] == ["edit_file"]
    assert entry.meta["reaction"] == "accepted"
    assert entry.meta["succeeded"] is True


def test_a_correction_resolves_the_previous_turn_as_corrected(svc, staging):
    """The reaction is measured, not guessed: a correction is the strongest negative signal."""
    _run_turn(svc, staging)  # park an accepted-looking turn
    _run_turn(svc, staging, correction=True)  # this turn CORRECTS the parked one
    (entry,) = staging.pending()
    assert entry.meta["reaction"] == "corrected"


def test_the_observer_is_a_noop_without_a_store(staging):
    """A null-memory service records nothing and does not raise."""
    report = obs.observe_turn(
        MemoryService.over_vector_store(None),
        session_key="s",
        route="r",
        tools=(),
        succeeded=True,
        correction=False,
        staging_store=staging,
    )
    assert report == {"resolved": False, "staged": False, "proposed": False, "pattern": ""}


# ── clause 2: live user.selfmodel.* entries read/written via MemoryService within caps ──


def test_a_resolved_turn_writes_a_candidate_and_a_retrospection(svc, staging):
    _run_turn(svc, staging)
    _run_turn(svc, staging)
    # The candidate accumulator exists and counts the resolved observation.
    cand = svc.get_semantic(obs._candidate_key("PersonalClaw + edit_file"))
    assert cand is not None
    # The retrospection ring has an entry (evidence, not injected).
    live = obs.load_live_entries(svc)
    assert any(e.facet == Facet.RETROSPECTION.value for e in live)


def test_the_retrospection_ring_is_capped_on_disk(svc, staging):
    """`trim_ring` decides survivors; the observer DELETES the overflow rows so the ring cannot
    outgrow its cap on disk (a growing store would blow the injection budget)."""
    # Distinct patterns so each resolve appends a fresh retrospection row.
    for i in range(CAPS[Facet.RETROSPECTION.value] + 5):
        obs.observe_turn(
            svc,
            session_key=f"s{i}",
            route=f"route-{i}",
            tools=(f"tool_{i}",),
            succeeded=True,
            correction=False,
            staging_store=staging,
        )
        obs.observe_turn(
            svc,
            session_key=f"s{i}",
            route=f"route-{i}",
            tools=(f"tool_{i}",),
            succeeded=True,
            correction=False,
            staging_store=staging,
        )
    ring = [e for e in obs.load_live_entries(svc) if e.facet == Facet.RETROSPECTION.value]
    assert len(ring) <= CAPS[Facet.RETROSPECTION.value]


def test_the_observer_never_writes_a_principle_row(svc, staging, home):
    """§2.6: propose, NEVER install. No amount of reinforcement makes the observer write a live
    `principle` — that row appears only when the human accepts."""
    for i in range(6):
        obs.observe_turn(
            svc,
            session_key="s1",
            route="PersonalClaw",
            tools=("edit_file",),
            succeeded=True,
            correction=False,
            staging_store=staging,
        )
    principles = [e for e in obs.load_live_entries(svc) if e.facet == Facet.PRINCIPLE.value]
    assert principles == []


# ── clause 3: reinforced habits file lesson_batch PROPOSALS (never self-installed) ──


def test_a_reinforced_habit_files_a_lesson_batch_proposal(svc, staging, home):
    """Two accepted-after-success observations of one pattern cross §2.6's conjunction and file a
    proposal into the shared human-gated queue."""
    from personalclaw.learning import proposals

    # turn 1 parks; turn 2 resolves turn1 (accepted) + parks; turn 3 resolves turn2 (accepted).
    # Two ACCEPTED observations of the SAME pattern → seen 2, confidence 1.0 → promotable.
    reports = [_run_turn(svc, staging) for _ in range(4)]
    assert any(r["proposed"] for r in reports)
    pending = proposals.list_pending(kind=proposals.Kind.LESSON_BATCH.value)
    assert pending, "a reinforced habit produced no proposal"
    prop = pending[0]
    assert prop.source_cadence == "self_model"
    assert prop.target == f"{KEY_PREFIX}.principle"
    assert "self_model" in prop.tags


def test_the_proposal_fingerprint_matches_the_shared_hash(svc, staging, home):
    """A self-model principle the user declined must collide with its own prior decision, not
    re-file under a second hash — so the filed proposal's fingerprint uses `content_fingerprint`."""
    from personalclaw.learning import proposals
    from personalclaw.learning.proposals import content_fingerprint

    for _ in range(4):
        _run_turn(svc, staging)
    prop = proposals.list_pending(kind=proposals.Kind.LESSON_BATCH.value)[0]
    assert prop.fingerprint == content_fingerprint(
        proposals.Kind.LESSON_BATCH.value, f"{KEY_PREFIX}.principle", prop.body
    )


def test_a_declined_principle_is_not_refiled(svc, staging, home):
    """Decision memory is the anti-nag machinery: once rejected, the same habit stays out of the
    queue rather than re-proposing every time it recurs."""
    from personalclaw.learning import proposals

    for _ in range(4):
        _run_turn(svc, staging)
    prop = proposals.list_pending(kind=proposals.Kind.LESSON_BATCH.value)[0]
    assert proposals.reject(prop.id, actor="user") is True
    # Keep observing the same habit — it must not come back.
    for _ in range(4):
        _run_turn(svc, staging)
    assert proposals.list_pending(kind=proposals.Kind.LESSON_BATCH.value) == []


# ── clause 4: the accept-installer writes the live principle the snapshot reads ──


def test_accepting_a_self_model_proposal_writes_the_live_principle(svc, staging, home):
    from personalclaw.learning import proposals

    for _ in range(4):
        _run_turn(svc, staging)
    prop = proposals.list_pending(kind=proposals.Kind.LESSON_BATCH.value)[0]

    def _install(p):
        obs.install_accepted_principle(svc, p.to_dict())

    proposals.accept(prop.id, actor="user", installer=_install)
    principles = [e for e in obs.load_live_entries(svc) if e.facet == Facet.PRINCIPLE.value]
    assert len(principles) == 1
    assert principles[0].body == prop.body


def test_the_installer_discriminates_self_model_proposals():
    assert obs.is_self_model_proposal({"source_cadence": "self_model"}) is True
    assert obs.is_self_model_proposal({"source_cadence": "consolidation"}) is False
    assert obs.is_self_model_proposal({}) is False


def test_the_installer_enforces_the_principle_cap_on_accept(svc):
    """Admitting a principle into a FULL tier displaces the weakest — the cap holds on disk the same
    way `plan_promotion` held it on paper, even against a hand-edited store."""
    # Seed the tier to its cap with weak-to-strong principles.
    for i in range(CAPS[Facet.PRINCIPLE.value]):
        obs.install_accepted_principle(
            svc,
            {
                "title": f"seed-{i}",
                "body": f"seed principle {i}",
                "confidence": 0.1 * (i + 1),
                "reinforcements": 3,
                "source_cadence": "self_model",
            },
        )
    assert (
        len([e for e in obs.load_live_entries(svc) if e.facet == Facet.PRINCIPLE.value])
        == CAPS[Facet.PRINCIPLE.value]
    )
    # A stronger newcomer displaces the weakest (seed-0 at confidence 0.1).
    obs.install_accepted_principle(
        svc,
        {
            "title": "newcomer",
            "body": "the new principle",
            "confidence": 0.99,
            "reinforcements": 4,
            "source_cadence": "self_model",
        },
    )
    live = [e for e in obs.load_live_entries(svc) if e.facet == Facet.PRINCIPLE.value]
    assert len(live) == CAPS[Facet.PRINCIPLE.value]
    bodies = {e.body for e in live}
    assert "the new principle" in bodies
    assert "seed principle 0" not in bodies


def test_an_empty_body_is_not_installed(svc):
    assert (
        obs.install_accepted_principle(svc, {"body": "  ", "source_cadence": "self_model"}) is False
    )
    assert obs.load_live_entries(svc) == []


# ── the snapshot producer reaches the allocator slot on the chat path ──


def test_the_producer_reads_live_entries_into_the_snapshot(svc, home):
    """The `context._self_model_snapshot` producer renders the observer/installer's live entries
    through S72's `snapshot`, so the block that reaches the allocator is real, not empty."""
    from personalclaw.context import _self_model_snapshot

    obs.install_accepted_principle(
        svc,
        {
            "title": "edit over rewrite",
            "body": "Prefer editing to rewriting.",
            "confidence": 0.9,
            "reinforcements": 3,
            "source_cadence": "self_model",
        },
    )
    block = _self_model_snapshot(svc)
    assert "Prefer editing to rewriting." in block


def test_an_empty_self_model_produces_an_empty_snapshot(svc):
    from personalclaw.context import _self_model_snapshot

    assert _self_model_snapshot(svc) == ""


def test_the_snapshot_reaches_the_allocator_self_model_slot(svc, home):
    """End-to-end: a live principle → producer → `_render_ambient(self_model=…)` → the rendered
    ambient text. This is the slot S80 built and left producerless, now fed on the chat path."""
    from personalclaw.context import _render_ambient, _self_model_snapshot

    obs.install_accepted_principle(
        svc,
        {
            "title": "targeted suite first",
            "body": "Run the targeted suite before the full run.",
            "confidence": 0.9,
            "reinforcements": 3,
            "source_cadence": "self_model",
        },
    )
    block = _self_model_snapshot(svc)
    rendered = _render_ambient(self_model=block)
    assert "Run the targeted suite before the full run." in rendered
