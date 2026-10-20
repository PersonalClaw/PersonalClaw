"""Inbox sorting — the background pass that gives each new message its triage verdict.

A message reaches the Inbox unsorted (``classification`` ``""``, which the Inbox shows as "Not
sorted yet"). :class:`InboxSorter` is woken when one arrives, waits a moment for the rest of a
burst, then sends the waiting messages to the background model in batches of at most
:data:`BATCH_MAX`, one call per batch, through the prompt bound for sorting (``inbox_classify``,
Settings › Prompts). Each answer lands on its row with the prompt that produced it
(``classified_by``), which is what a verdict she gives on the row is rated against. A message the
model could not sort carries the reason (``classify_error``) until she sorts it again
(``POST /api/inbox/{id}/sort``) or sorts it herself.

What it reads, and when it waits:

* **Messages only.** A row that is not a message (a notice, a proposal, her own note) is never
  sorted and carries no verdict; nor is an agent's own post (``post_to_inbox``), whose kind the
  agent gave when it posted and whose text may come from a Temporary or Incognito chat, which no
  background pass reads.
* **Once per message.** A message is sent while it is unsorted, never again once it has a
  verdict or a failure, and one pass runs at a time. A verdict she gave while the model was
  reading is kept: the answer lands only on a row still unsorted.
* **Inside the rules every background pass keeps.** Nothing is sent while sorting is off in
  Settings › Inbox (``inbox.sort_messages``) or incident mode is on, and a call the daily spend
  ceiling refuses, or that no background model could take (none is set up, or the bound one is
  resting after failing), leaves its messages unsorted. Each is a hold: no row changes, the
  reason is the sorter's :meth:`InboxSorter.health`, and the next wake tries again.

Each message is fenced on its own (``fence_message_for_prompt``) with its channel inside the
fence, so one message's text cannot read as a note about its neighbours.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from personalclaw import shutdown_event
from personalclaw.inbox import (
    OPEN_STATUSES,
    SOURCE_DECLARABLE_KINDS,
    Classification,
    Confidence,
    InboxItem,
    InboxStore,
    redact_item,
)

logger = logging.getLogger(__name__)

#: The prompt use case a sorting call renders (``task-inbox-classify`` unless she bound another).
USE_CASE = "inbox_classify"

#: The most messages one sorting call reads. A burst of mail is a few calls, never one per mail.
BATCH_MAX = 10

#: How much of each message, and of its thread, a sorting call reads: enough to tell a question
#: from a notice. A reply is drafted from the whole message (``inbox_service``).
MESSAGE_CHARS = 1500
THREAD_TURNS = 3

#: How long the sorter waits after a wake for the rest of a burst to arrive.
SETTLE_SECS = 2.0

#: Where an agent's own post comes from (``inbox_providers.native_source.SOURCE_NAME``).
AGENT_POST_SOURCE = "native"

_VERDICTS = frozenset(c.value for c in Classification)
_MACHINE_CONFIDENCES = frozenset(
    {Confidence.HIGH.value, Confidence.NEEDS_REVIEW.value, Confidence.ESCALATE.value}
)

#: What a message reads when its sorting call failed, as the row says it.
NOT_RENDERED = (
    "The sorting prompt could not be rendered, so nothing was sent. Check the prompt bound for "
    "inbox sorting in Settings › Prompts."
)
UNREADABLE = "The background model answered, but its answer could not be read."
LEFT_OUT = "The background model's answer left this message out."


def wants_sorting(item: InboxItem) -> bool:
    """Whether *item* is a message nobody has sorted and the sorter has not failed on."""
    kind = str(item.item_kind or "message")
    return (
        kind in SOURCE_DECLARABLE_KINDS
        and item.source != AGENT_POST_SOURCE
        and not item.classification
        and not item.classify_error
    )


def sorting_hold() -> str:
    """Why nothing may be sent for sorting right now, as a sentence; ``""`` when it may.

    Fails closed: a configuration that cannot be read sends nothing, since it may be the one
    that turned sorting off."""
    try:
        from personalclaw.config.loader import AppConfig

        if not AppConfig.load().inbox.sort_messages:
            return "Sorting is off in Settings › Inbox, so new messages stay unsorted."
    except Exception:  # noqa: BLE001 — an unread switch is not an on switch
        logger.warning("inbox sorting: the configuration could not be read", exc_info=True)
        return "Sorting waits: the configuration could not be read."
    from personalclaw.guardrails.incident import incident_active

    if incident_active():
        return "Incident mode is on, so nothing is sorted until it is turned off."
    return ""


def render_messages(items: list[InboxItem]) -> str:
    """The batch as the model reads it: one numbered, separately fenced block per message."""
    from personalclaw.inbox_service import fence_message_for_prompt

    blocks = []
    for n, item in enumerate(items, 1):
        fenced = fence_message_for_prompt(
            item,
            max_chars=MESSAGE_CHARS,
            max_turns=THREAD_TURNS,
            heading=f"Channel: {item.channel_name or item.channel}",
        )
        blocks.append(f"{n}. {fenced}")
    return "\n\n".join(blocks)


def _ordinal(value: Any, count: int) -> int | None:
    """A verdict's message number, when it names one of the *count* messages sent."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
    else:
        return None
    return n if 1 <= n <= count else None


