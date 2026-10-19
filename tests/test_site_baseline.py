"""A site baseline fails by name, shrinks only, and merges like code.

Each test builds a repository of its own in ``tmp_path``, whose ``.gitattributes`` holds the line
``site_baseline.UNION_ATTRIBUTE``, so the merges below run through the union driver the repository
declares (``tests/rails/test_site_baseline_tree.py`` holds the real ``.gitattributes`` to that
line). A planted "site" is a file under ``src/``, and the baseline lists one line per file. Git runs
with the suite's neutral environment (``tests/git_helper_guard.py``), never the machine's own.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

import git_helper_guard
import pytest
import site_baseline
from site_baseline import (
    REGISTRY,
    UNION_ATTRIBUTE,
    check,
    check_registry,
    code_site,
    load,
    numbered,
    scoped,
)
from table_rows import row

from personalclaw.sdk.testing import neutral_git_env

BASELINE = "tests/rails/baselines/planted.sites.jsonl"
RAIL = "tests/rails/test_planted.py"
#: The sites the base commit has, sorted. ``c`` and ``m`` are neighbours in the file.
BASE_SITES = ("a", "c", "m", "x", "z")
TODAY = "2026-10-19"


def _git(repo: Path, *args: str) -> str:
    env = os.environ | neutral_git_env(git_helper_guard.own_global_config())
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return proc.stdout.strip()


def _site(name: str, **extra: str) -> dict[str, str]:
    return {"site": name, "reason": f"{name} is planted", **extra}


def _registration(path: str = BASELINE, ceiling: int = 5, **extra: object) -> dict[str, object]:
    return {
        "site": path,
        "reason": "the planted census",
        "rail": RAIL,
        "replaces": "PLANTED_CEILING",
        "ceiling": ceiling,
        **extra,
    }


def _write(path: Path, *lines: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def _found(repo: Path) -> list[str]:
    """The planted census: one site per file under ``src/``."""
    return sorted(path.stem for path in (repo / "src").glob("*.site"))


def _baselines(repo: Path) -> list[Path]:
    return sorted(repo.rglob("*.sites.jsonl"))


def _plant(repo: Path, *names: str) -> None:
    for name in names:
        (repo / "src" / f"{name}.site").write_text(f"{name}\n", encoding="utf-8")


def _fix(repo: Path, *names: str) -> None:
    for name in names:
        (repo / "src" / f"{name}.site").unlink()


def _lines(repo: Path, *lines: dict[str, object]) -> None:
    _write(repo / BASELINE, *lines)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Sites a, c, m, x and z, listed in a registered baseline, committed on ``main``."""
    path = tmp_path / "repo"
    (path / "src").mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / ".gitattributes").write_text(UNION_ATTRIBUTE + "\n", encoding="utf-8")
    (path / RAIL).parent.mkdir(parents=True)
    (path / RAIL).write_text("", encoding="utf-8")
    _plant(path, *BASE_SITES)
    _lines(path, *(_site(name) for name in BASE_SITES))
    _write(path / REGISTRY, _registration())
    _commit(path, "base")
    return path


def _branch(repo: Path, name: str, fix: str, add: str, **line: str) -> str:
    """A branch off ``main`` that fixes one site and adds another, with its line."""
    _git(repo, "checkout", "-q", "-b", name, "main")
    _fix(repo, fix)
    _plant(repo, add)
    kept = [_site(site) for site in BASE_SITES if site != fix]
    kept.append(_site(add, **line))
    _lines(repo, *sorted(kept, key=lambda each: str(each["site"])))
    return _commit(repo, name)


