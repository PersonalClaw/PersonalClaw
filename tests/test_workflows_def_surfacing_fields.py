"""The def-side surfacing fields and the def→record adapter (TASKS-SOPS §2 — S61).

S58-S60 built the surfacing records as pure decision modules. `WorkflowDef` had none of the fields
they read — measured against `dataclasses.fields`, there was no `surface_mode`, `cadence_days`,
`escalation`, `packs`, `hands_off_to` or `guided` anywhere on the def. So every one of those
mechanisms was reachable only by a caller that hand-built a record, which is another way of saying
none of them could be driven by an authored template.

This session adds them to `DefMetadata` — the TYPED metadata block, not `extra` — for the reason
that block's own comment records: `from_dict` drops what it does not name, and annotating all 18
bundled templates with `keywords` once left the matcher reading 0/18 while the authors believed the
field was set. A field in an open dict is a field the reader treats as absent.

The adapter is ONE conversion point per record type. Two readers of the same fields drift, and the
drift shows as a def that surfaces through one path and not the other for identical metadata.
"""

import pytest

from personalclaw.workflows import surfacing_channels as sc
from personalclaw.workflows.models import DefMetadata, Node, NodeKind
from personalclaw.workflows.pool import SurfaceRoute
from personalclaw.workflows.surfacing import SurfaceMode
from personalclaw.workflows.surfacing_channels import Escalation


def _meta(**kw) -> DefMetadata:
    return DefMetadata.from_dict(kw)


# ── the fields exist and are TYPED ──


def test_the_surfacing_fields_are_TYPED_not_stashed_in_extra():
    """`DefMetadata.from_dict` drops what it does not name. Measured once already: all 18 bundled
    templates carried `keywords` and the matcher read 0 of 18, running entirely on description
    overlap while reporting 0.02-0.22 confidence — a control present and inert."""
    import dataclasses as dc

    names = {f.name for f in dc.fields(DefMetadata)}
    assert {
        "surface_mode",
        "agent_digest",
        "summary",
        "when_to_use",
        "cadence_days",
        "escalation",
        "packs",
        "hands_off_to",
        "guided",
    } <= names


def test_a_new_def_defaults_to_OFF():
    """OpenSquilla shipped auto-trigger-by-default and retreated to manual-first after pasted
    content kept firing workflows. Explicit invocation always works regardless."""
    assert DefMetadata().surface_mode == "off"


def test_the_fields_ROUND_TRIP():
    """The config round-trip contract: a field that serializes but does not deserialize is one that
    silently resets on the next read."""
    meta = _meta(
        surface_mode="suggest",
        agent_digest="do the thing",
        summary="s",
        when_to_use="w",
        cadence_days=7,
        escalation="auto",
        packs=["python-project"],
        hands_off_to=[{"target_def": "bug-fix", "context_fields": ["id"]}],
        guided=True,
    )
    assert DefMetadata.from_dict(meta.to_dict()) == meta


def test_every_new_field_appears_in_to_dict():
    payload = DefMetadata().to_dict()
    for key in (
        "surface_mode",
        "agent_digest",
        "summary",
        "when_to_use",
        "cadence_days",
        "escalation",
        "packs",
        "hands_off_to",
        "guided",
    ):
        assert key in payload, f"{key} serializes to nothing, so it cannot survive a save"


# ── tolerant coercion, in the SAFE direction ──


def test_an_UNKNOWN_surface_mode_reads_as_OFF():
    """A typo must not silently START surfacing a def. `off` is the direction that spends no tokens
    and injects no text the author did not intend."""
    assert _meta(surface_mode="vibes").surface_mode == "off"
    assert _meta(surface_mode="").surface_mode == "off"
    assert _meta(surface_mode=None).surface_mode == "off"


def test_surface_mode_is_case_INSENSITIVE():
    assert _meta(surface_mode="SUGGEST").surface_mode == "suggest"


def test_an_UNKNOWN_escalation_reads_as_MANUAL():
    """Manual materializes nothing — the reading that cannot put an unwanted task on the board."""
    assert _meta(escalation="aggressive").escalation == "manual"


def test_a_NEGATIVE_cadence_is_clamped_to_zero():
    """A negative cadence makes every comparison read as overdue, so a fat-fingered `-7` would nag
    forever."""
    assert _meta(cadence_days=-7).cadence_days == 0


