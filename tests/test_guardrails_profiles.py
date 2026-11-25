"""Tests for safety profiles + egress tiers + the provider health view."""

from __future__ import annotations

import pytest

from personalclaw.guardrails.health import provider_health
from personalclaw.guardrails.policy import (
    HEADLESS,
    INTERACTIVE,
    SafetyProfile,
    approval_policy_for_session,
    is_unattended_session,
    profile_for_session,
    safety_profile_for,
)
from personalclaw.llm_helpers import ToolApprovalPolicy
from personalclaw.net.policy import REGISTRY, egress_policy_for_tier, get_policy

# ── SafetyProfile ────────────────────────────────────────────────────────────


def test_only_the_profiles_the_product_runs_exist():
    """A session resolves to INTERACTIVE or HEADLESS (`profile_for_session`), and nothing else
    selects a profile. Four more were defined and never chosen, so a reader took a coding,
    review-only, cleanup or incident posture for one that exists."""
    from personalclaw import guardrails
    from personalclaw.guardrails import policy

    defined = {name for name, value in vars(policy).items() if isinstance(value, SafetyProfile)}
    assert defined == {"INTERACTIVE", "HEADLESS"}
    assert {INTERACTIVE.name, HEADLESS.name} == {"interactive", "headless"}
    assert not hasattr(policy, "get_profile") and not hasattr(guardrails, "get_profile")


def test_headless_is_read_only_by_construction():
    assert HEADLESS.tool_grants == "read"
    assert HEADLESS.approval == "hook_based"
    assert INTERACTIVE.tool_grants == "read_write"


@pytest.mark.parametrize(
    "session_key,unattended",
    [
        ("cron:job1", True),
        ("subagent:abc", True),
        ("channel:slack:c1", True),
        ("inbox:item1", True),
        ("side:x", True),
        ("loop-goal-1", True),
        ("loop:code:2", True),
        ("chat:main", False),
        ("", False),
    ],
)
def test_is_unattended_session(session_key, unattended):
    assert is_unattended_session(session_key) is unattended


def test_profile_for_session_by_construction():
    assert profile_for_session("cron:nightly").name == "headless"
    assert profile_for_session("loop-abc").name == "headless"
    assert profile_for_session("chat:main").name == "interactive"


def test_approval_policy_for_session_maps_from_profile():
    # The helper MUST derive from `profile_for_session(...).approval`, not a constant.
    # Unattended keys resolve to HEADLESS (approval == "hook_based") → HOOK_BASED.
    assert profile_for_session("cron:x").approval == "hook_based"
    assert approval_policy_for_session("cron:x") is ToolApprovalPolicy.HOOK_BASED
    assert approval_policy_for_session("subagent:x") is ToolApprovalPolicy.HOOK_BASED

    # An INTERACTIVE key: INTERACTIVE.approval == "ask" — a person answers each call. This helper
    # answers for a run with NO interactive callback, so nobody can: nothing runs. It mapped `ask`
    # to HOOK_BASED, whose default approves every call no hook names, which is how an operator
    # ceiling of `ask` had no effect on a heartbeat (`guardrails.policy.no_one_to_ask`).
    # Assert the MAP against the profile's declared approval, not a bare constant.
    interactive_approval = profile_for_session("chat:main").approval
    assert interactive_approval == "ask"
    expected = (
        ToolApprovalPolicy.AUTO_APPROVE
        if interactive_approval == "auto"
        else ToolApprovalPolicy.REJECT_ALL
    )
    assert approval_policy_for_session("chat:main") is expected
    assert approval_policy_for_session("") is expected


def test_approval_policy_auto_maps_to_auto_approve(monkeypatch):
    # Prove the "auto" branch is live: a profile with approval="auto" maps to
    # AUTO_APPROVE. Patch the resolver so the map, not a fixed profile, is exercised.
    import personalclaw.guardrails.policy as policy

    monkeypatch.setattr(
        policy, "profile_for_session", lambda _k: SafetyProfile(name="x", approval="auto")
    )
    assert approval_policy_for_session("whatever") is ToolApprovalPolicy.AUTO_APPROVE


def test_safety_profile_for_layers_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(
        '{"guardrails": {"budgets": {"max_tokens_per_day": 5000}, "scan_mode": "block"}}'
    )
    layered = safety_profile_for(HEADLESS)
    assert layered.budget.max_tokens == 5000  # operator day budget filled in
    # Every profile takes the operator's configured mode.
    assert layered.scan_mode == "block"
    assert safety_profile_for(INTERACTIVE).scan_mode == "block"


def test_profile_with_overrides():
    p = SafetyProfile(name="x").with_overrides(egress_tier="off")
    assert p.egress_tier == "off" and p.name == "x"


# ── egress tiers ─────────────────────────────────────────────────────────────


def test_registry_profile_registered():
    assert get_policy("registry").name == "registry"
    assert "pypi.org" in REGISTRY.allow_hosts


def test_egress_policy_for_tier():
    assert egress_policy_for_tier("off") is None
    assert egress_policy_for_tier("registry").name == "registry"
    assert egress_policy_for_tier("all").name == "strict"
    assert egress_policy_for_tier("bogus").name == "strict"  # unknown → safe default
    # 🔴 CORRECTED, not relaxed: "listed" used to return STRICT, whose
    # `allow_hosts` is ADDITIVE (it waives the private-range block) — so a "listed" tier
    # reached every public host exactly like "all" and could not narrow anything. It now
    # returns the exclusive LISTED profile, so the tier means what its name says.
    listed = egress_policy_for_tier("listed")
    assert listed.name == "listed" and listed.allow_only is True
    assert egress_policy_for_tier("registry").allow_only is True
    assert egress_policy_for_tier("all").allow_only is False


# ── provider health ──────────────────────────────────────────────────────────


def test_health_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    h = provider_health()
    # `callers` is the same population regrouped by subsystem — empty in, empty out.
    assert h == {"providers": [], "callers": [], "generated_from": 0}


def test_health_derives_from_audit(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    from personalclaw.guardrails.audit import AttemptRecord, record_attempt

    for i in range(4):
        record_attempt(
            AttemptRecord(
                audit_id=f"a{i}",
                ts=float(i),
                use_case="reasoning",
                provider="P",
                model="m",
                attempt=1,
                passed=True,
                latency_ms=100.0 + i * 10,
            )
        )
    record_attempt(
        AttemptRecord(
            audit_id="fail",
            ts=9.0,
            use_case="reasoning",
            provider="P",
            model="m",
            attempt=1,
            failure_mode="timeout",
            passed=False,
        )
    )
    h = provider_health()
    prov = next(p for p in h["providers"] if p["name"] == "P")
    assert prov["calls"] == 5 and prov["passed"] == 4 and prov["failed"] == 1
    assert prov["pass_rate"] == 0.8
    assert prov["p50_ms"] > 0
    assert prov["failure_modes"] == {"timeout": 1}
    assert prov["breaker_state"] == "closed"


def test_health_includes_open_breaker_without_audit(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    from personalclaw.guardrails.breaker import get_breaker

    b = get_breaker("DownProvider", threshold=1)
    b.record_failure()
    assert b.is_open()
    h = provider_health()
    prov = next(p for p in h["providers"] if p["name"] == "DownProvider")
    assert prov["breaker_state"] == "open" and prov["calls"] == 0
