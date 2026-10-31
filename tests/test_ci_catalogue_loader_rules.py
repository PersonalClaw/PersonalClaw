"""The catalogue loader's rules on external inputs, the merge tier's list, the worker count and
installs (tools/ci/catalogue.py). Each rule is one refused catalogue and one that loads."""

from __future__ import annotations

import pytest

from tools.ci import catalogue, jobs

_BASE = """
[tier.merge]
jobs = ["rails", "python-suite", "python-suite-macos", "security"]

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

[[job]]
id = "security"
tiers = ["merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
timeout_min = 25
command = [{ argv = ["uv", "run", "pytest", "tests/security"] }]
"""


_MERGE_LIST = '[tier.merge]\njobs = ["rails", "python-suite", "python-suite-macos", "security"]\n'


def _edit(old: str, new: str) -> str:
    assert old in _BASE, old
    return _BASE.replace(old, new, 1)


def _problems(text: str) -> list[str]:
    with pytest.raises(catalogue.CatalogueError) as caught:
        catalogue.load(text)
    return caught.value.problems


def test_the_base_loads_with_its_merge_list() -> None:
    cat = catalogue.load(_BASE)
    assert cat.merge_jobs == ("rails", "python-suite", "python-suite-macos", "security")


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
    text = _edit(
        '[tier.merge]\njobs = ["rails", "python-suite", "python-suite-macos", "security"]\n', ""
    )
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


# ── the jobs a push to main runs whatever the tree check answers ─────────────────────────────


def _every_push(names: str) -> str:
    return _edit(_MERGE_LIST, f"{_MERGE_LIST}every_push = {names}\n")


def test_the_jobs_every_push_runs_load_as_listed() -> None:
    assert catalogue.load(_every_push('["security", "rails"]')).every_push == ("security", "rails")


def test_a_catalogue_naming_no_every_push_list_loads_with_none() -> None:
    assert catalogue.load(_BASE).every_push is None


@pytest.mark.parametrize(
    ("names", "expect"),
    [
        ('["lint", "security"]', "every_push lists lint, which is no job the merge tier runs"),
        ('["security", "security"]', "tier.merge every_push names a job twice"),
        ('"security"', "tier.merge every_push is a list of job ids"),
        ('[3, "security"]', "tier.merge every_push is a list of job ids"),
        (
            '["rails"]',
            "every_push leaves out security, so a push whose tree a pull request's run tested"
            " would skip it",
        ),
        ("[]", "every_push leaves out security"),
    ],
)
def test_an_every_push_list_that_is_not_the_merge_tiers_is_refused(names: str, expect: str) -> None:
    assert expect in " ".join(_problems(_every_push(names)))


def test_every_push_holds_apps_contract_when_the_merge_tier_runs_it() -> None:
    # The owner's rule (2026-10-19): every push to main runs the security corpus and
    # apps-contract, whatever the tree check answers.
    assert catalogue.EVERY_PUSH_JOBS == ("security", "apps-contract")
    text = _BASE.replace(
        '"security"]\n', '"security", "apps-contract"]\nevery_push = ["security"]\n', 1
    )
    text += """
[[job]]
id = "apps-contract"
tiers = ["merge"]
inputs = [":(glob)**"]
external_inputs = ["apps-partner"]
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
timeout_min = 15
command = [{ argv = ["uv", "run", "pytest", "tests/apps"] }]
"""
    assert "every_push leaves out apps-contract" in " ".join(_problems(text))
    loaded = catalogue.load(
        text.replace('every_push = ["security"]', 'every_push = ["security", "apps-contract"]')
    )
    assert loaded.every_push == ("security", "apps-contract")


# ── the entry tier: what a pull request's run passes to enter the merge queue ─────────────

_RAILS_CHECKS = """
[[job]]
id = "rails-checks"
tiers = ["change", "entry", "merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
reuse = "tree"
timeout_min = 5
command = [{ argv = ["uv", "run", "python", "-m", "tools.ci", "rails", "check"] }]
"""

_ENTRY_LIST = '[tier.entry]\njobs = ["rails", "rails-checks", "python-suite", "security"]\n'


def _tiers(text: str, job_id: str, tiers: str) -> str:
    """``text`` with the entry ``job_id`` claiming ``tiers``."""
    head, *entries = text.split("\n[[job]]\n")
    edited = [
        (
            "\n".join(
                f"tiers = {tiers}" if line.startswith("tiers = ") else line
                for line in e.split("\n")
            )
            if e.startswith(f'id = "{job_id}"\n')
            else e
        )
        for e in entries
    ]
    assert edited != entries, job_id
    return "\n[[job]]\n".join([head, *edited])


def _queued() -> str:
    """The base with rails-checks and an entry tier: the merge tier less the macOS suite."""
    text = _edit(
        _MERGE_LIST, _MERGE_LIST.replace('"rails", ', '"rails", "rails-checks", ') + _ENTRY_LIST
    )
    text += _RAILS_CHECKS
    text = _tiers(text, "rails", '["change", "entry", "merge"]')
    for job_id in ("python-suite", "security"):
        text = _tiers(text, job_id, '["entry", "merge"]')
    return text


_QUEUED = _queued()


def test_the_entry_list_loads_as_listed() -> None:
    cat = catalogue.load(_QUEUED)
    assert cat.entry_jobs == ("rails", "rails-checks", "python-suite", "security")
    assert [j.id for j in cat.tier("entry")] == [
        "rails",
        "python-suite",
        "security",
        "rails-checks",
    ]


def test_a_catalogue_naming_no_entry_list_loads_with_none() -> None:
    assert catalogue.load(_BASE).entry_jobs is None


