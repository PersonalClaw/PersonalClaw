"""The Morning triage digest as it stands: its schedule, its last run, and the card's view of both.

Read by every surface that shows or answers the digest — the card's routes
(`dashboard.handlers.proactive`) and a reply on the chat channel the digest reached
(`proactive.channel_reply`) — so an answer typed on a channel reads the same digest a tap on the
card reads. Below the HTTP surface on purpose: a channel's reply arrives through the guarded door,
which must not import a route handler to find out what the current digest is.

**Nothing here reports an unmeasured value as a zero.** No run is ``(None, None, [])``, which the
view turns into ``never_run``, never into an empty digest; see :mod:`personalclaw.proactive.surface`
for the state vocabulary that keeps "off", "never run", "empty" and "broken" apart.
"""

from __future__ import annotations

import json
from typing import Any

#: The schedule the digest installs, under a DETERMINISTIC id (the `system:heartbeat:fts` convention
#: `triggers.models` documents). That is what makes the install idempotent with nothing to
#: remember: a second install finds this row instead of adding a duplicate, and the reconcile can
#: address it after a restart. `created_by="system"` is the closed three-value vocabulary the field
#: declares (`user`/`agent`/`system`) — it is the id that carries the feature name, which is also
#: what the dual-writer rule needs to edit-lock the row on the Automations page.
TRIAGE_TRIGGER_ID = "system:triage:digest"
TRIAGE_CREATED_BY = "system"


def trigger_store() -> Any:
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore()


def find_schedule(store: Any) -> Any:
    """The digest schedule, by its deterministic id, or None.

    `store.get` returns a `LoadedTrigger` — the row PLUS whatever was wrong with reading it. The
    entity to write is the `.trigger` inside; upserting the pair would set attributes on the
    wrapper and persist something with no id.
    """
    row = store.get(TRIAGE_TRIGGER_ID)
    return None if row is None else row.trigger


def schedule_payload(trigger: Any) -> dict[str, Any]:
    spec = getattr(trigger, "spec", None) or {}
    return {
        "id": str(getattr(trigger, "id", "") or ""),
        "name": str(getattr(trigger, "name", "") or ""),
        "cron": str(spec.get("expr", "") or "") if isinstance(spec, dict) else "",
        "enabled": bool(getattr(trigger, "enabled", False)),
        "created_by": str(getattr(trigger, "created_by", "") or ""),
    }


def install_state() -> dict[str, Any]:
    """Installedness + the drift between the config switch and the schedule's own flag.

    ``drift`` is reported rather than silently repaired on a READ. Disabling
    ``triage_enabled`` must retire the schedule, and the reconcile that does it is a POST — so a
    GET that quietly fixed the divergence would hide from the user that two switches had
    disagreed, and would make the read a writer.
    """
    from personalclaw.config.loader import AppConfig

    proactive = getattr(AppConfig.load(), "proactive", None)
    enabled = bool(getattr(proactive, "triage_enabled", False))
    trigger = find_schedule(trigger_store())
    if trigger is None:
        return {"installed": False, "enabled": enabled, "schedule": None, "drift": False}
    payload = schedule_payload(trigger)
    return {
        "installed": True,
        "enabled": enabled,
        "schedule": payload,
        "drift": payload["enabled"] != enabled,
    }


def _digest(status: str = "") -> tuple[dict | None, dict | None, list[dict]]:
    """The most recent triage run (of *status*, when given), its node output and its ledger."""
    from personalclaw.proactive.surface import TRIAGE_NODE_ID, TRIAGE_WORKFLOW
    from personalclaw.workflows import journal, service, store

    runs, _total = store.list_runs(workflow_name=TRIAGE_WORKFLOW, status=status, limit=1, offset=0)
    if not runs:
        return None, None, []
    run = runs[0].to_dict()
    run_id = str(run.get("run_id", "") or run.get("id", "") or "")
    result = service.output(run_id, TRIAGE_NODE_ID)
    output: dict | None = None
    if result.get("ok"):
        output = decode_output(result.get("output"))
    return run, output, journal.ledger(run_id)


def latest_digest() -> tuple[dict | None, dict | None, list[dict]]:
    """The most recent triage run, its node output and its ledger slice.

    Returns ``(None, None, [])`` when no run exists — which the view turns into ``never_run``,
    never into an empty digest.
    """
    return _digest()


def last_completed_digest() -> tuple[dict | None, dict | None, list[dict]]:
    """The most recent triage run that COMPLETED, its node output and its ledger slice.

    What the next digest starts from: its window begins where this run began, and what this
    run's card still has waiting on you is carried into it (`proactive.carry`). The run store's
    own word for it (``RunStatus.COMPLETE``). A digest that failed is neither: what it would have
    shown is still the last completed one's.
    """
    from personalclaw.workflows.models import RunStatus

    return _digest(RunStatus.COMPLETE.value)


def decode_output(value: Any) -> dict | None:
    """The triage node's output as a dict, whether it was stored as JSON text or as an object.

    The provider returns its summary as `ActionResult.stdout` (a JSON string), and the engine may
    hand it back either already-parsed or verbatim depending on the node's transform. Both are
    accepted; anything else is `None`, which the view reports as `never_run` rather than as a
    digest with every section empty.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def current_view() -> dict[str, Any]:
    """The card's view of the digest as it stands now (`surface.build_digest_view`).

    Raises what the reads raise: a caller that answers from it must not act on a digest it could
    not read.
    """
    from personalclaw.proactive.surface import build_digest_view

    state = install_state()
    run, output, events = latest_digest()
    return build_digest_view(
        enabled=state["enabled"],
        installed=state["installed"],
        run=run,
        output=output,
        events=events,
    )


__all__ = [
    "TRIAGE_CREATED_BY",
    "TRIAGE_TRIGGER_ID",
    "current_view",
    "decode_output",
    "find_schedule",
    "install_state",
    "last_completed_digest",
    "latest_digest",
    "schedule_payload",
    "trigger_store",
]
