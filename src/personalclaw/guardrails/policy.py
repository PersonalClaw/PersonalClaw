"""Named safety profiles.

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

``tool_grants`` is enforced by :func:`tool_grant_denial`, which every live tool seam
that owns a read-only decision now asks: the in-process MCP handler
(``mcp_shared.leaf_tool_denial``), the spawn approval loop (``subagent._run_inner``)
and the sandbox tool gateway. Each seam states its posture as a tier through
:func:`tool_grant_posture`, so the operator's ``tools`` ceiling scope narrows a live
tool call instead of being a value nothing reads.

Per-template graduated profiles (a template naming ``coding`` /
``review-only`` / ``cleanup``) arrive when that engine lands and picks the tier per
template; the tier vocabulary and its enforcement are already here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import partial
from typing import TYPE_CHECKING, Any

from personalclaw.constants import DASHBOARD_SESSION_PREFIX, HOOK_SESSION_PREFIX
from personalclaw.guardrails.autonomy import RUNG_AUTO_WITH_UNDO, RUNG_AUTONOMOUS, RUNG_ONE_TAP
from personalclaw.guardrails.budgets import Budget

if TYPE_CHECKING:
    from personalclaw.llm_helpers import ToolApprovalPolicy

# Tool-grant tiers. ``read`` = read-only tools only (default-deny write/execute);
# ``read_write`` = full grant (today's interactive default); ``custom`` = an explicit
# allowlist carried in ``tool_allowlist``. Enforced by :func:`tool_grant_denial` at the
# live tool seams — the in-process MCP handler (``mcp_shared.call_tool_with_logging``),
# the spawn approval loop (``subagent._run_inner``) and the sandbox tool gateway.
TOOL_READ = "read"
TOOL_READ_WRITE = "read_write"
TOOL_CUSTOM = "custom"

#: Every tier the grant algebra recognises, in widening order. Here rather than in a caller
#: because :func:`tool_grant_denial` reads an UNRECOGNISED tier as ``read`` (fail closed) —
#: so a surface that lets a human NAME a tier has to refuse a typo before that clamp grants
#: less than was written, and it must not carry its own copy of this list to do it.
TOOL_TIERS: tuple[str, ...] = (TOOL_READ, TOOL_CUSTOM, TOOL_READ_WRITE)


@dataclass(frozen=True)
class SafetyProfile:
    """A run's safety posture — approval + grants + egress + budget + scan in one object."""

    name: str
    approval: str = "ask"  # auto | hook_based | ask
    tool_grants: str = TOOL_READ_WRITE  # read | read_write | custom
    tool_allowlist: tuple[str, ...] = ()  # meaningful only when tool_grants == custom
    egress_tier: str = "all"  # off | listed | registry | all
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
# posture. The plane a tier is enforced on is every request a run makes through the egress
# guard (`net.policy.egress_policy_for_run`: page fetches, searches, an app's requests, the
# watched-source poll); core has no code-run egress plane (the sandbox providers do not own
# a network namespace). Enforcing "registry" there would deny every unattended fetch that is
# not pypi/npm/crates — i.e. every watched-source poll, every subagent research fetch, every
# inbox-triggered link read — with no UI to undo it. "all" is not "unguarded": it is STRICT
# (public hosts only, no loopback/RFC-1918/link-local, pinned IPs, byte + timeout caps,
# operator deny_hosts honoured). An operator who does want registry-only or allow-list-only
# unattended egress writes it in the governance ceiling
# (`{"scopes": {"egress": {"value": "listed"}}}`), which is enforced.
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

#: Inbound-access session keys — ``inbound:<surface>:<client>`` for the HTTP dialects and
#: ``inbound:cli:<...>`` for headless ``personalclaw run``.
#: A caller reaching in from outside the dashboard is unattended BY DEFINITION: no human
#: is watching the turn, so it resolves through HEADLESS.
#:
#: Deliberately here and NOT in ``session._STATELESS_PREFIXES`` (which the scope names).
#: That list is the PROVIDER-resume/pool axis: a key on it never resumes its ACP session
#: and never claims a warm process. Putting ``inbound:`` there would have silently
#: contradicted its own ``--session`` clause, which exists to let a named headless
#: session CONTINUE a conversation. Unattended and stateless are two different questions;
#: this module answers only the first one.
INBOUND_PREFIX = "inbound:"

