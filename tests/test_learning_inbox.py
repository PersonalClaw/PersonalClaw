"""S75 — the Proposal Inbox and the accept gate (§6.1 / §7).

§7's criterion 1 has two halves: one inbox across all six kinds with provenance, evidence manifests
and risk-tier metadata — **and the model cannot accept its own proposals under any trust mode**. The
second is load-bearing, and `test_an_agent_can_never_accept` is its regression.

**Measured before writing.** `proposals.accept()` takes `(pid, installer=...)` and NOTHING knows
WHO is accepting — no actor, no caller, no trust check. The invariant held only because no agent
tool happened to call it: an ABSENCE, not a control. One new MCP tool would have removed it silently
with no test failing.
"""

from __future__ import annotations

import inspect

import pytest

from personalclaw.learning.inbox import (
    ACCEPT_ACTORS,
    BULK_ACCEPTABLE_TIERS,
    FILE_ACTORS,
    REJECT_ACTORS,
    Denial,
    audit_denial,
    build_view,
    can_file,
    filter_rows,
    order_rows,
    require_human,
    row_from_proposal,
)


class _Prop:
    """A stand-in for a stored `Proposal`, built by keyword so a test can omit fields."""

    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


def _prop(pid="p1", **kw):
    base = dict(
        id=pid,
        kind="template_diff",
        title="add a retry to fetch",
        provenance="refiner",
        source_cadence="run_end",
        source_excerpt="step_failed x4",
        evidence_refs=["r1", "r2"],
        reinforcements=4,
        confidence=0.8,
        manifest_valid=True,
        manifest_issues=[],
        status="pending",
    )
    base.update(kw)
    return _Prop(**base)


# ── §7 criterion 1: the accept gate ──


def test_an_agent_can_never_accept():
    """THE regression, and the reason this module exists.

    `proposals.accept()` knows nothing about who calls it, so the invariant held by absence
    of a caller. An agent whose self-report installs itself is not reviewed at all.
    """
    gate = require_human(action="accept", actor="agent")
    assert not gate.allowed
    assert gate.denial == Denial.SELF_ACCEPT.value
    assert "propose but never accept" in gate.reason


def test_an_agent_can_never_reject_either():
    """Subtler: an agent that could reject would clear its own bad proposals from the queue before
    human read them, and §2.2's rejection exemplars would silently stop accumulating."""
    gate = require_human(action="reject", actor="agent")
    assert not gate.allowed and gate.denial == Denial.SELF_ACCEPT.value


def test_the_engine_cannot_accept_either():
    """Not an oversight. S56 gives the engine authority to record a task outcome because it OBSERVED
    the work — that is not authority to install autonomously-authored behaviour. An engine that
    could accept would make every gate upstream of it decorative.
    """
    gate = require_human(action="accept", actor="engine")
    assert not gate.allowed
    assert gate.denial == Denial.NOT_A_REVIEWER.value


def test_a_human_reviewer_may_accept_and_reject():
    for action in ("accept", "reject"):
        assert require_human(action=action, actor="user").allowed


def test_an_unknown_actor_is_denied_rather_than_assumed_human():
    """The failure directions are not symmetric: denying a human costs one click, while admitting an
    unrecognized caller is the hole this closes."""
    for actor in ("", "somebody-else", "AGENT", "root"):
        gate = require_human(action="accept", actor=actor)
        assert not gate.allowed
        assert gate.denial == Denial.UNKNOWN_ACTOR.value


def test_the_gate_takes_no_trust_parameter():
    """§7: "under ANY trust mode".

    A gate that a mode could relax is a gate whose invariant is a default. There is deliberately no
    parameter to pass.
    """
    params = set(inspect.signature(require_human).parameters)
    assert params == {"action", "actor", "status"}
    assert not any(p in params for p in ("trust", "trust_mode", "yolo", "force", "override"))


