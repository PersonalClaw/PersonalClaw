from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from personalclaw.inbox import ItemKind


@dataclass
class IncomingMessage:
    """One message a source polled, on its way to becoming an inbox row.

    ``kind`` is the source's own statement about WHAT this message is, and it is the only
    way ``mention``/``email`` rows can ever exist: the inbox's kind filter is a live
    reader, but before this field no source could write anything but the default, so the
    Mentions and Email filters could not match a single item by construction.

    Only a source knows its kind, and only from its own payload — a mail source knows it
    polled a mailbox; a channel source knows the vendor payload listed the operator among
    the message's at-mention ids. Core deliberately does NOT infer either one (scanning
    text for the user's name is an alerting heuristic, not an identity), so a source that
    does not set this stays a plain ``message``.

    Valid values are the channel-shaped kinds — ``message`` / ``mention`` / ``email``
    (``personalclaw.inbox.SOURCE_DECLARABLE_KINDS``). Anything else is refused at
    ingestion: the row still arrives, filed as ``message``, and the service logs a warning
    naming the source and the value it tried to claim.
    """

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
    kind: str = ItemKind.MESSAGE.value


class MessageSourceProvider(ABC):
    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    async def poll(
        self, watched_channels: list[str], checkpoints: dict[str, str], user_id: str
    ) -> tuple[list[IncomingMessage], dict[str, str]]: ...

    @abstractmethod
    async def send_reply(
        self, channel_id: str, text: str, thread_ts: str | None = None
    ) -> bool: ...

    @abstractmethod
    async def add_reaction(self, channel_id: str, ts: str, emoji: str) -> bool: ...

    @abstractmethod
    async def get_channel_history(
        self, channel_id: str, oldest: str, limit: int = 200
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def resolve_user_name(self, user_id: str) -> str: ...
