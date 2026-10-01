"""Inbox service — the runtime behind the dashboard Inbox page.

Holds the inbox entity (``state`` + ``store``) and provides the AI affordances the
dashboard calls on demand:

* :meth:`classify` — triage a stored item into needs_reply / fyi / noise.
* :meth:`draft_reply` — draft a reply to a stored item in the user's voice, standing on the
  notes the owner's own words name (:mod:`personalclaw.reply_grounding`).
* :meth:`generate_digest` — summarize a channel's recent messages into a catch-up item.

All three run one-shot LLM jobs over the item's stored content through the bound
chat model (``one_shot_completion``). The message text is EXTERNAL, untrusted
content (a scraped channel/filesystem message can carry a prompt-injection), so it is
wrapped with :func:`fence_untrusted` before it ever reaches a prompt — the model
reads it as quoted data, not instructions. This is the one seam where third-party
message text enters an LLM prompt, mirroring how the web-tools app fences web_fetch
output at its tool boundary.

The service is channel-independent: draft/classify/digest operate on the stored items
(populated by the native push source + every polled source), so they work even with no
external source connected.

It also owns the inbox **background loop** (:meth:`start` / :meth:`stop`): each tick polls
EVERY source :func:`personalclaw.inbox_providers.polled_sources` names — each installed inbox
app's, and the built-in drop folder while ``inbox.enabled`` is on — ingesting what each
returns with alert evaluation + live WS broadcast. One source failing does not stop the
others, and what it said is that source's health (:meth:`health`).

Periodic **maintenance** — retention cleanup honoring the entity settings
(``auto_cleanup_enabled`` / ``retention_days``), dismissed-set pruning, and the
feedback retire-candidate check — is no longer a second cadence inside this loop.
Since PR2-11 it is a registered remediation-engine job (``inbox.maintenance``),
deficit-driven off the LIVE store like every other absorbed maintenance pass, so
"old maintenance no longer runs independently" (§4.4 criterion #6) holds for the
inbox too. :meth:`run_maintenance` remains the implementation the engine drives —
bounced onto this loop via :meth:`run_maintenance_threadsafe` so the store is
mutated only on the thread that owns it, never from the engine's worker thread.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from personalclaw import attachments, shutdown_event
from personalclaw import trace_recorder as _trace
from personalclaw.guardrails.audit import caller_scope
from personalclaw.guardrails.local_queue import Attended
from personalclaw.identity import contributor_label, current_username, operator_name
from personalclaw.inbox import (
    SOURCE_DECLARABLE_KINDS,
    Classification,
    Confidence,
    InboxItem,
    InboxState,
    InboxStore,
    ItemKind,
    ItemStatus,
    evaluate_alert,
    notify_inbox_alert,
)
from personalclaw.security import fence_untrusted

if TYPE_CHECKING:
    from personalclaw.inbox_providers.base import IncomingMessage, MessageSourceProvider
    from personalclaw.reply_grounding import Grounding, Unread

logger = logging.getLogger(__name__)


def _dashboard_state():
    """The process-wide dashboard state (set at startup), or None headless."""
    from personalclaw.inbox_providers.native_source import get_dashboard_state

    return get_dashboard_state()


#: How long one source's poll may take before the inbox stops waiting for it. A source bounds its
#: own I/O (Mail Inbox's sockets time out); this is what keeps one that does not from holding
#: every other source's poll behind it.
SOURCE_POLL_TIMEOUT_SECS = 300.0

# Bound how much external text we feed a single prompt (a busy thread can be huge).
_MAX_MESSAGE_CHARS = 6000
_MAX_THREAD_TURNS = 12
_MAX_DIGEST_MESSAGES = 60

#: The most the owner may say about what one drafted reply should contain. Room for a paragraph and
#: a pasted abstract; the draft route refuses more, and the reply panel's field stops at it.
DRAFT_INSTRUCTIONS_MAX_CHARS = 2000

#: How a model answers that it will not draft without the owner's word: ``ASK:`` and its question.
DRAFT_ASK = "ASK:"

#: The longest question shown to her from a model that would not draft without her word.
_MAX_DRAFT_QUESTION_CHARS = 300


def _drafting_rules(user: str) -> str:
    """What every draft is written under, after whatever template is bound for drafting.

    A draft is sent as her, so the model may decide nothing for her: no promise, date or
    acceptance she did not give. Where the reply needs one, it asks her (:data:`DRAFT_ASK`)
    instead of writing around it. Measured before this: a draft with nothing to go on answered a
    request for an abstract with "I'll get you the final abstract by end of week"."""
    return (
        "Rules for this draft, whatever is above:\n"
        f"- Commit {user} to nothing they have not said here: no promise, date, deadline, "
        'acceptance or follow-up of your own ("I\'ll send it by Friday" is a promise). Thanks, '
        "an acknowledgement, or a question back to the sender commit to nothing.\n"
        f"- Where the reply needs something only {user} can give (content they have not given "
        f"you, a decision, a date), do not write around it: answer with {DRAFT_ASK} and one short "
        f"question to {user} saying what you need from them."
    )


@dataclass
class DraftOutcome:
    """What one Generate draft did: the item as it now stands, what the draft stood on, and the
    model's question for her when it would not write without her word.

    Nothing was written when a file she named could not be read (:attr:`unread`, and the model
    never ran) or when the model asked (:attr:`question`)."""

    item: InboxItem
    grounding: "Grounding"
    question: str = ""
    skipped: bool = False

    @property
    def unread(self) -> "list[Unread]":
        return self.grounding.unread

    def unread_sentence(self) -> str:
        return self.grounding.unread_sentence()

    @property
    def wrote(self) -> bool:
        return not self.unread and not self.question

    def report(self) -> dict:
        """What the reply panel is told about this draft."""
        from personalclaw.reply_grounding import word_count

        return {
            "read": [n.report() for n in self.grounding.named],
            "related": [n.report() for n in self.grounding.related],
            "summary": self.item.context_summary if self.wrote else "",
            "word_limit": self.grounding.word_limit,
            "words": word_count(self.item.draft) if self.wrote else 0,
            "question": self.question,
            "skipped": self.skipped,
        }


def _draft_answer(raw: str) -> tuple[str, str]:
    """A model's answer to a draft prompt as ``(kind, text)``: ``("ask", question)``,
    ``("skip", "")`` for the SKIP sentinel, or ``("draft", reply)``."""
    text = (raw or "").strip()
    if text.upper().startswith(DRAFT_ASK):
        question = " ".join(text[len(DRAFT_ASK) :].split())[:_MAX_DRAFT_QUESTION_CHARS]
        return ("ask", question) if question else ("skip", "")
    if text.upper() == "SKIP":
        return ("skip", "")
    return ("draft", text)


def polled_item_id(source_name: str, message: "IncomingMessage") -> str:
    """The id of the row a polled message becomes: ``{source}_{key}_{timestamp}``.

    ``key`` is the message's own id at its source (a mail's Message-ID, a chat message's ts),
    hashed with the source and the channel it was read from; a message with no id of its own is
    keyed by its content (channel, sender, time, thread and text). The id used to be
    ``{channel}_{timestamp}``: every row of a mailbox shares one channel, the receiving address,
    and a mail's Date counts whole seconds, so a second mail sent in the same second as another
    got the first one's id and was dropped as a duplicate of it.

    Hashed rather than spelled out because the id is a path segment of every item route, and a
    Message-ID may hold a ``/``. The trailing ``_{timestamp}`` is ``InboxItem.ts``, which sorting
    and retention read.
    """
    own = str(message.id or "")
    basis = (
        [source_name, message.channel_id, own]
        if own
        else [
            source_name,
            message.channel_id,
            message.sender_id,
            repr(float(message.timestamp or 0.0)),
            str(message.thread_id or ""),
            message.text,
        ]
    )
    key = hashlib.sha256("\0".join(basis).encode("utf-8")).hexdigest()[:16]
    return f"{source_name}_{key}_{message.timestamp}"


def digest_item_id(channel_id: str, ts: float) -> str:
    """The id for a generated channel digest: ``{channel}_digest_{uuid8}_{int(ts)}``.

    A module-level function rather than an inline f-string so a test can assert the REAL id
    instead of a copy of it. It was inline, and the first version of the test for this mirrored
    the f-string in a helper — which meant reverting the production line left every behavioural
    assertion green.

    The uuid8 is what makes two digests distinct: the id used to be `{channel}_digest_{int(ts)}`,
    second-granular, and `InboxStore.items` is keyed by id — so two digests generated in the
    same second silently REPLACED one another, discarding a paid model call's output with no
    error.

    It sits BEFORE the timestamp on purpose. `InboxItem.ts` is `id.rsplit("_", 1)[-1]`, so a
    uuid appended at the end would make every digest's `ts` a hex string — sorting and
    rendering as garbage rather than failing loudly. That contract is also what the
    Inbox-Unification plan means by "keeps the `ts` rsplit contract".
    """
    return f"{channel_id}_digest_{uuid.uuid4().hex[:8]}_{int(ts)}"


def _resolve_source_kind(declared: str, source_name: str) -> str:
    """The ``item_kind`` to persist for a source-declared *declared* kind.

    Unset (the default, and every source written before the field existed) is a plain
    ``message`` — the inbox began as a channel-message surface and that is what those rows
    are. A value inside :data:`SOURCE_DECLARABLE_KINDS` is taken as declared.

    Anything else is REFUSED, and the item is filed as ``message`` with a warning naming
    the source and the value. The two rejected postures, and why:

    * *Trust it.* A source could then invent a kind the dashboard has no chip, icon or
      label for — the row would be unfilterable and unreachable behind every kind chip.
      It could also claim one of core's non-channel attention kinds and render a row with
      no refs, no deep-link and no reply.
    * *Drop the message.* One typo (``"mail"`` for ``"email"``) would silently stop a
      user's mail from arriving at all, and in the filesystem source's case would wedge the
      poll batch on the offending file forever. Losing a message is a strictly worse
      outcome than mis-filing one.

    So the row is delivered (never lost) and confined to a kind the UI can render, and the
    mistake is loud on the one side that can fix it — the provider author's logs. This is
    NOT a silent fallback: an unknown kind always logs.
    """
    if not declared:
        return ItemKind.MESSAGE.value
    if declared in SOURCE_DECLARABLE_KINDS:
        return declared
    logger.warning(
        "inbox source %r declared item kind %r, which is not one of %s — filing as %r",
        source_name,
        declared,
        sorted(SOURCE_DECLARABLE_KINDS),
        ItemKind.MESSAGE.value,
    )
    return ItemKind.MESSAGE.value


def fence_message_for_prompt(item: InboxItem, owner: str | None = None) -> str:
    """Render an item's external text (body + thread context) as ONE fenced block.

    Everything the sender controlled is inside a single ``<untrusted_content>`` fence
    so the model can't be steered by injected instructions. Thread context is
    included oldest-first with attributions the model can quote.

    **TSE2-3 — a foreign-attributed item is also LABELLED.** In a shared inbox the fence
    alone is not enough: it says "this span is data", but every item is data, so a
    teammate's item and the owner's own fence identically and the model cannot tell which
    one carries the owner's intent. So an item attributed to somebody else additionally
    carries ``identity.contributor_label()`` — the one ``" (from <handle>)"`` form shared
    with semantic memory, not a second convention — and the fence declares the attributed
    owner in its ``source_id`` provenance attribute. The owner's own items are unlabelled,
    for the reason ``contributor_label`` documents: labelling every row hides the one case
    the label exists for.

    *owner* defaults to the live attribution username, so the two service call sites need
    no argument; tests pass it explicitly.
    """
    if owner is None:
        owner = current_username()
    label = contributor_label(item.owner_username, owner)
    parts: list[str] = []
    for turn in (item.thread_context or [])[-_MAX_THREAD_TURNS:]:
        who = str(turn.get("sender") or turn.get("sender_name") or "someone")
        txt = str(turn.get("text") or "")
        if txt.strip():
            parts.append(f"{who}: {txt}")
    body = (item.message or "")[:_MAX_MESSAGE_CHARS]
    parts.append(f"{item.sender_name or 'sender'}{label}: {body}")
    if item.attachments:
        parts.append(f"Attached:\n{attachments.listing(item.attachments)}")
    return fence_untrusted(
        "\n".join(parts),
        source="inbox-message",
        # Only for a FOREIGN item: the attributed owner is the provenance fact that changes
        # how the span should be read. Omitted for the owner's own items so the attribute's
        # presence is itself the signal, and so a solo install's prompts are byte-identical
        # to what they were before attribution existed.
        source_id=(item.owner_username or "") if label else "",
    )


class InboxService:
    """Owns inbox state/store + the on-demand AI triage affordances."""

    def __init__(
        self,
        *,
        state: InboxState | None = None,
        store: InboxStore | None = None,
        sources: "Callable[[], list[MessageSourceProvider]] | None" = None,
        user_name: str = "",
        style_rules: str = "",
    ) -> None:
        """``sources`` names what each tick polls; it is re-read every tick, so an inbox app
        enabled or disabled since the last one is picked up. None polls nothing (tests,
        headless); the gateway passes :func:`personalclaw.inbox_providers.polled_sources`."""
        self.state = state or InboxState()
        self.inbox = store or InboxStore()
        self._sources: "Callable[[], list[MessageSourceProvider]]" = sources or (lambda: [])
        self._user_name = user_name or "the user"
        self._style_rules = style_rules or ""
        self._last_poll_at = 0.0
        #: source name → what its last poll did (:meth:`_note_poll`), the per-source half of
        #: :meth:`health`. A source that stops being polled leaves it at the next tick.
        self._source_health: dict[str, dict[str, Any]] = {}
        self._poll_count = 0
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        # The event loop that owns the store, captured in start(). The remediation engine
        # drives maintenance from a worker thread and must bounce onto this loop.
        self._owner_loop: asyncio.AbstractEventLoop | None = None

    # ── health (mirrors what the dashboard status handler expects) ──
    def health(self) -> dict:
        """The loop's health, and each polled source's: what its last poll did.

        ``last_poll_ok`` is false while any source's last poll failed, and ``last_error`` names
        each failing source with the sentence its poll raised, so the one-line status a reader
        takes from this is true about every source, not only the first."""
        stale = bool(self._last_poll_at) and (time.time() - self._last_poll_at) > 900
        rows = [dict(row) for _, row in sorted(self._source_health.items())]
        failing = [row for row in rows if row["error"]]
        return {
            "running": self._task is not None and not self._task.done(),
            "last_poll_at": self._last_poll_at,
            "last_poll_ok": not failing,
            "last_error": "; ".join(f"{row['label']}: {row['error']}" for row in failing),
            "poll_count": self._poll_count,
            "stale": stale,
            "sources": rows,
        }

    def _note_poll(self, source: "MessageSourceProvider", error: str) -> None:
        """Record what *source*'s poll just did: ``error`` is its sentence, or "" when it read."""
        from personalclaw.inbox_providers import source_label

        name = str(source.source_name)
        now = time.time()
        previous = self._source_health.get(name) or {"name": name, "last_ok_at": 0.0}
        if error and previous.get("error") != error:
            # Once per new sentence, not once per tick: a refused login fails every minute.
            logger.warning("inbox source %s: poll failed: %s", name, error)
        row = {**previous, "label": source_label(source), "ok": not error, "error": error}
        row["last_poll_at"] = now
        if not error:
            row["last_ok_at"] = now
        self._source_health[name] = row

    # ── background loop (poll + maintenance) ──
    def start(self) -> None:
        """Start the background loop. Idempotent."""
        if self._task is None or self._task.done():
            # Capture the owning loop BEFORE create_task so run_maintenance_threadsafe can
            # bounce the engine's maintenance call back onto it (the store is mutated only here).
            self._owner_loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._loop())
            names = [str(source.source_name) for source in self._sources()]
            logger.info("Inbox loop started (polling: %s)", ", ".join(names) or "nothing yet")

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _poll_interval(self) -> float:
        try:
            from personalclaw.config.loader import AppConfig

            return float(AppConfig.load().inbox.poll_interval_seconds)
        except Exception:
            return 60.0

    async def _loop(self) -> None:
        # Sleep FIRST: the initial tick lands one interval after startup, so a
        # bare service construction (tests, headless) never touches the store.
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=self._poll_interval())
                return  # shutdown signaled
            except asyncio.TimeoutError:
                pass  # normal wake-up
            # Maintenance is NOT run here — it is the remediation engine's `inbox.maintenance`
            # job now, so this loop only polls. One cadence, one owner per pass.
            try:
                await self._poll_once()
            except Exception:  # noqa: BLE001 - the loop outlives any one pass
                logger.warning("Inbox poll pass failed", exc_info=True)

    async def _poll_once(self) -> None:
        """Poll every source :attr:`_sources` names, one after another, and ingest each.

        A source that raises (or runs past :data:`SOURCE_POLL_TIMEOUT_SECS`) keeps its
        checkpoints and contributes nothing this tick; what it raised is its health. The rest
        are polled as if it were not there."""
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load().inbox
        sources = self._sources()
        polled = {str(source.source_name) for source in sources}
        for gone in set(self._source_health) - polled:
            del self._source_health[gone]  # its app was disabled: no row for a source not polled
        changed = False
        for source in sources:
            try:
                messages, checkpoints = await asyncio.wait_for(
                    source.poll(
                        list(cfg.watched_channels), dict(self.state.last_read_ts), cfg.user_id
                    ),
                    timeout=SOURCE_POLL_TIMEOUT_SECS,
                )
            except asyncio.TimeoutError:
                self._note_poll(
                    source,
                    f"its last poll did not finish within {SOURCE_POLL_TIMEOUT_SECS:g} seconds",
                )
                continue
            except Exception as exc:  # noqa: BLE001 - a source's failure is its health
                self._note_poll(source, str(exc).strip() or type(exc).__name__)
                logger.debug("inbox source %s: poll raised", source.source_name, exc_info=True)
                continue
            self._note_poll(source, "")
            if checkpoints:
                self.state.last_read_ts.update(checkpoints)
                changed = True
            if self._ingest(
                messages, source=source, own_user_id=cfg.user_id, test_mode=cfg.test_mode
            ):
                changed = True
        if changed:
            self.state.save()
        self._last_poll_at = time.time()
        self._poll_count += 1

    def _ingest(
        self,
        messages: "list[IncomingMessage]",
        *,
        source: "MessageSourceProvider",
        own_user_id: str = "",
        test_mode: bool = False,
    ) -> int:
        """Convert *source*'s polled messages to stored items (dedup, mute/dismiss filters),
        evaluating alerts + broadcasting each new item live. Returns # ingested.

        Each row records the source it came from, which is where a reply to it is sent
        (``api_inbox_send``); the drop folder's rows cannot be answered."""
        if not messages:
            return 0
        operator = operator_name()
        dash_state = _dashboard_state()
        source_name = str(source.source_name)
        can_reply = source_name != "filesystem"
        count = 0
        for m in messages:
            item_id = polled_item_id(source_name, m)
            if item_id in self.inbox.items or item_id in self.state.dismissed:
                continue
            if m.thread_id and m.thread_id in self.state.muted_threads:
                continue
            if own_user_id and m.sender_id == own_user_id and not test_mode:
                continue
            if m.channel_name:
                self.state.channel_names[m.channel_id] = m.channel_name
            item = InboxItem(
                id=item_id,
                channel=m.channel_id,
                channel_name=m.channel_name or m.channel_id,
                thread_ts=m.thread_id,
                message=m.text,
                sender_id=m.sender_id,
                sender_name=m.sender_name or m.sender_id,
                thread_context=list(m.thread_context or []),
                created_at=m.timestamp or time.time(),
                source=source_name,
                can_reply=can_reply,
                # The source's own id for the message: a reply to this row is addressed with it
                # (`send_reply`'s third argument). Mail Inbox finds the exact mail by it; the
                # thread id alone named none for a first mail, and a reply went to whoever
                # wrote to the same address last.
                reply_target=str(m.id or "") if can_reply else "",
                # What the source says this IS (mention / email / plain message), validated
                # against the closed set — the inbox's kind filter is a live reader, so an
                # unvalidated value here would be a row no chip can reach.
                item_kind=_resolve_source_kind(m.kind, source_name),
                # The files it came with, kept under the row and listed on it: the Inbox offers
                # each for download, and an agent reading the message is told of them.
                attachments=attachments.keep(item_id, m.files) if m.files else [],
            )
            self.inbox.add(item)
            count += 1
            # Inbox→event bridge (EIAT-1 C2). Emitted here, past every acceptance filter
            # (dedup/mute/self), so an accepted item raises EXACTLY ONE event and a filtered
            # message raises none. The value is the RAW message text — it is fenced once, when a
            # trigger fires on it (`event_triggers.fire_payload`), never here, so it is never
            # double-fenced. `meta` carries the fields the inbox patterns match: `sender`/`address`.
            try:
                from personalclaw.event_triggers import SOURCE_INBOX, emit_event

                emit_event(
                    source=SOURCE_INBOX,
                    event_type="message_received",
                    key=item_id,
                    value=m.text,
                    now=time.time(),
                    meta={
                        "sender": m.sender_id,
                        "sender_name": m.sender_name or m.sender_id,
                        "address": m.channel_id,
                        "source_name": item.source,
                        # How many files came with it: the fire reads them off the row (`key`).
                        "attachments": str(len(item.attachments)),
                    },
                )
            except Exception:
                logger.debug("inbox event-trigger emit failed", exc_info=True)
            reason = evaluate_alert(item, operator)
            # Dev-only event-trace tap: one event per NEWLY
            # ingested item (past the dedup/mute/self filters), keyed by channel, carrying
            # the alert decision — so replay can assert inbox dedup + alert-once. No-op
            # unless PERSONALCLAW_TRACE_DIR is set.
            if _trace.is_recording():
                _trace.record(
                    "inbox",
                    m.channel_id,
                    "item_ingested",
                    {"item_id": item_id, "alerted": bool(reason), "reason": reason or ""},
                )
            if reason:
                notify_inbox_alert(dash_state, item, reason)
            if dash_state is not None:
                try:
                    from personalclaw.dashboard.handlers_inbox import _redact_item

                    dash_state.broadcast_ws("inbox_new_item", _redact_item(item.to_dict()))
                except Exception:
                    logger.debug("inbox ingest broadcast failed", exc_info=True)
        if count:
            self.inbox.flush()
        return count

    def run_maintenance(self) -> int:
        """Retention cleanup honoring the inbox entity settings + state pruning.
        Returns the number of items deleted. Safe to call any time.

        Driven by the remediation engine's ``inbox.maintenance`` job (PR2-11); callers on a
        worker thread must go through :meth:`run_maintenance_threadsafe` so this body runs on
        the loop that owns the store."""
        from personalclaw.providers.entity_routes import load_inbox_settings

        settings = load_inbox_settings()
        removed = 0
        if settings.get("auto_cleanup_enabled"):
            try:
                days = max(1, int(settings.get("retention_days") or 90))
            except (TypeError, ValueError):
                days = 90
            removed = self.inbox.cleanup_by_retention(days)
        if self.state.prune_dismissed():
            self.state.save()
        # The retire-candidate check rides this maintenance pass —
        # one-time "retire this rule?" proposal per producer per threshold crossing, notified
        # via the dashboard state. Its pending count feeds `maintenance_backlog` so the engine
        # schedules a pass whenever a proposal is due, preserving this cadence.
        try:
            from personalclaw.feedback import check_retire_candidates

            check_retire_candidates(state=_dashboard_state())
        except Exception:  # noqa: BLE001 — maintenance must never fail on feedback
            logger.debug("feedback retire check failed", exc_info=True)
        return removed

    def maintenance_backlog(self) -> int:
        """The magnitude a maintenance pass would act on right now — the remediation engine's
        ``inbox_maintenance_backlog`` deficit count (PR2-11). Read-only, and measured off THIS
        live store rather than a fresh one (which would fork the in-memory state the service
        holds — see :func:`personalclaw.inbox.live_store`).

        Sums the three things :meth:`run_maintenance` acts on: retention-expired items (only
        when auto-cleanup is enabled, matching the pass), prunable dismissed IDs, and pending
        feedback retire candidates. Each sub-count is best-effort — a measurement runs on the
        engine's worker thread while the loop may mutate, and must never raise into the pass."""
        total = 0
        try:
            from personalclaw.providers.entity_routes import load_inbox_settings

            settings = load_inbox_settings()
            if settings.get("auto_cleanup_enabled"):
                try:
                    days = max(1, int(settings.get("retention_days") or 90))
                except (TypeError, ValueError):
                    days = 90
                total += self.inbox.count_expired(days)
        except Exception:  # noqa: BLE001 — a measurement must never raise
            logger.debug("inbox maintenance: retention count failed", exc_info=True)
        try:
            total += self.state.count_prunable_dismissed()
        except Exception:  # noqa: BLE001
            logger.debug("inbox maintenance: dismissed count failed", exc_info=True)
        try:
            from personalclaw.feedback import pending_retire_candidate_count

            total += pending_retire_candidate_count()
        except Exception:  # noqa: BLE001
            logger.debug("inbox maintenance: retire-candidate count failed", exc_info=True)
        return total

    async def _run_maintenance_on_loop(self) -> int:
        return self.run_maintenance()

    def run_maintenance_threadsafe(self, *, timeout: float = 60.0) -> int:
        """Run :meth:`run_maintenance` on the loop that owns the store, callable from any thread.

        The remediation engine drives maintenance from a thread-pool executor, but the store is
        mutated only on the asyncio loop running the poll/ingest pass (`emit`/`resolve`/dismiss
        all go through it single-threaded). So a worker thread must NOT mutate it directly —
        this bounces the call onto the owning loop and blocks for the result. Falls back to an
        inline run when no loop is running (headless / tests) or when already on that loop."""
        loop = self._owner_loop
        if loop is None or not loop.is_running():
            return self.run_maintenance()
        try:
            if asyncio.get_running_loop() is loop:
                return self.run_maintenance()  # already on the owning loop — no bounce, no deadlock
        except RuntimeError:
            pass  # not on any loop (a worker thread) — bounce below
        future = asyncio.run_coroutine_threadsafe(self._run_maintenance_on_loop(), loop)
        return future.result(timeout=timeout)

    # ── AI affordances ──
    #
    # Both affordances declare their action type here, so the
    # governed inventory is complete in a process that never dispatched a provider action.
    # `inbox.reply_draft` is declared at the BOTTOM rung with a `one_tap` ceiling and
    # `leaves_machine=True`: an AI-drafted reply is written and shown, never sent, and no
    # accumulated track record can propose sending one by itself. `inbox.classify` labels
    # the user's own row, so it declares `autonomous` — which is what it already does.
    async def classify(self, item_id: str) -> InboxItem | None:
        """Triage a stored item into needs_reply/fyi/noise + confidence, persist, return it."""
        from personalclaw.guardrails.rungs import ensure_core_action_types

        ensure_core_action_types()
        item = self.inbox.items.get(item_id)
        if item is None:
            return None
        from personalclaw.llm_helpers import one_shot_completion
        from personalclaw.prompt_providers.runtime import render_use_case_prompt

        prompt = (
            render_use_case_prompt(
                "inbox_classify",
                {
                    "channel": item.channel_name or item.channel,
                    "sender": item.sender_name or "unknown",
                    "message": fence_message_for_prompt(item),
                },
            )
            or ""
        )
        from personalclaw.guardrails.failure import OutputContractError

        try:
            # output_type=dict adds one targeted-retry attempt. A parse miss
            # that survives the retry still safe-defaults to needs_reply/needs_review
            # (the error carries the retry text) — never a silent drop.
            # `caller_scope` so the attempt row names WHICH background pass spent this
            # (`G47`): triage, drafting and digests all resolve on the same axis.
            # An answer naming no known classification is that model failing the call: the
            # chain's next model is asked inside it.
            with caller_scope("inbox_triage"):
                raw = await one_shot_completion(
                    prompt,
                    use_case="background",
                    output_type=dict,
                    validate=_classification_problem,
                )
        except OutputContractError as exc:
            raw = exc.raw
        except Exception:
            logger.warning("inbox classify failed for %s", item_id, exc_info=True)
            return None
        cls, conf = _parse_classification(raw)
        return self.inbox.update(item_id, classification=cls, confidence=conf)

    async def draft_reply(self, item_id: str, *, instructions: str = "") -> DraftOutcome | None:
        """Draft a reply to a stored item in the user's voice; persist it and say what it did.

        ``instructions`` is what the owner said this reply should contain. It is the owner's own
        instruction, so it goes to the model as one, after the prompt and outside the fence that
        marks the sender's text as data. It rides after the rendered prompt rather than in the
        template, so it is followed whatever template the owner has bound for drafting, and so do
        the rules every draft is written under (:func:`_drafting_rules`).

        The draft is one model call with no tools, so what it may draw on is read first
        (:func:`personalclaw.reply_grounding.ground`): the files her instruction names or, naming
        none, the knowledge library's best matches for her words. Only her words choose; the
        message itself reads nothing. A file she names that cannot be read stops the draft before
        the model runs (:attr:`DraftOutcome.unread`), since a draft written around it would guess
        or promise on her behalf. A word limit she gives is asked for, and an over-long draft is
        asked for once more within it.

        Returns None if the item is unknown or the model call fails. A model that judges no
        reply is warranted returns the SKIP sentinel → we store an empty draft and leave the
        item pending (the human decides). One that needs her word first asks (``ASK:``), and
        nothing is written."""
        from personalclaw.guardrails.rungs import ensure_core_action_types

        ensure_core_action_types()
        item = self.inbox.items.get(item_id)
        if item is None:
            return None
        from personalclaw.llm_helpers import one_shot_completion
        from personalclaw.prompt_providers.runtime import render_use_case_prompt
        from personalclaw.reply_grounding import ground, word_count

        said = instructions.strip()
        grounding = await asyncio.to_thread(ground, said)
        if grounding.unread:
            return DraftOutcome(item, grounding)

        style = (
            f"Match this voice/style when replying:\n{self._style_rules}"
            if self._style_rules
            else ""
        )
        prompt = (
            render_use_case_prompt(
                "inbox_draft",
                {
                    "user_name": self._user_name,
                    "channel": item.channel_name or item.channel,
                    "sender": item.sender_name or "unknown",
                    "message": fence_message_for_prompt(item),
                    "style": style,
                },
            )
            or ""
        )
        user = self._user_name
        parts = [prompt, _drafting_rules(user)]
        notes = grounding.prompt_block(user)
        if notes:
            parts.append(notes)
        if said:
            parts.append(
                f"{user} said what this reply should say. Follow it: it is their own instruction, "
                "not part of the quoted message, and it means a reply is wanted, so do not answer "
                f"SKIP.\n\n{said}"
            )
        limit = grounding.word_limit
        if limit:
            parts.append(f"Keep the whole reply within {limit} words: {user}'s limit.")
        prompt = "\n\n".join(parts)
        try:
            # Asked for from the Inbox page, which waits on the draft.
            with caller_scope("inbox_triage"):
                kind, text = _draft_answer(
                    await one_shot_completion(
                        prompt, use_case="background", attended=Attended("Drafting the reply")
                    )
                    or ""
                )
        except Exception:
            logger.warning("inbox draft failed for %s", item_id, exc_info=True)
            return None
        if kind == "ask":
            return DraftOutcome(item, grounding, question=text)
        if text and limit and word_count(text) > limit:
            text = await self._within_limit(prompt, text, limit, item_id)
        # A produced draft implies the item wanted a reply — reflect that so the UI
        # sorts it sensibly, but never downgrade an escalate.
        updates: dict = {"draft": text, "context_summary": grounding.summary() if text else ""}
        if text and item.classification == Classification.NOISE:
            updates["classification"] = Classification.NEEDS_REPLY.value
        stored = self.inbox.update(item_id, **updates)
        if stored is None:
            return None
        return DraftOutcome(stored, grounding, skipped=kind == "skip")

    async def _within_limit(self, prompt: str, draft: str, limit: int, item_id: str) -> str:
        """*draft*, asked for once more within the owner's word *limit*. The shorter answer is
        kept when the second is not a draft, or is still over: a draft is never cut mid-sentence,
        and the panel shows its count against the limit."""
        from personalclaw.llm_helpers import one_shot_completion
        from personalclaw.reply_grounding import word_count

        quoted = fence_untrusted(draft, source="reply-draft")
        again = (
            f"{prompt}\n\nYour reply, quoted below, has {word_count(draft)} words, over "
            f"{self._user_name}'s limit of {limit}. Write it again within {limit} words, keeping "
            f"everything they asked for, and answer with only the new reply.\n\n{quoted}"
        )
        try:
            # The Inbox page still waits on this draft.
            with caller_scope("inbox_triage"):
                kind, text = _draft_answer(
                    await one_shot_completion(
                        again, use_case="background", attended=Attended("Shortening the reply")
                    )
                    or ""
                )
        except Exception:
            logger.warning("inbox draft: the shorter draft failed for %s", item_id, exc_info=True)
            return draft
        if kind != "draft" or not text or word_count(text) >= word_count(draft):
            return draft
        return text

    async def generate_digest(self, channel_id: str, hours: float = 4.0) -> InboxItem | None:
        """Summarize a channel's recent messages into a new digest inbox item.

        Pulls the window from the configured provider's channel history when one is
        wired; otherwise falls back to the stored items for that channel. Returns the
        created digest item, or None when there's nothing in the window."""
        messages = await self._recent_messages(channel_id, hours)
        if not messages:
            return None
        from personalclaw.llm_helpers import one_shot_completion
        from personalclaw.prompt_providers.runtime import render_use_case_prompt

        channel_name = self.state.channel_names.get(channel_id, channel_id)
        fenced = fence_untrusted(
            "\n".join(messages[-_MAX_DIGEST_MESSAGES:]), source="inbox-channel"
        )
        prompt = (
            render_use_case_prompt(
                "inbox_digest",
                {
                    "channel": channel_name,
                    "hours": f"{hours:g}",
                    "user_name": self._user_name,
                    "messages": fenced,
                },
            )
            or ""
        )
        try:
            # Asked for from the Inbox page, which waits on the digest.
            with caller_scope("inbox_triage"):
                summary = (
                    await one_shot_completion(
                        prompt, use_case="background", attended=Attended("Writing the digest")
                    )
                    or ""
                ).strip()
        except Exception:
            logger.warning("inbox digest failed for %s", channel_id, exc_info=True)
            return None
        if not summary:
            return None
        ts = time.time()
        item = InboxItem(
            id=digest_item_id(channel_id, ts),
            channel=channel_id,
            channel_name=channel_name,
            thread_ts=None,
            message=summary,
            sender_id="",
            sender_name=f"Digest · last {hours:g}h",
            classification=Classification.FYI.value,
            confidence=Confidence.HIGH.value,
            status=ItemStatus.PENDING.value,
            created_at=ts,
            context_summary=f"AI digest of {len(messages)} messages",
            source="digest",
            can_reply=False,
        )
        self.inbox.add(item)
        self.inbox.flush()
        return item

    async def _recent_messages(self, channel_id: str, hours: float) -> list[str]:
        """Attributed, oldest-first message lines for the window — from the channel's own
        source's history if it keeps one, else from stored items for that channel."""
        cutoff = time.time() - hours * 3600
        lines: list[str] = []
        owners = {it.source for it in self.inbox.items.values() if it.channel == channel_id}
        source = next((s for s in self._sources() if str(s.source_name) in owners), None)
        if source is not None:
            try:
                raw = await source.get_channel_history(channel_id, oldest=str(cutoff))
                for m in raw:
                    who = str(m.get("sender_name") or m.get("user") or "someone")
                    txt = str(m.get("text") or "")
                    if txt.strip():
                        lines.append(f"{who}: {txt}")
            except Exception:
                logger.debug("channel history fetch failed for %s", channel_id, exc_info=True)
        if not lines:
            stored = [
                it
                for it in self.inbox.items.values()
                if it.channel == channel_id and it.created_at >= cutoff and it.source != "digest"
            ]
            for it in sorted(stored, key=lambda i: i.created_at):
                if (it.message or "").strip():
                    lines.append(f"{it.sender_name or 'sender'}: {it.message}")
        return lines


