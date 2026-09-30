"""ChannelDelivery — the outbound handle the gateway uses to deliver results.

The gateway delivers cron/heartbeat/subagent results and interactive approval
prompts to whatever channel a session came from. That delivery is
channel-specific (Slack renders mrkdwn + Block Kit ack buttons + threads), so the
rendering lives in the channel's own bundle, not core. Each channel transport registers its
handle when its receiver starts (``start_inbound``), core drops it when that receiver stops
(``channel_transports.reconcile_inbound``), and core calls these high-level methods with PLAIN
text + structured intent; the implementation renders channel-specifically.

When no channel is configured nothing is registered and the gateway delivers to
the dashboard only. This is the outbound half of the core↔channel seam
(:class:`~personalclaw.gateway_services.GatewayServices` is the inbound half).

**One handle PER PROVIDER — see the registry at the bottom of this module.** A single shared
handle was the shape until #959: with Discord, Slack and Telegram all connected, each
transport wrote the same slot, so the last registration won (apps load alphabetically, so
always Telegram) and every outbound reply went to that one provider carrying another
provider's channel id.

**Every text a channel is handed is masked, here, once** (:class:`MaskedDelivery`). A channel
sends what it is handed to a service outside this machine, so the registry holds each handle
behind the mask and every reader of it gets text masked with ``security.redact_for_display``: a
run's result, a notification's title, a rich message, an automation's name, an approval.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import logging
import unicodedata
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol, runtime_checkable

from personalclaw.security import redact_values_for_display

logger = logging.getLogger(__name__)

#: How a progress item on a channel's stream stands (:meth:`ChannelDelivery.append_stream_task`):
#: running, then how its call ended — ran and succeeded, ran and failed, or did not run because its
#: approval was refused, went unanswered, or its turn stopped first.
TASK_STATUSES: tuple[str, ...] = (
    "in_progress",
    "complete",
    "failed",
    "rejected",
    "expired",
    "cancelled",
)

#: How an approval ends (:meth:`ChannelDelivery.request_approval`): the owner's answer either way,
#: nobody answering inside the owner's window, or the work that asked stopping first. A channel's
#: prompt is told which, wherever it ended.
APPROVAL_ENDINGS: tuple[str, ...] = ("approved", "rejected", "expired", "cancelled")


@runtime_checkable
class ChannelDelivery(Protocol):
    """Outbound delivery a channel provides to the gateway. All text is PLAIN
    markdown, masked by core before it is handed over (:class:`MaskedDelivery`) — the
    implementation renders it to the channel's format."""

    async def open_dm(self, user_id: str) -> str:
        """Open (or resolve) a DM channel with a user; return its channel id."""
        ...

    async def deliver_text(
        self,
        channel: str,
        text: str,
        thread_ts: str = "",
        *,
        unfurl_links: "bool | None" = None,
        unfurl_media: "bool | None" = None,
        reply_broadcast: "bool | None" = None,
    ) -> str:
        """Post plain-markdown text to a channel/thread; return the message ts. The
        optional link-preview / broadcast hints are generic messaging concepts a
        channel applies if it supports them (ignored otherwise)."""
        ...

    async def deliver_rich(
        self,
        channel: str,
        payload: "object",
        fallback_text: str,
        *,
        thread_ts: str = "",
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        reply_broadcast: bool = False,
    ) -> str:
        """Deliver a caller-supplied structured/rich payload (e.g. Block Kit) with a
        plain-text fallback; return the message ts. The payload is opaque to core —
        callers pass channel-shaped structures through; a channel that can't render
        rich content falls back to ``fallback_text``."""
        ...

    async def deliver_cron_result(
        self, channel: str, job_name: str, job_id: str, text: str, thread_ts: str = ""
    ) -> str:
        """Deliver a cron job result with the channel's ack affordance; return the
        parent message ts (for threading follow-ups)."""
        ...

    async def deliver_notification(
        self, channel: str, title: str, text: str, thread_ts: str = ""
    ) -> str:
        """Deliver a titled notification (heartbeat/subagent) to a channel/thread."""
        ...

    async def deliver_chat_mirror(self, channel: str, text: str, thread_ts: str = "") -> None:
        """Mirror a dashboard chat reply to a linked channel thread, rendering any
        trailing ``[OPTIONS: …]`` block as the channel's interactive affordance."""
        ...

    async def deliver_subagent_reply(
        self, channel: str, text: str, thread_ts: str = "", elapsed_secs: float = 0.0
    ) -> None:
        """Deliver a subagent's synthesized reply to a channel/thread, with the
        channel's timing affordance (a footer showing how long the run took)."""
        ...

    # ── Owner / channel resolution (provider-agnostic identity lookups) ──
    async def resolve_user_name(self, user_id: str) -> str:
        """Human-readable display name for a channel user id (best-effort; returns
        the id or empty on failure). Used by inbox sender-name resolution."""
        ...

    async def resolve_user_profile(self, user_id: str) -> "dict":
        """Full profile dict for a channel user (name/real_name/title/etc.); ``{}`` on
        failure. Shape is provider-defined — callers read known keys defensively."""
        ...

    async def channel_info(self, channel_id: str) -> "dict":
        """Metadata for a channel (e.g. ``{"name": ..., "is_im": ...}``); ``{}`` on
        failure. Provider-agnostic shape — callers read known keys defensively."""
        ...

    def list_reply_channels(self) -> "list[dict]":
        """Channels this delivery can post replies into, as ``{"id", "name"}`` dicts
        (the channel app's own config decides — tracked/active channels). Used by the
        dashboard's channel picker for link/handoff. May be empty."""
        ...

    def is_tracked_channel(self, channel_id: str) -> bool:
        """Whether *channel_id* is in this channel's outbound allowlist (the app's
        own tracked-channel config). Core consults this for targeted sends."""
        ...

    def build_thread_link(self, channel: str, ts: str) -> str:
        """Deep link to a message/thread on this channel provider (e.g. a
        jump-to-source URL for notifications). Returns "" when the provider has
        no linkable surface. Core never constructs vendor URLs itself — the
        provider owns its own link format."""
        ...

    # ── Attachment + streaming primitives (the surface core used to reach via the
    # raw client). All channel-specific rendering stays in the implementation. ──
    async def upload_attachment(
        self,
        channel: str,
        file_path: str,
        *,
        filename: str = "",
        thread_ts: str = "",
        title: str = "",
        initial_comment: str = "",
    ) -> str:
        """Upload a file to a channel/thread; return the delivered message ts (or "")."""
        ...

    async def start_stream(self, channel: str, thread_ts: str = "", initial_text: str = "") -> str:
        """Begin a live-updating stream message (for tool/progress animation); return
        its ts, or "" if the channel has no streaming affordance."""
        ...

    async def append_stream_task(
        self,
        channel: str,
        stream_ts: str,
        task_id: str,
        title: str,
        status: str,
    ) -> None:
        """Append/update a progress item on an in-flight stream started by
        start_stream. Channels without task-animation may no-op.

        ``status`` is how the item stands, one of :data:`TASK_STATUSES`: ``in_progress`` while
        the call runs, then how it ended. ``complete`` is a call that ran and succeeded and
        ``failed`` one that ran and failed. The other three are a call that did not run because
        its approval did not approve it, named as the approval ended: ``rejected`` (refused, by
        you or by a rule), ``expired`` (nobody answered in time) and ``cancelled`` (the turn
        stopped first). A channel shows each as what it is: a line that says done for a call
        that never ran tells the owner something untrue."""
        ...

    async def stop_stream(self, channel: str, stream_ts: str) -> None:
        """Finalize a stream started by start_stream."""
        ...

    async def request_approval(
        self,
        event: "object",
        *,
        source: str,
        parent_session_key: str = "",
        sessions: "object | None" = None,
        on_prompted: "Callable[[object], None] | None" = None,
    ) -> "bool | None":
        """Prompt the owner to approve a tool call on this channel.

        Returns ``True`` (approved) / ``False`` (not approved), or ``None`` if the
        channel can't prompt (no owner/channel) so the gateway falls back to the
        dashboard. Implementations own the channel-specific approval UI + the wait
        for the owner's response, and should coordinate with the dashboard via the
        ``on_prompted`` hook (invoked with the pending record) when provided by the
        caller. ``sessions`` is the live SessionManager for cross-surface reconcile.

        **How it ends.** The pending record carries a ``future``. The owner's press on this
        channel resolves it with ``"approved"`` or ``"rejected"``. However else the approval ends,
        core resolves it with how it ended (:data:`APPROVAL_ENDINGS`): ``"approved"``
        or ``"rejected"`` when it was answered somewhere else (the dashboard, the phone),
        ``"expired"`` when nobody answered inside the owner's window, ``"cancelled"`` when the
        work that asked stopped first. The wait keeps no clock of its own: the window is core's,
        up to a week, and core ends the wait however the approval ends, so a prompt that gave up
        on its own timer would say an approval had ended while it still waited. Once the future
        resolves, the prompt says how it ended and takes its buttons off, and a press on it
        after that is answered with that outcome rather than taken for an answer.

        **What the prompt shows: the approval brief.** ``event.tool_meta`` carries the
        core-composed brief under
        :data:`~personalclaw.approval_brief.APPROVAL_BRIEF_META_KEY`, which a channel reads
        with ``personalclaw.sdk.channel.approval_brief_for(event)`` (that also composes one
        for an approval the channel's own turn raised)::

            {"tool": str,              # the tool, masked
             "input": str,             # its arguments, masked, as the dashboard's card
                                       #   shows them ("" when it takes none)
             "purpose": str,           # why the runner says it is calling it, masked
             "risk": str,              # EFFECTIVE per-invocation risk (not the
                                       #   DECLARED event.risk_level)
             "summary": str,           # "Can: writes files · Risk: Caution", or ""
             "blastRadius": {"writes": bool, "network": bool,
                             "shell": bool, "readOnly": bool},   # optional
             "blastRadiusLine": str}                             # optional

        A prompt shows the tool, the arguments, the purpose and the summary line, which is
        what the dashboard's approval card shows, and splits like a reply when that is too
        long for one message, the buttons on the last part. Every string is already masked
        (:func:`~personalclaw.security.redact_field`), so a channel masks nothing itself.
        Two rules for a renderer that reads the facets itself:

        * ``blastRadius``/``blastRadiusLine`` are ABSENT when nothing could be
          established — show no blast-radius line at all, rather than "nothing
          established", which reads as "nothing happens";
        * every facet is a POSITIVE claim, so render only the ``True`` ones. Never
          enumerate all four with on/off states: a ``False`` means "not established",
          and painting it as "no network" turns absence of evidence into an all-clear.

        The rendering stays in the channel's own bundle."""
        ...


