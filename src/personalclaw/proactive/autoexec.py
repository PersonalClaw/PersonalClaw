"""Stage 3.5 — trivial-tier auto-execution, quadruple-bounded (PROACTIVE-ASSISTANT §1.6).

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
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from personalclaw.proactive.approval import ApprovalRule, Decision, match_rules
from personalclaw.proactive.manifest import SOURCE_INBOX, Manifest
from personalclaw.proactive.proposals import TIERS, Proposal

logger = logging.getLogger(__name__)

#: The trivial rung — the only tier that auto-executes without a taught rule.
TRIVIAL_TIER = TIERS[0]

#: ``action_type`` → the action provider that performs it. **A proposal can never name a
#: provider**; it names one of `proposals.ACTION_TYPES`, and this table is the only thing that
#: turns one into a dispatch. An action_type absent from here has no auto-execution path at all,
#: which is why `remind` and `none` are deliberately missing: a reminder is a trigger to mint,
#: not an action to run, and `none` is the model saying there is nothing to do.
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
SKIP_FAILED = "execution_failed"
#: The two PLATFORM gates, above §1.6's four. This module is a fifth UNATTENDED dispatch seam
#: (AUTONOMY-GUARDRAILS §1.2), so it carries the kill switch and the action denylist like the
#: other four — a digest that kept archiving through an incident would be the quiet exception
#: that makes the kill switch useless.
SKIP_INCIDENT = "incident_active"
SKIP_DENYLIST = "denied_by_denylist"

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
    """One proposal that was dispatched, with what authorised it and how to take it back."""

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
    ok: bool = True
    error: str = ""

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
                    "ok": a.ok,
                    "error": a.error,
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
            verdict, reason = meter.check_day(budget_from_config())
            if verdict is BudgetVerdict.EXCEEDED:
                return True, reason
            if run_key:
                verdict, reason = meter.check_run(run_key, run_budget_from_config())
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
    """
    config: dict[str, Any] = dict(proposal.action_config or {})
    config["op"] = proposal.action_type
    config["action_type"] = proposal.action_type
    config["item_id"] = item.source_id
    if proposal.action_type == "create_task" and not config.get("title_template"):
        config["title_template"] = item.title or f"Follow up: {item.source_id}"
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
) -> AutoExecResult:
    """Run the eligible proposals, defer the rest, and record BOTH.

    Zero silent drops is the contract (criterion 4): every proposal comes back either in
    ``executed`` or in ``deferred`` with a reason, and the counts always reconcile with the
    input. A caller that supplies `ledger` also gets one row per outcome.
    """
    from personalclaw.ledger.kinds import AUTO_EXECUTED, SKIPPED_BUDGET

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

    if incident_active():
        # FAIL-CLOSED, and before the loop: the kill switch exists to suspend unattended work,
        # and a digest that kept archiving through an incident would be the quiet exception that
        # makes it useless. The proposals still come back — pending, with the reason — so the
        # user sees an incident as a deferral rather than as a digest that went silent.
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
        if getattr(decision, "blocked", False):
            deferred.append(
                DeferredProposal(
                    proposal=proposal,
                    reason=SKIP_DENYLIST,
                    detail=str(getattr(decision, "reason", "") or ""),
                )
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
            outcome = None
            ok, reversal, error = False, "", f"{type(exc).__name__}: {exc}"
        else:
            ok = bool(getattr(outcome, "success", False))
            reversal = str(getattr(outcome, "reversal", "") or "")
            error = str(getattr(outcome, "error", "") or "")

        action = AutoAction(
            proposal=proposal,
            provider=provider,
            source_id=item.source_id,
            rule=named_rule,
            rule_pattern=pattern,
            reversal=reversal,
            ok=ok,
            error=error,
        )
        if ok:
            executed.append(action)
        else:
            deferred.append(DeferredProposal(proposal=proposal, reason=SKIP_FAILED, detail=error))
        write(
            AUTO_EXECUTED,
            {
                "item_ordinal": proposal.item_id,
                "item_source_id": item.source_id,
                "action_type": proposal.action_type,
                "tier": proposal.tier,
                "provider": provider,
                "rule": named_rule,
                "rule_pattern": pattern,
                "reversal": reversal,
                "undoable": bool(reversal),
                "outcome": "executed" if ok else "failed",
                "error": error,
            },
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

    One line per action, naming the rule that authorised it and whether an undo exists. The
    undo itself is a click on the ledger row; what the digest owes the user is the fact that
    something happened and what took it back.
    """
    lines: list[str] = []
    for action in result.executed:
        undo = " (undo available)" if action.undoable else ""
        lines.append(
            f"- auto-{action.proposal.action_type} on #{action.proposal.item_id} "
            f"via {action.rule}{undo}"
        )
    if result.budget_breached:
        lines.append(
            f"- stopped early: {result.budget_reason or 'the spend ceiling was reached'}; "
            f"{sum(1 for d in result.deferred if d.reason == SKIP_BUDGET)} left for you"
        )
    return tuple(lines)


def dumps(result: AutoExecResult) -> str:
    return json.dumps(result.summary(), separators=(",", ":"))


__all__ = [
    "AUTO_CAPABLE_PROVIDERS",
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
    "auto_execute",
    "default_budget_check",
    "dumps",
    "render_auto_lines",
]
