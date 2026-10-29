"""The source-tree checks split from
``tests/test_every_model_call_writes_its_usage_row.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from every_model_call_writes_its_usage_row_shared import spent_unread, unrecorded_calls
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


#: Sites that consume a stream and rightly write no row of their own, and why.
EXEMPT = {
    ("llm_helpers.py", "stream_and_collect"): (
        "the collector itself: it hands the call's complete event to the caller's on_complete, "
        "which every collect is made to pass"
    ),
    ("dashboard/chat_utils.py", "stream_slash_command"): (
        "yields the chat turn's own events to the turn, which writes the turn's row"
    ),
    ("agents/native/loop_phases.py", "LoopPhases._answer"): (
        "the native loop's own inference: the turn's complete event carries its usage and its "
        "call ids to whoever writes the turn's row"
    ),
    ("sdk/provider_helpers.py", "BrandedCatalog._probe_completion"): (
        "a connection test that stops at the first event, before a call reports any usage"
    ),
}


def _census() -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """Core's sites by file, and the ``(file, qualname)`` of each that writes no row."""
    everything: dict[str, list[str]] = {}
    unwritten: list[tuple[str, str]] = []
    for path, tree in source_index.package_trees():
        rel = path.relative_to(SRC).as_posix()
        sites, missing = unrecorded_calls(tree)
        if sites:
            everything[rel] = sites
        unwritten.extend((rel, qualname) for qualname in missing)
    return everything, unwritten


def test_every_model_call_in_core_writes_its_usage_row():
    _sites, unwritten = _census()
    unexplained = [site for site in unwritten if site not in EXEMPT]
    assert not unexplained, (
        "these consume a model's stream and write no usage row, so Settings → Usage cannot show "
        "what they spend. Write one: `usage_ledger.recorder(provider, who)` called with the "
        "complete event, or `on_complete=` on the collector (`session.chore_usage`, "
        f"`chat_utils.chat_usage` name whose spend it is): {unexplained}"
    )


def test_every_exemption_still_names_a_site_that_writes_no_row():
    """An exemption that outlived its site would excuse the next one written there."""
    _sites, unwritten = _census()
    stale = sorted(set(EXEMPT) - set(unwritten))
    assert not stale, f"no longer a site without its row, drop the exemption: {stale}"


def test_the_rail_finds_the_sites_it_exists_for():
    """The floor: a scan that found nothing would pass for free."""
    sites, _unwritten = _census()
    found = {(rel, q) for rel, names in sites.items() for q in names}
    assert len(found) >= 20, found
    for known in (
        # The one-shot call every background chore is (``chores.run_chore``).
        ("llm_helpers.py", "_one_shot_completion._ask"),
        ("dashboard/handlers/optimizer.py", "handle_optimize._optimize"),
        ("loop/judge.py", "_stream"),
        ("eval/judge.py", "LLMJudge.judge_turn"),
        ("gateway.py", "GatewayOrchestrator._run_heartbeat_task"),
    ):
        assert known in found, (known, sorted(found))


#: Sites that rightly never read ``EVENT_SPENT``, and why.
SPENT_EXEMPT = {
    ("dashboard/chat_utils.py", "stream_slash_command"): (
        "yields the turn's events, this one among them, to the turn, which writes the turn's row"
    ),
    ("sdk/provider_helpers.py", "BrandedCatalog._probe_completion"): (
        "a connection test that writes no row at all"
    ),
    ("agents/native/loop_phases.py", "LoopPhases._answer"): (
        "one step's inference inside the native turn: the turn says its EVENT_SPENT itself, from "
        "NativeAgentRuntime.stream (LoopState.spent) when it ends in an error, to whoever writes "
        "the turn's row (tests/test_native_loop_phases.py, the given_up golden)"
    ),
}


def _spent_census() -> list[tuple[str, str]]:
    unread: list[tuple[str, str]] = []
    for path, tree in source_index.package_trees():
        rel = path.relative_to(SRC).as_posix()
        unread.extend((rel, qualname) for qualname in spent_unread(tree))
    return unread


def test_every_site_writes_its_row_for_a_turn_that_ends_in_an_error():
    unexplained = [site for site in _spent_census() if site not in SPENT_EXEMPT]
    assert not unexplained, (
        "these write a usage row from a turn's EVENT_COMPLETE and none from the EVENT_SPENT a "
        "turn ending in an error sends first, so the calls it made before the error are spend "
        f"Settings → Usage cannot show. Write the row from it too: {unexplained}"
    )


def test_every_spent_exemption_still_names_a_site_that_reads_none():
    stale = sorted(set(SPENT_EXEMPT) - set(_spent_census()))
    assert not stale, f"the site reads EVENT_SPENT now, drop the exemption: {stale}"


def test_the_native_turn_says_what_it_spent_when_it_ends_in_an_error():
    """The floor under the native loop's exemption above: its inference reads no ``EVENT_SPENT``
    because the turn says it, once, around every phase. ``NativeAgentRuntime.stream`` handles an
    error by yielding its record's ``spent`` event and raising again, and ``LoopState.spent``
    builds an ``EVENT_SPENT``."""
    import ast

    import source_index

    def _function(rel: str, qualname: str) -> ast.AST:
        tree = source_index.tree(SRC / rel)
        cls, _, name = qualname.partition(".")
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == cls:
                for fn in node.body:
                    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
                        return fn
        raise AssertionError(f"{rel} has no {qualname}")

    stream = _function("agents/native/runtime.py", "NativeAgentRuntime.stream")
    handlers = [
        h
        for h in ast.walk(stream)
        if isinstance(h, ast.ExceptHandler)
        and isinstance(h.type, ast.Name)
        and h.type.id == "Exception"
    ]
    said = [
        h
        for h in handlers
        if any(
            isinstance(node, ast.Yield)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "spent"
            for node in ast.walk(h)
        )
        and isinstance(h.body[-1], ast.Raise)
        and h.body[-1].exc is None
    ]
    assert said, "NativeAgentRuntime.stream no longer says what an erroring turn spent"
    spent = _function("agents/native/loop_state.py", "LoopState.spent")
    assert any(
        isinstance(node, ast.Name) and node.id == "EVENT_SPENT" for node in ast.walk(spent)
    ), "LoopState.spent no longer builds an EVENT_SPENT"
