"""``python-suite``'s declared inputs name every path the suite reads (#3945).

**What the list decides.** ``python-suite`` is a security job, so every plan holds it whatever its
inputs say (``security posture: never selected out`` in ``tools/ci/plan.py``): no input list
makes a change skip the suite. What the inputs decide is which paths are known. A changed path no
input but the whole tree names plans the whole tier; a known one plans the jobs it reaches, so a
plan of known paths can leave out ``apps-contract`` and, on the paths it excludes, ``e2e``. The
list says what the suite reads, and this rail holds it to that, so the list stays true for the day
the inputs, not the security floor, decide whether the suite runs.

**What the suite reads.** Two census halves:

- *The whole-tree reader.* ``tests/test_publication_hygiene_baseline.py`` runs the
  publication-hygiene check (``scripts/check_publication_hygiene.py``) on the real tree. It lists
  every tracked path with ``tracked_files`` (``git ls-files``), its path rules read each name, and
  its size, home-path and office-document rules open each tracked file. The census lists the tree
  with the same ``tracked_files``, so the two cannot drift, and a row holds that the suite still
  runs that reader on the real tree. While it does, the suite reads every tracked top-level entry.
- *The paths a suite module names.* Every string literal in a suite module (``tests/`` outside
  ``tests/rails``, helpers included) whose first part is a tracked top-level entry, joined or not.
  A module it cannot parse fails the rail. This half is what the census keeps once the whole-tree
  reader moves to ``tests/rails``.

Each path either half finds must be reached by ``python-suite``'s inputs, but for
:data:`LEFT_OUT`, which the list leaves out by choice. So a tracked path no input reaches fails the
rail however a suite test reads it: an open, a glob, an import, a subprocess run from the root.
The planted controls run the same check on a list with one entry dropped, and on a tree with a
top-level entry added.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path

import pytest
from rails_repo import REPO

from scripts import check_publication_hygiene as hygiene
from tools.ci import catalogue
from tools.ci import plan as planner
from tools.ci import select, shards

_CAT = catalogue.read(REPO)
_INPUTS = _CAT.job("python-suite").inputs

#: The suite module that runs the whole-tree reader, and its tests that run it on the real tree:
#: each calls the named function of the reader with no root, so it reads the repository.
(_READER,) = select.WHOLE_TREE_READERS
_READER_TESTS = {
    "test_the_tracked_tree_carries_no_unfit_path": "violations",
    "test_the_home_path_rule_reads_the_tree_it_claims_to": "tracked_files",
}

#: Tracked paths the suite reads (the whole-tree reader opens both) that the list leaves out on
#: purpose: a planning choice, not a statement about what the suite reads. The flake is what the
#: Nix workflow (.github/workflows/nix.yml) builds and checks, a workflow with its own paths: list
#: and no catalogue job, so a change to it alone is named by no job's input and plans the whole
#: tier, python-suite with it. A row holds that these are the only tracked entries outside the
#: list and that each still plans the whole tier.
LEFT_OUT = frozenset({"flake.lock", "flake.nix"})


@lru_cache(maxsize=1)
def swept() -> tuple[str, ...]:
    """Every tracked path, listed as the whole-tree reader lists them."""
    return tuple(sorted(hygiene.tracked_files(REPO)))


def _tops(paths: Iterable[str]) -> frozenset[str]:
    return frozenset(p.split("/")[0] for p in paths)


def _suite_modules(root: Path) -> list[Path]:
    """Every Python file under ``root``'s ``tests/`` but ``tests/rails`` (``shards.RAILS``),
    judged by its path inside ``root``, so a folder above the checkout never counts."""
    rails = Path(shards.RAILS)
    return [
        p
        for p in sorted((root / "tests").rglob("*.py"))
        if not p.relative_to(root).is_relative_to(rails)
    ]


@lru_cache(maxsize=1)
def named() -> dict[str, tuple[str, ...]]:
    """Each repository path a suite module names, with the modules that name it."""
    return named_in(REPO, _tops(swept()))


def named_in(root: Path, tops: frozenset[str]) -> dict[str, tuple[str, ...]]:
    """Each path under one of ``tops`` that a suite module under ``root`` names in a string
    literal. A module it cannot parse fails: what the census cannot read, it cannot account for."""
    found: dict[str, set[str]] = {}
    for module in _suite_modules(root):
        rel = module.relative_to(root).as_posix()
        try:
            tree = ast.parse(module.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise AssertionError(
                f"{rel} cannot be parsed, so the census cannot read it: {exc}"
            ) from exc
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            text = node.value.strip().removeprefix("./")
            if not text or "\n" in text or len(text) > 300 or " " in text:
                continue
            if text.split("/")[0] in tops:
                found.setdefault(text.rstrip("/"), set()).add(rel)
    return {path: tuple(sorted(mods)) for path, mods in sorted(found.items())}


def covered(inputs: tuple[str, ...], path: str) -> bool:
    """Whether ``inputs`` reach ``path``. A path the census found may name a directory (a module
    that joins ``"docs"`` onto the root, say), and the suite may read any file under it, so a
    directory counts as covered only when an arbitrary file under it is reached."""
    return planner.reaches(inputs, path) or planner.reaches(inputs, path.rstrip("/") + "/x")


def uncovered(
    inputs: tuple[str, ...],
    tracked: Iterable[str] | None = None,
    names: Mapping[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Each path the census finds that ``inputs`` do not reach, with where it came from, but for
    :data:`LEFT_OUT`. ``tracked`` and ``names`` stand in for the two halves in a planted control."""
    tracked = swept() if tracked is None else tracked
    names = named() if names is None else names
    out = [
        f"{p} (tracked: the whole-tree reader opens it)"
        for p in tracked
        if p not in LEFT_OUT and not planner.reaches(inputs, p)
    ]
    for path, modules in names.items():
        if path not in LEFT_OUT and not covered(inputs, path):
            out.append(f"{path} (named by {', '.join(modules[:3])})")
    return out