def test_a_NON_NUMERIC_cadence_is_zero_not_a_crash():
    """Def metadata is authored as YAML by hand; a string here is an author typo, not a reason to
    fail loading the whole def."""
    assert _meta(cadence_days="weekly").cadence_days == 0


def test_guided_must_be_the_BOOLEAN_true():
    """Same rule §4 applies to `require_hitl`: a truthy string is an author mistake, and treating
    `"false"` as guided would surprise them with a mode they cannot explain."""
    assert _meta(guided="yes").guided is False
    assert _meta(guided=True).guided is True


def test_a_NON_DICT_handoff_entry_is_dropped():
    assert _meta(hands_off_to=["bug-fix", {"target_def": "ok"}]).hands_off_to == [
        {"target_def": "ok"}
    ]


# ── the adapter: ONE conversion point per record ──


def test_the_def_drives_S58s_surfacing_record():
    """Two readers of the same fields drift, and the drift shows as a def that surfaces through one
    path and not the other for identical metadata."""
    meta = sc.meta_from_def(_meta(surface_mode="passive", match_text="back up", agent_digest="d"))
    assert meta.surface_mode is SurfaceMode.PASSIVE
    assert meta.match_text == "back up"
    assert meta.agent_digest == "d"


def test_the_adapter_does_NOT_re_implement_tolerance():
    """`from_dict` already coerced an unknown mode to `off`, so by the time the adapter runs
    there is one tolerance rule, not two that could disagree."""
    assert sc.meta_from_def(_meta(surface_mode="nonsense")).surface_mode is SurfaceMode.OFF


def test_the_def_drives_the_CADENCE_record():
    state = sc.cadence_from_def(
        "backup", _meta(cadence_days=7, escalation="auto"), last_completed_at=100.0
    )
    assert state.cadence_days == 7
    assert state.escalation is Escalation.AUTO
    assert state.last_completed_at == 100.0


def test_the_run_FACTS_are_parameters_not_lookups():
    """`last_completed` reads the run table; a channel that queried per def would issue one query
    per template on every list render."""
    state = sc.cadence_from_def("d", _meta(cadence_days=1))
    assert state.last_completed_at == 0.0
    assert state.in_flight is False


def test_the_def_drives_the_HANDOFF_edges():
    edges = sc.handoffs_from_def(
        _meta(
            hands_off_to=[
                {"target_def": "bug-fix", "context_fields": ["id"], "requires_user_request": True}
            ]
        )
    )
    assert edges[0].target_def == "bug-fix"
    assert edges[0].context_fields == ["id"]
    assert edges[0].requires_user_request is True


def test_an_edge_pointing_NOWHERE_is_dropped():
    """A suggestion the user cannot accept is a dead affordance, and a dead affordance teaches them
    to ignore the live ones."""
    assert sc.handoffs_from_def(_meta(hands_off_to=[{"condition": "always"}])) == []


def test_the_doctor_entry_includes_PACKS():
    """Assembled here rather than per call site: a surface building this dict itself would forget
    `packs` and report every pack-gated def as unreachable."""
    entry = sc.doctor_entry("d", _meta(surface_mode="passive", packs=["python-project"]))
    assert entry["packs"] == ["python-project"]
    assert sc.doctor([entry]) == []


def test_a_def_reachable_only_by_CADENCE_passes_the_doctor():
    entry = sc.doctor_entry("backup", _meta(surface_mode="passive", cadence_days=7))
    assert sc.doctor([entry]) == []


def test_a_def_with_NO_channel_is_reported():
    entry = sc.doctor_entry("ghost", _meta(surface_mode="passive"))
    assert sc.doctor([entry])


# ── routing reads the REAL node tree ──


def _gate_in_branch() -> Node:
    """A gate buried under `cases` — the shape a hand-rolled walk misses.

    `cases` is a DICT keyed by label, not a list. Measured while writing this test: a list built a
    Node that `walk` raised on, and the raise was swallowed by `route_from_def`'s fallback — so the
    assertion passed for the WRONG reason and proved nothing about traversal.
    """
    gate = Node(kind=NodeKind.GATE, id="g", config={"kind": "approval"})
    return Node(
        kind=NodeKind.BRANCH,
        id="b",
        cases={"hit": Node(kind=NodeKind.SEQUENCE, id="s", children=[gate])},
    )


