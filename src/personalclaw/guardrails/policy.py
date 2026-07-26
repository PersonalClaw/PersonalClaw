"""Named safety profiles (AUTONOMY-GUARDRAILS §3).

Modeled line-for-line on the egress template (``net/policy.py``: a frozen dataclass
+ named module-level profiles + an operator-layering function). A profile is the
single object that decides approval + tool grants + egress + budget + scan for a
run — replacing the ad-hoc ``ToolApprovalPolicy.AUTO_APPROVE`` vs ``HOOK_BASED`` pick
the gateway makes today.

**Headless by construction:** unattended trigger-fired runs resolve through
``HEADLESS`` mechanically, keyed off the session-key conventions that already
classify unattended work (``session._STATELESS_PREFIXES`` + ``loop-*`` workers).
Auto-fired runs default read-only; write/execute is a creation-time grant on the
job/trigger, never acquired mid-run.

Per-template graduated profiles (a WORKFLOWS-V2 template naming ``coding`` /
``review-only`` / ``cleanup``) arrive when that engine lands and consumes
``tool_grants``; until then the profile decides approval + egress + budget + scan
for the unattended paths that exist today.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from personalclaw.guardrails.budgets import Budget

# Tool-grant tiers. ``read`` = read-only tools only (default-deny write/execute);
# ``read_write`` = full grant (today's interactive default); ``custom`` = an explicit
# allowlist carried in ``tool_allowlist`` (consumed by the tool-approval layer when the
# engine's per-template profiles land).
TOOL_READ = "read"
TOOL_READ_WRITE = "read_write"
TOOL_CUSTOM = "custom"


@dataclass(frozen=True)
class SafetyProfile:
    """A run's safety posture — approval + grants + egress + budget + scan in one object."""

    name: str
    approval: str = "ask"  # auto | hook_based | ask
    tool_grants: str = TOOL_READ_WRITE  # read | read_write | custom
    tool_allowlist: tuple[str, ...] = ()  # meaningful only when tool_grants == custom
    egress_tier: str = "all"  # off | listed | registry | all  (§4.2)
    denylist_extra: tuple[str, ...] = ()  # extra path globs layered on the base denylist
    budget: Budget = field(default_factory=Budget)
    scan_mode: str = "redact"  # warn | redact | block

    def with_overrides(self, **kw) -> "SafetyProfile":
        """A copy with fields replaced (operator config layering)."""
        return replace(self, **kw)


# ── Named profiles ────────────────────────────────────────────────────────────

# Today's chat defaults: a human is watching, full grants, public egress.
INTERACTIVE = SafetyProfile(
    name="interactive",
    approval="ask",
    tool_grants=TOOL_READ_WRITE,
    egress_tier="all",
    scan_mode="warn",
)

# Write inside the workspace, dev-registry egress only.
CODING = SafetyProfile(
    name="coding",
    approval="hook_based",
    tool_grants=TOOL_READ_WRITE,
    egress_tier="registry",
    scan_mode="redact",
)

# Read-only tools, no external writes — a reviewer/analyst.
REVIEW_ONLY = SafetyProfile(
    name="review_only",
    approval="hook_based",
    tool_grants=TOOL_READ,
    egress_tier="listed",
    scan_mode="redact",
)

# Delete allowed inside granted dirs only (a cleanup job).
CLEANUP = SafetyProfile(
    name="cleanup",
    approval="hook_based",
    tool_grants=TOOL_READ_WRITE,
    egress_tier="off",
    scan_mode="redact",
)

# Everything denied except notify — the incident posture.
INCIDENT = SafetyProfile(
    name="incident",
    approval="hook_based",
    tool_grants=TOOL_READ,
    egress_tier="off",
    scan_mode="block",
)

# The unattended default: read-only + creation-time grants. Auto-fired runs resolve
# HERE by construction (never blocks waiting for a human; writes require a grant on
# the job/trigger reviewed when the automation was created).
HEADLESS = SafetyProfile(
    name="headless",
    approval="hook_based",
    tool_grants=TOOL_READ,
    egress_tier="registry",
    scan_mode="redact",
)

_PROFILES: dict[str, SafetyProfile] = {
    p.name: p for p in (INTERACTIVE, CODING, REVIEW_ONLY, CLEANUP, INCIDENT, HEADLESS)
}


def get_profile(name: str) -> SafetyProfile:
    """Look up a named profile (defaults to HEADLESS — the safe unattended posture —
    for an unknown name, NOT interactive: an unrecognized profile must fail closed)."""
    return _PROFILES.get(name, HEADLESS)


def safety_profile_for(base: SafetyProfile) -> SafetyProfile:
    """Layer the operator's ``guardrails`` config onto a base profile (§3).

    Mirrors ``egress_policy_for``: the default budget + scan_mode from
    ``GuardrailsConfig`` fill in a profile that didn't set its own. Config read is
    lazy + best-effort so the module stays importable without a loaded config."""
    try:
        from personalclaw.config.loader import AppConfig

        gr = AppConfig.load().guardrails
    except Exception:
        return base
    # The operator's default day budget applies to a profile with no budget of its own.
    day_budget = Budget(
        max_tokens=gr.budgets.max_tokens_per_day, max_dollars=gr.budgets.max_dollars_per_day
    )
    budget = base.budget if not base.budget.is_unlimited else day_budget
    # An INTERACTIVE/local profile keeps its own scan_mode; the unattended profiles
    # inherit the operator's configured mode when they didn't force 'block'.
    scan_mode = base.scan_mode if base.scan_mode == "block" else gr.scan_mode
    return base.with_overrides(budget=budget, scan_mode=scan_mode)


# ── Headless-by-construction resolution ─────────────────────────────────────────

# loop-worker session keys (Goal/Code loop cycle workers) — unattended, like the
# stateless prefixes. Kept here (not in session.py) since it's a guardrail concern.
_LOOP_PREFIXES = ("loop-", "loop:")


def is_unattended_session(session_key: str) -> bool:
    """True when ``session_key`` names an unattended run (cron/subagent/channel/inbox/
    side/loop worker) — the keys that resolve through HEADLESS by construction."""
    from personalclaw.session import _STATELESS_PREFIXES

    key = session_key or ""
    return any(key.startswith(p) for p in (*_STATELESS_PREFIXES, *_LOOP_PREFIXES))


def profile_for_session(session_key: str) -> SafetyProfile:
    """Resolve the safety profile for a session key BY CONSTRUCTION.

    An unattended session (cron/subagent/channel/inbox/side/loop) resolves to
    ``HEADLESS`` (read-only default, config-layered budget + scan); everything else
    is the human-watched ``INTERACTIVE`` posture. This is the single object the
    gateway's approval pick consults, replacing the ad-hoc AUTO_APPROVE/HOOK_BASED
    branch. Operator config is layered in via ``safety_profile_for``."""
    base = HEADLESS if is_unattended_session(session_key) else INTERACTIVE
    return safety_profile_for(base)