# ── the mask: what a channel is handed ───────────────────────────────────────────────────
#
# A channel app sends what it is handed to a service outside this machine: Slack, Telegram,
# Discord, a mail server. So nothing core hands one may carry a key, and the masking is core's,
# in one place, rather than a copy in every app that each of them can forget. It used to be split
# between the two, each covering some paths: the apps sent a notification's title, a rich
# message's fallback or an automation's name as they were handed it, and core handed over a
# subagent's reply and an approval's title and input as they were, so a text was safe only where
# one of the two remembered.

#: The parameters of each sending method that carry text a channel shows: masked before the
#: method is called. Everything else a method takes (a channel id, a thread, a file's path, a
#: button's id) passes as it is.
_SENT_TEXT: dict[str, frozenset[str]] = {
    "deliver_text": frozenset({"text"}),
    "deliver_rich": frozenset({"payload", "fallback_text"}),
    "deliver_cron_result": frozenset({"job_name", "text"}),
    "deliver_notification": frozenset({"title", "text"}),
    "deliver_chat_mirror": frozenset({"text"}),
    "deliver_subagent_reply": frozenset({"text"}),
    "upload_attachment": frozenset({"title", "initial_comment"}),
    "start_stream": frozenset({"initial_text"}),
    "append_stream_task": frozenset({"title"}),
}

