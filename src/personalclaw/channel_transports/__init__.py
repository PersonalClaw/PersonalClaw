"""Channel transports — the comms-transport registry, and the one rule that runs their receivers.

A small in-memory registry of live :class:`ChannelTransportProvider` instances, keyed by
transport name. Two population paths:

- **Web UI** — the always-present in-app transport, registered at boot by
  :func:`register_default_transports` (it is not an extension).
- **Channel apps** (Slack, Telegram, Discord, email…) — registered/unregistered by the
  extension system: enabling or installing a channel app runs ``ChannelTypeHandler.register`` →
  :func:`register_transport`; disabling or removing it runs ``deregister`` →
  :func:`unregister_transport`; saving its settings or updating it does both, for a new instance.

**Receivers.** Every change to this registry asks for :func:`reconcile_inbound` — the one function
that decides which inbound receivers run: exactly one per registered channel that reports itself
configured, started on the instance registered NOW, and none for anything else. The gateway binds
its services handle with :func:`bind_inbound` at boot (the first reconciliation) and
:func:`unbind_inbound` at shutdown. Receivers used to be started once, at boot, and never stopped:
a channel enabled, installed or updated afterwards had no receiver until a restart, and one
disabled or uninstalled kept answering messages — on a token the uninstall had deleted. Before the
bind (a CLI process, a test with no gateway) nothing can receive, so a registry change starts
nothing.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from personalclaw.channel_transports.base import ChannelTransportProvider

logger = logging.getLogger(__name__)

_transports: "dict[str, ChannelTransportProvider]" = {}

#: The one in-app transport. It is how the dashboard itself talks, not a channel through which a
#: remote owner can be reached, so no "is a channel configured" question counts it — and it has no
#: receiver to run (the dashboard chat runner is its inbound).
WEBUI_TRANSPORT = "webui"

#: How long a receiver may take to start before it is stopped and reported as not starting.
#: Generous — Slack's own connect retries three times with a back-off — because the timeout is a
#: verdict shown to the owner. It exists so one hung start cannot read "starting" for ever.
START_TIMEOUT_SECS = 60.0
#: How long stopping a receiver may take before it is abandoned (and logged), so a receiver that
#: will not stop cannot hold up the one replacing it, or the gateway's shutdown.
STOP_TIMEOUT_SECS = 10.0
#: How long a channel's health probe may take inside a reconciliation.
HEALTH_TIMEOUT_SECS = 5.0

#: The ``health()`` state core reports while a receiver is starting. Core's own: a transport reports
#: only ``ready`` / ``offline`` / ``error`` (the conformance kit's closed set).
STARTING = "starting"


@dataclass
class _Receiver:
    """The receiver core runs for one channel: the instance it runs on, and its start."""

    transport: "ChannelTransportProvider"
    #: Resolves to ``""`` once the receiver runs, or to the sentence saying why it does not.
    start: "asyncio.Task[str]"

    @property
    def starting(self) -> bool:
        return not self.start.done()

    @property
    def failure(self) -> str:
        if not self.start.done() or self.start.cancelled():
            return ""
        return self.start.result()


@dataclass
class _Binding:
    """The gateway's services handle and the loop every receiver runs on."""

    services: Any
    loop: asyncio.AbstractEventLoop
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: "asyncio.Task[None] | None" = None
    again: bool = False


_binding: "_Binding | None" = None
#: The receiver core runs per channel NAME — so there is never room for two.
_receivers: "dict[str, _Receiver]" = {}


def register_transport(provider: "ChannelTransportProvider") -> None:
    _transports[provider.name] = provider
    request_reconcile()


def unregister_transport(name: str) -> None:
    _transports.pop(name, None)
    request_reconcile()


def get_transport(name: str) -> "ChannelTransportProvider | None":
    return _transports.get(name)


def list_transports() -> list[str]:
    return list(_transports.keys())


