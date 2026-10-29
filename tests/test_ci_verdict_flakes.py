"""The flake rule (``tools/ci/verdict.py``, ``_pass_flakes``): on a pull request's run, a run
whose only failures are at most two tests that each passed alone in a new process, in files the
pull request does not change, reads green and names them. Everything else stays red.

Every case is one planted run: its per-job records, as ``run-job`` writes them, the ``needs`` of
its results job, the run's jobs as the Actions API lists them, and the pull request's changed
paths. One shape passes; each case after it moves one thing and is red.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tools.ci import verdict

pytestmark = pytest.mark.security

HEAD = "a" * 40
CANDIDATE = verdict.Identity(HEAD, {})
RUN = 4242
ONE, TWO, THREE = "python-suite (1/3)", "python-suite (2/3)", "python-suite (3/3)"
PLAN = [ONE, TWO, THREE, "static"]
FLAKY = "tests/test_timing.py::test_it_answers_in_time"
OTHER = "tests/test_other.py::test_it_waits"
THIRD = "tests/test_third.py::test_it_resumes"


@pytest.fixture(autouse=True)
def _a_pull_requests_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test here is on a pull request's run unless it says otherwise: the command reads the
    rule's two lists on one only (``GITHUB_EVENT_NAME``), so a test that took the runner's event
    went red on a dispatch, a schedule or a push to main (breadth run 40633282629)."""
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")


PASSED_ALONE = "passed rerun alone in a new process"
PYTEST = ["uv", "run", "pytest", "-n", "4", "--junitxml=reports/junit.xml"]
RERUN = ["uv", "run", "pytest", "-n", "0", "-p", "no:cacheprovider", "-m", "not security"]
CHANGED = frozenset({"src/personalclaw/chat.py", "docs/chat.md"})


def _test(test: str, word: str | None = verdict.FLAKE, why: str = PASSED_ALONE) -> dict[str, Any]:
    return {"test": test, "rerun_result": word, "why": why}


def _record(
    key: str,
    *tests: dict[str, Any],
    exits: Sequence[int] | None = None,
    rerun_exit: int | None = 0,
    reran: bool = True,
    attempt: int = 1,
    head: str = HEAD,
    argv: Sequence[str] = RERUN,
    unstarted: int | None = None,
) -> str:
    """A shard's record: green when it names no test and no exit, else failed, with its tests
    rerun."""
    exits = exits if exits is not None else ((1,) if tests else (0,))
    failed = any(exits)
    rerun = None
    if tests:
        rerun = {
            "argv": list(argv) if reran else None,
            "exit": rerun_exit if reran else None,
            "tests": list(tests),
        }
    return verdict.job_record(
        key=key,
        result=verdict.FAILURE if failed else verdict.SUCCESS,
        identity=verdict.Identity(head, {}),
        steps=[(PYTEST, code, 30.0) for code in exits],
        attempt=attempt,
        run_id=RUN,
        rerun=rerun,
        unstarted_steps=unstarted,
    )


def _job(name: str, conclusion: str = "success", **kw: Any) -> dict[str, Any]:
    failed = conclusion == "failure"
    steps = kw.get(
        "steps",
        [
            {"name": "Runner identity", "conclusion": "success"},
            {"name": verdict.RUN_JOB_STEP, "conclusion": "failure" if failed else "success"},
            {"name": "the entry's record", "conclusion": "success"},
        ],
    )
    return {
        "name": name,
        "status": kw.get("status", "completed"),
        "conclusion": conclusion,
        "run_id": kw.get("run_id", RUN),
        "run_attempt": kw.get("run_attempt", 1),
        "steps": steps,
    }