def test_a_job_claiming_entry_without_the_list_is_refused() -> None:
    text = _QUEUED.replace(_ENTRY_LIST, "", 1)
    assert (
        "python-suite: its tiers claim entry, and the catalogue has no [tier.entry] jobs"
        " list" in _problems(text)
    )


def test_an_entry_job_the_list_leaves_out_is_refused() -> None:
    text = _QUEUED.replace('"python-suite", "security"]\n', '"security"]\n', 1)
    assert "python-suite: its tiers claim entry, and [tier.entry] does not list it" in (
        _problems(text)
    )


def test_a_listed_job_that_does_not_claim_entry_is_refused() -> None:
    text = _tiers(_QUEUED, "python-suite", '["merge"]')
    assert "python-suite: [tier.entry] lists it, and it is no job whose tiers claim entry" in (
        _problems(text)
    )


def test_an_entry_job_outside_the_merge_tier_is_refused() -> None:
    """The merge tier stays the whole gate: the queue's run holds every job the entry tier does."""
    text = _tiers(_QUEUED, "rails", '["change", "entry"]')
    text = text.replace(
        'jobs = ["rails", "rails-checks", "python-suite", "python-suite-macos"',
        'jobs = ["rails-checks", "python-suite", "python-suite-macos"',
        1,
    )
    assert (
        "rails: its tiers claim entry and not merge, and the queue's run must hold every job a"
        " pull request passed to enter it" in _problems(text)
    )


def test_the_jobs_the_entry_tier_must_hold_are_the_rails_their_check_and_the_corpus() -> None:
    assert catalogue.ENTRY_NEEDS_JOBS == {
        "rails": "every rail",
        "rails-checks": "the check that holds every rail to one leg",
        "security": "the security corpus",
    }


@pytest.mark.parametrize("job_id", sorted(catalogue.ENTRY_NEEDS_JOBS))
def test_an_entry_tier_without_the_rails_their_check_or_the_corpus_is_refused(
    job_id: str,
) -> None:
    """Each leaves the tier and its list, staying in the merge tier, and the catalogue is refused
    by name: a pull request never enters the queue without it."""
    tiers = '["change", "merge"]' if job_id != "security" else '["merge"]'
    text = _tiers(_QUEUED, job_id, tiers)
    text = text.replace(
        _ENTRY_LIST,
        _ENTRY_LIST.replace(f'"{job_id}", ', "").replace(f', "{job_id}"', ""),
        1,
    )
    what = catalogue.ENTRY_NEEDS_JOBS[job_id]
    assert _problems(text) == [
        f"the entry tier lacks {job_id}, {what}, so a pull request could enter the queue"
        " without it"
    ]


@pytest.mark.parametrize(
    ("new", "expect"),
    [
        ("[tier.entry]\njobs = []\n", "tier.entry jobs is a non-empty list of job ids"),
        (
            '[tier.entry]\njobs = "rails"\n',
            "tier.entry jobs is a non-empty list of job ids",
        ),
        (
            _ENTRY_LIST.replace('"rails", ', '"rails", "rails", '),
            "tier.entry jobs names a job twice",
        ),
        (_ENTRY_LIST + "every_push = []\n", "tier.entry has unknown key 'every_push'"),
    ],
)
def test_a_malformed_entry_list_is_refused(new: str, expect: str) -> None:
    assert expect in " ".join(_problems(_QUEUED.replace(_ENTRY_LIST, new, 1)))


def test_an_entry_tier_that_is_no_table_is_refused() -> None:
    text = _QUEUED.replace(_ENTRY_LIST, "", 1).replace(
        "[tier.merge]\n", "[tier]\nentry = 3\n[tier.merge]\n", 1
    )
    assert "tier.entry is a table holding jobs" in " ".join(_problems(text))


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


@pytest.mark.parametrize("name", [jobs.SANDBOX_HELPER_ENV, jobs.JOB_PYTHON_ENV])
def test_an_env_naming_what_run_job_gives_is_refused(name: str) -> None:
    """The sandbox helper's marker comes from run-job's own reading of the runner
    (tools/ci/jobs.py SANDBOX_HELPER_ENV); an entry that set it would claim a GitHub-hosted
    runner wherever it ran. The job's Python is its venv's (JOB_PYTHON_ENV); an entry that set
    it would run the job's commands on another."""
    assert set(catalogue.RUN_JOB_SETS) == {jobs.SANDBOX_HELPER_ENV, jobs.JOB_PYTHON_ENV}
    job = _edit("timeout_min = 10\n", f'timeout_min = 10\nenv = {{ {name} = "x" }}\n')
    assert any(f"env name {name!r} is run-job's" in p for p in _problems(job)), _problems(job)
    step = _edit(
        '"pytest", "tests/rails"] }]',
        f'"pytest", "tests/rails"], env = {{ {name} = "x" }} }}]',
    )
    assert any(f"command step 1 env name {name!r} is run-job's" in p for p in _problems(step))


def test_a_job_env_of_the_jobs_own_loads() -> None:
    text = _edit("timeout_min = 10\n", 'timeout_min = 10\nenv = { CI = "true", PC_X = "1" }\n')
    assert dict(catalogue.load(text).job("rails").env) == {"CI": "true", "PC_X": "1"}


def test_a_cap_that_leaves_a_local_run_no_time_is_refused() -> None:
    text = _edit("timeout_min = 10\n", "timeout_min = 1\n")
    assert "rails: timeout_min is 2 or more" in " ".join(_problems(text))
    assert catalogue.load(_edit("timeout_min = 10\n", "timeout_min = 2\n")).job("rails")
