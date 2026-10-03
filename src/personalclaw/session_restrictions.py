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

Such a session's words also reach no model but the one it runs on, so the registry keeps that
model for each such session's key too (:func:`mark_own_model`): the model its turn named, or the
one handed to a subagent with its mode when it was started. Work for the session that runs away
from its turn's own context (a request its agent's tool makes, a subagent) reads it from here.

Keys are bounded LRU dicts so a long-running gateway can't grow them without bound.
"""

from __future__ import annotations

from collections import OrderedDict

_MAX = 10_000

_temporary: OrderedDict[str, None] = OrderedDict()
_incognito: OrderedDict[str, None] = OrderedDict()
_own_models: OrderedDict[str, str] = OrderedDict()


def _put(store: OrderedDict, key: str, value: object = None) -> None:
    store[key] = value
    store.move_to_end(key)
    if len(store) > _MAX:
        store.popitem(last=False)


def mark_temporary(session_key: str) -> None:
    """Mark a session as temporary (blank-slate; memory writes suppressed)."""
    _put(_temporary, session_key)


def mark_incognito(session_key: str) -> None:
    """Mark a session as incognito (memory WRITES suppressed; reads allowed)."""
    _put(_incognito, session_key)


def is_temporary(session_key: str) -> bool:
    return session_key in _temporary


def is_incognito(session_key: str) -> bool:
    return session_key in _incognito


def is_restricted(session_key: str) -> bool:
    """True if the session should skip memory writes (temporary OR incognito)."""
    return session_key in _temporary or session_key in _incognito


def mark_own_model(session_key: str, model_ref: str) -> None:
    """Record ``model_ref`` (``"<entry>:<model>"``, or an agent CLI's ``acp:<cli>``) as the one
    model the restricted session ``session_key``'s work may reach. ``""`` records that none is
    known, so its work reaches none."""
    _put(_own_models, session_key, (model_ref or "").strip())


def own_model(session_key: str) -> str:
    """The model recorded for ``session_key`` (:func:`mark_own_model`), ``""`` when none is."""
    return _own_models.get(session_key, "")


def clear(session_key: str) -> None:
    """Drop all restriction flags for a session key (e.g. on session close)."""
    _temporary.pop(session_key, None)
    _incognito.pop(session_key, None)
    _own_models.pop(session_key, None)