#: The stages a plan leaves empty, as GitHub lists them: each skipped matrix job under its
#: unexpanded name, with no step and no runner (#5623's Merge run 40619732396: build, test-2 and
#: test-3).
EMPTY_STAGES = [_job("matrix.key", "skipped", steps=[]) for _ in range(3)]
#: #5623's Merge run 40619732396 as its results job listed it: every planned job passed, and the
#: verdict refused the listing for its three `matrix.key` jobs (exit 2).
LISTED_5623 = [
    *(
        _job(n)
        for n in (
            "tree-check", "shard-plan", "changes", "python-suite (1/3)", "rails (1/3)",
            "python-suite (3/3)", "python-suite-macos (2/3)", "python-suite (2/3)",
            "python-suite-macos (1/3)", "rails (2/3)", "static", "python-suite-macos (3/3)",
            "rails (3/3)",
        )
    ),
    *EMPTY_STAGES,
    *(_job(n) for n in ("rails-checks", "security", "browse-live")),
    _job("results", None, status="in_progress"),
]  # fmt: skip


def _jobs(*failed: str, extra: Sequence[dict[str, Any]] = ()) -> list[verdict.JobRun]:
    listed = [_job(n, "failure" if n in failed else "success") for n in ("tree-check", *PLAN)]
    listed += [*extra, _job("results", None, status="in_progress")]
    page = {"total_count": len(listed), "jobs": listed}
    return verdict.read_jobs(json.dumps([page]), run_id=RUN, attempt=1)


def _needs(test: str = "failure", **others: str) -> str:
    jobs = {"tree-check": "success", "changes": "success", "test": test, **others}
    return json.dumps({k: {"result": v, "outputs": {}} for k, v in jobs.items()})


def _judge(
    records: Sequence[str],
    *,
    changed: frozenset[str] | None = CHANGED,
    jobs: Sequence[verdict.JobRun] | None = None,
    needs: str | None = None,
    failed: Sequence[str] = (ONE,),
) -> verdict.Verdict:
    results = verdict.from_needs(needs or _needs(), CANDIDATE)
    keyed = {json.loads(r)["key"] for r in records}
    texts = list(records) + [_record(k) for k in PLAN if k not in keyed]
    results += verdict.from_job_records(texts)
    return verdict.decide(
        PLAN,
        results,
        CANDIDATE,
        run_id=RUN,
        changed=changed,
        jobs=_jobs(*failed) if jobs is None else jobs,
    )


def _red(judged: verdict.Verdict, says: str = "") -> None:
    assert not judged.green and not judged.flakes
    assert says in judged.flake_rule, judged.flake_rule


# ── The positive control ─────────────────────────────────────────────────────────────────────


def test_one_flake_that_passed_alone_is_green_and_named() -> None:
    judged = _judge([_record(ONE, _test(FLAKY))])
    assert judged.green and judged.reasons == ()
    assert [(t.key, t.test) for t in judged.flakes] == [(ONE, FLAKY)]
    text = verdict.summary(judged)
    assert text.startswith("### Verdict: green\n")
    assert f"- `{ONE}`: `{FLAKY}`" in text.split("Passed as flakes:")[1]
    assert verdict.flake_notices(judged) == f"::notice title=flake passed::{FLAKY} ({ONE})\n"


def test_two_flakes_in_two_shards_are_green() -> None:
    judged = _judge([_record(ONE, _test(FLAKY)), _record(TWO, _test(OTHER))], failed=(ONE, TWO))
    assert judged.green
    assert [t.test for t in judged.flakes] == [FLAKY, OTHER]


def test_a_vitest_flake_passes_and_its_setup_folder_is_read() -> None:
    test = "web/src/ui/Composer.test.tsx::it rises"
    assert _judge([_record(ONE, _test(test))]).green
    _red(
        _judge([_record(ONE, _test(test))], changed=CHANGED | {"web/src/test/setup.ts"}),
        "web/src/test/setup.ts",
    )
    _red(
        _judge([_record(ONE, _test(test))], changed=CHANGED | {"web/vitest.config.ts"}),
        "web/vitest.config.ts",
    )


# ── What the pull request changes ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("tests/test_timing.py", id="the test's own file, a new test in it too"),
        pytest.param("tests/conftest.py", id="a conftest above it"),
        pytest.param("conftest.py", id="a conftest at the root"),
        pytest.param("pyproject.toml", id="the pytest configuration"),
    ],
)
def test_a_flake_whose_run_reads_a_file_the_pull_request_changes_is_red(path: str) -> None:
    _red(_judge([_record(ONE, _test(FLAKY))], changed=CHANGED | {path}), path)