#: The fields of an approval request a channel shows the owner: what the call is, why it is
#: made and what it would run. ``request_id`` and the options stay, so the answer finds its way.
_APPROVAL_TEXT = frozenset(
    {"title", "text", "tool_purpose", "tool_input", "tool_input_obj", "tool_meta"}
)


def _positional(method: str) -> tuple[str, ...]:
    """The names of *method*'s positional parameters, in order, from the protocol."""
    params = inspect.signature(getattr(ChannelDelivery, method)).parameters.values()
    return tuple(p.name for p in params if p.name != "self" and p.kind == p.POSITIONAL_OR_KEYWORD)


def _masked_event(event: Any) -> Any:
    """An approval request with what the owner is shown masked (:data:`_APPROVAL_TEXT`).

    A copy when anything was masked, so the caller's own request keeps what it holds; the request
    itself when nothing was. The gateway raises an ``AgentEvent`` (a dataclass) and the dashboard's
    ask on a channel a namespace, so both are handled: a dataclass through ``replace``, anything
    else as a shallow copy with the masked fields set on it.
    """
    record = dataclasses.is_dataclass(event) and not isinstance(event, type)
    if record:
        names = {f.name for f in dataclasses.fields(event) if f.init} & _APPROVAL_TEXT
    else:
        names = {name for name in _APPROVAL_TEXT if hasattr(event, name)}
    shown = {name: redact_values_for_display(getattr(event, name)) for name in names}
    shown = {name: value for name, value in shown.items() if value != getattr(event, name)}
    if not shown:
        return event
    if record:
        return replace(event, **shown)
    masked = copy.copy(event)
    for name, value in shown.items():
        setattr(masked, name, value)
    return masked


