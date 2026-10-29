"""Every id the gateway mints, minted here: ``<prefix>_`` and 26 Crockford base32 characters.

Shared contracts §2.1 gives every new id one shape, so two ids of one kind always compare and sort
alike, wherever they were made: a message's (``msg_``), a part's (``p_``), a store event's
(``e_``), a run's (``run_``) and every other prefix in :data:`PREFIXES`. The 26 characters are 128
bits, the top 48 the milliseconds since the epoch and the low 80 random, written in lowercase
Crockford base32 (no ``i``, ``l``, ``o`` or ``u``, so an id read aloud or copied by hand is not
misread). Two ids minted in the same millisecond in one process take the previous one's random part
plus one, so ids sort by when they were made.

:func:`stable_id` is the same shape for a row read before it had an id: its time from the row's own
instant, its 80 bits from a digest of a seed the row determines, so the row reads as the same id
before and after a conversion.

``tests/rails/test_ids_have_one_minter.py`` holds the package to this module: no other module
spells the alphabet, imports a ULID library or builds a string from a prefix and an underscore.
An id used in a path goes through ``record_ids``, as any other name does.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

#: Every prefix an id may have: shared contracts §2.1's table, and the mini apps' ``ma``. A prefix
#: new work allocates is added in that work's change, with its row in the table.
PREFIXES: frozenset[str] = frozenset(
    "msg p e cg rec inc note run dlg sia mv mvp boot blank ev "
    "ka wq job rule acc fill off srq addr bind snd shr wt rv fnd tw pv demo "
    "ma".split()
)

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_TIME_BITS = 48
_RANDOM_BITS = 80
_CHARS = 26
_BODY = "[0-9a-hjkmnp-tv-z]{26}"

_ONE_MS = timedelta(milliseconds=1)

_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _checked(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"{prefix!r} is not an id prefix: one of {', '.join(sorted(PREFIXES))}")
    return prefix


def _encode(ms: int, random: int) -> str:
    if not 0 <= ms < 1 << _TIME_BITS:
        raise ValueError(f"{ms} milliseconds is outside what an id's 48 bits of time hold")
    value = ms << _RANDOM_BITS | random
    return "".join(_ALPHABET[(value >> (5 * (_CHARS - 1 - i))) & 31] for i in range(_CHARS))


def new_id(prefix: str) -> str:
    """A new id of *prefix*'s kind, sorting after every id this process minted before it.
    Raises ``ValueError`` for a prefix not in :data:`PREFIXES`."""
    global _last_ms, _last_random
    _checked(prefix)
    with _lock:
        ms = max(_now_ms(), _last_ms)
        if ms == _last_ms:
            random = _last_random + 1
            if random >> _RANDOM_BITS:
                # Every random value of this millisecond is used: the next id takes the next one.
                ms, random = ms + 1, secrets.randbits(_RANDOM_BITS)
        else:
            random = secrets.randbits(_RANDOM_BITS)
        _last_ms, _last_random = ms, random
    return f"{prefix}_{_encode(ms, random)}"


def stable_id(prefix: str, at: str, seed: str) -> str:
    """The id a row read before it had one is given: its time part from the UTC instant *at*, its
    random part from SHA-256 of *seed*, so the same row reads as the same id every time.

    *at* carries its offset. A stamp written before stored times did goes through
    ``instants.as_instant`` first, which is its one reading; a zone-less *at* raises ``ValueError``,
    since it names no instant. So does a prefix not in :data:`PREFIXES`."""
    _checked(prefix)
    when = datetime.fromisoformat(at)
    if when.tzinfo is None:
        raise ValueError(f"{at!r} names no instant: a stable id's time carries its offset")
    ms = (when - datetime(1970, 1, 1, tzinfo=timezone.utc)) // _ONE_MS
    random = int.from_bytes(
        hashlib.sha256(seed.encode("utf-8")).digest()[: _RANDOM_BITS // 8], "big"
    )
    return f"{prefix}_{_encode(ms, random)}"


def is_id(prefix: str, value: str) -> bool:
    """Whether *value* is an id of *prefix*'s kind. Raises ``ValueError`` for a prefix not in
    :data:`PREFIXES`."""
    return re.fullmatch(f"{re.escape(_checked(prefix))}_{_BODY}", value) is not None
