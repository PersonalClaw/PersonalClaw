"""The typed turn stays outside the biggest import cycle.

``turns`` is what a chat's messages are read as, and the chat's readers move onto it, and what
answers a headless turn in it. A unit inside the cycle is tied to everything else in it, so each
new reader of it would be too. Each imports only units outside the cycle: the keys a row records
its source by are kept below the gateway (``turn_origin``) and re-exported where they were
(``turn_source``).

The structural baseline's cycle ratchet holds every unit outside the cycle from joining it; this
names these by why they must stay out, with the imports that would tie one in.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO

from scripts import generate_structural_baseline as gen

#: The units of the typed turn, each outside the cycle.
KEPT_OUT = ("turn_origin", "turns")


def tied_in(graph: gen.UnitGraph, units: tuple[str, ...]) -> list[str]:
    """Each of *units* inside the biggest cycle of *graph*, with the imports that tie it in."""
    members = set(gen.biggest_cycle(graph.every))
    return [f"{unit}{gen._ties(unit, members, graph)}" for unit in units if unit in members]


def test_the_typed_turn_is_outside_the_import_cycle() -> None:
    """🔴 Red before: ``turns`` was in the cycle, tied in by importing ``turn_source``."""
    _inspected, graph = gen._unit_graph()
    assert len(gen.biggest_cycle(graph.every)) > 100, "no cycle found: this would pass vacuously"
    assert set(KEPT_OUT) <= set(graph.every), f"a unit is gone: {set(KEPT_OUT) - set(graph.every)}"
    assert tied_in(graph, KEPT_OUT) == []


def test_a_typed_turn_unit_that_imports_into_the_cycle_is_named() -> None:
    """The check finds a unit the cycle pulls in, and names the imports that tie it there."""
    every = {"hub_a": {"hub_b"}, "hub_b": {"hub_a", "turns"}, "turns": {"hub_a"}, "leaf": set()}
    sites = {
        ("hub_a", "hub_b"): "src/personalclaw/hub_a.py",
        ("hub_b", "hub_a"): "src/personalclaw/hub_b.py",
        ("hub_b", "turns"): "src/personalclaw/hub_b.py",
        ("turns", "hub_a"): "src/personalclaw/turns.py",
    }
    graph = gen.UnitGraph(every, every, sites)
    assert tied_in(graph, ("turns", "leaf")) == [
        "turns (src/personalclaw/turns.py imports hub_a; src/personalclaw/hub_b.py imports turns)"
    ]


#: What the typed turn reads a row's source by, which imports nothing of core at all.
ORIGIN = REPO / "src" / "personalclaw" / "turn_origin.py"


def core_imports(tree: ast.Module, rel: str) -> list[str]:
    """Each import of ``personalclaw``, or a relative one, in *tree* (the module at *rel*)."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = ["." * node.level + (node.module or "")]
        else:
            continue
        found += [
            f"{rel}:{node.lineno} imports {name}"
            for name in names
            if name.startswith((".", "personalclaw"))
        ]
    return found


def test_what_a_row_records_its_source_by_imports_nothing_of_core() -> None:
    """``turn_origin`` holds the keys the typed turn reads a row's source by: it imports nothing
    of core, so nothing it reads can tie it, or the typed turn, into the cycle."""
    assert core_imports(source_index.tree(ORIGIN), source_index.rel(ORIGIN)) == []


def test_an_import_of_core_in_the_origin_is_named() -> None:
    """The check names each import of core, a relative one included, and passes the rest."""
    planted = ast.parse(
        "import json\nfrom personalclaw.config import loader\nfrom . import turns\n"
    )
    assert core_imports(planted, "turn_origin.py") == [
        "turn_origin.py:2 imports personalclaw.config",
        "turn_origin.py:3 imports .",
    ]
