"""Every door that starts a workflow run hands it the gateway's workflow supervisor.

``workflows.service.start_run`` makes the run, then launches it on the supervisor it is given; with
none, the run is made and never launched, and the start answers ``WF_NO_SUPERVISOR``. Two doors
called it with none: the A2A door, so every ``POST /a2a/tasks`` failed, and the Inbox's apply of a
workflow proposal, so every accept failed. Each call of the run-start seam in core passes
``supervisor=``, or hands on the keywords its own caller gave it (``**``), which that caller is
held to in turn; ``supervisor=None`` passes none. A call that may pass none is named in
:data:`EXEMPT` with its reason; there are none. A call through an import under another name is not
seen.
"""

from __future__ import annotations

import ast

import source_index

#: The functions that start a run (``workflows.service``).
SEAMS = frozenset({"start_run", "start_kind_run"})

#: The calls of the run-start seam that may pass no supervisor, by ``<file>::<function>``, each
#: with why its run needs none: none. A new call that passes none is refused unless it is named
#: here.
EXEMPT: dict[str, str] = {}


def _function_of(parents: dict[ast.AST, ast.AST], node: ast.AST) -> str:
    """The dotted name of the functions (and classes) *node* sits in, ``<module>`` for none."""
    names = []
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return ".".join(reversed(names)) or "<module>"


def _calls_without_a_supervisor() -> tuple[int, list[str]]:
    """How many calls of the seam core makes, and the sites of those that pass no supervisor."""
    calls, sites = 0, []
    for path, tree in source_index.package_trees():
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in SEAMS:
                continue
            calls += 1
            given = {
                keyword.arg
                for keyword in node.keywords
                if not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
            }
            if "supervisor" not in given and None not in given:
                sites.append(f"{source_index.rel(path)}::{_function_of(parents, node)}")
    return calls, sites


def test_every_production_call_of_the_run_start_seam_passes_a_supervisor() -> None:
    calls, sites = _calls_without_a_supervisor()

    assert calls >= 8, f"the scan found {calls} calls of the run-start seam: it is reading little"
    missing = sorted(set(sites) - set(EXEMPT))
    assert missing == [], f"a run start with no supervisor never launches: {missing}"
    stale = sorted(set(EXEMPT) - set(sites))
    assert stale == [], f"named in EXEMPT but no longer a call that passes none: {stale}"
