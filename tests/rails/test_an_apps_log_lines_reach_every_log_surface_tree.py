"""The source-tree checks split from
``tests/test_an_apps_log_lines_reach_every_log_surface.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO

# Imported before the test runs: the probe imports the SDK at install.
import personalclaw.sdk.tool  # noqa: F401

#: The one module that attaches a sink, and the one process that is not the gateway and
#: keeps its own console (the availability probe's child, which must not open gateway.log).
_ATTACHES_ITS_OWN = {"log_sinks.py", "providers/availability_probe.py"}


def test_every_sink_the_gateway_attaches_goes_through_the_one_rule() -> None:
    """A handler attached to one logger name (``personalclaw``, an app's) is how the gap
    began: whatever logs under another name never reaches it. So a handler is attached in
    ``log_sinks`` only, which gives every sink the same records."""
    import ast

    src = REPO / "src" / "personalclaw"
    calls: dict[str, list[int]] = {}
    for path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"addHandler", "basicConfig"}
            ):
                calls.setdefault(path.relative_to(src).as_posix(), []).append(node.lineno)
    assert "log_sinks.py" in calls, "the rail no longer sees log_sinks' own attach"
    stray = {rel: lines for rel, lines in calls.items() if rel not in _ATTACHES_ITS_OWN}
    assert not stray, f"a log handler attached outside log_sinks.attach: {stray}"
