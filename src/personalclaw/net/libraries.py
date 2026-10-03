"""The libraries that send requests by themselves, held to the egress guard.

huggingface_hub (the model and voice downloads apps start) sends its requests with an HTTP client
of its own, inside its own calls, where no app can put the guard in front of them, and it follows
redirects by itself, from the Hub to the storage that serves a file. So in every PersonalClaw
process the library is given clients that ask the egress guard before each request leaves, each
redirect included (``net.client.check``: the connector policy with the owner's Network egress
settings on it, the one :func:`personalclaw.net.client.open_url` asks too): a refused host is never
contacted, the request fails with the sentence that names the setting which allows it, and every
request is audited (``egress_fetch``).

The guard is put in as the library is first imported (:func:`install` adds an import hook, so a
command that never downloads never pays for importing it), and before the library's first request.

What it cannot do: the guard checks the address a name resolves to when it is asked, and the
library's client then resolves the name again for itself, so unlike ``net.fetch`` it does not hold
the connection to the address it checked. And a process PersonalClaw starts that does not import
PersonalClaw (an app's sidecar, which runs in the app's own environment) is given the library's
settings (``library_env``), not this client.

The library's native Xet client opens its own connections, where no client can be given, so it is
switched off (``library_env``: ``HF_HUB_DISABLE_XET``) and a Xet-stored file downloads over the
guarded client. A library that cannot be given a client is switched offline instead
(``HF_HUB_OFFLINE``): its downloads are refused, never sent past the guard.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.abc
import logging
import os
import sys
import threading
from typing import Any

from personalclaw.net.client import check

logger = logging.getLogger(__name__)

#: The library this module guards, by its import name.
HUB = "huggingface_hub"

_lock = threading.Lock()


def _before(request: Any) -> None:
    check(str(request.url))


async def _before_async(request: Any) -> None:
    await asyncio.to_thread(check, str(request.url))


def _with(client: Any, hook: Any) -> Any:
    """*client* with *hook* run before each request it sends, ahead of the library's own."""
    hooks = client.event_hooks
    client.event_hooks = {**hooks, "request": [hook, *hooks.get("request", [])]}
    return client


def _hub_http() -> Any:
    """The library's own HTTP module, which holds its default clients, or ``None``."""
    try:
        return importlib.import_module(f"{HUB}.utils._http")
    except ImportError:
        return None


def guard_the_hub(hub: Any) -> None:
    """Give the imported library *hub* clients that ask the guard first, or, when it has no seam
    for one, switch it offline."""
    http = _hub_http()
    set_client = getattr(hub, "set_client_factory", None)
    set_async_client = getattr(hub, "set_async_client_factory", None)
    default_client = getattr(http, "default_client_factory", None)
    default_async_client = getattr(http, "default_async_client_factory", None)
    if (
        set_client is None
        or set_async_client is None
        or default_client is None
        or default_async_client is None
    ):
        os.environ["HF_HUB_OFFLINE"] = "1"
        constants = getattr(hub, "constants", None)
        if constants is not None:
            constants.HF_HUB_OFFLINE = True
        logger.error(
            "huggingface_hub %s takes no HTTP client from PersonalClaw, so it cannot be held to "
            "the network settings: its downloads are switched off",
            getattr(hub, "__version__", "?"),
        )
        return
    set_client(lambda: _with(default_client(), _before))
    set_async_client(lambda: _with(default_async_client(), _before_async))


class _GuardOnImport(importlib.abc.MetaPathFinder):
    """Finds nothing itself: it lets the library be found as usual, and guards it once loaded."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname != HUB:
            return None
        for finder in sys.meta_path:
            find = getattr(finder, "find_spec", None)
            if finder is self or find is None:
                continue
            spec = find(fullname, path, target)
            if spec is not None:
                break
        else:
            return None
        loader = spec.loader
        exec_module = getattr(loader, "exec_module", None)
        if exec_module is None:  # pragma: no cover - every loader the library ships with has one
            return spec

        def exec_and_guard(module: Any) -> None:
            exec_module(module)
            guard_the_hub(module)

        loader.exec_module = exec_and_guard  # type: ignore[union-attr]
        return spec


def install() -> None:
    """Hold the library to the egress guard in this process: now when it is already imported,
    else as it is first imported. Idempotent."""
    with _lock:
        if HUB in sys.modules:
            guard_the_hub(sys.modules[HUB])
            return
        if not any(isinstance(finder, _GuardOnImport) for finder in sys.meta_path):
            sys.meta_path.insert(0, _GuardOnImport())


__all__ = ["HUB", "guard_the_hub", "install"]
