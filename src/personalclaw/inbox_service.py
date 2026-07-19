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
alert evaluation + live WS broadcast) and runs periodic maintenance — retention
cleanup honoring the entity settings (``auto_cleanup_enabled`` / ``retention_days``)
plus dismissed-set pruning. Polling no-ops when no provider is wired; maintenance
always runs so native/push items age out too.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from personalclaw import shutdown_event
from personalclaw.inbox import (
    Classification,
    Confidence,
    InboxItem,
    InboxState,
    InboxStore,
    ItemStatus,
    evaluate_alert,
    notify_inbox_alert,
)
from personalclaw.security import fence_untrusted

if TYPE_CHECKING:
    from personalclaw.inbox_providers.base import IncomingMessage, MessageSourceProvider

logger = logging.getLogger(__name__)

# Retention cleanup + state pruning cadence within the background loop.
_MAINTENANCE_EVERY_SECS = 6 * 3600


def _dashboard_state():
    """The process-wide dashboard state (set at startup), or None headless."""
    from personalclaw.inbox_providers.native_source import get_dashboard_state

    return get_dashboard_state()

# Bound how much external text we feed a single prompt (a busy thread can be huge).
_MAX_MESSAGE_CHARS = 6000
_MAX_THREAD_TURNS = 12
_MAX_DIGEST_MESSAGES = 60


def _fence_message(item: InboxItem) -> str:
    """Render an item's external text (body + thread context) as ONE fenced block.

    Everything the sender controlled is inside a single ``<untrusted_content>`` fence
    so the model can't be steered by injected instructions. Thread context is
    included oldest-first with attributions the model can quote."""
    parts: list[str] = []
    for turn in (item.thread_context or [])[-_MAX_THREAD_TURNS:]:
        who = str(turn.get("sender") or turn.get("sender_name") or "someone")
        txt = str(turn.get("text") or "")
        if txt.strip():
            parts.append(f"{who}: {txt}")
    body = (item.message or "")[:_MAX_MESSAGE_CHARS]
    parts.append(f"{item.sender_name or 'sender'}: {body}")
    return fence_untrusted("\n".join(parts), source="inbox-message")


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
        self._last_maintenance_at = 0.0
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

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
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=self._poll_interval()
                )
                return  # shutdown signaled
            except asyncio.TimeoutError:
                pass  # normal wake-up
            now = time.time()
            if now - self._last_maintenance_at >= _MAINTENANCE_EVERY_SECS:
                try:
                    self.run_maintenance()
                except Exception:
                    logger.warning("Inbox maintenance failed", exc_info=True)
                self._last_maintenance_at = now
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
        ingested = self._ingest(
            messages, own_user_id=cfg.user_id, test_mode=cfg.test_mode
        )
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
        from personalclaw.providers.entity_routes import load_inbox_settings

        settings = load_inbox_settings()
        operator = self._operator_name()
        dash_state = _dashboard_state()
        can_reply = bool(
            self._provider is not None
            and self._provider.source_name != "filesystem"
        )
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
                source=self._provider.source_name if self._provider else "native",
                can_reply=can_reply,
            )
            self.inbox.add(item)
            count += 1
            reason = evaluate_alert(item, settings, operator)
            if reason:
                notify_inbox_alert(dash_state, item, reason)
            if dash_state is not None:
                try:
                    from personalclaw.dashboard.handlers_inbox import _redact_item

                    dash_state.broadcast_ws(
                        "inbox_new_item", _redact_item(item.to_dict())
                    )
                except Exception:
                    logger.debug("inbox ingest broadcast failed", exc_info=True)
        if count:
            self.inbox.flush()
        return count

    def run_maintenance(self) -> int:
        """Retention cleanup honoring the inbox entity settings + state pruning.
        Returns the number of items deleted. Safe to call any time."""
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
        return removed

    @staticmethod
    def _operator_name() -> str:
        try:
            from personalclaw.config.loader import AppConfig
            return AppConfig.load().dashboard.user_name or ""
        except Exception:
            return ""

    # ── AI affordances ──
    async def classify(self, item_id: str) -> InboxItem | None:
        """Triage a stored item into needs_reply/fyi/noise + confidence, persist, return it."""
        item = self.inbox.items.get(item_id)
        if item is None:
            return None
        from personalclaw.llm_helpers import one_shot_completion
        from personalclaw.prompt_providers.runtime import render_use_case_prompt

        prompt = render_use_case_prompt("inbox_classify", {
            "channel": item.channel_name or item.channel,
            "sender": item.sender_name or "unknown",
            "message": _fence_message(item),
        }) or ""
        try:
            raw = await one_shot_completion(prompt, use_case="background")
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
        item = self.inbox.items.get(item_id)
        if item is None:
            return None
        from personalclaw.llm_helpers import one_shot_completion
        from personalclaw.prompt_providers.runtime import render_use_case_prompt

        style = f"Match this voice/style when replying:\n{self._style_rules}" if self._style_rules else ""
        prompt = render_use_case_prompt("inbox_draft", {
            "user_name": self._user_name,
            "channel": item.channel_name or item.channel,
            "sender": item.sender_name or "unknown",
            "message": _fence_message(item),
            "style": style,
        }) or ""
        try:
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
        fenced = fence_untrusted("\n".join(messages[-_MAX_DIGEST_MESSAGES:]), source="inbox-channel")
        prompt = render_use_case_prompt("inbox_digest", {
            "channel": channel_name,
            "hours": f"{hours:g}",
            "user_name": self._user_name,
            "messages": fenced,
        }) or ""
        try:
            summary = (await one_shot_completion(prompt, use_case="background") or "").strip()
        except Exception:
            logger.warning("inbox digest failed for %s", channel_id, exc_info=True)
            return None
        if not summary:
            return None
        ts = time.time()
        item = InboxItem(
            id=f"{channel_id}_digest_{int(ts)}",
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
                it for it in self.inbox.items.values()
                if it.channel == channel_id and it.created_at >= cutoff and it.source != "digest"
            ]
            for it in sorted(stored, key=lambda i: i.created_at):
                if (it.message or "").strip():
                    lines.append(f"{it.sender_name or 'sender'}: {it.message}")
        return lines


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
