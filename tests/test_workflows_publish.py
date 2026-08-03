"""Tests for `publish:`, material-change gating and evidence bundles (WORK-CONTAINERS §2, S47).

The property carrying this module: **a declaration is a promise about output.** A malformed
`publish:` FAILS the node rather than degrading to "no publish" — a node whose author declared a
deliverable and silently produced nothing would report success, which is the completion-lie
class the
artifact gate exists to catch.

The asymmetry beside it: a REGISTRY failure does not fail the node. The work happened; losing
the copy
is worth reporting, not worth discarding a completed stage over. A bad declaration is the
author's bug
(fail loudly); a registry outage is the environment's (degrade honestly).

The second property is that material-change gating protects a finite resource. The artifact registry
keeps 50 snapshots; a refinement loop that published every round would consume the window in five
runs, so the window that exists to hold real revision history would hold near-duplicates.
"""

import pytest

from personalclaw.workflows.publish import (
    BUNDLE_SCHEMA,
    HANDOFF_SECTIONS,
    MATERIAL_CHANGE_RATIO,
    PUBLISHABLE_KINDS,
    EvidenceFile,
    Lineage,
    PublishAction,
    PublishSpec,
    append_ledger_rows,
    content_hash,
    evidence_bundle,
    handoff_report,
    ledger_row,
    materially_changed,
    parse_publish,
    skipped_without_reason,
    upsert_plan,
)

LONG = "The ingest pipeline batches every thirty seconds. " * 20


# ── parsing the declaration ──


def test_a_bare_name_is_accepted():
    """The common case. Requiring the object form for `publish: my-report` would be ceremony on the
    shape a template author reaches for first."""
    spec, error = parse_publish({"publish": "Weekly digest"})
    assert error == ""
    assert spec.artifact == "Weekly digest"
    assert spec.kind == "markdown"


def test_the_object_form_carries_a_kind():
    spec, error = parse_publish({"publish": {"artifact": "Report", "kind": "json"}})
    assert error == ""
    assert spec.kind == "json"


def test_no_publish_block_is_not_an_error():
    assert parse_publish({}) == (None, "")


def test_a_publish_with_no_NAME_is_an_error():
    """Publishing by name is the whole mechanism — a nameless declaration cannot land anywhere."""
    spec, error = parse_publish({"publish": {"kind": "markdown"}})
    assert spec is None
    assert "no artifact name" in error


def test_an_unpublishable_KIND_is_refused_rather_than_coerced():
    """Measured precedent in the artifact registry: `video_generate` passed a kind in neither
    set, so
    every generated video was silently stored AS AN IMAGE for months. A refusal here is that
    class of
    bug not recurring."""
    spec, error = parse_publish({"publish": {"artifact": "R", "kind": "image"}})
    assert spec is None
    assert "not publishable" in error


def test_a_binary_kind_is_not_publishable():
    """A run's text output cannot be a binary artifact body, and letting it through would store a
    string where a renderer expects bytes."""
    assert "image" not in PUBLISHABLE_KINDS
    assert "pdf" not in PUBLISHABLE_KINDS


def test_an_unknown_lineage_EDGE_is_refused():
    """The three edge types answer three different questions. A fourth invented edge would be an
    untyped link nobody can interpret, which is what typing them was for."""
    spec, error = parse_publish({"publish": {"artifact": "R", "lineage": {"caused_by": ["run:1"]}}})
    assert spec is None
    assert "unknown lineage edge" in error


def test_a_non_object_publish_is_refused():
    spec, error = parse_publish({"publish": ["a", "b"]})
    assert spec is None
    assert error


# ── material-change gating ──


def test_identical_content_does_not_earn_a_version():
    """A refinement round that converged republishes the same body. Recording it as a new version
    would spend one of fifty snapshots to say nothing changed."""
    changed, why = materially_changed(LONG, LONG)
    assert changed is False
    assert "identical" in why


def test_a_whitespace_only_change_is_not_material():
    """A model that re-emits its output with one different newline has not revised anything."""
    assert materially_changed(LONG, LONG.replace(". ", ".  "))[0] is False


