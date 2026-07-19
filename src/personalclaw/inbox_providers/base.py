from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IncomingMessage:
    id: str
    channel_id: str
    channel_name: str
    thread_id: str | None = None
    text: str = ""
    sender_id: str = ""
    sender_name: str = ""
    timestamp: float = 0.0
    thread_context: list[dict[str, str]] = field(default_factory=list)
    is_dm: bool = False


class MessageSourceProvider(ABC):
    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    async def poll(
        self, watched_channels: list[str], checkpoints: dict[str, str], user_id: str
    ) -> tuple[list[IncomingMessage], dict[str, str]]: ...

    @abstractmethod
    async def send_reply(self, channel_id: str, text: str, thread_ts: str | None = None) -> bool: ...

    @abstractmethod
    async def add_reaction(self, channel_id: str, ts: str, emoji: str) -> bool: ...

    @abstractmethod
    async def get_channel_history(self, channel_id: str, oldest: str, limit: int = 200) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def resolve_user_name(self, user_id: str) -> str: ...
