"""A table row that leaves the conftest's fenced registries as it found them.

``tests/conftest.py`` puts each fenced registry back (the provider registry, the tool providers,
the workflow definitions, the notification kinds and the rest :func:`state_leak_guard.fenced_state`
reads) once per test, not once per row. Rows of one table share that test, so a row that changed
a fenced registry would hand the change to the row after it, which could then pass on what it was
left. :func:`fences_unchanged` reads them around one row and fails that row if any moved::

    for route in ROUTES:
        with row(subtests, route=route), fences_unchanged():
            ...

``row`` goes first, so a change is reported as the row that made it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from state_leak_guard import fenced_state


@contextmanager
def fences_unchanged() -> Iterator[None]:
    """Fail when the block leaves any fenced registry other than it found it."""
    before = fenced_state()
    yield
    after = fenced_state()
    changed = {
        name: sorted(before[name] ^ after.get(name, frozenset()))
        for name in before
        if after.get(name) != before[name]
    }
    assert not changed, f"the row changed registries a test's fences put back: {changed}"