def test_a_real_rewrite_IS_material():
    changed, why = materially_changed(LONG, "A completely different summary of the pipeline.")
    assert changed is True
    assert "%" in why


def test_a_tiny_edit_below_the_threshold_is_not_material():
    tweaked = LONG[:-2] + "X."
    changed, why = materially_changed(LONG, tweaked)
    assert changed is False
    assert f"{MATERIAL_CHANGE_RATIO:.0%}" in why


def test_publishing_over_a_real_body_with_an_EMPTY_one_is_refused():
    """The single most destructive publish: a node that failed to produce output replacing a good
    artifact with nothing, and bumping the version so the good body is one revert away at best."""
    changed, why = materially_changed(LONG, "   ")
    assert changed is False
    assert "empty" in why


def test_first_content_is_always_material():
    assert materially_changed("", "anything")[0] is True


def test_the_reason_is_returned_even_when_nothing_changed():
    """A silent no-op looks identical to a failed write. A user who refined a document and saw
    no new
    version needs to know the system judged it unchanged, not that the publish broke."""
    assert materially_changed(LONG, LONG)[1]


def test_the_hash_ignores_whitespace():
    assert content_hash("a  b\n") == content_hash("a b")


def test_the_hash_distinguishes_real_differences():
    assert content_hash("a b") != content_hash("a c")


# ── the upsert decision ──


def spec(name: str = "Weekly digest", **kw) -> PublishSpec:
    return PublishSpec(artifact=name, **kw)


def test_no_existing_artifact_means_CREATE():
    plan = upsert_plan(spec(), "body", existing_content=None)
    assert plan.action is PublishAction.CREATE
    assert plan.change_note == "first publish"


def test_a_material_change_means_a_new_VERSION():
    plan = upsert_plan(spec(), "a wholly different body", existing_content=LONG)
    assert plan.action is PublishAction.VERSION


def test_an_immaterial_change_is_a_NOOP_not_a_failure():
    """ "Nothing material changed" is the correct answer for a converged refinement round. Reporting
    it as an error would make a converged loop look broken."""
    plan = upsert_plan(spec(), LONG, existing_content=LONG)
    assert plan.action is PublishAction.NOOP
    assert plan.reason


def test_a_new_version_carries_a_CHANGE_NOTE():
    """A version with no note is a revision nobody can explain later."""
    plan = upsert_plan(spec(), "different", existing_content=LONG)
    assert plan.change_note


def test_an_explicit_change_note_wins_over_the_derived_one():
    plan = upsert_plan(spec(), "different", existing_content=LONG, change_note="addressed review")
    assert plan.change_note == "addressed review"


def test_provenance_is_attached_on_every_action_INCLUDING_the_noop():
    """A reader asking "which run produced this" needs an answer even when the latest run changed
    nothing — otherwise a converged refinement loop makes the artifact look abandoned by its
    producer."""
    plan = upsert_plan(spec(), LONG, existing_content=LONG, run_id="r-1", node_id="write")
    assert plan.meta == {"run_id": "r-1", "node_id": "write"}


def test_the_source_lineage_edge_names_the_run_and_node():
    plan = upsert_plan(spec(), "body", existing_content=None, run_id="r-1", node_id="write")
    assert plan.lineage[Lineage.SOURCE.value] == ["run:r-1#write"]


def test_republishing_from_the_same_run_does_not_duplicate_the_edge():
    existing = {Lineage.SOURCE.value: ["run:r-1#write"]}
    plan = upsert_plan(
        spec(lineage=existing), "body", existing_content=None, run_id="r-1", node_id="write"
    )
    assert plan.lineage[Lineage.SOURCE.value] == ["run:r-1#write"]


