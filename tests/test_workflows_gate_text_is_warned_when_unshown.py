"""Validation warns an author about words on a gate its kind never shows.

A gate's words reach someone only through the keys the engine reads for its kind: an approval or
an event asks its `prompt` (else its `message`), a judge sends its `prompt` to its model, and an
expression gate's `message` is what its failure says. An expression gate asks nobody, and a
verifier or a ladder reads no words at all. #3774 found two bundled templates whose expression gate
carried an approval's question, and pinned the bundled library with a census. A workflow a person
or a chat agent writes had no such check: the words were saved, and never reached anyone. The
validator now says so, as a warning — the spec still runs.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows.validator import SEVERITY_WARNING, validate_spec

CODE = "WF_GATE_TEXT_UNSHOWN"


def _spec(gate: dict) -> dict:
    return {
        "name": "gate-words",
        "root": {
            "kind": "sequence",
            "children": [
                {"kind": "infer", "id": "draft", "config": {"prompt": "Write it."}},
                {"kind": "gate", "id": "check", "config": gate},
            ],
        },
    }


def _warned(gate: dict) -> list[dict]:
    result = validate_spec(_spec(gate))
    return [i.to_dict() for i in result.issues if i.code == CODE]


@pytest.mark.parametrize(
    ("gate", "unshown", "said"),
    [
        (
            {"kind": "expression", "expr": "{{nodes.draft.output}}", "prompt": "Approve it?"},
            "prompt",
            "asks nobody",
        ),
        (
            {"kind": "judge", "prompt": "Is it accurate?", "message": "Check the facts."},
            "message",
            "judge's model is sent its `prompt`",
        ),
        (
            {"kind": "verify_command", "verify": {"command": "true"}, "prompt": "Did it pass?"},
            "prompt",
            "reads no words",
        ),
        (
            {"kind": "ladder", "criteria": ["compiles"], "message": "Climb it."},
            "message",
            "reads no words",
        ),
        (
            {"kind": "approval", "prompt": "Publish it?", "message": "It goes live."},
            "message",
            "only when it has no `prompt`",
        ),
    ],
)
def test_words_a_gate_kind_never_shows_are_warned(gate: dict, unshown: str, said: str) -> None:
    """🔴 Red on main: nothing was reported, and the words were saved to reach nobody."""
    warned = _warned(gate)
    assert len(warned) == 1, warned
    (issue,) = warned
    assert issue["severity"] == SEVERITY_WARNING
    assert issue["path"].endswith("children[1]"), issue
    assert f"`{unshown}`" in issue["message"] and said in issue["message"], issue["message"]


def test_it_is_a_warning_the_spec_still_saves() -> None:
    result = validate_spec(_spec({"kind": "expression", "expr": "true", "prompt": "Approve it?"}))
    assert result.ok, [i.to_dict() for i in result.errors]


@pytest.mark.parametrize(
    "gate",
    [
        {"kind": "approval", "prompt": "Publish it?"},
        {"kind": "approval", "message": "Publish it?"},
        {"kind": "event", "message": "Waiting for the next check."},
        {"kind": "judge", "prompt": "Is it accurate?"},
        {"kind": "expression", "expr": "true", "message": "The draft is empty."},
        {"kind": "verify_command", "verify": {"command": "true"}},
    ],
)
def test_words_the_kind_shows_are_not_warned(gate: dict) -> None:
    """CONTROL: the warning is about unshown words, not about words."""
    result = validate_spec(_spec(gate))
    assert result.ok, [i.to_dict() for i in result.errors]  # judged, not refused unparsed
    assert _warned(gate) == []


def test_every_gate_kind_says_which_words_it_reads() -> None:
    """The table the warning reads covers every kind, so a new kind cannot go unjudged."""
    from personalclaw.workflows.models import GateKind
    from personalclaw.workflows.validator import GATE_TEXT_KEYS, GATE_TEXT_READ

    assert set(GATE_TEXT_READ) == set(GateKind)
    for kind, keys in GATE_TEXT_READ.items():
        assert keys <= set(GATE_TEXT_KEYS), (kind, keys)