def test_an_already_resolved_proposal_cannot_be_re_decided():
    """Re-deciding would overwrite a recorded decision — and §2.2 learns from those."""
    for status in ("accepted", "rejected", "superseded"):
        gate = require_human(action="accept", actor="user", status=status)
        assert not gate.allowed
        assert gate.denial == Denial.ALREADY_RESOLVED.value


def test_a_draft_is_still_decidable():
    assert require_human(action="accept", actor="user", status="draft").allowed


def test_the_actor_vocabulary_is_reused_not_redefined():
    """S56's matrix already carries the doctrine — the AGENT is a worker whose self-report is
    what needs checking". Two actor enums would eventually disagree about who an `agent` is.
    """
    from personalclaw.workflows.verified_done import Actor

    for actor in (a.value for a in Actor):
        gate = require_human(action="accept", actor=actor)
        assert gate.denial != Denial.UNKNOWN_ACTOR.value, f"{actor} should be a known actor"


def test_only_the_user_may_decide():
    assert ACCEPT_ACTORS == {"user"}
    assert REJECT_ACTORS == {"user"}


def test_filing_is_the_safe_verb_and_all_actors_may_do_it():
    """The whole design depends on non-human proposers; only the DECISION is human-only."""
    for actor in ("user", "agent", "engine"):
        assert can_file(actor)
    assert FILE_ACTORS == {"user", "agent", "engine"}
    assert not can_file("ghost")


def test_a_refused_action_produces_an_audit_row():
    """A blocked self-accept is the signal that something calls the wrong path — invisible if only
    successes are logged."""
    gate = require_human(action="accept", actor="agent")
    row = audit_denial(action="accept", actor="agent", pid="p1", gate=gate)
    assert row["outcome"] == "blocked"
    assert row["denial"] == Denial.SELF_ACCEPT.value
    assert row["actor"] == "agent" and row["proposal"] == "p1"
    assert row["reason"]


# ── §6.1: every kind, one queue ──


def test_the_inbox_covers_every_proposal_kind():
    """One surface for every kind. A second kind list is how a surface silently stops showing one.

    Derived from `Kind` itself, not a hardcoded count: the three project_* kinds (LEA-12) join the
    original six, and a surface that enumerated a stale number would drop the newest kind — the
    exact failure this test exists to catch.
    """
    from personalclaw.learning.proposals import Kind

    kinds = [k.value for k in Kind]
    view = build_view([_prop(f"p{i}", kind=k) for i, k in enumerate(kinds)])
    assert set(view.by_kind) == set(kinds)
    assert view.total == len(kinds)


def test_a_row_carries_everything_needed_to_decide():
    """§6.1 names these, and each absence produces a specific bad review."""
    row = row_from_proposal(_prop())
    payload = row.to_dict()
    for field_name in (
        "provenance",
        "source_excerpt",
        "evidence_refs",
        "reinforcements",
        "manifest_valid",
        "risk_tier",
    ):
        assert field_name in payload


def test_a_row_without_provenance_cannot_be_rendered_honestly():
    """A proposal whose source cannot be shown is one a reviewer cannot weigh — and a queue of
    unweighable rows trains people to bulk-accept, defeating the invariant while appearing to honour
    it."""
    assert not row_from_proposal(_prop(provenance="")).renderable
    assert not row_from_proposal(_prop(title="")).renderable
    assert row_from_proposal(_prop()).renderable


def test_unrenderable_rows_are_REPORTED_not_hidden():
    """A proposal missing its provenance is a PROPOSER bug; quietly hiding it makes that bug
    invisible."""
    view = build_view([_prop("good"), _prop("bad", provenance="")])
    assert view.unrenderable == ["bad"]
    assert view.total == 2


