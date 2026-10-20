"""The source-tree checks split from
``tests/test_app_update_no_poller.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import source_index
from app_update_no_poller_shared import SRC


def _external_callers(symbol: str) -> dict[str, str]:
    """Files under src/personalclaw that REFERENCE ``symbol`` without also DEFINING it.

    Matches the bare identifier rather than ``symbol(`` on purpose: the one production
    reacher passes it by name — ``asyncio.to_thread(surface_app_updates, state)`` — so a
    call-only regex would miss it. A file that defines the symbol is its home and is
    excluded; every remaining hit is an external reacher, which is precisely where an
    accidental poller would appear.
    """
    ref = re.compile(rf"\b{symbol}\b")
    define = re.compile(rf"\bdef\s+{symbol}\b")
    out: dict[str, str] = {}
    for p in source_index.package_files():
        text = source_index.text(p)
        if ref.search(text) and not define.search(text):
            out[p.relative_to(SRC).as_posix()] = text
    return out


def test_surface_app_updates_has_exactly_one_production_caller_the_read_path() -> None:
    callers = set(_external_callers("surface_app_updates"))
    assert callers == {"dashboard/handlers/apps.py"}, (
        "zero polling processes added: surface_app_updates must be reached only "
        "from the /api/apps read handler. A second caller is how a poller creeps in. "
        f"Found: {sorted(callers)}"
    )


def test_updates_available_stays_internal_to_catalog() -> None:
    # updates_available() is the pure computation. Only apps/catalog.py (which defines it,
    # and so is excluded above) may call it; any EXTERNAL caller could schedule it on a loop.
    callers = set(_external_callers("updates_available"))
    assert callers == set(), (
        "updates_available must stay internal to apps/catalog.py; an external caller "
        f"is how a background poller creeps in. Found: {sorted(callers)}"
    )