def test_a_flake_in_a_folder_beside_the_changed_conftest_still_passes() -> None:
    assert _judge([_record(ONE, _test(FLAKY))], changed=CHANGED | {"tests/web/conftest.py"}).green


@pytest.mark.parametrize(
    "changed", [None, frozenset()], ids=["no changed list", "an empty changed list"]
)
def test_without_a_readable_changed_list_nothing_passes(changed: frozenset[str] | None) -> None:
    judged = _judge([_record(ONE, _test(FLAKY))], changed=changed)
    assert not judged.green and not judged.flakes


def test_without_the_runs_jobs_nothing_passes_and_nothing_is_said() -> None:
    """A push's run and a local one give the rule neither list: it is not consulted at all."""
    results = verdict.from_needs(_needs(), CANDIDATE)
    results += verdict.from_job_records(
        [_record(ONE, _test(FLAKY))] + [_record(k) for k in PLAN[1:]]
    )
    judged = verdict.decide(PLAN, results, CANDIDATE, run_id=RUN)
    assert not judged.green and judged.flake_rule == ""
    assert "the flake rule passed none" not in verdict.summary(judged)


# ── How many, and beside what ────────────────────────────────────────────────────────────────


def test_three_flakes_are_red() -> None:
    judged = _judge(
        [_record(ONE, _test(FLAKY), _test(OTHER)), _record(TWO, _test(THIRD))], failed=(ONE, TWO)
    )
    _red(judged, "3 failed tests passed alone, more than the 2")


def test_a_flake_beside_a_real_red_is_red() -> None:
    judged = _judge(
        [
            _record(ONE, _test(FLAKY)),
            _record(
                TWO, _test(OTHER, verdict.REPEATED_FAILURE, "failed again, alone"), rerun_exit=1
            ),
        ],
        failed=(ONE, TWO),
    )
    _red(judged, "not passed by a rerun alone")


def test_a_test_that_failed_alone_too_is_red() -> None:
    judged = _judge(
        [
            _record(
                ONE,
                _test(FLAKY),
                _test(OTHER, verdict.REPEATED_FAILURE, "failed again, alone"),
                rerun_exit=1,
            )
        ]
    )
    _red(judged, "not passed by a rerun alone")


@pytest.mark.parametrize(
    ("test", "says"),
    [
        pytest.param(
            _test("tests/rails/test_rule.py::test_it", None, "a rail: red at once and never rerun"),
            "not classified",
            id="a rail",
        ),
        pytest.param(
            _test("tests/test_guard.py::test_it", None, "security-marked: never rerun"),
            "not classified",
            id="a security-marked test",
        ),
        pytest.param(
            _test("web/e2e/chat.spec.ts::it answers [chromium]", verdict.FLAKE, "a retry"),
            "was not rerun alone",
            id="an end-to-end test its record does not show rerun alone",
        ),
    ],
)
def test_a_test_no_rerun_alone_classified_is_red(test: dict[str, Any], says: str) -> None:
    _red(_judge([_record(ONE, _test(FLAKY), test)]), says)


def test_a_rerun_that_did_not_exit_0_is_red() -> None:
    _red(_judge([_record(ONE, _test(FLAKY), rerun_exit=1)]), "not passed by a rerun alone")


def test_a_playwright_flake_is_red() -> None:
    """Playwright's own retry is the rerun: its record names the step's own argv and exit, so no
    rerun alone passed it, and its test is an end-to-end one."""
    test = _test("web/e2e/chat.spec.ts::it answers [chromium]", why="passed on Playwright's retry")
    _red(_judge([_record(ONE, test, rerun_exit=1)]), "not passed by a rerun alone")


# ── An end-to-end test, rerun alone by run-job ───────────────────────────────────────────────

E2E_TEST = "web/e2e/chat.spec.ts::it answers [chromium]"
#: run-job's alone rerun of it (tools/ci/jobs.py, diagnose_playwright).
ALONE = [
    "npx",
    "playwright",
    "test",
    *verdict.E2E_ALONE,
    "--project=chromium",
    "--output=/tmp/rerun/test-results",
    "e2e/chat.spec.ts:7",
]
#: The e2e-shard step's own argv, which a record of Playwright's own retry names.
E2E_STEP = ["npx", "playwright", "test", "--project=smoke", "--project=chromium", "--shard=1/2"]