class _Masking:
    """A sending method of the wrapped handle, called with its text masked first.

    Resolved per access, so a channel that has no such method answers ``AttributeError`` through
    the mask as it did without it, and ``getattr(delivery, name, None)`` still tells.
    """

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name
        self.texts = _SENT_TEXT[name]
        self.positions = _positional(name)

    def __get__(self, obj: Any, owner: type | None = None) -> Any:
        if obj is None:
            return self
        send = getattr(obj.inner, self.name)
        texts, positions = self.texts, self.positions

        async def masked(*args: Any, **kwargs: Any) -> Any:
            args = tuple(
                redact_values_for_display(a) if i < len(positions) and positions[i] in texts else a
                for i, a in enumerate(args)
            )
            kwargs = {
                k: redact_values_for_display(v) if k in texts else v for k, v in kwargs.items()
            }
            return await send(*args, **kwargs)

        return masked


class _MaskingApproval:
    """``request_approval`` with the request's text masked, when the channel can prompt at all."""

    def __get__(self, obj: Any, owner: type | None = None) -> Any:
        if obj is None:
            return self
        ask = getattr(obj.inner, "request_approval")

        async def masked(event: Any, *args: Any, **kwargs: Any) -> Any:
            return await ask(_masked_event(event), *args, **kwargs)

        return masked


class MaskedDelivery:
    """A channel's outbound handle as core holds it: every text it is handed masked first.

    The registry stores this, never the app's own handle, so every path to a channel masks the
    same way: :func:`delivery_for`, :func:`owner_reachable`, :func:`approval_delivery`,
    :func:`reach_owner` and :func:`deliver_to_owner`. Text is masked with
    ``security.redact_values_for_display`` (``redact_for_display`` over a string, and over every
    string of a rich payload); a channel id, a thread, a path or a button's id passes as it is.
    What the channel adds to the protocol, and every read, reaches the handle unchanged.
    """

    deliver_text = _Masking()
    deliver_rich = _Masking()
    deliver_cron_result = _Masking()
    deliver_notification = _Masking()
    deliver_chat_mirror = _Masking()
    deliver_subagent_reply = _Masking()
    upload_attachment = _Masking()
    start_stream = _Masking()
    append_stream_task = _Masking()
    request_approval = _MaskingApproval()

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "inner", inner)

    def __setattr__(self, name: str, value: Any) -> None:
        # The handle has nothing of its own to set: an attribute set on it is set on the channel's
        # handle, so a sending method assigned here is still called through the mask rather than
        # standing in for it.
        setattr(self.inner, name, value)

    def __getattr__(self, name: str) -> Any:
        # Only reached for what the class does not define: the protocol's reads and whatever a
        # channel adds of its own. `inner` itself lands here only on an instance being copied,
        # before `__init__` set it; answering from it would recurse.
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def __repr__(self) -> str:
        return f"MaskedDelivery({self.inner!r})"


# ── the registry: one handle per provider ────────────────────────────────────────────────
#
# Process-level, like `inbox_providers.native_source`'s dashboard-state hook and for the same
# reason: the WRITERS are channel transports reaching core through `GatewayServices`, while the
# READERS are both the gateway (owner notifications, cron results, subagent replies) and the
# dashboard (the chat mirror, the channel-link picker). Two objects held two separate slots for
# one fact before this — `GatewayOrchestrator._channel_delivery` and
# `DashboardState.channel_delivery` — and every shipped transport wrote BOTH, which is how one
# overwrite could take out delivery on two unrelated paths at once.
#
# A dict rather than a list: the routing key is the provider, because a channel id means nothing
# without one. `deliver_text("C123", …)` is answerable only by the provider that issued `C123`.

_REGISTRY: dict[str, MaskedDelivery] = {}


