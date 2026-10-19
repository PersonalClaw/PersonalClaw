"""The catalogue loader's rules on external inputs, the merge tier's list, the worker count and
installs (tools/ci/catalogue.py). Each rule is one refused catalogue and one that loads."""

from __future__ import annotations

import pytest

from tools.ci import catalogue

_BASE = """
[tier.merge]
jobs = ["rails", "python-suite", "python-suite-macos"]

[[job]]
id = "rails"
tiers = ["change", "merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
reuse = "tree"
timeout_min = 10
command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]

[[job]]
id = "python-suite"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = ["shard-history"]
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
timeout_min = 55
shards = 4
command = [{ argv = ["uv", "run", "pytest", "--shard", "{shard}/4"] }]

[[job]]
id = "python-suite-macos"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = ["shard-history"]
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
platform = "macos-26"
timeout_min = 50
command = [{ argv = ["uv", "run", "pytest", "tests"] }]
"""


_MERGE_LIST = '[tier.merge]\njobs = ["rails", "python-suite", "python-suite-macos"]\n'


def _edit(old: str, new: str) -> str:
    assert old in _BASE, old
    return _BASE.replace(old, new, 1)


def _problems(text: str) -> list[str]:
    with pytest.raises(catalogue.CatalogueError) as caught:
        catalogue.load(text)
    return caught.value.problems


def test_the_base_loads_with_its_merge_list() -> None:
    cat = catalogue.load(_BASE)
    assert cat.merge_jobs == ("rails", "python-suite", "python-suite-macos")


def test_every_external_input_has_a_kind() -> None:
    assert set(catalogue.EXTERNAL_INPUT_KINDS) == catalogue.EXTERNAL_INPUTS
    assert set(catalogue.EXTERNAL_INPUT_KINDS.values()) == {
        "revision",
        "plan-only",
        "unpinned",
        "live",
    }
    assert catalogue.LIVE_INPUTS == {"advisory-feed"}
    assert catalogue.UNPINNED_INPUTS == {"package-index", "browser-download"}


@pytest.mark.parametrize("unpinned", sorted(catalogue.UNPINNED_INPUTS))
def test_tree_reuse_of_a_job_reading_an_unpinned_input_is_refused(unpinned: str) -> None:
    text = _edit(
        'external_inputs = []\ntoolchain = ["uv-sync"]',
        f'external_inputs = ["{unpinned}"]\ntoolchain = ["uv-sync"]',
    )
    assert f"rails: reuse is tree, but it reads {unpinned}" in " ".join(_problems(text))


def test_a_ttl_on_a_job_reading_an_unpinned_input_loads() -> None:
    text = _edit(
        'external_inputs = []\ntoolchain = ["uv-sync"]\ncache = ["uv"]\nsecurity = "ordinary"\n'
        'reuse = "tree"',
        'external_inputs = ["package-index"]\ntoolchain = ["uv-sync"]\ncache = ["uv"]\n'
        'security = "ordinary"\nreuse = "ttl:24"',
    )
    assert str(catalogue.load(text).job("rails").reuse) == "ttl:24"


@pytest.mark.parametrize("kept", ["apps-partner", "shard-history"])
def test_tree_reuse_of_a_job_reading_a_pinned_or_plan_only_input_loads(kept: str) -> None:
    text = _edit(
        'external_inputs = []\ntoolchain = ["uv-sync"]',
        f'external_inputs = ["{kept}"]\ntoolchain = ["uv-sync"]',
    )
    assert catalogue.load(text).job("rails").reuse.kind == "tree"


def test_a_catalogue_without_a_merge_list_is_refused() -> None:
    text = _edit('[tier.merge]\njobs = ["rails", "python-suite", "python-suite-macos"]\n', "")
    assert "the catalogue has no [tier.merge] jobs list" in " ".join(_problems(text))


def test_a_merge_job_the_list_leaves_out_is_refused() -> None:
    text = _edit('jobs = ["rails", "python-suite",', 'jobs = ["python-suite",')
    assert "rails: its tiers claim merge, and [tier.merge] does not list it" in _problems(text)


def test_a_listed_job_that_does_not_claim_merge_is_refused() -> None:
    text = _edit('id = "rails"\ntiers = ["change", "merge"]', 'id = "rails"\ntiers = ["change"]')
    assert "rails: [tier.merge] lists it, and it is no job whose tiers claim merge" in _problems(
        text
    )


@pytest.mark.parametrize(
    ("old", "new", "expect"),
    [
        ("jobs = [", "jobs = 3 #[", "tier.merge jobs is a non-empty list of job ids"),
        ('jobs = ["rails", ', 'jobs = ["rails", "rails", ', "tier.merge jobs names a job twice"),
        ("[tier.merge]\n", "[tier.merge]\nextra = 1\n", "tier.merge has unknown key 'extra'"),
        ("[tier.merge]\n", "[tier.weekly]\nx = 1\n[tier.merge]\n", "unknown table 'tier.weekly'"),
        (_MERGE_LIST, "tier = 3\n", "tier is a table of tiers"),
        (_MERGE_LIST, "[tier]\nmerge = 3\n", "tier.merge is a table holding jobs"),
    ],
)
def test_a_malformed_merge_list_is_refused(old: str, new: str, expect: str) -> None:
    assert expect in " ".join(_problems(_edit(old, new)))


