"""Every step kind and every gate kind says where its config goes.

A ``{{secret:NAME}}`` is filled with the value only where a step uses its config itself. The text
a model reads keeps the reference: a stage's, an infer's or a visualize step's config, and a judge
gate's ``prompt`` and ``evidence``. A step that starts a run hands it the reference. An action's
kind is its provider's (its own rail says where each provider's config goes). So every kind is
named here exactly once, and a new one is a choice someone makes.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import engine_support, node_bindings
from personalclaw.workflows.models import CONTAINER_KINDS, GateKind, Node, NodeKind
from personalclaw.workflows.tick import ReadyNode

pytestmark = pytest.mark.security

#: Step kinds whose config is the text of a model call.
MODEL_TEXT = frozenset({NodeKind.STAGE, NodeKind.INFER, NodeKind.VISUALIZE})
#: Step kinds that start a run with their config.
HANDS_A_RUN = frozenset({NodeKind.SUBWORKFLOW})
#: Step kinds whose config goes where what they name says: an action's provider, a gate's kind.
BY_WHAT_THEY_NAME = frozenset({NodeKind.ACTION, NodeKind.GATE})
#: Step kinds that use their config themselves, with what they do with it.
USES_ITS_CONFIG = {
    NodeKind.TRANSFORM: "reshapes data into its output",
    NodeKind.WAIT: "waits for the time or the event it names",
}

#: Gate kinds whose config a model reads, with the keys it reads.
GATE_MODEL_TEXT = {GateKind.JUDGE: frozenset({"prompt", "evidence"})}
#: Every other gate kind, with what it does with its config.
GATE_USES_ITS_CONFIG = {
    GateKind.APPROVAL: "asks the owner",
    GateKind.EVENT: "waits for the event it names",
    GateKind.EXPRESSION: "evaluates its condition",
    GateKind.LADDER: "runs each criterion's check",
    GateKind.VERIFY_COMMAND: "runs its check",
    GateKind.VERIFY_SCRIPT: "runs its check",
}


def test_every_step_kind_says_where_its_config_goes():
    named = [
        MODEL_TEXT,
        HANDS_A_RUN,
        BY_WHAT_THEY_NAME,
        frozenset(USES_ITS_CONFIG),
        CONTAINER_KINDS,
    ]

    assert frozenset().union(*named) == frozenset(NodeKind)
    assert sum(len(kinds) for kinds in named) == len(NodeKind), "each kind is named once"
    assert node_bindings._MODEL_FACING_KINDS == MODEL_TEXT


def test_every_gate_kind_says_where_its_config_goes():
    assert set(GATE_MODEL_TEXT) | set(GATE_USES_ITS_CONFIG) == set(GateKind)
    assert not set(GATE_MODEL_TEXT) & set(GATE_USES_ITS_CONFIG)
    assert all(reason.strip() for reason in GATE_USES_ITS_CONFIG.values())


def _model_text(kind: str) -> frozenset[str]:
    item = ReadyNode(
        path="root.children[0]",
        node=Node(kind=NodeKind.GATE, id="g", config={"kind": kind}),
        lane="",
    )
    return node_bindings._gate_model_text(None, item, {})  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", sorted(GateKind, key=lambda k: k.value))
def test_a_gate_keeps_the_references_in_the_text_its_model_reads(kind):
    """🔴 Red before: a judge's prompt and evidence were in no model-facing table."""
    assert _model_text(kind.value) == GATE_MODEL_TEXT.get(kind, frozenset())


def test_a_gate_whose_kind_is_unknown_keeps_them_too():
    """What reads an unknown kind's text can't be known."""
    assert _model_text("judgment") == engine_support.JUDGE_TEXT == GATE_MODEL_TEXT[GateKind.JUDGE]
