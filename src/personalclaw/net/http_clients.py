"""The HTTP clients a model provider and an app send their requests with, each asking the egress
guard about every request before it leaves, each redirect hop included.

``net.fetch`` sends one request and buffers its answer. A client library sends its own: a model
provider's SDK and an app's own HTTP code send many requests, stream them, retry them and follow
their redirects by themselves, so the guard is put inside the client, in front of every request:

* :func:`http_client` and :func:`sync_http_client`: an ``httpx`` client whose first request hook
  asks the guard. httpx runs a client's request hooks for every request it sends, the request it
  sends to follow a redirect included, so each hop is asked before it is sent. A model SDK that
  takes an ``http_client`` (``openai``, ``anthropic``) is handed one.
* :func:`http_session`: an ``aiohttp`` session whose connector asks the guard. aiohttp asks its
  connector for a connection for every request it sends, each redirect hop included.
* :class:`RequestGuard` is the guard itself, for a client library that takes no HTTP client of its
  caller's: its ``ask(url)`` raises :class:`EgressBlocked` for a request the guard refuses. The
  caller asks it from the hook the library offers before each request it sends; that glue is the
  library's own, so it lives with the app that uses the library, never here.

What each asks (:class:`RequestGuard`): the connector policy with the owner's Network egress
settings on it, or, for a model provider, :data:`~personalclaw.net.policy.MODEL_PROVIDER` with
them on it. The endpoint the owner configured for a client stays reachable on their own machine or
network (:func:`~personalclaw.net.policy.with_endpoint`). A host on Denied hosts is never
contacted: the request is refused before it is sent, as :class:`EgressBlocked` carrying the
sentence that names the setting and the host (:func:`~personalclaw.net.guard.refusal_for`), and
the refusal is in the audit log (``egress_fetch``), as is every request let through.

A request made for a run keeps to that run's egress tier as well, as a fetch through the SDK does,
except a request of a client every run shares: the agent's own model (its chat, its stream, its
embeddings, its model list), which a run whose tier is ``off`` still thinks with. Those keep to the
owner's settings alone.

What these cannot do that ``net.fetch`` does: the guard checks the address a name resolves to when
it is asked, and the client then resolves the name again for itself, so the connection is not held
to the address the guard checked.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from personalclaw.net.client import EgressBlocked, audit, refusal_in
from personalclaw.net.guard import GuardDecision, evaluate, refusal_for
from personalclaw.net.policy import (
    CONNECTOR,
    EgressPolicy,
    egress_held_to,
    egress_policy_for,
    provider_egress_policy,
    with_endpoint,
)

if TYPE_CHECKING:
    import aiohttp
    import httpx

#: The door these clients' rows in the audit log name (``egress_fetch`` from
#: ``net.client:<policy>``).
DOOR = "net.client"

#: How a refusal's sentence ends: the next step once the owner has allowed the host.
TRY_AGAIN = "then try again"


@dataclass(frozen=True, kw_only=True)
class RequestGuard:
    """What a client asks the egress guard before each request it sends: :meth:`ask` (or
    :meth:`ask_async`) with the request's URL, which raises :class:`EgressBlocked`, its message the
    sentence the owner reads, for a request the guard refuses, and audits the answer either way.
    :meth:`ask` looks the host up, so it blocks: on an event loop, ask :meth:`ask_async`. A hook a
    client library runs before it sends a request asks it with the request's URL and lets the
    refusal propagate, so the library stops the request before it is sent.

    * *endpoint* is the address the owner configured for the client (a provider's base URL): its
      host is reachable on their own machine or network.
    * *model_provider* judges the requests as a model provider's
      (:data:`~personalclaw.net.policy.MODEL_PROVIDER`), not as an app's own (the connector policy).
    * *shared_by_every_run* is for a client every run shares, the agent's own model (its chat, its
      stream, its embeddings, its model list): its requests keep to the owner's Network egress
      settings alone, never to the egress tier of the run a call is made for. Every other client's
      requests keep to that tier as well.
    * *then* ends a refusal's sentence.
    * *refused_as* is an exception class the refusal is raised as besides :class:`EgressBlocked`: a
      client library that retries a request whose sending failed, and wraps the failure in its own
      error, raises its own base error as it is, so a refusal named as one is final at once (the
      ``openai`` and ``anthropic`` clients' ``OpenAIError`` and ``AnthropicError``).
    """

    endpoint: str = ""
    model_provider: bool = False
    shared_by_every_run: bool = False
    then: str = TRY_AGAIN
    refused_as: type[Exception] | None = None

    def _policy(self) -> EgressPolicy:
        """The policy a request is judged by, read when it is asked: the owner's settings now."""
        if self.model_provider:
            return provider_egress_policy(self.endpoint)
        return with_endpoint(egress_policy_for(CONNECTOR), self.endpoint)

    def ask(self, url: str) -> None:
        """Ask the guard about one request to *url* and audit the answer; raise the refusal."""
        policy = self._policy()
        if self.shared_by_every_run:
            # Shared by every run, so no run's tier narrows what this client reaches.
            with egress_held_to(""):
                decision = evaluate(url, policy)
        else:
            decision = evaluate(url, policy)
        if decision.allow:
            audit(url, policy, outcome="allowed", door=DOOR)
            return
        audit(url, policy, outcome="denied", reason=decision.reason, door=DOOR)
        raise self._refusal(url, decision)

    async def ask_async(self, url: str) -> None:
        """:meth:`ask`, off the event loop: the guard looks the host up, which blocks. The run a
        call is made for goes with it, as it goes with any work handed to a thread this way."""
        await asyncio.to_thread(self.ask, url)

    def _refusal(self, url: str, decision: GuardDecision) -> EgressBlocked:
        """The refusal of *url*, its message the sentence the owner reads."""
        kind = EgressBlocked if self.refused_as is None else _refused_as(self.refused_as)
        refused = kind(decision)
        refused.args = (refusal_for(url, decision, then=self.then),)
        return refused


def refusal_sentence(exc: BaseException | None) -> str:
    """The sentence for the egress guard's refusal that *exc* is or was raised over
    (:func:`~personalclaw.net.client.refusal_in`), as these clients say it: what was not reached
    and the setting that decided it. ``""`` when *exc* is no refusal."""
    refused = refusal_in(exc)
    if refused is None:
        return ""
    return refusal_for(refused.decision.url, refused.decision, then=TRY_AGAIN)


#: The refusal made for each client library's own error, made once.
_REFUSALS_AS: dict[type[Exception], type[EgressBlocked]] = {}


def _refused_as(error: type[Exception]) -> type[EgressBlocked]:
    """A refusal that is also *error*, for a client library that treats its own errors as final."""
    kind = _REFUSALS_AS.get(error)
    if kind is None:
        kind = type("EgressBlocked", (EgressBlocked, error), {"__module__": __name__})
        _REFUSALS_AS[error] = kind
    return kind


def _with_hook(hooks: Mapping[str, list[Any]] | None, first: Any) -> dict[str, list[Any]]:
    """*hooks* with *first* run before each request, ahead of the caller's own request hooks."""
    given = dict(hooks or {})
    return {
        "request": [first, *given.get("request", [])],
        "response": list(given.get("response", [])),
    }


def http_client(
    *,
    endpoint: str = "",
    model_provider: bool = False,
    shared_by_every_run: bool = False,
    then: str = TRY_AGAIN,
    refused_as: type[Exception] | None = None,
    **options: Any,
) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` that asks the egress guard about every request it sends, each
    redirect hop included (:class:`RequestGuard` for the keywords before *options*). *options* are
    ``httpx.AsyncClient``'s own, ``event_hooks`` included: the guard's hook runs before the
    caller's. A refused request raises :class:`EgressBlocked` before it is sent."""
    import httpx

    guard = RequestGuard(
        endpoint=endpoint,
        model_provider=model_provider,
        shared_by_every_run=shared_by_every_run,
        then=then,
        refused_as=refused_as,
    )

    async def ask(request: httpx.Request) -> None:
        await guard.ask_async(str(request.url))

    hooks = _with_hook(options.pop("event_hooks", None), ask)
    return httpx.AsyncClient(event_hooks=hooks, **options)


def sync_http_client(
    *,
    endpoint: str = "",
    model_provider: bool = False,
    shared_by_every_run: bool = False,
    then: str = TRY_AGAIN,
    refused_as: type[Exception] | None = None,
    **options: Any,
) -> httpx.Client:
    """:func:`http_client`'s synchronous twin: an ``httpx.Client`` asking the guard about every
    request it sends, each redirect hop included."""
    import httpx

    guard = RequestGuard(
        endpoint=endpoint,
        model_provider=model_provider,
        shared_by_every_run=shared_by_every_run,
        then=then,
        refused_as=refused_as,
    )

    def ask(request: httpx.Request) -> None:
        guard.ask(str(request.url))

    hooks = _with_hook(options.pop("event_hooks", None), ask)
    return httpx.Client(event_hooks=hooks, **options)


@functools.cache
def _asking_connector() -> type:
    """The ``aiohttp`` connector that asks the guard before every connection it is asked for:
    aiohttp asks it once for each request it sends, each redirect hop included."""
    import aiohttp

    class AskingConnector(aiohttp.TCPConnector):
        def __init__(self, guard: RequestGuard, **options: Any) -> None:
            super().__init__(**options)
            self._guard = guard

        async def connect(self, req: Any, traces: Any, timeout: Any) -> Any:
            await self._guard.ask_async(str(req.url))
            return await super().connect(req, traces, timeout)

    return AskingConnector


def http_session(
    *,
    endpoint: str = "",
    model_provider: bool = False,
    shared_by_every_run: bool = False,
    then: str = TRY_AGAIN,
    **options: Any,
) -> aiohttp.ClientSession:
    """An ``aiohttp.ClientSession`` that asks the egress guard about every request it sends, each
    redirect hop included (:class:`RequestGuard` for the keywords before *options*). *options* are
    ``aiohttp.ClientSession``'s own, except ``connector``: the session's connector is the one that
    asks. Made inside a running event loop, as any session is."""
    import aiohttp

    guard = RequestGuard(
        endpoint=endpoint,
        model_provider=model_provider,
        shared_by_every_run=shared_by_every_run,
        then=then,
    )
    return aiohttp.ClientSession(connector=_asking_connector()(guard), **options)


__all__ = [
    "DOOR",
    "RequestGuard",
    "TRY_AGAIN",
    "http_client",
    "http_session",
    "refusal_sentence",
    "sync_http_client",
]