def test_a_pytest_n_must_equal_workers() -> None:
    text = _edit(
        'timeout_min = 10\ncommand = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]',
        'timeout_min = 10\nworkers = 4\ncommand = [{ argv = ["uv", "run", "pytest", "-n", "8",'
        ' "tests/rails"] }]',
    )
    assert "rails: a step passes pytest -n 8, and workers is 4" in _problems(text)


@pytest.mark.parametrize(
    "spelling", [["-n", "4"], ["-n4"], ["--numprocesses", "4"], ["--numprocesses=4"]]
)
def test_every_spelling_of_pytest_n_is_read(spelling: list[str]) -> None:
    step = catalogue.Step(("uv", "run", "pytest", *spelling, "tests"))
    assert catalogue.pytest_workers(step) == ["4"]


def test_a_pytest_n_without_workers_is_refused() -> None:
    text = _edit('"pytest", "tests/rails"]', '"pytest", "-n", "4", "tests/rails"]')
    assert "rails: a step passes pytest -n, so workers is required" in _problems(text)


def test_workers_that_reach_nothing_are_refused() -> None:
    text = _edit("timeout_min = 10\n", "timeout_min = 10\nworkers = 2\n")
    assert any("workers is 2, and no step passes pytest -n" in p for p in _problems(text))


def test_workers_env_carries_workers_without_a_pytest_step() -> None:
    text = _edit(
        "timeout_min = 10\n", 'timeout_min = 10\nworkers = "auto"\nworkers_env = "TOOL_WORKERS"\n'
    )
    job = catalogue.load(text).job("rails")
    assert job.workers == "auto" and job.workers_env == "TOOL_WORKERS"


@pytest.mark.parametrize(
    ("fields", "expect"),
    [
        ('workers_env = "VITEST"\n', "workers_env is set, so workers is required"),
        ('workers = 2\nworkers_env = "not a name"\n', "workers_env is an environment variable"),
    ],
)
def test_a_bad_workers_env_is_refused(fields: str, expect: str) -> None:
    text = _edit("timeout_min = 10\n", f"timeout_min = 10\n{fields}")
    assert any(expect in p for p in _problems(text))


@pytest.mark.parametrize(
    "argv",
    [
        '["uv", "sync", "--locked", "--extra", "dev"]',
        '["npm", "ci"]',
        '["npx", "playwright", "install", "--with-deps", "chromium"]',
    ],
)
def test_an_install_as_a_step_is_refused(argv: str) -> None:
    text = _edit(
        'command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]',
        f'command = [{{ argv = {argv} }}, {{ argv = ["uv", "run", "pytest", "tests/rails"] }}]',
    )
    assert any("rails: command step 1 is an install" in p for p in _problems(text))


@pytest.mark.parametrize(
    ("fields", "expect"),
    [
        ('env = { HOME = "/elsewhere" }\n', "env name 'HOME' is the runner's"),
        ('env = { PERSONALCLAW_HOME = "/elsewhere" }\n', "env name 'PERSONALCLAW_HOME' is the"),
        ('env = { GIT_CONFIG_GLOBAL = "/elsewhere" }\n', "env name 'GIT_CONFIG_GLOBAL' is the"),
        ('env = { PATH = "/elsewhere" }\n', "env name 'PATH' is the runner's"),
        ('workers = "auto"\nworkers_env = "HOME"\n', "workers_env 'HOME' is the runner's"),
        ('workers = "auto"\nworkers_env = "LC_ALL"\n', "workers_env 'LC_ALL' is the runner's"),
    ],
)
def test_a_job_env_or_workers_env_naming_a_runner_variable_is_refused(
    fields: str, expect: str
) -> None:
    text = _edit("timeout_min = 10\n", f"timeout_min = 10\n{fields}")
    assert any(expect in p for p in _problems(text)), _problems(text)


@pytest.mark.parametrize("name", ["TMPDIR", "GIT_CONFIG_COUNT", "PERSONALCLAW_HOME"])
def test_a_step_env_naming_a_runner_variable_is_refused(name: str) -> None:
    text = _edit(
        '"pytest", "tests/rails"] }]', f'"pytest", "tests/rails"], env = {{ {name} = "x" }} }}]'
    )
    assert any(f"command step 1 env name {name!r} is the runner's" in p for p in _problems(text))


def test_a_job_env_of_the_jobs_own_loads() -> None:
    text = _edit("timeout_min = 10\n", 'timeout_min = 10\nenv = { CI = "true", PC_X = "1" }\n')
    assert dict(catalogue.load(text).job("rails").env) == {"CI": "true", "PC_X": "1"}


def test_a_cap_that_leaves_a_local_run_no_time_is_refused() -> None:
    text = _edit("timeout_min = 10\n", "timeout_min = 1\n")
    assert "rails: timeout_min is 2 or more" in " ".join(_problems(text))
    assert catalogue.load(_edit("timeout_min = 10\n", "timeout_min = 2\n")).job("rails")