#: A webhook's agent turn (``POST /api/hooks/agent``, ``hook:<id>``): an outside system starts it,
#: and nobody is watching it run. The route has always said so ("runs unattended") and nothing
#: here did, so the turn resolved INTERACTIVE, the posture of a chat someone is reading: full
#: tool grants, and each call that needed approval parked on a prompt nobody would see. Unattended
#: and not stateless, like ``inbound:`` above: a registered callback resumes its own context.
_WEBHOOK_PREFIX = HOOK_SESSION_PREFIX

#: Every prefix this module classifies as unattended on top of session.py's own.
_EXTRA_UNATTENDED_PREFIXES = (
    *_LOOP_PREFIXES,
    UNATTENDED_DISPATCH_PREFIX,
    INBOUND_PREFIX,
    _WEBHOOK_PREFIX,
)


def unattended_dispatch_key(origin: str) -> str:
    """The guardrail identity for a sessionless unattended dispatch.

    ``origin`` names WHAT fired (``trigger:<id>``, ``hook:<id>``) so a clamp in the SEL is
    attributable to the automation that caused it. The key is a guardrail identity only —
    it is never used to open or look up a chat session.
    """
    return f"{UNATTENDED_DISPATCH_PREFIX}{(origin or 'unknown').strip()}"


#: The dashboard's provider-key wrapper. ``chat_utils._history_key_for`` turns a chat
#: session's own key into ``dashboard:<key>`` for the provider/history layer, so the
#: guardrail readers downstream of a turn see the WRAPPED form while ``chat_runner``'s
#: own classification reads the bare one.
#:
#: 🔴 ``is_unattended_session("inbound:cli:abc")`` was True while
#: ``is_unattended_session("dashboard:inbound:cli:abc")`` was False, and
#: ``profile_for_session`` on the wrapped key returned INTERACTIVE. A headless CLI turn
#: therefore presented the unattended posture to the one caller that reads
#: ``session.key`` and the ATTENDED posture to every caller downstream of the provider
#: key — the egress tier, the rung ceiling and the denylist among them. Stripping the
#: wrapper is done for the INBOUND family only: a plain ``dashboard:mychat`` still
#: matches nothing after stripping and stays attended, so no interactive session's
#: classification moves.
#:
#: Aliased from ``constants`` rather than re-declared: ``chat_utils`` applies the wrapper
#: and ``usage_ledger`` keys rows by it, so a private copy here would be the same literal
#: a third time, free to drift from the layer that writes it.
_DASHBOARD_WRAPPER = DASHBOARD_SESSION_PREFIX


def is_unattended_session(session_key: str) -> bool:
    """True when ``session_key`` names an unattended run (cron/subagent/channel/inbox/
    side/loop worker, an ``inbound:`` access surface, a webhook's ``hook:`` turn, or a
    sessionless ``unattended:`` dispatch) — the keys that resolve through HEADLESS by
    construction.

    Accepts either the bare session key or the dashboard-wrapped provider form of an
    inbound key (see ``_DASHBOARD_WRAPPER``), so the posture does not depend on which
    layer is asking.
    """
    from personalclaw.session import _STATELESS_PREFIXES

    key = session_key or ""
    if key.startswith(_DASHBOARD_WRAPPER + INBOUND_PREFIX):
        key = key[len(_DASHBOARD_WRAPPER) :]
    return any(key.startswith(p) for p in (*_STATELESS_PREFIXES, *_EXTRA_UNATTENDED_PREFIXES))


