"""The source index walks each root once, parses each file once, and warms outside every timeout.

Each claim tests/source_index.py makes is held here, each with a control that breaks it:

- every walk has a vacuity floor, and a walk over a near-empty root fails it;
- no file is parsed twice in one process, and a planted second parse is refused where it happens;
- the warm-up runs before pytest-timeout starts timing the first rail, so a slow warm-up passes
  under a one-second limit, and the same warm-up moved inside the test fails it;
- collecting a rail that used to read the tree at import parses nothing from test code.
"""

from __future__ import annotations

import ast
import gc
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
import source_index
from rails_repo import REPO

TESTS = REPO / "tests"


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _near_empty_root(root: Path) -> Path:
    """One file under each of the four roots: a tree every floor must refuse."""
    _write(root, "src/personalclaw/__init__.py")
    _write(root, "web/src/main.ts")
    _write(root, "docs/index.md")
    _write(root, ".github/workflows/ci.yml")
    return root


@pytest.mark.parametrize("name", sorted(source_index.ROOTS))
def test_a_walk_that_finds_too_little_fails_its_floor(tmp_path, name):
    index = source_index.SourceIndex(_near_empty_root(tmp_path))
    with pytest.raises(source_index.VacuousWalk, match=f"the {name} walk found 1 files"):
        index.files(name)


@pytest.mark.parametrize("name", sorted(source_index.ROOTS))
def test_a_walk_at_its_floor_passes(tmp_path, name):
    """The control for the floors: the same near-empty root passes once a floor is 1."""
    index = source_index.SourceIndex(
        _near_empty_root(tmp_path), floors=dict.fromkeys(source_index.ROOTS, 1)
    )
    assert len(index.files(name)) == 1


def test_a_path_outside_every_root_is_refused(tmp_path):
    """A rail whose control plants its own tree must read that tree, not the real one: the index
    refuses to answer for a path under none of its roots, where it used to hand back the real
    walk and leave the rail to report a real file against the planted root."""
    planted = _write(tmp_path, "core/phone_home.py", "X = 1\n")
    assert not source_index.holds(planted)
    for asked in (source_index.text, source_index.tree, source_index.rel):
        with pytest.raises(source_index.NotIndexed, match="under none of the index's roots"):
            asked(planted)
    with pytest.raises(source_index.NotIndexed, match="under none of the index's roots"):
        source_index.under(source_index.package_files(), tmp_path / "core")


def test_a_path_under_a_root_is_answered():
    """The control: the same calls answer for a file and a folder the index holds."""
    path = source_index.package_files()[0]
    assert source_index.holds(path)
    assert source_index.text(path) is source_index.text(path)
    assert source_index.rel(path).startswith("src/personalclaw/")
    assert source_index.under(source_index.package_files(), path.parent)


def test_every_real_walk_clears_its_floor():
    for name in source_index.ROOTS:
        assert len(source_index.INDEX.files(name)) >= source_index.FLOORS[name], name


def test_the_package_walk_holds_only_python_modules():
    assert all(p.suffix == ".py" for p in source_index.package_files())


def test_no_file_was_parsed_twice_in_this_process():
    """By the time this runs, every rail this worker ran before it has asked for its trees."""
    source_index.tree(source_index.package_files()[0])
    twice = {source_index.rel(p): n for p, n in source_index.PARSES.items() if n > 1}
    assert twice == {}


def test_a_second_parse_of_the_same_file_is_refused(tmp_path):
    """The planted double parse: two indexes over one file, each with its own cache, which is
    exactly what two rails each keeping a private tree amounted to."""
    _near_empty_root(tmp_path)
    path = _write(tmp_path, "src/personalclaw/mod.py", "X = 1\n")
    floors = dict.fromkeys(source_index.ROOTS, 1)
    first = source_index.SourceIndex(tmp_path, floors)
    assert isinstance(first.tree(path), ast.Module)
    assert first.tree(path) is first.tree(path)
    with pytest.raises(source_index.DoubleParse, match="src/personalclaw/mod.py was parsed again"):
        source_index.SourceIndex(tmp_path, floors).tree(path)


def _generation(obj: object) -> int | None:
    """The collector generation *obj* is tracked in, or None when it is not tracked."""
    for generation in (0, 1, 2):
        if any(o is obj for o in gc.get_objects(generation=generation)):
            return generation
    return None


def test_a_built_index_holds_its_trees_in_the_oldest_generation(tmp_path):
    """Built, the trees sit where the collector seldom looks, so a later test's collections do not
    walk millions of nodes they cannot free."""
    _near_empty_root(tmp_path)
    index = source_index.SourceIndex(tmp_path, dict.fromkeys(source_index.ROOTS, 1))
    source_index._build(index)
    (tree,) = index._trees.values()
    assert _generation(tree) == 2


