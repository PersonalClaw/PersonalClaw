"""The source-tree checks split from
``tests/test_ci_junit_artifacts.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import pytest
import real_home_guard
import yaml
from ci_catalogue_steps import read
from ci_junit_artifacts_shared import (
    _REPO,
    _REPORT_DIR,
    jobs,
    junit_uploads,
    key,
    pytest_commands,
    steps,
    with_key,
)

from tools.ci import catalogue

_WORKFLOWS = _REPO / ".github" / "workflows"


_CI = _WORKFLOWS / "ci.yml"


_BREADTH = _WORKFLOWS / "breadth.yml"


#: Job ids old enough in each workflow that their absence means the parser broke rather
#: than that CI changed. The vacuity floor for :func:`jobs`.
_LONG_STANDING = {
    "ci.yml": frozenset({"lint", "test-shard", "test", "web", "rails", "harness", "client"}),
    "breadth.yml": frozenset({"coverage-shard", "coverage", "audit", "security", "install-smoke"}),
}


@pytest.fixture(scope="module")
def workflows() -> dict[str, dict[str, str]]:
    parsed = {}
    for path in (_CI, _BREADTH):
        assert path.is_file(), f"{path} moved — re-point this rail"
        parsed[path.name] = jobs(path.read_text(encoding="utf-8"))
    return parsed


def _pytest_jobs(parsed: dict[str, str]) -> dict[str, str]:
    return {
        name: block
        for name, block in parsed.items()
        if any(pytest_commands(s) for s in steps(block))
    }


def test_the_job_scan_is_not_vacuous(workflows: dict[str, dict[str, str]]) -> None:
    """If :func:`jobs` returned nothing, every rail below would pass on an empty set."""
    for filename, expected in _LONG_STANDING.items():
        missing = expected - set(workflows[filename])
        assert not missing, f"the job scan lost long-standing {filename} jobs {sorted(missing)}"


def test_the_pytest_step_scan_finds_every_known_leg(workflows: dict[str, dict[str, str]]) -> None:
    """The rails are about pytest steps, so a scan that found none would be self-satisfying.

    Named by JOB rather than by count: a new pytest job should extend this list (and pick up
    the rails with it), not silently shrink what they cover.
    """
    ci = set(_pytest_jobs(workflows["ci.yml"]))
    full = set(_pytest_jobs(workflows["breadth.yml"]))
    assert ci >= {
        "lint",
        "test-shard",
        "test-macos",
        "browse-live",
        "security",
        "rails",
        "client",
    }, sorted(ci)
    assert full >= {
        "ubuntu",
        "macos",
        "arm",
        "canary",
        "security",
        "coverage-shard",
    }, sorted(full)


@pytest.mark.parametrize("filename", ["ci.yml", "breadth.yml"])
def test_every_pytest_invocation_writes_a_junit_report(
    workflows: dict[str, dict[str, str]], filename: str
) -> None:
    """A pytest step with no ``--junitxml`` has nothing to publish."""
    for job, block in _pytest_jobs(workflows[filename]).items():
        for step in steps(block):
            for command in pytest_commands(step):
                assert f"--junitxml={_REPORT_DIR}" in command, (
                    f"{filename} job `{job}` invokes pytest without "
                    f"`--junitxml={_REPORT_DIR}…`, so a red run publishes no report and "
                    f"classifying it costs a full log read or a blind re-run: {command}"
                )


@pytest.mark.parametrize("filename", ["ci.yml", "breadth.yml"])
def test_every_pytest_job_uploads_its_report_even_when_red(
    workflows: dict[str, dict[str, str]], filename: str
) -> None:
    """And uploads it on FAILURE — a ``success()``-gated report is the wrong half."""
    for job, block in _pytest_jobs(workflows[filename]).items():
        uploads = junit_uploads(block)
        assert uploads, (
            f"{filename} job `{job}` writes a JUnit report but no step uploads it, so the "
            "XML dies with the runner and the red is as opaque as before."
        )
        for upload in uploads:
            condition = key(upload, "if") or "success()"
            assert re.search(r"\balways\(\)|\bfailure\(\)|!\s*cancelled\(\)", condition), (
                f"{filename} job `{job}` uploads its JUnit report under `if: {condition}`. "
                "The default is success(), which publishes the report only on the runs that "
                "do not need it. Use `if: always()`."
            )


@pytest.mark.parametrize("filename", ["ci.yml", "breadth.yml"])
def test_a_matrix_job_names_its_artifact_per_leg(
    workflows: dict[str, dict[str, str]], filename: str
) -> None:
    """Artifact names are unique per run, so N legs sharing one name collapse to one upload.

    ci.yml's ``test-shard`` runs 4 legs and breadth.yml's ``macos`` runs 6; a fixed name
    would keep whichever finished last, which is reliably not the one that failed.
    """
    matrixed = {
        job: block
        for job, block in _pytest_jobs(workflows[filename]).items()
        if re.search(r"^ {6}matrix:\s*$", block, flags=re.MULTILINE)
    }
    # Vacuity floor, per file: the sharded job is named, not counted, so a `matrix:` indent
    # change reds HERE (parser broke) rather than quietly emptying the rail below.
    expected = {"ci.yml": "test-shard", "breadth.yml": "macos"}[filename]
    assert expected in matrixed, (
        f"the matrix scan did not find {filename}'s `{expected}` job (found: "
        f"{sorted(matrixed)}) — the parser broke, not the workflow."
    )
    for job, block in matrixed.items():
        for upload in junit_uploads(block):
            name = with_key(upload, "name") or ""
            assert "${{ matrix." in name, (
                f"{filename} job `{job}` uploads every matrix leg's JUnit report as "
                f"{name!r}. Without a `${{{{ matrix.… }}}}` coordinate the legs collide and "
                "only one report survives."
            )


def test_the_real_home_guard_report_is_uploaded_per_shard(
    workflows: dict[str, dict[str, str]],
) -> None:
    """The one red the JUnit report structurally CANNOT explain still reaches an artifact.

    The real-home guard fails the test that touches the real home, which the XML carries. What
    it cannot pin on a test — a thread that outlived the test that started it, an import during
    collection — fails the run from ``pytest_sessionfinish`` by assigning ``session.exitstatus``:
    no testcase fails, so the XML reads ``failures=0`` and the shard surfaces as a bare
    process-level exit naming nothing. MEASURED on the walk-based rail before it: that
    signature cost three shifts of guessing on one PR, because the offending paths lived only
    on STDOUT, where the #2720 truncation class can eat them (#3386).

    The expected path is read from :data:`real_home_guard.REPORT_RELPATH` rather than
    spelled here, so moving the writer without moving the upload reds this test instead
    of silently publishing a file that no longer exists.
    """
    block = workflows["ci.yml"]["test-shard"]
    wanted = real_home_guard.REPORT_RELPATH
    assert wanted.startswith(_REPORT_DIR), (
        f"the guard report is written to {wanted!r}, outside {_REPORT_DIR!r} — the only "
        "directory .gitignore covers and CI uploads from."
    )
    uploads = [
        s
        for s in steps(block)
        if "actions/upload-artifact" in s and (with_key(s, "path") or "") == wanted
    ]
    assert uploads, (
        f"ci.yml job `test-shard` runs the whole suite but no step uploads {wanted}, so a "
        "session-level guard red is unattributable from any artifact."
    )
    for upload in uploads:
        condition = key(upload, "if") or "success()"
        assert re.search(r"\balways\(\)|\bfailure\(\)|!\s*cancelled\(\)", condition), (
            f"the guard report is uploaded under `if: {condition}`, which skips exactly the "
            "failing runs it exists for. Use `if: always()`."
        )
        name = with_key(upload, "name") or ""
        assert "${{ matrix." in name, (
            f"all four shards upload the guard report as {name!r}; without a matrix "
            "coordinate they collide and only the last leg to finish survives."
        )


# ── The tier workflows' half (#3915) ──────────────────────────────────────────────────────────
#
# pr.yml's and merge.yml's stage jobs run the catalogue's steps through run-job and upload what
# they wrote, one step for every entry. So the rules above hold there as: every catalogue pytest
# step writes its report under reports/ and its job declares it (run-job then fails a job that
# wrote none), and every stage job uploads reports/ whatever the entry answered, under the
# entry's own name. The flip deletes ci.yml and its parameter above; these stay.

#: The stage jobs' matrix: a part of the plan's matrix, one entry a leg.
_STAGE_MATRIX = re.compile(r"\$\{\{\s*fromJSON\(needs\.changes\.outputs\.[\w-]+\)\s*\}\}")


def _stage_jobs(filename: str) -> dict[str, dict]:
    doc = yaml.safe_load((_WORKFLOWS / filename).read_text(encoding="utf-8"))
    return {
        name: job
        for name, job in (doc.get("jobs") or {}).items()
        if _STAGE_MATRIX.fullmatch(str(((job or {}).get("strategy") or {}).get("matrix", "")))
    }


def _reports_uploads(job: dict) -> list[dict]:
    return [
        s
        for s in job.get("steps") or []
        if "actions/upload-artifact" in str(s.get("uses", ""))
        and _REPORT_DIR in str((s.get("with") or {}).get("path", "")).split()
    ]


def test_every_catalogue_pytest_step_writes_a_junit_report_its_job_declares() -> None:
    steps_seen = 0
    for job in read().jobs:
        for step in job.command:
            args = catalogue.pytest_args(step.argv)
            if args is None:
                continue
            steps_seen += 1
            written = [w.split("=", 1)[1] for w in args if w.startswith("--junitxml=")]
            assert len(written) == 1 and written[0].startswith(_REPORT_DIR), (
                f"{job.id}'s pytest step writes {written}, not one report under {_REPORT_DIR}:"
                f" a red run publishes no report ({step.render()})"
            )
            assert written[0] in job.junit, (
                f"{job.id} writes {written[0]} but its `junit` names {list(job.junit)}, so"
                " run-job never checks the report was written"
            )
    assert steps_seen >= 20, f"read only {steps_seen} catalogue pytest steps: the read broke"


@pytest.mark.parametrize("filename", ["pr.yml", "merge.yml"])
def test_every_stage_job_uploads_the_entrys_reports_per_entry_even_when_red(
    filename: str,
) -> None:
    stage = _stage_jobs(filename)
    assert stage, f"{filename} has no stage job this scan can find: the scan broke"
    for name, job in stage.items():
        uploads = _reports_uploads(job)
        assert (
            len(uploads) == 1
        ), f"{filename} job `{name}` uploads {_REPORT_DIR} {len(uploads)} times"
        condition = str(uploads[0].get("if") or "success()")
        assert re.search(r"\balways\(\)|!\s*cancelled\(\)", condition), (
            f"{filename} job `{name}` uploads its reports under `if: {condition}`, which skips"
            " the red runs they exist for"
        )
        artifact = str((uploads[0].get("with") or {}).get("name", ""))
        assert "${{ matrix.slug }}" in artifact, (
            f"{filename} job `{name}` names its reports {artifact!r}: without the entry's slug"
            " its legs collide and one report survives"
        )


def test_the_real_home_guard_report_reaches_a_stage_jobs_upload() -> None:
    """The guard's report sits under reports/, which every stage job uploads."""
    assert real_home_guard.REPORT_RELPATH.startswith(_REPORT_DIR)
    for filename in ("pr.yml", "merge.yml"):
        for name, job in _stage_jobs(filename).items():
            assert _reports_uploads(job), f"{filename} job `{name}` uploads no {_REPORT_DIR}"


def test_a_stage_job_that_uploads_only_on_success_or_under_one_name_is_named() -> None:
    """The positive controls, through the upload reader the checks above use."""
    job = next(iter(_stage_jobs("merge.yml").values()))
    upload = _reports_uploads(job)[0]
    assert re.search(r"\balways\(\)", str(upload.get("if")))
    gated = {**job, "steps": [{**upload, "if": "${{ success() }}"}]}
    assert not re.search(r"\balways\(\)|!\s*cancelled\(\)", str(_reports_uploads(gated)[0]["if"]))
    elsewhere = {
        **job,
        "steps": [{**upload, "with": {**upload["with"], "path": "web/test-results/"}}],
    }
    assert _reports_uploads(elsewhere) == []
