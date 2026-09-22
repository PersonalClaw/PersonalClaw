"""#1783 clause 2 — the `glossary` slot's producer, asserted at its CALL SITES.

The defect this closes is a complete read side with no writer: `glossary` is a built-in
slot, injected on every turn by `memory_slots`, editable in Settings → Memory → Slots —
and nothing in the product ever wrote a line into it. A user could only fill it by hand.

So these tests deliberately do NOT exercise `detect_glossary_definition` or
`capture_glossary_term` in isolation. A helper with no caller passes a helper test, which
is exactly how this shipped inert: `capture_slot_lines` was already correct and already
tested. What had to be proved is that a real turn reaches it, through both callers:

* :func:`after_turn_review.run_after_turn_review` — the review pass itself, and
* :func:`dashboard.chat_runner._maybe_after_turn_review` — the gateway hook that every
  dashboard chat turn actually goes through.

Each is paired with a vacuity control, because "the slot has a line" is only evidence of
a working writer if an ordinary turn leaves it empty.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw import after_turn_review as atr
from personalclaw.dashboard.chat_runner import _maybe_after_turn_review
from personalclaw.learning.gate import Cadence, GateDecision, GateReason
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def svc():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return MemoryService.over_vector_store(store)


def _glossary_lines(service) -> list[str]:
    """Live `glossary` lines, read back through the SAME service API the editor uses.

    `lines` carries tombstoned entries too — the slot is append-only, so a retirement is a
    flag rather than a removal. `live_count` is cross-checked against the filter because
    getting that flag's name wrong yields a helper that silently counts retired lines as
    live, which passes every test except the resurrection one.
    """
    for slot in service.slots():
        if slot["name"] == "glossary":
            live = [ln["text"] for ln in slot["lines"] if not ln["tombstoned"]]
            assert len(live) == slot["live_count"]
            return live
    raise AssertionError("no `glossary` slot — the built-in list changed")


# ── call site 1: run_after_turn_review ───────────────────────────────────────


def test_review_pass_writes_the_defined_term_into_the_glossary_slot(svc):
    """The whole point: a definitional turn, run through the real review entry point,
    leaves a line in `glossary` — no LLM, no correction signal, no tool calls."""
    atr.run_after_turn_review(
        service=svc,
        user_message="by CR I mean a code review",
        assistant_text="Got it.",
        correction=False,
    )
    assert _glossary_lines(svc) == ["CR — a code review"]


def test_review_pass_leaves_the_glossary_empty_on_an_ordinary_turn(svc):
    """Vacuity control. Without this, a writer that fired on EVERY turn would pass the
    test above, and the slot injected into every prompt would fill with conversation."""
    atr.run_after_turn_review(
        service=svc,
        user_message="can you rerun the failing test and paste the output?",
        assistant_text="Sure.",
        correction=False,
    )
    assert _glossary_lines(svc) == []


def test_review_pass_does_not_capture_when_facet_capture_is_off(svc):
    """The call site sits inside `run_after_turn_review`'s `capture_facets` branch, so the
    existing opt-out governs glossary capture too. Pinned because a second, independent
    switch for the same kind of passive learning is the drift this avoids."""
    atr.run_after_turn_review(
        service=svc,
        user_message="by CR I mean a code review",
        assistant_text="Got it.",
        correction=False,
        capture_facets=False,
    )
    assert _glossary_lines(svc) == []


def test_repeating_the_definition_reinforces_one_line(svc):
    """`reinforce=True` at the call site: a term the user re-explains is one entry with
    more evidence, not a second line in a slot that is capped at 600 chars."""
    for message in (
        "by the run ledger I mean the append-only event store",
        "the run ledger means the append-only event store",
    ):
        atr.run_after_turn_review(
            service=svc, user_message=message, assistant_text="Noted.", correction=False
        )
    assert _glossary_lines(svc) == ["run ledger — the append-only event store"]


# ── call site 2: the gateway hook every dashboard turn goes through ──────────


def _state_for(service):
    memory = SimpleNamespace(vector_store=service._vs)
    return SimpleNamespace(
        context_builder=SimpleNamespace(get_memory_for=lambda *_a, **_k: memory),
        broadcast_ws=lambda *a, **k: None,
    )


def _session():
    return SimpleNamespace(
        key="dashboard:chat-x", workspace_dir=None, memory_store=None, _ephemeral=False
    )


def test_dashboard_turn_reaches_glossary_capture(svc, monkeypatch):
    """The call site that matters in production. `tool_calls=0` and `correction=False`
    on purpose: the capture must sit BEFORE the expensive-review threshold, because a
    user defining a term does no tool work — behind the gate it would never fire."""
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _m: svc)
    _maybe_after_turn_review(
        _state_for(svc),
        _session(),
        user_message="FYI, SEL stands for the security event log",
        assistant_text="Understood.",
        tool_calls=0,
        provider=None,
    )
    assert _glossary_lines(svc) == ["SEL — the security event log"]


def test_dashboard_turn_writes_nothing_on_an_ordinary_message(svc, monkeypatch):
    """Vacuity control for the gateway path."""
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _m: svc)
    _maybe_after_turn_review(
        _state_for(svc),
        _session(),
        user_message="deploy it when the pipeline goes green",
        assistant_text="Will do.",
        tool_calls=0,
        provider=None,
    )
    assert _glossary_lines(svc) == []


def test_a_restricted_session_captures_no_glossary_term(svc, monkeypatch):
    """A denied gate decision suppresses this capture like every other one. Incognito and
    temporary sessions promise no memory writes, and a slot is the most durable write
    there is — it is injected into every later turn's prompt."""
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _m: svc)
    _maybe_after_turn_review(
        _state_for(svc),
        _session(),
        user_message="by CR I mean a code review",
        assistant_text="Got it.",
        tool_calls=0,
        provider=None,
        decision=GateDecision(
            permitted=False,
            worthwhile=False,
            reason=GateReason.RESTRICTED,
            cadence=Cadence.PER_TURN,
        ),
    )
    assert _glossary_lines(svc) == []


def test_a_human_retired_term_is_never_re_captured(svc):
    """MGAV-8's guard, reached through this call site: the user deleting a captured term
    is final, however many times they say the sentence again. Re-adding it would not read
    as a duplicate row — it would read as the assistant overruling them."""
    atr.run_after_turn_review(
        service=svc,
        user_message="by CR I mean a code review",
        assistant_text="Got it.",
        correction=False,
    )
    assert svc.slot_tombstone("glossary", "CR — a code review") is True
    atr.run_after_turn_review(
        service=svc,
        user_message="by CR I mean a code review",
        assistant_text="Got it.",
        correction=False,
    )
    assert _glossary_lines(svc) == []
