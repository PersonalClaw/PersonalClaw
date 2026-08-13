"""Autonomy guardrails — the personal safety floor + model-call chokepoint.

This package is the LLM twin of ``net/`` (the network egress chokepoint): one
seam every non-interactive model call passes through, so the platform can meter,
fail fast on provider outages, and record a tamper-evident attempt trail without
touching the interactive chat stream a human is watching.

Session 1 (this slice) ships the chokepoint core:

* :mod:`personalclaw.guardrails.failure` — the failure-mode taxonomy + typed errors.
* :mod:`personalclaw.guardrails.breaker` — a per-provider three-state circuit breaker.
* :mod:`personalclaw.guardrails.audit` — the attempt-level JSONL audit trail.
* :mod:`personalclaw.guardrails.model_call` — ``ModelCallGuard``, the provider
  adapter that wires breaker + hard timeout + audit around a resolved
  ``ModelProvider``.

Later sessions added the budget meter, path/action denylist, incident kill switch,
DISABLE_LIVE_WRITES and named safety profiles, plus:

* :mod:`personalclaw.guardrails.autonomy` — the earned-autonomy rung ladder (§5):
  per-action-type rungs, a DERIVED track record, user-clicked promotion and automatic
  demotion. It sits ON TOP of the floor above and never relaxes it.

The ``sdk.guardrails`` facade is still to come (see
``docs/roadmap/plans/AUTONOMY-GUARDRAILS.md``).
"""

from personalclaw.guardrails.autonomy import (
    RUNGS,
    ActionTypeSpec,
    Demotion,
    Eligibility,
    PromotionRule,
    RungGrant,
    action_type,
    demote,
    grant_rung,
    granted_rung,
    promotion_eligibility,
    register_action_type,
    registered_action_types,
    reset_action_types,
    resolve_rung,
    rung_rank,
    rung_state,
)
from personalclaw.guardrails.breaker import (
    BreakerState,
    CircuitBreaker,
    get_breaker,
    reset_breakers,
)
from personalclaw.guardrails.budgets import (
    Budget,
    BudgetVerdict,
    SpendMeter,
    budget_from_config,
    get_meter,
    reset_meter,
    run_budget_from_config,
)
from personalclaw.guardrails.denylist import (
    DenyDecision,
    DenyRule,
    check_action,
    enforce_action,
)
from personalclaw.guardrails.failure import (
    BudgetExceededError,
    CircuitOpenError,
    FailureMode,
    ModelCallTimeout,
    OutputContractError,
    PromptInjectionBlocked,
    SecretLeakBlocked,
)
from personalclaw.guardrails.flags import guard_flag
from personalclaw.guardrails.incident import (
    IncidentState,
    incident_active,
    reset_incident_mirror,
)
from personalclaw.guardrails.model_call import ModelCallGuard, wrap_model_call_guard
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
from personalclaw.guardrails.scan import ScanResult, scan_outbound
from personalclaw.guardrails.writes import live_writes_disabled

__all__ = [
    "ActionTypeSpec",
    "BreakerState",
    "Budget",
    "BudgetExceededError",
    "BudgetVerdict",
    "CircuitBreaker",
    "CircuitOpenError",
    "Demotion",
    "DenyDecision",
    "DenyRule",
    "Eligibility",
    "FailureMode",
    "HEADLESS",
    "INTERACTIVE",
    "IncidentState",
    "ModelCallGuard",
    "ModelCallTimeout",
    "OutputContractError",
    "PromotionRule",
    "RUNGS",
    "RungGrant",
    "SafetyProfile",
    "ScanResult",
    "PromptInjectionBlocked",
    "SecretLeakBlocked",
    "SpendMeter",
    "action_type",
    "approval_policy_for_session",
    "budget_from_config",
    "check_action",
    "demote",
    "enforce_action",
    "get_breaker",
    "get_meter",
    "get_profile",
    "grant_rung",
    "granted_rung",
    "guard_flag",
    "incident_active",
    "is_unattended_session",
    "live_writes_disabled",
    "profile_for_session",
    "promotion_eligibility",
    "register_action_type",
    "registered_action_types",
    "reset_action_types",
    "reset_breakers",
    "reset_incident_mirror",
    "reset_meter",
    "resolve_rung",
    "rung_rank",
    "rung_state",
    "run_budget_from_config",
    "safety_profile_for",
    "scan_outbound",
    "wrap_model_call_guard",
]
