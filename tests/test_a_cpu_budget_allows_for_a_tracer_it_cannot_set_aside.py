"""A CPU budget allows for a tracer it cannot set aside (``tests/cpu_budget.py``, #5629).

From 3.12 a thread's trace function is set aside only for its own frames: while any thread carries
one, the interpreter's code stays instrumented. Coverage gives every thread started under it one,
so on breadth's coverage leg the credential screen's one-second budget read up to 1.16 s with its
own thread's tracer set aside. :func:`cpu_budget.limit` reads a bound as it is where nothing traces,
and as :data:`cpu_budget.TRACED` times it while a tracer is set, on this thread or for new ones.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator

import cpu_budget
import pytest

#: The smallest regression a budget read through ``cpu_budget.limit`` guards: the repeated-read
#: command at 40 KB took 12.9 s before its fix, a lower bound for its 256 KB budget.
_SMALLEST_LIMITED_REGRESSION = 12.9


def _tracer(frame, event, arg):
    return None


@pytest.fixture
def untraced() -> Iterator[None]:
    """No tracer on this thread or for new ones while the test runs; the run's own put back after
    (breadth's coverage leg sets both)."""
    own, new = sys.gettrace(), threading.gettrace()
    sys.settrace(None)
    threading.settrace(None)
    try:
        yield
    finally:
        threading.settrace(new)
        sys.settrace(own)


def test_a_bound_is_itself_where_nothing_traces(untraced):
    """Where nothing traces, a budget's bound is the one it was written with."""
    assert not cpu_budget.traced()
    assert cpu_budget.limit(1.0) == 1.0
    assert cpu_budget.limit(5.0) == 5.0


def test_a_tracer_on_this_thread_allows_for_it(untraced):
    sys.settrace(_tracer)
    try:
        assert cpu_budget.traced()
        assert cpu_budget.limit(1.0) == cpu_budget.TRACED
    finally:
        sys.settrace(None)


def test_a_tracer_set_only_for_new_threads_allows_for_it(untraced):
    """🔴 Red before: there was no limit, and every bound read as written, traced or not. The case
    the set-aside cannot undo: coverage sets a tracer for every thread started from here on, and
    the timed thread's own is set aside."""
    threading.settrace(_tracer)
    try:
        assert sys.gettrace() is None, "premise: this thread's own tracer is set aside"
        assert cpu_budget.traced()
        assert cpu_budget.limit(1.0) == cpu_budget.TRACED
    finally:
        threading.settrace(None)


def test_the_allowance_stays_below_the_regressions_it_is_read_against():
    """The budgets read through the limit exist for searches that ran in time quadratic in their
    input. Allowed for a tracer, a one-second budget must still fail the smallest of those
    regressions, with room to spare."""
    assert cpu_budget.TRACED * 1.0 * 1.25 <= _SMALLEST_LIMITED_REGRESSION
