"""No-model degraded-contract tests (PLATFORM-RESILIENCE §5).

Pins the contract registry, the availability derivation (every needed use-case must
resolve), the read-only/fail-safe backlog + availability probes, and the
one-notification-per-transition rule (silent baseline on first sight → warning on
down → info on recovery).
"""

from __future__ import annotations

import pytest

from personalclaw.resilience import degraded
from personalclaw.resilience.degraded import DegradedContract


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot + restore the process-global contract registry and transition
    baseline, so a test's throwaway ``t_*`` contracts never leak into sibling tests
    (or other files) under xdist."""
    saved = dict(degraded._CONTRACTS)
    degraded.reset_transition_state()
    yield
    degraded._CONTRACTS.clear()
    degraded._CONTRACTS.update(saved)
    degraded.reset_transition_state()


# ── the built-in contract set ────────────────────────────────────────────────


def test_builtin_contracts_registered():
    surfaces = {c.surface for c in degraded.all_contracts()}
    assert {
        "chat",
        "inbox_enrichment",
        "memory_extraction",
        "knowledge_ingest",
        "search_ranking",
        "transcription",
        "assistant_reasoning",
    } <= surfaces


def test_no_future_infra_contracts_registered():
    """The synthesis-watcher floor is future infra (WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS) —
    it must NOT be registered here (nothing in code to declare against)."""
    surfaces = {c.surface for c in degraded.all_contracts()}
    assert "synthesis" not in surfaces and "synthesis_watchers" not in surfaces


def test_no_contract_has_a_drain_yet():
    """Drains are §4 remediation-engine jobs — every contract's drain is None until
    that engine lands (a drain wired here would be building unbuilt infra)."""
    assert all(c.drain is None for c in degraded.all_contracts())


# ── availability derivation ──────────────────────────────────────────────────


def test_availability_all_use_cases_must_resolve(monkeypatch):
    """A surface is available only when EVERY use-case it needs resolves."""
    resolvable = {"chat"}
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda uc: uc in resolvable,
    )
    degraded.register_contract(DegradedContract(surface="t_one", use_cases=("chat",), floor="f"))
    degraded.register_contract(
        DegradedContract(surface="t_both", use_cases=("chat", "embedding"), floor="f")
    )
    rows = {r["surface"]: r for r in degraded.evaluate()}
    assert rows["t_one"]["available"] is True  # chat resolves
    assert rows["t_both"]["available"] is False  # embedding does not


def test_availability_probe_fault_fails_available_not_down(monkeypatch):
    """A raising probe must not make a surface look falsely degraded (avoid a false
    alarm from an unrelated bug)."""

    def _boom(uc):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr("personalclaw.providers.provider_bridge.can_resolve_use_case", _boom)
    degraded.register_contract(DegradedContract(surface="t_fault", use_cases=("chat",), floor="f"))
    row = next(r for r in degraded.evaluate() if r["surface"] == "t_fault")
    assert row["available"] is True


def test_backlog_probe_is_fail_safe(monkeypatch):
    """A raising backlog probe reports 0, never propagates."""
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: False
    )

    def _boom() -> int:
        raise RuntimeError("store gone")

    degraded.register_contract(
        DegradedContract(surface="t_backlog", use_cases=("chat",), floor="f", backlog_probe=_boom)
    )
    row = next(r for r in degraded.evaluate() if r["surface"] == "t_backlog")
    assert row["backlog"] == 0


def test_degraded_surfaces_lists_only_unavailable(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda uc: uc == "chat",
    )
    degraded.register_contract(DegradedContract(surface="t_up", use_cases=("chat",), floor="f"))
    degraded.register_contract(
        DegradedContract(surface="t_down", use_cases=("embedding",), floor="f")
    )
    down = degraded.degraded_surfaces()
    assert "t_down" in down and "t_up" not in down


# ── transition notifications (one per change; silent baseline) ───────────────


class _RecordingState:
    def __init__(self):
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body))


def test_first_evaluation_is_silent_baseline(monkeypatch):
    """No boot storm — the first sight of a surface only seeds the baseline."""
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: False
    )
    degraded.register_contract(DegradedContract(surface="t_new", use_cases=("chat",), floor="f"))
    state = _RecordingState()
    degraded.evaluate(notify=True, state=state)
    assert state.notes == []  # baseline seeded, nothing emitted


def test_down_then_recovery_emits_warning_then_info(monkeypatch):
    available = {"value": True}
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda uc: available["value"],
    )
    degraded.register_contract(
        DegradedContract(surface="t_flap", use_cases=("chat",), floor="the floor")
    )
    state = _RecordingState()
    # Filter to THIS surface's notes — the built-in contracts share the monkeypatched
    # probe and transition alongside t_flap, which is not what this test measures.
    flap = lambda: [n for n in state.notes if "t_flap" in n[1]]  # noqa: E731

    degraded.evaluate(notify=True, state=state)  # baseline: available
    assert flap() == []

    available["value"] = False
    degraded.evaluate(notify=True, state=state)  # went down → warning
    assert len(flap()) == 1
    assert flap()[0][0] == "warning" and "t_flap" in flap()[0][1]

    available["value"] = True
    degraded.evaluate(notify=True, state=state)  # recovered → info
    assert len(flap()) == 2
    assert flap()[1][0] == "info" and "recovered" in flap()[1][1]


def test_no_change_emits_nothing(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: False
    )
    degraded.register_contract(DegradedContract(surface="t_stable", use_cases=("chat",), floor="f"))
    state = _RecordingState()
    degraded.evaluate(notify=True, state=state)  # baseline
    degraded.evaluate(notify=True, state=state)  # still down — no new note
    degraded.evaluate(notify=True, state=state)
    assert state.notes == []


def test_evaluate_without_notify_never_touches_state(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: False
    )
    degraded.register_contract(DegradedContract(surface="t_quiet", use_cases=("chat",), floor="f"))
    # notify defaults False; a plain rollup for the Doctor must not notify.
    rows = degraded.evaluate()
    assert any(r["surface"] == "t_quiet" for r in rows)