def test_an_end_to_end_flake_rerun_alone_is_green_and_named() -> None:
    judged = _judge([_record(ONE, _test(E2E_TEST), argv=ALONE)])
    assert judged.green, judged.flake_rule
    assert [(t.key, t.test) for t in judged.flakes] == [(ONE, E2E_TEST)]


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([a for a in ALONE if a != "--workers=1"], id="no --workers=1"),
        pytest.param([a for a in ALONE if a != "--retries=0"], id="no --retries=0"),
        pytest.param([*ALONE[:3], "--workers", "1", *ALONE[4:]], id="--workers spelled apart"),
        pytest.param([*ALONE, "--workers=3"], id="a second --workers"),
        pytest.param([*ALONE, "-j2"], id="-j"),
        pytest.param([*ALONE, "--retries=1"], id="a second --retries"),
        pytest.param([*ALONE, "--repeat-each=2"], id="--repeat-each"),
        pytest.param(E2E_STEP, id="the step's own argv, exit 0"),
        pytest.param(RERUN, id="a pytest rerun's argv"),
    ],
)
def test_an_end_to_end_flake_whose_rerun_did_not_run_alone_is_red(argv: list[str]) -> None:
    _red(_judge([_record(ONE, _test(E2E_TEST), argv=argv)]), "was not rerun alone")


def test_playwrights_own_retry_with_no_alone_rerun_is_red() -> None:
    """A record of Playwright's own retry names the step's own argv and its exit, 1."""
    test = _test(E2E_TEST, why="passed on Playwright's one retry, in a new worker")
    _red(_judge([_record(ONE, test, argv=E2E_STEP, rerun_exit=1)]), "not passed by a rerun alone")


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("web/e2e/chat.spec.ts", id="its own spec"),
        pytest.param("web/e2e/helpers.ts", id="a helper"),
        pytest.param("web/e2e/fixtures/home/config.json", id="a fixture"),
        pytest.param("web/e2e/isolation.ts", id="the isolation rules"),
        pytest.param("web/e2e/on-shard.mjs", id="a runner"),
        pytest.param("web/playwright.config.ts", id="the configuration and its gateway launcher"),
        pytest.param("scripts/spa_dist_freshness.py", id="the preview's first script"),
    ],
)
def test_an_end_to_end_flake_whose_run_reads_a_changed_file_is_red(path: str) -> None:
    judged = _judge([_record(ONE, _test(E2E_TEST), argv=ALONE)], changed=CHANGED | {path})
    _red(judged, path)


def test_an_end_to_end_flake_beside_another_changed_spec_still_passes() -> None:
    changed = CHANGED | {"web/e2e/settings.spec.ts"}
    assert _judge([_record(ONE, _test(E2E_TEST), argv=ALONE)], changed=changed).green


# ── A browser leg's flake, beside the merges of every leg ─────────────────────────────────────
#
# A shard leg red for one flake used to stop before its home run, so `e2e` and `ui-quality` went
# red merging a leg with no blob, and `e2e` went red again on the merged report's retried pass:
# two reds no rerun explains. run-job now runs the leg's other homes after its failed tests, and
# assert-one-run names a retried pass rather than redding on it, so both merges are green and the
# leg is the one red key, judged by its own rerun alone.

LEG_ONE, LEG_TWO = "e2e-shard (1/2)", "e2e-shard (2/2)"
LEGS = [LEG_ONE, LEG_TWO, "e2e-pinned", "e2e", "ui-quality"]
#: The leg's record: its shared run red, its idle home's run and its pass-through after it green.
LEG_EXITS = (1, 0, 0)


