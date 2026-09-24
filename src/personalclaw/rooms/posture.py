"""Per-member safety posture, and the human as the room's sole approver (`AR-6`).

A room exists so that differently-privileged agents can deliberate in one place: a
read-only critic and a tool-bearing executor share a transcript without sharing reach.
That is this module's whole job, and it does it by **instantiating the shipped
Autonomy-Guardrails vocabulary differently per member** rather than minting a room-specific
one — the plan header's soul guardrail applied to safety (`AGENT-ROOMS` §C5). There is no
second safety-profile object here: the type is
:class:`~personalclaw.guardrails.policy.SafetyProfile` and the narrowing is
:meth:`~personalclaw.guardrails.policy.SafetyProfile.with_overrides`.

**The base is resolved, not invented.** A member's posture starts as
``guardrails.policy.profile_for_session("room:<room>:<member>")``, which is ``INTERACTIVE``
by construction (``room:`` is absent from every unattended/stateless prefix tuple — see
``rooms.turn``'s module docstring) with the operator CEILING already intersected in. So a
corrupt ceiling fails the turn closed here, before a member speaks.

**The DEFAULT is the narrow posture, not the room's.** A member that declares nothing runs
at :data:`DEFAULT_MEMBER_TOOL_GRANTS` — ``read`` — because a member added with no stated
reach must be the read-only one. This is the one place where "absence means inherit" would
have been the wrong reading of a safety field, and it is worth naming because the field is
called ``profile_narrowing``: its ABSENCE is the narrowest tier, and the room's own posture
is the CEILING a declaration may reach up to, never the default it falls back on.

**A member may only NARROW, and only on axes this module can actually enforce.**
:data:`MEMBER_AXES` is the closed set and :data:`REFUSED_AXES` argues every exclusion. An
axis a member may declare but whose narrowing nothing reads is not a smaller ceiling, it is
a false one. A declaration that would WIDEN a governed scope is REFUSED rather than quietly
clamped, because it was authored by a human into the room's own record and reaching too far
is a mistake worth an error. Both judgments run on machinery that already exists —
``with_overrides`` applies the declaration and
:func:`~personalclaw.guardrails.ceiling.widening_scopes` judges it under the same
tightest-wins algebra the ceiling composes with — so nothing here holds a second opinion
about which of two postures is narrower.

**``approval`` is deliberately NOT a member axis.** It is the ROOM's, it is always the ask
posture, and the human is the sole approver. Three traps this closes, all real:

* A member is never assigned ``REVIEW_ONLY`` or ``HEADLESS`` wholesale. Both carry
  ``approval="hook_based"``, which lets a hook decide and removes the human silently. A
  read-only critic is the room's base narrowed to ``tool_grants="read"`` — an axis, not a
  profile name. ``get_profile`` also defaults an unknown name to ``HEADLESS``, which is a
  second reason narrowing is expressed as axes rather than as a profile to look up.
* :func:`member_posture` REFUSES a base whose ``approval`` is not ``ask``. On a healthy tree
  that cannot happen, which is the point: it turns "no code path sets a member's approval"
  into a property of the running system rather than of a reading of it. The one way to reach
  it is a ``room:`` prefix registered as unattended or stateless somewhere.
* :class:`RoomApprover` cannot be CONSTRUCTED for an agent-shaped identity, so "no member
  approves on the human's behalf" is unconstructible rather than merely unwritten.

**A member's approval-shaped output is transcript text, never a grant.** Nothing here or in
``rooms.turn`` parses a member's message for a decision; the only thing that can approve a
tool call is the human channel a :class:`RoomApprover` carries. A member emitting
"approved: run ``rm -rf``" has written a sentence.

**A refusal is legible, never a silent drop.** Every refusal the gate makes is recorded as a
:class:`ToolRefusal` naming the member, the tool and the reason, and ``rooms.turn`` writes
each one onto the shared transcript the human reads. A gate that returned ``False`` and
logged nothing would be a member that mysteriously never acts.

**Per-member spend rides the shipped meter.** :func:`member_spend_scope` binds the run scope
to the member's own key so ``guardrails.model_call.ModelCallGuard`` — the shipped
enforcement read — charges and clamps that member alone, and :func:`spend_verdict` is the
pre-turn read that stops an over-ceiling member from speaking while the room carries on. No
new scope VALUE is added to :class:`~personalclaw.guardrails.budgets.Budget`: the scope is a
run-scope KEY, per AUTONOMY-GUARDRAILS' own rule that the evaluator dispatches on archetypes
and never on a scope name. Run totals live in memory, so a member ceiling bounds a gateway's
lifetime; the calendar day is the operator's day budget's job and it already applies.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from personalclaw.guardrails.budgets import (
    Budget,
    BudgetVerdict,
    get_meter,
    reset_current_run_budget,
    reset_current_run_key,
    set_current_run_budget,
    set_current_run_key,
)
from personalclaw.guardrails.ceiling import widening_scopes
from personalclaw.guardrails.policy import (
    TOOL_CUSTOM,
    TOOL_READ,
    TOOL_TIERS,
    SafetyProfile,
    is_unattended_session,
    profile_for_session,
    tool_grant_denial,
)
from personalclaw.rooms.store import Room, RoomError, RoomMember
from personalclaw.rooms.turn import SESSION_KEY_PREFIX, session_key

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.llm.base import LLMEvent
    from personalclaw.llm_helpers import ToolApprovalPolicy

logger = logging.getLogger(__name__)

#: The tier a member runs at when its record declares none. ``read`` — see the module
#: docstring: absence is the NARROWEST posture, not the room's.
DEFAULT_MEMBER_TOOL_GRANTS = TOOL_READ

#: The one approval posture a room member may run under. ``ask`` is the strictest rung on
#: the approval scale, so it is what ``INTERACTIVE`` ∩ any operator ceiling resolves to.
ROOM_APPROVAL = "ask"

#: The axes a member may declare, and the only ones. Every entry is a CAPABILITY field on
#: :class:`~personalclaw.guardrails.policy.SafetyProfile` — what the member may reach — and
#: every entry is READ on a member's turn: ``tool_grants``/``tool_allowlist`` by
#: :func:`approval_channel`'s gate through ``tool_grant_denial``, and ``budget`` by
#: :func:`spend_verdict` plus :func:`member_spend_scope`.
MEMBER_AXES: tuple[str, ...] = ("tool_grants", "tool_allowlist", "budget")

#: Why each :class:`SafetyProfile` axis a caller plausibly reaches for is NOT a member axis.
#: Data rather than prose in one error string, so the refusal an author reads is the same
#: sentence this module argues from — and so admitting an axis means deleting its row here.
#:
#: 🔴 The last three are a MEASURED DEVIATION from `AGENT-ROOMS` §C5, which names all six.
#: Their enforcement points RE-RESOLVE the profile from a session key instead of accepting
#: one — ``web/fetch.py:135`` reads ``profile_for_session(session_key).egress_tier`` and
#: ``guardrails/denylist.py:151-155`` re-resolves for ``denylist_extra`` and
#: ``path_allowlist``, neither taking an injected profile. A member narrowing those would
#: therefore be handed back the ROOM's base at the moment of enforcement, so the declaration
#: would read as binding and bind nothing. Admitting an axis whose narrowing is silently
#: discarded is worse than not offering it, so they are refused until those seams take a
#: profile — the refusal names the reason, so the day one of them does, this row is the fix.
REFUSED_AXES: dict[str, str] = {
    "approval": "the room's approval posture is the human's, never a member's",
    "scan_mode": "a member cannot choose to be scanned less than the operator scans it",
    "egress_tier": (
        "its enforcement point re-resolves the profile from the session key, so a member's "
        "narrowing would be discarded at the moment it mattered"
    ),
    "denylist_extra": (
        "its enforcement point re-resolves the profile from the session key, so a member's "
        "narrowing would be discarded at the moment it mattered"
    ),
    "path_allowlist": (
        "its enforcement point re-resolves the profile from the session key, so a member's "
        "narrowing would be discarded at the moment it mattered"
    ),
}

#: The axis carrying a tuple of patterns, normalised from whatever JSON the record holds.
_TUPLE_AXES = ("tool_allowlist",)

#: The axis carrying a plain string.
_STRING_AXES = ("tool_grants",)

#: The numeric keys of the ``budget`` axis, matching :class:`Budget`'s own fields.
_BUDGET_KEYS = ("max_tokens", "max_dollars")


# ── the declaration ────────────────────────────────────────────────────────


def parse_narrowing(raw: object) -> dict[str, Any]:
    """Validate a member's declared narrowing into ``with_overrides`` kwargs.

    Shape only — whether the values actually narrow is :func:`member_posture`'s question,
    because that needs the base they are declared against. Refuses (fail CLOSED) rather than
    dropping what it cannot read: a safety field silently ignored is a member running with
    more reach than its author wrote down.

    An absent or empty declaration is ``{}``, which is the common case and not an error. Note
    that ``{}`` does NOT mean "the room's posture" — :func:`member_posture` applies
    :data:`DEFAULT_MEMBER_TOOL_GRANTS` to it.
    """
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise RoomError(
            "room_member_posture_invalid",
            "profile_narrowing must be an object of safety axes.",
        )
    unknown = sorted(k for k in raw if k not in MEMBER_AXES)
    if unknown:
        # A refused axis gets its OWN reason, because the axes an author most plausibly
        # reaches for are the excluded ones rather than a misspelling, and "not a member
        # axis" alone reads as an oversight in this list instead of a decision about it.
        reason = next((REFUSED_AXES[k] for k in unknown if k in REFUSED_AXES), "")
        raise RoomError(
            "room_member_posture_invalid",
            f"profile_narrowing names {', '.join(unknown)}, which is not a member axis"
            + (f": {reason}" if reason else "")
            + f". Use one of: {', '.join(MEMBER_AXES)}.",
        )
    out: dict[str, Any] = {}
    for axis in _STRING_AXES:
        if axis in raw:
            value = raw[axis]
            if not isinstance(value, str) or not value.strip():
                raise RoomError(
                    "room_member_posture_invalid", f"{axis} must be a non-empty string."
                )
            out[axis] = value.strip()
    for axis in _TUPLE_AXES:
        if axis in raw:
            value = raw[axis]
            if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
                raise RoomError("room_member_posture_invalid", f"{axis} must be a list of strings.")
            entries = tuple(p.strip() for p in value if p.strip())
            if not entries:
                raise RoomError(
                    "room_member_posture_invalid",
                    f"{axis} is empty, which one reader would take as 'no restriction' and "
                    "another as 'deny everything'. Omit it instead.",
                )
            out[axis] = entries
    if "budget" in raw:
        out["budget"] = _parse_budget(raw["budget"])
    _check_tool_axes(out)
    return out


def _check_tool_axes(out: dict[str, Any]) -> None:
    """The two ways a tool declaration is internally incoherent, both refused.

    A typo'd tier and an allowlist under the wrong tier are one defect wearing two faces: a
    posture its author reads as tight that the grant algebra reads as something else.
    ``tool_grant_denial`` is fail-closed about both — an unrecognised tier is treated as
    ``read``, and an allowlist is consulted ONLY under ``custom`` — so neither is a security
    hole. They are worse than a hole in one specific way: the author is told nothing, and a
    declaration nobody reads is how a room ends up with a member everyone believes is
    restricted. So they are refused where the author can still see the refusal.

    ``custom`` with no allowlist is NOT one of these: it denies every tool, which is a
    coherent posture for a member that should reason and not act, and it is already what the
    grant algebra does with an empty list.
    """
    tier = out.get("tool_grants")
    if tier is not None and tier not in TOOL_TIERS:
        raise RoomError(
            "room_member_posture_invalid",
            f"tool_grants must be one of {', '.join(TOOL_TIERS)}, not {tier!r} — an "
            "unrecognised tier is read as the narrowest one, which is not what a typo means.",
        )
    if "tool_allowlist" in out and tier != TOOL_CUSTOM:
        raise RoomError(
            "room_member_posture_invalid",
            f"a tool_allowlist is only consulted under tool_grants={TOOL_CUSTOM!r}; declare "
            "that tier alongside it, or drop the allowlist — as written it would restrict "
            "nothing.",
        )


def _parse_budget(raw: object) -> Budget:
    """A member's declared ceiling.

    Zero in a dimension means "inherit" — :class:`Budget`'s own convention, and why a
    negative value is refused rather than clamped: ``-1`` reads as unlimited under that
    convention, so accepting it would turn a typo into a removed ceiling. Which value is
    inherited is :func:`member_posture`'s job, because only it holds the base.
    """
    if not isinstance(raw, dict):
        raise RoomError(
            "room_member_posture_invalid",
            f"budget must be an object with {' and '.join(_BUDGET_KEYS)}.",
        )
    unknown = sorted(k for k in raw if k not in _BUDGET_KEYS)
    if unknown:
        raise RoomError(
            "room_member_posture_invalid",
            f"budget names {', '.join(unknown)}; it holds {', '.join(_BUDGET_KEYS)}.",
        )
    values: dict[str, float] = {}
    for key in _BUDGET_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RoomError("room_member_posture_invalid", f"budget.{key} must be a number.")
        if value < 0:
            raise RoomError(
                "room_member_posture_invalid",
                f"budget.{key} cannot be negative — 0 already means 'no ceiling of my own'.",
            )
        values[key] = value
    return Budget(
        max_tokens=int(values.get("max_tokens", 0)),
        max_dollars=float(values.get("max_dollars", 0.0)),
    )


# ── the resolved posture ───────────────────────────────────────────────────


def member_posture(key: str, member: RoomMember) -> SafetyProfile:
    """The effective :class:`SafetyProfile` for one member's turn.

    *key* is the member's own session key (``room:<room>:<member>``) — the same identity that
    holds its provider session and meters its spend, so there is one string to reason about
    rather than three that have to agree.

    Order matters and each step is load-bearing: resolve the base (ceiling included), assert
    the room still owns ``approval``, apply the restrictive DEFAULT, inherit the budget
    dimensions the member left at 0, then refuse anything that widened.
    """
    base = profile_for_session(key)
    if base.approval != ROOM_APPROVAL:
        # Unreachable on a healthy tree — and that is the point. The one way here is a
        # resolution that stopped being INTERACTIVE, which means a `room:` prefix was
        # registered as unattended or stateless. That change would hand every member a
        # hook-approved posture, so the turn fails instead of quietly proceeding.
        raise RoomError(
            "room_member_posture_invalid",
            f"the room posture for {member.name!r} resolved to approval {base.approval!r}, "
            f"not {ROOM_APPROVAL!r} — the human must stay the approver, so this turn is "
            "refused. A 'room:' session prefix has been registered as unattended or "
            "stateless.",
        )
    narrowing = parse_narrowing(member.profile_narrowing)
    narrowing.setdefault("tool_grants", DEFAULT_MEMBER_TOOL_GRANTS)
    declared_budget = narrowing.get("budget")
    if declared_budget is not None:
        # "0 means inherit" resolved against the base, which is the only place that knows
        # what is being inherited. Without this a member declaring ONLY max_tokens would
        # read as dropping the operator's dollar ceiling to unlimited — `_tighter_cap`
        # treats 0 as unlimited — and `widening_scopes` would refuse a legitimate ceiling.
        narrowing["budget"] = Budget(
            max_tokens=declared_budget.max_tokens or base.budget.max_tokens,
            max_dollars=declared_budget.max_dollars or base.budget.max_dollars,
        )
    candidate = base.with_overrides(name=f"room_member:{member.name}", **narrowing)
    wider = widening_scopes(base, candidate)
    if wider:
        raise RoomError(
            "room_member_posture_widens",
            f"{member.name!r} declares a posture wider than the room's on "
            f"{', '.join(wider)}. A member may only narrow: state what it may NOT do.",
        )
    return candidate


def describe_members(room: Room) -> list[dict]:
    """Every member's RESOLVED posture, for the human and for `AR-8`'s UI.

    The declaration is already on the wire (it is a ``RoomMember`` field); this is the
    answer, which is the part a reader cannot compute — it folds in the restrictive default
    and the operator ceiling. Without it "this member is read-only" is a claim the UI would
    have to re-derive.

    A member whose declaration WIDENS reports its refusal instead of resolving. That state is
    reachable without anybody editing the room: :func:`~personalclaw.rooms.store.add_member`
    validates shape at write time, while widening is judged against a base the operator's
    ceiling can tighten afterwards. So this read is per-member tolerant — one unusable
    declaration must not blank the roster — while the TURN stays fail-closed on the same
    member (:func:`member_posture` raises there). Refusing to display is not safety; refusing
    to run is.
    """
    out: list[dict] = []
    for member in room.members:
        row: dict[str, Any] = {"name": member.name, "declared": dict(member.profile_narrowing)}
        try:
            profile = member_posture(session_key(room.id, member.name), member)
        except RoomError as exc:
            row["refused"] = exc.code
            row["detail"] = exc.message
            out.append(row)
            continue
        row.update(
            approval=profile.approval,
            tool_grants=profile.tool_grants,
            tool_allowlist=list(profile.tool_allowlist),
            budget={
                "max_tokens": profile.budget.max_tokens,
                "max_dollars": profile.budget.max_dollars,
            },
        )
        out.append(row)
    return out


# ── the approval channel: the human, or nobody ─────────────────────────────


def agent_shaped_identity(identity: str) -> str:
    """Why *identity* is an agent's rather than the human's, or ``""`` when it is the human's.

    **The human is identified by PREFIX ABSENCE** — the same mechanism that makes a room's
    base posture INTERACTIVE. There is no ``is_human`` flag to forge: an identity is the
    human's exactly when it carries no agent-session prefix at all.

    Built on :func:`~personalclaw.guardrails.policy.is_unattended_session` rather than on a
    private copy of the prefix tuples, so the two answers cannot drift — plus the one case
    that predicate deliberately answers ``False`` for. ``room:`` is absent from both prefix
    tuples BY DESIGN (that absence is what keeps the human the approver), which means a
    member's own session key reads as attended and would otherwise pass as human. It is the
    single most important identity to refuse here, so it is checked explicitly.

    An empty identity is refused: "nobody in particular" must not resolve to the human.
    """
    key = (identity or "").strip()
    if not key:
        return "an empty identity names nobody, and nobody is not the human"
    if key.startswith(SESSION_KEY_PREFIX):
        return f"{key!r} is a room member's own session key"
    if is_unattended_session(key):
        return f"{key!r} carries an unattended session prefix"
    return ""


@dataclass(frozen=True)
class RoomApprover:
    """The human, and their answer channel. Unconstructible for an agent-shaped identity.

    Identity and callback travel together because they are one fact: *who* is approving and
    *how they are asked*. Validating in ``__post_init__`` is what turns "no member approves on
    the human's behalf" into an unconstructible state rather than a rule some future caller
    has to remember — the same reason :func:`approval_channel` hands back its policy and its
    gate as one value.
    """

    identity: str
    decide: Callable[[LLMEvent], Awaitable[bool]]

    def __post_init__(self) -> None:
        reason = agent_shaped_identity(self.identity)
        if reason:
            raise RoomError(
                "room_approver_not_human",
                f"Only the human may approve a room member's tool call, and {reason}. "
                "The room has one approver and no member may stand in for them.",
            )


@dataclass(frozen=True)
class ToolRefusal:
    """One refused tool call, with enough to tell the human what happened and why."""

    member: str
    tool: str
    reason: str

    def sentence(self) -> str:
        """The transcript line. Names the member, the tool and the reason, in that order."""
        return f"{self.member} was refused {self.tool or 'an unnamed tool'} — {self.reason}"


#: Why a tool is refused when no approval channel is bound to the turn. A constant because
#: the test that asserts a refusal is legible and the code that produces it must agree on
#: the sentence, and because it is the reason `AR-8` exists to remove.
NO_APPROVER_REASON = (
    "only the human may approve a room member's tool call and no approval channel is bound "
    "to this turn, so there is nobody to ask"
)


def approval_channel(
    member: RoomMember,
    profile: SafetyProfile,
    approver: "RoomApprover | None",
    *,
    record: Callable[[ToolRefusal], None],
) -> tuple[ToolApprovalPolicy, Callable[[LLMEvent], Awaitable[bool]]]:
    """The tool-approval policy and the gate for one member's turn, as ONE value.

    Returned together because they are one decision and the unsafe combination is exactly the
    one a caller could otherwise assemble: ``HOOK_BASED`` with no callback falls through
    ``llm_helpers._resolve_permission`` to "Default: auto-approve". Handing back both makes
    that state unconstructible at the call site.

    The policy is ``HOOK_BASED`` with **no hook manager bound** — which is what the ask
    posture maps to (``guardrails.policy.approval_policy_for_session``) while keeping the hook
    branch out of the decision, because a hook verdict of ``TOOL_AUTO_APPROVE`` approves
    without asking and would remove the human from the one surface whose defining property is
    that they are in it. With ``hooks=None`` that branch is skipped and the gate below is the
    whole decision.

    The gate asks the two questions a solo session asks, in that order:

    1. **May this member use this tool at all?** ``tool_grant_denial`` against the member's
       own tier — the shipped grant ALGEBRA, asked exactly as every other live tool seam asks
       it. A read-only critic's write tool is refused HERE without troubling the human: the
       grant question precedes the approval question, as ``chat_runner``'s task-mode gate runs
       before its approval card.
    2. **Does the human approve?** Only they can, and only through *approver*. With none bound
       there is nobody to ask, so the call is refused — the same answer a solo session gives
       when the prompt is never answered. `AR-8` binds the channel; that is a missing CALLER,
       not a missing rule, which is why there is one gate here and not two paths.

    Every ``False`` is handed to *record* on its way out, which is what makes the refusal
    legible instead of a member that mysteriously never acts.
    """
    from personalclaw.llm_helpers import ToolApprovalPolicy
    from personalclaw.workflows.batch_compile import is_write_tool

    async def gate(event: "LLMEvent") -> bool:
        title = getattr(event, "title", "") or ""
        denial = tool_grant_denial(profile, title, write_class=is_write_tool(title))
        if denial:
            record(ToolRefusal(member.name, title, denial))
            return False
        if approver is None:
            record(ToolRefusal(member.name, title, NO_APPROVER_REASON))
            return False
        if not await approver.decide(event):
            record(
                ToolRefusal(member.name, title, f"{approver.identity} declined it for this member")
            )
            return False
        return True

    return ToolApprovalPolicy.HOOK_BASED, gate


# ── per-member spend ───────────────────────────────────────────────────────


def spend_verdict(key: str, profile: SafetyProfile) -> tuple[BudgetVerdict, str]:
    """Where this member stands against its OWN ceiling, before it takes a turn.

    ``EXCEEDED`` means this member stops speaking and the room and its other members carry on
    — a shared ceiling would make one member's spend everybody's silence, which is the
    opposite of what a per-member budget is for. ``WARN`` (80%) is surfaced and stops nothing.
    """
    if profile.budget.is_unlimited:
        return BudgetVerdict.OK, ""
    return get_meter().check_run(key, profile.budget)


@contextlib.contextmanager
def member_spend_scope(key: str, profile: SafetyProfile) -> Iterator[None]:
    """Charge and clamp every model call inside this block to *key*'s own ceiling.

    Shaped after ``guardrails.audit.caller_scope`` (sync, token-scoped) for the same reasons:
    a ContextVar set in a coroutine is visible to everything it awaits, and a token restores
    the parent binding instead of clearing it.

    A context manager rather than a bare pair of calls because the reset MUST happen: a leaked
    run key charges the next member's tokens to this one, and a leaked budget applies this
    member's ceiling to the next. Both resets are no-raise by contract
    (``budgets.reset_current_run_key``), so teardown cannot replace a provider's error with a
    bookkeeping one.

    Binding the budget as well as the key is what makes the ceiling bite MID-turn:
    ``ModelCallGuard`` reads ``current_run_budget()`` on every call and raises
    ``BudgetExceededError`` once the run total passes it. Without it the key would only accrue
    a total :func:`spend_verdict` reads on the NEXT turn.
    """
    key_token = set_current_run_key(key)
    budget_token = set_current_run_budget(profile.budget)
    try:
        yield
    finally:
        reset_current_run_budget(budget_token)
        reset_current_run_key(key_token)
