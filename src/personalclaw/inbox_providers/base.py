from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from personalclaw.attachments import Attachment
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

    ``files`` are the files the message came with (a mail's attachments), each as the message
    gave it (:class:`~personalclaw.attachments.Attachment`). The row lists them beside ``text``
    and core keeps them (``attachments.keep``), so ``text`` is the message's words alone: a
    source never turns an attached file into text in it, and never fences it. Core fences the
    words wherever a model reads them, with where they came from.

    ``instruction`` is the owner's instruction for the message, when she gave the source one for
    messages like it (Mail Inbox's prompt for mail to one of its addresses), word for word as a
    setting of the source's app holds it. Never words of the message: those are ``text``. Core
    takes it as hers only when the app holds it in a setting its manifest declares an instruction
    (``x-meta.instruction``), or gives that setting it as its default
    (``apps.instruction_settings``); anything else handed over here is refused, and the message
    arrives without one. A fire on the message hands its action her instruction first, outside
    any fence, then the message fenced once (``triggers.fire_facts.hand_on``). It is not kept on
    the Inbox row, whose text is the message's.
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
    files: list[Attachment] = field(default_factory=list, kw_only=True)
    instruction: str = field(default="", kw_only=True)


class MessageSourceProvider(ABC):
    """A source the inbox polls: an installed inbox app's, or a built-in one.

    Every registered source is polled while :meth:`polling_enabled` says so
    (``inbox_providers.polled_sources``); ``display_name``, when a source has one, is what
    the inbox's sentences call it.
    """

    #: Whether this source reads the channels the owner lists in ``inbox.watched_channels`` (the
    #: ``watched_channels`` its :meth:`poll` is handed). Settings → Inbox shows that list, naming
    #: the sources that read it, only while a polled source says so: a list no source reads would
    #: be a control that changes nothing.
    watches_channels: bool = False

    @property
    @abstractmethod
    def source_name(self) -> str: ...

    def polling_enabled(self) -> bool:
        """Whether the inbox polls this source now.

        An installed app's source is registered while its app is enabled, and enabling it is
        the owner's say-so, so the default is yes. A source that ships inside core has no app
        the owner chose (a native app is locked on), so it reads its own switch here: the drop
        folder's is ``inbox.enabled``."""
        return True

    @abstractmethod
    async def poll(
        self, watched_channels: list[str], checkpoints: dict[str, str], user_id: str
    ) -> tuple[list[IncomingMessage], dict[str, str]]:
        """What arrived since ``checkpoints``, and the checkpoints to resume from.

        ``checkpoints`` is one dict every source shares, so a source namespaces its own keys
        and returns them. A poll that could not read the source RAISES: the inbox keeps the
        checkpoints it had, shows the exception's message as this source's health (so write
        it as a sentence the owner can act on), and tries again next tick. Returning an
        empty list instead would read as "nothing new" while nothing was read."""

    @abstractmethod
    async def send_reply(self, channel_id: str, text: str, thread_ts: str | None = None) -> bool:
        """Send ``text`` as a reply to one message this source polled.

        ``thread_ts`` is that message's ``IncomingMessage.id``, the source's own id for it,
        which the inbox keeps on its row (``reply_target``): a mail's ``Message-ID``, a chat
        message's ts. A source threads the reply under it as its platform does. Truthy when
        it was sent. A falsy result that is not a plain bool may say why it was
        not, as its ``str()`` (the convention channel transports' refusals follow): the
        inbox shows it to the owner who pressed Send."""

    @abstractmethod
    async def add_reaction(self, channel_id: str, ts: str, emoji: str) -> bool: ...

    @abstractmethod
    async def get_channel_history(
        self, channel_id: str, oldest: str, limit: int = 200
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def resolve_user_name(self, user_id: str) -> str: ...
