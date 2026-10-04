"""Stage 3.5 — trivial-tier auto-execution, quadruple-bounded.

The sharpest edge in the plan: the point where a model's proposal becomes a write nobody
watched. §1.6 bounds it four ways, and all four are enforced HERE rather than requested in a
prompt, because a bound a prompt asks for is a bound an injected inbox item can ask to skip:

1. **The switch.** ``proactive.auto_execute_enabled`` is off by default. Off means this stage
   dispatches nothing, reads no budget, and returns every proposal deferred — the plan's
   one-click revoke, and the reason it is checked first is that a revoked switch must not even
   spend a store read.
2. **The frozen capability set** (substrate decision 7). A proposal names an ``action_type``,
   never a provider; :data:`PROVIDER_FOR_ACTION` is the only thing that turns one into a
   dispatch, and the provider it names must ALSO be in the caller's declared capability set.
   Two independent gates, so neither an unmapped action nor an undeclared provider can execute.
3. **The per-run cap.** ``max_auto_actions_per_run`` (default 5). The rest queue pending
   regardless of tier — a hundred trivial archives is still a hundred unattended writes.
4. **The NEW-1 budget floor**, consulted before EVERY action rather than once per run: a run
   that starts under its ceiling can cross it mid-flight, and a single check at the top would
   authorise the whole batch on the strength of the cheapest moment in it.

Eligibility itself is the fifth bound and the one that makes the other four meaningful: only a
``trivial``-tier proposal or one a taught always-approve rule matched is a candidate. Tier is
policy-clamped upstream (`proposals.clamp_tier` only ever RAISES), so "a jailbroken item cannot
self-assign trivial" holds by construction and this module never re-reads the model's ask.

**The budget check fails CLOSED here, unlike `triggers/screen.py`'s.** That module's budget gate
fails OPEN, correctly: it decides whether a trigger FIRES AT ALL, and a hung probe that stopped
every automation on the machine would be a worse outage than one unverified fire. This gate
decides whether an unattended WRITE happens, and its fallback is not an outage — the proposal
queues pending and the user sees it in the digest, which is exactly where it would have been
anyway. Nothing is lost by refusing, so refusing is the honest direction.

Nothing here reads the clock or the config: ``now``, ``cap`` and ``enabled`` are parameters, so
a test drives the whole ladder without a config file and the caller stays the only place that
decides what "today" and "the ceiling" mean.

**Your answer runs here too, and it is attended.** A Yes on the digest (its card, or a reply on the
chat channel it reached: ``proactive.answer``) dispatches through this stage with ``answered=True``,
so the capability set, the item's lane, the spend floor and the action denylist hold it as they hold
everything else. Two gates do not, because both are about work nobody answered: the incident kill
switch suspends unattended work, and the operator ceiling's ``ask`` refuses the auto-execute grant
that approves without asking. A person answered, so the answer needs no grant, and incident mode
treats it as it treats every attended action (a chat's tool calls, a press in the Inbox): it runs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from personalclaw.proactive.approval import ApprovalRule, Decision, match_rules
from personalclaw.proactive.manifest import SOURCE_INBOX, Manifest
from personalclaw.proactive.proposals import TIERS, Proposal, bind_arguments

logger = logging.getLogger(__name__)

#: The trivial rung — the only tier that auto-executes without a taught rule.
TRIVIAL_TIER = TIERS[0]

#: ``action_type`` → the action provider that performs it. **A proposal can never name a
#: provider**; it names one of `proposals.ACTION_TYPES`, and this table is the only thing that
#: turns one into a dispatch. Every kind but `none` (the model saying there is nothing to do) is
#: here, because each is a Yes on the digest card: a kind with no entry would be a button that
#: can only fail, so it is not in the action set at all.
PROVIDER_FOR_ACTION: dict[str, str] = {
    "archive": "inbox-op",
    "mute_thread": "inbox-op",
    "dismiss": "inbox-op",
    "reply_draft": "inbox-op",
    "create_task": "create-task",
}

#: The providers whose actions may be dispatched UNATTENDED by default. Just `inbox-op`, and
#: the narrowness is §1.6 bound 2: external-reach actions are not in the trivial-capable set,
#: so even a taught always-approve rule for `reply_draft` reaches a provider that can only
#: write a draft. A caller that declares a wider set on its own node widens it deliberately.
AUTO_CAPABLE_PROVIDERS: frozenset[str] = frozenset({"inbox-op"})


def answer_capabilities(action_type: str) -> frozenset[str]:
    """What her Yes on one proposal admits: the provider of the proposal's own kind, and only it.

    Not the digest's unattended set: that set bounds what runs with nobody watching, and filing a
    task is not in it, so a Yes on "File a task" run under it was refused. Nor anything wider: a
    Yes on an archive admits no task list, whatever the proposal's config says.
    """
    provider = PROVIDER_FOR_ACTION.get(action_type, "")
    return frozenset({provider}) if provider else frozenset()


#: The lane an `inbox-op` action can address. A `run`-lane or `channel`-lane item has no inbox
#: row to archive, and dispatching against its `source_id` would name someone else's row.
_INBOX_OP_LANE = SOURCE_INBOX

# ── why a proposal did not auto-execute (a closed vocabulary, so a ledger reader counts) ──

SKIP_DISABLED = "auto_execute_disabled"
SKIP_NO_PROVIDER = "no_auto_provider"
SKIP_NOT_CAPABLE = "outside_capability_set"
SKIP_WRONG_LANE = "wrong_lane"
SKIP_UNKNOWN_ITEM = "unknown_item_id"
SKIP_DENIED = "denied_by_rule"
SKIP_SUPPRESSED = "suppressed"
SKIP_NEEDS_YOU = "needs_you"
SKIP_CAP = "over_run_cap"
SKIP_BUDGET = "skipped_budget"
#: The dispatch ran and the provider reported a failure (or raised). The same token as the
#: `auto_failed` ledger kind, as `skipped_budget` is its kind's: one word for one fact.
SKIP_FAILED = "auto_failed"
#: The two PLATFORM gates, above the four. This module is a fifth UNATTENDED dispatch seam
#: (AUTONOMY-GUARDRAILS §1.2), so it carries the kill switch and the action denylist like the
#: other four — a digest that kept archiving through an incident would be the quiet exception
#: that makes the kill switch useless. The kill switch holds only what nobody answered: your Yes
#: is attended work, which incident mode leaves running, as it leaves a chat's tool calls.
SKIP_INCIDENT = "incident_active"
SKIP_DENYLIST = "denied_by_denylist"
#: The operator ceiling says a person decides every action on this machine
#: (``{"approval": {"value": "ask"}}``). Auto-execution is a grant (`approval_grants`) like any
#: other: neither the trivial tier nor a taught always-approve rule loosens the ceiling. Your Yes
#: is a person deciding, so it asks for no grant and is never refused for one.
SKIP_CEILING = "refused_by_ceiling"

#: The rule name a trivial-tier execution with no taught rule behind it records. The ledger row
#: must ALWAYS name what authorised the action (§1.6 bound 4), and "the tier floor policy" is a
#: real answer — an empty `rule` field would read as a taught rule whose key went missing.
TIER_POLICY_RULE = "policy:trivial-tier"

#: The `ActionContext.event` every auto-executed action carries. Its own name, not `clock`: the
#: SEL row, the denylist audit and a provider's own logs all read it, and "the triage digest did
#: this on its own" is the one thing a reader of any of those three needs to be able to tell.
AUTO_EXEC_EVENT = "triage_auto_execute"


@dataclass(frozen=True)
class AutoAction:
    """One proposal that was dispatched AND landed, with what authorised it and how to undo it.

    Only a dispatch the provider reported as a success becomes one. A failed dispatch is a
    :class:`DeferredProposal` with :data:`SKIP_FAILED` and the provider's reason, so nothing that
    reads ``executed`` can list or count a failure as an action taken.
    """

    proposal: Proposal
    provider: str
    #: The inbox/source id the ordinal resolved to. What the undo and the digest link name.
    source_id: str
    #: The taught rule's key, or :data:`TIER_POLICY_RULE`. Never empty.
    rule: str
    rule_pattern: str = ""
    #: The provider's opaque undo handle. Empty when the provider had nothing to reverse
    #: (the effect already held), which is recorded rather than papered over.
    reversal: str = ""

    @property
    def undoable(self) -> bool:
        return bool(self.reversal)


@dataclass(frozen=True)
class DeferredProposal:
    """A proposal that stayed pending, and the closed-vocabulary reason it did."""

    proposal: Proposal
    reason: str
    detail: str = ""
    #: The rule that deferred it, when a rule did (deny/suppress). Empty otherwise.
    rule: str = ""


@dataclass(frozen=True)
class AutoExecResult:
    executed: tuple[AutoAction, ...] = ()
    deferred: tuple[DeferredProposal, ...] = ()
    #: True when the NEW-1 floor refused mid-run. The remaining proposals are in `deferred`
    #: with `SKIP_BUDGET`, so this flag is a summary of them, never a substitute.
    budget_breached: bool = False
    budget_reason: str = ""
    #: Ledger rows written (0 when the caller supplied no run context).
    ledger_rows: int = 0

    def summary(self) -> dict:
        """The flat, JSON-safe shape a ledger row, an action result and the digest all read."""
        return {
            "auto_executed": [
                {
                    "item_id": a.proposal.item_id,
                    "source_id": a.source_id,
                    "action_type": a.proposal.action_type,
                    "provider": a.provider,
                    "rule": a.rule,
                    "reversal": a.reversal,
                    "undoable": a.undoable,
                }
                for a in self.executed
            ],
            "auto_deferred": [
                {
                    "item_id": d.proposal.item_id,
                    "action_type": d.proposal.action_type,
                    "tier": d.proposal.tier,
                    "reason": d.reason,
                    "rule": d.rule,
                    # What failed or held it, in the provider's or the guard's words. The digest
                    # card says it from here (`not_done_note`); without it a failure reads as a
                    # proposal nobody tried.
                    "detail": d.detail,
                }
                for d in self.deferred
            ],
            "budget_breached": self.budget_breached,
            "budget_reason": self.budget_reason,
            "auto_ledger_rows": self.ledger_rows,
        }

    @property
    def pending(self) -> tuple[Proposal, ...]:
        """The proposals the digest must still show under "what needs you"."""
        return tuple(d.proposal for d in self.deferred)


#: ``(provider_name, action_config, ctx) -> result``. The result is read by attribute
#: (`success` / `reversal` / `error`) rather than typed, so this module needs no import from
#: `action_providers` and a test injects a plain object. The `ctx` is the SAME object the
#: denylist gate inspected — a gate that screened one context while the provider executed
#: against another would be screening something that never ran.
DispatchFn = Callable[[str, dict, Any], Awaitable[Any]]
#: ``() -> (breached, reason)``. Consulted before EVERY action.
BudgetCheckFn = Callable[[], tuple[bool, str]]
#: ``(kind, fields) -> None``. No-op by default; the caller supplies the run's journal.
LedgerFn = Callable[[str, dict], None]


async def _default_dispatch(provider_name: str, action_config: dict, ctx: Any) -> Any:
    from personalclaw.action_providers.base import ActionResult
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    provider = get_action_provider(provider_name)
    if provider is None:
        return ActionResult(
            success=False,
            error=f"auto-execute: action provider {provider_name!r} is not registered",
        )
    return await provider.execute(action_config, ctx)


def _make_context(action_config: dict) -> Any:
    from personalclaw.action_providers.base import ActionContext

    return ActionContext(event=AUTO_EXEC_EVENT, payload=dict(action_config))


def default_budget_check(run_key: str = "") -> BudgetCheckFn:
    """The NEW-1 floor, bound to a run. Day scope always; run scope when there is a run.

    Fails CLOSED — see this module's docstring for why that diverges from
    `triggers/screen.py`'s deliberate fail-open. A probe that raises returns
    ``(True, "…could not be verified…")``, so the proposals queue pending with a reason the
    user can read rather than executing on an unverified ceiling.

    🔑 THAT REFUSAL WAS UNREACHABLE FOR THE COMMONEST CAUSE UNTIL #3458. It is written here
    and it is right, but ``budget_from_config`` used to swallow a failed config read and
    hand back an unlimited budget, which reads as *under* the ceiling — so a lost ceiling
    took the happy path and nothing raised. The builder now raises
    :class:`~personalclaw.guardrails.budgets.BudgetConfigUnreadable`, which is what makes
    this handler fire. The code here did not change; its precondition did.
    """

    def check() -> tuple[bool, str]:
        try:
            from personalclaw.guardrails.budgets import (
                BudgetVerdict,
                budget_from_config,
                get_meter,
                run_budget_from_config,
            )

            meter = get_meter()
            # The token ceilings only: a spent dollar one refuses the calls that cost money where
            # they are made, and a proposal whose work costs nothing still runs.
            verdict, reason = meter.check_day_before_work(budget_from_config())
            if verdict is BudgetVerdict.EXCEEDED:
                return True, reason
            if run_key:
                verdict, reason = meter.check_run_before_work(run_key, run_budget_from_config())
                if verdict is BudgetVerdict.EXCEEDED:
                    return True, reason
        except Exception as exc:  # noqa: BLE001 - an unverified ceiling authorises nothing
            logger.warning("auto-execute: budget check failed", exc_info=True)
            return True, f"the budget could not be verified ({type(exc).__name__}), so nothing ran"
        return False, ""

    return check


def _action_config(proposal: Proposal, item: Any) -> dict:
    """The provider payload for `proposal`, bound to the item its ordinal resolved to.

    The ordinal→id resolution happens HERE and only here. A proposal's `item_id` is a manifest
    ordinal, never a store id, so a dispatch that forwarded it unchanged would address an inbox
    row named "3".

    Only the arguments the kind declares are read (`proposals.bind_arguments`), whatever the
    proposal carries, so no caller's proposal names the provider, the operation, the item or the
    task list the action reaches. A reply binds none: with no text given, `inbox-op` drafts it
    through the product's own drafting path. A task's title is passed as a VALUE under a fixed
    template, so the title (the proposal's, else the item's own words) is filed as written and
    never read as a template of its own.
    """
    arguments, _unbound = bind_arguments(proposal.action_type, proposal.action_config)
    config: dict[str, Any] = dict(arguments)
    config["op"] = proposal.action_type
    config["action_type"] = proposal.action_type
    config["item_id"] = item.source_id
    if proposal.action_type == "create_task":
        config["title"] = arguments.get("title") or item.title or f"Follow up: {item.source_id}"
        config["title_template"] = "$title"
    return config


async def auto_execute(
    proposals: Sequence[Proposal],
    *,
    manifest: Manifest,
    rules: Sequence[ApprovalRule] = (),
    now: datetime,
    enabled: bool,
    cap: int,
    capabilities: Sequence[str] | frozenset[str] = AUTO_CAPABLE_PROVIDERS,
    session_key: str = "",
    dispatch: DispatchFn | None = None,
    budget_check: BudgetCheckFn | None = None,
    ledger: LedgerFn | None = None,
    answered: bool = False,
) -> AutoExecResult:
    """Run the eligible proposals, defer the rest, and record BOTH.

    Zero silent drops is the contract (criterion 4): every proposal comes back either in
    ``executed`` (it landed) or in ``deferred`` with a reason (a failed dispatch included), and
    the counts always reconcile with the input. A caller that supplies `ledger` also gets one row
    per outcome.

    ``answered`` says the owner answered these proposals (their Yes): attended work. It runs in
    incident mode and under an ``ask`` ceiling, which suspend and refuse only what nobody
    answered, and everything else here holds it as it holds the digest's own run. Off, the
    default, is the digest acting on its own, held by both.
    """
    from personalclaw.ledger.kinds import AUTO_EXECUTED, AUTO_FAILED, SKIPPED_BUDGET

    executed: list[AutoAction] = []
    deferred: list[DeferredProposal] = []
    rows = 0
    breached = False
    breach_reason = ""

    def write(kind: str, fields: dict) -> None:
        nonlocal rows
        if ledger is None:
            return
        try:
            ledger(kind, fields)
        except Exception:  # noqa: BLE001 - a ledger failure must not undo a landed action
            logger.warning("auto-execute: ledger row %s failed", kind, exc_info=True)
            return
        rows += 1

    if not enabled:
        return AutoExecResult(
            deferred=tuple(
                DeferredProposal(proposal=p, reason=SKIP_DISABLED, detail="auto-execution is off")
                for p in proposals
            ),
        )

    from personalclaw.guardrails.denylist import enforce_action
    from personalclaw.guardrails.incident import incident_active

    if not answered and incident_active():
        # FAIL-CLOSED, and before the loop: the kill switch exists to suspend unattended work,
        # and a digest that kept archiving through an incident would be the quiet exception that
        # makes it useless. The proposals still come back — pending, with the reason — so the
        # user sees an incident as a deferral rather than as a digest that went silent. Her own
        # answer is not unattended work: it runs, as a chat's tool calls run in incident mode.
        return AutoExecResult(
            deferred=tuple(
                DeferredProposal(
                    proposal=p,
                    reason=SKIP_INCIDENT,
                    detail="incident mode is active — unattended auto-execution is suspended",
                )
                for p in proposals
            ),
        )

    from personalclaw import approval_grants

    if not answered and not approval_grants.stands(
        approval_grants.AUTO_EXECUTE,
        caller=session_key or "proactive:auto_execute",
        subject=f"proposals={len(proposals)}",
    ):
        # Before the loop, like the kill switch: every proposal comes back pending with the
        # reason, so the digest shows what waits for you rather than going quiet. Her answer asks
        # for no grant: the grant approves what nobody answered, and she answered.
        return AutoExecResult(
            deferred=tuple(
                DeferredProposal(
                    proposal=p,
                    reason=SKIP_CEILING,
                    detail="the operator ceiling says a person decides every action",
                )
                for p in proposals
            ),
        )

    allowed = frozenset(capabilities)
    dispatch = dispatch or _default_dispatch
    check = budget_check or default_budget_check()

    for index, proposal in enumerate(proposals):
        if breached:
            deferred.append(
                DeferredProposal(proposal=proposal, reason=SKIP_BUDGET, detail=breach_reason)
            )
            write(
                SKIPPED_BUDGET,
                {
                    "item_ordinal": proposal.item_id,
                    "action_type": proposal.action_type,
                    "tier": proposal.tier,
                    "reason": breach_reason,
                    "outcome": SKIP_BUDGET,
                },
            )
            continue

        provider = PROVIDER_FOR_ACTION.get(proposal.action_type, "")
        if not provider:
            deferred.append(DeferredProposal(proposal=proposal, reason=SKIP_NO_PROVIDER))
            continue
        if provider not in allowed:
            deferred.append(
                DeferredProposal(proposal=proposal, reason=SKIP_NOT_CAPABLE, detail=provider)
            )
            continue

        item = manifest.by_ordinal(proposal.item_id)
        if item is None:
            # Belt AND braces: `parse_proposals` already refuses an ordinal the manifest never
            # minted. Re-checking here is what makes the ordinal contract hold for ANY caller
            # of this stage, not only for one that came through the parser.
            deferred.append(DeferredProposal(proposal=proposal, reason=SKIP_UNKNOWN_ITEM))
            continue
        if provider == "inbox-op" and item.source != _INBOX_OP_LANE:
            deferred.append(
                DeferredProposal(proposal=proposal, reason=SKIP_WRONG_LANE, detail=item.source)
            )
            continue

        match = match_rules(rules, proposal.pattern_key, now=now)
        rule_key = match.rule.key if match.rule is not None else ""
        if match.decision is Decision.DENY:
            deferred.append(
                DeferredProposal(
                    proposal=proposal, reason=SKIP_DENIED, detail=match.reason, rule=rule_key
                )
            )
            continue
        if match.decision is Decision.SUPPRESS:
            deferred.append(
                DeferredProposal(
                    proposal=proposal, reason=SKIP_SUPPRESSED, detail=match.reason, rule=rule_key
                )
            )
            continue
        if not (match.auto_executes or proposal.tier == TRIVIAL_TIER):
            deferred.append(DeferredProposal(proposal=proposal, reason=SKIP_NEEDS_YOU))
            continue

        if len(executed) >= max(0, int(cap or 0)):
            deferred.append(
                DeferredProposal(
                    proposal=proposal, reason=SKIP_CAP, detail=f"cap {cap} reached at #{index + 1}"
                )
            )
            continue

        breached, breach_reason = check()
        if breached:
            deferred.append(
                DeferredProposal(proposal=proposal, reason=SKIP_BUDGET, detail=breach_reason)
            )
            write(
                SKIPPED_BUDGET,
                {
                    "item_ordinal": proposal.item_id,
                    "action_type": proposal.action_type,
                    "tier": proposal.tier,
                    "reason": breach_reason,
                    "outcome": SKIP_BUDGET,
                },
            )
            continue

        config = _action_config(proposal, item)
        ctx = _make_context(config)
        # The action denylist, threaded with the run's session key so a SafetyProfile's extra
        # globs are not silently skipped. Placed HERE, in `auto_execute`, rather than inside
        # `_default_dispatch`: a gate that lived in the default dispatch would be bypassed by any
        # caller that supplied its own, which is exactly the shape of the seam that loses a
        # control. `enforce_action` also writes the SEL row and, on `needs_human`, raises the
        # notification — so a refused auto-execution is never a silent drop.
        decision = enforce_action(provider, config, ctx, session_key=session_key)
        if decision.blocked:
            # The rule's code and its sentence, as a trigger's fire records the same refusal.
            deferred.append(
                DeferredProposal(proposal=proposal, reason=SKIP_DENYLIST, detail=decision.refusal())
            )
            continue

        named_rule = rule_key or TIER_POLICY_RULE
        pattern = match.rule.pattern if match.rule is not None else TRIVIAL_TIER
        try:
            outcome = await dispatch(provider, config, ctx)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - a raising provider is a failed action, not a crash
            logger.warning("auto-execute: %s raised", provider, exc_info=True)
            ok, reversal, error = False, "", f"{type(exc).__name__}: {exc}"
        else:
            ok = bool(getattr(outcome, "success", False))
            reversal = str(getattr(outcome, "reversal", "") or "")
            error = str(getattr(outcome, "error", "") or "")

        attempt = {
            "item_ordinal": proposal.item_id,
            "item_source_id": item.source_id,
            "action_type": proposal.action_type,
            "tier": proposal.tier,
            "provider": provider,
            "rule": named_rule,
            "rule_pattern": pattern,
        }
        if not ok:
            # Its own kind, never an `auto_executed` row with a failed outcome: a reader counting
            # what the machine did counts the kind, and the failure would be counted as done.
            deferred.append(DeferredProposal(proposal=proposal, reason=SKIP_FAILED, detail=error))
            write(AUTO_FAILED, {**attempt, "outcome": SKIP_FAILED, "reason": error})
            continue
        executed.append(
            AutoAction(
                proposal=proposal,
                provider=provider,
                source_id=item.source_id,
                rule=named_rule,
                rule_pattern=pattern,
                reversal=reversal,
            )
        )
        write(
            AUTO_EXECUTED,
            {**attempt, "reversal": reversal, "undoable": bool(reversal), "outcome": "executed"},
        )

    return AutoExecResult(
        executed=tuple(executed),
        deferred=tuple(deferred),
        budget_breached=breached,
        budget_reason=breach_reason,
        ledger_rows=rows,
    )


def render_auto_lines(result: AutoExecResult) -> tuple[str, ...]:
    """The digest's "what your machine did" lines for the auto-executed half (§1.6 bound 4).

    One line per action that LANDED, naming the rule that authorised it and whether an undo
    exists. The undo itself is a click on the ledger row; what the digest owes the user is the
    fact that something happened and what took it back. What failed, or what a guard stopped, is
    not something the machine did: it is said where the proposal waits for you
    (:func:`not_done_note`, :func:`stopped_note`).
    """
    lines: list[str] = []
    for action in result.executed:
        undo = " (undo available)" if action.undoable else ""
        lines.append(
            f"- auto-{action.proposal.action_type} on #{action.proposal.item_id} "
            f"via {action.rule}{undo}"
        )
    return tuple(lines)


# ── what did not happen, in plain words (the digest's text, its card, and a reply) ──

#: The deferrals of an action the digest was about to take on its own: it was eligible, and then
#: the dispatch failed, a safety rule held it, or the per-run cap was reached. Each is said on its
#: proposal as not done, with why and what to do next.
NOT_DONE_ON_ITS_OWN: frozenset[str] = frozenset({SKIP_FAILED, SKIP_DENYLIST, SKIP_CAP})

#: Why each deferral happened, as the rest of "Not done: …". The guard's own detail follows for
#: the reasons whose detail is the specific cause (:data:`_DETAIL_IS_THE_CAUSE`); the others carry
#: a fixed sentence or an internal name, and the phrase here is the whole answer.
_WHY: dict[str, str] = {
    SKIP_FAILED: "it was tried and it failed",
    SKIP_DENYLIST: "a safety rule held it",
    SKIP_CAP: "this digest reached its limit of actions it may take on its own",
    SKIP_BUDGET: "the spend ceiling was reached",
    SKIP_INCIDENT: "incident mode is on, which holds the digest's actions",
    SKIP_CEILING: "your approval ceiling says a person decides every action",
    SKIP_NOT_CAPABLE: "the digest is not allowed to take that kind of action",
    SKIP_WRONG_LANE: "the item is not in your Inbox, so the digest cannot act on it",
    SKIP_UNKNOWN_ITEM: "the item is not in this digest",
    SKIP_NO_PROVIDER: "the digest has no way to take that kind of action",
    SKIP_DENIED: "your rule says never for this",
    SKIP_SUPPRESSED: "you turned this kind of proposal down recently",
    SKIP_DISABLED: "auto-execution is off",
    SKIP_NEEDS_YOU: "it needs your say",
}
_DETAIL_IS_THE_CAUSE: frozenset[str] = frozenset({SKIP_FAILED, SKIP_DENYLIST, SKIP_BUDGET})


def why_not_done(reason: str, detail: str = "", *, on_its_own: bool = False) -> str:
    """Why a proposal's action did not happen, in plain words (the rest of "Not done: …").

    ``on_its_own`` says the digest made the attempt unasked, which is the first thing a reader of
    the failure needs to know: nobody asked for this attempt.
    """
    if on_its_own and reason == SKIP_FAILED:
        why = "the digest tried it on its own and it failed"
    else:
        why = _WHY.get(reason, "it did not run")
    if reason in _DETAIL_IS_THE_CAUSE:
        cause = detail.strip().rstrip(".")
        if cause:
            return f"{why} — {cause}"
        if reason == SKIP_FAILED:
            return f"{why}, and no reason was given"
    return why


#: Where the digest's text sends you to answer a proposal: its card. The text is shown in
#: PersonalClaw (the notification), where nothing takes a typed reply, and on a chat channel, which
#: adds how to answer there (`channel_reply.reply_footer`), so the card is what it names everywhere.
_ON_THE_CARD = "on the Morning triage card in your Inbox"


def not_done_note(
    reason: str,
    detail: str = "",
    *,
    answered: bool = False,
    on_card: bool = False,
) -> str:
    """The sentence a proposal carries when its action did not happen, or ``""``.

    Unanswered (the digest acting on its own), only :data:`NOT_DONE_ON_ITS_OWN` gets one: every
    other deferral is a plain proposal waiting for you, or a stage stop said once. ``answered``
    is a "yes" that did not happen, so every reason gets one, and the next step is the item
    itself — the answer is recorded, and a second tap would not act again. ``on_card`` is the
    card's own row, whose Yes button is the way to try again; the digest's text names that card.
    """
    if not answered and reason not in NOT_DONE_ON_ITS_OWN:
        return ""
    why = why_not_done(reason, detail, on_its_own=not answered)
    yes = "Yes" if on_card else f"Yes {_ON_THE_CARD}"
    if answered or reason == SKIP_DENYLIST:
        # The same rule holds a "yes", so offering one would offer a refusal.
        then = "Open the item to do it yourself."
    elif reason == SKIP_CAP:
        then = f"{yes} does it now."
    else:
        then = f"{yes} tries it again, or open the item to do it yourself."
    return f"Not done: {why}. {then}"


def stopped_note(reasons: Iterable[str], *, budget_reason: str = "") -> str:
    """The one sentence for a stage that stopped as a whole, or ``""``.

    Incident mode, the approval ceiling and a spend breach each defer EVERY remaining proposal,
    whatever its tier (a breach defers the rest before their eligibility is read), so a failure
    said per proposal would claim a medium-tier one was about to run. They are said once.
    """
    reasons = list(reasons)
    if SKIP_INCIDENT in reasons or SKIP_CEILING in reasons:
        why = why_not_done(SKIP_INCIDENT if SKIP_INCIDENT in reasons else SKIP_CEILING)
        return f"Nothing ran on its own: {why}. What it proposed waits for you."
    left = reasons.count(SKIP_BUDGET)
    if not left:
        return ""
    cause = (budget_reason or "").strip().rstrip(".") or "the spend ceiling was reached"
    rest = (
        "1 proposal it had not run waits" if left == 1 else f"{left} proposals it had not run wait"
    )
    return f"Auto-execution stopped early: {cause}. {rest} for you."


def dumps(result: AutoExecResult) -> str:
    return json.dumps(result.summary(), separators=(",", ":"))


__all__ = [
    "AUTO_CAPABLE_PROVIDERS",
    "NOT_DONE_ON_ITS_OWN",
    "PROVIDER_FOR_ACTION",
    "SKIP_BUDGET",
    "AUTO_EXEC_EVENT",
    "SKIP_CAP",
    "SKIP_DENIED",
    "SKIP_DENYLIST",
    "SKIP_DISABLED",
    "SKIP_FAILED",
    "SKIP_INCIDENT",
    "SKIP_NEEDS_YOU",
    "SKIP_NOT_CAPABLE",
    "SKIP_NO_PROVIDER",
    "SKIP_SUPPRESSED",
    "SKIP_UNKNOWN_ITEM",
    "SKIP_WRONG_LANE",
    "TIER_POLICY_RULE",
    "TRIVIAL_TIER",
    "AutoAction",
    "AutoExecResult",
    "DeferredProposal",
    "answer_capabilities",
    "auto_execute",
    "default_budget_check",
    "dumps",
    "not_done_note",
    "render_auto_lines",
    "stopped_note",
    "why_not_done",
]