def _judge_legs(records: Sequence[str], failed: Sequence[str] = (LEG_ONE,)) -> verdict.Verdict:
    keyed = {json.loads(r)["key"] for r in records}
    texts = list(records) + [_record(k) for k in LEGS if k not in keyed]
    results = verdict.from_needs(_needs(), CANDIDATE) + verdict.from_job_records(texts)
    listed = [_job(n, "failure" if n in failed else "success") for n in ("tree-check", *LEGS)]
    listed.append(_job("results", None, status="in_progress"))
    page = {"total_count": len(listed), "jobs": listed}
    jobs = verdict.read_jobs(json.dumps([page]), run_id=RUN, attempt=1)
    return verdict.decide(LEGS, results, CANDIDATE, run_id=RUN, changed=CHANGED, jobs=jobs)


def test_a_legs_flake_its_alone_rerun_passed_beside_green_merges_is_green_and_named() -> None:
    judged = _judge_legs([_record(LEG_ONE, _test(E2E_TEST), argv=ALONE, exits=LEG_EXITS)])
    assert judged.green, judged.flake_rule
    assert [(t.key, t.test) for t in judged.flakes] == [(LEG_ONE, E2E_TEST)]
    assert f"{E2E_TEST} ({LEG_ONE})" in verdict.flake_notices(judged)


def test_a_legs_flake_whose_alone_rerun_failed_is_red() -> None:
    test = _test(E2E_TEST, verdict.REPEATED_FAILURE, "failed again, alone")
    record = _record(LEG_ONE, test, argv=ALONE, exits=LEG_EXITS, rerun_exit=1)
    _red(_judge_legs([record]))


def test_a_legs_security_marked_failure_is_red_alone_and_beside_a_flake() -> None:
    """Never rerun, so never a flake: alone it leaves the rule nothing to pass, and beside one it
    is named as what keeps the leg red."""
    guarded = _test(E2E_TEST, None, NEVER_RERUN)
    _red(_judge_legs([_record(LEG_ONE, guarded, argv=ALONE, exits=LEG_EXITS)]))
    flake = _test("web/e2e/settings.spec.ts::it saves [chromium]")
    record = _record(LEG_ONE, flake, guarded, argv=ALONE, exits=LEG_EXITS)
    _red(_judge_legs([record]), f"`{E2E_TEST}` is not classified")


def test_a_legs_flake_beside_a_red_merge_is_red() -> None:
    """As before run-job ran the leg's other homes on: `e2e` red merging a leg with no blob."""
    records = [
        _record(LEG_ONE, _test(E2E_TEST), argv=ALONE, exits=(1,)),
        _record("e2e", exits=(1, 0, 0)),
    ]
    _red(_judge_legs(records, failed=(LEG_ONE, "e2e")), "`e2e`")


def test_a_flake_whose_job_never_started_a_planned_step_is_red() -> None:
    """run-job ran the job's other steps on after the flake's step, and the job's time ran out
    before its third began: the two that ran read one red step alone, and a step never ran."""
    record = _record(ONE, _test(FLAKY), exits=(0, 1), unstarted=1)
    _red(_judge([record]), "never started 1 of its planned steps")


def test_a_flake_whose_job_started_every_planned_step_reads_as_its_version_3_record() -> None:
    """A job-record/4 record naming no unstarted step is judged as the same job's version-3
    record: the flake passes, named alike."""
    four = _record(ONE, _test(FLAKY), exits=(0, 1), unstarted=0)
    three = _record(ONE, _test(FLAKY), exits=(0, 1))
    assert json.loads(four)["schema"] == "personalclaw.ci.job-record/4"
    judged, before = _judge([four]), _judge([three])
    assert judged.green and before.green
    assert judged.flakes == before.flakes


def test_a_flake_in_a_record_that_does_not_say_is_read_as_before() -> None:
    """A job-record/3 record, from a run-job before version 4, names no count: its steps alone
    are read, as they were."""
    record = _record(ONE, _test(FLAKY), exits=(0, 1))
    assert json.loads(record)["schema"] == "personalclaw.ci.job-record/3"
    assert _judge([record]).green


def test_a_job_that_failed_in_two_steps_is_red() -> None:
    _red(_judge([_record(ONE, _test(FLAKY), exits=(1, 1))]), "one step that exited 1 alone")


@pytest.mark.parametrize("code", [2, 124, -9], ids=["usage error", "timed out", "killed"])
def test_a_step_that_exited_otherwise_is_red(code: int) -> None:
    _red(_judge([_record(ONE, _test(FLAKY), exits=(code,))]), "one step that exited 1 alone")


