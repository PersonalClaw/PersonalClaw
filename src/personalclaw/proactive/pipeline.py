"""The five-stage triage pipeline, wired end to end (PROACTIVE-ASSISTANT §1.1-§1.5).

`run_triage` is the whole digest: collect → gate → ONE proposal call → rank → deliver. The
stages themselves are pure and live beside this module; what is here is the *order*, the two
model calls, and the four spend decisions that make the order defensible:

1. **Nothing collected ⇒ nothing spent.** The manifest is built before any model is reachable,
   and an empty manifest returns immediately with `llm_calls == 0`. That is §1.2's
   precondition guard: one cheap store read decides whether the expensive stages run at all.
2. **No rules ⇒ no gate call.** Delegated to `should_call_gate`, which also honours the
   `classifier_gate_enabled` switch.
3. **Nothing survived the gate ⇒ no proposal call.** A window the user's own rules emptied
   still produces a digest (so they can see the filter worked), but it produces it for free.
4. **Exactly one proposal call, ever.** No retry against the schema — a parse miss degrades to
   a plain digest. `output_type=dict` already buys one targeted retry inside
   `one_shot_completion` on the providers that enforce schemas; layering a second retry here
   would spend a third call on the same fenced content.

Both calls resolve on the **background** axis (`use_case="background"`), never the chat axis,
and both go through `render_use_case_prompt` so the user can edit them in Settings → Prompts
like every other internal prompt.

`completion` and `deliver` are injectable, defaulting to the real
`one_shot_completion` / `DashboardState.notify` path. That is not a test seam bolted on: a
digest is an unattended scheduled run, and the two things it does that can fail on someone
else's infrastructure are the two things a caller may need to supply — the provider passes the
defaults, so the production path is the default path.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from personalclaw.proactive.autoexec import AutoExecResult, render_auto_lines
from personalclaw.proactive.gate import (
    GateResult,
    GateRule,
    apply_gate,
    open_gate,
    parse_gate_output,
    should_call_gate,
)
from personalclaw.proactive.manifest import (
    CollectedItem,
    Manifest,
    build_manifest,
    render_manifest_lines,
)
from personalclaw.proactive.proposals import (
    MAX_PROPOSALS,
    Proposal,
    ProposalBatch,
    RefusedProposal,
    parse_proposals,
)
from personalclaw.proactive.rank import Digest, render_digest

logger = logging.getLogger(__name__)

#: Named so the scope on both calls is greppable and so a spend audit can tell the gate's
#: cheap call apart from the proposal call inside one digest run.
GATE_SCOPE = "triage_gate"
PROPOSE_SCOPE = "triage_propose"

CompletionFn = Callable[..., Awaitable[object]]
DeliverFn = Callable[[Digest], bool]
#: §1.6's auto-execution stage, injected rather than imported-and-called so the ordering test
#: does not also become a test of the guardrails floor. `None` means "propose only", which is
#: what every caller before PA-3 did and what a caller with `auto_execute_enabled` off still
#: effectively gets (the stage itself refuses, one layer down).
AutoExecFn = Callable[[tuple[Proposal, ...], Manifest], Awaitable["AutoExecResult"]]


@dataclass
class TriageResult:
    """Everything the run produced, in the shape a ledger row and an action result need."""

    manifest: Manifest = field(default_factory=Manifest)
    gate: GateResult = field(default_factory=GateResult)
    batch: ProposalBatch = field(default_factory=ProposalBatch)
    digest: Digest | None = None
    #: §1.6's outcome, or None when the caller passed no auto-execution stage.
    auto: AutoExecResult | None = None
    llm_calls: int = 0
    delivered: bool = False
    #: True when the window was empty and the pipeline returned before any model was reachable.
    short_circuited: bool = False
    #: True when the gate was consulted (as opposed to defaulted open).
    gate_called: bool = False
    notes: tuple[str, ...] = ()

    @property
    def proposals(self) -> tuple[Proposal, ...]:
        return self.batch.proposals

    @property
    def refused(self) -> tuple[RefusedProposal, ...]:
        return self.batch.refused

    def summary(self) -> dict:
        """The flat, JSON-safe shape a template binds and a ledger row carries."""
        return {
            "collected": len(self.manifest),
            "lanes": self.manifest.counts(),
            "dropped": len(self.gate.dropped),
            "surfaced": len(self.gate.surfaced),
            "proposable": len(self.gate.proposable),
            "proposals": [
                {
                    "item_id": p.item_id,
                    "action_type": p.action_type,
                    "tier": p.tier,
                    "pattern_key": p.pattern_key,
                    "clamped": p.clamped,
                }
                for p in self.proposals
            ],
            "refused": [
                {"reason": r.reason, "item_id": r.item_id, "action_type": r.action_type}
                for r in self.refused
            ],
            "llm_calls": self.llm_calls,
            "delivered": self.delivered,
            "short_circuited": self.short_circuited,
            "gate_called": self.gate_called,
            "degraded": self.batch.degraded,
            "digest_title": self.digest.title if self.digest else "",
            "digest_body": self.digest.body if self.digest else "",
            # Merged rather than nested: `summary()` is the flat shape a template binds with
            # `{{nodes.triage.output.auto_executed}}`, and a nested object would make the one
            # thing PA-5's digest card needs the one thing a binding cannot reach.
            **(self.auto.summary() if self.auto is not None else {}),
        }


async def _default_completion(prompt: str, **kwargs: object) -> object:
    from personalclaw.llm_helpers import one_shot_completion

    return await one_shot_completion(prompt, **kwargs)  # type: ignore[arg-type]


def make_notify_deliver(*, run_id: str = "", trigger_id: str = "") -> DeliverFn:
    """The default delivery: the substrate's outbound contract → the singular notify gate.

    §1.5 asks for delivery "through the substrate's outbound delivery contract (decision 13)"
    with a "stable event-id, statusUrl into the run journal" — so the digest rides
    `triggers.delivery.Delivery.to_notify_kwargs()` rather than calling `notify(kind, title,
    body)` bare. The two things that buys are exactly the two the criteria name: `statusUrl`
    lands in the notification's `meta` so the digest card deep-links the run journal
    (criterion 1), and `event_id` is DERIVED from `(trigger_id, run_id)` rather than random, so
    a re-delivered digest dedupes instead of arriving twice (criterion 9's substrate).

    `DashboardState.notify` remains the only gate consulted: mute-all, minimum severity, then
    quiet-hours suppression for anything below `error`. A digest is `info`, so quiet hours DEFER
    it, which is the behaviour criterion 1 asks for. Building the `Delivery` here and then
    sending it some other way would be the second path R18 forbids.
    """

    def deliver(digest: Digest) -> bool:
        from personalclaw.action_providers.services import get_action_services
        from personalclaw.triggers.delivery import (
            EVENT_SUCCEEDED,
            Delivery,
            event_id,
            status_url,
        )

        services = get_action_services()
        if services is None:
            logger.warning("triage: services unavailable, digest not delivered")
            return False
        payload = Delivery(
            event=EVENT_SUCCEEDED,
            event_id=event_id(trigger_id=trigger_id or "triage-digest", run_id=run_id),
            title=digest.title,
            body=digest.body,
            status_url=status_url(run_id=run_id, trigger_id=trigger_id),
            trigger_id=trigger_id,
            run_id=run_id,
            kind=digest.kind,
        )
        try:
            services.state.notify(**payload.to_notify_kwargs())
        except Exception:  # noqa: BLE001 - an undelivered digest must not fail the run
            logger.warning("triage: digest delivery failed", exc_info=True)
            return False
        return True

    return deliver


async def _ask(
    completion: CompletionFn,
    prompt: str,
    *,
    scope: str,
) -> object | None:
    """One background call, with the caller scope stamped and every failure absorbed.

    Returns `None` when the call could not produce anything. `OutputContractError` is unwrapped
    to its raw text first: a schema miss that still returned prose is worth parsing, and
    throwing it away would turn a recoverable partial answer into a degraded digest.
    """
    from personalclaw.guardrails.failure import OutputContractError

    # The `caller_scope` bind lives at each CALL SITE, not here, and deliberately spells the
    # caller as a literal. Binding through this function's `scope` parameter worked at runtime
    # but was invisible to `test_model_call_attribution`'s source scan — and to a human grepping
    # for which pass spends, which is the whole point of naming the caller. `scope` stays for the
    # failure log.
    try:
        return await completion(prompt, use_case="background", output_type=dict)
    except OutputContractError as exc:
        return getattr(exc, "raw", None)
    except Exception:  # noqa: BLE001 - an unattended run absorbs a provider failure
        logger.warning("triage: %s call failed", scope, exc_info=True)
        return None


def _render(use_case: str, values: dict[str, str]) -> str:
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    try:
        return (render_use_case_prompt(use_case, values) or "").strip()
    except Exception:  # noqa: BLE001
        logger.warning("triage: prompt %s unresolvable", use_case, exc_info=True)
        return ""


async def run_triage(
    items: list[CollectedItem] | tuple[CollectedItem, ...],
    *,
    rules: list[GateRule] | tuple[GateRule, ...] = (),
    gate_enabled: bool = True,
    max_proposals: int = MAX_PROPOSALS,
    window_start: str = "",
    run_id: str = "",
    trigger_id: str = "",
    completion: CompletionFn | None = None,
    deliver: DeliverFn | None = None,
    auto_execute: AutoExecFn | None = None,
) -> TriageResult:
    """Run the digest over an already-collected item set and deliver it.

    Collection is the CALLER's job (`collect.collect_all`) because the three lanes need live
    handles — the inbox store, the dashboard state — that belong to whoever is running the
    pipeline, and reaching for them here would make every test of the ordering also a test of
    the gateway's wiring.
    """
    from personalclaw.guardrails.audit import caller_scope

    completion = completion or _default_completion
    deliver = deliver or make_notify_deliver(run_id=run_id, trigger_id=trigger_id)

    manifest = build_manifest(items, window_start=window_start)

    # Spend decision 1: an empty window costs nothing and delivers nothing. Delivering a
    # "nothing happened" notification every morning is how a digest gets muted in week two.
    if manifest.is_empty:
        return TriageResult(
            manifest=manifest,
            gate=GateResult(),
            short_circuited=True,
            notes=("empty window: no model call, no delivery",),
        )

    notes: list[str] = []
    llm_calls = 0

    # Stage 2. Spend decision 2 lives inside `should_call_gate`.
    gate_called = False
    if should_call_gate(manifest, rules, enabled=gate_enabled):
        prompt = _render(
            "triage_classify",
            {
                "rules": "\n".join(f"- [{r.source}] {r.rule}" for r in rules),
                "items": render_manifest_lines(manifest),
            },
        )
        if not prompt:
            notes.append("gate prompt unresolvable: gate defaulted open")
            gate = open_gate(manifest)
        else:
            with caller_scope("triage_gate"):
                raw = await _ask(completion, prompt, scope=GATE_SCOPE)
            llm_calls += 1
            gate_called = True
            outcomes = parse_gate_output(raw, manifest) if raw is not None else {}
            if not outcomes:
                notes.append("gate returned nothing usable: failed open")
            gate = apply_gate(manifest, outcomes)
    else:
        gate = open_gate(manifest)
        notes.append("gate not consulted")

    # Stage 3. Spend decision 3: a window the user's rules emptied still gets a digest, for
    # free — the "filtered by your rules: N" line is how they see the gate working.
    batch = ProposalBatch()
    if gate.proposable:
        cap = max(0, min(int(max_proposals or 0), MAX_PROPOSALS))
        if cap == 0:
            notes.append("proposal cap is zero: no proposal call")
        else:
            # The survivors are wrapped, NOT re-numbered. `build_manifest` would renumber them
            # 1..K and the ordinal contract would then be asserted against a second id space —
            # a reply saying `3 yes` would name the third survivor rather than the third
            # collected item. The wrap keeps the ordinals the collect stage minted.
            surviving = Manifest(items=gate.proposable, window_start=window_start)
            prompt = _render(
                "triage_propose",
                {
                    "items": render_manifest_lines(surviving),
                    "max_proposals": str(cap),
                },
            )
            if not prompt:
                notes.append("proposal prompt unresolvable: plain digest")
                batch = ProposalBatch(degraded=True)
            else:
                # Spend decision 4: ONE call. No retry loop against the schema.
                with caller_scope("triage_propose"):
                    raw = await _ask(completion, prompt, scope=PROPOSE_SCOPE)
                llm_calls += 1
                if raw is None:
                    notes.append("proposal call failed: plain digest")
                    batch = ProposalBatch(degraded=True)
                else:
                    batch = parse_proposals(raw, allowed_ordinals=surviving.ordinals())
                    if len(batch.proposals) > cap:
                        batch = ProposalBatch(
                            proposals=batch.proposals[:cap],
                            refused=(
                                *batch.refused,
                                *(
                                    RefusedProposal(
                                        reason="over_cap",
                                        item_id=p.item_id,
                                        action_type=p.action_type,
                                    )
                                    for p in batch.proposals[cap:]
                                ),
                            ),
                            degraded=batch.degraded,
                            extra_keys=batch.extra_keys,
                        )

    # Stage 3.5 (§1.6). BEFORE rendering, and that order is the whole reason this stage is not
    # bolted on after `deliver`: a digest that listed an archived item under "needs you" and
    # then archived it a second later would be actively misleading — the user would tap a
    # proposal for work the machine had already done. Auto-execution therefore happens here,
    # and only its LEFTOVERS reach the "needs you" section.
    auto: AutoExecResult | None = None
    if auto_execute is not None and batch.proposals:
        try:
            auto = await auto_execute(batch.proposals, manifest)
        except Exception:  # noqa: BLE001 - an unattended run degrades to propose-only
            logger.warning("triage: auto-execution stage failed", exc_info=True)
            notes.append("auto-execution failed: every proposal stayed pending")
            auto = None
    pending = auto.pending if auto is not None else batch.proposals

    # Stages 4-5. Ranking and rendering are deterministic; delivery is the singular gate.
    digest = render_digest(
        manifest,
        kept=gate.kept,
        proposals=pending,
        dropped_count=len(gate.dropped),
        degraded=batch.degraded,
        auto_lines=render_auto_lines(auto) if auto is not None else (),
    )
    delivered = bool(deliver(digest))

    return TriageResult(
        manifest=manifest,
        gate=gate,
        batch=batch,
        digest=digest,
        auto=auto,
        llm_calls=llm_calls,
        delivered=delivered,
        short_circuited=False,
        gate_called=gate_called,
        notes=tuple(notes),
    )


__all__ = [
    "GATE_SCOPE",
    "PROPOSE_SCOPE",
    "TriageResult",
    "make_notify_deliver",
    "run_triage",
]