def test_the_projection_survives_an_older_record():
    """`Proposal` gains fields as the flywheel grows; a projection raising on an older record would
    empty the inbox exactly when someone needs to review a backlog."""
    row = row_from_proposal(_Prop(id="old", kind="skill", title="t", provenance="p"))
    assert row.reinforcements == 0 and row.evidence_refs == [] and row.manifest_valid is True
    # UNGRADED, not "correlated": defaulting to a tier would upgrade a record from before the
    # tier existed into a claim nobody made.
    assert row.evidence_strength == ""


def test_a_row_carries_the_evidence_TIER_not_only_the_count():
    """The count cannot distinguish a MEASUREMENT from a co-occurrence.

    EVALUATION-SUBSTRATE §3.1 files a retirement whose evidence is a paired on/off ablation and
    stamps `evidence_strength="ablation"` to say so. Before this projection carried the field, the
    tier was write-only across the whole queue — NINE `enqueue` call sites in eight modules stamped
    one and nothing anywhere read it — so the reviewer deciding a retirement saw `2 evidence
    ref(s)` whether the claim was measured or merely correlated.
    """
    measured = row_from_proposal(_prop("a", kind="retirement", evidence_strength="ablation"))
    correlated = row_from_proposal(_prop("b", evidence_strength="correlated"))

    assert measured.to_dict()["evidence_strength"] == "ablation"
    # VACUITY FLOOR: a hardcoded constant would satisfy the line above. These two rows carry the
    # same evidence COUNT, so only the tier can tell them apart.
    assert measured.evidence_refs == correlated.evidence_refs
    assert measured.to_dict()["evidence_strength"] != correlated.to_dict()["evidence_strength"]


# ── risk tiers are metadata, never a lane ──


def test_a_manual_only_row_is_never_bulk_acceptable():
    """§3.1 stamps `manual_only` on destructive edits, and bulk-plus-destructive is the combination
    that turns an ergonomic affordance into an accident."""
    assert "manual_only" not in BULK_ACCEPTABLE_TIERS
    row = row_from_proposal(_prop(), risk_tier="manual_only")
    assert not row.bulk_acceptable


def test_an_invalid_manifest_is_never_bulk_acceptable():
    row = row_from_proposal(_prop(manifest_valid=False), risk_tier="low")
    assert not row.bulk_acceptable


def test_a_row_with_no_evidence_is_never_bulk_acceptable():
    row = row_from_proposal(_prop(evidence_refs=[]), risk_tier="low")
    assert not row.bulk_acceptable


def test_an_UNRENDERABLE_row_is_never_bulk_acceptable():
    """A defect found while probing: a row with no provenance came back `bulk_acceptable=True` while
    `renderable=False`, so a row the UI cannot honestly show was eligible for a control that accepts
    without opening it. Bulk-accepting something a reviewer could not have read is the
    human-installs invariant in name only.
    """
    row = row_from_proposal(_prop(provenance="", evidence_refs=["r1"]), risk_tier="low")
    assert row.renderable is False
    assert row.bulk_acceptable is False


def test_a_clean_low_risk_row_IS_bulk_acceptable():
    """A gate that excluded everything would be a bug, not a control."""
    assert row_from_proposal(_prop(), risk_tier="low").bulk_acceptable


def test_bulk_eligibility_is_a_ui_bound_not_the_gate():
    """Every accept in a bulk action still passes `require_human` individually."""
    assert require_human(action="accept", actor="agent").allowed is False


def test_there_is_no_auto_tier_in_the_refiners_vocabulary():
    """§3.1: any "auto" tier is guardrail-violating."""
    from personalclaw.learning.refiner import RiskTier

    assert "auto" not in {t.value for t in RiskTier}


# ── ordering and filtering ──


def test_manual_only_sorts_FIRST():
    """It is the tier that most needs attention. Burying destructive proposals under a page of
    parameter tweaks is how one gets accepted by momentum."""
    rows = [
        row_from_proposal(_prop("low"), risk_tier="low"),
        row_from_proposal(_prop("danger"), risk_tier="manual_only"),
        row_from_proposal(_prop("mid"), risk_tier="review"),
    ]
    assert [r.id for r in order_rows(rows)] == ["danger", "mid", "low"]


