"""The global kill switch, on the unified trigger path (decision 7 — S117).

🔴 THE DEFECT. `personalclaw incident on` did NOT stop a clock trigger. The CLI calls it
"Suspend/resume all unattended work", `guardrails/incident.py` is SEL-audited, and three other
subsystems honour it (hooks, subagent spawns, the legacy `event_triggers` fire path). The unified
engine — the SOLE path that fires clock triggers since S100 — never read the flag. Driven before a
line was written:

    incident active: True
    tick() -> fires: ['clock:nightly']    outcome=ran

So the one control an operator reaches for *during* an incident was the one thing that kept running
unattended work, while reporting itself active. Worse than a missing feature: a switch that lies.

🔴 THE SECOND DEFECT, found while wiring the first. `tools.run`'s `manual_gate_plan` PRINTED
"gates enforced: incident, screen, budget, claim, yield, capability" and enforced **none** of them —
it was a description with no enforcement anywhere. Measured with the switch thrown: `ok: True`, the
runner invoked. A plan that describes a control nobody applies is worse than no plan, because it
tells the user the boundary held.

Every test drives the REAL `incident` store under an isolated home.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.guardrails import incident
from personalclaw.triggers import service as svc
from personalclaw.triggers import tools as T
from personalclaw.triggers.firepath import GATE_ORDER, FireContext, evaluate
from personalclaw.triggers.models import Outcome, Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _isolate_incident(tmp_path, monkeypatch):
    """A real flag file, under tmp_path — never the user's home.

    `incident.activate()` writes to `config_dir()`, so this patches the loader the module actually
    reads and resets the process-global mirror both ways (see the test-isolation-hazards memory:
    a cached mirror leaking between tests is exactly how a kill-switch test poisons its neighbours).
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    incident.reset_incident_mirror()
    yield
    incident.reset_incident_mirror()


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _due(store, tid="clock:nightly", provider="run-prompt"):
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            next_fire_at="2027-01-15T07:00:00+00:00",
            capabilities={"providers": [provider]},
            workflow={"inline": {"provider": provider, "config": {}}},
        )
    )


def _tick(store, tmp_path):
    return asyncio.run(svc.tick(store, now=NOW, base_dir=tmp_path, persist=False))


# ── the gate itself ──


def test_the_kill_switch_is_the_FIRST_gate():
    """Ahead of the injection screen, because an incident halts everything unconditionally.

    A gate ordered after `screen` would make "is this payload clean" a precondition for honouring a
    kill switch — the switch would depend on the content of the thing it is meant to stop.
    """
    assert GATE_ORDER[0] == "incident"


def test_the_gate_vocabulary_covers_the_new_gate():
    """§7 criterion 8's zero-silent-drops rule is enforced structurally: a gate with no outcome
    raises `KeyError` mid-fire, which loses the fire instead of refusing it."""
    from personalclaw.triggers.firepath import gate_order_is_intact

    assert gate_order_is_intact() == []


def test_an_incident_refuses_the_walk():
    incident.activate(reason="test")
    decision = asyncio.run(evaluate(FireContext(trigger_id="clock:x")))
    assert decision.allowed is False
    assert decision.gate == "incident"


def test_the_refusal_is_typed_REFUSED_not_a_skip():
    """A policy refusal, not a cadence skip. `skipped_gate` would file the kill switch alongside
    quiet hours in the runs inbox — a user filtering for "why did nothing run" needs to tell "the
    operator suspended everything" from "it was 3am"."""
    incident.activate(reason="test")
    decision = asyncio.run(evaluate(FireContext(trigger_id="clock:x")))
    assert decision.outcome == Outcome.REFUSED.value


def test_the_reason_says_HOW_TO_RESUME():
    """An operator who finds a refused fire must not have to grep for the command."""
    incident.activate(reason="test")
    decision = asyncio.run(evaluate(FireContext(trigger_id="clock:x")))
    assert "incident mode" in decision.reason
    assert "personalclaw incident off" in decision.reason


def test_no_incident_passes_the_gate():
    decision = asyncio.run(evaluate(FireContext(trigger_id="clock:x")))
    assert "incident" in decision.passed


def test_resuming_lets_fires_through_again():
    """A kill switch that could not be released would be a self-inflicted outage."""
    incident.activate(reason="test")
    assert asyncio.run(evaluate(FireContext(trigger_id="clock:x"))).allowed is False
    incident.resume()
    assert asyncio.run(evaluate(FireContext(trigger_id="clock:x"))).allowed is True


# ── driven through a real tick ──


def test_a_due_clock_trigger_does_NOT_fire_during_an_incident(store, tmp_path):
    """🔴 THE DEFECT, pinned end to end. This exact assertion failed before this session."""
    _due(store)
    incident.activate(reason="test")
    result = _tick(store, tmp_path)
    assert result.fires == []