def provider_of(delivery: Any) -> str:
    """The provider name for a delivery that did not declare one.

    The protocol has 18 methods and no provider member, so core cannot ask a handle what it is.
    This derives a STABLE, DISTINCT key from the implementation's own module — `discord_runtime`
    → `discord` — which is all the registry needs: two different apps must not collide. It is a
    namespacing fallback, never a semantic guess.

    A transport SHOULD pass `provider=` explicitly (it already passes exactly that string to
    :meth:`GatewayServices.deliver_channel_inbound` on the way in, at the same lifecycle point),
    and then this is not consulted at all. The fallback exists so a core upgrade cannot break an
    app that has not been updated yet: three un-updated apps still land in three distinct keys.
    """
    module = getattr(type(delivery), "__module__", "") or ""
    root = module.split(".")[0]
    for suffix in ("_runtime", "_channel", "_delivery"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    return root or type(delivery).__name__.lower()


def register(delivery: "ChannelDelivery | None", provider: str = "") -> str:
    """Register (or with ``None``, clear) a provider's outbound handle. Returns the key used.

    ``register(None)`` with no provider clears EVERY handle — the "shutting down, nothing is
    reachable" case. ``register(None, provider="slack")`` clears just that one.
    """
    if delivery is None:
        if provider:
            _REGISTRY.pop(provider, None)
            return provider
        _REGISTRY.clear()
        return ""
    inner = delivery.inner if isinstance(delivery, MaskedDelivery) else delivery
    key = provider or provider_of(inner)
    previous = _REGISTRY.get(key)
    _REGISTRY[key] = MaskedDelivery(inner)
    if previous is not None and previous.inner is not inner:
        # Same provider re-registering (a reconnect) is normal and quiet at debug. What must
        # never happen silently again is two DIFFERENT providers sharing a key, which is why the
        # log names the key: if a derivation ever collides, this line is the evidence.
        logger.debug("channel delivery re-registered for %s", key)
    return key


def delivery_for(provider: str) -> "ChannelDelivery | None":
    """The handle for one provider, or None when that channel is not connected.

    **The only correct resolver for a reply to an incoming message.** A reply carries the origin
    channel's id, so it is deliverable by exactly one provider; returning a different one is not
    a degraded delivery but a misdirected one. Callers must treat None as "do not send" — never
    as "send via whatever is available".
    """
    return _REGISTRY.get(provider) if provider else None


def owner_reachable() -> "ChannelDelivery | None":
    """The first connected channel, or None — "is any channel connected".

    Deliberately a different question from :func:`delivery_for`, and not the way to reach the
    owner either: a message FOR the owner goes through :func:`reach_owner`, which tries every
    connected channel until one gets through, because the first one may know no owner or be
    unable to reach the one it knows. Sorted so the pick is deterministic rather than
    dict-insertion-ordered, which would make the same home behave differently across restarts
    depending on app load order — the property that made #959 hard to see.
    """
    for key in sorted(_REGISTRY):
        return _REGISTRY[key]
    return None


def approval_channel() -> str:
    """The channel the owner chose in "Send approvals to" (``agent.approval_channel``), or ``""``.

    ``""`` is the default order: the connected channels by name, the first that knows the owner
    asking. Read per approval, so a choice saved in Settings applies to the next one."""
    from personalclaw.config.loader import AppConfig

    return AppConfig.load().agent.approval_channel


def approval_providers(origin: str = "") -> list[str]:
    """The channels an approval may ask on, in the order they are tried.

    ``origin`` is the channel the chat asking started on (``DashboardState.channel_provider_for``):
    it comes FIRST, because the person asking is there. "Send approvals to" governs what has no
    channel origin (a chat in PersonalClaw, an unattended run, a trigger), and what is tried after
    an origin that cannot ask: the chosen channel alone when the owner chose one, and none while
    it is not connected — a channel the owner did not choose never stands in for it, and the
    approval waits in PersonalClaw, where every approval is listed. Otherwise every connected
    channel in name order, as :func:`reach_owner` tries them — which is where every approval went
    before the owner could choose, so Discord asked whenever it was paired.
    """
    chosen = approval_channel()
    if chosen:
        order = [chosen] if chosen in _REGISTRY else []
    else:
        order = sorted(_REGISTRY)
    if origin and origin in _REGISTRY:
        return [origin, *(key for key in order if key != origin)]
    return order


def approval_delivery(origin: str = "") -> "tuple[str, ChannelDelivery] | None":
    """The channel that asks the owner an approval, as ``(provider, delivery)``: the first of
    :func:`approval_providers` that knows the owner (``owner_id_for``) and has an Approve/Deny
    prompt, or None when none does. The provider names who answers there (you, on that channel).

    A channel with no owner id cannot ask anyone, so it is passed over rather than asked and left
    to answer "cannot prompt" — which ended a subagent's approval at the dashboard while the next
    channel in the order could have asked."""
    from personalclaw.config.credentials import owner_id_for

    for key in approval_providers(origin):
        delivery = _REGISTRY.get(key)
        if (
            delivery is not None
            and getattr(delivery, "request_approval", None) is not None
            and owner_id_for(key)
        ):
            return key, delivery
    return None


@dataclass(frozen=True)
class OwnerDelivery:
    """What happened to one message for the owner: which channel took it, or why none did."""

    #: The channel that delivered it (its registry key), ``""`` when none did.
    provider: str = ""
    #: That channel's handle and the DM it opened with the owner.
    delivery: "ChannelDelivery | None" = None
    channel: str = ""
    #: What the send returned (a message ts, a thread ts…).
    result: Any = None
    #: One clause per connected channel that could not, in the order they were tried.
    reasons: tuple[str, ...] = ()
    #: Whether one of those channels raised, so the gateway log holds an error for it.
    logged: bool = False
    #: Whether it went to the Inbox because no channel could take it.
    inboxed: bool = False
    #: The channel it was for, as it is shown, when one was named (``reach_owner``'s ``only``): no
    #: other channel was tried.
    named: str = ""

    @property
    def delivered(self) -> bool:
        return bool(self.provider)

    @property
    def no_channel(self) -> bool:
        """No channel was connected at all: nothing to fall back FROM, so nothing failed."""
        return not self.provider and not self.reasons

    def sentence(self) -> str:
        """Why no channel delivered it, for the owner. ``""`` when one did or none is connected."""
        if self.delivered or not self.reasons:
            return ""
        tail = " The gateway log has the errors." if self.logged else ""
        why = "; ".join(self.reasons)
        if self.named:
            return f"This was for {self.named} only, and it could not go out there: {why}.{tail}"
        return f"No channel could deliver this to you: {why}.{tail}"


def channel_shown_as(key: str) -> str:
    """The name a channel is shown under — its transport's own display name, else its key."""
    from personalclaw.channel_transports import get_transport

    transport = get_transport(key)
    return transport.display_name if transport is not None else key


def _chat_channels(transports: "Mapping[str, Any] | None" = None) -> dict[str, Any]:
    """The chat channels set up here, by key: *transports*, else the registered ones. The Web UI
    is not one."""
    from personalclaw.channel_transports import WEBUI_TRANSPORT, get_transport, list_transports

    if transports is None:
        transports = {key: get_transport(key) for key in list_transports()}
    return {k: t for k, t in transports.items() if k != WEBUI_TRANSPORT and t is not None}


def _shown(chat: Mapping[str, Any]) -> dict[str, str]:
    return {k: str(getattr(t, "display_name", "") or k) for k, t in chat.items()}


def chat_channel_names() -> dict[str, str]:
    """Every chat channel registered here, by key, with the name it is shown under (its key when it
    gives none). Core names no vendor, so a channel's name is always the one it registered."""
    return _shown(_chat_channels())


def _set_up_here(shown: Mapping[str, str]) -> str:
    """The sentence naming the chat channels set up here, for a refusal to end with."""
    if not shown:
        return "No chat channel is set up here."
    return f"The chat channels set up here: {', '.join(sorted(shown.values()))}."


def named_chat_channel(
    name: str, *, transports: "Mapping[str, Any] | None" = None
) -> tuple[str, str]:
    """The chat channel *name* means, as ``(key, "")``, or ``("", the sentence saying why not)``.

    For a message the owner asked for on one channel ("message me on Telegram"). *name* is the
    channel's key or the name it is shown under, in any case. A name that is not a chat channel set
    up here is refused with the ones that are, so the owner can be asked which: another channel
    never stands in for the one they named. *transports* is the channels to look in, by key; the
    registered ones when omitted.
    """
    shown = _shown(_chat_channels(transports))
    wanted = name.strip().casefold()
    for key, display in shown.items():
        if wanted and wanted in (key.casefold(), display.casefold()):
            return key, ""
    return "", (
        f"{name.strip() or 'That'} isn't one of the chat channels set up here. "
        f"{_set_up_here(shown)}"
    )


def same_user(owner_id: str, user_id: str) -> bool:
    """Whether *user_id* is the user *owner_id* names. Slack spells one user with a ``U`` or a
    ``W`` in front, so either matches the other."""
    if not owner_id or not user_id:
        return False
    return (
        user_id == owner_id
        or user_id.replace("W", "U", 1) == owner_id
        or user_id.replace("U", "W", 1) == owner_id
    )


#: The longest id a message may be addressed to: far longer than any platform's, and shorter than
#: pasted text.
_ID_MAX_LEN = 256


def id_problem(target: str) -> str:
    """Why *target* can't be anyone's id, in one sentence, or ``""`` when it could be: it is empty,
    longer than any platform's id, or has a space or control character in it. Which channel it
    belongs to, and whether it is one of that channel's, the channels answer (:func:`channel_of_id`,
    :func:`target_problem`)."""
    value = str(target or "")
    if not value:
        return "An id to send to can't be empty."
    if len(value) > _ID_MAX_LEN:
        return "That's too long to be an id to send to."
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in value):
        return "An id to send to has no spaces or control characters."
    return ""