# ── A shard that never finished ──────────────────────────────────────────────────────────────


def test_a_cancelled_shard_beside_a_flake_is_red() -> None:
    """Its other tests never ran, so it left no record and its job is cancelled."""
    records = [_record(ONE, _test(FLAKY)), _record(THREE), _record("static")]
    results = verdict.from_needs(_needs("cancelled"), CANDIDATE)
    results += verdict.from_job_records(records)
    listed = [_job("tree-check"), _job(ONE, "failure"), _job(TWO, "cancelled"), _job(THREE)]
    listed += [_job("static"), _job("results", None, status="in_progress")]
    jobs = verdict.read_jobs(
        json.dumps([{"total_count": len(listed), "jobs": listed}]), run_id=RUN, attempt=1
    )
    judged = verdict.decide(PLAN, results, CANDIDATE, run_id=RUN, changed=CHANGED, jobs=jobs)
    _red(judged, f"`{TWO}` reads missing")


def test_a_shard_that_timed_out_beside_a_flake_is_red() -> None:
    judged = _judge([_record(ONE, _test(FLAKY)), _record(TWO, exits=(124,))], failed=(ONE, TWO))
    _red(judged, f"`{TWO}` did not fail in one step that exited 1 alone")


# ── The run's jobs ───────────────────────────────────────────────────────────────────────────


def test_a_failed_step_after_the_run_job_line_is_red() -> None:
    steps = [
        {"name": verdict.RUN_JOB_STEP, "conclusion": "failure"},
        {"name": "the entry's reports", "conclusion": "failure"},
    ]
    listed = [_job(n) for n in ("tree-check", TWO, THREE, "static")]
    listed += [_job(ONE, "failure", steps=steps), _job("results", None, status="in_progress")]
    jobs = verdict.read_jobs(
        json.dumps([{"total_count": len(listed), "jobs": listed}]), run_id=RUN, attempt=1
    )
    _red(_judge([_record(ONE, _test(FLAKY))], jobs=jobs), "not in its run-job line alone")


def test_a_failed_job_outside_the_plan_is_red() -> None:
    listed = [_job(n) for n in (TWO, THREE, "static")]
    listed += [_job("tree-check", "failure"), _job(ONE, "failure")]
    listed += [_job("results", None, status="in_progress")]
    jobs = verdict.read_jobs(
        json.dumps([{"total_count": len(listed), "jobs": listed}]), run_id=RUN, attempt=1
    )
    _red(_judge([_record(ONE, _test(FLAKY))], jobs=jobs), "`tree-check` is 'failure'")


def test_a_job_still_running_is_red() -> None:
    listed = [_job(n) for n in ("tree-check", TWO, THREE)]
    listed += [_job(ONE, "failure"), _job("static", None, status="in_progress")]
    jobs = verdict.read_jobs(
        json.dumps([{"total_count": len(listed), "jobs": listed}]), run_id=RUN, attempt=1
    )
    _red(_judge([_record(ONE, _test(FLAKY))], jobs=jobs), "`static` is in_progress")


def test_a_failing_key_the_jobs_do_not_list_as_failed_is_red() -> None:
    _red(_judge([_record(ONE, _test(FLAKY))], failed=()), f"no failed job `{ONE}`")