def register_default_transports() -> None:
    """Register the always-present in-app Web UI transport. Idempotent.

    Slack is NOT registered here — the extension system owns its lifecycle via
    ``ChannelTypeHandler`` (enable/disable). This keeps one source of truth for
    every extension-backed transport.
    """
    from personalclaw.channel_transports.webui import WebUITransport

    register_transport(WebUITransport())


# ── receivers ──────────────────────────────────────────────────────────────────────────────


async def bind_inbound(services: Any) -> None:
    """Hand the receivers the gateway's services, and reconcile for the first time (boot).

    ``services`` is the :class:`~personalclaw.gateway_services.GatewayServices` handle every
    receiver is started with. Returns once the first pass has begun each configured channel's
    start; the starts themselves run on, so a slow channel does not hold up the boot.
    """
    global _binding
    _binding = _Binding(services=services, loop=asyncio.get_running_loop())
    await reconcile_inbound()


async def unbind_inbound() -> None:
    """Stop every receiver and forget the services (gateway shutdown). Nothing starts after this."""
    global _binding
    binding, _binding = _binding, None
    if binding is None:
        return
    async with binding.lock:  # let a pass that is already running finish first
        await asyncio.gather(*(_retire(name) for name in list(_receivers)))


def request_reconcile() -> None:
    """Ask for :func:`reconcile_inbound` to run soon. Callable from any thread; returns at once.

    The registry changes on the event loop (a settings save, the Providers toggle) and on worker
    threads (the app lifecycle routes run ``app_manager`` off the loop), so the request hops onto
    the gateway's loop instead of touching a receiver from the caller's thread. Requests arriving
    while a pass runs fold into one more pass, never a concurrent one.
    """
    binding = _binding
    if binding is None or binding.loop.is_closed():
        return
    try:
        on_loop = asyncio.get_running_loop() is binding.loop
    except RuntimeError:
        on_loop = False
    if on_loop:
        _schedule(binding)
        return
    try:
        binding.loop.call_soon_threadsafe(_schedule, binding)
    except RuntimeError:  # the loop closed between the check and the call: nothing runs anyway
        pass


def _schedule(binding: _Binding) -> None:
    if _binding is not binding:  # unbound (or bound again) since the request was made
        return
    binding.again = True
    if binding.pending is None or binding.pending.done():
        binding.pending = binding.loop.create_task(_drain(binding), name="channel-receivers")


async def _drain(binding: _Binding) -> None:
    while binding.again and _binding is binding:
        binding.again = False
        await reconcile_inbound()


async def settled() -> None:
    """Return once every reconciliation requested so far has run.

    A reader that reports what runs (the Channels page) waits on this, so it cannot answer from the
    moment between a registry change and the pass that acts on it. It does not wait for the starts a
    pass began — those report ``starting`` until they finish.
    """
    binding = _binding
    while binding is not None and binding.pending is not None and not binding.pending.done():
        await asyncio.wait({binding.pending})
        binding = _binding


#: How long a thread may wait for the receivers to settle before going on without them.
SETTLE_TIMEOUT_SECS = 30.0


def settle_from_thread(timeout: float = SETTLE_TIMEOUT_SECS) -> None:
    """Block until every reconciliation requested so far has run — from a thread off the loop.

    For a caller about to take away what a receiver runs on: an app's unload purges the app's
    modules, and the receiver it just asked to stop has to be stopped by then, not still running
    that code on the loop. Returns at once when nothing is bound, and when called on the loop
    itself, which cannot wait for its own pass.
    """
    binding = _binding
    if binding is None or binding.loop.is_closed():
        return
    try:
        if asyncio.get_running_loop() is binding.loop:
            return
    except RuntimeError:
        pass
    try:
        asyncio.run_coroutine_threadsafe(settled(), binding.loop).result(timeout)
    except Exception:  # noqa: BLE001 - a receiver that will not settle must not hold the caller
        logger.warning("channel receivers did not settle within %gs", timeout, exc_info=True)


