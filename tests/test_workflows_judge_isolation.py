"""cross_model judge isolation, ENFORCED at the gate (WF2LOO-11).

`isolation: cross_model` promises a judge on a different model FAMILY than the worker — a
same-family "independent" judge shares the blind spots it exists to catch. `judge_actors`
implemented the family check (`plan_judge_session` / `validate_judge_model`) but had no caller, so a
template asking for cross-model independence silently got fresh-session isolation only (S146). This
pins the wiring that closed that gap:

* a `cross_model` gate whose judge resolves to a DIFFERENT family runs it — and provably passes the
  validated model down to the completion call, so the judge runs on the model it was checked
  against, not a use-case guess;
* a `cross_model` gate that can only obtain a SAME-family (or undeterminable) judge FAILS CLOSED —
  it never silently downgrades to fresh, which is the exact defect this atom fixes.

The model resolution is injected (`judge_model_resolver`), so these need no live provider.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.engine import dispatch_gate
from personalclaw.workflows.models import FailureClass, InstanceState, Node

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _gate(cfg: dict) -> Node:
    return Node.from_dict({"kind": "gate", "id": "accept", "config": {"kind": "judge", **cfg}})


#: The contract object the judge gate asks for since WF2LOO-13. A bare "PASS" now reads as "the
#: judge could not answer in the required shape" — a PROTOCOL failure, not a pass.
_CONTRACT_PASS = '{"verdict": "PASS", "proof": "re-ran the verify command; exit 0"}'

# A worker on the "claude" family; the evidence is long enough to clear the pre-tier.
_WORKER = "Anthropic:claude-opus-5"
_EVIDENCE = "A substantial deliverable with more than enough characters for the pre-tier to allow."


async def test_a_different_family_judge_runs_and_is_pinned() -> None:
    """The judge resolves to a gpt-family model — a different family than the claude worker — so
    the gate runs it AND passes that concrete model down, proving the judge ran on the validated
    different-family model rather than a use-case default."""
    seen: dict[str, object] = {}

    async def completion(instruction, *, use_case=None, output_type=None, model=""):
        seen["model"] = model
        return _CONTRACT_PASS

    node = _gate({"prompt": "accept?", "evidence": _EVIDENCE, "isolation": "cross_model"})
    result = await dispatch_gate(
        node,
        BindingContext(),
        now=0.0,
        completion=completion,
        worker_model=_WORKER,
        judge_model_resolver=lambda _uc: "OpenAI:gpt-5",
    )
    assert result.state == InstanceState.DONE
    assert seen["model"] == "OpenAI:gpt-5", "the validated different-family model must be pinned"


async def test_a_same_family_judge_fails_closed() -> None:
    """The only judge on offer is the SAME family as the worker (both claude). cross_model cannot
    be satisfied, so the gate FAILS — the whole point is that it never quietly runs fresh."""
    calls = {"n": 0}

    async def completion(instruction, *, use_case=None, output_type=None, model=""):
        calls["n"] += 1
        return _CONTRACT_PASS

    node = _gate({"prompt": "accept?", "evidence": _EVIDENCE, "isolation": "cross_model"})
    result = await dispatch_gate(
        node,
        BindingContext(),
        now=0.0,
        completion=completion,
        worker_model=_WORKER,
        judge_model_resolver=lambda _uc: "Anthropic:claude-sonnet-5",
    )
    assert result.state == InstanceState.FAILED
    assert result.failure.failure_class == FailureClass.USER
    assert "cross_model" in result.failure.cause_plain
    assert calls["n"] == 0, "an unsatisfiable isolation guarantee must not call the model at all"


async def test_an_undeterminable_judge_family_fails_closed() -> None:
    """No judge model resolves (empty). Silence about the family is not evidence of difference, so
    the gate fails closed rather than certifying against an unknown."""

    async def completion(instruction, *, use_case=None, output_type=None, model=""):
        return _CONTRACT_PASS

    node = _gate({"prompt": "accept?", "evidence": _EVIDENCE, "isolation": "cross_model"})
    result = await dispatch_gate(
        node,
        BindingContext(),
        now=0.0,
        completion=completion,
        worker_model=_WORKER,
        judge_model_resolver=lambda _uc: "",
    )
    assert result.state == InstanceState.FAILED
    assert "cross_model" in result.failure.cause_plain


async def test_an_undeterminable_worker_family_fails_closed() -> None:
    """The worker's own family is unknown (no worker_model). "Different from the worker" cannot be
    proven against an unknown worker, so the gate fails closed BEFORE even resolving a judge —
    not knowing what to differ from is not proof of difference."""
    calls = {"n": 0}

    async def completion(instruction, *, use_case=None, output_type=None, model=""):
        calls["n"] += 1
        return _CONTRACT_PASS

    node = _gate({"prompt": "accept?", "evidence": _EVIDENCE, "isolation": "cross_model"})
    result = await dispatch_gate(
        node,
        BindingContext(),
        now=0.0,
        completion=completion,
        worker_model="",  # the worker family is undeterminable
        judge_model_resolver=lambda _uc: "OpenAI:gpt-5",
    )
    assert result.state == InstanceState.FAILED
    assert "cross_model" in result.failure.cause_plain
    assert calls["n"] == 0


async def test_a_fresh_isolation_gate_is_unchanged_and_pins_no_model() -> None:
    """The additive contract: a non-cross gate must call the completion seam EXACTLY as before —
    no `model` pin — so the loop library's existing completion fakes stay valid."""
    seen: dict[str, object] = {"model": "UNSET"}

    async def completion(instruction, *, use_case=None, output_type=None, model=""):
        seen["model"] = model
        return _CONTRACT_PASS

    node = _gate({"prompt": "accept?", "evidence": _EVIDENCE, "isolation": "fresh"})
    result = await dispatch_gate(
        node,
        BindingContext(),
        now=0.0,
        completion=completion,
        worker_model=_WORKER,
        # Would offer a same-family judge; a fresh gate must never consult it.
        judge_model_resolver=lambda _uc: "Anthropic:claude-sonnet-5",
    )
    assert result.state == InstanceState.DONE
    assert seen["model"] == "", "a non-cross_model gate must not pin a model"