@pytest.mark.parametrize(
    ("pages", "says"),
    [
        pytest.param([], "a list of the API's pages", id="no page"),
        pytest.param([{"total_count": 3, "jobs": [_job(ONE)]}], "1 jobs of 3", id="a job unseen"),
        pytest.param(
            [{"total_count": 1, "jobs": [_job(ONE, run_id=1)]}], "not run 4242", id="another run"
        ),
        pytest.param(
            [{"total_count": 1, "jobs": [_job(ONE, run_attempt=2)]}],
            "attempt 1",
            id="another attempt",
        ),
        pytest.param(
            [{"total_count": 2, "jobs": [_job(ONE), _job(ONE)]}], "twice", id="a job twice"
        ),
        pytest.param(
            [{"total_count": 2, "jobs": [_job(ONE), _job(ONE, "skipped", steps=[])]}],
            "twice",
            id="a job that ran and a skipped job of one name",
        ),
        pytest.param(
            [{"total_count": 2, "jobs": [_job(ONE, "skipped", steps=[]), _job(ONE, "failure")]}],
            "twice",
            id="a skipped job and a job that ran of one name",
        ),
        pytest.param(
            [{"total_count": 3, "jobs": [*EMPTY_STAGES[:2], _job("matrix.key")]}],
            "twice",
            id="a job that ran named as the skipped stages are",
        ),
        pytest.param(
            [{"total_count": 3, "jobs": [_job("matrix.key", "failure"), *EMPTY_STAGES[:2]]}],
            "twice",
            id="a failed job named as the skipped stages are, listed first",
        ),
        pytest.param(
            [{"total_count": 2, "jobs": [_job(ONE)]}, {"total_count": 3, "jobs": [_job(TWO)]}],
            "the pages count",
            id="pages that disagree",
        ),
    ],
)
def test_a_jobs_listing_that_cannot_be_trusted_is_refused(pages: list[Any], says: str) -> None:
    with pytest.raises(verdict.Refused, match=says):
        verdict.read_jobs(json.dumps(pages), run_id=RUN, attempt=1)


def test_the_stages_a_plan_leaves_empty_read_under_one_name() -> None:
    listed = [_job(n) for n in ("tree-check", *PLAN)] + EMPTY_STAGES
    jobs = verdict.read_jobs(
        json.dumps([{"total_count": len(listed), "jobs": listed}]), run_id=RUN, attempt=1
    )
    assert [j.name for j in jobs].count("matrix.key") == 3
    judged = _judge([_record(ONE, _test(FLAKY))], jobs=_jobs(ONE, extra=EMPTY_STAGES))
    assert judged.green and [t.test for t in judged.flakes] == [FLAKY]


# ── The identity still holds ─────────────────────────────────────────────────────────────────


def test_a_flake_on_another_head_is_red() -> None:
    _red(_judge([_record(ONE, _test(FLAKY), head="b" * 40)]), "stale head")


@pytest.mark.parametrize(
    "name", ["", "/etc/passwd", "../outside", "tests/./x.py"], ids=["empty", "abs", "up", "dot"]
)
def test_a_changed_list_naming_no_path_is_refused(name: str) -> None:
    with pytest.raises(verdict.Refused, match="no path of the repository"):
        verdict.read_changed(f"docs/a.md\0{name}\0")


# ── The command ──────────────────────────────────────────────────────────────────────────────


def _files(
    tmp_path: Path,
    records: Sequence[str],
    *,
    failed: Sequence[str] = (ONE,),
    extra: Sequence[dict[str, Any]] = (),
) -> list[str]:
    folder = tmp_path / "records"
    for text in [
        *records,
        *(_record(k) for k in PLAN if k not in {json.loads(r)["key"] for r in records}),
    ]:
        key = json.loads(text)["key"]
        art = folder / verdict.record_artifact(key, run_id=RUN, attempt=1)
        art.mkdir(parents=True)
        (art / "record.json").write_text(text)
    listed = [_job(n, "failure" if n in failed else "success") for n in ("tree-check", *PLAN)]
    listed += [*extra, _job("results", None, status="in_progress")]
    (tmp_path / "jobs.json").write_text(json.dumps([{"total_count": len(listed), "jobs": listed}]))
    (tmp_path / "changed").write_text("".join(p + "\0" for p in sorted(CHANGED)))
    (tmp_path / "plan.json").write_text(json.dumps(PLAN))
    (tmp_path / "needs.json").write_text(_needs())
    return [
        "--plan", str(tmp_path / "plan.json"), "--needs", str(tmp_path / "needs.json"),
        "--head", HEAD, "--run-head", HEAD, "--records", str(folder), "--run-id", str(RUN),
        "--changed", str(tmp_path / "changed"), "--jobs", str(tmp_path / "jobs.json"),
    ]  # fmt: skip


