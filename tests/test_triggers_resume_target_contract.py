"""WF2AUT-14 ratification ratchet: the resume-target contract SHAPE is pinned.

See ``docs/roadmap/design-notes/wf2aut14-resume-target-contract.md``. Behavioural pins (a
real run driven to a real gate and resumed, the missing-target dispositions, idempotence)
live in ``test_triggers_resume_target.py``; this file is the cement the atom adds — it pins
the *shape* of the contract WF2LOO-9 and every future consumer read against:

* the public authored key,
* the exact authored field set (``node_id`` deliberately absent — descoped),
* the exact normalized-output key set.

A change to any of these is a change to the contract, so it must be a deliberate edit to a
RED test (and to the design note), never a silent drift.
"""

from __future__ import annotations

from types import SimpleNamespace

from personalclaw.triggers.models import _RESUME_TARGET_FIELDS, parse_trigger
from personalclaw.triggers.wakeup import RESUME_TARGET_KEY, resume_target_of

#: The ratified authored field set. ``node_id`` from the original R11 scope is NOT here.
_RATIFIED_AUTHORED_FIELDS = ("run_id", "project_id", "resume_token", "answer")

#: The exact keys ``resume_target_of`` normalizes a populated target down to.
_RATIFIED_NORMALIZED_KEYS = {
    "run_id",
    "project_id",
    "resume_token",
    "gate_answer",
    "answers_gate",
}


def test_the_resume_key_is_the_public_contract_key() -> None:
    assert RESUME_TARGET_KEY == "resume"


def test_the_authored_field_set_is_ratified_and_frozen() -> None:
    """A change here is a change to the contract every consumer reads — make it deliberately
    and update the ratification design note in the same change."""
    assert _RESUME_TARGET_FIELDS == _RATIFIED_AUTHORED_FIELDS


def test_node_id_is_descoped_not_silently_accepted() -> None:
    """The one original-scope field that did NOT ship. A resume block carrying ``node_id``
    must raise an unknown-field issue at save time — the descope is enforced, not just
    documented."""
    assert "node_id" not in _RESUME_TARGET_FIELDS
    _, issues = parse_trigger(
        {
            "id": "j1",
            "name": "j",
            "kind": "clock",
            "spec": {"kind": "interval", "interval_secs": 3600},
            "workflow": {"resume": {"run_id": "r", "node_id": "n"}},
        }
    )
    assert any(i.path == f"workflow.{RESUME_TARGET_KEY}.node_id" for i in issues)


def test_the_normalized_shape_is_ratified() -> None:
    """``resume_target_of`` returns exactly these keys for a fully-populated target."""
    target = resume_target_of(
        SimpleNamespace(
            workflow={
                RESUME_TARGET_KEY: {
                    "run_id": "r-7",
                    "project_id": "p1",
                    "resume_token": "tok",
                    "answer": True,
                }
            }
        )
    )
    assert set(target) == _RATIFIED_NORMALIZED_KEYS


def test_a_target_that_names_no_run_normalizes_to_empty() -> None:
    """The one fail-open direction: no ``run_id`` means no target, so the trigger falls back
    to an ordinary new-run wake rather than being silently disabled."""
    assert resume_target_of(SimpleNamespace(workflow={RESUME_TARGET_KEY: {}})) == {}