def _live_inbox_service() -> "InboxService | None":
    """The RUNNING inbox service, reached through the process-wide dashboard state — the same
    seam :func:`personalclaw.inbox.live_store` uses, and for the same reason: the service holds
    its store in memory, so the remediation engine must drive THIS instance, never a fresh one.

    ``None`` in a headless/CLI process (no gateway, no dashboard state) — where there is no loop
    running maintenance anyway. isinstance-checked so a test's ``MagicMock`` state is not mistaken
    for a live service."""
    state = _dashboard_state()
    svc = getattr(state, "_inbox_svc", None) if state is not None else None
    return svc if isinstance(svc, InboxService) else None


def inbox_maintenance_backlog() -> int:
    """The live inbox service's pending-maintenance magnitude, or 0 when none is running — the
    remediation deficit ``inbox_maintenance_backlog`` (PR2-11). Always safe to call: a bare
    process has no service, so there is nothing to prune and the count is 0 (the deficit is still
    emitted, at 0, so the schedulability rail can see it)."""
    svc = _live_inbox_service()
    return svc.maintenance_backlog() if svc is not None else 0


def run_live_inbox_maintenance() -> str:
    """Run the live inbox service's maintenance pass — the ``inbox.maintenance`` job entry point
    (PR2-11). Bounces onto the loop that owns the store so nothing is mutated from the engine's
    worker thread. A no-op string when no service is running (the deficit would then be 0 and the
    job unscheduled, but the run-now path can still reach here)."""
    svc = _live_inbox_service()
    if svc is None:
        return "no inbox service running"
    removed = svc.run_maintenance_threadsafe()
    return f"inbox maintenance: {removed} item(s) removed"


def _classification_problem(raw: str) -> str:
    """What makes a classify answer unusable, ``""`` when it names a known classification."""
    from personalclaw.llm_helpers import parse_llm_json

    data = parse_llm_json(raw)
    label = str(data.get("classification", "")).lower() if isinstance(data, dict) else ""
    return "" if label in {c.value for c in Classification} else "no known 'classification'"


def _parse_classification(raw: str) -> tuple[str, str]:
    """Parse the classify model output → (classification, confidence), defaulting
    safely to needs_reply/needs_review when the JSON is malformed."""
    valid_cls = {c.value for c in Classification}
    valid_conf = {c.value for c in Confidence}
    try:
        from personalclaw.llm_helpers import parse_llm_json

        data = parse_llm_json(raw) or {}
    except Exception:
        data = {}
    cls = str(data.get("classification", "")).lower()
    conf = str(data.get("confidence", "")).lower()
    return (
        cls if cls in valid_cls else Classification.NEEDS_REPLY.value,
        conf if conf in valid_conf else Confidence.NEEDS_REVIEW.value,
    )