def test_declared_lineage_survives_the_upsert():
    """A template author's INFORMED_BY edges are evidence about what the run read. Dropping them
    while adding the source edge would lose the half an audit cares about."""
    plan = upsert_plan(
        spec(lineage={Lineage.INFORMED_BY.value: ["knowledge:item-7"]}),
        "body",
        existing_content=None,
        run_id="r-1",
    )
    assert plan.lineage[Lineage.INFORMED_BY.value] == ["knowledge:item-7"]
    assert plan.lineage[Lineage.SOURCE.value] == ["run:r-1"]


# ── evidence bundles ──


def files(*names) -> list[EvidenceFile]:
    return [EvidenceFile(name=n, kind="image", size=100, sha256=f"hash-{n}") for n in names]


def test_a_bundle_carries_a_digest_per_file():
    """An evidence bundle exists so "what did my machine do while I slept" has PROOF. A manifest
    listing a screenshot with no digest cannot tell you it is still the one the run took."""
    bundle = evidence_bundle(files("after.png", "before.png"))
    assert all(f["sha256"] for f in bundle["files"])


def test_bundle_files_are_SORTED_so_two_identical_bundles_are_identical():
    """An unstable order would make every bundle look changed to the material-change gate, defeating
    it for exactly the artifact kind that is re-published most."""
    a = evidence_bundle(files("b.png", "a.png"))
    b = evidence_bundle(files("a.png", "b.png"))
    assert a == b


def test_a_bundle_declares_its_schema_version():
    """A manifest with no version is one a later reader has to guess the shape of, and the
    guess will
    be wrong exactly when the shape changed."""
    assert evidence_bundle([])["schema"] == BUNDLE_SCHEMA


def test_an_empty_bundle_is_honest_about_being_empty():
    bundle = evidence_bundle([], run_id="r-1")
    assert bundle["count"] == 0
    assert bundle["files"] == []


def test_a_bundle_file_may_declare_an_expiry():
    """Screenshots and video age out; a manifest that could not say so would point at files that
    quietly stopped existing."""
    payload = EvidenceFile(
        name="v.mp4", kind="video", size=1, sha256="h", expires_at="2026-09-01"
    ).to_dict()
    assert payload["expires_at"] == "2026-09-01"


def test_a_file_with_no_expiry_omits_the_key():
    assert "expires_at" not in files("a.png")[0].to_dict()


# ── the terminal handoff report ──


def test_every_section_is_present_even_when_nothing_was_recorded():
    """An absent `side_effects` section reads as "nothing was committed or sent", which is a claim —
    and it is the claim a user most wants to be true and least wants to be guessed."""
    report = handoff_report(did=["ran the tests"])
    for key in HANDOFF_SECTIONS:
        assert report[key]
    assert report["side_effects"] == ["nothing recorded"]


def test_each_section_carries_its_PURPOSE():
    """The shape is the contract that lets the board render any template's report with no
    per-template code. A report whose shape varied would get one generic renderer showing none of
    it."""
    report = handoff_report()
    assert "reason" in report["skipped_purpose"]


def test_a_single_string_section_is_accepted():
    assert handoff_report(did="wrote the file")["did"] == ["wrote the file"]


def test_a_skip_WITHOUT_a_reason_is_flagged():
    """A skip with no reason is the most misleading line in a report: the reader cannot tell a
    deliberate omission from a silent failure, so they must re-do the work to find out."""
    report = handoff_report(skipped=["the deploy step", "linting because the config was missing"])
    assert skipped_without_reason(report) == ["the deploy step"]


def test_nothing_recorded_is_not_flagged_as_a_reasonless_skip():
    assert skipped_without_reason(handoff_report()) == []


@pytest.mark.parametrize(
    "text",
    [
        "skipped the push because no remote is configured",
        "skipped linting since the config was missing",
        "deploy blocked on credentials",
        "tests failed so the commit was not made",
    ],
)
def test_a_reasoned_skip_is_accepted(text):
    assert skipped_without_reason(handoff_report(skipped=[text])) == []


# ── the results ledger ──


def test_a_reverted_attempt_is_RECORDED_not_dropped():
    """An attempt log that dropped failures would make a five-attempt convergence look like a
    first-try success, and the next run would repeat the four failures."""
    row = ledger_row(3, outcome="failed", note="lint error", reverted=True)
    assert row["reverted"] is True
    assert row["attempt"] == 3


