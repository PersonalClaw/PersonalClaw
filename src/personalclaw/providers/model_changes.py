"""The models PersonalClaw can answer on changed: a model binding or a provider instance was
written.

The open pages show what follows from them (the header's model chip: whether a model is set up,
which surfaces wait on one), and used to learn of a change only from their own poll, up to 20 s
later. Every writer of a binding (``use_cases.save_active_models``) and of a provider instance
(``instances``) calls :func:`announce` once its write has landed; the dashboard subscribes when it
starts and tells every open page (a ``refresh`` frame naming ``models``).

In this process only: a write from another PersonalClaw process of the same home (the CLI) tells
nobody here, and the pages learn of it from their poll, as before.

A listener never breaks a write: the write has happened by the time it runs, so a listener's fault
is logged and the rest are still told.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: ``() -> None`` callbacks, told after a binding or a provider instance is written, whoever wrote.
_listeners: list[Callable[[], None]] = []


def subscribe(listener: Callable[[], None]) -> None:
    """Be told each time a binding or a provider instance is written (idempotent)."""
    if listener not in _listeners:
        _listeners.append(listener)


def unsubscribe(listener: Callable[[], None]) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


def announce() -> None:
    """Tell every listener that the models PersonalClaw can answer on may have changed."""
    for listener in list(_listeners):
        try:
            listener()
        except Exception:  # noqa: BLE001 — the write has happened; a listener cannot undo it
            logger.warning("a model-change listener failed", exc_info=True)
