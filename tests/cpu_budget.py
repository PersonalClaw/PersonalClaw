"""A test's CPU budget, read as the work's own cost and not a tracer's (#4769).

A budget test times a call that once ran in time quadratic in its input, and holds it to a second
of CPU time. The CPU time is the right clock, since a loaded machine stretches the wall clock
without adding work. But a tracer adds work: breadth's coverage leg runs the suite under coverage's
tracer, which runs each line of the package several times slower. There the credential screen's
256 KB check took 1.2 to 1.4 s, against about 0.3 s untraced, and the push rule's 1 MB line took
3.7 s.

So :func:`seconds` times the call with this thread's trace function set aside. The call is made
once before that, traced, so coverage still measures the code, and so the timed call finds what a
running gateway already has (a compiled regex, a filled cache). A quadratic search still takes
minutes untraced, so the budget still catches it.

A tracer on ``sys.monitoring`` (coverage's ``sysmon`` core, a debugger on 3.12+) cannot be set
aside one call at a time. It fails the test loudly, naming the tool, rather than timing the tracer.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable


def _monitoring_tools() -> list[str]:
    monitoring = getattr(sys, "monitoring", None)
    if monitoring is None:
        return []
    return [name for tool in range(6) if (name := monitoring.get_tool(tool))]


def seconds(fn: Callable[[], object]) -> float:
    """The CPU time of a second call of *fn*, with this thread's trace function set aside."""
    fn()
    tools = _monitoring_tools()
    if tools:
        raise AssertionError(
            f"sys.monitoring tools trace this process ({', '.join(tools)}), and a budget cannot "
            "set them aside: time this work without them"
        )
    tracer = sys.gettrace()
    sys.settrace(None)
    try:
        started = time.process_time()
        fn()
        return time.process_time() - started
    finally:
        sys.settrace(tracer)
