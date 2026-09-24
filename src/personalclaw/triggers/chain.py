"""The `run_completed` chain runtime — "when X finishes, run Y" (§7 item 8 — S122).

**🔴 THE DEFECT THIS CLOSES.** `run_completed` is a declared kind with no firing path. It is in
`KINDS`, `SPEC_KEYS` accepts `{source_trigger, source_def}`, the store persists it, `/api/triggers`
lists it and the Automations page renders it. Nothing ever fired one. Measured first, with a
`clock:nightly` and a `run_completed:after` pointed at it:

    clock tick considered: ['clock:nightly']       # the source fires
    file poller:           []
    web poller:            []
    → run_completed:after is reached by NOTHING

So "when my nightly backup finishes, notify me" was creatable, listed, and permanently silent. The
same present-and-inert shape as S121's `web_watch` and S93's `file`, and the third instance of it in
this kind's table — which is why the completeness test in `tests/test_triggers_chain.py` now asserts
every declared kind either has a runtime or a documented reason.

**Chaining is where an automation system earns its keep, and also where it eats itself.** So the two
controls here are not optional:

* **A DEPTH CAP.** A → B → C is useful; A → B → A is an infinite fire loop that a scheduler cannot
  distinguish from enthusiasm. The chain carries its own depth in the payload, and `MAX_CHAIN_DEPTH`
  refuses past it with a visible reason rather than silently stopping.
* **CYCLE DETECTION on the path, not just the depth.** A cap alone bounds the damage but reports
  it as "too deep" when the real answer is "this chain loops". The payload carries the ids already
  fired in this chain, so a repeat is named as a cycle — the difference between a user fixing their
  config and a user believing the depth limit is too low.

**Fired through the SAME dispatch as every other kind**, so a chained action executes identically
to a clock one and passes every gate in `firepath` — including the kill switch (S117) and the
capability fence (S116). A chain with its own dispatch path would be a second place for those
controls to be forgotten, which is precisely how the `web_watch` gap happened.

**What this does NOT own:** the source run's outcome classification (the executor's), and matching
on run OUTCOME (`only_on: failed`) — `SPEC_KEYS` declares only `{source_trigger, source_def}`, and
adding a key the entity does not carry would be a fence nobody can author.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.triggers.provider import armable
from personalclaw.triggers.routing import routed

logger = logging.getLogger(__name__)

#: How many links a chain may have. A → B → C is a real workflow; deeper is almost always a mistake,
#: and every link past this is unattended work the user did not schedule directly. Bounded low on
#: purpose: the cost of being wrong in the permissive direction is a fire loop.
MAX_CHAIN_DEPTH = 3

#: Payload keys the chain owns. Named as data so a reader sees exactly what a chained fire
#: carries beyond a normal one, and so the depth/path cannot be spelled two ways in two places.
DEPTH_KEY = "chain_depth"
PATH_KEY = "chain_path"


def chain_triggers(store: Any, *, source_id: str) -> list[Any]:
    """Every enabled `run_completed` trigger waiting on `source_id`.

    Matches `source_trigger` against the completed trigger's id, and `source_def` against its
    workflow ref — the two keys `SPEC_KEYS` declares. A trigger with neither key matches nothing
    rather than everything: a chain that fired on every run in the system would be a fire storm
    authored by omission.

    Reads a :func:`~personalclaw.triggers.routing.routed` store (TSE-5) so a shared/team ``trigger``
    provider can contribute the "when the team brief finishes, notify me" half of a cascade. Safe
    here, unlike the poll loops: a ``run_completed`` row holds no schedule to advance,
    and the write its fire produces (the gateway's outcome recorder) is routed back to the serving
    store by :meth:`personalclaw.triggers.store.TriggerStore.upsert`.
    """
    out: list[Any] = []
    for trigger in armable(routed(store)):
        if trigger.kind != "run_completed" or not trigger.enabled:
            continue
        spec = trigger.spec if isinstance(trigger.spec, dict) else {}
        wanted = str(spec.get("source_trigger", "") or "").strip()
        if wanted and wanted == source_id:
            out.append(trigger)
    return out


def chain_triggers_for_def(store: Any, *, source_def: str) -> list[Any]:
    """Every enabled `run_completed` trigger waiting on a workflow DEF rather than a trigger id.

    Separate from `chain_triggers` because the two are genuinely different questions: "after that
    automation" and "after any run of that workflow". Merging them behind one argument would make a
    caller that knew only the trigger id accidentally match def-keyed rows.
    """
    if not source_def:
        return []
    out: list[Any] = []
    for trigger in armable(routed(store)):
        if trigger.kind != "run_completed" or not trigger.enabled:
            continue
        spec = trigger.spec if isinstance(trigger.spec, dict) else {}
        if str(spec.get("source_def", "") or "").strip() == source_def:
            out.append(trigger)
    return out


def chain_refusal(payload: dict[str, Any], *, next_id: str) -> str:
    """Why this chain must stop here, or "" to proceed.

    Returns a REASON rather than a bool because §7 criterion 8 bans silent drops and the two
    refusals mean different things to the person who has to fix the config: "too deep" is a
    legitimate chain that outgrew the cap, "loops" is a mistake. Reporting a cycle as a depth
    overflow sends someone off to raise a limit that was never the problem.
    """
    path = payload.get(PATH_KEY)
    seen = [str(p) for p in path] if isinstance(path, list) else []

    if next_id in seen:
        return (
            f"chain cycle: {next_id} already fired in this chain "
            f"({' → '.join(seen)}), so it is refused rather than looping"
        )

    try:
        depth = int(payload.get(DEPTH_KEY, 0) or 0)
    except (TypeError, ValueError):
        depth = 0
    if depth >= MAX_CHAIN_DEPTH:
        return (
            f"chain depth limit reached ({MAX_CHAIN_DEPTH}); {next_id} is refused. "
            f"Chain so far: {' → '.join(seen) or 'unknown'}"
        )
    return ""


def chain_payload(
    source_payload: dict[str, Any],
    *,
    source_id: str,
    trigger: Any,
) -> dict[str, Any]:
    """The payload a chained fire receives: its own identity, plus the chain's provenance.

    The depth and path are carried IN THE PAYLOAD rather than in a sidecar deliberately. A chain
    is a single logical cascade whose state lives exactly as long as it does; persisting it means
    reconciling an abandoned chain's leftovers on every boot, and a chain interrupted by a restart
    should simply stop.
    """
    path = source_payload.get(PATH_KEY)
    seen = [str(p) for p in path] if isinstance(path, list) else []
    try:
        depth = int(source_payload.get(DEPTH_KEY, 0) or 0)
    except (TypeError, ValueError):
        depth = 0

    return {
        "trigger_id": trigger.id,
        "trigger_name": trigger.name,
        "kind": "run_completed",
        # What finished, so the action can say why it is running at all.
        "source_trigger_id": source_id,
        DEPTH_KEY: depth + 1,
        PATH_KEY: [*seen, source_id] if source_id not in seen else seen,
    }


def next_fires(
    store: Any,
    *,
    source_id: str,
    source_payload: dict[str, Any] | None = None,
    source_def: str = "",
) -> tuple[list[tuple[Any, dict[str, Any]]], list[dict[str, str]]]:
    """`(fires, refused)` for one completed run.

    `fires` is `(trigger, payload)` pairs for the caller to dispatch; `refused` carries
    `{trigger_id, reason}` so the caller can write the ledger rows criterion 8 requires. Both are
    returned rather than the caller re-deriving refusals, because a chain that stopped with no row
    is indistinguishable from one that was never configured.
    """
    payload = dict(source_payload or {})
    candidates = list(chain_triggers(store, source_id=source_id))
    if source_def:
        seen_ids = {t.id for t in candidates}
        candidates += [
            t for t in chain_triggers_for_def(store, source_def=source_def) if t.id not in seen_ids
        ]

    fires: list[tuple[Any, dict[str, Any]]] = []
    refused: list[dict[str, str]] = []
    for trigger in candidates:
        reason = chain_refusal(payload, next_id=trigger.id)
        if reason:
            logger.info("run_completed %s refused: %s", trigger.id, reason)
            refused.append({"trigger_id": trigger.id, "reason": reason})
            continue
        fires.append((trigger, chain_payload(payload, source_id=source_id, trigger=trigger)))
    return fires, refused