def test_an_index_built_without_the_promotion_leaves_its_trees_young(tmp_path):
    """The control: the same build without the collection leaves the tree where every young
    collection walks it, which is what the test above fails on if _build stops promoting."""
    _near_empty_root(tmp_path)
    index = source_index.SourceIndex(tmp_path, dict.fromkeys(source_index.ROOTS, 1))
    enabled = gc.isenabled()
    gc.disable()
    try:
        index.warm()
        (tree,) = index._trees.values()
        assert _generation(tree) == 0
    finally:
        if enabled:
            gc.enable()


def test_a_tree_carries_its_repository_path():
    path = source_index.package_files()[0]
    tree = source_index.tree(path)
    assert compile(tree, source_index.rel(path), "exec").co_filename == source_index.rel(path)


def test_a_module_that_imports_the_index_reads_it():
    import controller_sources as reads

    assert source_index.reads_the_index(reads)
    assert not source_index.reads_the_index(types.ModuleType("empty"))


# ── The warm-up runs outside the timeout ─────────────────────────────────────────────────────

#: A conftest that stands a slow warm-up in for the real one. With the plugin registered it runs
#: before the timer; without it the test warms the index itself, inside its own limit.
_SLOW_WARM_UP = """
import sys, time
sys.path.insert(0, {tests!r})
import source_index

def _slow():
    if "seconds" not in source_index.WARMED:
        time.sleep(2.5)
        source_index.WARMED["seconds"] = 2.5
    return source_index.WARMED["seconds"]

source_index.warm = _slow

def pytest_configure(config):
    if {registered!r}:
        config.pluginmanager.register(source_index.WarmUp(), "source-index-warm-up")
"""

_A_RAIL = """
import source_index

def test_a_rail():
    source_index.warm()
"""


def _run_with_one_second_limit(tmp_path: Path, registered: bool) -> subprocess.CompletedProcess:
    _write(tmp_path, "pytest.ini", "[pytest]\n")
    _write(tmp_path, "conftest.py", _SLOW_WARM_UP.format(tests=str(TESTS), registered=registered))
    _write(tmp_path, "test_rail.py", _A_RAIL)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_"))}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--timeout=1"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=100,
    )


def test_a_slow_warm_up_lands_outside_a_one_second_limit(tmp_path):
    done = _run_with_one_second_limit(tmp_path, registered=True)
    assert done.returncode == 0, (done.stdout + done.stderr)[-3000:]
    assert "1 passed" in done.stdout


def test_the_same_warm_up_inside_the_test_times_out(tmp_path):
    """The control: without the hook, the first rail pays the warm-up inside its own limit."""
    done = _run_with_one_second_limit(tmp_path, registered=False)
    assert done.returncode != 0
    assert "Timeout" in done.stdout + done.stderr


# ── Nothing in the test tree parses the tree at collection ───────────────────────────────────

#: Counts the parses made by code under tests/ until collection ends. pytest's assertion rewrite
#: parses each test module itself, and Hypothesis's plugin reads the constants of every loaded
#: module; neither is a rail, so only a calling frame in a test-tree file counts.
_COUNT_PARSES = """
import ast, sys, traceback

TESTS = {tests!r}
_real = ast.parse
SEEN = []
ON = [True]

def _counting(*args, **kwargs):
    if ON[0]:
        stack = traceback.extract_stack()[:-1]
        top = stack[-1].filename
        if "_pytest/assertion/rewrite" not in top and top.startswith(TESTS):
            SEEN.append(top)
    return _real(*args, **kwargs)

ast.parse = _counting

def pytest_collection_finish(session):
    ON[0] = False
    print("PARSED-BY-TESTS", len(SEEN), sorted(set(SEEN)))
"""


@pytest.mark.timeout(300)
def test_collecting_the_copy_rail_parses_nothing_from_test_code(tmp_path):
    """It used to build its table at import and parse the whole package to name its ids."""
    _write(tmp_path, "count_parses.py", _COUNT_PARSES.format(tests=str(TESTS)))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), env.get("PYTHONPATH", "")])
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-qq", "--no-cov", "-n0"]
        + [
            "-p",
            "count_parses",
            "-p",
            "no:cacheprovider",
            "tests/test_cli_commands_in_copy_parse.py",
        ],
        cwd=TESTS.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=280,
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-3000:]
    line = next(x for x in done.stdout.splitlines() if x.startswith("PARSED-BY-TESTS"))
    parsed_by = line.split(" ", 2)[2]
    assert "test_cli_commands_in_copy_parse.py" not in parsed_by
    assert "source_index.py" not in parsed_by, line
