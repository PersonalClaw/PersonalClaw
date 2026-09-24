"""Process-global per-session memory-restriction registry (channel-agnostic).

Two session modes restrict memory behavior regardless of which channel opened the
session:

- **temporary** — blank-slate thread: no prior context loaded (memory READS
  suppressed — ``blocks_reads``) and memory writes suppressed.
- **incognito** — ephemeral: memory WRITES suppressed, but reads are allowed (the
  session still sees already-injected memory context). Reads are NOT blocked.

These are generic session concepts (a Slack thread, a Web-UI session, or a future
channel can all be temporary/incognito), so the registry lives in core. The channel
that opens a restricted session marks its key here; core memory-gating code
(dashboard handlers, the chat runner) reads it — neither side imports the other.

Keys are bounded LRU dicts so a long-running gateway can't grow them without bound.
"""

from __future__ import annotations

from collections import OrderedDict

_MAX = 10_000

_temporary: OrderedDict[str, None] = OrderedDict()
_incognito: OrderedDict[str, None] = OrderedDict()


def _add(store: OrderedDict[str, None], key: str) -> None:
    store[key] = None
    store.move_to_end(key)
    if len(store) > _MAX:
        store.popitem(last=False)


def mark_temporary(session_key: str) -> None:
    """Mark a session as temporary (blank-slate; memory writes suppressed)."""
    _add(_temporary, session_key)


def mark_incognito(session_key: str) -> None:
    """Mark a session as incognito (memory WRITES suppressed; reads allowed)."""
    _add(_incognito, session_key)


def is_temporary(session_key: str) -> bool:
    return session_key in _temporary


def is_incognito(session_key: str) -> bool:
    return session_key in _incognito


def is_restricted(session_key: str) -> bool:
    """True if the session should skip memory writes (temporary OR incognito)."""
    return session_key in _temporary or session_key in _incognito


def clear(session_key: str) -> None:
    """Drop all restriction flags for a session key (e.g. on session close)."""
    _temporary.pop(session_key, None)
    _incognito.pop(session_key, None)
