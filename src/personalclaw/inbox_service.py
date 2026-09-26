"""Inbox service — the runtime behind the dashboard Inbox page.

Holds the inbox entity (``state`` + ``store``) and provides the AI affordances the
dashboard calls on demand:

* :meth:`classify` — triage a stored item into needs_reply / fyi / noise.
* :meth:`draft_reply` — draft a reply to a stored item in the user's voice.
* :meth:`generate_digest` — summarize a channel's recent messages into a catch-up item.

All three run one-shot LLM jobs over the item's stored content through the bound
chat model (``one_shot_completion``). The message text is EXTERNAL, untrusted
content (a scraped channel/filesystem message can carry a prompt-injection), so it is
wrapped with :func:`fence_untrusted` before it ever reaches a prompt — the model
reads it as quoted data, not instructions. This is the one seam where third-party
message text enters an LLM prompt, mirroring how the web-tools app fences web_fetch
output at its tool boundary.

The service is channel-independent: draft/classify/digest operate on the stored items
(populated by the native push source + any configured poll providers), so they work
even with no external provider connected.

It also owns the inbox **background loop** (:meth:`start` / :meth:`stop`): each tick
polls the wired message-source provider for new messages (ingesting them with
alert evaluation + live WS broadcast). Polling no-ops when no provider is wired.

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
import logging
import time
import uuid
from typing import TYPE_CHECKING

from personalclaw import shutdown_event
from personalclaw import trace_recorder as _trace
from personalclaw.guardrails.audit import caller_scope
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

logger = logging.getLogger(__name__)


def _dashboard_state():
    """The process-wide dashboard state (set at startup), or None headless."""
    from personalclaw.inbox_providers.native_source import get_dashboard_state

    return get_dashboard_state()


# Bound how much external text we feed a single prompt (a busy thread can be huge).
_MAX_MESSAGE_CHARS = 6000
_MAX_THREAD_TURNS = 12
_MAX_DIGEST_MESSAGES = 60


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
        provider: "MessageSourceProvider | None" = None,
        user_name: str = "",
        style_rules: str = "",
    ) -> None:
        self.state = state or InboxState()
        self.inbox = store or InboxStore()
        self._provider = provider
        self._user_name = user_name or "the user"
        self._style_rules = style_rules or ""
        self._last_poll_at = 0.0
        self._last_poll_ok = True
        self._last_error = ""
        self._poll_count = 0
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        # The event loop that owns the store, captured in start(). The remediation engine
        # drives maintenance from a worker thread (PR2-11) and must bounce onto this loop.
        self._owner_loop: asyncio.AbstractEventLoop | None = None

    # ── health (mirrors what the dashboard status handler expects) ──
    def health(self) -> dict:
        stale = bool(self._last_poll_at) and (time.time() - self._last_poll_at) > 900
        return {
            "running": self._task is not None and not self._task.done(),
            "last_poll_at": self._last_poll_at,
            "last_poll_ok": self._last_poll_ok,
            "last_error": self._last_error,
            "poll_count": self._poll_count,
            "stale": stale,
        }

    # ── background loop (poll + maintenance) ──
    def start(self) -> None:
        """Start the background loop. Idempotent."""
        if self._task is None or self._task.done():
            # Capture the owning loop BEFORE create_task so run_maintenance_threadsafe can
            # bounce the engine's maintenance call back onto it (the store is mutated only here).
            self._owner_loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._loop())
            logger.info(
                "Inbox loop started (provider=%s)",
                self._provider.source_name if self._provider else "none",
            )

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
            # job now (PR2-11), so this loop only polls. One cadence, one owner per pass.
            if self._provider is not None:
                try:
                    await self._poll_once()
                    self._last_poll_ok = True
                    self._last_error = ""
                except Exception as exc:
                    self._last_poll_ok = False
                    self._last_error = str(exc) or exc.__class__.__name__
                    logger.warning("Inbox poll failed", exc_info=True)
                self._last_poll_at = time.time()
                self._poll_count += 1

    async def _poll_once(self) -> None:
        """Fetch new messages from the wired provider and ingest them."""
        assert self._provider is not None
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load().inbox
        messages, checkpoints = await self._provider.poll(
            list(cfg.watched_channels), dict(self.state.last_read_ts), cfg.user_id
        )
        if checkpoints:
            self.state.last_read_ts.update(checkpoints)
        ingested = self._ingest(messages, own_user_id=cfg.user_id, test_mode=cfg.test_mode)
        if ingested or checkpoints:
            self.state.save()

    def _ingest(
        self,
        messages: "list[IncomingMessage]",
        *,
        own_user_id: str = "",
        test_mode: bool = False,
    ) -> int:
        """Convert polled messages to stored items (dedup, mute/dismiss filters),
        evaluating alerts + broadcasting each new item live. Returns # ingested."""
        if not messages:
            return 0
        operator = operator_name()
        dash_state = _dashboard_state()
        can_reply = bool(self._provider is not None and self._provider.source_name != "filesystem")
        source_name = self._provider.source_name if self._provider else "native"
        count = 0
        for m in messages:
            item_id = f"{m.channel_id}_{m.timestamp}"
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
                # What the source says this IS (mention / email / plain message), validated
                # against the closed set — the inbox's kind filter is a live reader, so an
                # unvalidated value here would be a row no chip can reach.
                item_kind=_resolve_source_kind(m.kind, source_name),
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
                    },
                )
            except Exception:
                logger.debug("inbox event-trigger emit failed", exc_info=True)
            reason = evaluate_alert(item, operator)
            # Dev-only event-trace tap (Self-Verification §2.1): one event per NEWLY
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
        # FEEDBACK-SIGNAL (plan 58): the retire-candidate check rides this maintenance pass —
        # one-time "retire this rule?" proposal per producer per threshold crossing, notified
        # via the dashboard state. Its pending count feeds `maintenance_backlog` so the engine
        # schedules a pass whenever a proposal is due, preserving this cadence (PR2-11).
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
    # Both affordances declare their action type here (AUTONOMY-GUARDRAILS §5.2), so the
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
            # output_type=dict adds one targeted-retry attempt (§2.4). A parse miss
            # that survives the retry still safe-defaults to needs_reply/needs_review
            # (the error carries the retry text) — never a silent drop.
            # `caller_scope` so the attempt row names WHICH background pass spent this
            # (`G47`): triage, drafting and digests all resolve on the same axis.
            with caller_scope("inbox_triage"):
                raw = await one_shot_completion(prompt, use_case="background", output_type=dict)
        except OutputContractError as exc:
            raw = exc.raw
        except Exception:
            logger.warning("inbox classify failed for %s", item_id, exc_info=True)
            return None
        cls, conf = _parse_classification(raw)
        return self.inbox.update(item_id, classification=cls, confidence=conf)

    async def draft_reply(self, item_id: str) -> InboxItem | None:
        """Draft a reply to a stored item in the user's voice; persist + return the item.

        Returns None if the item is unknown or the model call fails. A model that
        judges no reply is warranted returns the SKIP sentinel → we store an empty
        draft and leave the item pending (the human decides)."""
        from personalclaw.guardrails.rungs import ensure_core_action_types

        ensure_core_action_types()
        item = self.inbox.items.get(item_id)
        if item is None:
            return None
        from personalclaw.llm_helpers import one_shot_completion
        from personalclaw.prompt_providers.runtime import render_use_case_prompt

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
        try:
            with caller_scope("inbox_triage"):
                raw = (await one_shot_completion(prompt, use_case="background") or "").strip()
        except Exception:
            logger.warning("inbox draft failed for %s", item_id, exc_info=True)
            return None
        draft = "" if raw.upper() == "SKIP" else raw
        # A produced draft implies the item wanted a reply — reflect that so the UI
        # sorts it sensibly, but never downgrade an escalate.
        updates: dict = {"draft": draft, "context_summary": "AI-drafted reply"}
        if draft and item.classification == Classification.NOISE:
            updates["classification"] = Classification.NEEDS_REPLY
        return self.inbox.update(item_id, **updates)

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
            with caller_scope("inbox_triage"):
                summary = (await one_shot_completion(prompt, use_case="background") or "").strip()
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
            classification=Classification.FYI,
            confidence=Confidence.HIGH,
            status=ItemStatus.PENDING,
            created_at=ts,
            context_summary=f"AI digest of {len(messages)} messages",
            source="digest",
            can_reply=False,
        )
        self.inbox.add(item)
        self.inbox.flush()
        return item

    async def _recent_messages(self, channel_id: str, hours: float) -> list[str]:
        """Attributed, oldest-first message lines for the window — from the provider's
        channel history if available, else from stored items for that channel."""
        cutoff = time.time() - hours * 3600
        lines: list[str] = []
        if self._provider is not None:
            try:
                raw = await self._provider.get_channel_history(channel_id, oldest=str(cutoff))
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