def test_the_command_passes_a_flake_and_writes_its_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    odd = "tests/test_timing.py::test_it[100%]"
    assert verdict.main(_files(tmp_path, [_record(ONE, _test(odd))])) == 0
    out = capsys.readouterr().out
    assert out.startswith("### Verdict: green\n")
    assert f"::notice title=flake passed::tests/test_timing.py::test_it[100%25] ({ONE})\n" in out


def test_the_command_reads_a_green_run_whose_plan_leaves_stages_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # #5623: every planned job passed, and the verdict refused the listing (exit 2).
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    argv = _files(tmp_path, [], failed=(), extra=EMPTY_STAGES)
    (tmp_path / "needs.json").write_text(_needs(test="success"))
    assert verdict.main(argv) == 0
    capsys.readouterr()
    # And with the listing as it happened.
    real = [{"total_count": len(LISTED_5623), "jobs": LISTED_5623}]
    (tmp_path / "jobs.json").write_text(json.dumps(real))
    assert verdict.main(argv) == 0
    assert capsys.readouterr().out.startswith("### Verdict: green\n")


def test_the_command_refuses_the_rules_lists_on_a_push(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert verdict.main(_files(tmp_path, [_record(ONE, _test(FLAKY))])) == 2
    assert "on a pull request's run only" in capsys.readouterr().err


def test_the_command_reads_the_two_lists_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    argv = _files(tmp_path, [_record(ONE, _test(FLAKY))])
    i = argv.index("--jobs")
    assert verdict.main(argv[:i] + argv[i + 2 :]) == 2
    assert "read together" in capsys.readouterr().err


def test_the_command_says_why_the_rule_passed_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    records = [_record(ONE, _test(FLAKY)), _record(TWO, _test(OTHER)), _record(THREE, _test(THIRD))]
    assert verdict.main(_files(tmp_path, records, failed=(ONE, TWO, THREE))) == 1
    out = capsys.readouterr().out
    assert "- the flake rule passed none: 3 failed tests passed alone" in out
    assert "::notice" not in out


# ── The ids the rule reads are the repository's paths ────────────────────────────────────────


def test_every_pytest_step_runs_from_the_repository_root() -> None:
    """run-job names a failed pytest test relative to its step's folder, and the rule checks that
    name against the pull request's changed paths, which are the repository's. A pytest step run
    from a folder of its own would name its tests from there, and a test the pull request changed
    would match nothing."""
    from tools.ci import catalogue

    elsewhere = [
        f"{job.id}: {step.workdir}$ {' '.join(step.argv)}"
        for job in catalogue.read().jobs
        for step in job.command
        if "pytest" in step.argv and step.workdir
    ]
    assert elsewhere == []


# ── A security-posture job ───────────────────────────────────────────────────────────────────
#
# The suite shards these plants use are security-posture jobs: the rule reads that same attempt's
# own rerun and passes ordinary flakes only. A security-marked test is never rerun, so its failure
# stays red, and a shard passes only when every security-marked test in it passed on attempt 1.

SECURITY_TEST = "tests/test_guard.py::test_it_refuses_the_forged_token"
NEVER_RERUN = "security-marked: the rerun deselects it, so it is never rerun"


def test_the_plants_suite_is_a_security_posture_job() -> None:
    from tools.ci import catalogue

    [job] = [j for j in catalogue.read().jobs if j.id == ONE.split(" ")[0]]
    assert job.security == "security"


def test_a_security_marked_failure_in_a_security_job_is_red_even_beside_an_ordinary_flake() -> None:
    record = _record(ONE, _test(FLAKY), _test(SECURITY_TEST, None, NEVER_RERUN))
    _red(_judge([record]), f"`{SECURITY_TEST}` is not classified")


def test_an_ordinary_flake_in_a_security_job_whose_security_tests_all_passed_is_green() -> None:
    """Its record names no security-marked failure: every one passed on attempt 1."""
    judged = _judge([_record(ONE, _test(FLAKY))])
    assert judged.green
    assert [(t.key, t.test, t.standing) for t in judged.flakes] == [(ONE, FLAKY, verdict.FLAKE)]
    assert f"{FLAKY} ({ONE})" in verdict.flake_notices(judged)
