import asyncio
import json
import logging
import time
from typing import Any

from personalclaw.config import loader as config_loader
from personalclaw.inbox_providers.base import IncomingMessage, MessageSourceProvider


def config_dir():
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)


class FilesystemSourceProvider(MessageSourceProvider):
    """Polls JSON files from disk for incoming messages.

    Each ``*.json`` dropped in ``<home>/inbox/incoming/`` carries a ``messages`` list whose
    entries map 1:1 onto :class:`~personalclaw.inbox_providers.base.IncomingMessage`,
    including its ``kind`` — this is core's own seam for a local producer (a mail fetcher, a
    channel bridge) to state that a message is an ``email`` or a ``mention`` rather than a
    plain ``message``. The value is passed through as declared and validated once, at
    ingestion, against the closed set; this provider never guesses a kind from the text.
    """

    #: What the inbox's sentences call this source.
    display_name = "Drop folder"

    @property
    def source_name(self) -> str:
        return "filesystem"

    def polling_enabled(self) -> bool:
        """Only while ``inbox.enabled`` ("Poll the drop folder") is on, which it is not by
        default: anything that can write to this machine can drop a file here, and the native
        app that ships this source is locked on, so enabling it is no one's say-so."""
        return bool(config_loader.AppConfig.load().inbox.enabled)

    async def poll(
        self, watched_channels: list[str], checkpoints: dict[str, str], user_id: str
    ) -> tuple[list[IncomingMessage], dict[str, str]]:
        incoming_dir = config_dir() / "inbox" / "incoming"
        if not incoming_dir.exists():
            return [], checkpoints

        processed_dir = incoming_dir / "processed"

        def _read_files() -> list[IncomingMessage]:
            messages: list[IncomingMessage] = []
            processed_dir.mkdir(parents=True, exist_ok=True)
            for f in sorted(incoming_dir.glob("*.json")):
                try:
                    data = json.loads(f.read_text())
                    for raw in data.get("messages", []):
                        msg = IncomingMessage(
                            # The message's own id when the file names one. None is not
                            # made up from the file's name: a file dropped again under a used
                            # name named its messages as the first one's were, and the inbox
                            # keys a message with no id by its content instead.
                            id=str(raw.get("id") or ""),
                            channel_id=raw.get("channel_id", "filesystem"),
                            channel_name=raw.get("channel_name", "local"),
                            thread_id=raw.get("thread_id"),
                            text=raw.get("text", ""),
                            sender_id=raw.get("sender_id", ""),
                            sender_name=raw.get("sender_name", ""),
                            timestamp=raw.get("timestamp", time.time()),
                            thread_context=raw.get("thread_context", []),
                            is_dm=raw.get("is_dm", False),
                            kind=str(raw.get("kind") or ""),
                        )
                        messages.append(msg)
                    f.rename(processed_dir / f.name)
                except (json.JSONDecodeError, OSError):
                    logger.warning("Failed to read message file %s", f, exc_info=True)
            return messages

        messages = await asyncio.to_thread(_read_files)
        return messages, checkpoints

    async def send_reply(self, channel_id: str, text: str, thread_ts: str | None = None) -> bool:
        return True

    async def add_reaction(self, channel_id: str, ts: str, emoji: str) -> bool:
        return True

    async def get_channel_history(
        self, channel_id: str, oldest: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        return []

    async def resolve_user_name(self, user_id: str) -> str:
        return user_id


Provider = FilesystemSourceProvider


def create_provider(config=None):
    """Extension factory for filesystem inbox source."""
    return FilesystemSourceProvider()
