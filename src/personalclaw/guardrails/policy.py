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
from typing import TYPE_CHECKING

from personalclaw.guardrails.autonomy import RUNG_AUTO_WITH_UNDO, RUNG_AUTONOMOUS
from personalclaw.guardrails.budgets import Budget

if TYPE_CHECKING:
    from personalclaw.llm_helpers import ToolApprovalPolicy

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
    # Path globs the run is CONFINED to (the ``paths`` ceiling scope's allow plane).
    # Empty = unconfined (deny-only, today's posture). Non-empty = a closed ruleset: a
    # path-carrying action config that matches none of these is refused by
    # ``denylist.check_action``. Only an operator ceiling writes it — no named profile
    # ships one, so the default stays byte-identical to the deny-only behaviour.
    path_allowlist: tuple[str, ...] = ()
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
#
# 🔴 ``egress_tier="all"``, corrected from ``"registry"`` when PHF-8 gave the tier a real
# enforcement point. REGISTRY was authored (net/policy.py) for "sandboxed code runs that
# need the common dev registries WITHOUT opening the whole internet" — a PACKAGE-manager
# posture. The plane that actually exists to enforce a tier on is the agent's page fetch
# (`web.fetch.web_fetch`) and the watched-source poll; core has no code-run egress plane
# (the sandbox providers do not own a network namespace). Enforcing "registry" there would
# deny every unattended fetch that is not pypi/npm/crates — i.e. every watched-source
# poll, every subagent research fetch, every inbox-triggered link read — with no UI to
# undo it. "all" is not "unguarded": it is STRICT (public hosts only, no loopback/RFC-1918/
# link-local, pinned IPs, byte + timeout caps, operator deny_hosts honoured). An operator
# who does want registry-only or allow-list-only unattended egress writes it in the
# governance ceiling (`{"scopes": {"egress": {"value": "listed"}}}`), which is enforced.
HEADLESS = SafetyProfile(
    name="headless",
    approval="hook_based",
    tool_grants=TOOL_READ,
    egress_tier="all",
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

#: The prefix for a dispatch that has NO session at all — a store-backed trigger fire, a
#: memory-event trigger, a top-level script hook. Those seams hold a trigger/hook id and
#: nothing else, and they are unattended by definition: no human is watching, and the
#: action is an automated side-effect (the same reasoning the incident kill switch already
#: applies to a hook's action). Before PHF-8 every one of them passed ``session_key=""``,
#: which classified as ATTENDED and resolved INTERACTIVE — so "headless by construction"
#: held in tests and nowhere else. :func:`unattended_dispatch_key` mints the identity.
UNATTENDED_DISPATCH_PREFIX = "unattended:"

# loop-worker session keys (Goal/Code loop cycle workers) — unattended, like the
# stateless prefixes. Kept here (not in session.py) since it's a guardrail concern.
_LOOP_PREFIXES = ("loop-", "loop:")

#: Every prefix this module classifies as unattended on top of session.py's own.
_EXTRA_UNATTENDED_PREFIXES = (*_LOOP_PREFIXES, UNATTENDED_DISPATCH_PREFIX)


def unattended_dispatch_key(origin: str) -> str:
    """The guardrail identity for a sessionless unattended dispatch.

    ``origin`` names WHAT fired (``trigger:<id>``, ``hook:<id>``) so a clamp in the SEL is
    attributable to the automation that caused it. The key is a guardrail identity only —
    it is never used to open or look up a chat session.
    """
    return f"{UNATTENDED_DISPATCH_PREFIX}{(origin or 'unknown').strip()}"


def is_unattended_session(session_key: str) -> bool:
    """True when ``session_key`` names an unattended run (cron/subagent/channel/inbox/
    side/loop worker, a sessionless ``unattended:`` dispatch, or the ``_bg`` background
    key) — the keys that resolve through HEADLESS by construction."""
    from personalclaw.session import _STATELESS_PREFIXES, BACKGROUND_KEY

    key = session_key or ""
    # ``_bg`` is the shared background/heartbeat/cron/lessons session key (see
    # session.py) — genuinely unattended, so it resolves through HEADLESS even though
    # it matches no prefix. It's an exact key, not a prefix, hence the equality check.
    if key == BACKGROUND_KEY:
        return True
    return any(key.startswith(p) for p in (*_STATELESS_PREFIXES, *_EXTRA_UNATTENDED_PREFIXES))


def profile_for_session(session_key: str) -> SafetyProfile:
    """Resolve the safety profile for a session key BY CONSTRUCTION.

    An unattended session (cron/subagent/channel/inbox/side/loop, or a sessionless
    ``unattended:`` dispatch) resolves to ``HEADLESS`` (read-only default, config-layered
    budget + scan); everything else is the human-watched ``INTERACTIVE`` posture. This is
    the single object the gateway's approval pick consults, replacing the ad-hoc
    AUTO_APPROVE/HOOK_BASED branch. Operator config is layered in via
    ``safety_profile_for``.

    **Then the CEILING intersects it** (PLATFORM-HARDENING-FLOORS §5): the operator's
    ``governance/ceiling.json`` is level one and this profile is level two, and tightest
    wins. Composing HERE — rather than at each seam — is deliberate: this function is
    already the single object every dispatch seam consults (rung routing, the action
    denylist, the tool-approval pick, egress), so one call site makes the ceiling live
    everywhere at once and leaves no seam that reads a profile the ceiling never bounded.
    A corrupt ceiling raises out of here, which fails the dispatch CLOSED."""
    base = HEADLESS if is_unattended_session(session_key) else INTERACTIVE
    layered = safety_profile_for(base)
    from personalclaw.guardrails.ceiling import active_ceiling, resolve

    return resolve(active_ceiling(), layered)


def ceiling_permits_approval(value: str) -> bool:
    """Whether the operator CEILING permits an explicit approval grant of ``value``.

    The spawn path (``subagent._run_inner``) resolves its approval posture through five
    widening branches — the dashboard trust toggle, an explicit ``approval_mode="auto"``
    from a cron/agent caller, ``--approval yolo``, the config default, and
    ``auto_approve_subagent_tools`` — each of which can only set ``auto``. Those are
    deliberate USER grants, so the profile default must not veto them (that would delete
    the trust toggle). The CEILING must: it is the operator's hard bound, and an operator
    who wrote ``{"approval": {"value": "ask"}}`` has said no run on this machine
    auto-approves, including one a toggle widened.

    Implemented by resolving the grant as a posture: a profile carrying the grant is
    intersected with the ceiling, and the grant stands only if it survives. That keeps
    ONE composition rule (tightest wins, via :func:`~personalclaw.guardrails.ceiling.
    resolve`) instead of a second hand-rolled comparison that could drift from it.
    """
    from personalclaw.guardrails.ceiling import active_ceiling, resolve

    probe = SafetyProfile(name="approval_grant", approval=value)
    return resolve(active_ceiling(), probe).approval == value


def rung_ceiling_for_profile(profile: SafetyProfile) -> str:
    """The highest autonomy rung a run under ``profile`` may reach (AUTONOMY-GUARDRAILS
    §5.2, layered per PLATFORM-HARDENING-FLOORS §5).

    **Two levels, one rule — tightest wins.** The action type's own ceiling is level one;
    this is level two, and it may only NARROW. The composition lives in
    :func:`~personalclaw.guardrails.rungs.route_action_type`, which takes the lower of the
    two, so a profile can never hand a type a rung its declaration refused.

    The ordinal is read off ``profile.approval``, the one profile field that describes how
    much the run may decide alone:

    * ``auto`` — the operator pre-approved this posture, so nothing here narrows it.
    * ``ask`` — a human is watching the run and sees the result as it lands, so the
      type's own ceiling is the only bound that matters.
    * ``hook_based`` — the UNATTENDED posture: there is no human to ask and no one
      watching. ``autonomous`` (silent, no undo handle) would mean an action ran and left
      no trace a user would notice, so it narrows to ``auto_with_undo`` — execute, but
      keep the reversal handle and the passive notification that let the user find it.

    The INCIDENT posture is not expressed here: ``resolve_rung`` clamps every resolution
    to ``one_tap`` while an incident is active, which outranks both levels.
    """
    return RUNG_AUTONOMOUS if profile.approval in ("auto", "ask") else RUNG_AUTO_WITH_UNDO


def approval_policy_for_session(session_key: str) -> "ToolApprovalPolicy":
    """Resolve a session's tool-approval policy from its SafetyProfile.

    The first production reader of ``SafetyProfile.approval`` — it replaces the
    ad-hoc hardcoded approval pick at the unattended dispatch seams (the gateway's
    heartbeat/background loop) with a value DERIVED from ``profile_for_session``.
    ``ToolApprovalPolicy`` is imported lazily to keep this module importable without
    dragging in ``llm_helpers`` (and to keep the guardrails↔llm layering one-way).

    Mapping from ``profile.approval``:
      * ``auto``       → AUTO_APPROVE
      * ``hook_based`` → HOOK_BASED
      * ``ask``        → HOOK_BASED  — an unattended run has no human to ask, so
        HOOK_BASED keeps the security-hook deny gate rather than auto-approving.
        Interactive paths keep their own interactive-callback flow and never call
        this helper, so ``ask`` reaching here only means a run with no interactive
        callback, where HOOK_BASED is the safe resolution.
    """
    from personalclaw.llm_helpers import ToolApprovalPolicy

    approval = profile_for_session(session_key).approval
    if approval == "auto":
        return ToolApprovalPolicy.AUTO_APPROVE
    return ToolApprovalPolicy.HOOK_BASED
