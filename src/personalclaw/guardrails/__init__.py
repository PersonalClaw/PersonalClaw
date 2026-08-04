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

Later sessions add the budget meter, path/action denylist, incident kill switch,
DISABLE_LIVE_WRITES, named safety profiles, and the ``sdk.guardrails`` facade
(see ``docs/roadmap/plans/AUTONOMY-GUARDRAILS.md``).
"""

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
    get_profile,
    is_unattended_session,
    profile_for_session,
    safety_profile_for,
)
from personalclaw.guardrails.scan import ScanResult, scan_outbound
from personalclaw.guardrails.writes import live_writes_disabled

__all__ = [
    "BreakerState",
    "Budget",
    "BudgetExceededError",
    "BudgetVerdict",
    "CircuitBreaker",
    "CircuitOpenError",
    "DenyDecision",
    "DenyRule",
    "FailureMode",
    "HEADLESS",
    "INTERACTIVE",
    "IncidentState",
    "ModelCallGuard",
    "ModelCallTimeout",
    "OutputContractError",
    "SafetyProfile",
    "ScanResult",
    "PromptInjectionBlocked",
    "SecretLeakBlocked",
    "SpendMeter",
    "budget_from_config",
    "check_action",
    "enforce_action",
    "get_breaker",
    "get_meter",
    "get_profile",
    "guard_flag",
    "incident_active",
    "is_unattended_session",
    "live_writes_disabled",
    "profile_for_session",
    "reset_breakers",
    "reset_incident_mirror",
    "reset_meter",
    "run_budget_from_config",
    "safety_profile_for",
    "scan_outbound",
    "wrap_model_call_guard",
]
