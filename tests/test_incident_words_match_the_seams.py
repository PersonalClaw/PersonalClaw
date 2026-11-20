"""What incident mode says it does with the work it stops, seam by seam, is what each seam does.

🔴 The defect (Backlog: "Incident mode says nothing is lost, but hook and event fires during it are
dropped"). Incident mode's contract said "nothing is lost by it: the skipped work is still due when
the switch is turned off". That held at some seams only:

- a lifecycle hook's action, a webhook and an event automation that fired meanwhile were dropped,
  and the hook's history row said "automated actions resume when it clears", as if that fire
  would still run;
- a scheduled automation whose time came was refused, and the tick had already moved it to its
  next time, so a daily automation ran the next day, not at Resume;
- Resume's own docstring promised a Runs surface showing "suppressed during incident" that no page
  has.

Held work (idle automations, loop cycles, a running loop or workflow run, file and page watches,
Inbox sorting, memory consolidation and background compression) is still done afterwards; the rest
is not. The words now say which is which: the contract in `guardrails/incident.py`, the hook's
history row, and the Settings hint (`GuardrailsPanel.test`'s own case). Each seam's row here reads
its words, and the clock seam's rows tick a real store, since that is the one whose fate the
words must not get wrong again.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from personalclaw.guardrails import incident
from personalclaw.triggers import history
from personalclaw.triggers import service as svc
from personalclaw.triggers.models import Outcome, Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0
#: Due a minute before NOW: past it, but not so late that the slot is missed and reviewed.
DUE = "2027-01-15T07:59:00+00:00"
EVERY = 3600


@pytest.fixture(autouse=True)
def _isolate_incident(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The real flag file under tmp_path, and the process mirror reset both ways."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    incident.reset_incident_mirror()
    yield
    incident.reset_incident_mirror()


def _contract() -> str:
    return " ".join((incident.__doc__ or "").split())


#: Each seam incident mode stops, what becomes of the work it stops there, and the words the
#: contract says it in.
SEAMS = [
    ("recurring automation", "runs at its next time"),
    ("one-shot", "switched off and kept, to run by hand"),
    ("lifecycle hook", "dropped"),
    ("webhook", "dropped"),
    ("event automation", "dropped"),
    ("background agent", "refused"),
    ("idle automations and loop cycles", "run on the first check after Resume"),
    ("running loop or workflow run", "held where it is"),
    ("file and page watches", "catch up after Resume"),
    ("Inbox sorting, memory consolidation and background compression", "run after Resume"),
]


def test_the_contract_no_longer_says_nothing_is_lost() -> None:
    """🔴 Before: "nothing is lost by it: the skipped work is still due when the switch is turned
    off"."""
    contract = _contract()
    assert "nothing is lost" not in contract
    assert "still due when the switch is turned off" not in contract


@pytest.mark.parametrize(("seam", "fate"), SEAMS, ids=[seam for seam, _ in SEAMS])
def test_the_contract_says_what_becomes_of_the_work_at_each_seam(seam: str, fate: str) -> None:
    """🔴 Before: no seam's own fate was named, only the one that was not true of all of them."""
    lines = [line.lower() for line in _contract().split(" - ") if seam.lower() in line.lower()]
    assert lines, f"the contract names no line for the {seam}"
    assert any(fate.lower() in line for line in lines), (seam, fate, lines)


def test_resume_promises_no_page_that_does_not_exist() -> None:
    """🔴 Before: "so the Runs surface can show 'suppressed during incident'": no page does."""
    said = " ".join((inspect.getdoc(incident.resume) or "").split())
    assert "Runs surface" not in said
    assert "suppressed during incident" not in said


def test_a_hook_fire_during_an_incident_says_it_is_not_run_later() -> None:
    """🔴 Before: "automated actions resume when it clears", read as this fire still to come. The
    row is inert (`SKIPPED_GATE`): nothing ran, and nothing will."""
    outcome = history.HOOK_STATUS_TO_OUTCOME["skipped_incident"]
    assert outcome == Outcome.SKIPPED_GATE.value
    said = history._hook_reason("skipped_incident", outcome)
    assert "resume" not in said, said
    assert "not run later" in said, said
    assert "incident mode" in said, said


def test_a_refused_fire_says_it_is_dropped_not_suspended() -> None:
    """🔴 Before: "unattended fires are suspended", read as a fire still to come. The gate's refusal
    (every automation fire: clock, event, webhook) and a run by hand's say it is not run later, and
    still say how to turn the switch off."""
    from personalclaw.triggers import tools
    from personalclaw.triggers.firepath import FireContext, evaluate

    incident.activate(reason="test")
    gate = asyncio.run(
        evaluate(FireContext(trigger_id="clock:x", moment=datetime(2027, 1, 15, 12)))
    )
    for said in (gate.reason, tools.manual_refusal()):
        assert "suspended" not in said, said
        assert "not run later" in said, said
        assert "personalclaw incident off" in said, said
    assert "a recurring automation runs at its next time" in gate.reason
    assert "a one-shot is kept, switched off, to run by hand" in gate.reason


# ── the clock seam, on a real tick ───────────────────────────────────────────────────────────────


def _store(tmp_path: Path, spec: dict, tid: str = "clock:nightly") -> TriggerStore:
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=tid,
            name="Nightly digest",
            kind="clock",
            enabled=True,
            spec=spec,
            next_fire_at=DUE,
            capabilities={"providers": ["run-prompt"]},
            workflow={"inline": {"provider": "run-prompt", "config": {}}},
        )
    )
    return store


def _tick(store: TriggerStore, tmp_path: Path, now: float = NOW) -> svc.TickResult:
    return asyncio.run(svc.tick(store, now=now, base_dir=tmp_path, persist=True))


def test_a_scheduled_fire_during_an_incident_runs_at_its_next_time_not_at_resume(
    tmp_path: Path,
) -> None:
    """The fate the words give it: refused, and moved to its next time by the same tick, so
    Resume does not run it."""
    store = _store(tmp_path, {"kind": "interval", "interval_secs": EVERY})
    incident.activate(reason="test")
    refused = _tick(store, tmp_path)
    assert refused.fires == []
    row = next(r for r in refused.ledger_rows if r["trigger_id"] == "clock:nightly")
    assert row["outcome"] == Outcome.REFUSED.value

    after = store.get("clock:nightly").trigger
    assert after.enabled
    next_at = datetime.fromisoformat(after.next_fire_at).timestamp()
    assert next_at > NOW, "the refused slot is still due"

    incident.resume()
    assert _tick(store, tmp_path).fires == [], "Resume ran the slot the incident refused"
    assert [f.trigger.id for f in _tick(store, tmp_path, now=next_at + 1).fires] == [
        "clock:nightly"
    ], "it runs at its next time"


def test_a_one_shot_whose_time_passes_during_an_incident_is_switched_off_and_kept(
    tmp_path: Path,
) -> None:
    """Its only slot is taken: it is switched off, and its row stays, to be run by hand."""
    store = _store(tmp_path, {"kind": "at", "at": NOW - 60}, tid="clock:pack-the-bag")
    incident.activate(reason="test")
    assert _tick(store, tmp_path).fires == []
    kept = store.get("clock:pack-the-bag")
    assert kept is not None, "the one-shot is gone"
    assert kept.trigger.enabled is False
    incident.resume()
    assert _tick(store, tmp_path).fires == [], "Resume ran the one-shot on its own"
