"""`knowledge-propose` — the workflow path into the LEARNING-FLYWHEEL review queue.

WF2KNO-8 (KNOWLEDGE-SYNTHESIS §3.3/§3.4). Three things are worth a test here, and none of
them are the happy path on its own:

1. **The wiring.** A provider registered in one set and absent from the other validates,
   saves, and then fails at run time — so the template's node name, the registry, the hook
   allowlist and the capability fence are all asserted.
2. **The silent drop.** `proposals.enqueue` SKIPS an unknown kind and logs at *debug*. That
   is what made the pre-WF2KNO-8 workaround necessary and invisible, so the kind's presence
   is asserted directly rather than inferred from a green filing.
3. **SKIP is success.** A prior decision forbidding a re-file is the queue working. A
   provider that failed the node on it would make a correct cadence look broken.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.knowledge_propose_provider import (
    KnowledgeProposeActionProvider,
)
from personalclaw.learning import proposals

BUNDLED = Path(__file__).resolve().parents[1] / "src/personalclaw/workflows/bundled"


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def body(result):
    return json.loads(result.stdout)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. Filing writes a durable proposal row and an inbox item — never the
    developer's own store."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def ctx():
    return ActionContext(event="workflow_node", payload={"node_id": "file", "run_id": "run-1"})


def _gap_healing() -> dict:
    return json.loads((BUNDLED / "gap-healing/workflow.json").read_text())


def _node(spec: dict, node_id: str) -> dict:
    for child in spec["root"]["children"]:
        if child.get("id") == node_id:
            return child
    raise AssertionError(f"no node {node_id!r}")


# ── the kind exists (the silent-drop guard) ──


def test_the_knowledge_draft_kind_exists():
    """The atom's own blocker. Without this value `enqueue` returns SKIP and logs at debug,
    so routing would ship as a declared-but-inert path that drops every draft."""
    assert proposals.Kind.KNOWLEDGE_DRAFT.value == "knowledge_draft"
    assert proposals.Kind("knowledge_draft") is proposals.Kind.KNOWLEDGE_DRAFT


def test_an_unknown_kind_still_skips(home):
    """The closed enum still refuses a typo — the guard the new value must not have loosened."""
    verdict, prop = proposals.enqueue(
        kind="knowledge_drafts",  # plural typo
        title="T",
        body="B" * 60,
        provenance="human",
    )
    assert verdict is proposals.Verdict.SKIP
    assert prop is None


def test_the_new_kind_has_an_inbox_headline():
    """A kind with no label surfaces as the generic "Proposal", which is the one card a
    reviewer cannot triage without opening it."""
    from personalclaw.learning.proposals import _KIND_LABELS

    assert _KIND_LABELS[proposals.Kind.KNOWLEDGE_DRAFT.value] != "Proposal"


# ── a draft reaches the queue ──


def test_a_draft_reaches_enqueue_under_the_knowledge_draft_kind(home, ctx):
    result = run(
        KnowledgeProposeActionProvider().execute(
            {
                "drafts": [
                    {
                        "entity": "Retrieval cascade",
                        "title": "Retrieval cascade",
                        "body": "The store resolves a query FTS-first, then by embedding.",
                        "sufficient_evidence": True,
                    }
                ],
                "evidence": {"Retrieval cascade": ["[i1] FTS runs first."]},
                "source_cadence": "gap-healing",
            },
            ctx,
        )
    )
    assert result.success
    out = body(result)
    assert out["counts"]["filed"] == 1

    pending = proposals.list_pending()
    assert [p.kind for p in pending] == [proposals.Kind.KNOWLEDGE_DRAFT.value]
    filed = pending[0]
    assert filed.title == "Retrieval cascade"
    assert filed.source_cadence == "gap-healing"
    assert filed.run_id == "run-1"
    # The excerpt is what makes the proposal checkable; `enqueue` fences it on the way in.
    assert "FTS runs first" in filed.source_excerpt


def test_it_writes_nothing_into_the_knowledge_store(home, ctx):
    """The whole point of the atom: propose, don't write. The pre-WF2KNO-8 node persisted a
    TTL'd probe, so an assertion that the store stays untouched is the regression guard."""
    run(
        KnowledgeProposeActionProvider().execute(
            {
                "title": "Ephemeris",
                "body": "Something the store keeps referencing.",
                "occurrences": 5,
            },
            ctx,
        )
    )
    assert not list(home.glob("workspace/knowledge/*.db"))


def test_a_json_string_batch_is_accepted(home, ctx):
    """`{{nodes.draft.output.drafts | json}}` renders to a STRING. Requiring a live list would
    have made the batch form fail on the one call shape it exists for."""
    result = run(
        KnowledgeProposeActionProvider().execute(
            {
                "drafts": json.dumps(
                    [{"title": "Vector floor", "body": "Token similarity is the floor metric."}]
                ),
                "evidence": json.dumps({"Vector floor": ["[i2] tokens when no embedder."]}),
            },
            ctx,
        )
    )
    assert body(result)["counts"]["filed"] == 1
    assert "tokens when no embedder" in proposals.list_pending()[0].source_excerpt


