"""A site baseline fails by name, shrinks only, and merges as a set of lines.

Each test builds a repository of its own in ``tmp_path``, whose ``.gitattributes`` holds the line
``site_baseline.SITES_ATTRIBUTE`` (``tests/rails/test_site_baseline_tree.py`` holds the real
``.gitattributes`` to that line), and whose merge driver is the one a clone of this repository
has: ``scripts/install_git_hooks.sh``, the script ``npm run hooks:install`` runs, configures it in
the planted repository, and the script it names is copied in. So the merges below run as a clone's
do. A planted "site" is a file under ``src/``, and the baseline lists one line per file. Git runs
with the suite's neutral environment (``tests/git_helper_guard.py``), never the machine's own.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import git_helper_guard
import pytest
import site_baseline
from site_baseline import (
    DRIVER,
    REGISTRY,
    SITES_ATTRIBUTE,
    check,
    check_registry,
    code_site,
    load,
    numbered,
    parse,
    scoped,
)
from table_rows import row

from personalclaw.sdk.testing import neutral_git_env

BASELINE = "tests/rails/baselines/planted.sites.jsonl"
#: A second baseline, registered on the line after the planted one.
SECOND = "tests/rails/baselines/second.sites.jsonl"
RAIL = "tests/rails/test_planted.py"
#: The sites the base commit has, sorted. ``c`` and ``m`` are neighbours in the file.
BASE_SITES = ("a", "c", "m", "x", "z")
TODAY = "2026-10-19"
#: The file GitHub names for a step's job summary.
JOB_SUMMARY = "GITHUB_STEP_SUMMARY"
#: What ``npm run hooks:install`` runs in a clone: it configures the merge driver.
INSTALLER = site_baseline.REPO / "scripts" / "install_git_hooks.sh"


def _env() -> dict[str, str]:
    return os.environ | neutral_git_env(git_helper_guard.own_global_config())


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=_env(), check=True
    )
    return proc.stdout.strip()


def _configure_as_a_clone(repo: Path) -> None:
    """Run the hook installer in *repo*, as a clone runs it, and copy in each file of this
    repository the merge driver it configures runs. ``CI`` is dropped from its environment,
    since the installer configures nothing on a CI runner."""
    (repo / ".githooks").mkdir()
    env = {name: value for name, value in _env().items() if name != "CI"}
    subprocess.run(["sh", str(INSTALLER)], cwd=repo, env=env, check=True, capture_output=True)
    driver = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", f"merge.{DRIVER}.driver"],
        capture_output=True,
        text=True,
        env=_env(),
    )
    for word in driver.stdout.split():
        if (site_baseline.REPO / word).is_file():
            (repo / word).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(site_baseline.REPO / word, repo / word)


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


@pytest.fixture(autouse=True)
def _no_job_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """On GitHub every step has a job summary, and these planted lines are not this run's."""
    monkeypatch.delenv(JOB_SUMMARY, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Sites a, c, m, x and z, listed in a registered baseline, committed on ``main`` in a
    repository configured as a clone of this one (:func:`_configure_as_a_clone`)."""
    path = tmp_path / "repo"
    (path / "src").mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / ".gitattributes").write_text(SITES_ATTRIBUTE + "\n", encoding="utf-8")
    _configure_as_a_clone(path)
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


def _merge(repo: Path, first: str, second: str) -> subprocess.CompletedProcess[str]:
    """The three-way merge git makes to rebase ``second`` onto ``first``, written as a tree: on
    stdout the tree first, and on stderr whatever the merge driver said."""
    return subprocess.run(
        ["git", "-C", str(repo), "merge-tree", "--write-tree", first, second],
        capture_output=True,
        text=True,
        env=_env(),
    )


def _land(repo: Path, first: str, second: str) -> tuple[str, str] | None:
    """``first`` lands on main, then ``second`` is rebased onto it: the merge (:func:`_merge`)
    as a commit with ``first`` as its parent, checked out. ``None`` on a conflict."""
    proc = _merge(repo, first, second)
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
        assert landed is not None, "the two baselines conflicted: the sites-set driver did not run"
        base, _ = landed
        assert _found(repo) == ["a", "m", "n", "o", "x"]
        added = check(_found(repo), BASELINE, base, root=repo, prune=False)
        assert [line.site for line in added] == ["o" if first == one else "n"]
        check_registry(_baselines(repo), base, root=repo, prune=False)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "rm", "-q", ".gitattributes")
    _commit(repo, "no driver")
    one = _branch(repo, "one-plain", fix="c", add="n", added=TODAY)
    two = _branch(repo, "two-plain", fix="z", add="o", added=TODAY)
    assert _land(repo, one, two) is None, "without the driver the same two branches conflict"


def _ceilings(repo: Path) -> list[tuple[str, object]]:
    return [(line.site, line.fields.get("ceiling")) for line in load(repo / REGISTRY)]


def test_a_branch_from_before_a_ceiling_change_brings_back_no_stale_row(repo: Path):
    """A branch from before main lowered a ceiling registers a baseline on the next line. A union
    merge kept both sides of that hunk, so the old ceiling's row came back beside the new one with
    no conflict. Merged as sets of lines, the old row is one main deleted, and it stays deleted;
    a clone without the driver's configuration stops on it as a conflict."""
    _git(repo, "checkout", "-q", "-b", "lower", "main")
    _fix(repo, "z")
    _lines(repo, *(_site(site) for site in ("a", "c", "m", "x")))
    _write(repo / REGISTRY, _registration(ceiling=4))
    lowered = _commit(repo, "lower")
    _git(repo, "checkout", "-q", "-b", "second", "main")
    _write(repo / SECOND, _site("s"))
    _write(repo / REGISTRY, _registration(), _registration(SECOND, ceiling=1, added=TODAY))
    second = _commit(repo, "second")
    landed = _land(repo, lowered, second)
    assert landed is not None, "the rebase stopped: the sites-set driver did not merge it"
    assert _ceilings(repo) == [(BASELINE, 4), (SECOND, 1)], "the old ceiling's row came back"
    check_registry(_baselines(repo), lowered, root=repo, prune=False)
    _git(repo, "config", "--remove-section", f"merge.{DRIVER}")
    assert _land(repo, lowered, second) is None, "without the driver git merged it as text, cleanly"


def _both_change_a_ceiling(repo: Path) -> tuple[str, str]:
    _git(repo, "checkout", "-q", "-b", "four", "main")
    _write(repo / REGISTRY, _registration(ceiling=4))
    four = _commit(repo, "four")
    _git(repo, "checkout", "-q", "-b", "three", "main")
    _write(repo / REGISTRY, _registration(ceiling=3))
    return four, _commit(repo, "three")


def _both_add_a_site(repo: Path) -> tuple[str, str]:
    one = _branch(repo, "one", fix="c", add="n", added=TODAY)
    _git(repo, "checkout", "-q", "-b", "two", "main")
    _plant(repo, "n")
    _lines(
        repo,
        *(_site(site) for site in ("a", "c", "m")),
        {"site": "n", "reason": "admitted twice", "added": TODAY},
        *(_site(site) for site in ("x", "z")),
    )
    return one, _commit(repo, "two")


#: Two branches that each give one site a line of their own: the file, the site, and the two.
TWICE = [
    ("both change one ceiling", REGISTRY, BASELINE, _both_change_a_ceiling),
    ("both add one site", BASELINE, "n", _both_add_a_site),
]


def test_two_branches_that_each_change_one_sites_line_stop_as_a_conflict(repo: Path, subtests):
    """Two lines for one site are for a person to choose between, so the merge stops, the file
    holds both lines, and the driver names the file and the site."""
    assert len(TWICE) == 2, "a case was dropped from the table"
    for name, path, site, branches in TWICE:
        with row(subtests, case=name):
            proc = _merge(repo, *branches(repo))
            assert proc.returncode == 1, f"the merge did not stop: {proc.stdout}{proc.stderr}"
            assert f"{path} lists the site {site} twice" in proc.stderr, proc.stderr
            tree = proc.stdout.splitlines()[0]
            merged = parse(_git(repo, "show", f"{tree}:{path}") + "\n", path)[0]
            assert [line.site for line in merged].count(site) == 2, merged


def test_a_branch_from_before_a_fix_brings_back_no_deleted_line(repo: Path):
    """Two branches that fix neighbouring sites edit one hunk, and a union merge kept both sides
    of it, so each deleted line came back. Merged as sets of lines, each stays deleted. A line a
    merge resolved by hand brings back is named as stale, never as an addition, until pruned."""
    _git(repo, "checkout", "-q", "-b", "one", "main")
    _fix(repo, "c")
    _lines(repo, *(_site(s) for s in ("a", "m", "x", "z")))
    one = _commit(repo, "one")
    _git(repo, "checkout", "-q", "-b", "two", "main")
    _fix(repo, "m")
    _lines(repo, *(_site(s) for s in ("a", "c", "x", "z")))
    two = _commit(repo, "two")
    landed = _land(repo, one, two)
    assert landed is not None, "the rebase stopped: the sites-set driver did not merge it"
    base, _ = landed
    assert [line.site for line in load(repo / BASELINE)] == ["a", "x", "z"], "a line came back"
    check(_found(repo), BASELINE, base, root=repo, prune=False)
    _lines(repo, *(_site(s) for s in BASE_SITES))
    with pytest.raises(AssertionError) as failed:
        check(_found(repo), BASELINE, base, root=repo, prune=False)
    message = str(failed.value)
    assert "Site c is listed" in message and "Site m is listed" in message, message
    assert "was added" not in message, "a line brought back is stale, not an addition"
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


def test_every_line_a_branch_adds_reaches_the_job_summary_with_its_reason(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The review reads the job summary, not a passing test's captured output: each line the
    branch adds, to a baseline or to the registry, is written there with its date and reason.
    A check that adds nothing, or has no base to compare with, writes nothing."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv(JOB_SUMMARY, str(summary))
    base = _git(repo, "rev-parse", "main")
    check(list(BASE_SITES), BASELINE, base, root=repo, prune=False)
    _plant(repo, "p")
    _lines(repo, *(_site(site, **({"added": TODAY} if site == "p" else {})) for site in "acmpxz"))
    check(_found(repo), BASELINE, None, root=repo, prune=False)
    assert not summary.exists(), "a check that added nothing wrote to the job summary"
    check(_found(repo), BASELINE, base, root=repo, prune=False)
    _write(repo / NEW, _site("q"))
    _write(repo / REGISTRY, _registration(), _registration(NEW, ceiling=1, added=TODAY))
    check_registry(_baselines(repo), base, root=repo, prune=False)
    text = summary.read_text(encoding="utf-8")
    assert f"{BASELINE}: 1 line this branch adds (compared with the base {base[:12]})" in text
    assert f"added {TODAY}: p: p is planted" in text
    assert f"{REGISTRY}: 1 line this branch adds" in text
    assert f"added {TODAY}: {NEW}: the planted census" in text


def test_a_new_baseline_file_reaches_the_job_summary_with_each_of_its_sites(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A file the base lacks adds every line it has: the summary names the file against the
    ceiling its registry line gives, and lists each of its sites with its reason."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv(JOB_SUMMARY, str(summary))
    base = _git(repo, "rev-parse", "main")
    _write(repo / NEW, _site("q"), _site("r"))
    _write(repo / REGISTRY, _registration(), _registration(NEW, ceiling=2, added=TODAY))
    assert _the_new_baseline(repo, base) == []
    text = summary.read_text(encoding="utf-8")
    assert f"### {NEW}: a new baseline file this branch adds (compared with the base " in text
    assert f"{NEW}: a new baseline of 2 sites (it replaces PLANTED_CEILING, ceiling 2)" in text
    assert "q: q is planted\nr: r is planted\n" in text


def test_a_line_in_the_job_summary_stays_one_line_whatever_its_reason_holds(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A reason is free text: a line break in it is written escaped, so it cannot end the
    summary's fence and set text outside it."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv(JOB_SUMMARY, str(summary))
    base = _git(repo, "rev-parse", "main")
    _plant(repo, "p")
    reason = "first\n~~~\nafter the fence\r"
    _lines(
        repo,
        *(
            {"site": site, "reason": reason, "added": TODAY} if site == "p" else _site(site)
            for site in "acmpxz"
        ),
    )
    check(_found(repo), BASELINE, base, root=repo, prune=False)
    text = summary.read_text(encoding="utf-8")
    assert [line for line in text.splitlines() if line.startswith("~~~")] == ["~~~text", "~~~"]
    assert f"added {TODAY}: p: first\\n~~~\\nafter the fence\\r\n" in text


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
    assert argv[1:3] == ["-m", "pytest"] and argv[3].startswith("--basetemp="), argv
    assert argv[4:] == ["-q", RAIL, site_baseline.REGISTRY_RAIL]