def _takes(transport: Any, target: str) -> bool:
    """Whether *transport* takes *target* as one of its chat or channel ids. A check that raises
    claims nothing."""
    try:
        return not transport.validate_target(target)
    except Exception:  # noqa: BLE001 - a broken check must not claim someone else's id
        logger.warning("channel %s: validate_target raised", getattr(transport, "name", ""))
        return False


def channels_taking(
    target: str, *, user: bool = False, transports: "Mapping[str, Any] | None" = None
) -> list[str]:
    """The chat channels set up here that take *target*, by key and sorted: as a chat or channel
    id (``ChannelTransportProvider.validate_target``), or with *user* as the owner's user id there
    (``owner_id_for``), the owner being the one user core sends a message to."""
    chat = _chat_channels(transports)
    if user:
        from personalclaw.config.credentials import owner_id_for

        return [key for key in sorted(chat) if same_user(owner_id_for(key), target)]
    return [key for key in sorted(chat) if _takes(chat[key], target)]


def channel_of_id(
    target: str, *, user: bool = False, transports: "Mapping[str, Any] | None" = None
) -> tuple[str, str]:
    """The chat channel an id sent without naming its channel belongs to, as ``(key, "")``, or
    ``("", the sentence saying why not)``.

    *target* is a chat or channel id, or with *user* a user id. An id means nothing without the
    channel that issued it (#959), and core cannot tell one platform's ids from another's, so each
    chat channel set up here is asked (:func:`channels_taking`). Exactly one takes it → that one.
    None, or more than one, is refused with the channels to choose from, so the sender says which:
    an id is never handed to whichever channel happens to sort first. With no chat channel set up
    at all there is no channel to name, and the answer is ``("", "")``: the message has nowhere to
    go but PersonalClaw itself. *transports* is the channels to ask, by key; the registered ones
    when omitted.
    """
    chat = _chat_channels(transports)
    if not chat:
        return "", ""
    takes = channels_taking(target, user=user, transports=chat)
    if len(takes) == 1:
        return takes[0], ""
    shown = _shown(chat)
    names = ", ".join(shown[key] for key in takes)
    if user and not takes:
        return "", (
            f"{target} isn't the owner's user id on any chat channel set up here, so which "
            f"channel it is on is unknown. {_set_up_here(shown)} Say which one to send it on."
        )
    if user:
        return "", f"{target} is the owner's user id on {names}. Say which one to send it on."
    if not takes:
        return "", (
            f"No chat channel set up here takes {target} as a chat or channel id. "
            f"{_set_up_here(shown)} Say which one to send it on."
        )
    return "", f"{target} could be a chat or channel on {names}. Say which one to send it on."