def test_stronger_evidence_sorts_first_within_a_tier():
    rows = [
        row_from_proposal(_prop("one", reinforcements=1), risk_tier="low"),
        row_from_proposal(_prop("twenty", reinforcements=20), risk_tier="low"),
    ]
    assert [r.id for r in order_rows(rows)] == ["twenty", "one"]


def test_an_unknown_tier_sorts_to_the_top_because_it_needs_looking_at():
    rows = [
        row_from_proposal(_prop("known"), risk_tier="low"),
        row_from_proposal(_prop("weird"), risk_tier="who-knows"),
    ]
    assert order_rows(rows)[0].id == "weird"


def test_the_order_is_stable():
    """A queue that reshuffles between renders makes a reviewer lose their place, and re-reading
    they already dismissed is how a decision gets reversed by accident."""
    rows = [row_from_proposal(_prop(f"p{i}"), risk_tier="low") for i in range(5)]
    first = [r.id for r in order_rows(rows)]
    second = [r.id for r in order_rows(list(reversed(rows)))]
    assert first == second


def test_filtering_by_kind_and_tier():
    rows = [
        row_from_proposal(_prop("a", kind="skill"), risk_tier="low"),
        row_from_proposal(_prop("b", kind="lesson_batch"), risk_tier="manual_only"),
    ]
    assert [r.id for r in filter_rows(rows, kind="skill")] == ["a"]
    assert [r.id for r in filter_rows(rows, tier="manual_only")] == ["b"]


def test_flagged_only_surfaces_the_broken_manifests():
    """§3.1 RECORDS invalid manifests rather than rejecting them; a flag nobody can filter to is a
    flag nobody sees."""
    rows = [
        row_from_proposal(_prop("ok")),
        row_from_proposal(_prop("broken", manifest_valid=False, manifest_issues=["no root_cause"])),
    ]
    assert [r.id for r in filter_rows(rows, flagged_only=True)] == ["broken"]


def test_an_invalid_manifest_still_appears_in_the_queue():
    """ "Lenient-but-recording": dropping it would hide a refiner bug behind an empty inbox."""
    view = build_view([_prop("broken", manifest_valid=False)])
    assert view.total == 1 and view.flagged == 1


def test_filters_compose():
    rows = [
        row_from_proposal(_prop("a", kind="skill", manifest_valid=False), risk_tier="low"),
        row_from_proposal(_prop("b", kind="skill"), risk_tier="low"),
    ]
    assert [r.id for r in filter_rows(rows, kind="skill", flagged_only=True)] == ["a"]


# ── the assembled view ──


def test_counts_exist_for_every_filter_chip():
    """A filter chip with no count is a chip a user has to click to discover is empty."""
    view = build_view(
        [_prop("a", kind="skill"), _prop("b", kind="skill"), _prop("c", kind="retirement")],
        tiers={"c": "manual_only"},
    )
    assert view.by_kind == {"retirement": 1, "skill": 2}
    assert view.by_tier["manual_only"] == 1


def test_the_view_serializes_for_an_api():
    payload = build_view([_prop()]).to_dict()
    assert set(payload) >= {
        "rows",
        "total",
        "by_kind",
        "by_tier",
        "flagged",
        "unrenderable",
        "bulk_acceptable",
    }


def test_an_empty_queue_is_handled():
    view = build_view([])
    assert view.total == 0 and view.by_kind == {} and view.unrenderable == []


def test_a_missing_tier_defaults_to_review_not_low():
    """Only a `template_diff` carries typed ops to derive a tier from; defaulting to `low` stamps
    bulk-accept eligibility on a kind nobody scored."""
    row = row_from_proposal(_prop(kind="lesson_batch"))
    assert row.risk_tier == "review"


