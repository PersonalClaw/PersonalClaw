"""One plan per run from the diff (tools/ci/plan.py), over a planted catalogue and overrides.

Every case reads a catalogue and an areas file written here, never the tree's:
tests/rails/test_ci_plan_tree.py holds the real catalogue's rows.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import catalogue
from tools.ci import plan as planner

_TEXT = """
[tier.merge]
jobs = [
  "rails", "python-suite", "python-suite-macos", "docs-check", "web", "build", "sdk", "lint",
  "audit",
]

[[job]]
id = "rails"
tiers = ["change", "merge"]
inputs = ["src/", "tests/"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
timeout_min = 10
command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]

[[job]]
id = "python-suite"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
timeout_min = 55
command = [{ argv = ["uv", "run", "pytest", "tests"] }]

[[job]]
id = "python-suite-macos"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
platform = "macos-26"
timeout_min = 50
command = [{ argv = ["uv", "run", "pytest", "tests"] }]

[[job]]
id = "docs-check"
tiers = ["change", "merge"]
inputs = ["docs/", ":(glob)*.md", ":(glob)web/**/*.md"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
timeout_min = 5
command = [
  { argv = ["sh", "scripts/check_docs.sh"] },
  { argv = ["python3", "tools/check_links.py"] },
]

[[job]]
id = "web"
tiers = ["merge"]
inputs = ["web/", ":(exclude,glob)web/**/*.md"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
timeout_min = 5
after = ["build"]
command = [{ argv = ["node", "e2e/check.mjs"], workdir = "web" }]

[[job]]
id = "build"
tiers = ["merge"]
inputs = ["package.json"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
timeout_min = 5
command = [{ argv = ["npm", "run", "build"] }]

[[job]]
id = "sdk"
tiers = ["merge"]
inputs = ["src/sdk/"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
timeout_min = 5
command = [{ argv = ["python3", "contract.py"], workdir = "scripts/sdk" }]

[[job]]
id = "lint"
tiers = ["merge"]
inputs = ["scripts/"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
timeout_min = 5
command = [{ argv = ["python3", "-m", "flake8", "scripts"] }]

[[job]]
id = "audit"
tiers = ["merge"]
inputs = ["pyproject.toml"]
external_inputs = ["advisory-feed"]
toolchain = []
cache = []
security = "ordinary"
live = true
timeout_min = 5
command = [{ argv = ["python3", "-m", "tools.ci", "audit", "run"] }]
"""

#: The planted catalogue's overrides, as tools/ci/areas.toml holds them.
_AREAS = """
[[override]]
paths = ["README.md"]
jobs = ["build"]
reason = "the sdist must carry README.md, so a README change rebuilds the package"

[[override]]
paths = ["ci.yml"]
whole_tier = true
reason = "the file that defines the jobs: editing it re-runs all of them"
"""

#: The jobs a merge plan always holds: the rails, both security suites, and the live audit.
_FLOOR = {"rails", "python-suite", "python-suite-macos", "audit"}
_MERGE = {
    "rails",
    "python-suite",
    "python-suite-macos",
    "docs-check",
    "web",
    "build",
    "sdk",
    "lint",
    "audit",
}


def _cat(text: str = _TEXT) -> catalogue.Catalogue:
    return catalogue.load(text)


def _plan(paths, tier: str = "merge", text: str = _TEXT, areas: str = _AREAS, **kw):
    cat = _cat(text)
    return planner.plan(cat, tier, paths, overrides=catalogue.load_areas(areas, cat), **kw)


# ── what a diff selects ──────────────────────────────────────────────────────────────────────


def test_a_docs_diff_selects_its_job_and_the_floor_only() -> None:
    p = _plan(["docs/guide.md"])
    assert not p.full
    assert set(p.jobs) == _FLOOR | {"docs-check"}
    assert any("docs/guide.md" in r for r in p.reasons["docs-check"])


def test_a_jobs_after_is_planned_with_it() -> None:
    p = _plan(["web/src/App.tsx"])
    assert {"web", "build"} <= set(p.jobs) and "sdk" not in p.jobs
    assert any("after" in r for r in p.reasons["build"])


def test_a_declared_input_schedules_its_job() -> None:
    assert "sdk" in _plan(["src/sdk/channel.py"]).jobs
    assert "sdk" not in _plan(["src/app.py"]).jobs


def test_a_path_a_step_names_schedules_that_job() -> None:
    p = _plan(["scripts/check_docs.sh"])
    assert "docs-check" in p.jobs and not p.full
    assert any("a step runs it" in r for r in p.reasons["docs-check"])


def test_a_steps_workdir_is_part_of_the_path_it_runs() -> None:
    """sdk's step runs contract.py from scripts/sdk; its inputs (src/sdk/) do not reach it."""
    p = _plan(["scripts/sdk/contract.py"])
    assert "sdk" in p.jobs and not p.full
    assert any("a step runs it" in r for r in p.reasons["sdk"])


def test_a_script_a_steps_script_imports_schedules_that_job(tmp_path) -> None:
    (tmp_path / "scripts" / "sdk").mkdir(parents=True)
    (tmp_path / "scripts" / "sdk" / "contract.py").write_text("import json\nimport snapshot\n")
    (tmp_path / "scripts" / "sdk" / "snapshot.py").write_text("SIGNATURES = {}\n")
    p = _plan(["scripts/sdk/snapshot.py"], root=tmp_path)
    assert "sdk" in p.jobs and not p.full
    assert any("a step runs it" in r for r in p.reasons["sdk"])
    assert "sdk" not in _plan(["scripts/sdk/snapshot.py"]).jobs, "no root, so no import is read"


def test_a_path_only_a_step_names_is_unknown_and_plans_the_whole_tier() -> None:
    """A step schedules its job, but only a declared input makes a path known: docs-check's
    step runs tools/check_links.py, which no input other than the whole tree names."""
    p = _plan(["tools/check_links.py"])
    assert p.full and set(p.jobs) == _MERGE
    assert any("a step runs it" in r for r in p.reasons["docs-check"])
    assert any("named by no input but the whole tree" in r for r in p.full_reasons)


def test_an_after_chain_is_planned_to_its_end() -> None:
    """a needs b needs c, and c comes first in the catalogue, so one pass over it stops at b."""
    cat = _cat()
    sdk = cat.job("sdk")
    chain = (
        dataclasses.replace(sdk, id="c", inputs=("c/",), after=()),
        dataclasses.replace(sdk, id="b", inputs=("b/",), after=("c",)),
        dataclasses.replace(sdk, id="a", inputs=("a/",), after=("b",)),
    )
    longer = dataclasses.replace(cat, jobs=(*cat.jobs, *chain))
    p = planner.plan(longer, "merge", ["a/x.py"], overrides=())
    assert not p.full and {"a", "b", "c"} <= set(p.jobs)


def test_the_area_map_is_the_catalogue_so_an_input_edit_moves_the_plan() -> None:
    """Nothing is kept beside the catalogue: narrowing docs-check's inputs is all it takes for
    a Markdown page under web/ to stop scheduling it."""
    assert "docs-check" in _plan(["web/notes.md"]).jobs
    narrowed = _TEXT.replace(
        'inputs = ["docs/", ":(glob)*.md", ":(glob)web/**/*.md"]', 'inputs = ["docs/"]', 1
    )
    assert narrowed != _TEXT
    p = _plan(["web/notes.md"], text=narrowed)
    assert not p.full and "docs-check" not in p.jobs and "web" not in p.jobs


def test_an_excluded_path_leaves_its_job_out() -> None:
    p = _plan(["web/docs/notes.md"])
    assert not p.full and "web" not in p.jobs and "docs-check" in p.jobs


def test_an_override_adds_its_jobs_with_its_reason() -> None:
    p = _plan(["README.md"])
    assert "build" in p.jobs and "docs-check" in p.jobs and not p.full
    assert any("the sdist must carry README.md" in r for r in p.reasons["build"])


def test_a_whole_tier_override_plans_the_whole_tier() -> None:
    p = _plan(["ci.yml"])
    assert p.full and set(p.jobs) == _MERGE
    assert any("defines the jobs" in r for r in p.full_reasons)


#: A path of each kind the planted catalogue knows: an input's, an override's, a step's, none's.
_EVERY_KIND_OF_PATH = (
    ["README.md"],
    ["docs/a.md"],
    ["web/x.ts"],
    ["src/sdk/a.py"],
    ["ci.yml"],
    ["Makefile"],
    ["nowhere/new.txt"],
    ["tools/check_links.py"],
)


def test_overrides_only_add() -> None:
    for paths in _EVERY_KIND_OF_PATH:
        assert set(_plan(paths, areas="").jobs) <= set(_plan(paths).jobs), paths


@pytest.mark.parametrize(
    "paths", ['["Makefile"]', '[":(glob)**"]', '["*"]', '["?*"]', '[":(exclude)docs/"]']
)
def test_an_override_never_makes_a_path_known(paths) -> None:
    """An override adds its jobs, and only an input names a path: Makefile is no input's, so it
    still plans the whole tier, the override's job and reason in it, whatever the override
    reaches."""
    areas = (
        _AREAS
        + f'\n[[override]]\npaths = {paths}\njobs = ["build"]\n'
        + 'reason = "an override that reaches a path no input names"\n'
    )
    p = _plan(["Makefile"], areas=areas)
    assert p.full and set(p.jobs) == _MERGE, paths
    assert any("no input names" in r for r in p.reasons["build"]), p.reasons["build"]
    for kind_of_path in _EVERY_KIND_OF_PATH:
        assert set(_plan(kind_of_path).jobs) <= set(_plan(kind_of_path, areas=areas).jobs)


def test_a_plan_holds_only_its_tiers_jobs() -> None:
    p = _plan(["web/src/App.tsx", "docs/a.md"], tier="change")
    assert set(p.jobs) == {"rails", "docs-check"}


def test_the_plan_keeps_the_catalogues_order() -> None:
    p = _plan(["web/x.ts", "docs/a.md", "src/sdk/a.py"])
    order = [j.id for j in _cat().tier("merge")]
    assert list(p.jobs) == [j for j in order if j in p.jobs]


# ── fail-safe: what the planner cannot read runs the whole tier ──────────────────────────────


@pytest.mark.parametrize(
    ("paths", "why"),
    [
        ([], "the diff is empty"),
        (["nowhere/new.txt"], "nowhere/new.txt is named by no input but the whole tree"),
        ([""], "cannot be read"),
        (["/etc/passwd"], "cannot be read"),
        (["../outside.py"], "cannot be read"),
        (["docs/a\nb.md"], "cannot be read"),
        (["docs/a.md", 3], "cannot be read"),
        (["docs/a.md\r"], "cannot be read"),
        (["docs/a.md\0src/x.py"], "cannot be read"),
        (["docs//a.md"], "cannot be read"),
        (["./docs/a.md"], "cannot be read"),
        (["docs/./a.md"], "cannot be read"),
        (["docs/a.md/"], "cannot be read"),
        (None, "the diff could not be read"),
    ],
    ids=[
        "empty",
        "unknown",
        "blank",
        "absolute",
        "parent",
        "newline",
        "not-a-string",
        "carriage-return",
        "nul",
        "double-slash",
        "dot-slash",
        "dot-part",
        "trailing-slash",
        "none",
    ],
)
def test_a_diff_it_cannot_read_plans_the_whole_tier(paths, why) -> None:
    p = _plan(paths)
    assert p.full and set(p.jobs) == _MERGE
    assert any(why in r for r in p.full_reasons), p.full_reasons


def test_one_unknown_path_outvotes_any_number_of_known_ones() -> None:
    p = _plan(["docs/a.md", "docs/b.md", "brand-new/thing.ts"])
    assert p.full and set(p.jobs) == _MERGE


def test_a_planner_error_plans_the_whole_tier(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("a bug in the matcher")

    monkeypatch.setattr(planner, "reaches", boom)
    p = _plan(["docs/a.md"])
    assert p.full and set(p.jobs) == _MERGE
    assert any("a bug in the matcher" in r for r in p.full_reasons)


def test_full_plans_the_whole_tier() -> None:
    p = _plan(["docs/a.md"], full=True)
    assert p.full and set(p.jobs) == _MERGE
    assert p.full_reasons == ("the run asked for the whole tier (--full)",)


def test_an_unknown_paths_reason_says_what_would_have_named_it() -> None:
    p = _plan(["nowhere/new.txt"])
    assert p.full_reasons == ("nowhere/new.txt is named by no input but the whole tree",)


@pytest.mark.parametrize(
    ("tier", "why"),
    [
        ("Merge", "'Merge' is not a tier"),
        ("nightly", "'nightly' is not a tier"),
        ("weekly", "the weekly tier has no job"),
    ],
)
def test_a_tier_the_catalogue_has_no_job_in_is_refused(tier, why) -> None:
    """A plan of a tier with no job would run nothing; one with a misspelled name, too."""
    with pytest.raises(ValueError, match=why):
        _plan(["docs/a.md"], tier=tier)


def test_a_security_or_live_job_is_never_selected_out() -> None:
    for paths in (["docs/a.md"], ["web/x.ts"], ["README.md"]):
        assert _FLOOR <= set(_plan(paths).jobs), paths


# ── the matcher agrees with git ──────────────────────────────────────────────────────────────

_FILES = (
    "src/x.py src/a/y.py src/a/b/z.py src/z.py docs/d.md README.md src/a/R.md "
    ".github/workflows/ci.yml .github/workflows/other.yml tests/t.py top.txt web/e2e/a.spec.ts "
    "harness x[1].txt x1.txt a-b.txt a^b.txt"
).split()

_SPECS = [
    [":(glob)**"],
    [":(glob)src"],
    [":(glob)src/*"],
    [":(glob)src/**"],
    [":(glob)*.md"],
    [":(glob)**/*.md"],
    ["src"],
    ["src/"],
    ["src/a/"],
    ["*.md"],
    ["src/*"],
    [":(literal)src"],
    [":(icase)SRC/x.py"],
    [":/src/a"],
    [":(glob)src/a/**/z.py"],
    [":(glob)**", ":(exclude,glob).github/**", ":(exclude,glob)*.md"],
    [":(exclude,glob)src/**"],
    [":(glob)**", ":!docs/", ":^tests/"],
    ["web/e2e/a.spec.ts"],
    # A folder spec names no file of its name; git compares every spec as a literal path first.
    ["top.txt/"],
    ["harness/"],
    [":(glob)**", ":(exclude)harness/"],
    ["x[1].txt"],
    [":(glob)x[1].txt"],
    [":(literal)x[1].txt"],
    # A negated class: git reads ! and ^ alike, and under :(glob) a class never matches a /.
    ["a[^^]b.txt"],
    ["a[!-]b.txt"],
    [":(glob)a[^^]b.txt"],
    [":(glob)src/[!x]*"],
    ["src/[ax]*"],
    [":(glob)src[!x]a/*"],
    ["src[!x]a/*"],
    [":(glob)src?a/*"],
    ["src?a/*"],
    # Without :(glob), every run of * crosses folders and ** is no folder rule.
    ["src/**/z.py"],
    ["src/a**"],
    ["*"],
    ["?*"],
    [":(glob)**/*"],
    [":(glob)*"],
    [":(icase)*.MD"],
    [":(icase,glob)**/*.MD"],
    [":(icase,literal)SRC/X.PY"],
    [":(glob)**", ":(exclude,icase)SRC/"],
    [":(top)src"],
    [":/:src/a"],
    [":(glob)**", ":!:docs"],
    # An empty path is the whole tree, as an include or an exclude.
    [":/"],
    [":(top)"],
    [":(glob)"],
    [":!"],
    ["src/", ":(exclude,glob)**"],
]


def test_every_form_the_matcher_is_held_to_is_one_the_catalogue_accepts() -> None:
    for specs in _SPECS:
        for spec in specs:
            assert catalogue.pathspec_fault(spec) is None, spec


@pytest.mark.parametrize(
    ("spec", "fault"),
    [
        (".", "normal form"),
        ("./", "normal form"),
        ("./src", "normal form"),
        ("src/./a", "normal form"),
        ("src/.", "normal form"),
        ("src//a", "normal form"),
        ("src//", "normal form"),
        (":(top).", "normal form"),
        (":(top)./src", "normal form"),
        (":(literal,glob)src", "literal and glob"),
        ("src\\*", "backslash"),
        ("x[1.txt", "never closes"),
        (":(glob)src/[a/b]", "a / in a class"),
        ("src/[[:alpha:]]*", "character class"),
    ],
)
def test_a_pathspec_git_reads_otherwise_than_the_matcher_is_refused(spec, fault) -> None:
    """git reads . and ./src as the tree and src, but :(top). as nothing: the loader takes each
    path in its one normal form, and no wildcard the matcher would read otherwise than git."""
    got = catalogue.pathspec_fault(spec)
    assert got is not None and fault in got, (spec, got)


def _with_build_inputs(*inputs: str) -> catalogue.Catalogue:
    cat = _cat()
    build = dataclasses.replace(cat.job("build"), inputs=inputs)
    return dataclasses.replace(cat, jobs=tuple(build if j.id == "build" else j for j in cat.jobs))


@pytest.mark.parametrize(
    "spec", [":(glob)**", ":/", ":(top)", ":(glob)", "*", "?*", ":(glob)**/*", ":(icase)*"]
)
def test_an_input_of_the_whole_tree_names_no_path_however_it_is_spelled(spec) -> None:
    """build declares a whole-tree input: it is scheduled by any path, and makes none known."""
    whole = _with_build_inputs(spec)
    p = planner.plan(whole, "merge", ["nowhere/new.txt"], overrides=())
    assert p.full and set(p.jobs) == _MERGE, spec
    assert planner.whole_tree(spec), spec
    assert "build" in planner.plan(whole, "merge", ["docs/a.md"], overrides=()).jobs, spec


@pytest.mark.parametrize("spec", [":!", ":(exclude,glob)**", ":(exclude)*"])
def test_an_exclude_of_the_whole_tree_names_no_path_either(spec) -> None:
    """An exclude names the paths a job skips; one that skips every path names none."""
    cat = _with_build_inputs("src/sdk/", spec)
    p = planner.plan(cat, "merge", ["nowhere/new.txt"], overrides=())
    assert p.full and set(p.jobs) == _MERGE, spec
    assert not planner.whole_tree(spec), spec


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return done.stdout


def test_reaches_matches_what_git_ls_files_selects(tmp_path) -> None:
    for rel in _FILES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    for specs in _SPECS:
        want = set(_git(tmp_path, "ls-files", "--", *specs).splitlines())
        got = {f for f in _FILES if planner.reaches(tuple(specs), f)}
        assert got == want, specs


# ── the rails floor ──────────────────────────────────────────────────────────────────────────


def _history(folder: Path, run_id: int, workflow: str, rails: float, conclusion: str) -> None:
    head = {
        "kind": "run",
        "run_id": run_id,
        "workflow": workflow,
        "jobs": [
            {"name": "rails", "conclusion": conclusion, "minutes": rails},
            {"name": "lint", "conclusion": "success", "minutes": 1.0},
        ],
    }
    test = {"kind": "test", "test": "tests/test_x.py::test_it"}
    lines = [json.dumps(head), json.dumps(test)]
    (folder / f"history-{run_id}-1.jsonl").write_text("\n".join(lines) + "\n")


def _head(folder: Path, run_id: int, head: str) -> None:
    (folder / f"history-{run_id}-1.jsonl").write_text(head + "\n")


def test_the_rails_floor_is_the_median_of_cis_recorded_rails_runs(tmp_path) -> None:
    """The median, not the mean (14.5 here), of ci.yml's passing rails runs alone."""
    _history(tmp_path, 1, "ci.yml", 6.0, "success")
    _history(tmp_path, 2, "ci.yml", 30.0, "success")
    _history(tmp_path, 3, "ci.yml", 7.5, "success")
    _history(tmp_path, 4, "breadth.yml", 99.0, "success")
    _history(tmp_path, 5, "ci.yml", 1.0, "cancelled")
    rails = '{"name": "rails", "conclusion": "success", "minutes": %s}'
    _head(tmp_path, 6, '{"kind": "test", "workflow": "ci.yml", "jobs": [%s]}' % (rails % 2))
    _head(tmp_path, 7, '{"kind": "run", "workflow": "ci.yml", "jobs": [%s]}' % (rails % "true"))
    assert planner.rails_floor(tmp_path) == {"minutes": 7.5, "runs": 3}


@pytest.mark.parametrize(
    "head",
    [
        '{"kind": "run", "workflow": "ci.yml", "jobs": 5}',
        '{"kind": "run", "workflow": "ci.yml", "jobs": true}',
        '{"kind": "run", "workflow": "ci.yml", "jobs": {"rails": 3}}',
        '{"kind": "run", "workflow": "ci.yml", "jobs": [5, "rails", null]}',
        "[1, 2]",
        '{"kind": "run", "workflow": "ci.yml", "jobs": [{"name": "rails", "conclusion": "success",'
        ' "minutes": NaN}]}',
        '{"kind": "run", "workflow": "ci.yml", "jobs": [{"name": "rails", "conclusion": "success",'
        ' "minutes": Infinity}]}',
        '{"kind": "run", "workflow": "ci.yml", "jobs": [{"name": "rails", "conclusion": "success",'
        ' "minutes": -3}]}',
    ],
    ids=[
        "jobs-int",
        "jobs-bool",
        "jobs-table",
        "job-not-table",
        "not-a-table",
        "nan",
        "inf",
        "neg",
    ],
)
def test_a_history_head_it_cannot_measure_from_is_skipped(tmp_path, head) -> None:
    _history(tmp_path, 1, "ci.yml", 6.0, "success")
    _head(tmp_path, 2, head)
    assert planner.rails_floor(tmp_path) == {"minutes": 6.0, "runs": 1}
    p = _plan(["docs/a.md"], history=tmp_path)
    assert not p.full and p.rails_floor == {"minutes": 6.0, "runs": 1}


def test_a_history_the_floor_cannot_be_read_from_plans_anyway(tmp_path, monkeypatch) -> None:
    def broken(_folder):
        raise RuntimeError("a bug in the floor")

    monkeypatch.setattr(planner, "rails_floor", broken)
    p = _plan(["docs/a.md"], history=tmp_path)
    assert not p.full and p.rails_floor == planner.NOT_MEASURED


@pytest.mark.parametrize("where", ["missing", "empty"])
def test_with_no_recorded_runs_the_floor_is_not_measured(tmp_path, where) -> None:
    folder = tmp_path / "history"
    if where == "empty":
        folder.mkdir()
    assert planner.rails_floor(folder) == planner.NOT_MEASURED
    assert planner.rails_floor(None) == planner.NOT_MEASURED


def test_every_plan_reports_the_floor() -> None:
    assert _plan(["docs/a.md"]).rails_floor == planner.NOT_MEASURED
    assert _plan([]).rails_floor == planner.NOT_MEASURED


# ── the command ──────────────────────────────────────────────────────────────────────────────


def _command(*args: str, stdin: str = "docs/a.md\n") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tools.ci", "plan", *args],
        capture_output=True,
        text=True,
        input=stdin,
        cwd=Path(__file__).resolve().parents[1],
    )


def _cli(tmp_path: Path, *args: str, paths: str = "docs/a.md\n") -> tuple[int, str]:
    cat = tmp_path / "catalogue.toml"
    cat.write_text(_TEXT)
    (tmp_path / "areas.toml").write_text(_AREAS)
    listed = tmp_path / "changed.txt"
    listed.write_text(paths)
    done = _command("merge", "--catalogue", str(cat), "--paths", str(listed), *args)
    return done.returncode, done.stdout


def test_the_command_prints_the_plan_as_json(tmp_path) -> None:
    code, out = _cli(tmp_path)
    assert code == 0
    data = json.loads(out)
    assert set(data) == {"tier", "full", "jobs", "reasons", "full_reasons", "rails_floor"}
    assert set(data["jobs"]) == _FLOOR | {"docs-check"} and data["full"] is False
    assert data["rails_floor"] == planner.NOT_MEASURED


def test_the_command_plans_the_whole_tier_for_an_empty_list(tmp_path) -> None:
    code, out = _cli(tmp_path, paths="")
    assert code == 0 and json.loads(out)["full"] is True


@pytest.mark.parametrize("listed", ["missing", "not-utf-8"])
def test_the_command_plans_the_whole_tier_for_a_list_it_cannot_read(tmp_path, listed) -> None:
    code, _ = _cli(tmp_path)
    assert code == 0
    paths = tmp_path / "unreadable.txt"
    if listed == "not-utf-8":
        paths.write_bytes(b"docs/\xff.md\n")
    done = _command("merge", "--catalogue", str(tmp_path / "catalogue.toml"), "--paths", str(paths))
    data = json.loads(done.stdout)
    assert done.returncode == 0 and data["full"] is True
    assert data["full_reasons"] == ["the diff could not be read"]


def test_the_command_reads_a_nul_separated_list(tmp_path) -> None:
    code, out = _cli(tmp_path, "-z", paths="docs/a.md\0src/sdk/b.py\0")
    assert code == 0 and "sdk" in json.loads(out)["jobs"]


def test_the_command_with_full_plans_the_whole_tier(tmp_path) -> None:
    code, out = _cli(tmp_path, "--full")
    assert code == 0 and json.loads(out)["full"] is True


@pytest.mark.parametrize(
    "paths", ["docs/a.md\r\nsrc/x.py\r\n", "docs/a.md\0src/x.py\n"], ids=["crlf", "nul"]
)
def test_the_command_plans_the_whole_tier_for_a_list_it_cannot_split(tmp_path, paths) -> None:
    code, out = _cli(tmp_path, paths=paths)
    assert code == 0 and json.loads(out)["full"] is True


@pytest.mark.parametrize(
    ("tier", "why"), [("Merge", "'Merge' is not a tier"), ("weekly", "the weekly tier has no job")]
)
def test_the_command_exits_2_on_a_tier_with_no_job(tmp_path, tier, why) -> None:
    cat = tmp_path / "catalogue.toml"
    cat.write_text(_TEXT)
    (tmp_path / "areas.toml").write_text(_AREAS)
    done = _command(tier, "--catalogue", str(cat))
    assert done.returncode == 2 and done.stdout == "", (done.returncode, done.stdout)
    assert why in done.stderr


@pytest.mark.parametrize("minutes", ["NaN", "Infinity", "-Infinity"])
def test_the_command_prints_strict_json_whatever_the_history(tmp_path, minutes) -> None:
    history = tmp_path / "history"
    history.mkdir()
    head = '{"kind": "run", "workflow": "ci.yml", "jobs": [{"name": "rails", "conclusion": '
    _head(history, 1, head + f'"success", "minutes": {minutes}' + "}]}")
    code, out = _cli(tmp_path, "--history", str(history))
    assert code == 0
    data = json.loads(out, parse_constant=lambda c: pytest.fail(f"not strict JSON: {c}"))
    assert data["rails_floor"] == planner.NOT_MEASURED


@pytest.mark.parametrize("jobs", ["5", "true", "[5]"])
def test_the_command_plans_on_a_history_it_cannot_read(tmp_path, jobs) -> None:
    history = tmp_path / "history"
    history.mkdir()
    _head(history, 1, '{"kind": "run", "workflow": "ci.yml", "jobs": %s}' % jobs)
    code, out = _cli(tmp_path, "--history", str(history))
    assert code == 0 and json.loads(out)["rails_floor"] == planner.NOT_MEASURED


def test_the_command_exits_2_on_a_catalogue_it_cannot_read(tmp_path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("not toml [[[")
    done = _command("merge", "--catalogue", str(bad))
    assert done.returncode == 2 and done.stdout == ""


def test_the_command_reads_the_overrides_beside_the_catalogue(tmp_path) -> None:
    code, out = _cli(tmp_path, paths="ci.yml\n")
    assert code == 0 and json.loads(out)["full"] is True
    other = tmp_path / "elsewhere.toml"
    other.write_text("")
    code, out = _cli(tmp_path, "--areas", str(other), paths="ci.yml\n")
    assert code == 0 and json.loads(out)["full"] is True, "ci.yml is no input's, so unknown"
    assert json.loads(out)["full_reasons"] == ["ci.yml is named by no input but the whole tree"]


@pytest.mark.parametrize("areas", [None, "[[override]]\npaths = []\n"], ids=["missing", "bad"])
def test_the_command_exits_2_on_overrides_it_cannot_read(tmp_path, areas) -> None:
    cat = tmp_path / "catalogue.toml"
    cat.write_text(_TEXT)
    if areas is not None:
        (tmp_path / "areas.toml").write_text(areas)
    done = _command("merge", "--catalogue", str(cat))
    assert done.returncode == 2 and done.stdout == "", done.stderr
    assert "areas.toml" in done.stderr


# ── the area map's overrides ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("old", "new", "expect"),
    [
        ('jobs = ["build"]', 'jobs = ["no-such-job"]', "names no-such-job, which is no job"),
        (
            'reason = "the sdist must carry README.md, so a README change rebuilds the package"',
            'reason = ""',
            "reason is a non-empty string",
        ),
        ('paths = ["README.md"]', 'paths = ["../README.md"]', "leaves the repository"),
        ('paths = ["README.md"]', "paths = []", "paths names at least one pathspec"),
        ('jobs = ["build"]', 'jobs = ["build"]\nextra = 1', "unknown key 'extra'"),
        ('jobs = ["build"]', "jobs = []", "adds no job and is not whole_tier"),
        (
            'reason = "the sdist must carry README.md, so a README change rebuilds the package"',
            'reason = "   "',
            "reason is a non-empty string",
        ),
        (
            'reason = "the sdist must carry README.md, so a README change rebuilds the package"',
            "reason = 5",
            "reason is a non-empty string",
        ),
        ('paths = ["README.md"]', 'paths = ["./README.md"]', "normal form"),
    ],
)
def test_an_override_the_catalogue_cannot_hold_is_refused(old, new, expect) -> None:
    assert old in _AREAS
    with pytest.raises(catalogue.CatalogueError) as caught:
        catalogue.load_areas(_AREAS.replace(old, new, 1), _cat())
    assert any(expect in p for p in caught.value.problems), caught.value.problems


@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("override = 5\n", "override is a list of"),
        ("override = [1]\n", "override 1: is not a table"),
        (_AREAS + '\n[[job]]\nid = "x"\n', "unknown table 'job'"),
        ("not toml [[[", "is not TOML"),
    ],
    ids=["not-a-list", "not-a-table", "a-job", "not-toml"],
)
def test_an_areas_file_the_loader_cannot_read_is_refused(text, expect) -> None:
    with pytest.raises(catalogue.CatalogueError) as caught:
        catalogue.load_areas(text, _cat())
    assert any(expect in p for p in caught.value.problems), caught.value.problems


def test_the_catalogue_holds_no_override() -> None:
    """The overrides live in the areas file alone, so the catalogue refuses one."""
    with pytest.raises(catalogue.CatalogueError) as caught:
        catalogue.load(_TEXT + _AREAS)
    assert any("unknown table 'override'" in p for p in caught.value.problems)


def test_the_overrides_load_typed() -> None:
    first, second = catalogue.load_areas(_AREAS, _cat())
    assert first.paths == ("README.md",) and first.jobs == ("build",) and not first.whole_tier
    assert second.whole_tier and second.jobs == ()


# ── the rails floor on CI, from the collector's history ──────────────────────────────────────

_REPO = "example/repo"


def _zip(
    run_id: int,
    rails: float,
    workflow: str = "ci.yml",
    *,
    kind: str = "run",
    line_bytes: int | None = None,
) -> bytes:
    """One history archive whose run line names ``run_id``; ``line_bytes`` pads that line,
    its newline included, to exactly that length."""
    import io
    import zipfile

    head: dict[str, object] = {
        "kind": kind,
        "run_id": run_id,
        "workflow": workflow,
        "jobs": [{"name": "rails", "conclusion": "success", "minutes": rails}],
    }
    line = json.dumps(head) + "\n"
    if line_bytes is not None:
        head["pad"] = ""
        bare = len(json.dumps(head)) + 1
        head["pad"] = "x" * (line_bytes - bare)
        line = json.dumps(head) + "\n"
        assert len(line.encode()) == line_bytes
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"history-{run_id}-1.jsonl", line)
    return out.getvalue()


class _Clock:
    """A clock that moves only when a fake gh call takes time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _gh(
    runs: dict[int, tuple[str, bytes | None]],
    *,
    collector: bool = True,
    owner: str = _REPO,
    size: int = 10,
    clock: _Clock | None = None,
    cost=None,
    timeouts: list[float | None] | None = None,
):
    """A fake gh: the listed runs, each with its conclusion and its history archive. With a
    ``clock``, each call takes ``cost(args)`` seconds, or its timeout, when that is shorter, and
    then fails as a hung call would."""
    from tools.ci import telemetry

    def spend(args: tuple[str, ...], timeout: float | None) -> None:
        if timeouts is not None:
            timeouts.append(timeout)
        if clock is None:
            return
        took = cost(args) if cost else 0.0
        if timeout is not None and took > timeout:
            clock.now += timeout
            raise subprocess.TimeoutExpired(["gh", *args], timeout)
        clock.now += took

    def text(*args: str, timeout: float | None = None) -> str:
        spend(args, timeout)
        path = args[1]
        if path.startswith(f"repos/{_REPO}/actions/workflows/"):
            return "".join(f"{r} {c} {owner}\n" for r, (c, _) in runs.items())
        if path.startswith(f"repos/{_REPO}/actions/artifacts?name="):
            run = int(path.split("telemetry-history-")[1].split("-")[0])
            if runs[run][1] is None:
                return json.dumps({"artifacts": []})
            made_by = {"id": 9000 + run, "head_branch": "main"}
            made_by.update(head_repository_id=1, repository_id=1)
            listed = [{"id": run, "size_in_bytes": size, "expired": False, "workflow_run": made_by}]
            return json.dumps({"artifacts": listed})
        if path.startswith(f"repos/{_REPO}/actions/runs/"):
            return json.dumps(
                {
                    "path": telemetry.COLLECTOR_PATH if collector else "an-impostor-workflow.yml",
                    "event": "workflow_run",
                    "head_branch": "main",
                    "head_repository": {"full_name": _REPO},
                }
            )
        raise AssertionError(args)

    def binary(*args: str, timeout: float | None = None) -> bytes:
        spend(args, timeout)
        run = int(args[1].split("/artifacts/")[1].split("/")[0])
        data = runs[run][1]
        assert data is not None
        return data

    return text, binary


def _floor(text, binary, **kw):
    return planner.rails_floor_from_ci(_REPO, text=text, binary=binary, **kw)


def test_the_ci_floor_is_the_median_of_the_newest_three_collected_runs() -> None:
    """A cancelled run is skipped; a red run counts, since its rails job passed."""
    runs = {
        5: ("success", _zip(5, 8.0)),
        4: ("cancelled", _zip(4, 1.0)),
        3: ("failure", _zip(3, 6.0)),
        2: ("success", None),
        1: ("success", _zip(1, 7.0)),
        0: ("success", _zip(0, 99.0)),
    }
    assert _floor(*_gh(runs)) == {"minutes": 7.0, "runs": 3}


def test_history_the_collector_did_not_upload_is_not_read() -> None:
    text, binary = _gh({1: ("success", _zip(1, 7.0))}, collector=False)
    assert _floor(text, binary) == planner.NOT_MEASURED


def test_a_history_line_for_another_run_is_not_read() -> None:
    assert _floor(*_gh({1: ("success", _zip(2, 7.0))})) == planner.NOT_MEASURED


def test_a_run_of_another_repository_is_not_read() -> None:
    """A fork's run on its own main can carry a history of the same name."""
    text, binary = _gh({1: ("success", _zip(1, 7.0))}, owner="someone/fork")
    assert _floor(text, binary) == planner.NOT_MEASURED


@pytest.mark.parametrize(
    ("workflow", "kind"), [("other.yml", "run"), ("ci.yml", "test")], ids=["workflow", "kind"]
)
def test_a_head_of_another_workflow_or_kind_is_not_read(workflow: str, kind: str) -> None:
    text, binary = _gh({1: ("success", _zip(1, 7.0, workflow, kind=kind))})
    assert _floor(text, binary) == planner.NOT_MEASURED


def test_a_history_line_is_read_only_within_its_bound() -> None:
    """A line of the bound, its newline included, is read; one byte more is refused."""
    from tools.ci import shards

    bound = shards.MAX_HISTORY_LINE_BYTES
    at = _gh({1: ("success", _zip(1, 7.0, line_bytes=bound))})
    over = _gh({1: ("success", _zip(1, 7.0, line_bytes=bound + 1))})
    assert _floor(*at) == {"minutes": 7.0, "runs": 1}
    assert _floor(*over) == planner.NOT_MEASURED


def test_a_long_history_line_is_never_read_whole(monkeypatch) -> None:
    """The bound is on what is read, not only on what is kept: of a line three times over it,
    about the bound is read (io's readline may read one buffer past its limit), never the
    line."""
    import zipfile

    from tools.ci import shards

    bound = shards.MAX_HISTORY_LINE_BYTES
    read: list[int] = []
    real = zipfile.ZipExtFile.readline

    def readline(self, limit: int = -1) -> bytes:
        got = real(self, limit)
        read.append(len(got))
        return got

    monkeypatch.setattr(zipfile.ZipExtFile, "readline", readline)
    assert _floor(*_gh({1: ("success", _zip(1, 7.0, line_bytes=3 * bound))})) == (
        planner.NOT_MEASURED
    )
    assert read and max(read) < 2 * bound, read


def test_an_archive_it_cannot_read_is_skipped_and_the_next_is_read() -> None:
    runs = {2: ("success", b"not a zip archive"), 1: ("success", _zip(1, 7.0))}
    assert _floor(*_gh(runs)) == {"minutes": 7.0, "runs": 1}


def test_a_failing_api_gives_not_measured_never_a_guess(capsys) -> None:
    def text(*_a: str, timeout: float | None = None) -> str:
        raise OSError("api down")

    assert _floor(text, text) == planner.NOT_MEASURED
    assert "rails floor not read" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("listing", "each"),
    [(59.0, 119.0), (30.0, 100.0), (1.0, 119.0), (0.0, 30.0), (0.0, 500.0), (61.0, 1.0)],
)
def test_reading_never_passes_its_time_limit(listing: float, each: float) -> None:
    """Every gh call is given only the time left, so a slow API cannot hold the gating job
    past the limit, however its time is spread across calls."""
    from tools.ci import shards

    clock = _Clock()
    runs = {r: ("success", _zip(r, 8.0)) for r in range(9, 0, -1)}
    timeouts: list[float | None] = []

    def cost(args: tuple[str, ...]) -> float:
        return listing if "/actions/workflows/" in args[1] else each

    text, binary = _gh(runs, clock=clock, cost=cost, timeouts=timeouts)
    _floor(text, binary, clock=clock)
    assert clock.now <= planner.FLOOR_SECONDS, clock.now
    assert timeouts and all(t is not None and 0 < t <= shards.GH_TIMEOUT_SECONDS for t in timeouts)


def test_a_read_that_runs_out_of_time_keeps_what_it_read(capsys) -> None:
    clock = _Clock()
    runs = {2: ("success", _zip(2, 8.0)), 1: ("success", _zip(1, 6.0))}
    text, binary = _gh(runs, clock=clock, cost=lambda args: 15.0)
    assert _floor(text, binary, clock=clock) == {"minutes": 8.0, "runs": 1}
    assert clock.now <= planner.FLOOR_SECONDS
    assert "rails floor not read" in capsys.readouterr().err


def test_a_floor_given_to_plan_is_the_one_reported() -> None:
    p = _plan(["docs/a.md"], floor={"minutes": 7.0, "runs": 3})
    assert p.rails_floor == {"minutes": 7.0, "runs": 3}


def _outputs(tmp_path: Path, paths: str, *extra: str) -> subprocess.CompletedProcess[str]:
    cat = tmp_path / "catalogue.toml"
    cat.write_text(_TEXT)
    (tmp_path / "areas.toml").write_text(_AREAS)
    listed = tmp_path / "changed.txt"
    listed.write_text(paths)
    return _command("merge", "--catalogue", str(cat), "--paths", str(listed), "--outputs", *extra)


@pytest.mark.parametrize(
    ("paths", "full", "jobs"),
    [
        ("docs/a.md\n", "false", _FLOOR | {"docs-check"}),
        ("newtop/x.py\n", "true", _MERGE),
        ("", "true", _MERGE),
    ],
    ids=["a plan", "an unknown path", "an empty diff"],
)
def test_outputs_prints_github_output_lines_and_the_plan_on_stderr(
    tmp_path, paths: str, full: str, jobs: set[str]
) -> None:
    done = _outputs(tmp_path, paths)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert len(lines) == 2 and lines[0] == f"full={full}", lines
    assert lines[1].startswith("jobs=")
    listed = lines[1][len("jobs=") :]
    assert set(json.loads(listed)) == jobs
    assert listed == json.dumps(json.loads(listed), separators=(",", ":"))
    assert json.loads(done.stderr)["full"] is (full == "true")


def test_outputs_with_full_plans_the_whole_tier(tmp_path) -> None:
    done = _outputs(tmp_path, "docs/a.md\n", "--full")
    assert done.returncode == 0 and done.stdout.splitlines()[0] == "full=true"


def _main_outputs(tmp_path: Path, monkeypatch, capsys, text, binary) -> tuple[int, list[str], str]:
    """``plan merge --repo --outputs`` in this process, its gh calls answered by the fakes."""
    from tools.ci import shards

    cat = tmp_path / "catalogue.toml"
    cat.write_text(_TEXT)
    (tmp_path / "areas.toml").write_text(_AREAS)
    listed = tmp_path / "changed.txt"
    listed.write_text("docs/a.md\n")
    monkeypatch.setattr(shards, "_gh_text", text)
    monkeypatch.setattr(shards, "_gh_binary", binary)
    code = planner.main(
        ["merge", "--catalogue", str(cat), "--paths", str(listed), "--repo", _REPO, "--outputs"]
    )
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def test_an_api_answer_that_is_not_json_still_gives_the_plan(tmp_path, monkeypatch, capsys) -> None:
    """The floor is a report, never a gate: whatever the API answers, the plan is printed."""
    good, binary = _gh({1: ("success", _zip(1, 7.0))})

    def text(*args: str, timeout: float | None = None) -> str:
        if "/actions/artifacts?name=" in args[1]:
            return "<html>an error page</html>"
        return good(*args, timeout=timeout)

    code, out, err = _main_outputs(tmp_path, monkeypatch, capsys, text, binary)
    assert code == 0, err
    assert out[0] == "full=false" and out[1].startswith("jobs=") and len(out) == 2
    assert "rails floor not read" in err


def test_a_warning_during_the_floor_read_stays_off_the_outputs(
    tmp_path, monkeypatch, capsys
) -> None:
    """stdout carries the two output lines alone; what the floor read says goes to stderr."""
    from tools.ci import shards

    big = shards.MAX_HISTORY_ARCHIVE_BYTES + 1
    text, binary = _gh({1: ("success", _zip(1, 7.0))}, size=big)
    code, out, err = _main_outputs(tmp_path, monkeypatch, capsys, text, binary)
    assert code == 0 and len(out) == 2, out
    assert out[0] == "full=false" and out[1].startswith("jobs=")
    assert "telemetry history skipped" in err
    assert json.loads(err[err.index("{") :])["rails_floor"] == planner.NOT_MEASURED
