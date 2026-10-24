"""``python -m tools.ci select`` reads this tree as it is (#3945 item 9).

``tools/ci/select.py`` selects the suite's test files a change can break from an import graph it
parses for itself. The rules, each failed by a planted edit below:

1. Every module the graph holds parses: one that cannot selects the whole suite on every change.
2. Every dynamic import outside ``tests/rails`` is listed in ``tools/ci/select-dynamic.toml``
   with one kind, a reason, and a target that exists: a ``prefix`` names a module, a package or
   a folder of modules here; a ``named`` site's module names, in a literal, a module or a file
   the graph holds. No entry names a site that is gone. An unlisted site selects the whole
   suite on every change.
3. The ``security`` marker reads the same here as in ``tests/security_census.py``, for every
   suite module: a marked module is never selected out.
4. Each whole-tree reader is a suite module, and ``make check`` runs ``select --run`` after the
   change tier.
5. Recall (#3946): ``make selection-map`` runs the suite under coverage with per-test contexts and
   writes, in the clone's git common folder, which test files ran each source file
   (``tools/ci/selection_map.py``). Where this clone has one, made at a commit this checkout
   holds, every test file it maps to a source file must be selected by a change to that file
   alone (:func:`tools.ci.select.recall_misses`): the graph's misses, measured. Where it has
   none, the check on the real map skips and says why; the planted map runs everywhere, and is
   exact.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest
from rails_repo import REPO
from security_census import carries_marker

from tools.ci import select, shards

#: The graph over the whole tree is parsed once for the module, by whichever of its tests runs
#: first (``_graph``); another process waits for that build and reads its cache. Listed in
#: tests/timeout-marks.toml with its reason.
_GRAPH_CAP = pytest.mark.timeout(120)


@lru_cache(maxsize=1)
def _graph() -> select.Graph:
    return select.load_graph(REPO, False)


def _listed() -> list[select.Listed]:
    return select.read_listed(REPO)


def _targets_named(graph: select.Graph, path: str) -> bool:
    fact = graph.facts[path]
    if any(m in graph.by_name for m in fact.modules):
        return True
    for literal in fact.paths:
        text = select._normal(literal)
        if text and (text in graph.nodes or any(n.startswith(text + "/") for n in graph.nodes)):
            return True
    return any(name in graph.by_name for name, _top in fact.imports)


def _list_problems(graph: select.Graph, listed: list[select.Listed]) -> list[str]:
    bad = [f"{s.file}: {s.call}: {s.problem()}" for s in listed if s.problem()]
    bad += [
        f"{file}: {call} is a dynamic import {select.DYNAMIC_FILE} does not list"
        for file, call in select.unlisted(graph, listed)
    ]
    found: dict[tuple[str, str], int] = {}
    for path, call, _head in graph.dynamic:
        found[(path, call)] = found.get((path, call), 0) + 1
    for site in listed:
        if found.get((site.file, site.call), 0) > 0:
            found[(site.file, site.call)] -= 1
        else:
            bad.append(f"{site.file}: {site.call} is listed and is no longer in the tree")
        if site.prefix and not any(
            name == site.prefix or name.startswith(site.prefix + ".") for name in graph.by_name
        ):
            bad.append(f"{site.file}: prefix {site.prefix} names no module here")
        if site.named and site.file in graph.facts and not _targets_named(graph, site.file):
            bad.append(f"{site.file}: named, but it names no module or file the graph holds")
    return bad


@_GRAPH_CAP
def test_every_module_the_graph_holds_parses() -> None:
    broken = {p: f.error for p, f in _graph().facts.items() if f.error}
    assert broken == {}


@_GRAPH_CAP
def test_every_dynamic_import_is_listed_with_a_target_that_exists() -> None:
    assert _list_problems(_graph(), _listed()) == []


def test_the_list_is_not_vacuous() -> None:
    kinds = {
        "prefix": [s for s in _listed() if s.prefix],
        "named": [s for s in _listed() if s.named],
        "outside": [s for s in _listed() if s.outside],
    }
    assert all(kinds.values()), {k: len(v) for k, v in kinds.items()}


@_GRAPH_CAP
def test_a_new_site_a_stale_entry_and_a_bad_target_each_fail() -> None:
    graph = _graph()
    listed = _listed()
    first = listed[0]
    assert any("does not list" in p for p in _list_problems(graph, listed[1:]))
    stale = select.Listed(first.file, "importlib.import_module(gone)", "", True, False, "gone")
    assert any("no longer in the tree" in p for p in _list_problems(graph, [*listed, stale]))
    wrong = select.Listed(first.file, first.call, "personalclaw.no_such_thing", False, False, "x")
    assert any("names no module here" in p for p in _list_problems(graph, [wrong, *listed[1:]]))


@_GRAPH_CAP
def test_the_security_marker_reads_as_the_census_reads_it() -> None:
    differ = []
    for rel in shards.universe(REPO):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        if select._carries_security(tree) != carries_marker(tree):
            differ.append(rel)
    assert differ == []
    assert any(_graph().facts[r].security for r in shards.universe(REPO))


@pytest.mark.parametrize("reader", select.WHOLE_TREE_READERS)
def test_each_whole_tree_reader_is_a_suite_module(reader: str) -> None:
    assert reader in shards.universe(REPO)


def test_make_check_runs_the_selection_after_the_change_tier() -> None:
    text = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(r"^check:\n((?:\t.*\n)+)", text, re.MULTILINE)
    assert recipe is not None, "the Makefile has no check target"
    lines = [line.strip() for line in recipe.group(1).splitlines()]
    assert lines[0] == "$(PYTHON) -m tools.ci run change --working-tree"
    assert any("-m tools.ci select" in line and "--run" in line for line in lines[1:]), lines


# ── recall ────────────────────────────────────────────────────────────────────────────────────

#: Where ``make selection-map`` writes the map, under the git common folder.
MAP = "selection-map/selection-map.json"


def _common() -> Path | None:
    done = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


def _real_map() -> tuple[dict[str, list[str]] | None, str]:
    common = _common()
    if common is None:
        return None, "this checkout is not in a git clone"
    path = common / MAP
    if not path.is_file():
        return None, f"this clone has no selection map ({path}): `make selection-map` writes one"
    data = json.loads(path.read_text(encoding="utf-8"))
    commit = str(data.get("commit", ""))
    held = subprocess.run(
        ["git", "-C", str(REPO), "merge-base", "--is-ancestor", commit, "HEAD"],
        capture_output=True,
        check=False,
    )
    if held.returncode != 0:
        return None, f"the map was made at {commit[:12]}, which this checkout does not hold"
    return {str(k): [str(t) for t in v] for k, v in data.get("map", {}).items()}, ""


@_GRAPH_CAP
def test_the_graph_finds_every_test_the_coverage_map_ran_for_a_changed_file() -> None:
    coverage, why = _real_map()
    if coverage is None:
        pytest.skip(why)
    graph = select.load_graph(REPO, False)
    misses = select.recall_misses(graph, select.read_listed(REPO), shards.universe(REPO), coverage)
    assert misses == [], f"{len(misses)} test files the coverage map ran are not selected"


def test_on_a_planted_map_recall_is_exact() -> None:
    files = {
        "src/personalclaw/__init__.py": "",
        "src/personalclaw/a.py": "",
        "src/personalclaw/b.py": "import personalclaw.a\n",
        "tests/test_imports_b.py": "import personalclaw.b\n",
        "tests/test_reached_otherwise.py": "",
    }
    paths = sorted(files)
    facts = {
        p: select.parse(files[p], select.names_of(p, {"src/personalclaw": True})[0], False)
        for p in paths
    }
    graph = select.build(REPO, paths, facts, frozenset({"src", "tests"}))
    universe = ["tests/test_imports_b.py", "tests/test_reached_otherwise.py"]
    coverage = {"src/personalclaw/a.py": universe}
    assert select.recall_misses(graph, [], universe, coverage) == [
        ("src/personalclaw/a.py", "tests/test_reached_otherwise.py")
    ]
    assert select.recall_misses(graph, [], universe, {"src/personalclaw/a.py": universe[:1]}) == []
