"""A piece of work is named through ``personalclaw.workref`` and read back through it, and its kinds
are the closed set of shared contracts §2.2, both ways.

- **One parser.** Nothing but ``workref.py`` splits a work's name at its colon: a ``.split(":")``,
  ``.rsplit(":")``, ``.partition(":")`` or ``.rpartition(":")`` on a value named ``work``,
  ``work_id`` or ``work_ref`` (as a name or an attribute) is a second parser, and the first one
  that disagrees with ``workref.parse`` (an id holds colons: ``chat:dashboard:chat-1``) reads a
  name as the wrong work.
- **The kinds, both ways.** Every ``work_ref(<kind>, …)`` in the package names a kind in
  ``WORK_KINDS``, and every kind in ``WORK_KINDS`` has a producer, a ``work_ref`` call naming it,
  or is in :data:`AWAITING_PRODUCER` with the issue whose change adds its producer. That change
  removes its row; a row whose kind has a producer by then fails here, so the table only shrinks.
- **The history row's writers.** ``agent_work_id`` and ``run_work_id``, which name what a trigger's
  fire started, each build their name through ``work_ref`` with their own kind
  (``tests/test_workref.py`` holds the strings they write to what they wrote before).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import source_index

from personalclaw import workref

PARSER = "src/personalclaw/workref.py"
WORK_NAMES = {"work", "work_id", "work_ref"}
SPLITS = {"split", "rsplit", "partition", "rpartition"}

#: The kinds of work no producer names yet, each with the issue that adds its producer.
AWAITING_PRODUCER = {
    "job": "#4508: a background command, owned by its chat, is the first work of this kind",
}


def _named(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return str(node.slice.value)
    return ""


def _second_parsers(files: Iterable[tuple[str, ast.Module]]) -> list[str]:
    found: list[str] = []
    for rel, tree in files:
        if rel == PARSER:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in SPLITS or _named(node.func.value) not in WORK_NAMES:
                continue
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == ":":
                found.append(
                    f"{rel}:{node.lineno}: {_named(node.func.value)}.{node.func.attr}(':')"
                )
    return found


def _produced_kinds(files: Iterable[tuple[str, ast.Module]]) -> dict[str, list[str]]:
    kinds: dict[str, list[str]] = {}
    for rel, tree in files:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _named(node.func) != "work_ref" or not node.args:
                continue
            first = node.args[0]
            kind = (
                first.value
                if isinstance(first, ast.Constant) and isinstance(first.value, str)
                else "<not a literal>"
            )
            kinds.setdefault(kind, []).append(f"{rel}:{node.lineno}")
    return kinds


#: The writers of a trigger history row's work id (``ActionResult.work_id``), with their kinds.
WRITERS = {
    ("src/personalclaw/subagent.py", "agent_work_id"): "subagent",
    ("src/personalclaw/workflows/models.py", "run_work_id"): "workflow",
}


def _writer_kinds(rel: str, tree: ast.Module, name: str) -> set[str]:
    """The kinds the function *name* of *tree* builds a name through ``work_ref`` with."""
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert found, f"{rel} has no {name}: move this rail's row with it"
    return set(_produced_kinds([(rel, ast.Module(body=found, type_ignores=[]))]))


def _package() -> list[tuple[str, ast.Module]]:
    trees = source_index.package_trees()
    assert len(trees) > 500, f"walked {len(trees)} modules — vacuous"
    return [(source_index.rel(Path(path)), tree) for path, tree in trees]


def test_nothing_but_workref_splits_a_works_name() -> None:
    assert _second_parsers(_package()) == []


def test_every_kind_produced_is_a_kind_and_every_kind_is_produced() -> None:
    produced = _produced_kinds(_package())
    assert produced, "found no work_ref call in the package — vacuous"
    strangers = {k: v for k, v in produced.items() if k not in workref.WORK_KINDS}
    assert strangers == {}, "a work's name is built for a kind there is not (or not a literal one)"
    unproduced = [k for k in workref.WORK_KINDS if k not in produced and k not in AWAITING_PRODUCER]
    assert (
        unproduced == []
    ), "these kinds have no producer: name the change that adds one in AWAITING_PRODUCER"
    landed = [k for k in AWAITING_PRODUCER if k in produced]
    assert landed == [], "these kinds have a producer now: remove their AWAITING_PRODUCER rows"
    assert set(AWAITING_PRODUCER) <= set(workref.WORK_KINDS), "an awaited kind that is not a kind"


def test_the_history_row_writers_build_their_name_through_work_ref() -> None:
    trees = dict(_package())
    for (rel, name), kind in WRITERS.items():
        assert _writer_kinds(rel, trees[rel], name) == {kind}, f"{name} builds its name by hand"


def test_each_check_sees_its_planted_violation() -> None:
    """The positive control: a second parser, a kind there is not and a kind built from a value
    are each found, beside a split of something that is not a work's name."""
    planted = ast.parse(
        "kind, _, rest = row['work_id'].partition(':')\n"
        "a, b = work.split(':', 1)\n"
        "c = self.work_ref.rpartition(':')\n"
        "d = session_key.split(':')\n"
        "e = work_ref('cron', x)\n"
        "f = workref.work_ref(kind, x)\n"
        "g = work_ref('chat', key)\n"
    )
    files = [("src/personalclaw/planted.py", planted)]
    lines = sorted(int(f.split(": ", 1)[0].rsplit(":", 1)[1]) for f in _second_parsers(files))
    assert lines == [1, 2, 3]
    assert _second_parsers([(PARSER, planted)]) == [], "the parser is the one place a name is split"
    assert set(_produced_kinds(files)) == {"cron", "<not a literal>", "chat"}
    by_hand = ast.parse("def agent_work_id(agent_id):\n    return f'subagent:{agent_id}'\n")
    assert _writer_kinds("src/personalclaw/subagent.py", by_hand, "agent_work_id") == set()
