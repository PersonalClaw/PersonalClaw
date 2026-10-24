"""The capability fingerprint's probe list holds for this tree (``tools/ci/capabilities.py``).

The list is read from ``tests/`` by AST, so a new ``shutil.which`` probe joins it by itself. These
rows hold what a scan alone cannot: that every reference to ``shutil.which`` in the suite, and
every candidate a test hands the container runtime, names a tool the scan reads, or sits in a
site a listed constant covers; and that the constants the fingerprint reads are the suite's own.
A probe added in a shape the scan cannot read, ``shutil.which`` taken as a value among them,
fails the first row, by file and function. Every file is read with ``ast``, never imported: the
browser and container helpers are the suite's, and importing them would make this module more
than a rail. That the browser lookup finds what ``tests/browse_chrome.py`` finds is held in
``tests/test_ci_capabilities.py``.
"""

from __future__ import annotations

import ast

import pytest
from rails_repo import REPO

from tools.ci import capabilities as cap


def _constants(rel: str) -> dict[str, object]:
    """The module-level literals of ``REPO/rel``, by name."""
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                # A constant that is no literal (a call, a name, an expression) is not one the
                # fingerprint reads, so it is left out rather than failing the row.
                try:
                    out[target.id] = ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    value = node.value
                    if isinstance(value, ast.Call) and len(value.args) == 1:
                        try:
                            out[target.id] = frozenset(ast.literal_eval(value.args[0]))
                        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                            pass
    return out


def test_every_probe_of_the_hosts_path_is_named_or_covered() -> None:
    found = cap.scan(REPO)
    assert found.unresolved == set(cap.COVERED_SITES), (
        "a shutil.which probe whose tool the fingerprint cannot name: "
        f"{sorted(found.unresolved - set(cap.COVERED_SITES))}; name the tool with a literal, a "
        "loop over a constant of literals, or a helper called with literals, or add the site "
        "and its constant to capabilities.COVERED_SITES"
    )


def test_the_probe_list_holds_the_suites_own_probes() -> None:
    probes = set(cap.probe_list(REPO))
    programs = _constants(cap.CONTAINER_MODULE)[cap.CONTAINER_PROGRAMS]
    assert isinstance(programs, frozenset) and programs
    assert programs <= probes, "the container runtime's programs"
    assert {"dash", "shellcheck"} <= probes, "the installer's _tool_or_skip probes"
    assert {"git", "tmux", "node", "npm", "uv"} <= probes


def test_each_switch_is_one_the_suite_reads() -> None:
    named: set[str] = set()
    for path in (REPO / "tests").rglob("*.py"):
        values = _constants(path.relative_to(REPO).as_posix()).values()
        named |= {value for value in values if isinstance(value, str)}
    missing = [s for s in cap.SWITCHES if s not in named]
    assert not missing, f"a switch no module of the suite names: {missing}"
    assert _constants(cap.CHROME_MODULE)["CHROME_ENV"] == cap.CHROME_SWITCH
    assert _constants(cap.CONTAINER_MODULE)["OPT_IN"] in cap.SWITCHES


def test_the_chrome_lookup_is_the_browse_proofs_own() -> None:
    lookup = cap.ChromeLookup.read(REPO)
    own = _constants(cap.CHROME_MODULE)
    assert lookup.prefixes == own["_REVISION_DIR_PREFIXES"]
    assert lookup.globs == own["_BINARY_GLOBS"]


def test_the_covered_modules_are_where_the_fingerprint_reads_them() -> None:
    for rel in {cap.CONTAINER_MODULE, cap.CHROME_MODULE, *(r for r, _ in cap.COVERED_SITES)}:
        assert (REPO / rel).is_file(), f"{rel} is gone; the fingerprint reads it"
    for rel, constant in cap.COVERED_SITES.items():
        assert constant in _constants(rel[0]), f"{rel[0]} holds no {constant}"


@pytest.mark.timeout(70)
def test_the_prefilter_and_a_full_parse_find_the_same_probes() -> None:
    """The fingerprint parses only the files that name a word of ``capabilities.PREFILTER``; a
    full parse of every file under ``tests/`` must find exactly the same probes and the same
    unnamed sites, so the two readings cannot drift apart."""
    fast = cap.scan(REPO)
    full = cap.scan(REPO, prefilter=False)
    assert fast.probes == full.probes, sorted(fast.probes ^ full.probes)
    assert fast.unresolved == full.unresolved, sorted(fast.unresolved ^ full.unresolved)
