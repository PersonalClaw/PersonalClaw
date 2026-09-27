"""A revise closes the ask it answered, with its own outcome (ledger 292).

`revise{step_ref, comment}` is the third answer to a waiting gate: "change that step, then ask me
again". On `main` it consumed the ask's token and re-asked, and wrote no resolution at all for the
ask it answered — so that ask's `confirmation_pending` stayed open for good, its escalation bet was
graded as if nobody had answered, and the per-gate scoring could not see that a person had engaged.

What these pin, against the real controller and the real outcome resolver:

* the revise closes its ask: `confirmation_resolved` with the verb `revised`, citing the id that
  ask was minted with, and `answered: true` — a person did answer it;
* the re-ask is a new question with a new id, so no answer to one can close the other;
* scoring counts a revise apart from yes and no: the gate's said-no table reports it as `revised`,
  never as a pass or a reject, and the escalation bet for the revised ask is graded as the answer
  `revised` with no number — neither the yes it bet on nor the no that would score it −1.
"""

from __future__ import annotations

import pytest

from personalclaw.learning import outcome_resolver
from personalclaw.learning import proposals as P
from personalclaw.ledger import outcomes
from personalclaw.memory_service import MemoryService
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import introspection
from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """The workflow store, the inbox and the proposal store under one tmp home. The resolver scans
    runs through `store`, which binds `config_dir` at import — hence the env var as well."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    return home


def _spec() -> dict:
    """A prompt-bearing step and the gate that reviews it — the shape a revise is for."""
    return {
        "name": "reviewed",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "draft", "config": {"expr": {"v": 1}, "prompt": "w"}},
                {
                    "kind": "gate",
                    "id": "approve",
                    "config": {"kind": "approval", "prompt": "Ship it?", "timeout_secs": 0},
                },
            ],
        },
    }


async def _parked() -> RunController:
    spec = _spec()
    run = store.create(WorkflowRun(id="", workflow_name="reviewed", mode="background"))
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices())
    await c.run_to_completion(timeout=20)
    assert c.run.status == RunStatus.NEEDS_INPUT
    return c


def _ask(c: RunController) -> HI.Continuation:
    pending = HI.list_continuations(c.run.id)
    assert len(pending) == 1, [p.node_id for p in pending]
    return pending[0]


def _confirmations(c: RunController, kind: str) -> list[dict]:
    return [e for e in J.ledger(c.run.id) if e.get("kind") == kind]


async def _revised_then_asked_again(c: RunController) -> tuple[HI.Continuation, HI.Continuation]:
    first = _ask(c)
    result = c.resume(first.token, {"revise": {"step_ref": "draft", "comment": "be terser"}})
    assert result["ok"] and result["revised"], result
    await c.run_to_completion(timeout=20)
    second = _ask(c)
    return first, second


async def test_a_revise_closes_the_ask_it_answered() -> None:
    c = await _parked()
    first, _second = await _revised_then_asked_again(c)

    resolved = _confirmations(c, J.CONFIRMATION_RESOLVED)
    # The defect first: on `main` the revised ask's pending half was never closed.
    assert resolved, "the revised ask was left open"
    closing = resolved[0]
    assert closing["confirmation_id"] == first.confirmation_id
    assert closing["verb"] == "revised"
    assert closing["answered"] is True
    assert closing["approved"] is False


async def test_the_re_ask_is_a_new_question_with_its_own_id() -> None:
    c = await _parked()
    first, second = await _revised_then_asked_again(c)
    assert second.confirmation_id and second.confirmation_id != first.confirmation_id
    pending = [e["confirmation_id"] for e in _confirmations(c, J.CONFIRMATION_PENDING)]
    assert pending == [first.confirmation_id, second.confirmation_id]

    # Answering the re-ask closes the re-ask, and nothing else.
    assert c.resume(second.token, True)["ok"]
    await c.run_to_completion(timeout=20)
    closes = [(e["confirmation_id"], e["verb"]) for e in _confirmations(c, J.CONFIRMATION_RESOLVED)]
    assert closes == [(first.confirmation_id, "revised"), (second.confirmation_id, "approve")]


async def test_the_gates_scoring_counts_a_revise_apart_from_yes_and_no() -> None:
    c = await _parked()
    _first, second = await _revised_then_asked_again(c)
    c.resume(second.token, True)
    await c.run_to_completion(timeout=20)

    stats = introspection.gate_stats(J.ledger(c.run.id))["approve"]
    assert (stats.passes, stats.rejects, stats.revised) == (1, 0, 1)
    assert stats.total == 1, "a revise is not a pass or a reject, so it is not in the pass rate"
    assert stats.to_dict()["revised"] == 1


async def test_the_escalation_bet_grades_a_revise_as_its_own_answer() -> None:
    """Each ask opened a bet that interrupting the person would get a yes. The revised ask's bet is
    graded as the answer `revised`, with no number: on the scale that bet is measured on, a yes is
    1.0 and a no is 0.0 — which would score this −1, as if the person had refused the work."""
    c = await _parked()
    first, second = await _revised_then_asked_again(c)
    c.resume(second.token, True)
    await c.run_to_completion(timeout=20)

    events = J.ledger(c.run.id)
    questions = {
        q.record.get("confirmation_id"): q
        for q in outcomes.open_questions(events)
        if q.producer == outcomes.PRODUCER_ESCALATION
    }
    on_first, on_second = questions[first.confirmation_id], questions[second.confirmation_id]
    assert outcomes.measure_from_events(on_first, events) is None, "a revise scored on the scale"
    assert outcomes.answer_from_events(on_first, events) == "revised"
    assert outcomes.measure_from_events(on_second, events) == 1.0
    assert outcomes.answer_from_events(on_second, events) == "approve"

    # The resolver, past the horizon: the revise is graded measured — somebody answered — and
    # counted apart from the scored answers and from the unanswered ones.
    opened = outcome_resolver._epoch(on_first.ts)
    assert opened is not None
    report = outcome_resolver.resolve(
        MemoryService.over_vector_store(None), now=opened + 2 * 24 * 3600.0
    )
    assert report == {"resolved": 1, "unscored": 1, "inconclusive": 0, "pending": 0, "proposed": 0}
    graded = {
        e["pending_event_id"]: e for e in J.ledger(c.run.id) if e.get("kind") == J.OUTCOME_RESOLVED
    }
    revised = graded[on_first.event_id]
    assert revised["resolution"] == outcomes.MEASURED
    assert revised["answer"] == "revised"
    assert revised["measured"] is None
    approved = graded[on_second.event_id]
    assert (approved["answer"], approved["measured"]) == ("approve", 1.0)
