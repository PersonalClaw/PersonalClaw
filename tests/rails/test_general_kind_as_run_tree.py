"""The source-tree checks split from
``tests/test_general_kind_as_run.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import general_kind_as_run_shared
import source_index
from rails_repo import REPO

_isolated = general_kind_as_run_shared._isolated


def test_the_run_path_has_no_convergence_consumer_yet() -> None:
    """What this change did NOT deliver, held so it cannot become a stale docstring.

    The template's convergence is RESOLVED into the policy (asserted above) but nothing on the run
    path READS `policy.convergence`: `loop/supervisor.py` is the ONE evaluator and its
    `done_signal` takes a `Loop`, reading `kind_config` and `workspace_dir` off it. Those are the
    `node_config` / `run_input` destinations the 33-column census says require the loop's phases to
    already be a graph — i.e. later work, not a gap to paper over here.

    Railed in BOTH directions, which is the point:

    * if a `workflows/` module starts reading `convergence` while this test still claims none
      does, it reds — the claim became a lie;
    * if `loop/supervisor.py` stops being the sole reader, it reds too — the evaluator moved and
      the port's recipe changed.
    """
    src = REPO / "src" / "personalclaw"
    readers: dict[str, list[int]] = {}
    for path in source_index.package_files():
        if path.name == "supervisor_policy.py":
            continue  # the declaration's own module
        # Parsed, not grepped. A substring scan for ".convergence" also matches the VALIDATOR's
        # error strings ("unknown supervisor.convergence field") — measured, it reported
        # `workflows/validator.py` as a reader — so the rail would fire on documentation and then
        # be relaxed to shut it up. An attribute ACCESS is the only thing that is actually a read.
        tree = source_index.tree(path)
        lines = sorted(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "convergence"
        )
        if lines:
            readers[str(path.relative_to(src))] = lines

    assert set(readers) == {"loop/supervisor.py"}, (
        "the set of modules reading `policy.convergence` changed. If a `workflows/` module now "
        "consumes it, the run path HAS a convergence consumer — update this test and the "
        "`convergence` field's docstring, which both still say it does not. Readers found: "
        f"{sorted(readers)}"
    )
