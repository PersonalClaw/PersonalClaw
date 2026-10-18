"""The source-tree checks split from
``tests/test_action_provider_chokepoints.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import inspect

import source_index
from action_provider_chokepoints_shared import (
    EXECUTION_SITES,
    MANUAL_SEAM,
    REVERSAL_SITE,
    WORKFLOW_LOOKUPS,
    _enforce_action_calls,
    _reaches_the_registry,
    _source,
)

#: The functions that call the hand dispatch, each after it has established that you asked: `/run`
#: once `run_source.of_request` says the run is yours, your answer once `approval_answer` let it
#: through, and the restart review's decision once `approval_answer` let it through.
HAND_DISPATCH_CALLERS: dict[str, str] = {
    "_run_yours": "run_source.yours",
    "api_trigger_answer": "forbidden",
    "api_trigger_review": "forbidden",
}


def test_only_your_run_reaches_the_hand_dispatch():
    """🔴 The run-by-hand dispatch asks no denylist, because you answer what a run of yours runs.
    So it must be yours alone, and these are the properties that keep it so:

    * the dispatch is called from the three functions above and no other, each AFTER the check
      that says the asker is you;
    * `/run` reaches it only through `_run_yours`, chosen when `run_source.yours` says so, and a
      run anyone else asks for (`_run_asked`) goes through the gateway's fire dispatch
      (`run_admitted`), which asks `enforce_action`, and never through the hand dispatch;
    * the dispatch itself takes no asker: nothing can hand it someone else's run to judge.
    """
    import ast

    from personalclaw.dashboard.handlers import trigger_runs, triggers

    src = _source(MANUAL_SEAM)
    assert "manual_refusal" in src, "your own Run now is no longer gated"
    tree = ast.parse(src)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    dispatch = functions["_dispatch_store_action"]
    taken = {a.arg for a in dispatch.args.args + dispatch.args.kwonlyargs}
    assert "runs_for" not in taken and "source" not in taken, sorted(taken)

    def _calls(node: ast.AST, name: str) -> list[ast.Call]:
        found = []
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called == name:
                found.append(call)
        return found

    callers: dict[str, ast.AST] = {}
    for module in (trigger_runs, triggers):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _calls(
                node, "_dispatch_store_action"
            ):
                if node.name != "_dispatch_store_action":
                    callers[node.name] = node
    assert set(callers) == set(HAND_DISPATCH_CALLERS), sorted(callers)
    for name, check in HAND_DISPATCH_CALLERS.items():
        if check == "run_source.yours":
            continue
        node = callers[name]
        first_dispatch = min(c.lineno for c in _calls(node, "_dispatch_store_action"))
        guards = [c.lineno for c in _calls(node, check)]
        assert guards and min(guards) < first_dispatch, f"{name} dispatches before {check}"

    run_store = functions["_run_store"]
    assert _calls(run_store, "_run_yours") and _calls(run_store, "_run_asked")
    assert _calls(run_store, "yours"), "/run no longer asks whose run it is"
    asked = functions["_run_asked"]
    assert not _calls(asked, "_dispatch_store_action"), "a run nobody answers took the hand path"
    assert _calls(asked, "run_admitted") and _calls(asked, "admit_fire")
    gateway = _enforce_action_calls("personalclaw.gateway")
    assert gateway, "the fire dispatch a run nobody answers takes no longer asks the denylist"


def test_the_site_list_is_not_STALE():
    """🔴 The test that makes the list above trustworthy.

    A hardcoded list of call sites rots the moment someone adds one — and a rotted list reads as
    "all sites are checked" while silently covering fewer. So the list is verified against the tree:
    every module that reaches `get_action_provider`, by any name (`_reaches_the_registry`), must be
    either an execution site or a documented exemption.
    """
    import pathlib

    root = pathlib.Path(inspect.getfile(__import__("personalclaw"))).parent
    callers: set[str] = set()
    for path, tree in source_index.package_trees():
        if _reaches_the_registry(tree):
            rel = path.relative_to(root).with_suffix("")
            # A package's `__init__` is the package (`personalclaw.action_providers`).
            parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
            callers.add(".".join(("personalclaw", *parts)))

    known = {m for m, _ in EXECUTION_SITES} | {
        REVERSAL_SITE,
        "personalclaw.dashboard.handlers.hooks",
        # The would-execute preview — reads `supports_dry_run` only; the properties that
        # earn the exemption are asserted in `test_the_would_execute_preview_site_only_reads_the_
        # declaration` above, so this entry cannot become a silent bypass.
        "personalclaw.dashboard.handlers.doctor",
        # The create-time existence check (#779) — `triggers.tools.create` resolves a provider to
        # ask ONE question, "is this name registered?", and refuses the row before it exists when
        # the answer is no. The result is never bound and nothing executes; the properties that
        # earn the exemption are asserted in
        # `test_the_create_time_provider_check_only_asks_existence` below.
        "personalclaw.triggers.tools",
        # The delegating provider -- the one resolve in this set that lives INSIDE a
        # provider rather than at a fire path. Every other entry in `EXECUTION_SITES` is a seam
        # a trigger/hook/tile arrives at, and the gate belongs there: `selfqa-commit-watch` is
        # only reachable THROUGH the gateway file-trigger path, which already carries the
        # denylist and the kill switch, so a second gate here would fence an already-fenced
        # call. The properties that earn the exemption are asserted in
        # `test_the_delegating_provider_only_hands_off_to_a_frozen_name` below.
        "personalclaw.action_providers.selfqa_watch_provider",
        # The grant copy — `triggers.grants` resolves a provider to read ONE attribute,
        # `display_name`, for the words the owner reads when a grant is asked for or a run is
        # refused ("not allowed to use the “Bash Command” action"). It decides nothing and runs
        # nothing; the properties that earn the exemption are asserted in
        # `test_the_grant_copy_only_reads_the_display_name` below.
        "personalclaw.triggers.grants",
        # The rung router -- `guardrails.rungs.can_be_undone` resolves each provider of an
        # action type to read ONE declaration, `reversal_kinds`, so a rung that promises undo
        # is only ever given to an action every provider of which can be taken back. It runs
        # nothing and undoes nothing; the properties that earn the exemption are asserted in
        # `test_the_rung_router_only_reads_the_undo_declaration` below.
        "personalclaw.guardrails.rungs",
        # A workflow step's two read-only lookups: preflight asks whether a step's provider is
        # registered, and the binding pass whether its action is a model turn
        # (`hands_config_to_a_model`). Neither runs anything; the properties that earn the
        # exemption are asserted in `test_the_workflow_lookups_only_read_a_declaration` below.
        *WORKFLOW_LOOKUPS,
        "personalclaw.action_providers.registry",  # defines it
        "personalclaw.action_providers",  # re-exports it
    }
    unaccounted = callers - known
    assert not unaccounted, (
        "these modules reach an action provider but are not in EXECUTION_SITES: "
        f"{sorted(unaccounted)}. Add them (with a policy check) or document the exemption."
    )