def _land(repo: Path, first: str, second: str) -> tuple[str, str] | None:
    """``first`` lands on main, then ``second`` is rebased onto it: the three-way merge git
    makes for the rebase, as a commit with ``first`` as its parent. ``None`` on a conflict."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "merge-tree", "--write-tree", first, second],
        capture_output=True,
        text=True,
        env=os.environ | neutral_git_env(git_helper_guard.own_global_config()),
    )
    if proc.returncode == 1:
        return None
    assert proc.returncode == 0, proc.stderr
    tree = proc.stdout.splitlines()[0]
    landed = _git(repo, "commit-tree", tree, "-p", first, "-m", "rebased")
    _git(repo, "checkout", "-q", "--detach", landed)
    return first, landed


def test_two_branches_that_each_fix_a_site_and_add_one_merge_in_either_order(repo: Path):
    """The merge-order hole, closed: each branch fixes one site and adds another at the same
    place in the file, and whichever lands first, the other rebases cleanly and passes."""
    one = _branch(repo, "one", fix="c", add="n", added=TODAY)
    two = _branch(repo, "two", fix="z", add="o", added=TODAY)
    for first, second in ((one, two), (two, one)):
        landed = _land(repo, first, second)
        assert landed is not None, "the two baselines conflicted: the union driver did not run"
        base, _ = landed
        assert _found(repo) == ["a", "m", "n", "o", "x"]
        added = check(_found(repo), BASELINE, base, root=repo, prune=False)
        assert [line.site for line in added] == ["o" if first == one else "n"]
        check_registry(_baselines(repo), base, root=repo, prune=False)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "rm", "-q", ".gitattributes")
    _commit(repo, "no union driver")
    one = _branch(repo, "one-plain", fix="c", add="n", added=TODAY)
    two = _branch(repo, "two-plain", fix="z", add="o", added=TODAY)
    assert _land(repo, one, two) is None, "without the driver the same two branches conflict"


def test_a_union_merge_that_repeats_a_site_fails(repo: Path):
    one = _branch(repo, "one", fix="c", add="n", added=TODAY)
    _git(repo, "checkout", "-q", "-b", "two", "main")
    _plant(repo, "n")
    _lines(
        repo,
        *(_site(s) for s in ("a", "c", "m")),
        {"site": "n", "reason": "admitted twice", "added": TODAY},
        *(_site(s) for s in ("x", "z")),
    )
    two = _commit(repo, "two")
    landed = _land(repo, one, two)
    assert landed is not None
    with pytest.raises(AssertionError, match=r"Site n is listed 2 times"):
        check(_found(repo), BASELINE, landed[0], root=repo, prune=False)


def test_a_union_merge_that_brings_back_a_deleted_line_fails_until_pruned(repo: Path):
    """What the union driver cannot do: two branches that fix neighbouring sites edit one hunk,
    and the union keeps both sides of it, so each deleted line comes back. The check names
    them as stale, never passes them, and ``--prune`` deletes them."""
    _git(repo, "checkout", "-q", "-b", "one", "main")
    _fix(repo, "c")
    _lines(repo, *(_site(s) for s in ("a", "m", "x", "z")))
    one = _commit(repo, "one")
    _git(repo, "checkout", "-q", "-b", "two", "main")
    _fix(repo, "m")
    _lines(repo, *(_site(s) for s in ("a", "c", "x", "z")))
    two = _commit(repo, "two")
    landed = _land(repo, one, two)
    assert landed is not None
    base, _ = landed
    with pytest.raises(AssertionError) as failed:
        check(_found(repo), BASELINE, base, root=repo, prune=False)
    message = str(failed.value)
    assert "Site c is listed" in message and "Site m is listed" in message, message
    assert "was added" not in message, "a line the merge brought back is stale, not an addition"
    check(_found(repo), BASELINE, base, root=repo, prune=True)
    check(_found(repo), BASELINE, base, root=repo, prune=False)
    kept = [line.site for line in load(repo / BASELINE)]
    assert kept == ["a", "x", "z"]


def test_each_way_the_file_and_the_tree_disagree_fails_by_name(repo: Path, subtests):
    """A new site, a stale line and a repeated line each fail, naming the site; so does a rail
    that hands over one name for two sites."""
    cases = [
        ("a new site", ["a", "c", "m", "p", "x", "z"], BASE_SITES, "Site p is not listed"),
        ("a stale line", ["a", "c", "m", "x"], BASE_SITES, "Site z is listed in"),
        ("a repeated line", list(BASE_SITES), (*BASE_SITES, "m"), "Site m is listed 2 times"),
        ("a repeated name", [*BASE_SITES, "m"], BASE_SITES, "the rail found m 2 times"),
    ]
    assert len(cases) == 4, "a failure was dropped from the table"
    for name, found, listed, message in cases:
        with row(subtests, case=name):
            _lines(repo, *(_site(site) for site in listed))
            with pytest.raises(AssertionError, match=message):
                check(found, BASELINE, None, root=repo, prune=False)
    _lines(repo, *(_site(site) for site in BASE_SITES))
    check(list(BASE_SITES), BASELINE, None, root=repo, prune=False)


def test_prune_deletes_stale_and_repeated_lines_sorts_and_never_adds_one(repo: Path):
    unsorted = (repo / BASELINE).read_text().splitlines(keepends=True)
    unsorted = [unsorted[4], unsorted[0], unsorted[2], unsorted[2], unsorted[1], unsorted[3]]
    (repo / BASELINE).write_text("".join(unsorted), encoding="utf-8")
    found = ["a", "c", "m", "p", "x"]  # z is fixed, p is new
    with pytest.raises(AssertionError, match="Site p is not listed") as failed:
        check(found, BASELINE, None, root=repo, prune=True)
    assert "Site z" not in str(failed.value) and "2 times" not in str(failed.value)
    kept = (repo / BASELINE).read_text().splitlines()
    assert [json.loads(line)["site"] for line in kept] == ["a", "c", "m", "x"]
    assert kept == [
        json.dumps(_site(site)) for site in ("a", "c", "m", "x")
    ], "a line was rewritten"


def test_a_line_the_branch_adds_needs_added_and_a_reason(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Against the base, a branch cannot grow its own list: its new line passes only with
    ``added`` and a reason, and is printed for the job summary."""
    base = _git(repo, "rev-parse", "main")
    monkeypatch.setenv(site_baseline.BASE_VARIABLE, base)
    _git(repo, "checkout", "-q", "-b", "grows", "main")
    _plant(repo, "p")
    _lines(repo, *(_site(site) for site in sorted((*BASE_SITES, "p"))))
    with pytest.raises(AssertionError) as failed:
        check(_found(repo), BASELINE, root=repo, prune=False)
    assert (
        "Site p was added to the baseline in this branch; add `added` and a reason, or fix the "
        "site" in str(failed.value)
    )
    assert f"compared with the base {base[:12]}" in str(failed.value)
    _lines(repo, *(_site(site, **({"added": TODAY} if site == "p" else {})) for site in "acmpxz"))
    added = check(_found(repo), BASELINE, root=repo, prune=False)
    assert [line.site for line in added] == ["p"]
    assert f"added {TODAY}: p: p is planted" in capsys.readouterr().out