def target_problem(key: str, target: str, *, transports: "Mapping[str, Any] | None" = None) -> str:
    """Why chat channel *key* can't send to chat or channel id *target*, or ``""`` if it can: the
    channel checks its own ids (``ChannelTransportProvider.validate_target``)."""
    transport = _chat_channels(transports).get(key)
    if transport is None:
        return f"{key} isn't one of the chat channels set up here."
    try:
        return str(transport.validate_target(target) or "")
    except Exception:  # noqa: BLE001 - a broken check refuses rather than letting any id through
        logger.warning("channel %s: validate_target raised", key, exc_info=True)
        return f"{channel_shown_as(key)} couldn't check that id."


async def reach_owner(
    send: "Callable[[ChannelDelivery, str], Awaitable[Any]]",
    *,
    only: str = "",
) -> OwnerDelivery:
    """Deliver to the owner through the FIRST connected channel that actually reaches them.

    ``only`` names the one channel to try — the owner chose it (a chat's "Continue on …", a
    message they asked for on that channel) — and no other is tried when it cannot. Not being
    connected is then a reason like the others, so the message is not taken for one with nowhere
    to go: a channel the owner did not name never stands in for the one they did.

    Every connected channel is tried in the stable order of their names until one delivers:
    its owner id (``config.credentials.owner_id_for`` — the channel's own key, else the shared
    one), the DM it opens with that id (``open_dm``), then ``send(delivery, dm)``. A channel
    with no owner id, one whose ``open_dm`` answers ``""`` or raises, and one whose send raises
    is passed over, with a clause saying so, and the next one is tried. The DM and the message
    always go through the same handle.

    This used to pick ONE channel — the first that knew any owner id — and stop there. With
    email and Telegram connected and only the shared id set (another platform's user id), email
    came first, its ``open_dm`` rightly refused an id that is not an address, and every owner
    notification was dropped although Telegram could have delivered it. Core names no channel
    here: each channel decides for itself whether the id it has reaches anyone.
    """
    from personalclaw.config.credentials import owner_id_for

    reasons: list[str] = []
    logged = False
    named = channel_shown_as(only) if only else ""
    for key in [only] if only else sorted(_REGISTRY):
        delivery = _REGISTRY.get(key)
        name = channel_shown_as(key)
        if delivery is None:
            # The named channel is not connected; any other was unregistered while an earlier
            # channel was being tried.
            if only:
                reasons.append(f"{name} isn't connected")
            continue
        owner = owner_id_for(key)
        if not owner:
            reasons.append(f"{name} has no owner id")
            continue
        try:
            dm = await delivery.open_dm(owner)
        except Exception:  # noqa: BLE001 - one channel's failure hands over to the next
            logger.warning("channel %s: opening the owner's DM failed", key, exc_info=True)
            dm, logged = "", True
        if not dm:
            reasons.append(f"{name} could not open a conversation with the owner id it has")
            continue
        try:
            result = await send(delivery, dm)
        except Exception:  # noqa: BLE001 - one channel's failure hands over to the next
            logger.warning("channel %s: delivering to the owner failed", key, exc_info=True)
            reasons.append(f"{name} could not send it")
            logged = True
            continue
        return OwnerDelivery(
            provider=key,
            delivery=delivery,
            channel=dm,
            result=result,
            reasons=tuple(reasons),
            logged=logged,
            named=named,
        )
    return OwnerDelivery(reasons=tuple(reasons), logged=logged, named=named)


