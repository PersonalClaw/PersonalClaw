"""Search provider registry + use-case resolution.

Holds the live :class:`~personalclaw.search_providers.base.SearchProvider`
instances (registered by the extension system's ``SearchTypeHandler`` when a
bundled/installed search extension is enabled) and resolves a use-case to a
provider via the active-binding store.

Resolution order (mirrors the Model bridge, minus the agent/native axis):
1. The provider bound to the use-case in ``active_search_providers.json``
   (Settings → Search); a non-general use-case borrows the general binding.
2. Implicit fallback: any registered provider (so search works out-of-box once a
   single provider is configured, without forcing a binding) — preferring an
   available one, and for ``fetch-article`` one that ``supports_fetch``.

Whether a provider WORKS is measured, never inferred from its settings: a key that is present
is not a key that is accepted. Each provider carries the outcome of its last search
(:func:`last_check`), recorded by its Test (:func:`check_provider`) and by every real search
(:func:`search_with_fallback`), and one nothing has measured says so.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from personalclaw.search_providers.base import SearchFallback, SearchProvider, SearchResult

logger = logging.getLogger(__name__)

#: What a provider's Test searches for: a query any engine answers that names nothing of the
#: user's. The Test spends one search from the provider's quota, as a search in a chat would.
CHECK_QUERY = "PersonalClaw"
#: The most one Test waits for its search before it reports the provider as not answering.
CHECK_TIMEOUT_SECS = 30.0


@dataclass(frozen=True)
class SearchCheck:
    """One measured search: whether it answered, the words its failure gave (masked, bounded),
    and when (epoch seconds)."""

    ok: bool
    detail: str
    checked_at: float

    def to_wire(self) -> dict[str, Any]:
        return {
            "state": "ok" if self.ok else "failed",
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


_providers: dict[str, SearchProvider] = {}
#: provider name → the app that registered it, so Settings → Providers can show a provider's
#: state on its app's card (the provider is ``brave``, the app ``brave-search``).
_provider_app: dict[str, str] = {}
#: provider name → its last measured search. Kept for the provider object that was measured: a
#: provider registered again was rebuilt from saved settings, and is not judged by the old answer.
_checks: dict[str, SearchCheck] = {}


def _keyless_provider() -> SearchProvider | None:
    """The zero-config out-of-box floor provider, chosen by the ``keyless``
    capability a provider DECLARES (not by naming a specific vendor in core).

    A keyless provider runs with no API key/config, so it guarantees a working
    web_search for a user who has bound nothing, and is the retry target when a
    bound provider errors. If several declare keyless, the first registered wins."""
    for p in _providers.values():
        try:
            if p.capabilities().keyless:
                return p
        except Exception:
            continue
    return None


def register_provider(provider: SearchProvider, *, app: str = "") -> None:
    """Register *provider*, from the installed *app* ("" for one core registers itself).

    A provider registered again under its name is the same app rebuilt from its saved settings
    (a new key, say), so what the previous object's searches answered is forgotten with it.
    """
    _providers[provider.name] = provider
    _checks.pop(provider.name, None)
    if app:
        _provider_app[provider.name] = app
    else:
        _provider_app.pop(provider.name, None)


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)
    _provider_app.pop(name, None)
    _checks.pop(name, None)


def app_of(name: str) -> str:
    """The installed app that registered the provider *name* ("" when none did)."""
    return _provider_app.get(name, "")


def last_check(name: str) -> SearchCheck | None:
    """The last measured search of the provider *name*, or ``None`` when nothing has measured it
    since it was registered — which is what a surface then says, not "ready"."""
    return _checks.get(name)


def _record(provider: SearchProvider, *, ok: bool, detail: str = "") -> SearchCheck:
    """Record how a search through *provider* went, while it is still the provider registered
    under its name: a search that ends after a settings save answered for the settings before."""
    check = SearchCheck(ok=ok, detail=detail, checked_at=time.time())
    if _providers.get(provider.name) is provider:
        _checks[provider.name] = check
    return check


def _failure_words(exc: BaseException) -> str:
    """What a failed search said: its own words, masked and cut to the relayed-failure bound."""
    from personalclaw.providers.failure_copy import failure_detail

    return failure_detail(str(exc)) or f"It failed ({type(exc).__name__}) without saying why."


def _sentence(words: str) -> str:
    """*words* ending as a sentence does, so a second one can follow it."""
    return words if words.endswith((".", "!", "?")) else f"{words}."


async def check_provider(provider: SearchProvider) -> SearchCheck:
    """The Test: one small search through *provider* now, recorded as its last check.

    A real search, because that is the only thing that shows a key is accepted: a provider's
    ``is_available`` says only that it has what it needs to try.
    """
    try:
        await asyncio.wait_for(provider.search(CHECK_QUERY, max_results=1), CHECK_TIMEOUT_SECS)
    except TimeoutError:
        return _record(
            provider, ok=False, detail=f"It did not answer within {CHECK_TIMEOUT_SECS:.0f} s."
        )
    except Exception as exc:  # noqa: BLE001 — a failed Test is its answer, never a crash
        logger.debug("search provider %r Test failed", provider.name, exc_info=True)
        return _record(provider, ok=False, detail=_failure_words(exc))
    return _record(provider, ok=True)


def get_provider(name: str) -> SearchProvider | None:
    return _providers.get(name)


def list_providers() -> list[SearchProvider]:
    return list(_providers.values())


async def _first_available(candidates: list[SearchProvider]) -> SearchProvider | None:
    """Return the first candidate whose ``is_available()`` resolves True, else the
    first candidate at all (so a probe failure doesn't strand a sole provider)."""
    for p in candidates:
        try:
            if await p.is_available():
                return p
        except Exception:
            logger.debug("search provider %r availability probe failed", p.name, exc_info=True)
    return candidates[0] if candidates else None


async def resolve_search_provider_for_use_case(use_case: str) -> SearchProvider | None:
    """Resolve a search use-case to a live provider, or None if none is resolvable.

    Async because availability is probed (credential/endpoint resolution). Callers
    that just need "is anything bound" without a probe use
    :func:`can_resolve_search_use_case`.
    """
    from personalclaw.search_providers.use_cases import (
        VALID_SEARCH_USE_CASES,
        active_search_provider_names,
    )

    if use_case not in VALID_SEARCH_USE_CASES:
        raise ValueError(f"Unknown search use case: {use_case!r}")

    # 1. The active binding (Settings → Search), with the general-fallback applied.
    for name in active_search_provider_names(use_case):
        provider = _providers.get(name)
        if provider is not None:
            return provider
        # A bound name whose provider isn't registered (disabled/removed) → fall
        # through to the implicit fallback rather than failing outright.
        logger.info(
            "bound search provider %r for %r is not registered; falling back", name, use_case
        )
        break

    # 2. Implicit fallback: any registered provider. For fetch-article, prefer one
    #    that can actually extract content; otherwise prefer any available one. A
    #    provider that declares itself ``keyless`` sorts last among candidates so a
    #    user-configured/keyed provider always wins when present, while still
    #    guaranteeing a working web_search for a user who has configured nothing.
    def _keyless_key(p: SearchProvider) -> bool:
        try:
            return p.capabilities().keyless
        except Exception:
            return False

    candidates = sorted(_providers.values(), key=_keyless_key)
    if not candidates:
        return None
    if use_case == "fetch-article":
        fetchers = [p for p in candidates if p.capabilities().supports_fetch]
        if fetchers:
            return await _first_available(fetchers)
    return await _first_available(candidates)


async def search_with_fallback(use_case: str, query: str, **kw: Any) -> SearchResult | None:
    """Resolve + run a search for ``use_case``, degrading to the keyless default when
    the bound provider fails at call time. ``None`` when no provider is registered at all.

    A user may bind a keyed provider (Tavily/Exa/…) whose key later expires or hits a
    quota — the bound provider *resolves* fine but its ``search()`` raises (e.g. HTTP
    432). Rather than hard-fail (defeating the point of shipping a keyless floor), retry
    once with the keyless provider (the one declaring keyless=True) when it's a different,
    registered provider — and SAY so: the result's ``fallback`` names the provider that failed
    and what it said, so the agent and the user are told the answer came from somewhere else.
    Every search is recorded as its provider's last check (:func:`last_check`). The original
    error is raised when there is no fallback to try; when the fallback fails too, the error
    says what each of them said, the bound provider's first.
    """
    provider = await resolve_search_provider_for_use_case(use_case)
    if provider is None:
        return None
    try:
        result = await provider.search(query, **kw)
    except Exception as exc:
        reason = _failure_words(exc)
        _record(provider, ok=False, detail=reason)
        fallback = _keyless_provider()
        if fallback is None or fallback.name == provider.name:
            raise
        logger.warning(
            "search via %r failed (%s); falling back to keyless %r",
            provider.name,
            reason,
            fallback.name,
        )
        # The keyless default may not honor every kwarg (depth/domains) — it clamps
        # them itself, so pass through unchanged.
        try:
            result = await fallback.search(query, **kw)
        except Exception as fallback_exc:
            also = _failure_words(fallback_exc)
            _record(fallback, ok=False, detail=also)
            # Both, the bound one first: the fallback's error alone says the search went to an
            # engine the owner never chose, and hides why the one they chose failed.
            raise RuntimeError(
                f"{provider.display_name} failed: {_sentence(reason)} {fallback.display_name}, "
                f"tried instead, failed too: {_sentence(also)}"
            ) from fallback_exc
        _record(fallback, ok=True)
        result.fallback = SearchFallback(
            provider=provider.name,
            display_name=provider.display_name,
            served_by=fallback.display_name,
            reason=reason,
        )
        return result
    _record(provider, ok=True)
    return result


async def fetch_with_fallback(url: str, **kw):
    """Extract a single URL's content via the ``fetch-article``-bound provider, falling
    back to the native fetch pipeline when no fetch-capable provider is bound or the
    bound one fails at call time.

    Returns ``(FetchResult, used_native: bool)``. A provider's ``fetch()`` (Tavily
    ``/extract``, Exa ``/contents``) is an optional optimization; the native
    ``web.fetch`` pipeline (egress-guarded httpx + trafilatura) is the always-available
    spine, so a missing/expired key never breaks single-URL extraction — it just routes
    through the native path.
    """
    from personalclaw.search_providers.base import FetchResult

    provider = await resolve_search_provider_for_use_case("fetch-article")
    if provider is not None and provider.capabilities().supports_fetch:
        try:
            return await provider.fetch(url, **kw), False
        except Exception as exc:
            logger.warning(
                "fetch via %r failed (%s); falling back to native pipeline", provider.name, exc
            )

    # Native fallback: the guarded web_fetch pipeline (no provider, no key).
    from personalclaw.web.fetch import web_fetch as _native_fetch

    max_tokens = kw.get("max_tokens", 0) or 5000
    start_index = kw.get("start_index", 0) or 0
    outcome = await _native_fetch(
        url,
        max_tokens=max_tokens,
        start_index=start_index,
        require_provenance=False,
    )
    if not outcome.ok:
        raise RuntimeError(outcome.error or "fetch failed")
    return (
        FetchResult(
            url=outcome.url,
            content=outcome.content,
            title=outcome.title,
            char_count=outcome.char_count,
            truncated=outcome.truncated,
            next_index=outcome.next_index,
        ),
        True,
    )


def can_resolve_search_use_case(use_case: str) -> bool:
    """Cheaply report whether *some* provider could serve ``use_case`` without
    probing availability (for hot GETs / readiness signals). True when the
    use-case is bound to a registered provider OR any provider is registered."""
    try:
        from personalclaw.search_providers.use_cases import (
            VALID_SEARCH_USE_CASES,
            active_search_provider_names,
        )
    except Exception:
        return False
    if use_case not in VALID_SEARCH_USE_CASES:
        return False
    for name in active_search_provider_names(use_case):
        if name in _providers:
            return True
    return bool(_providers)
