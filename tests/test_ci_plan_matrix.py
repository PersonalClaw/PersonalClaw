"""The plan as a job matrix (tools/ci/plan.py ``matrix`` and ``plan --matrix``): one entry per
planned job and shard, keyed as ``run-job`` keys its record, so a workflow's test stage runs
exactly what the plan names and its results stage can tell which record is missing."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import catalogue, jobs
from tools.ci import plan as planner
from tools.ci import verdict

pytestmark = pytest.mark.security

_TEXT = """
[tier.merge]
jobs = ["rails", "python-suite", "python-suite-macos", "security", "web-build", "web"]

[[job]]
id = "rails"
tiers = ["change", "merge"]
inputs = ["src/", "tests/"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
python = "3.12.15"
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
python = "3.12.15"
timeout_min = 55
shards = 3
command = [{ argv = ["uv", "run", "pytest", "tests", "{shard}"] }]

[[job]]
id = "python-suite-macos"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
platform = "macos-26"
python = "3.13.16"
timeout_min = 50
shards = 2
command = [{ argv = ["uv", "run", "pytest", "tests", "{shard}"] }]

[[job]]
id = "security"
tiers = ["merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
python = "3.12.15"
timeout_min = 25
command = [{ argv = ["uv", "run", "pytest", "tests/security"] }]

[[job]]
id = "web-build"
tiers = ["merge"]
inputs = ["web/"]
external_inputs = []
toolchain = ["node"]
cache = []
security = "ordinary"
timeout_min = 20
needs_node = true
command = [{ argv = ["npm", "run", "build"] }]

[[job]]
id = "web"
tiers = ["merge"]
inputs = ["web/"]
external_inputs = []
toolchain = ["node", "chromium"]
cache = []
security = "ordinary"
timeout_min = 30
needs_node = true
needs_browser = true
after = ["web-build"]
command = [{ argv = ["npx", "playwright", "test"] }]
"""


def _cat(text: str = _TEXT) -> catalogue.Catalogue:
    return catalogue.load(text)


def _matrix(paths: list[str] | None, text: str = _TEXT) -> dict:
    cat = _cat(text)
    return planner.matrix(cat, planner.plan(cat, "merge", paths, overrides=()))


def test_each_planned_job_gets_one_entry_per_shard_keyed_as_run_job_keys_its_record() -> None:
    m = _matrix(None)
    assert [e["key"] for e in m["include"]] == [
        "rails",
        "python-suite (1/3)", "python-suite (2/3)", "python-suite (3/3)",
        "python-suite-macos (1/2)", "python-suite-macos (2/2)",
        "security",
        "web-build",
        "web",
    ]  # fmt: skip
    assert m["keys"] == [e["key"] for e in m["include"]]


def test_an_entry_carries_what_its_job_needs_to_run() -> None:
    by_key = {e["key"]: e for e in _matrix(None)["include"]}
    assert by_key["python-suite (2/3)"] == {
        "id": "python-suite",
        "key": "python-suite (2/3)",
        "shard": "2/3",
        "runs-on": "ubuntu-24.04",
        "timeout": 55,
        "python": "3.12.15",
        "node": False,
        "browser": False,
        "container": "",
        "after": [],
        "stage": 1,
        "slug": "python-suite-2-3",
        "history": False,
        "programs": [],
        "external": [],
    }
    assert by_key["python-suite-macos (1/2)"]["runs-on"] == "macos-26"
    assert by_key["web"]["shard"] == "" and by_key["web"]["python"] == ""
    assert by_key["web"]["node"] is True and by_key["web"]["browser"] is True
    # Read apart: a job that needs Node and no browser gets Node and no browser.
    assert by_key["web-build"]["node"] is True and by_key["web-build"]["browser"] is False


def test_an_entry_waits_for_the_planned_jobs_whose_output_it_uses() -> None:
    # A strategy.matrix starts every entry at once, so an entry whose job uses another's output
    # is in a later stage than that job's: one matrix per stage keeps the catalogue's `after`.
    by_key = {e["key"]: e for e in _matrix(None)["include"]}
    assert (by_key["web-build"]["stage"], by_key["web-build"]["after"]) == (1, [])
    assert (by_key["web"]["stage"], by_key["web"]["after"]) == (2, ["web-build"])


def test_after_naming_an_unplanned_job_waits_for_nothing() -> None:
    cat = _cat()
    narrowed = planner.Plan("merge", False, ("web",), {"web": ["x"]}, ())
    [entry] = planner.matrix(cat, narrowed)["include"]
    assert (entry["stage"], entry["after"]) == (1, [])


def test_a_cycle_of_after_is_refused() -> None:
    text = _TEXT.replace(
        'command = [{ argv = ["npm", "run", "build"] }]',
        'after = ["web"]\ncommand = [{ argv = ["npm", "run", "build"] }]',
    )
    with pytest.raises(ValueError, match="cycle"):
        _matrix(None, text)


def test_an_unplanned_job_has_no_entry() -> None:
    cat = _cat()
    narrowed = planner.Plan("merge", False, ("rails",), {"rails": ["x"]}, ())
    assert planner.matrix(cat, narrowed)["keys"] == ["rails"]


def test_a_security_job_is_in_the_matrix_whatever_the_diff() -> None:
    # The planner plans every security job on any diff, so the suite's shards and the corpus
    # reach the matrix for a web-only change too: the security evidence always runs on a
    # candidate.
    keys = _matrix(["web/a.ts"])["keys"]
    assert {
        "python-suite (1/3)",
        "python-suite (2/3)",
        "python-suite (3/3)",
        "python-suite-macos (1/2)",
        "python-suite-macos (2/2)",
        "security",
    } <= set(keys)
    assert "web" in keys


def test_a_planned_id_the_catalogue_lacks_is_refused() -> None:
    cat = _cat()
    bad = planner.Plan("merge", False, ("rails", "gone"), {}, ())
    with pytest.raises(ValueError, match="gone"):
        planner.matrix(cat, bad)


def test_the_matrix_key_is_run_jobs_record_key_for_every_shard() -> None:
    for job in _cat().jobs:
        shards = range(1, job.shards + 1) if job.shards > 1 else [None]
        for shard in shards:
            assert planner.matrix_key(job, shard) == jobs.key_of(job, shard)


def test_the_real_merge_tier_reaches_every_merge_job_and_shard() -> None:
    root = catalogue.REPO
    path = root / catalogue.CATALOGUE
    cat = catalogue.load(path.read_text(encoding="utf-8"), str(path))
    m = planner.matrix(cat, planner.plan(cat, "merge", None, overrides=()))
    want = [
        jobs.key_of(j, s)
        for j in cat.tier("merge")
        for s in (range(1, j.shards + 1) if j.shards > 1 else [None])
    ]
    assert m["keys"] == want
    assert {e["id"] for e in m["include"]} == set(cat.merge_jobs)
    assert all(e["timeout"] >= 1 for e in m["include"]), "every job has a timeout"
    by_id = {j.id: j for j in cat.jobs}
    for e in m["include"]:
        job = by_id[e["id"]]
        assert (e["node"], e["browser"]) == (job.needs_node, job.needs_browser), e["key"]
        assert e["container"] == (job.container or ""), e["key"]


def test_the_real_merge_tier_puts_each_job_after_the_jobs_whose_output_it_uses() -> None:
    root = catalogue.REPO
    path = root / catalogue.CATALOGUE
    cat = catalogue.load(path.read_text(encoding="utf-8"), str(path))
    entries = planner.matrix(cat, planner.plan(cat, "merge", None, overrides=()))["include"]
    stage = {e["id"]: e["stage"] for e in entries}
    by_id = {j.id: j for j in cat.jobs}
    for e in entries:
        assert e["after"] == [a for a in by_id[e["id"]].after if a in stage], e["key"]
        assert all(stage[a] < e["stage"] for a in e["after"]), e["key"]
    # web-build's dist is what package and the e2e legs read, and e2e and the UI quality check
    # each merge the legs' blobs.
    assert stage["web-build"] == 1
    assert {stage["package"], stage["e2e-shard"], stage["e2e-stateful"]} == {2}
    assert {stage["e2e"], stage["ui-quality"]} == {3}


def _real() -> catalogue.Catalogue:
    path = catalogue.REPO / catalogue.CATALOGUE
    return catalogue.load(path.read_text(encoding="utf-8"), str(path))


def _real_entries(tier: str) -> list[dict]:
    cat = _real()
    return planner.matrix(cat, planner.plan(cat, tier, None, overrides=()))["include"]


@pytest.mark.parametrize("tier", ["change", "merge", "breadth"])
def test_an_entrys_slug_names_the_artifact_its_record_is_uploaded_under(tier: str) -> None:
    # A workflow cannot slugify a key, so the entry carries the slug the verdict's artifact name
    # is built from: the test stage uploads `ci-record-<slug>-<run id>-<attempt>`, and the
    # results stage reads a record only from the folder record_artifact names for its key.
    for e in _real_entries(tier):
        for run_id, attempt in ((1, 1), (40123456789, 3)):
            assert verdict.record_artifact(e["key"], run_id=run_id, attempt=attempt) == (
                f"{verdict.RECORD_ARTIFACT_PREFIX}{e['slug']}-{run_id}-{attempt}"
            ), e["key"]


def test_the_slug_is_the_record_artifacts_for_keys_of_every_shape() -> None:
    slugs = {
        "static": "static",
        "python-suite (2/4)": "python-suite-2-4",
        "e2e-shard (1/2)": "e2e-shard-1-2",
        "A_b  c": "a-b-c",
        "x/y": "x-y",
        "-Lead-": "lead",
    }
    for key, slug in slugs.items():
        assert verdict.record_slug(key) == slug, key
        assert verdict.record_artifact(key, run_id=7, attempt=1) == (
            f"{verdict.RECORD_ARTIFACT_PREFIX}{slug}-7-1"
        )
    with pytest.raises(verdict.Refused):
        verdict.record_slug("(/)")


def test_an_entry_names_the_programs_the_setup_action_puts_on_path() -> None:
    by_id = {e["id"]: e for e in _real_entries("merge")}
    # run-job installs uv-sync, npm-ci and chromium itself and finds every other program on
    # PATH, so the setup action installs the ones a runner image may lack, in the catalogue's
    # order: the installer's linters for static, tmux for the suites' real-daemon leg.
    assert by_id["static"]["programs"] == ["shellcheck", "dash"]
    assert by_id["python-suite"]["programs"] == ["tmux"]
    assert by_id["python-suite-macos"]["programs"] == ["tmux"]
    assert by_id["web-unit"]["programs"] == []


def test_every_toolchain_word_is_set_up_by_exactly_one_party() -> None:
    # uv and node by the setup action's python-version and node inputs; the installs by
    # run-job; the programs by the setup action's programs input. A word in none of them would
    # leave a job that cannot run on a runner, and a word in two would be set up twice.
    parties = [{"uv", "node"}, set(jobs.INSTALL_ARGV), set(planner.SETUP_PROGRAMS)]
    assert set().union(*parties) == set(catalogue.TOOLCHAINS)
    assert sum(len(p) for p in parties) == len(catalogue.TOOLCHAINS)
    assert set(planner.SETUP_PROGRAMS) <= set(jobs.PROGRAMS)


def test_an_entry_takes_the_whole_history_when_its_job_reads_a_base() -> None:
    by_id = {e["id"]: e for e in _real_entries("merge")}
    # apps-contract's step names {base}, which run-job reads as the merge-base with origin/main,
    # and a depth-1 checkout does not hold it. static's scan reads the whole tree, no base.
    assert by_id["apps-contract"]["history"] is True
    assert by_id["static"]["history"] is False
    assert by_id["python-suite"]["history"] is False
    assert by_id["web-build"]["history"] is False
    # A job whose step takes a diff's base, in a catalogue of its own.
    text = _TEXT.replace(
        'command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]',
        'command = [{ argv = ["uv", "run", "pytest", "tests/rails", "--base", "{base}"] }]',
    )
    by_key = {e["key"]: e for e in _matrix(None, text)["include"]}
    assert by_key["rails"]["history"] is True and by_key["security"]["history"] is False


def test_an_entry_names_its_external_inputs() -> None:
    by_id = {e["id"]: e for e in _real_entries("merge")}
    # The shard plan a suite shard reads, and the partner checkout apps-contract reads.
    assert by_id["python-suite"]["external"] == ["shard-history"]
    assert by_id["apps-contract"]["external"] == ["apps-partner", "package-index"]
    assert by_id["rails"]["external"] == []


# ── the workflow jobs a matrix is run by ─────────────────────────────────────────────────────


def _split(paths: list[str] | None = None, text: str = _TEXT) -> dict[str, list[str]]:
    jobs_ = planner.stage_jobs(_matrix(paths, text))
    return {name: [e["key"] for e in entries] for name, entries in jobs_.items()}


def test_stage_one_splits_into_what_a_later_stage_waits_for_and_the_rest() -> None:
    # `build` holds the stage-1 entries a later entry uses; `test` the stage-1 entries nothing
    # waits for, which start beside `build`; `test-<k>` stage k from 2. So the suites never
    # hold back the stage that waits on the build.
    assert _split() == {
        "build": ["web-build"],
        "test": [
            "rails",
            "python-suite (1/3)", "python-suite (2/3)", "python-suite (3/3)",
            "python-suite-macos (1/2)", "python-suite-macos (2/2)",
            "security",
        ],
        "test-2": ["web"],
    }  # fmt: skip


def test_a_stage_job_with_no_entry_is_not_named() -> None:
    cat = _cat()
    narrowed = planner.Plan("merge", False, ("rails", "web"), {"rails": ["x"], "web": ["x"]}, ())
    assert {
        name: [e["key"] for e in entries]
        for name, entries in planner.stage_jobs(planner.matrix(cat, narrowed)).items()
    } == {"test": ["rails", "web"]}


def test_the_real_tiers_split_into_their_workflows_stage_jobs() -> None:
    merge = {
        name: [e["id"] for e in entries]
        for name, entries in planner.stage_jobs({"include": _real_entries("merge")}).items()
    }
    assert list(merge) == ["build", "test", "test-2", "test-3"]
    assert merge["build"] == ["web-build"]
    assert merge["test-2"] == ["package", "e2e-shard", "e2e-shard", "e2e-stateful"]
    assert merge["test-3"] == ["e2e", "ui-quality"]
    change = planner.stage_jobs({"include": _real_entries("change")})
    assert list(change) == ["test"]
    # Every entry is in exactly one job, in plan order within it.
    for tier in ("change", "merge", "breadth"):
        entries = _real_entries(tier)
        split = planner.stage_jobs({"include": entries})
        flat = [e["key"] for job in split.values() for e in job]
        assert sorted(flat) == sorted(e["key"] for e in entries)
        for job in split.values():
            keys = [e["key"] for e in job]
            assert keys == [e["key"] for e in entries if e["key"] in keys]


def test_the_real_breadth_tier_carries_a_container_entrys_container() -> None:
    root = catalogue.REPO
    path = root / catalogue.CATALOGUE
    cat = catalogue.load(path.read_text(encoding="utf-8"), str(path))
    entries = planner.matrix(cat, planner.plan(cat, "breadth", None, overrides=()))["include"]
    [smoke] = [e for e in entries if e["id"] == "install-smoke"]
    assert smoke["container"] == cat.job("install-smoke").container
    assert smoke["container"].startswith("ubuntu:")


# ── the command ──────────────────────────────────────────────────────────────────────────────


def _cli(tmp_path: Path, *args: str, paths: str = "web/a.ts\n") -> subprocess.CompletedProcess:
    cat = tmp_path / "catalogue.toml"
    cat.write_text(_TEXT)
    (tmp_path / "areas.toml").write_text("")
    listed = tmp_path / "changed.txt"
    listed.write_text(paths)
    return subprocess.run(
        [sys.executable, "-m", "tools.ci", "plan", "merge", "--catalogue", str(cat),
         "--paths", str(listed), *args],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )  # fmt: skip


def test_the_command_prints_the_matrix_as_json(tmp_path: Path) -> None:
    done = _cli(tmp_path, "--matrix")
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert set(data) == {"include", "keys"}
    assert "web" in data["keys"]


def test_the_command_adds_the_matrix_and_keys_to_its_output_lines(tmp_path: Path) -> None:
    done = _cli(tmp_path, "--matrix", "--outputs")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert [line.split("=", 1)[0] for line in lines] == [
        "full", "jobs", "matrix", "keys", "matrix-build", "matrix-test", "matrix-test-2",
    ]  # fmt: skip
    matrix = json.loads(lines[2].split("=", 1)[1])
    # The object a workflow's `strategy.matrix` reads holds `include` alone: any other key
    # would become a matrix dimension and multiply the jobs.
    assert set(matrix) == {"include"}
    assert json.loads(lines[3].split("=", 1)[1]) == [e["key"] for e in matrix["include"]]
    # One line per stage job with an entry, each its own `include` alone, together the matrix.
    split = {line.split("=", 1)[0]: json.loads(line.split("=", 1)[1]) for line in lines[4:]}
    assert all(set(m) == {"include"} for m in split.values())
    split_keys = [e["key"] for m in split.values() for e in m["include"]]
    assert sorted(split_keys) == sorted(json.loads(lines[3].split("=", 1)[1]))
    assert [e["key"] for e in split["matrix-build"]["include"]] == ["web-build"]
    assert [e["key"] for e in split["matrix-test-2"]["include"]] == ["web"]


def test_without_matrix_the_output_lines_are_unchanged(tmp_path: Path) -> None:
    done = _cli(tmp_path, "--outputs")
    assert [line.split("=", 1)[0] for line in done.stdout.splitlines()] == ["full", "jobs"]