def profile_for_session(session_key: str) -> SafetyProfile:
    """Resolve the safety profile for a session key BY CONSTRUCTION.

    An unattended session (cron/subagent/channel/inbox/side/loop, a webhook's turn, or a
    sessionless ``unattended:`` dispatch) resolves to ``HEADLESS`` (read-only default,
    config-layered budget + scan); everything else is the human-watched ``INTERACTIVE`` posture.
    This is the single object the gateway's approval pick consults, replacing the ad-hoc
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


def rung_ceiling_for_profile(profile: SafetyProfile, *, unattended: bool = False) -> str:
    """The highest autonomy rung a run under ``profile`` may reach (AUTONOMY-GUARDRAILS
    §5.2, layered per PLATFORM-HARDENING-FLOORS §5).

    **Two levels, one rule — tightest wins.** The action type's own ceiling is level one;
    this is level two, and it may only NARROW. The composition lives in
    :func:`~personalclaw.guardrails.rungs.route_action_type`, which takes the lower of the
    two, so a profile can never hand a type a rung its declaration refused.

    The ordinal is read off ``profile.approval``, the one profile field that describes how
    much the run may decide alone, and whether anybody is watching (``unattended``):

    * ``auto`` — the operator pre-approved this posture, so nothing here narrows it.
    * ``ask`` on a run someone is watching — they see the result as it lands, so the
      type's own ceiling is the only bound that matters.
    * ``hook_based`` — the UNATTENDED posture: there is no human to ask and no one
      watching. ``autonomous`` (silent, no undo handle) would mean an action ran and left
      no trace a user would notice, so it narrows to ``auto_with_undo`` — execute, but
      keep the reversal handle and the passive notification that let the user find it.
      The route applies this bound only to an action that can be undone
      (``rungs.route_action_type``): one with no handle to keep runs at its own rung, and
      the audit row every execution writes is its trace.
    * ``ask`` on an unattended run — only the operator ceiling puts it there
      (``{"approval": {"value": "ask"}}``: a person decides every action on this machine),
      and nobody is watching to decide. It narrows to ``one_tap``: the action does not run,
      it raises a request for a person. Reading it as "a human is watching" made the ceiling
      LOOSEN an unattended run from ``auto_with_undo`` to ``autonomous``, the direction a
      ceiling must never move: each step down the approval scale is at most as permissive
      as the one above it.

    The INCIDENT posture is not expressed here: ``resolve_rung`` clamps every resolution
    to ``one_tap`` while an incident is active, which outranks both levels.
    """
    if profile.approval == "auto":
        return RUNG_AUTONOMOUS
    if profile.approval == "ask":
        return RUNG_ONE_TAP if unattended else RUNG_AUTONOMOUS
    return RUNG_AUTO_WITH_UNDO


def approval_policy_for_session(session_key: str) -> "ToolApprovalPolicy":
    """Resolve a session's tool-approval policy from its SafetyProfile.

    The first production reader of ``SafetyProfile.approval`` — it replaces the
    ad-hoc hardcoded approval pick at the unattended dispatch seams (the gateway's
    heartbeat/background loop) with a value DERIVED from ``profile_for_session``.
    ``ToolApprovalPolicy`` is imported lazily to keep this module importable without
    dragging in ``llm_helpers`` (and to keep the guardrails↔llm layering one-way).

    Interactive paths keep their own interactive-callback flow and never call this helper,
    so it answers for a run with NO callback: nobody can be asked (:func:`no_one_to_ask`).
    """
    return no_one_to_ask(profile_for_session(session_key).approval)


def no_one_to_ask(approval: str) -> "ToolApprovalPolicy":
    """The tool-approval policy for a run nobody can be asked in, at approval posture *approval*.

    * ``auto``       → AUTO_APPROVE.
    * ``hook_based`` → HOOK_BASED: the operator's hooks deny what they deny and approve what
      they approve, and a call no hook names runs.
    * ``ask``        → REJECT_ALL. A person decides each call, and there is no person to ask,
      so nothing runs. Only the operator ceiling puts ``ask`` on such a run
      (``{"approval": {"value": "ask"}}``); this mapped it to HOOK_BASED, whose default
      approves every call no hook names, so the ceiling had no effect on a heartbeat or an
      unattended announce at all.
    """
    from personalclaw.llm_helpers import ToolApprovalPolicy

    if approval == "auto":
        return ToolApprovalPolicy.AUTO_APPROVE
    if approval == "hook_based":
        return ToolApprovalPolicy.HOOK_BASED
    return ToolApprovalPolicy.REJECT_ALL


# ── tool grants (§3 ``tool_grants``) ──────────────────────────────────────────────


def tool_grant_posture(
    name: str, grants: str, *, allowlist: tuple[str, ...] = ()
) -> "SafetyProfile":
    """A tool-grant posture bounded by the operator CEILING — tightest wins.

    A seam that owns a read-only decision (a research-class spawn, a research-class
    workflow leaf) states that decision HERE as a ``tool_grants`` tier instead of as a
    private boolean, and the ceiling's ``tools`` scope intersects it. That intersection is
    the whole point: ``{"scopes": {"tools": {"allow": [...]}}}`` already parses, validates
    and composes into ``tool_grants="custom"`` + ``tool_allowlist`` (``ceiling.
    _overrides_gate``), so before this seam read it, an operator's tool allowlist narrowed
    nothing at all.

    Mirrors :func:`ceiling_permits_approval`'s probe pattern deliberately: ONE composition
    rule (:func:`~personalclaw.guardrails.ceiling.resolve`), never a second hand-rolled
    comparison that could drift from it.
    """
    from personalclaw.guardrails.ceiling import active_ceiling, resolve

    probe = SafetyProfile(name=name, tool_grants=grants, tool_allowlist=allowlist)
    return resolve(active_ceiling(), probe)


def tool_grant_denial(
    profile: SafetyProfile, tool_name: str, *, write_class: bool, detail: str = ""
) -> str:
    """Why ``profile.tool_grants`` refuses ``tool_name``, or ``""`` when it grants it.

    The ONE answer to "does this posture permit this tool", asked by every live tool seam:
    ``mcp_shared.leaf_tool_denial`` (the in-process MCP handler every tool call funnels
    through), ``subagent._run_inner``'s permission loop, and the sandbox
    :class:`~personalclaw.sandbox_providers.tool_gateway.ToolGateway`.

    ``write_class`` is supplied by the CALLER, not derived here, because each seam holds a
    different view of the call: the leaf, spawn and room seams pass
    ``not task_modes.read_grant_admits(...)`` (what the tool declares — a read, or a
    proposal), and the sandbox gateway passes its ``task_modes.task_mode_denies`` verdict over
    each surface tool's own declaration. This function owns the grant ALGEBRA — which tier
    means what — and nothing else.

    Fail-CLOSED at every edge, because a grant set that cannot be read must deny rather
    than wave through:

    * ``custom`` admits only names its ``tool_allowlist`` matches, by the SAME
      ``name_glob`` matcher the ceiling composed the allowlist with. An empty or
      blank-only allowlist therefore denies EVERYTHING — an unparseable grant set is the
      narrowest posture, not the widest.
    * An unrecognised tier is treated as ``read``. A typo in a profile must not read as
      "full grant".
    * An allowlisted name is served whatever its class: an explicit allowlist entry IS the
      write grant, so ``custom`` never second-guesses the operator who wrote it.
    """
    from personalclaw.guardrails.registries import MATCHER_NAME_GLOB, get_matcher

    grants = str(profile.tool_grants or "").strip()
    name = (tool_name or "").strip()
    suffix = f" — {detail}" if detail else ""
    if grants == TOOL_CUSTOM:
        allow = tuple(str(p).strip() for p in (profile.tool_allowlist or ()) if str(p).strip())
        matcher = get_matcher(MATCHER_NAME_GLOB)
        if any(matcher(name, pattern) for pattern in allow):
            return ""
        return (
            f"{name or '(unnamed tool)'} is not on the {profile.name!r} profile's tool "
            f"allowlist ({', '.join(allow) or 'empty'}){suffix}"
        )
    if grants == TOOL_READ_WRITE:
        return ""
    if write_class:
        tier = grants or "(unset)"
        return (
            f"{name or '(unnamed tool)'} is write-class and the {profile.name!r} profile "
            f"grants {tier!r} tools only{suffix}"
        )
    return ""


def declared_tool_grant_denial(
    profile: SafetyProfile,
    tool_name: str,
    declared: object = "",
    tool_kind: str = "",
    tool_input: object = None,
    *,
    proposes: bool = False,
    tells_owner: bool = False,
    owner_notices: bool = False,
    may_change: tuple[str, ...] = (),
    detail: str = "",
) -> str:
    """:func:`tool_grant_denial` for a call judged by what its tool DECLARES.

    A call is within a ``read`` grant when its tool declares it only reads, or that its only
    effect is a proposal the owner reviews (:func:`personalclaw.task_modes.read_grant_admits`).
    Every seam that holds a declaration asks it here — a research leaf, a research subagent and
    the native runtime it is handed to, a room's critic — so they refuse alike. A call that
    declares nothing is a change, and a ``read`` grant refuses it.

    ``owner_notices`` widens a ``read`` grant by one thing: a call that does nothing but tell the
    owner something (``tells_owner``, ``tool_providers.base.only_tells_the_owner``). Only an
    automation's own agent is granted it (``subagent``): telling the owner what it found is what
    an automation is for, and the owner is the only one such a call reaches.

    ``may_change`` widens it by the files the owner allowed the automation to change
    (``write_scope``): a native file write into one of them is admitted, and a refusal of any
    other change says which files the run may change.
    """
    from personalclaw import write_scope
    from personalclaw.task_modes import read_grant_admits

    within_read = (
        read_grant_admits(declared, tool_name, tool_kind, tool_input, proposes=proposes)
        or (owner_notices and tells_owner)
        or write_scope.admits(tool_name, tool_input, may_change)
    )
    if may_change and not detail:
        detail = f"this run may change only {write_scope.sentence(may_change)}"
    return tool_grant_denial(profile, tool_name, write_class=not within_read, detail=detail)


def _server_not_believed(tool_name: str) -> str:
    """The external MCP server *tool_name* (``mcp/<server>/<tool>``) is a tool of, when the owner
    has not trusted that server's read-only labels (``security.mcp_read_only_servers``); ``""``
    otherwise. Such a server's tools all count as changes whatever they say they do, so a read
    grant refusing one is refusing it for that, not for what the tool does."""
    from personalclaw.tool_providers.registry import MCP_NAMESPACE

    if not tool_name.startswith(MCP_NAMESPACE):
        return ""
    server = tool_name[len(MCP_NAMESPACE) :].partition("/")[0]
    if not server:
        return ""
    from personalclaw.mcp_client import read_only_labels_trusted

    return "" if read_only_labels_trusted(server) else server


#: Why a ``read`` tier refuses a tool, in the words the tier is shown by ("Read-only tools"). Never
#: the grant algebra's own vocabulary ("write-class", a profile's internal name): an agent repeats
#: the reason it was given to the owner, and those words told her nothing she could act on.
READ_ONLY_REASON = "its tools are read-only, and {tool} is not one of them"

#: The same for a shell command: a ``read`` tier is shown the shell, and the command decides.
READ_ONLY_COMMAND_REASON = "its tools are read-only, and this command does more than read"

#: Why, beside :data:`READ_ONLY_REASON`, for a tool of an external MCP server whose read-only labels
#: the owner has not trusted: what it says of itself is believed by no gate, so none of its tools is
#: a read here, and the sentence says where that is decided and what reads her files instead.
UNTRUSTED_SERVER_REASON = (
    "a tool of the MCP server {server} counts as one only once the owner trusts that server's "
    "read-only labels on the Tools page, and the files in the folders the owner shared are read "
    "with read_file, list_dir, glob and grep"
)

#: What a run shown fewer tools than it has is told, beside its tools, of the MCP servers whose
#: tools say they only read and were left out for that alone: what stands between it and them, and
#: where the owner changes it, so it can say so instead of only that it could not read.
UNSHOWN_READS_NOTE = (
    "[tools not shown] {servers} {have} tools that say they only read, and you were not shown "
    "them: a tool of an MCP server counts as a read only once the owner trusts that server's "
    "read-only labels, on the Tools page. If your task needs them, say so in your answer, naming "
    "the server."
)


def shown_of(pool: Iterable[Any], offered: Callable[[str], bool]) -> tuple[list[Any], str]:
    """The tool definitions of *pool* a run held to a narrower tier is shown, those *offered*
    admits by name, and the note naming what it was not shown only because an MCP server's
    read-only labels are not trusted, with where the owner trusts them (:func:`unshown_reads_note`).
    """
    shown: list[Any] = []
    hidden: list[Any] = []
    for tool in pool:
        (shown if offered(str(getattr(tool, "name", "") or "")) else hidden).append(tool)
    return shown, unshown_reads_note(hidden)


def unshown_reads_note(hidden: Iterable[Any]) -> str:
    """The note for the tools a run held to a narrower tier was not shown (*hidden*, tool
    definitions read by their ``name`` and ``annotations``): each MCP server one of whose hidden
    tools says it only reads (``readOnlyHint``), while the owner has not trusted the server's
    labels. ``""`` when there is none."""
    servers = sorted(
        {
            server
            for tool in hidden
            if (getattr(tool, "annotations", None) or {}).get("readOnlyHint") is True
            and (server := _server_not_believed(str(getattr(tool, "name", "") or "")))
        }
    )
    if not servers:
        return ""
    named = ", ".join(servers)
    return UNSHOWN_READS_NOTE.format(
        servers=f"The MCP server {named}" if len(servers) == 1 else f"The MCP servers {named}",
        have="has" if len(servers) == 1 else "have",
    )


def _reads_at_most(profile: SafetyProfile) -> bool:
    """Whether *profile*'s tier is ``read``: any tier but the two wider ones, since
    :func:`tool_grant_denial` reads an unrecognised tier as ``read``."""
    return str(profile.tool_grants or "").strip() not in (TOOL_CUSTOM, TOOL_READ_WRITE)


def offer_refusal(
    profile: SafetyProfile,
    tool_name: str,
    declared: object = "",
    *,
    proposes: bool = False,
    tells_owner: bool = False,
    owner_notices: bool = False,
    may_change: tuple[str, ...] = (),
) -> str:
    """Why a run held to *profile* is not SHOWN *tool_name* at all, or ``""`` when it is.

    Judged on what the tool declares, before any call: a tool is shown exactly when some call to
    it can pass :func:`declared_tool_grant_denial`. At a ``read`` tier those are a tool that
    declares it only reads or only files a proposal; the platform shell, since each command is
    read before it runs and one that only reads passes; a tool whose call can do nothing but tell
    the owner something, for a run granted that (``owner_notices``); and a native file write, for
    a run given files it may change (``may_change``). Anything else could only be refused, so it
    is not shown, and a call to it by a name the model has from elsewhere is refused by the grant,
    which a run held to an offer is held to as well (:func:`granted_call_refusal`).
    """
    from personalclaw import write_scope
    from personalclaw.task_modes import declared_level, is_shell_invocation

    grants = str(profile.tool_grants or "").strip()
    if grants == TOOL_READ_WRITE:
        return ""
    if grants == TOOL_CUSTOM:
        return tool_grant_denial(profile, tool_name, write_class=True)
    if (
        proposes
        or declared_level(declared) == "safe"
        or is_shell_invocation(tool_name, "", declared)
        or (owner_notices and tells_owner)
        or (bool(may_change) and write_scope.writes_a_file(tool_name))
    ):
        return ""
    return READ_ONLY_REASON.format(tool=tool_name or "this tool")


def granted_call_refusal(
    profile: SafetyProfile,
    tool_name: str,
    declared: object = "",
    tool_kind: str = "",
    tool_input: object = None,
    *,
    proposes: bool = False,
    tells_owner: bool = False,
    owner_notices: bool = False,
    may_change: tuple[str, ...] = (),
) -> str:
    """:func:`declared_tool_grant_denial` for one call, said the way the tier is shown.

    A ``read`` tier refuses as outside its read-only tools (:data:`READ_ONLY_REASON`), or, for a
    shell command, as a command that does more than read (:data:`READ_ONLY_COMMAND_REASON`), and
    names the files the run may change when it was given some. A wider tier's refusal (an
    allowlist's) is its own sentence, as :func:`tool_grant_denial` words it.
    """
    from personalclaw import write_scope
    from personalclaw.task_modes import is_shell_invocation

    denial = declared_tool_grant_denial(
        profile,
        tool_name,
        declared,
        tool_kind,
        tool_input,
        proposes=proposes,
        tells_owner=tells_owner,
        owner_notices=owner_notices,
        may_change=may_change,
    )
    if not denial or not _reads_at_most(profile):
        return denial
    if is_shell_invocation(tool_name, tool_kind, declared):
        said = READ_ONLY_COMMAND_REASON
    else:
        said = READ_ONLY_REASON.format(tool=tool_name or "this tool")
        if server := _server_not_believed(tool_name):
            said += f": {UNTRUSTED_SERVER_REASON.format(server=server)}"
    if may_change:
        said += f" — this run may change only {write_scope.sentence(may_change)}"
    return said


@contextmanager
def tool_grants_held(runtime: object, profile: SafetyProfile) -> Iterator[bool]:
    """Hold *runtime*'s tool calls to *profile*'s tool grants for the turn run inside, then give
    it back the grants it held before.

    Asked in the runtime before approval (``NativeAgentRuntime.set_tool_grants``), since an approval
    the runtime answers itself never reaches the host, so a call whose tool the grants do not cover
    is refused whatever would approve it. Restored after, because the runtime is a session's and
    outlives the turn: left in place, one turn's hold would bound the session's own later turns.

    Yields False, holding nothing, when *runtime* cannot be held: an agent CLI runs its tools where
    the host never sees them, so a caller that needs the hold refuses the turn instead.
    """
    hold = getattr(runtime, "set_tool_grants", None)
    if not callable(hold):
        yield False
        return
    prior = getattr(runtime, "tool_grants", None)
    hold(partial(declared_tool_grant_denial, profile))
    try:
        yield True
    finally:
        hold(prior)