def test_a_check_never_claims_a_comparison_it_did_not_make(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], subtests
):
    monkeypatch.delenv(site_baseline.BASE_VARIABLE, raising=False)
    check(list(BASE_SITES), BASELINE, root=repo, prune=False)
    assert f"{BASELINE}: compared with the working tree (no base)" in capsys.readouterr().out
    missing = "0" * 40
    for name, call in (
        ("a baseline", lambda: check(list(BASE_SITES), BASELINE, missing, root=repo)),
        ("the registry", lambda: check_registry(_baselines(repo), missing, root=repo)),
    ):
        with row(subtests, checked=name):
            with pytest.raises(AssertionError, match=f"the base {missing} is not in this clone"):
                call()


NEW = "tests/rails/baselines/new.sites.jsonl"
RENAMED = "tests/rails/baselines/renamed.sites.jsonl"


def _renamed(repo: Path) -> None:
    _git(repo, "mv", BASELINE, RENAMED)
    _write(repo / REGISTRY, _registration(RENAMED))


def _renamed_unregistered(repo: Path) -> None:
    _git(repo, "mv", BASELINE, RENAMED)


def _recreated_with_an_extra_site(repo: Path) -> None:
    _plant(repo, "p")
    (repo / BASELINE).unlink()
    _lines(repo, *(_site(site) for site in sorted((*BASE_SITES, "p"))))
    _write(repo / REGISTRY, _registration(added=TODAY))


def _new_file_without_added(repo: Path) -> None:
    _write(repo / NEW, _site("q"))
    _write(repo / REGISTRY, _registration(), _registration(NEW))


def _new_file_over_its_ceiling(repo: Path) -> None:
    _write(repo / NEW, _site("q"), _site("r"))
    _write(repo / REGISTRY, _registration(), _registration(NEW, ceiling=1, added=TODAY))


def _ceiling_raised(repo: Path) -> None:
    _write(repo / REGISTRY, _registration(ceiling=9))


def _the_registry(repo: Path, base: str) -> object:
    return check_registry(_baselines(repo), base, root=repo, prune=False)


def _the_planted_baseline(repo: Path, base: str) -> object:
    return check(_found(repo), BASELINE, base, root=repo, prune=False)


def _the_new_baseline(repo: Path, base: str) -> object:
    found = [line.site for line in load(repo / NEW)]
    return check(found, NEW, base, root=repo, prune=False)


