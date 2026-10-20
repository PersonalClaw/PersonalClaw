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

from collections.abc import Iterator
from contextlib import contextmanager
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
