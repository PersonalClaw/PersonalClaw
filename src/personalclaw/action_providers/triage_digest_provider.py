"""``triage-digest`` action provider — the triage pipeline's one call site (PA §1.1-§1.5).

The bundled "Morning triage" template fires ONE `action` node, and this is it. Everything the
pipeline needs that only the running gateway has — the inbox store, the live channel sessions,
the run store, the notification gate — is reached from here, so the pipeline itself stays a
pure function of an item list and two callables.

**Why an action node rather than a chain of `infer` nodes.** Three properties the plan asks for
are only obtainable from code:

* the zero-item short-circuit has to happen BEFORE a model is reachable, and an `infer` node is
  a model call by definition — a template that expressed the guard as a `branch` would have to
  collect in one node, and the collect stage needs live service handles;
* the ordinal contract has to be *enforced*, not requested. A prompt can ask for exact ids; only
  code can refuse a proposal that named one the manifest never minted;
* the drop and refusal rationales have to be *recorded*. `journal.step_skipped` carries no
  reason, so a model-authored gate leaves "why did nothing surface?" unanswered in the one place
  the plan says it must be answered.

**Where the filter rules live: on this node, in `action_config`.** Not in a new store. The
bundled template is copied into the user's own `defs/` the moment they instantiate it, so the
rules are editable exactly where the schedule and the capability set are, and a rule change is a
template edit the engine already versions. A parallel rules store would be a second thing to
back up and a second place "why was this dropped?" has to be looked up.

``action_config`` shape::

    {
        "filter_rules": [{"source": "inbox", "rule": "skip dependabot"}],  # optional
        "window_hours": 24,     # optional fallback when no digest has completed yet
        "max_proposals": 8      # optional; clamped to MAX_PROPOSALS
    }

**What the last digest left waiting comes with it.** The last digest that completed is read once:
the window starts where it began, and what its card still has waiting on you, checked against
each item as its lane reads it now, is carried into this one (`proactive.carry`).

Output (one JSON object, so the template can bind ``{{nodes.triage.output.*}}``) is
:meth:`TriageResult.summary`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
    run_identity,
)

logger = logging.getLogger(__name__)

#: Fallback look-back when no digest has ever completed. A day, because the schedule default is
#: daily; the first run of a fresh install therefore sees one day, not the whole backlog, which
#: is the difference between a digest and a wall of text.
DEFAULT_WINDOW_HOURS = 24

#: The node id the ledger rows are stamped with when the engine did not supply one.
_NODE_ID = "triage"


def _proactive_config() -> Any:
    from personalclaw.config.loader import AppConfig

    return AppConfig.load().proactive


def _previous_digest() -> tuple[dict | None, dict | None, list[dict]]:
    """The last digest that completed: its run row, its output and its ledger slice.

    ``(None, None, [])`` when there is none, and when it cannot be read (which the log says): the
    window then looks back the fallback hours, and nothing is carried.
    """
    from personalclaw.proactive import digest_state

    try:
        return digest_state.last_completed_digest()
    except Exception:  # noqa: BLE001 - no run store → the fallback window is correct
        logger.warning("triage: the last digest could not be read", exc_info=True)
        return None, None, []


def _window(config: dict[str, Any], previous: dict | None) -> tuple[float, str]:
    """(epoch seconds, ISO string) for the window start — where the last completed digest BEGAN,
    else N hours back.

    Where it began, not where it ended: it collected as it began and then spent its model calls,
    so a message that arrived in between was too new for it, and a window that started at its end
    left that message out of every digest. The two windows now meet exactly.

    Both spellings are returned because the three lanes compare against different stamps: the
    inbox store keeps epoch floats, the run store keeps ISO strings. Converting at the boundary
    once beats each collector guessing. The ISO one is in the run store's own form, so the run
    lane's text comparison is exact.
    """
    from datetime import UTC, datetime

    try:
        hours = float(config.get("window_hours") or DEFAULT_WINDOW_HOURS)
    except (TypeError, ValueError):
        hours = DEFAULT_WINDOW_HOURS
    fallback = time.time() - max(0.0, hours) * 3600.0

    stamp = str((previous or {}).get("started_at") or (previous or {}).get("created_at") or "")
    if stamp:
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return (parsed.timestamp(), stamp)
        except ValueError:
            logger.debug("triage: unparseable last-digest stamp %r", stamp)
    return (fallback, datetime.fromtimestamp(fallback, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _still_waiting(
    previous: tuple[dict | None, dict | None, list[dict]], *, inbox_store: Any, state: Any
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """``(still waiting, dealt with)``: what the last digest's card has waiting on you, each
    checked against its item as its lane reads it now (`carry.recheck`)."""
    from personalclaw.proactive.carry import recheck, waiting_from
    from personalclaw.proactive.collect import current_item
    from personalclaw.proactive.surface import build_digest_view

    run, output, events = previous
    if run is None or output is None:
        return (), ()
    view = build_digest_view(enabled=True, installed=True, run=run, output=output, events=events)

    def look(source: str, source_id: str) -> Any:
        return current_item(source, source_id, inbox_store=inbox_store, state=state)

    return recheck(waiting_from(view), look=look)


def _rules(config: dict[str, Any]) -> list[Any]:
    from personalclaw.proactive.gate import GateRule

    raw = config.get("filter_rules")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    out: list[GateRule] = []
    for entry in raw or []:
        if isinstance(entry, dict):
            text = str(entry.get("rule", "") or "").strip()
            if text:
                out.append(GateRule(source=str(entry.get("source", "*") or "*"), rule=text))
        elif isinstance(entry, str) and entry.strip():
            # A bare string is a rule that applies to every lane. Accepted because that is what
            # a user types first, and refusing it would make the common case the awkward one.
            out.append(GateRule(source="*", rule=entry.strip()))
    return out


def _record(result: Any, ctx: ActionContext) -> int:
    """Write the gate's drop rationales and the proposal refusals to the run ledger.

    Returns the number of rows written. Zero when the engine supplied no `run_id`/`instance_path`
    — a row stamped with a bare node id is durably written and then INVISIBLE in the runs
    surface, because `inspect_node` slices a run's ledger on the engine's instance key. Writing
    it anyway would answer "why was this dropped?" to nobody, so the rows are skipped and the
    absence is reported in the result instead of being faked.
    """
    run_id = run_identity(ctx, "run_id")
    instance_path = run_identity(ctx, "instance_path")
    if not run_id or not instance_path:
        return 0

    from personalclaw.ledger.kinds import (
        PROPOSAL_CARRIED,
        PROPOSAL_DROPPED,
        PROPOSAL_REFUSED,
        SKIPPED_TRIAGE,
    )
    from personalclaw.workflows.journal import Journal

    journal = Journal(run_id=run_id)
    written = 0
    for item in result.gate.dropped:
        outcome = result.gate.outcomes.get(item.ordinal)
        journal.write(
            SKIPPED_TRIAGE,
            node_id=_NODE_ID,
            instance_path=instance_path,
            epoch=0,
            actor="triage",
            item_ordinal=item.ordinal,
            item_source=item.source,
            item_source_id=item.source_id,
            rationale=(outcome.rationale if outcome else "") or "dropped by the classifier gate",
            rule=(outcome.rule if outcome else ""),
        )
        written += 1
    for refusal in result.refused:
        journal.write(
            PROPOSAL_REFUSED,
            node_id=_NODE_ID,
            instance_path=instance_path,
            epoch=0,
            actor="triage",
            reason=refusal.reason,
            item_ordinal=refusal.item_id,
            action_type=refusal.action_type,
            detail=refusal.detail,
        )
        written += 1
    for carried in result.carry.carried:
        journal.write(
            PROPOSAL_CARRIED,
            node_id=_NODE_ID,
            instance_path=instance_path,
            epoch=0,
            actor="triage",
            item_ordinal=carried.proposal.item_id,
            item_source=carried.item.source,
            item_source_id=carried.item.source_id,
            action_type=carried.proposal.action_type,
            # The kind is what happened; the detail is where it came from, which the card's
            # journal line shows.
            detail=f"first proposed in run {carried.first_run_id}",
            first_run_id=carried.first_run_id,
            first_proposed_at=carried.first_proposed_at,
        )
        written += 1
    for gone in result.carry.dropped:
        journal.write(
            PROPOSAL_DROPPED,
            node_id=_NODE_ID,
            instance_path=instance_path,
            epoch=0,
            actor="triage",
            item_source=gone.waiting.item.source,
            item_source_id=gone.waiting.item.source_id,
            action_type=gone.waiting.proposal.action_type,
            reason=gone.reason,
            first_run_id=gone.waiting.first_run_id,
            first_proposed_at=gone.waiting.first_proposed_at,
        )
        written += 1
    return written


def _approval_rules(memory: Any = None) -> list[Any]:
    """The user's taught approval rules, decoded. An exact prefix scan, never a vector search.

    §1.4 is explicit that approval lookups are exact — a rule is policy, and a nearest-neighbour
    match on policy would auto-execute against a pattern the user never taught. Undecodable rows
    are dropped by `rules_from_rows` rather than guessed at; the rules-manager surface is where a
    broken row gets reported, and a matcher that invented a verdict for one would be worse than
    one that ignores it.

    Returns `[]` on any failure, which degrades auto-execution to trivial-tier only. That is the
    safe direction: a missing rule store can never manufacture an approve verdict, it can only
    fail to find one, and the proposal then queues pending exactly as it did before PA-3.
    """
    from personalclaw.proactive.approval import APPROVAL_KEY_PREFIX, rules_from_rows

    try:
        if memory is None:
            from personalclaw.memory_service import MemoryService
            from personalclaw.vector_memory import VectorMemoryStore

            store = VectorMemoryStore()
            store.init()
            memory = MemoryService.over_vector_store(store)
        rows = [
            (str(row.get("key") or ""), row.get("value_json"))
            for row in memory.get_all_semantic()
            if str(row.get("key") or "").startswith(APPROVAL_KEY_PREFIX)
        ]
    except Exception:  # noqa: BLE001 - no rule store → trivial tier only, never a free approve
        logger.warning("triage: approval rules unreadable", exc_info=True)
        return []
    return rules_from_rows(rows)


def _ledger_writer(ctx: ActionContext) -> Any:
    """A `(kind, fields) -> None` writer bound to this run, or None when there is no run.

    Same reason `_record` returns 0 rather than writing: a row stamped with a bare node id is
    durably written and then INVISIBLE in the runs surface, because `inspect_node` slices a
    run's ledger on the engine's instance key. An auto-execution the user cannot find in the
    ledger is exactly the silent unattended write §1.6 exists to prevent, so the absence is
    reported (`auto_ledger_rows: 0`) instead of faked.
    """
    run_id = run_identity(ctx, "run_id")
    instance_path = run_identity(ctx, "instance_path")
    if not run_id or not instance_path:
        return None

    from personalclaw.workflows.journal import Journal

    journal = Journal(run_id=run_id)

    def write(kind: str, fields: dict[str, Any]) -> None:
        journal.write(
            kind,
            node_id=_NODE_ID,
            instance_path=instance_path,
            epoch=0,
            actor="triage",
            **fields,
        )

    return write


def _capabilities(action_config: dict[str, Any]) -> frozenset[str]:
    """The frozen set of providers this digest may DISPATCH (§1.6 bound 2).

    Read off the node, defaulting to `AUTO_CAPABLE_PROVIDERS` (just `inbox-op`). This is a
    second, narrower fence than the trigger-level one in `triggers/screen.py`: that one decides
    whether the trigger may run `triage-digest` AT ALL, and it cannot express "and the digest
    may then archive but not send", because the only provider it sees on the node is
    `triage-digest` itself. A malformed declaration collapses to the default rather than to
    everything — an unparseable capability list must never widen a fence.
    """
    from personalclaw.proactive.autoexec import AUTO_CAPABLE_PROVIDERS

    raw = action_config.get("capabilities")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, (list, tuple)):
        return AUTO_CAPABLE_PROVIDERS
    names = frozenset(str(n).strip() for n in raw if str(n).strip())
    return names or AUTO_CAPABLE_PROVIDERS


def _auto_stage(action_config: dict[str, Any], ctx: ActionContext, cfg: Any) -> Any:
    """The §1.6 stage, bound to this run's config, rules, budget and journal.

    Built here rather than inside the pipeline because every input is a live handle the pipeline
    deliberately does not hold — the config, the memory store, the run's journal. The pipeline
    keeps the ORDER (auto-execute before render), this keeps the wiring.
    """
    from datetime import UTC, datetime

    from personalclaw.guardrails.policy import unattended_dispatch_key
    from personalclaw.proactive.autoexec import auto_execute, default_budget_check

    run_id = run_identity(ctx, "run_id")
    # The trigger whose fire this is, as its dispatch says (`ActionContext.trigger_id`), never the
    # payload's: a workflow step's payload is its template's, and could name another automation.
    trigger_id = ctx.trigger_id
    # A digest fire has no chat session by definition, so it gets the sessionless unattended
    # identity — the same one the gateway's store-trigger seam uses. Threading it is what lets the
    # run's `SafetyProfile.denylist_extra` layer onto the operator denylist instead of being
    # silently skipped, and it is what makes a clamp in the SEL attributable to this automation
    # rather than to "some action".
    session_key = unattended_dispatch_key(f"trigger:{trigger_id or run_id or 'triage-digest'}")
    rules = _approval_rules()
    ledger = _ledger_writer(ctx)
    capabilities = _capabilities(action_config)
    enabled = bool(getattr(cfg, "auto_execute_enabled", False))
    cap = int(getattr(cfg, "max_auto_actions_per_run", 0) or 0)

    async def stage(proposals: Any, manifest: Any) -> Any:
        return await auto_execute(
            proposals,
            manifest=manifest,
            rules=rules,
            now=datetime.now(UTC),
            enabled=enabled,
            cap=cap,
            capabilities=capabilities,
            session_key=session_key,
            budget_check=default_budget_check(run_id),
            ledger=ledger,
        )

    return stage


class TriageDigestActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "triage-digest"

    @property
    def display_name(self) -> str:
        return "Run the Triage Digest"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from datetime import UTC, datetime

        from personalclaw.action_providers.services import get_action_services
        from personalclaw.proactive.collect import collect_all
        from personalclaw.proactive.pipeline import run_triage
        from personalclaw.proactive.proposals import MAX_PROPOSALS

        cfg = _proactive_config()
        if not getattr(cfg, "triage_enabled", False):
            # Fail CLOSED, and visibly. A refusal reported as success is how a switch that is
            # off becomes indistinguishable from a pipeline that found nothing — and this
            # switch is the plan's soul guardrail, so the run says which one it was.
            return ActionResult(
                success=False,
                error=(
                    "triage-digest refused: proactive.triage_enabled is off — "
                    "nothing is collected and no model is called until you turn it on"
                ),
            )

        services = get_action_services()
        inbox_store = None
        state = None
        if services is not None:
            state = services.state
            inbox_store = getattr(state, "_inbox_store", None)
            if inbox_store is None:
                svc = getattr(state, "_inbox_svc", None)
                inbox_store = getattr(svc, "inbox", None) if svc is not None else None

        now = datetime.now(UTC)
        previous = _previous_digest()
        since_ts, since_iso = _window(action_config, previous[0])
        waiting, handled = _still_waiting(previous, inbox_store=inbox_store, state=state)
        items = collect_all(
            inbox_store=inbox_store,
            state=state,
            since_ts=since_ts,
            since_iso=since_iso,
        )

        try:
            cap = int(action_config.get("max_proposals") or MAX_PROPOSALS)
        except (TypeError, ValueError):
            cap = MAX_PROPOSALS

        result = await run_triage(
            items,
            rules=_rules(action_config),
            gate_enabled=bool(getattr(cfg, "classifier_gate_enabled", True)),
            max_proposals=cap,
            window_start=since_iso,
            # The run id is what makes the digest's `statusUrl` deep-link THIS run's journal and
            # what makes its `event_id` derived rather than random (§1.5 / criterion 9). Empty
            # when a caller fires the provider outside a run — the delivery then falls back to
            # the trigger link rather than pointing at a run that does not exist.
            run_id=run_identity(ctx, "run_id"),
            trigger_id=ctx.trigger_id,
            # Passed unconditionally, not behind `auto_execute_enabled`: the switch is
            # enforced INSIDE the stage, where a refusal produces a reason per proposal
            # (`auto_execute_disabled`) that the digest and the ledger can both show. Gating the
            # wiring here instead would make "the switch is off" indistinguishable from "the
            # stage was never wired", which is the failure its own `triage_enabled` refusal
            # is written to avoid one layer up.
            auto_execute=_auto_stage(action_config, ctx, cfg),
            waiting=waiting,
            handled=handled,
            now=now,
        )

        summary = result.summary()
        summary["window_start"] = since_iso
        summary["ledger_rows"] = _record(result, ctx)
        summary["notes"] = list(result.notes)
        if result.batch.degraded:
            # The digest went out at its floor: the items, the gate applied, and no new
            # proposals, which its body says. A run that did that is degraded, and says so, not a
            # success. "New": what an earlier digest left waiting is still in it.
            return ActionResult(
                success=True,
                exit_code=0,
                stdout=json.dumps(summary),
                outcome="degraded",
                summary=(
                    "The triage digest went out without new proposals: the proposal step gave "
                    "none it could use."
                ),
            )
        return ActionResult(success=True, exit_code=0, stdout=json.dumps(summary))


def create_provider(config: dict[str, Any] | None = None) -> "TriageDigestActionProvider":
    return TriageDigestActionProvider()