#: Each way a branch could grow a list through its file rather than its lines, the check that
#: holds it, and what that check says.
REGISTRY_CASES = [
    ("renamed", _renamed, _the_registry, f"{RENAMED} was added to the registry in this branch"),
    ("renamed, unregistered", _renamed_unregistered, _the_registry, f"{RENAMED} is not registered"),
    (
        "re-created with an extra site",
        _recreated_with_an_extra_site,
        _the_planted_baseline,
        "Site p was added",
    ),
    (
        "a registry line with no added",
        _new_file_without_added,
        _the_new_baseline,
        f"{NEW} is a new baseline file in this branch",
    ),
    (
        "a new file over its ceiling",
        _new_file_over_its_ceiling,
        _the_new_baseline,
        "2 sites, over the ceiling 1",
    ),
    ("a ceiling raised", _ceiling_raised, _the_registry, "raises its ceiling from 5 to 9"),
]


def test_a_branch_cannot_grow_a_list_by_renaming_or_recreating_its_file(repo: Path, subtests):
    assert len(REGISTRY_CASES) == 6, "a case was dropped from the table"
    base = _git(repo, "rev-parse", "main")
    for number, (name, plant, held_by, message) in enumerate(REGISTRY_CASES):
        with row(subtests, case=name):
            _git(repo, "checkout", "-q", "-f", "-b", f"case-{number}", "main")
            _git(repo, "clean", "-qfd")
            plant(repo)
            with pytest.raises(AssertionError, match=message):
                held_by(repo, base)


def test_a_new_baseline_file_registered_with_added_passes_and_is_named(
    repo: Path, capsys: pytest.CaptureFixture[str]
):
    base = _git(repo, "rev-parse", "main")
    _write(repo / NEW, _site("q"), _site("r"))
    _write(repo / REGISTRY, _registration(), _registration(NEW, ceiling=2, added=TODAY))
    assert _the_new_baseline(repo, base) == []
    added = check_registry(_baselines(repo), base, root=repo, prune=False)
    assert [line.site for line in added] == [NEW]
    out = capsys.readouterr().out
    assert f"{NEW}: a new baseline of 2 sites (it replaces PLANTED_CEILING, ceiling 2)" in out


#: Lines a baseline cannot hold, and what the check says about each.
MALFORMED = [
    ("not JSON", "{site: a}\n", ":1 is not JSON"),
    ("not an object", '["a"]\n', ":1 is not a JSON object"),
    ("no site", '{"reason": "r"}\n', ":1 names no site"),
    ("no reason", '{"site": "a", "reason": " "}\n', ":1: site a has no reason"),
    ("a malformed date", '{"site": "a", "reason": "r", "added": "19 Oct"}\n', "not a YYYY-MM-DD"),
    ("no final newline", '{"site": "a", "reason": "r"}', "does not end with a newline"),
]


def test_every_malformed_line_fails_naming_where_it_is(repo: Path, subtests):
    assert len(MALFORMED) == 6, "a case was dropped from the table"
    for name, text, message in MALFORMED:
        with row(subtests, case=name):
            (repo / BASELINE).write_text(text, encoding="utf-8")
            with pytest.raises(AssertionError, match=message):
                check(["a"], BASELINE, None, root=repo, prune=False)


def test_a_sites_key_holds_through_an_edit_above_it_and_repeats_are_numbered():
    before = "class H:\n    def get(self):\n        send({'error': x})\n"
    after = (
        "import os\n\n\nclass H:\n    def get(self):\n        log()\n        send({'error': x})\n"
    )
    twice = "def f():\n    send(a)\n    send( a )\n    send(b)\n"

    def keys(source: str) -> list[str]:
        calls = [
            code_site("m.py", scope, node)
            for node, scope in scoped(ast.parse(source))
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "send"
        ]
        return numbered(calls)

    assert keys(before) == keys(after) == ["m.py::H.get::send({'error': x})"]
    assert keys(twice) == ["m.py::f::send(a)", "m.py::f::send(a) #2", "m.py::f::send(b)"]


def test_prune_runs_every_registered_rail_in_prune_mode(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    ran: list[tuple[list[str], str | None]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        ran.append((argv, env.get(site_baseline.PRUNE_VARIABLE)))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(site_baseline.subprocess, "run", run)
    assert site_baseline.main(["--prune", "--root", str(repo)]) == 0
    [(argv, prune)] = ran
    assert prune == "1"
    assert argv[1:4] == ["-m", "pytest", "-q"]
    assert argv[4:] == [RAIL, site_baseline.REGISTRY_RAIL]
