"""The source-tree checks split from
``tests/test_nothing_waits_on_a_model_on_the_event_loop.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO

_PACKAGE = REPO / "src" / "personalclaw"


#: Operations whose work can include a round trip to a model: memory operations that embed a
#: question or a memory, or judge a lesson; putting a turn's message together; and the embedding
#: of a send's routing suggestion, a skill match and a re-index's readiness probe. Named by
#: method, whichever object it is called on, so a new caller is read without anyone adding it.
MODEL_BOUND = frozenset(
    {
        "semantic_context",
        "get_semantic_context",
        "recall_lessons",
        "recall_facts",
        "recall_with_provenance",
        "rank_episodic",
        "search_episodic",
        "get_episodic_context",
        "episodic_context",
        "active_recall",
        "rank_semantic",
        "rank_lessons",
        "get_context_preview",
        "embed_query",
        "set_semantic",
        "write_episodic",
        "write_lesson",
        "import_memory",
        "migrate_from_markdown",
        "reembed_stale",
        "count_to_reembed",
        "promote_episodic_patterns",
        "suggest_for_send",
        "surface_skills",
        "search_skills",
        "_resolve_embed",
        "assemble_context",
        "build_message",
    }
)


_HAND_OFF = frozenset({"to_thread", "run_in_executor"})


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _sites() -> tuple[list[str], int]:
    """``(calls on the event loop, calls handed off)`` across every ``async def`` in the package."""
    on_loop: list[str] = []
    handed_off = 0
    for path, tree in source_index.package_trees():
        # Each call is read once, from the outermost ``async def`` around it: ``ast.walk`` reaches
        # an ``async def`` before the ones nested in it, and a nested one's calls are its too.
        read: set[int] = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            off: set[int] = set()
            handed: set[str] = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and _name(node.func) in _HAND_OFF:
                    off.update(id(n) for n in ast.walk(node))
                    handed.update(_name(a) for a in node.args if _name(a))
                    if id(node) not in read:
                        # `asyncio.to_thread(svc.set_semantic, ...)`: the operation, handed off.
                        handed_off += sum(1 for a in node.args if _name(a) in MODEL_BOUND)
            for node in ast.walk(fn):
                if isinstance(node, ast.FunctionDef) and node.name in handed:
                    off.update(id(n) for n in ast.walk(node))
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call) or id(node) in read:
                    continue
                read.add(id(node))
                if _name(node.func) not in MODEL_BOUND:
                    continue
                if id(node) in off:
                    handed_off += 1
                else:
                    rel = path.relative_to(_PACKAGE).as_posix()
                    on_loop.append(f"{rel}:{node.lineno} {fn.name} .{_name(node.func)}()")
    return on_loop, handed_off


def test_nothing_on_the_event_loop_waits_on_a_model():
    """🔴 Red before: twenty calls in sixteen functions: the recall's three, a send's routing
    suggestion, the re-index's probe, and putting together the message of a chat turn, a webhook's
    turn, a heartbeat, a subagent and the two turns that read a subagent's result."""
    on_loop, handed_off = _sites()
    # Vacuity floor: a matcher that stopped matching would find nothing anywhere and pass.
    seen = len(on_loop) + handed_off
    assert seen >= 20, f"the census found only {seen} model-bound calls: is it reading?"
    listed = "\n".join(on_loop)
    assert not on_loop, f"work that can wait on a model runs on the event loop:\n{listed}"
