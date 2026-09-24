"""``inbox-op`` action provider — the triage tier's hands (PROACTIVE-ASSISTANT §1.6).

Five operations against the inbox a triage proposal can bind arguments to: ``archive``,
``mark_read``, ``mute_thread``, ``dismiss`` and ``reply_draft``. Registered like every other
native action, so once it exists it is usable by ALL trigger kinds, not only by triage.

**Every operation is reversible, and that is the reason this provider exists at all.** §1.6
makes reversibility the definition of the trivial-capable class: an action the user can take
back in one click is one the machine may perform unattended. So each execution returns an
opaque ``reversal`` handle carrying the PRIOR state, and :meth:`reverse` restores it. A handle
is base64 rather than a colon-joined string because two of the three things it has to carry —
an item id (``{channel}_{ts}``, and a channel name may contain anything) and a previous draft
(free text) — cannot be delimited by any character reserved in the handle grammar.

**``reply_draft`` writes a DRAFT and never sends.** That is §1.6 bound 2 expressed in the
provider rather than trusted to the caller: even a user's own always-approve rule for
``reply_draft`` reaches this code, and this code has no send path. Graduating a pattern to an
actual send is a separate per-rule toggle over a send-capable provider, and it is deliberately
not reachable from here.

**Writes go through :func:`personalclaw.inbox.live_store`.** The running service holds items in
MEMORY and never re-reads the file, so a provider that constructed its own ``InboxStore()``
would write a row the API cannot see and that the service's next save silently overwrites. A
missing live store is reported as a failure, not worked around.

``action_config`` shape::

    {"op": "archive", "item_id": "C123_1712000000.5", "draft": "…"}

``op`` also accepts the proposal vocabulary spelling under the key ``action_type``, so a
:class:`~personalclaw.proactive.proposals.Proposal` binds without a translation layer.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from personalclaw.inbox import ItemStatus, live_state, live_store, redact_item

logger = logging.getLogger(__name__)

#: The handle kind. The only part of a reversal handle any shared code reads —
#: :func:`personalclaw.guardrails.ladder.reverse_action` matches it against
#: :attr:`ActionProvider.reversal_kinds` to find a provider willing to undo one.
HANDLE_KIND = "inbox-op"

#: Status-setting ops, and the status each sets. `archive` lands on HANDLED rather than
#: DISMISSED: the two are different terminal states in `ItemStatus`, and archiving is the
#: "I dealt with this" one. `dismiss` is here too even though §1.3 floors it at `high` (so it
#: never auto-executes without a taught rule) — the provider still has to be able to perform
#: it when a user's own rule or a hand-written trigger asks.
_STATUS_OPS: dict[str, str] = {
    "archive": ItemStatus.HANDLED.value,
    "mark_read": ItemStatus.SEEN.value,
    "dismiss": ItemStatus.DISMISSED.value,
}

#: Every op this provider performs. A closed set, checked before anything is touched, so an
#: injected `action_type` cannot reach a code path by resembling one.
OPS: frozenset[str] = frozenset({*_STATUS_OPS, "mute_thread", "reply_draft"})


def _thread_key(item: Any) -> str:
    """The mute key for `item`'s thread — the same derivation the inbox API uses.

    ``PUT /api/inbox/{id}`` computes ``item.thread_ts or item.id.split("_", 1)[1]``, and this
    has to agree with it exactly: a mute written under a different key is a mute the UI's
    unmute cannot find, and the thread would stay silent with nothing to click.
    """
    thread = getattr(item, "thread_ts", None)
    if thread:
        return str(thread)
    raw = str(getattr(item, "id", "") or "")
    return raw.split("_", 1)[1] if "_" in raw else raw


def _encode(payload: dict[str, Any]) -> str:
    blob = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return f"{HANDLE_KIND}:{blob}"


def _decode(handle: str) -> dict[str, Any] | None:
    kind, _, blob = (handle or "").partition(":")
    if kind != HANDLE_KIND or not blob:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(blob.encode()).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _broadcast(state: Any, item: Any) -> None:
    """Push the mutated row to any open dashboard. Best-effort by design.

    An auto-executed archive the open UI does not learn about reads as a stuck row until the
    next poll — the user watches the digest claim it archived something that is visibly still
    there. A failed broadcast must not fail the action, though: the write already landed.
    """
    try:
        state.broadcast_ws("inbox_item_updated", redact_item(item.to_dict()))
    except Exception:  # noqa: BLE001 - a UI push is never worth failing a landed write for
        logger.debug("inbox-op: broadcast failed", exc_info=True)


class InboxOpActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "inbox-op"

    @property
    def display_name(self) -> str:
        return "Inbox Operation"

    @property
    def reversal_kinds(self) -> tuple[str, ...]:
        return (HANDLE_KIND,)

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.action_providers.services import get_action_services

        op = str(action_config.get("op") or action_config.get("action_type") or "").strip()
        item_id = str(action_config.get("item_id") or "").strip()
        if op not in OPS:
            return ActionResult(
                success=False,
                error=f"inbox-op: unknown op {op!r} (expected one of {', '.join(sorted(OPS))})",
            )
        if not item_id:
            return ActionResult(success=False, error="inbox-op: item_id is required")

        services = get_action_services()
        state = getattr(services, "state", None)
        store = live_store(state) if state is not None else None
        if store is None:
            return ActionResult(
                success=False,
                error=(
                    "inbox-op: no running inbox service — an operation written to a store the "
                    "service cannot see would be overwritten by its next save"
                ),
            )
        item = store.items.get(item_id)
        if item is None:
            return ActionResult(success=False, error=f"inbox-op: no inbox item {item_id!r}")

        if op in _STATUS_OPS:
            prior = str(getattr(item, "status", "") or "")
            target = _STATUS_OPS[op]
            if prior == target:
                # Not an error and not a lie: the effect the caller wanted already holds, so
                # there is nothing to undo and no handle is offered.
                return ActionResult(
                    success=True,
                    stdout=json.dumps({"op": op, "item_id": item_id, "changed": False}),
                )
            store.update(item_id, status=target)
            if op == "dismiss":
                inbox_state = live_state(state)
                if inbox_state is not None:
                    inbox_state.dismissed.add(item_id)
                    inbox_state.save()
            _broadcast(state, store.items.get(item_id) or item)
            handle = _encode({"op": op, "item_id": item_id, "prior": prior})
            return ActionResult(
                success=True,
                stdout=json.dumps({"op": op, "item_id": item_id, "changed": True}),
                reversal=handle,
            )

        if op == "mute_thread":
            inbox_state = live_state(state)
            if inbox_state is None:
                return ActionResult(
                    success=False,
                    error="inbox-op: no running inbox service, so the mute set is unreachable",
                )
            key = _thread_key(item)
            already = key in inbox_state.muted_threads
            if already:
                return ActionResult(
                    success=True,
                    stdout=json.dumps({"op": op, "thread": key, "changed": False}),
                )
            inbox_state.muted_threads.add(key)
            inbox_state.save()
            return ActionResult(
                success=True,
                stdout=json.dumps({"op": op, "thread": key, "changed": True}),
                reversal=_encode({"op": op, "item_id": item_id, "thread": key}),
            )

        # reply_draft — writes the draft field and nothing else. There is no send path here.
        text = str(action_config.get("draft") or action_config.get("body") or "").strip()
        if not text:
            return ActionResult(success=False, error="inbox-op: reply_draft needs a draft body")
        prior_draft = str(getattr(item, "draft", "") or "")
        store.update(item_id, draft=text)
        _broadcast(state, store.items.get(item_id) or item)
        return ActionResult(
            success=True,
            stdout=json.dumps({"op": op, "item_id": item_id, "drafted": len(text)}),
            reversal=_encode({"op": op, "item_id": item_id, "prior": prior_draft}),
        )

    async def reverse(self, handle: str) -> ActionResult:
        """Restore the prior state a handle carries.

        Resolves against what EXISTS and refuses otherwise, per the base contract: an
        optimistic "sure, done" would take away the user's undo and their evidence in one
        call. The state check is deliberately strict — if the item has moved on since (the
        user archived it themselves, another rule dismissed it), the reversal refuses rather
        than clobbering the newer state with an older one.
        """
        from personalclaw.action_providers.services import get_action_services

        payload = _decode(handle)
        if payload is None:
            return ActionResult(success=False, error="inbox-op: unrecognised reversal handle")
        op = str(payload.get("op") or "")
        item_id = str(payload.get("item_id") or "")

        services = get_action_services()
        state = getattr(services, "state", None)
        store = live_store(state) if state is not None else None
        if store is None:
            return ActionResult(
                success=False, error="inbox-op: no running inbox service to undo against"
            )

        if op == "mute_thread":
            inbox_state = live_state(state)
            key = str(payload.get("thread") or "")
            if inbox_state is None or key not in inbox_state.muted_threads:
                return ActionResult(success=False, error="inbox-op: that thread is no longer muted")
            inbox_state.muted_threads.discard(key)
            inbox_state.save()
            return ActionResult(success=True, stdout=json.dumps({"undone": op, "thread": key}))

        item = store.items.get(item_id)
        if item is None:
            return ActionResult(
                success=False,
                error=f"inbox-op: inbox item {item_id!r} is gone, so there is nothing to restore",
            )
        prior = str(payload.get("prior") or "")

        if op in _STATUS_OPS:
            if str(getattr(item, "status", "") or "") != _STATUS_OPS[op]:
                return ActionResult(
                    success=False,
                    error=(
                        f"inbox-op: {item_id!r} is no longer {_STATUS_OPS[op]!r}, so undoing the "
                        f"{op} would overwrite a newer change"
                    ),
                )
            store.update(item_id, status=prior or ItemStatus.PENDING.value)
            if op == "dismiss":
                inbox_state = live_state(state)
                if inbox_state is not None:
                    inbox_state.dismissed.discard(item_id)
                    inbox_state.save()
            _broadcast(state, store.items.get(item_id) or item)
            return ActionResult(success=True, stdout=json.dumps({"undone": op, "item_id": item_id}))

        if op == "reply_draft":
            store.update(item_id, draft=prior)
            _broadcast(state, store.items.get(item_id) or item)
            return ActionResult(success=True, stdout=json.dumps({"undone": op, "item_id": item_id}))

        return ActionResult(success=False, error=f"inbox-op: cannot undo {op!r}")


def create_provider(config: dict[str, Any] | None = None) -> "InboxOpActionProvider":
    return InboxOpActionProvider()