def test_the_ledger_is_append_only():
    rows = append_ledger_rows([ledger_row(1, outcome="ok")], [ledger_row(2, outcome="ok")])
    assert [r["attempt"] for r in rows] == [1, 2]


def test_a_repeated_attempt_number_is_KEPT():
    """Two rows for attempt 3 means the attempt was re-run. Collapsing them by attempt number would
    hide a retry, which is the single most useful thing a results ledger records."""
    rows = append_ledger_rows(
        [ledger_row(3, outcome="failed")], [ledger_row(3, outcome="ok", note="retried")]
    )
    assert len(rows) == 2


def test_appending_to_an_empty_ledger_works():
    assert len(append_ledger_rows([], [ledger_row(1, outcome="ok")])) == 1


# ── the engine seam ──


def test_a_malformed_declaration_FAILS_the_node():
    """The whole property: a declaration is a promise about output. Degrading to "no publish" would
    let a node whose author declared a deliverable report success while producing nothing."""
    from personalclaw.workflows.engine import NodeResult, apply_publish
    from personalclaw.workflows.models import InstanceState, Node

    node = Node.from_dict(
        {"kind": "stage", "id": "s", "config": {"prompt": "x", "publish": {"kind": "markdown"}}}
    )
    result = apply_publish(node, NodeResult(state=InstanceState.DONE, output="body"), run_id="r")
    assert result.state is InstanceState.FAILED
    assert "invalid publish declaration" in result.failure.cause_plain


def test_a_node_with_no_publish_block_is_untouched():
    from personalclaw.workflows.engine import NodeResult, apply_publish
    from personalclaw.workflows.models import InstanceState, Node

    node = Node.from_dict({"kind": "stage", "id": "s", "config": {"prompt": "x"}})
    result = apply_publish(node, NodeResult(state=InstanceState.DONE, output="body"))
    assert result.published is None
    assert result.output == "body"


def test_a_FAILED_node_does_not_publish():
    """Publishing the output of a node that failed would store a deliverable the run does not stand
    behind — and the artifact would carry the run's provenance while contradicting its outcome."""
    from personalclaw.workflows.engine import NodeResult, apply_publish
    from personalclaw.workflows.models import InstanceState, Node

    node = Node.from_dict(
        {"kind": "stage", "id": "s", "config": {"prompt": "x", "publish": "Report"}}
    )
    result = apply_publish(node, NodeResult(state=InstanceState.FAILED, output="partial"))
    assert result.published is None


def test_a_non_text_output_is_a_recorded_NOOP():
    """A node whose output is structured data the caller binds elsewhere has still done its job.
    Recording the no-op keeps the absence visible instead of looking like a lost publish."""
    from personalclaw.workflows.engine import NodeResult, apply_publish
    from personalclaw.workflows.models import InstanceState, Node

    node = Node.from_dict(
        {"kind": "stage", "id": "s", "config": {"prompt": "x", "publish": "Report"}}
    )
    result = apply_publish(node, NodeResult(state=InstanceState.DONE, output={"rows": [1, 2]}))
    assert result.published["action"] == "noop"
    assert "not text" in result.published["reason"]


def test_the_publish_outcome_is_a_DECLARED_field_so_it_reaches_the_journal():
    """An attribute set on the instance would work at runtime and never be serialized, so the ledger
    would show a published artifact with no record of the publish."""
    from dataclasses import fields

    from personalclaw.workflows.engine import NodeResult

    assert "published" in {f.name for f in fields(NodeResult)}


def test_a_string_output_stays_reachable_at_its_original_binding_path():
    """Wrapping it in a dict would break every `{{nodes.x.output}}` downstream, so publishing a
    node's output would change what its consumers read."""
    from personalclaw.workflows.engine import NodeResult, apply_publish
    from personalclaw.workflows.models import InstanceState, Node

    node = Node.from_dict(
        {"kind": "stage", "id": "s", "config": {"prompt": "x", "publish": "Report"}}
    )
    result = apply_publish(
        node, NodeResult(state=InstanceState.DONE, output="the body"), run_id="r"
    )
    assert result.output == "the body"


