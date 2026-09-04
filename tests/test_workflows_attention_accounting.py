"""Human-attention accounting (EVALUATION-SUBSTRATE §4.4, atom ES-16).

The behaviours the atom is defined by:

1. a human-answered gate journals ``resolved_after_secs`` — the dwell is explicit in the
   event rather than re-derived from the continuation record; an auto-approved gate
   carries none (it cost no attention);
2. ``attention_events_per_run``, the decayed pending-attention debt, and the trend are
   pure queries over existing events — computed on demand, stored nowhere;
3. the post-grant-rise predicate is the mechanical demotion signal: it fires only on a
   real sample on both sides of the grant;
4. promotion proposals cite the attention note through an injected callback, and a
   citation failure never blocks the proposal.
"""

from __future__ import annotations

import time

import pytest

from personalclaw.workflows import human_input as HI
from personalclaw.workflows import introspection as intro
from personalclaw.workflows import journal as J

pytestmark = pytest.mark.asyncio


# ── 1. the dwell stamp rides the real resolution path ────────────────────────


async def test_a_human_answer_journals_its_dwell(tmp_path, monkeypatch):
    from tests.test_workflows_human_input import _blocked

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    c, _status = await _blocked({"timeout_secs": 0})
    cont = HI.list_continuations(c.run.id)[0]
    # Backdate the ask so the dwell is unambiguously positive.
    cont.created_at = time.time() - 30.0
    HI.save_continuation(cont)
    c.resume(cont.token, True)
    resolved = [e for e in J.ledger(c.run.id) if e.get("kind") == J.GATE_RESOLVED]
    assert len(resolved) == 1
    dwell = resolved[0].get("resolved_after_secs")
    assert isinstance(dwell, float) and dwell >= 29.0


# ── 2. the metric is a pure query ─────────────────────────────────────────────


def test_auto_approved_gates_cost_no_attention():
    events = [
        {"kind": "gate_resolved", "answer": {"auto": True}},
        {"kind": "gate_resolved", "answer": {"choice": "ship"}},
        {"kind": "user_edited_mid_flight"},
        {"kind": "judge_divergence"},
        {"kind": "step_completed"},
    ]
    hits = intro.attention_events(events)
    assert len(hits) == 3
    assert all(not (e.get("answer") or {}).get("auto") for e in hits)


def test_debt_decays_on_the_half_life():
    now = 1_000_000.0
    week = 7 * 86400.0
    assert intro.attention_debt([now], now=now) == 1.0
    assert intro.attention_debt([now - week], now=now) == 0.5
    assert intro.attention_debt([now - 2 * week], now=now) == 0.25
    # A future or missing timestamp is skipped, never guessed.
    assert intro.attention_debt([now + 60, 0.0], now=now) == 0.0


def test_trend_needs_a_sample_and_reads_direction():
    assert intro.attention_trend([3, 0]) == ""  # below the minimum sample
    assert intro.attention_trend([3, 3, 3, 0, 0, 0]) == "falling"
    assert intro.attention_trend([0, 0, 0, 2, 2, 3]) == "rising"
    assert intro.attention_trend([1, 1, 1, 1, 1, 1]) == "flat"


def test_attention_stats_summarizes_dwell_and_series():
    runs = [
        (100.0, [{"kind": "gate_resolved", "answer": {"auto": True}}]),
        (
            200.0,
            [
                {"kind": "user_edited_mid_flight", "ts": 200.0},
                {"kind": "gate_resolved", "answer": {"ok": 1}, "resolved_after_secs": 12.5},
            ],
        ),
    ]
    s = intro.attention_stats("tmpl", runs, now=1_000_000.0)
    assert s.runs == 2 and s.attention_events == 2
    assert s.events_per_run == 1.0
    assert s.dwell_p50_secs == 12.5
    assert s.debt > 0
    assert "attention: 1.0/run over 2 runs" in s.note()
    assert intro.attention_stats("empty", [], now=1.0).note() == ""


# ── 3. the mechanical demotion signal ────────────────────────────────────────


def test_post_grant_rise_requires_samples_on_both_sides():
    series = [(1.0, 0), (2.0, 0), (3.0, 0), (4.0, 2), (5.0, 2), (6.0, 3)]
    assert intro.post_grant_rise(series, granted_at=3.5) is True
    assert intro.post_grant_rise(series, granted_at=0.0) is False  # no grant, no signal
    assert intro.post_grant_rise(series[:4], granted_at=3.5) is False  # thin post-grant side
    falling = [(1.0, 3), (2.0, 3), (3.0, 2), (4.0, 0), (5.0, 0), (6.0, 0)]
    assert intro.post_grant_rise(falling, granted_at=3.5) is False


# ── 4. proposals cite the trend through the injected callback ────────────────


def test_promotion_proposal_carries_the_attention_note(monkeypatch):
    from personalclaw.guardrails import ladder

    class _El:
        eligible = True
        next_rung = "one_tap"
        reason = "10 clean approvals over 7 days"

    class _Spec:
        key = "action.test_scope"

    filed: list[tuple[str, str, str]] = []
    monkeypatch.setattr(ladder, "registered_action_types", lambda: (_Spec(),))
    monkeypatch.setattr(ladder, "promotion_eligibility", lambda key: _El())
    monkeypatch.setattr(
        ladder,
        "_file_proposal",
        lambda key, rung, record: filed.append((key, rung, record)) or True,
    )
    monkeypatch.setattr("personalclaw.guardrails.rungs.ensure_core_action_types", lambda: None)

    ladder.propose_promotions(note_for=lambda key: "workflow attention: 0.4/run over 12 runs")
    assert filed and "workflow attention: 0.4/run over 12 runs" in filed[0][2]

    filed.clear()

    def _boom(key: str) -> str:
        raise RuntimeError("citation source down")

    ladder.propose_promotions(note_for=_boom)
    assert filed and filed[0][2] == _El.reason  # the proposal survives a citation failure