def parse_verdicts(raw: str, count: int) -> dict[int, tuple[str, str]]:
    """The model's answer → ``{message number: (classification, confidence)}``.

    Only a verdict naming one of the messages sent, with a classification from the closed set,
    is taken; the first for a number wins. A confidence outside the set is left ``""`` rather
    than guessed. A message with no verdict here is a failure, never a default."""
    from personalclaw.llm_helpers import parse_llm_json

    try:
        data = parse_llm_json(raw or "")
    except Exception:  # noqa: BLE001 — an unreadable answer sorts nothing
        return {}
    rows = data.get("verdicts") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[int, tuple[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        n = _ordinal(row.get("message"), count)
        verdict = str(row.get("classification") or "").strip().lower()
        if n is None or n in out or verdict not in _VERDICTS:
            continue
        confidence = str(row.get("confidence") or "").strip().lower()
        out[n] = (verdict, confidence if confidence in _MACHINE_CONFIDENCES else "")
    return out


def verdicts_problem(raw: str, count: int) -> str:
    """What makes a sorting answer unusable, ``""`` when it sorts at least one of the *count*
    messages sent. One that sorts only some is used: the rest fail with :data:`LEFT_OUT`."""
    return "" if parse_verdicts(raw, count) else "no verdict for any message it was sent"


def _failure(exc: BaseException) -> str:
    from personalclaw.providers.failure_copy import sentence_with_detail

    return sentence_with_detail("The background model could not sort it.", exc)


class InboxSorter:
    """Sorts the new messages in one store, in the background, a batch per model call.

    Runs on the loop that owns the store (:meth:`start` from ``InboxService.start``), so the
    store is changed only there. :meth:`wake` is how anything that adds a message asks for a
    pass; the inbox's poll tick wakes it too, which is what picks up a message that arrived
    while sorting was held."""

    def __init__(self, store: InboxStore, *, settle_secs: float = SETTLE_SECS) -> None:
        self._store = store
        self._settle = settle_secs
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._hold = ""

    def start(self) -> None:
        """Start sorting, and sort what waited while nothing ran. Idempotent."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        self.wake()

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    def health(self) -> dict:
        """``held``: why sorting is waiting now ("" when it is not); ``waiting``: how many open
        messages are unsorted."""
        return {"held": self._hold, "waiting": len(self.waiting())}

    def waiting(self) -> list[InboxItem]:
        """The open messages waiting to be sorted, oldest first."""
        rows = [
            item
            for item in list(self._store.items.values())
            if wants_sorting(item) and str(item.status) in OPEN_STATUSES
        ]
        return sorted(rows, key=lambda item: float(item.created_at or 0.0))

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            if shutdown_event.is_set():
                return
            if self._settle:
                await asyncio.sleep(self._settle)
            try:
                while await self.sort_pending():
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — the sorter outlives any one pass
                logger.warning("inbox sorting pass failed", exc_info=True)

    async def sort_pending(self) -> int:
        """Sort the oldest waiting messages in one model call. Returns how many rows it settled
        (sorted, or failed with the reason); 0 when nothing waits or sorting is held."""
        self._hold = sorting_hold()
        if self._hold:
            return 0
        batch = self.waiting()[:BATCH_MAX]
        if not batch:
            return 0
        return await self._sort(batch)

    async def _sort(self, batch: list[InboxItem]) -> int:
        from personalclaw.guardrails.audit import caller_scope
        from personalclaw.guardrails.failure import (
            BudgetExceededError,
            CircuitOpenError,
            OutputContractError,
            PromptInjectionBlocked,
            SecretLeakBlocked,
        )
        from personalclaw.guardrails.rungs import ensure_core_action_types
        from personalclaw.llm.registry import ProviderResolutionError as NoModelBuilt
        from personalclaw.llm_helpers import one_shot_completion
        from personalclaw.prompt_providers.runtime import render_use_case_prompt
        from personalclaw.providers.prompt_use_cases import active_prompt_ref
        from personalclaw.providers.provider_bridge import ProviderResolutionError as NoModelBound

        # `inbox.classify` is a declared action type (it labels her own rows, autonomously).
        ensure_core_action_types()
        producer = active_prompt_ref(USE_CASE)
        prompt = render_use_case_prompt(USE_CASE, {"messages": render_messages(batch)})
        if not prompt:
            return self._settle_rows(batch, {}, NOT_RENDERED, producer)
        count = len(batch)
        try:
            # `caller_scope` names the background pass that spent this on the attempt row. An
            # answer with a verdict for none of the messages is that model failing the call, so
            # the chain's next model is asked inside it (`validate`).
            with caller_scope("inbox_triage"):
                raw = await one_shot_completion(
                    prompt,
                    use_case="background",
                    output_type=dict,
                    validate=lambda answer: verdicts_problem(answer, count),
                )
        except BudgetExceededError as exc:
            self._hold = exc.sentence()
            return 0
        except (NoModelBound, NoModelBuilt, CircuitOpenError) as exc:
            # Nothing could take the call: no background model is set up, or the one bound is
            # resting after failing. Nothing was sent, so the messages wait for the next tick.
            self._hold = f"Sorting waits for the background model: {exc}"
            return 0
        except OutputContractError as exc:
            raw = exc.raw
        except (SecretLeakBlocked, PromptInjectionBlocked) as exc:
            # Refused before the model ran, for what one message holds: sort the rest without it.
            if len(batch) > 1:
                half = len(batch) // 2
                return await self._sort(batch[:half]) + await self._sort(batch[half:])
            return self._settle_rows(batch, {}, _failure(exc), producer)
        except Exception as exc:  # noqa: BLE001 — a failed call is the rows' to say
            logger.warning("inbox sorting call failed for %d message(s)", len(batch), exc_info=True)
            return self._settle_rows(batch, {}, _failure(exc), producer)
        verdicts = parse_verdicts(raw, len(batch))
        return self._settle_rows(batch, verdicts, LEFT_OUT if verdicts else UNREADABLE, producer)

    def _settle_rows(
        self,
        batch: list[InboxItem],
        verdicts: dict[int, tuple[str, str]],
        failure: str,
        producer: str,
    ) -> int:
        """Write each row's verdict, or *failure* where it has none, in one save, and show them.

        Only a row still unsorted is written: one she sorted while the model read it keeps her
        verdict, and one that is gone is left gone."""
        changes: dict[str, dict[str, str]] = {}
        for n, item in enumerate(batch, 1):
            row = self._store.items.get(item.id)
            if row is None or not wants_sorting(row):
                continue
            verdict = verdicts.get(n)
            if verdict is None:
                changes[item.id] = {"classify_error": failure}
            else:
                changes[item.id] = {
                    "classification": verdict[0],
                    "confidence": verdict[1],
                    "classified_by": producer,
                    "classify_error": "",
                }
        for row in self._store.update_many(changes):
            _announce(row)
        return len(batch)


def _announce(item: InboxItem) -> None:
    """Show a sorted row on every open surface."""
    from personalclaw.inbox_providers.native_source import get_dashboard_state

    state = get_dashboard_state()
    if state is None:
        return
    try:
        state.broadcast_ws("inbox_item_updated", redact_item(item.to_dict()))
    except Exception:  # noqa: BLE001 — a frame that did not go out is caught up by a reload
        logger.debug("inbox sorting: could not announce %s", item.id, exc_info=True)


__all__ = [
    "BATCH_MAX",
    "InboxSorter",
    "USE_CASE",
    "parse_verdicts",
    "render_messages",
    "sorting_hold",
    "verdicts_problem",
    "wants_sorting",
]