async def reconcile_inbound() -> None:
    """THE lifecycle rule for channel receivers — run after anything that can change one.

    For every registered channel (not the Web UI, whose inbound is the dashboard itself):

    * it gets a receiver when its own ``health()`` says it is configured — any state but
      ``offline``, the rule :func:`configured_channels` answers with;
    * a receiver running on ANOTHER instance of it (rebuilt from saved settings, updated, turned off
      and on) is stopped before the registered instance starts, so two never run at once;
    * a receiver whose channel was disabled or removed, or reports ``offline`` now (its credential
      is gone), is stopped.

    Each start runs as its own task, so a slow or failing channel never holds up another. A start
    that raises or times out becomes that channel's health — the sentence saying why — until the
    channel changes (its settings are saved, it is turned off and on, it is updated). Stopping a
    receiver also drops the outbound delivery handle registered under the channel's name: a channel
    that is turned off does not keep taking the owner's notifications.

    Idempotent: with nothing changed it changes nothing. A no-op until :func:`bind_inbound`.
    """
    binding = _binding
    if binding is None:
        return
    async with binding.lock:
        if _binding is not binding:
            return
        wanted: "dict[str, ChannelTransportProvider]" = {}
        for name, transport in list(_transports.items()):
            if name == WEBUI_TRANSPORT:
                continue
            configured = await _configured(transport)
            running = _receivers.get(name)
            if configured or (
                configured is None and running is not None and running.transport is transport
            ):
                wanted[name] = transport
        for name in [n for n, r in _receivers.items() if wanted.get(n) is not r.transport]:
            await _retire(name)
        for name, transport in wanted.items():
            if name not in _receivers:
                _receivers[name] = _Receiver(
                    transport,
                    binding.loop.create_task(
                        _start(transport, binding.services), name=f"channel-receiver:{name}"
                    ),
                )


async def _configured(transport: "ChannelTransportProvider") -> "bool | None":
    """Whether the channel says it is configured; ``None`` when its probe cannot answer.

    Unanswered is not "unconfigured": a probe that raises or hangs once must not stop a receiver
    that is working, so the caller leaves such a channel as it is.
    """
    try:
        health = await asyncio.wait_for(transport.health(), timeout=HEALTH_TIMEOUT_SECS)
    except Exception:
        logger.warning("channel %s: health probe failed", transport.name, exc_info=True)
        return None
    return health.get("state") != "offline"


async def _start(transport: "ChannelTransportProvider", services: Any) -> str:
    """Start one channel's receiver: ``""`` once it runs, else the sentence saying why not."""
    loop = asyncio.get_running_loop()
    began = loop.time()
    try:
        await asyncio.wait_for(transport.start_inbound(services), timeout=START_TIMEOUT_SECS)
    except Exception as exc:  # noqa: BLE001 - one channel's broken start must not reach the rest
        # `wait_for` gives up with a TimeoutError — and so can the transport's own code, early.
        # Only the first is "did not finish starting in time"; the second is its own error.
        timed_out = isinstance(exc, TimeoutError) and loop.time() - began >= START_TIMEOUT_SECS
        reason = (
            f"it did not finish starting within {START_TIMEOUT_SECS:g} seconds"
            if timed_out
            else _describe(exc)
        )
    else:
        logger.info("channel %s: receiver started", transport.name)
        return ""
    logger.warning("channel %s: receiver did not start: %s", transport.name, reason)
    await _stop(transport)  # whatever it started before failing goes with it
    reason = reason.rstrip(". ")  # an exception's own full stop, before the sentence's
    # Both remedies are true of THIS code: a save rebuilds the channel and a re-enable registers a
    # new instance, and either one is a new start (a failed instance is not retried on its own).
    return (
        f"{transport.display_name} is not receiving messages — its receiver did not start: "
        f"{reason}. Fix its settings, or turn it off and on, to try again."
    )