# ── provenance has to REACH the artifact ──


def test_lineage_flattens_to_SCALAR_keys():
    """Measured: `clean_event_metadata` bounds event metadata to string-keyed scalars ≤256 chars — a
    deliberate size bound. Passing the nested lineage dict through stringified it into a Python repr
    (`"{'informed_by': ['knowledge:item-7']}"`) that no reader can parse. Widening the sanitizer
    would loosen a bound that exists on purpose."""
    from personalclaw.workflows.publish import flatten_lineage

    flat = flatten_lineage({"source": ["run:r-1#write"], "informed_by": ["k:7", "k:9"]})
    assert flat == {"lineage_source": "run:r-1#write", "lineage_informed_by": "k:7,k:9"}
    assert all(isinstance(v, str) for v in flat.values())


def test_lineage_round_trips():
    """The writer and reader ship together, so the format is one decision in one place. A
    writer whose
    reader lives elsewhere is a format that drifts."""
    from personalclaw.workflows.publish import flatten_lineage, parse_lineage

    original = {"source": ["run:r-1#write"], "informed_by": ["k:7", "k:9"]}
    assert parse_lineage(flatten_lineage(original)) == original


def test_an_empty_edge_is_OMITTED_not_written_as_blank():
    """A key whose value is empty reads as "this edge was considered and found nothing", which is a
    claim the publish never made."""
    from personalclaw.workflows.publish import flatten_lineage

    assert flatten_lineage({"related": []}) == {}


def test_parsing_ignores_non_lineage_metadata_keys():
    from personalclaw.workflows.publish import parse_lineage

    assert parse_lineage({"run_id": "r-1", "lineage_source": "run:r-1"}) == {"source": ["run:r-1"]}


def test_the_artifact_WRITERS_can_carry_event_provenance():
    """Measured: `ArtifactEvent.metadata` and `clean_event_metadata` both existed, but only
    `record_impression` could reach them — so a workflow computed a full run/node lineage and the
    artifact landed carrying none of it. Provenance computed and discarded."""
    import inspect

    from personalclaw.artifacts.native import NativeArtifactProvider

    for method in ("create", "update", "create_binary"):
        params = inspect.signature(getattr(NativeArtifactProvider, method)).parameters
        assert "event_metadata" in params, method


def test_publishing_records_the_run_on_the_artifacts_own_event(tmp_path, monkeypatch):
    """The end-to-end claim, against the REAL registry: after a publish, "which run produced this"
    has an answer on disk."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    from personalclaw.artifacts import native, registry
    from personalclaw.workflows.engine import NodeResult, apply_publish
    from personalclaw.workflows.models import InstanceState, Node
    from personalclaw.workflows.publish import parse_lineage

    provider = native.NativeArtifactProvider()
    monkeypatch.setattr(provider, "_root", home / "artifacts", raising=False)
    registry.register_provider(provider)
    try:
        node = Node.from_dict(
            {
                "kind": "stage",
                "id": "write",
                "config": {
                    "prompt": "x",
                    "publish": {
                        "artifact": "Provenance probe",
                        "kind": "markdown",
                        "lineage": {"informed_by": ["knowledge:item-7"]},
                    },
                },
            }
        )
        result = apply_publish(
            node,
            NodeResult(state=InstanceState.DONE, output="A body about the ingest internals."),
            run_id="r-prov",
        )
        assert result.published["action"] == "create"
        art = provider.find_similar("Provenance probe")
        assert art is not None
        event = art.events[0]
        assert event.metadata["run_id"] == "r-prov"
        assert event.metadata["node_id"] == "write"
        edges = parse_lineage(event.metadata)
        assert edges["source"] == ["run:r-prov#write"]
        assert edges["informed_by"] == ["knowledge:item-7"]
    finally:
        registry.unregister_provider(provider.name)