async def deliver_to_owner(
    send: "Callable[[ChannelDelivery, str], Awaitable[Any]]",
    *,
    title: str,
    text: str,
    state: Any = None,
    only: str = "",
) -> OwnerDelivery:
    """Deliver a notification for the owner: the first channel that reaches them, else the Inbox.

    :func:`reach_owner` picks the channel, or tries the one ``only`` names and no other. When that
    channel, or every connected one, could not deliver, the notification goes to the Inbox (the
    native source, ``state`` or the one wired at startup), ending with the sentence saying why — it
    is never dropped. With no channel connected at all and none named, nothing failed: the caller's
    dashboard delivery is the delivery, and the Inbox is left alone.

    ``title`` and ``text`` are what the Inbox item shows.
    """
    outcome = await reach_owner(send, only=only)
    if outcome.delivered or outcome.no_channel:
        return outcome
    from personalclaw.inbox_providers.native_source import post_to_inbox

    # The reason is part of the message, after it: the Inbox shows an item's `context` under
    # "Context the agent used", which this is not.
    message = "\n\n".join(part for part in (title, text, outcome.sentence()) if part)
    try:
        item = post_to_inbox(message, kind="notification", sender_name="PersonalClaw", state=state)
    except Exception:  # noqa: BLE001 - the log line below is then the only record left
        logger.exception("owner notification: posting it to the Inbox failed")
        item = None
    if item is None:
        logger.error(
            "owner notification %r reached no channel and not the Inbox: %s",
            title,
            outcome.sentence(),
        )
    return replace(outcome, inboxed=item is not None)


def registered_providers() -> list[str]:
    """The connected providers, sorted. For diagnostics and tests."""
    return sorted(_REGISTRY)
