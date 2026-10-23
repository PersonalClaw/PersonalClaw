"""One row of a table test, reported as a subtest only when it does not pass.

``subtests.test()`` builds a report for every row and logs it through pytest's hooks, and under
xdist sends it to the controller too. Measured on this suite's tables: about 1.4 ms per row in one
process and about 3 ms per row at ``-n 2``, against a few microseconds to a millisecond of work
the row itself does. A table of 200 rows paid more for being reported than for being checked.

:func:`row` keeps what a table test needs from a subtest and drops the rest: a row that fails or
skips is reported as a subtest named by its input, with its own message, and the rows after it
still run, exactly as ``subtests.test()`` does; a row that passes reports nothing. So every failing
row still names itself (``SUBFAILED(name='council')``), and only the ``SUBPASSED`` lines that
``-rA`` printed for passing rows are gone.

Use it in place of ``subtests.test(...)``::

    for name in rows:
        with row(subtests, name=name):
            assert ..., f"{name}: ..."

docs/maintainers/testing.md says when a variation is a row.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest

#: What a row may raise and still be reported as that row: an assertion or any other error, a
#: ``pytest.skip`` (reported as the row's ``SUBSKIPPED``) and a ``pytest.fail``. ``pytest.exit``
#: and a keyboard interrupt end the run, as they would inside ``subtests.test()``.
_REPORTED = (Exception, pytest.skip.Exception, pytest.fail.Exception)


@contextmanager
def row(subtests: Any, **kwargs: Any) -> Iterator[None]:
    """Run one row; if it raises, report it as the subtest *kwargs* name and go on."""
    try:
        yield
    except _REPORTED as exc:
        with subtests.test(**kwargs):
            raise exc


class labelled:
    """*subtests*, every row reported through it named by *names* as well as its own input.

    A table that was a test of its own, folded into a row of a test with others, reports its
    rows under that test: ``labelled(subtests, check="what the old test was")`` keeps saying which
    table a failing row is in (``SUBFAILED(check='...', case='...')``)."""

    def __init__(self, subtests: Any, **names: Any) -> None:
        self._subtests = subtests
        self._names = names

    def test(self, **kwargs: Any) -> Any:
        return self._subtests.test(**self._names, **kwargs)


@asynccontextmanager
async def tasks_ended() -> AsyncIterator[None]:
    """End the tasks a row started and left running, as the event loop's teardown ends a test's.

    A row that was a test of its own, folded into a test with others, shares that test's event
    loop: a turn it started in the background would otherwise run on into the rows after it. The
    tasks still running when the row ends are cancelled and waited for, so none reaches the next
    row."""
    before = asyncio.all_tasks()
    try:
        yield
    finally:
        left = asyncio.all_tasks() - before
        for task in left:
            task.cancel()
        await asyncio.gather(*left, return_exceptions=True)
