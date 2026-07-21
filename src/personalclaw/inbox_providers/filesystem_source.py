import asyncio
import json
import logging
import time
from typing import Any

from personalclaw.config.loader import config_dir
from personalclaw.inbox_providers.base import IncomingMessage, MessageSourceProvider

logger = logging.getLogger(__name__)


class FilesystemSourceProvider(MessageSourceProvider):
    """Polls JSON files from disk for incoming messages."""

    @property
    def source_name(self) -> str:
        return "filesystem"

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
                            id=raw.get("id", f"{f.stem}_{len(messages)}"),
                            channel_id=raw.get("channel_id", "filesystem"),
                            channel_name=raw.get("channel_name", "local"),
                            thread_id=raw.get("thread_id"),
                            text=raw.get("text", ""),
                            sender_id=raw.get("sender_id", ""),
                            sender_name=raw.get("sender_name", ""),
                            timestamp=raw.get("timestamp", time.time()),
                            thread_context=raw.get("thread_context", []),
                            is_dm=raw.get("is_dm", False),
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
