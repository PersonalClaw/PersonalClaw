"""The child half of provider availability — see :mod:`personalclaw.providers.availability`.

Runs as ``personalclaw availability-probe <app>…`` in its own process, never inside the
gateway. For every provider record of every named app it resolves the app's
``availability()`` hook exactly as the extension loader does
(:func:`personalclaw.providers.loader.load_availability`), and writes one JSON line per record
to stdout the moment that record's answer exists. Every hook runs on its own thread, so a slow
hook delays only its own line and the gateway keeps every answer that landed before it gave up.

stdout is the protocol channel and nothing else. App code that prints — a hook with a debug
``print``, a library announcing itself on import — goes to stderr, because ``sys.stdout`` is
repointed there before any app module is imported.

Read-only by construction: the apps are discovered into a PRIVATE registry with no type
handlers, so registering one enables nothing, and nothing here seeds, installs or writes.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from personalclaw.providers.registry import RegisteredProvider

from personalclaw.providers.availability import (
    AVAILABLE,
    UNAVAILABLE,
    UNAVAILABLE_WITHOUT_REASON,
    UNKNOWN,
)


def _records(names: list[str]) -> tuple[list["RegisteredProvider"], list[str]]:
    """Every provider record of the named apps, plus the names that are not installed."""
    from personalclaw.apps import app_python
    from personalclaw.providers.loader import (
        discover_bundled_extensions,
        discover_installed_extensions,
    )
    from personalclaw.providers.registry import ProviderRegistry

    # The installed apps' Python packages (``<home>/app-python``) join the import path exactly as
    # they do in the gateway (``register_extension_providers``) — before any app module is
    # imported. Without it a hook asking "is my package installed?" answers for a process that
    # cannot see where the installer put it, and every such app reads as not installed.
    app_python.activate()
    registry = ProviderRegistry()  # no type handlers: registering enables nothing
    for manifest in discover_bundled_extensions():
        registry.register(manifest)
    for manifest, _enabled in discover_installed_extensions():
        registry.register(manifest)
    found: list["RegisteredProvider"] = []
    missing: list[str] = []
    for name in names:
        primary = registry.get(name)
        if primary is None:
            missing.append(name)
        else:
            found.extend(primary.chain())
    return found, missing


def _answer(ext: "RegisteredProvider") -> dict[str, Any]:
    """Run one record's hook (if it has one) and shape the protocol line."""
    from personalclaw.providers.loader import load_availability

    line: dict[str, Any] = {
        "name": ext.name,
        "implementation": ext.provider_config.implementation,
    }
    try:
        probe = load_availability(ext)
        if probe is None:
            return {**line, "state": AVAILABLE, "reason": ""}
        ok, reason = probe()
    except BaseException as exc:  # noqa: BLE001 — a hook may raise anything, SystemExit included
        return {**line, "state": UNKNOWN, "reason": _raised(exc)}
    if ok:
        return {**line, "state": AVAILABLE, "reason": str(reason or "")}
    return {**line, "state": UNAVAILABLE, "reason": str(reason or "") or UNAVAILABLE_WITHOUT_REASON}


def _raised(exc: BaseException) -> str:
    detail = str(exc).strip()
    what = type(exc).__name__
    return (
        f"Its availability check failed ({what}: {detail})."
        if detail
        else (f"Its availability check failed ({what}).")
    )


def _emit(channel: TextIO, line: dict[str, Any]) -> None:
    channel.write(json.dumps(line) + "\n")
    channel.flush()


def main(names: list[str]) -> int:
    """Answer for ``names`` on stdout, one JSON line per provider record. Exit 0."""
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    records, missing = _records(names)
    for name in missing:
        _emit(
            channel,
            {
                "name": name,
                "implementation": None,
                "state": UNKNOWN,
                "reason": "This app is not installed any more.",
            },
        )
    answers: queue.Queue[dict[str, Any]] = queue.Queue()
    for ext in records:
        threading.Thread(target=lambda e=ext: answers.put(_answer(e)), daemon=True).start()
    for _ in records:
        _emit(channel, answers.get())
    channel.close()
    return 0