async def _retire(name: str) -> None:
    """Stop the receiver core runs for ``name``, a start still in flight included."""
    receiver = _receivers.pop(name, None)
    if receiver is None:
        return
    if receiver.starting:
        receiver.start.cancel()
        await asyncio.wait({receiver.start})
    if not receiver.failure:  # a failed start already stopped what it had begun
        await _stop(receiver.transport)


async def _stop(transport: "ChannelTransportProvider") -> None:
    """Stop ``transport``'s receiver and drop its outbound delivery handle. Never raises."""
    from personalclaw.channel_delivery import register

    try:
        await asyncio.wait_for(transport.stop_inbound(), timeout=STOP_TIMEOUT_SECS)
    except Exception:  # noqa: BLE001 - a receiver that will not stop must not keep the next one out
        logger.warning("channel %s: stopping its receiver failed", transport.name, exc_info=True)
    else:
        logger.info("channel %s: receiver stopped", transport.name)
    # Keyed by the channel's name — the provider string it passes to `deliver_channel_inbound`,
    # which is also the key a reply resolves its delivery by (`channel_delivery.delivery_for`).
    register(None, provider=transport.name)


_URL_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://[^/\s'\"<>]+)[^\s'\"<>]*", re.IGNORECASE)


def _describe(exc: BaseException) -> str:
    """One line naming what went wrong, safe to show: credentials masked, URL paths dropped.

    A vendor client's error can carry the request URL, and some vendors put the token in the path
    (``/bot<token>/getMe``) — a shape no credential pattern recognises — so a URL keeps only its
    scheme and host, which is the part that says what could not be reached.
    """
    from personalclaw.security import redact_for_display

    text = " ".join(str(exc).split())
    text = _URL_RE.sub(lambda m: m.group(1), redact_for_display(text))
    detail = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return detail if len(detail) <= 240 else detail[:239] + "…"


async def channel_health(transport: "ChannelTransportProvider") -> dict[str, Any]:
    """What a channel's status says: its receiver's start while that is the news, else its health.

    A start in flight reads ``starting``; a start that failed reads ``error`` with the sentence
    saying why. Otherwise the transport's own ``health()`` answers — it knows whether its socket is
    up. A probe that raises reads as an error naming it, so one channel cannot break a listing.
    """
    receiver = _receivers.get(transport.name)
    if receiver is not None and receiver.transport is transport:
        if receiver.starting:
            return {
                "state": STARTING,
                "detail": f"{transport.display_name} is starting to receive messages.",
            }
        if receiver.failure:
            return {"state": "error", "detail": receiver.failure}
    try:
        return await transport.health()
    except Exception as exc:  # noqa: BLE001 - a transport's probe must never break the list
        return {"state": "error", "detail": _describe(exc)}


async def configured_channels(
    transports: "Iterable[ChannelTransportProvider] | None" = None,
) -> list[str]:
    """Display names of the external channels that report themselves configured.

    Asked of each channel's OWN ``health()``, because only the app knows what it needs: a state of
    ``offline`` is a transport saying it has nothing to connect with (no token, no account), while
    ``ready`` and ``error`` (half-up — outbound works, inbound waits) both mean it is configured.
    Core used to answer this from two Slack credential NAMES, which put one vendor in core and
    could not see a Slack configured through its settings.

    ``transports`` defaults to the registered ones (the gateway, or a CLI command that booted the
    provider registry). A probe that raises is not a configured channel, and says why.
    """
    names: list[str] = []
    candidates = (
        list(transports)
        if transports is not None
        else [t for t in (get_transport(n) for n in list_transports()) if t is not None]
    )
    for transport in candidates:
        if transport.name == WEBUI_TRANSPORT:
            continue
        try:
            state = (await transport.health()).get("state")
        except Exception:
            logger.warning("channel %s: health probe failed", transport.name, exc_info=True)
            continue
        if state != "offline":
            names.append(transport.display_name)
    return names