def test_a_gate_buried_in_BRANCH_CASES_still_routes_to_a_RUN():
    """S45 measured a hand-rolled walk finding 4 of 13 nodes because branch children live under
    `cases`/`default_case`. Missing a gate here would route a gated def to a blueprint, which has no
    engine to pause."""
    assert sc.route_from_def(_meta(surface_mode="passive", guided=True), _gate_in_branch()) is (
        SurfaceRoute.RUN
    )


def test_a_lightweight_GUIDED_def_is_a_blueprint():
    root = Node(
        kind=NodeKind.SEQUENCE,
        id="s",
        children=[Node(kind=NodeKind.INFER, id="i", config={"prompt": "x"})],
    )
    assert sc.route_from_def(_meta(surface_mode="passive", guided=True), root) is (
        SurfaceRoute.BLUEPRINT
    )


def test_a_lightweight_def_that_is_not_guided_stays_PASSIVE():
    root = Node(kind=NodeKind.SEQUENCE, id="s", children=[])
    assert sc.route_from_def(_meta(surface_mode="passive"), root) is SurfaceRoute.PASSIVE


def test_a_MULTI_TURN_stage_forces_a_run_even_when_guided():
    root = Node(
        kind=NodeKind.SEQUENCE,
        id="s",
        children=[Node(kind=NodeKind.STAGE, id="st", config={"max_turns": 5})],
    )
    assert sc.route_from_def(_meta(surface_mode="passive", guided=True), root) is SurfaceRoute.RUN


def test_a_SCHEMA_bearing_stage_forces_a_run():
    root = Node(
        kind=NodeKind.SEQUENCE,
        id="s",
        children=[Node(kind=NodeKind.STAGE, id="st", config={"schema": {"type": "object"}})],
    )
    assert sc.route_from_def(_meta(surface_mode="passive", guided=True), root) is SurfaceRoute.RUN


def test_an_UNWALKABLE_spec_routes_to_a_RUN_not_a_blueprint():
    """The engine is the only thing that can report why a malformed graph will not run; a blueprint
    would silently render nothing and the user would think the def was empty."""
    assert sc.route_from_def(_meta(surface_mode="passive", guided=True), object()) is (
        SurfaceRoute.RUN
    )


def test_an_OFF_def_never_routes_to_a_blueprint():
    """`off` means the def does not surface itself, so a guided `off` def must not become a
    blueprint — that would materialize a conversation the user switched off."""
    root = Node(kind=NodeKind.SEQUENCE, id="s", children=[])
    assert sc.route_from_def(_meta(surface_mode="off", guided=True), root) is SurfaceRoute.PASSIVE


def test_an_OFF_def_that_NEEDS_the_engine_still_reports_RUN():
    """Measured: the structural facts win over the mode here. A gated def IS a run — reporting it as
    passive would tell a caller it could be injected as text, which would silently drop its gate.
    `off` governs whether it SURFACES (S58's veto owns that), not what it structurally is."""
    assert sc.route_from_def(_meta(surface_mode="off"), _gate_in_branch()) is SurfaceRoute.RUN


# ── the def still round-trips as a whole ──


def test_a_WHOLE_def_round_trips_with_the_new_fields():
    """The fields live on `DefMetadata`, which `WorkflowDef.to_dict` nests — so a def-level round
    trip is what proves an authored template keeps its surfacing configuration across a save."""
    from personalclaw.workflows.models import WorkflowDef

    original = WorkflowDef(
        name="backup",
        root=Node(kind=NodeKind.SEQUENCE, id="s", children=[]),
        metadata=_meta(surface_mode="suggest", cadence_days=30, packs=["ci"], guided=True),
    )
    restored = WorkflowDef.from_dict(original.to_dict())
    assert restored.metadata.surface_mode == "suggest"
    assert restored.metadata.cadence_days == 30
    assert restored.metadata.packs == ["ci"]
    assert restored.metadata.guided is True


@pytest.mark.parametrize("mode", ["off", "passive", "suggest"])
def test_each_declared_mode_survives_a_def_round_trip(mode):
    from personalclaw.workflows.models import WorkflowDef

    original = WorkflowDef(
        name="d",
        root=Node(kind=NodeKind.SEQUENCE, id="s", children=[]),
        metadata=_meta(surface_mode=mode),
    )
    assert WorkflowDef.from_dict(original.to_dict()).metadata.surface_mode == mode
