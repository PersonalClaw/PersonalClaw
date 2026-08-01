"""Projecting a waiting run into the platform's attention surfaces (WF2-R7, WF2-R11).

A run that parks on `needs_input` is the one engine state whose resolution requires a human.
Until this module existed the only place that fact appeared was an SSE frame — so if nobody
happened to have the run view open, an approval gate waited silently forever. A scheduled run
firing at 3am would park and simply never be mentioned.

That is the gap this closes. A gate raises a **durable inbox item** plus **one** notification,
through `emit_attention_item` — the same seam the loop watchdog uses, for the same reason: a
caller that did `store.add(...)` and `state.notify(...)` separately drifts the two apart, and
the usual result is two notifications for one event or an inbox row nobody was told about.

Three properties this has to get right:

**Deduped per (run, node, epoch).** The watchdog re-polls a waiting run every few seconds and
`_ensure_continuation` is idempotent per epoch — so without a dedup key a gate would stack a
row per poll, each with a valid resume token. Keyed on the EPOCH rather than the token because
a rewind legitimately re-asks the same question, and that genuinely is a new ask.

**Resolved when answered.** An inbox row that stays open after its gate is answered is worse
than no row: the user clicks it, finds nothing to do, and stops trusting the inbox. The
resolution runs off the same `gate_resolved` moment the widget uses.

**Never load-bearing.** Every function here is best-effort and swallows. A run must not fail
because the inbox could not be written — the gate is what the user is waiting on, and losing
the run to a bookkeeping error is strictly worse than losing the row.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The registered notification pair. `loop/needs_input` already exists, carries
#: `attention=True`, and is what a user's "always interrupt me for needs_input" rule keys on —
#: so a workflow gate rides the SAME pair rather than inventing a second one a user would have
#: to configure separately to get the same behaviour.
SOURCE = "loop"
KIND = "needs_input"


def dedup_key(run_id: str, instance_path: str, epoch: int) -> str:
    """The idempotency key for one gate's attention item.

    Epoch-scoped, not token-scoped: a rewind re-asks the same question at a NEW epoch, and
    that is a genuinely new ask deserving its own row. Two polls of the same waiting gate are
    not.
    """
    return f"workflow:{run_id}:{instance_path}:{epoch}"


def ask_title(workflow: str, node_id: str, ask: dict[str, Any] | None) -> str:
    """The row's one-line summary.

    The ask's own prompt when it has one — that is the actual question, and a generic
    "workflow needs input" forces the user to open the row to learn anything. Truncated,
    because an inbox row is a glance and a model-authored prompt can be a paragraph.
    """
    prompt = str((ask or {}).get("prompt") or "").strip()
    if prompt:
        return prompt if len(prompt) <= 120 else prompt[:117] + "…"
    label = node_id or "a step"
    return f"{workflow}: {label} needs your input"


def ask_body(ask: dict[str, Any] | None, handoff: dict[str, Any] | None) -> str:
    """The row's detail: what kind of answer is wanted, and what the run was doing.

    The handoff's outstanding work is included because the decision often depends on it — "is
    this the last step or are eight more waiting on me?" changes how urgently a user acts.
    """
    parts: list[str] = []
    kind = str((ask or {}).get("kind") or "").strip()
    if kind:
        parts.append(
            {
                "approval": "Waiting for your approval.",
                "choice": "Waiting for you to choose an option.",
                "text": "Waiting for a written answer.",
                "form": "Waiting for you to fill in a form.",
            }.get(kind, f"Waiting for a {kind} answer.")
        )
    outstanding = (handoff or {}).get("outstanding")
    if isinstance(outstanding, list) and outstanding:
        parts.append(f"{len(outstanding)} other step(s) still pending.")
    return " ".join(parts)


def raise_gate_item(
    state: Any,
    *,
    run_id: str,
    workflow: str,
    node_id: str,
    instance_path: str,
    epoch: int,
    resume_token: str,
    ask: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
) -> str:
    """Project one waiting gate into the inbox + a notification. Returns the item id or "".

    `refs` carries the run id AND the resume token, which is what makes the row actionable
    rather than a notification with extra steps: the surface reading it can answer in place
    instead of sending the user off to find the run.
    """
    if state is None:
        return ""
    try:
        from personalclaw.inbox import ItemKind, emit_attention_item

        return emit_attention_item(
            state,
            source=SOURCE,
            kind=KIND,
            item_kind=ItemKind.NEEDS_INPUT.value,
            title=ask_title(workflow, node_id, ask),
            body=ask_body(ask, handoff),
            refs={
                "workflow": run_id,
                "workflow_name": workflow,
                "workflow_node": node_id,
                "resume_token": resume_token,
            },
            dedup_key=dedup_key(run_id, instance_path, epoch),
        )
    except Exception:
        # Best-effort by contract: the gate is what the user is waiting on, and losing the run
        # to a bookkeeping failure is strictly worse than losing the row.
        logger.debug("workflow %s: could not raise the gate attention item", run_id, exc_info=True)
        return ""


#: Statuses an attention row can still be closed FROM. A row the user already dismissed or a
#: draft already sent must not be silently rewritten — the user's own action wins.
_OPEN_STATUSES = ("pending", "seen")


def resolve_gate_item(state: Any, run_id: str, node_id: str = "") -> int:
    """Close the open inbox row(s) for an answered (or expired) gate. Returns the count.

    Called on the same `gate_resolved` moment the widget folds. An inbox row that outlives its
    gate is worse than no row at all — the user opens it, finds nothing to do, and learns to
    distrust the surface.

    Scoped by node when one is named: a run with two concurrent gates has two rows, and
    answering one must not close the other. With no node it closes every open row for the run,
    which is what a run ENDING means (see :func:`resolve_run_items`).

    Takes the state for `live_store`: writing through a fresh `InboxStore()` closed the row in a
    detached copy the service then overwrote, so an answered gate's row stayed open in the UI.
    Found by approving a real gate in a real browser and watching the row survive it.
    """
    try:
        from personalclaw.inbox import InboxStore, ItemStatus, live_store

        store = live_store(state)
        if store is None:
            store = InboxStore()
            store.load()
        closed = 0
        for item in list(store.items.values()):
            if item.refs.get("workflow") != run_id:
                continue
            if node_id and item.refs.get("workflow_node") != node_id:
                continue
            if item.status in _OPEN_STATUSES:
                # HANDLED, not DISMISSED: the user (or the engine on their behalf) actually
                # answered it. Dismissed would read as "ignored", which is a different fact and
                # feeds the engagement signals differently.
                item.status = ItemStatus.HANDLED.value
                closed += 1
        if closed:
            store.save()
        return closed
    except Exception:
        logger.debug(
            "workflow %s: could not resolve the gate attention item", run_id, exc_info=True
        )
        return 0


def resolve_run_items(state: Any, run_id: str) -> int:
    """Close every open attention row for a run. Returns how many were closed.

    A run that failed, was cancelled or completed answers its own outstanding questions by
    ending: nothing about it is actionable any more. Without this, cancelling a run mid-gate
    would leave a permanently unanswerable row in the inbox — the exact dead-row problem this
    module exists to avoid, arrived by a different path.
    """
    return resolve_gate_item(state, run_id)
