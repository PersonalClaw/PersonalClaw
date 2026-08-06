"""Tests for safety profiles + egress tiers + the provider health view
(AUTONOMY-GUARDRAILS §3, §4.2, §2.5)."""

from __future__ import annotations

import pytest

from personalclaw.guardrails.health import provider_health
from personalclaw.guardrails.policy import (
    HEADLESS,
    INTERACTIVE,
    SafetyProfile,
    approval_policy_for_session,
    get_profile,
    is_unattended_session,
    profile_for_session,
    safety_profile_for,
)
from personalclaw.llm_helpers import ToolApprovalPolicy
from personalclaw.net.policy import REGISTRY, egress_policy_for_tier, get_policy
from personalclaw.session import BACKGROUND_KEY

# ── §3 SafetyProfile ─────────────────────────────────────────────────────────


def test_named_profiles_exist():
    for name in ("interactive", "coding", "review_only", "cleanup", "incident", "headless"):
        assert get_profile(name).name == name


def test_unknown_profile_fails_closed_to_headless():
    # An unrecognized profile must NOT default to interactive (that would grant a
    # human-watched posture to an unattended run) — it fails closed to headless.
    assert get_profile("bogus").name == "headless"


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


def test_background_key_is_headless():
    # AG-5 edit 1: `_bg` (the shared background/heartbeat/cron/lessons key) matches no
    # unattended prefix, so it must be classified explicitly — it is genuinely
    # unattended and must resolve through HEADLESS, not INTERACTIVE.
    assert is_unattended_session(BACKGROUND_KEY) is True
    assert profile_for_session(BACKGROUND_KEY).name == "headless"


def test_approval_policy_for_session_maps_from_profile():
    # The helper MUST derive from `profile_for_session(...).approval`, not a constant.
    # Unattended keys resolve to HEADLESS (approval == "hook_based") → HOOK_BASED.
    assert profile_for_session(BACKGROUND_KEY).approval == "hook_based"
    assert approval_policy_for_session(BACKGROUND_KEY) is ToolApprovalPolicy.HOOK_BASED
    assert approval_policy_for_session("cron:x") is ToolApprovalPolicy.HOOK_BASED
    assert approval_policy_for_session("subagent:x") is ToolApprovalPolicy.HOOK_BASED

    # An INTERACTIVE key: INTERACTIVE.approval == "ask", which this helper maps to
    # HOOK_BASED (an unattended reach with no human to ask keeps the security gate).
    # Assert the MAP against the profile's declared approval, not a bare constant.
    interactive_approval = profile_for_session("chat:main").approval
    assert interactive_approval == "ask"
    expected = (
        ToolApprovalPolicy.AUTO_APPROVE
        if interactive_approval == "auto"
        else ToolApprovalPolicy.HOOK_BASED
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
    # HEADLESS didn't force block; it inherits the operator's configured mode.
    assert layered.scan_mode == "block"


def test_incident_profile_forces_block_scan():
    # INCIDENT declares scan_mode=block; safety_profile_for keeps a forced block even
    # when config says otherwise (an incident must not relax the scan).
    from personalclaw.guardrails.policy import INCIDENT

    assert INCIDENT.scan_mode == "block"


def test_profile_with_overrides():
    p = SafetyProfile(name="x").with_overrides(egress_tier="off")
    assert p.egress_tier == "off" and p.name == "x"


# ── §4.2 egress tiers ────────────────────────────────────────────────────────


def test_registry_profile_registered():
    assert get_policy("registry").name == "registry"
    assert "pypi.org" in REGISTRY.allow_hosts


def test_egress_policy_for_tier():
    assert egress_policy_for_tier("off") is None
    assert egress_policy_for_tier("registry").name == "registry"
    assert egress_policy_for_tier("all").name == "strict"
    assert egress_policy_for_tier("listed").name == "strict"
    assert egress_policy_for_tier("bogus").name == "strict"  # unknown → safe default


# ── §2.5 provider health ─────────────────────────────────────────────────────


def test_health_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    h = provider_health()
    assert h == {"providers": [], "generated_from": 0}


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