def test_the_same_trigger_fires_once_the_incident_is_over(store, tmp_path):
    """The other half — proving the refusal is the switch and not a broken fixture."""
    _due(store)
    incident.activate(reason="test")
    assert _tick(store, tmp_path).fires == []
    incident.resume()
    _due(store)
    assert [f.trigger.id for f in _tick(store, tmp_path).fires] == ["clock:nightly"]


def test_a_refused_fire_still_writes_a_LEDGER_ROW(store, tmp_path):
    """§7 criterion 8: zero silent drops. An operator must be able to see that the switch is what
    stopped the work — a suspended automation with no row is indistinguishable from a broken one."""
    _due(store)
    incident.activate(reason="test")
    result = _tick(store, tmp_path)
    row = next(r for r in result.ledger_rows if r["trigger_id"] == "clock:nightly")
    assert row["outcome"] == Outcome.REFUSED.value
    assert "incident mode" in row["reason"]


def test_EVERY_due_trigger_is_refused_not_just_the_first(store, tmp_path):
    """A global switch is global. One row refused while another fires would be the worst outcome:
    an operator would believe automation was suspended when it was not."""
    for i in range(3):
        _due(store, tid=f"clock:t{i}")
    incident.activate(reason="test")
    result = _tick(store, tmp_path)
    assert result.fires == []
    assert len(result.ledger_rows) == 3
    assert {r["outcome"] for r in result.ledger_rows} == {Outcome.REFUSED.value}


# ── the manual paths ──


def test_the_manual_bypass_set_NEVER_includes_the_kill_switch():
    """Declared as data so the intent survives a refactor. The legacy path already recorded the
    reasoning: a `/test` that ignored incident mode would run unattended work during the incident
    the switch was thrown for."""
    assert "incident" in T.MANUAL_NEVER_BYPASSES
    assert "incident" not in T.MANUAL_BYPASSES


def test_a_MANUAL_run_is_refused_during_an_incident(store, tmp_path):
    """🔴 THE SECOND DEFECT. `manual_gate_plan` listed `incident` under "gates enforced" while
    `run()` enforced nothing — measured: `ok: True` and the runner invoked."""
    _due(store, tid="clock:x")
    incident.activate(reason="test")
    calls = []
    result = T.run(store, trigger_id="clock:x", runner=lambda p: calls.append(p) or "LAUNCHED")
    assert result.ok is False
    assert calls == [], "the runner must not be reached"
    assert "incident mode" in result.text


def test_a_manual_run_works_normally_when_there_is_no_incident(store, tmp_path):
    _due(store, tid="clock:x")
    calls = []
    result = T.run(store, trigger_id="clock:x", runner=lambda p: calls.append(p) or "LAUNCHED")
    assert result.ok is True
    assert len(calls) == 1


def test_a_DRY_RUN_still_reports_during_an_incident(store, tmp_path):
    """Deliberate: a dry run executes nothing, so it is a READ. Telling an operator what *would*
    happen is the opposite of running unattended work, and refusing it would remove the one way to
    inspect an automation while the system is suspended."""
    _due(store, tid="clock:x")
    incident.activate(reason="test")
    result = T.run(store, trigger_id="clock:x", dry_run=True)
    assert result.ok is True
    assert result.data["plan"]["executes"] is False


def test_the_plan_the_tool_REPORTS_matches_what_it_ENFORCES():
    """The invariant the inert plan violated: every gate named "enforced" must have an enforcement
    point. `incident` is the one this session wired; the rest are enforced where their inputs exist
    (documented on `manual_refusal`), so this asserts the specific claim that was false."""
    plan = T.manual_gate_plan()
    assert "incident" in plan["enforced"]
    incident.activate(reason="test")
    assert T.manual_refusal() != "", "a gate reported as enforced must actually refuse"


def test_manual_refusal_is_SILENT_with_no_incident():
    assert T.manual_refusal() == ""


# ── the fail-open contract this gate inherits ──


def test_an_UNREADABLE_flag_does_not_halt_all_automation(store, tmp_path, monkeypatch):
    """🔴 Deliberately fail-OPEN, and the one place in this file where "open" is correct.

    `guardrails/incident.py` says it outright: an unreadable flag must not halt every automation on
    a filesystem hiccup. That is the opposite of the fence's deny-by-default, because the failure
    modes are opposite — a stuck-open capability fence grants power that was never asked for, while
    a stuck-closed kill switch silently stops every automation the user depends on and looks exactly
    like the scheduler being broken. This gate inherits that contract, it does not second-guess it.
    """
    monkeypatch.setattr(
        "personalclaw.guardrails.incident._read_file",
        lambda: (_ for _ in ()).throw(OSError("unreadable")),
    )
    incident.reset_incident_mirror()
    _due(store)
    result = _tick(store, tmp_path)
    assert [f.trigger.id for f in result.fires] == ["clock:nightly"]
