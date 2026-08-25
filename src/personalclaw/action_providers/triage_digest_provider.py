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

**Where the §1.2 filter rules live: on this node, in `action_config`.** Not in a new store. The
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
)

logger = logging.getLogger(__name__)

#: The bundled template's name. Read here for the "since the last successful digest" window —
#: the window is a property of THIS pipeline's own history, not of whatever else has run.
TRIAGE_WORKFLOW = "morning-triage"

#: Fallback look-back when no digest has ever completed. A day, because the schedule default is
#: daily; the first run of a fresh install therefore sees one day, not the whole backlog, which
#: is the difference between a digest and a wall of text.
DEFAULT_WINDOW_HOURS = 24

#: The node id the ledger rows are stamped with when the engine did not supply one.
_NODE_ID = "triage"


def _proactive_config() -> Any:
    from personalclaw.config.loader import AppConfig

    return AppConfig.load().proactive


def _window(config: dict[str, Any]) -> tuple[float, str]:
    """(epoch seconds, ISO string) for the window start — last completed digest, else N hours.

    Both spellings are returned because the three lanes compare against different stamps: the
    inbox store keeps epoch floats, the run store keeps ISO strings. Converting at the boundary
    once beats each collector guessing.
    """
    from datetime import UTC, datetime

    try:
        hours = float(config.get("window_hours") or DEFAULT_WINDOW_HOURS)
    except (TypeError, ValueError):
        hours = DEFAULT_WINDOW_HOURS
    fallback = time.time() - max(0.0, hours) * 3600.0

    try:
        from personalclaw.workflows.store import list_runs

        runs, _total = list_runs(workflow_name=TRIAGE_WORKFLOW, status="completed", limit=1)
    except Exception:  # noqa: BLE001 - no run store → the fallback window is correct
        runs = []
    if runs:
        stamp = str(getattr(runs[0], "completed_at", "") or getattr(runs[0], "created_at", ""))
        if stamp:
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return (parsed.timestamp(), stamp)
            except ValueError:
                logger.debug("triage: unparseable last-digest stamp %r", stamp)
    return (fallback, datetime.fromtimestamp(fallback, UTC).isoformat())


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
    run_id = str(ctx.payload.get("run_id", "") or "")
    instance_path = str(ctx.payload.get("instance_path", "") or "")
    if not run_id or not instance_path:
        return 0

    from personalclaw.ledger.kinds import PROPOSAL_REFUSED, SKIPPED_TRIAGE
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
    return written


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

        since_ts, since_iso = _window(action_config)
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
            run_id=str(ctx.payload.get("run_id", "") or ""),
            trigger_id=str(ctx.payload.get("trigger_id", "") or ""),
        )

        summary = result.summary()
        summary["window_start"] = since_iso
        summary["ledger_rows"] = _record(result, ctx)
        summary["notes"] = list(result.notes)
        return ActionResult(success=True, exit_code=0, stdout=json.dumps(summary))


def create_provider(config: dict[str, Any] | None = None) -> "TriageDigestActionProvider":
    return TriageDigestActionProvider()