def test_an_ungrounded_draft_is_not_filed(home, ctx):
    """The template's gate asks the model to admit thin evidence; honoring the admission is
    what makes asking worth anything."""
    result = run(
        KnowledgeProposeActionProvider().execute(
            {
                "drafts": [
                    {"title": "Thin", "body": "Not much here.", "sufficient_evidence": False},
                    {
                        "title": "Solid",
                        "body": "Grounded in excerpts.",
                        "sufficient_evidence": True,
                    },
                ]
            },
            ctx,
        )
    )
    out = body(result)
    assert out["counts"] == {"filed": 1, "skipped": 1, "considered": 2}
    assert [p.title for p in proposals.list_pending()] == ["Solid"]


def test_nothing_to_file_is_a_failure_not_a_silent_success(home, ctx):
    """An empty config is a template bug, not a quiet no-op — the distinction the SKIP case
    below deliberately does NOT get."""
    result = run(KnowledgeProposeActionProvider().execute({}, ctx))
    assert not result.success
    assert "nothing to file" in result.error


# ── SKIP is success ──


def test_a_prior_decision_skip_is_reported_as_success(home, ctx):
    """`enqueue`'s own docstring: a SKIP means a prior decision forbids it and nothing was
    written, and the caller should treat that as success — not nagging is the feature."""
    cfg = {"drafts": [{"title": "Retrieval cascade", "body": "The cascade, described at length."}]}
    first = run(KnowledgeProposeActionProvider().execute(cfg, ctx))
    assert body(first)["counts"]["filed"] == 1

    # Reject it, which records a cooling-down decision, then re-file the identical draft.
    proposals.reject(proposals.list_pending()[0].id)

    second = run(KnowledgeProposeActionProvider().execute(cfg, ctx))
    assert second.success, "a SKIP must not fail the node"
    assert not second.error
    out = body(second)
    assert out["counts"] == {"filed": 0, "skipped": 1, "considered": 1}
    assert out["skipped"][0]["verdict"] == proposals.Verdict.SKIP.value
    assert out["skipped"][0]["reason"]


def test_an_inferred_draft_below_the_evidence_floor_skips_successfully(home, ctx):
    """The floor applies to INFERRED drafts, which is what a gap-healing pass produces. One
    mention is not a phantom hub, and a node that failed on it would fail on every quiet run."""
    result = run(
        KnowledgeProposeActionProvider().execute(
            {"drafts": [{"title": "One-off", "body": "Mentioned exactly once.", "mentions": 1}]},
            ctx,
        )
    )
    assert result.success
    assert body(result)["counts"]["filed"] == 0
    assert proposals.list_pending() == []


# ── the wiring (all three registration points + the template) ──


def test_the_provider_is_registered_everywhere_it_must_be():
    """A provider in one set but not the other validates, saves, and then fails at run time."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )
    from personalclaw.triggers.screen import (
        READ_ONLY_PROVIDERS,
        WRITE_CAPABLE_PROVIDERS,
        provider_is_read_only,
    )
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    _ensure_default_providers_registered()
    assert get_action_provider("knowledge-propose") is not None
    assert "knowledge-propose" in ALLOWED_HOOK_PROVIDERS
    # Filing writes a durable row and raises an inbox item, so it needs the explicit opt-in.
    assert "knowledge-propose" in WRITE_CAPABLE_PROVIDERS
    assert "knowledge-propose" not in READ_ONLY_PROVIDERS
    assert not provider_is_read_only("knowledge-propose")


def test_gap_healing_files_through_the_proposal_queue():
    node = _node(_gap_healing(), "file")
    assert node["config"]["provider"] == "knowledge-propose"


def test_the_session_37_workaround_is_gone():
    """The workaround persisted a TTL'd probe tagged `proposal` INTO the store, so drafts never
    reached the review gate and expired unseen 30 days later."""
    spec = _gap_healing()
    node = _node(spec, "file")
    with_args = node["config"]["with"]
    assert "ttl" not in with_args
    assert with_args.get("kind") != "probe"
    assert "proposal" not in with_args.get("tags", [])
    # And no OTHER node in the template writes the store either.
    providers = {
        c.get("config", {}).get("provider")
        for c in spec["root"]["children"]
        if c["kind"] == "action"
    }
    assert "knowledge-persist" not in providers


def test_a_schema_edit_routes_through_the_same_kind(home, ctx):
    """§3.3's half of the atom. `schema_conventions` states that the document is never
    overwritten from the system's side and that schema-edit proposals route through the
    learning queue — this is the route that claim depends on, targeted at the file so a
    reviewer sees WHICH conventions document an accepted edit would change."""
    from personalclaw.knowledge.schema_conventions import SCHEMA_FILENAME

    result = run(
        KnowledgeProposeActionProvider().execute(
            {
                "title": "Convention: name entities as the store spells them",
                "body": "Three corrections in a row retitled an entry to the store's spelling.",
                "target": SCHEMA_FILENAME,
                "source_cadence": "schema-edit",
                "provenance": "human",  # a repeated user override IS the evidence
            },
            ctx,
        )
    )
    assert result.success
    filed = proposals.list_pending()[0]
    assert filed.kind == proposals.Kind.KNOWLEDGE_DRAFT.value
    assert filed.target == SCHEMA_FILENAME
    # The conventions file itself is untouched: proposing is not writing.
    assert not (home / "workspace/knowledge" / SCHEMA_FILENAME).exists()