@pytest.mark.timeout(40)
def test_the_census_is_not_vacuous() -> None:
    """The whole-tree half lists what the reader reads: every tracked path, the flake and the
    binaries included, not only the text files a licence filter would keep."""
    assert len(swept()) > 4000, len(swept())
    assert len(named()) > 200, len(named())
    for top in ("src", "tests", "web", "docs", "pyproject.toml", "flake.nix", "flake.lock"):
        assert any(p == top or p.startswith(top + "/") for p in swept()), top
    assert any(p.endswith(".png") for p in swept())


def test_the_suite_runs_the_whole_tree_reader_on_the_real_tree() -> None:
    """The whole-tree half models a reader the suite runs. When that reader leaves the suite
    (for tests/rails), this row fails: drop the half with it, and let each entry no suite module
    names leave the list."""
    assert _READER in shards.universe(REPO), f"{_READER} is no longer a suite module"
    tree = ast.parse((REPO / _READER).read_text(encoding="utf-8"))
    tests = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name, entry in _READER_TESTS.items():
        assert name in tests, f"{_READER}::{name} is gone"
        calls = [
            n
            for n in ast.walk(tests[name])
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == entry
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "hygiene"
        ]
        assert any(
            not c.args and not c.keywords for c in calls
        ), f"{name} no longer calls {entry}()"


def test_python_suites_inputs_cover_every_path_the_suite_reads() -> None:
    missing = uncovered(_INPUTS)
    assert not missing, (
        "python-suite's inputs (tools/ci/catalogue.toml) miss paths the suite reads, so the list"
        " no longer says what the suite reads; name each in it:\n  " + "\n  ".join(missing[:30])
    )


def test_python_suites_inputs_are_not_the_whole_tree() -> None:
    """A whole-tree input names no path, so a src/ diff would plan the whole tier."""
    assert not any(planner.whole_tree(spec) for spec in _INPUTS), _INPUTS


def _without(dropped: str) -> tuple[str, ...]:
    assert dropped in _INPUTS, dropped
    return tuple(spec for spec in _INPUTS if spec != dropped)


def _names(missing: list[str], dropped: str) -> bool:
    stem = dropped.rstrip("/")
    return any(m == stem or m.startswith(stem + "/") or m.startswith(stem + " ") for m in missing)


@pytest.mark.parametrize("dropped", ["docs/", "README.md", "src/", ".githooks/", "uv.lock"])
def test_an_input_list_missing_an_entry_the_suite_reads_fails(dropped: str) -> None:
    """The planted control: drop one entry and the rail names what it no longer covers."""
    assert _names(uncovered(_without(dropped)), dropped)


@pytest.mark.parametrize("dropped", ["docs/", "web/"])
def test_suite_modules_read_docs_and_web_by_name(dropped: str) -> None:
    """Besides the whole-tree reader, suite modules name paths under docs/ and web/, so each
    keeps its entry on the named half alone until those readers move to tests/rails."""
    assert _names(uncovered(_without(dropped), tracked=()), dropped)


@pytest.mark.timeout(40)
def test_a_tracked_top_level_entry_the_list_does_not_name_fails() -> None:
    """The planted control for a new top-level entry: the whole-tree reader opens it, however
    else a suite test reads it, so the rail fails until the list names it."""
    missing = uncovered(_INPUTS, tracked=(*swept(), "brand-new/x.py"), names={})
    assert missing == ["brand-new/x.py (tracked: the whole-tree reader opens it)"], missing


@pytest.mark.timeout(40)
def test_the_flake_is_left_out_on_purpose_and_plans_the_whole_tier() -> None:
    """The only tracked entries outside the list are LEFT_OUT, and each is there by choice: the
    suite reads it, and a change to it alone is named by no job's input, so it plans the whole
    tier, python-suite with it. Adding to LEFT_OUT, or taking from it, is a reviewed edit of this
    row."""
    outside = {t for t in _tops(swept()) if not covered(_INPUTS, t)}
    assert outside == LEFT_OUT, sorted(outside)
    areas = catalogue.read_areas(REPO, _CAT)
    for path in sorted(LEFT_OUT):
        assert path in swept(), f"{path} is no longer tracked"
        p = planner.plan(_CAT, "merge", [path], overrides=areas, root=REPO)
        assert p.full and "python-suite" in p.jobs, (path, p.full_reasons)
        assert f"{path} is named by no input but the whole tree" in p.full_reasons, p.full_reasons


def test_a_checkout_under_a_folder_named_rails_still_finds_the_suite(tmp_path: Path) -> None:
    root = tmp_path / "rails" / "tree"
    (root / "tests" / "rails").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text("X = 'docs/a.md'\n", encoding="utf-8")
    (root / "tests" / "rails" / "test_r.py").write_text("X = 'web/a.ts'\n", encoding="utf-8")
    assert _suite_modules(root) == [root / "tests" / "test_a.py"]
    assert named_in(root, frozenset({"docs", "web"})) == {"docs/a.md": ("tests/test_a.py",)}


def test_a_suite_module_the_census_cannot_parse_fails_it(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_broken.py").write_text("def (:\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="tests/test_broken.py cannot be parsed"):
        named_in(tmp_path, frozenset({"docs"}))