def test_the_view_is_a_projection_and_writes_nothing():
    """Asserted against the source: the store stays in `learning.proposals`."""
    from personalclaw.learning import inbox

    src = inspect.getsource(inbox)
    for forbidden in ("atomic_write", "_save(", "sqlite3", ".unlink(", "open("):
        assert forbidden not in src, f"the inbox writes via {forbidden}"


# ── the gate wired into the REAL accept/reject path ──


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the proposal store at tmp_path.

    Patches the accessors rather than `config_dir`, matching this program's convention: the module
    resolves them per call, so patching the accessor is what actually redirects it — and nothing can
    reach the real home.
    """
    from personalclaw.learning import proposals as P

    monkeypatch.setattr(P, "_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_decisions_path", lambda: tmp_path / "decisions.json")
    return P


def _filed(store, pid="p1", **kw):
    base = dict(
        id=pid, kind="lesson_batch", title="t", body="b", provenance="refiner", status="pending"
    )
    base.update(kw)
    prop = store.Proposal(**base)
    store._save(prop)
    return prop


def test_the_real_accept_refuses_an_agent(store):
    """The wiring, not just the decision. Without this the gate is another module with no caller and
    the invariant stays an absence."""
    _filed(store, "p-agent")
    with pytest.raises(store.AcceptError, match="propose but never accept"):
        store.accept("p-agent", actor="agent")


def test_the_real_accept_refuses_the_engine(store):
    _filed(store, "p-engine")
    with pytest.raises(store.AcceptError, match="human reviewer"):
        store.accept("p-engine", actor="engine")


def test_the_real_accept_refuses_an_unknown_actor(store):
    _filed(store, "p-ghost")
    with pytest.raises(store.AcceptError, match="unrecognized actor"):
        store.accept("p-ghost", actor="ghost")


def test_the_real_accept_allows_a_human(store):
    _filed(store, "p-user")
    assert store.accept("p-user", actor="user").status == "accepted"


def test_an_agent_cannot_accept_even_a_proposal_it_filed(store):
    """ "Its own proposals" is the phrase §7 uses, and the gate does not need to know who filed it —
    an agent may never accept anything, which is the stronger and simpler property."""
    _filed(store, "own", provenance="agent:refiner")
    with pytest.raises(store.AcceptError):
        store.accept("own", actor="agent")


def test_a_refused_accept_leaves_the_proposal_pending(store):
    """A blocked decision must not consume the row: the human still has to see it."""
    _filed(store, "p1")
    with pytest.raises(store.AcceptError):
        store.accept("p1", actor="agent")
    assert store.get("p1") is not None
    assert store.get("p1").status == "pending"


def test_the_real_reject_refuses_an_agent(store):
    """An agent that could reject would clear its own bad proposals before a human read them, and
    §2.2's rejection exemplars would stop accumulating."""
    _filed(store, "r1")
    assert store.reject("r1", actor="agent") is False
    assert store.get("r1") is not None


def test_the_real_reject_allows_a_human(store):
    _filed(store, "r2")
    assert store.reject("r2", actor="user") is True


def test_the_actor_defaults_to_user_so_existing_callers_are_unaffected(store):
    """Every human-facing caller predates the gate; a required parameter would break them all."""
    _filed(store, "legacy")
    assert store.accept("legacy").status == "accepted"


def test_a_missing_proposal_still_raises_rather_than_being_gated(store):
    """Order matters: a nonexistent id is a caller bug regardless of actor, and reporting it as a
    permission problem would send someone hunting the wrong thing."""
    with pytest.raises(store.AcceptError, match="no proposal"):
        store.accept("nope", actor="agent")


def test_accept_and_reject_both_take_an_actor():
    """Asserted on the signatures, so a future refactor that drops the parameter fails here."""
    from personalclaw.learning import proposals as P

    assert "actor" in inspect.signature(P.accept).parameters
    assert "actor" in inspect.signature(P.reject).parameters
