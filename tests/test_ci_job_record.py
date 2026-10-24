"""The per-job record ``run-job`` writes (tools/ci/verdict.py job_record / from_job_records):
what it holds, and every shape the reader refuses."""

from __future__ import annotations

import json

import pytest

from tools.ci import verdict

pytestmark = pytest.mark.security

HEAD = "a" * 40
PARTNER = "b" * 40


def _record(**kw) -> str:
    kw.setdefault("key", "rails")
    kw.setdefault("result", verdict.SUCCESS)
    kw.setdefault("identity", verdict.Identity(HEAD, {"apps-partner": PARTNER}))
    kw.setdefault("steps", [(["uv", "run", "pytest"], 0, 1.5)])
    return verdict.job_record(**kw)


def _with(**fields) -> str:
    data = json.loads(_record())
    data.update(fields)
    return json.dumps(data)


def _without(*names: str, **fields) -> str:
    data = json.loads(_record(**fields))
    for name in names:
        del data[name]
    return json.dumps(data)


_RUNNER = {"label": "ubuntu-24.04", "image": "ubuntu24/20270110.1", "os": "linux", "arch": "x64"}


def test_a_record_names_what_the_job_ran_on_and_its_toolchain() -> None:
    tools = {"python": "3.12.15", "uv-sync": "/opt/uv@sha256:" + "c" * 64}
    data = json.loads(_record(runner=_RUNNER, toolchain=tools))
    assert data["schema"] == "personalclaw.ci.job-record/3"
    assert (data["runner"], data["toolchain"]) == (_RUNNER, tools)
    [result] = verdict.from_job_records([_record(runner=_RUNNER, toolchain=tools)])
    assert (result.runner, result.toolchain) == (_RUNNER, tools)
    # A bare host names no image; a record written without either names null for it.
    [bare] = verdict.from_job_records([_record(runner={**_RUNNER, "image": ""})])
    assert bare.runner is not None and bare.runner["image"] == ""
    data = json.loads(_record())
    assert (data["runner"], data["toolchain"]) == (None, None)
    [none] = verdict.from_job_records([_record()])
    assert (none.runner, none.toolchain) == (None, None)


def test_a_version_2_record_is_still_read_and_names_neither() -> None:
    """Records an older run-job wrote stay readable (a run's verdict, the telemetry collector);
    only a push's replay needs what they lack, and runs the job without it."""
    text = _without("runner", "toolchain")
    old = json.loads(text)
    old["schema"] = "personalclaw.ci.job-record/2"
    [result] = verdict.from_job_records([json.dumps(old)])
    assert (result.key, result.result) == ("rails", verdict.SUCCESS)
    assert (result.runner, result.toolchain) == (None, None)


def test_a_record_reads_back_as_the_result_it_names() -> None:
    [result] = verdict.from_job_records([_record(attempt=2)])
    # A record names the partners its own job read, so it is judged against those alone.
    assert result == verdict.JobResult(
        "rails",
        verdict.SUCCESS,
        verdict.Identity(HEAD, {"apps-partner": PARTNER}),
        2,
        own_partners=True,
    )


def test_a_record_names_the_run_that_wrote_it_or_none() -> None:
    # On GitHub run-job writes $GITHUB_RUN_ID; a local record names no run.
    data = json.loads(_record(run_id=987654321))
    assert data["schema"] == "personalclaw.ci.job-record/3"
    assert data["run_id"] == 987654321
    [result] = verdict.from_job_records([_record(run_id=987654321)])
    assert result.run_id == 987654321
    assert json.loads(_record())["run_id"] is None
    [local] = verdict.from_job_records([_record()])
    assert local.run_id is None


def test_a_record_holds_what_a_reuse_store_reads() -> None:
    data = json.loads(_record(quarantined=["tests/test_a.py::test_x"], plan="offline", workers=2))
    assert data["schema"] == verdict.JOB_RECORD_SCHEMA
    assert data["rerun"] is None
    assert data["quarantined"] == ["tests/test_a.py::test_x"]
    assert data["plan"] == "offline" and data["workers"] == 2
    assert data["steps"] == [{"argv": ["uv", "run", "pytest"], "exit": 0, "seconds": 1.5}]


def test_records_judged_by_decide_are_green_only_for_their_head() -> None:
    results = verdict.from_job_records([_record()])
    candidate = verdict.Identity(HEAD, {"apps-partner": PARTNER})
    assert verdict.decide(["rails"], results, candidate).green
    other = verdict.Identity("c" * 40, {"apps-partner": PARTNER})
    assert not verdict.decide(["rails"], results, other).green


def test_a_success_with_a_failed_step_is_never_written() -> None:
    with pytest.raises(verdict.Refused, match="a step exited non-zero"):
        _record(steps=[(["false"], 1, 0.1)])


@pytest.mark.parametrize("seconds", [float("nan"), float("inf")])
def test_a_record_with_seconds_that_are_no_number_is_never_written(seconds: float) -> None:
    # Refused as it is written, not only when it is read back: the text is JSON or nothing.
    with pytest.raises(verdict.Refused, match="job record 'rails' is not JSON"):
        _record(steps=[(["uv", "run", "pytest"], 0, seconds)])


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("[]", "must be an object"),
        ("{", "is not JSON"),
        ('{"key": "a", "key": "b"}', "job record 1 repeats the key 'key'"),
        (_with(extra=1), "must hold exactly"),
        (_with(schema="other/1"), "has schema"),
        (_with(key=""), "names no job key"),
        (_with(result="passed"), "which is no job's"),
        # A word of another JSON type is refused by name, never a crash.
        (_with(result=["success"]), "which is no job's"),
        (_with(result={}), "which is no job's"),
        (_with(head_sha="a" * 39), "head_sha must be a full object id"),
        (_with(head_sha=HEAD + "\n"), "head_sha must be a full object id"),
        (_with(external_revisions={"apps-partner": "short"}), "must be a full object id"),
        (_with(external_revisions=[]), "external_revisions must be an object"),
        (_with(attempt=0), "attempt must be a whole number from 1"),
        (_with(attempt=True), "attempt must be a whole number from 1"),
        (_with(run_id=0), "run_id must be a whole number from 1, or null"),
        (_with(run_id=-1), "run_id must be a whole number from 1, or null"),
        (_with(run_id=True), "run_id must be a whole number from 1, or null"),
        (_with(run_id="7"), "run_id must be a whole number from 1, or null"),
        (_with(run_id=7.0), "run_id must be a whole number from 1, or null"),
        # A record of the schema before the run id: it names no run, so it is never read.
        (_with(schema="personalclaw.ci.job-record/1"), "has schema"),
        (_with(rerun="no"), "rerun must be null or an object"),
        (_with(rerun=False), "rerun must be null or an object"),
        (_with(quarantined=[""]), "quarantined must be a list of test ids"),
        (_with(quarantined=None), "quarantined must be a list of test ids"),
        (_with(plan="guessed"), "plan is 'guessed'"),
        (_with(plan=["ci"]), "plan is \\['ci'\\]"),
        (_with(workers=0), "workers must be a whole number from 1"),
        (_with(steps={}), "steps must be a list"),
        (_with(steps=[{"argv": ["x"], "exit": 0}]), "each step holds exactly"),
        (_with(steps=[{"argv": [], "exit": 0, "seconds": 0}]), "non-empty list of strings"),
        (_with(steps=[{"argv": ["x"], "exit": 0.5, "seconds": 0}]), "exit must be a whole"),
        (_with(steps=[{"argv": ["x"], "exit": 0, "seconds": -1}]), "seconds must be a number"),
        (_with(steps=[{"argv": ["x"], "exit": 2, "seconds": 0}]), "a step exited non-zero"),
        (
            _with(steps=[{"argv": ["x"], "exit": 0, "seconds": 0, "x": 1}]),
            "each step holds exactly",
        ),
        (_with(steps=[{"argv": ["x", 1], "exit": 0, "seconds": 0}]), "non-empty list of strings"),
        (_with(workers=True), "workers must be a whole number from 1"),
        (_with(steps=[{"argv": ["x"], "exit": False, "seconds": 0}]), "exit must be a whole"),
        (_with(steps=[{"argv": ["x"], "exit": 0, "seconds": True}]), "seconds must be a number"),
        (_with().replace('"seconds": 1.5', '"seconds": 1e999'), "seconds must be a number"),
        (_with().replace('"seconds": 1.5', '"seconds": NaN'), "NaN, which is not JSON"),
        (_with().replace('"seconds": 1.5', '"seconds": Infinity'), "Infinity, which is not JSON"),
        (_with().replace('"attempt": 1', '"attempt": -Infinity'), "-Infinity, which is not JSON"),
        # What the job ran on (#3919): null, or exactly the four names, as text, the label, OS
        # and architecture not empty; and a toolchain of text by toolchain word.
        (_with(runner=[]), "runner must be null or name exactly"),
        (_with(runner={**_RUNNER, "host": "x"}), "runner must be null or name exactly"),
        (_with(runner={k: v for k, v in _RUNNER.items() if k != "image"}), "runner must be null"),
        (_with(runner={**_RUNNER, "image": 1}), "runner must be null or name exactly"),
        (_with(runner={**_RUNNER, "label": ""}), "runner must be null or name exactly"),
        (_with(runner={**_RUNNER, "arch": ""}), "runner must be null or name exactly"),
        (_with(toolchain=[]), "toolchain must be null or an object of strings"),
        (_with(toolchain={"": "x"}), "toolchain must be null or an object of strings"),
        (_with(toolchain={"python": 3}), "toolchain must be null or an object of strings"),
        # A version-3 record without them, and a version-2 record with them.
        (_without("runner"), "must hold exactly"),
        (_with(schema="personalclaw.ci.job-record/2"), "must hold exactly"),
    ],
)
def test_a_record_it_cannot_read_is_refused(text: str, why: str) -> None:
    with pytest.raises(verdict.Refused, match=why):
        verdict.from_job_records([text])


def test_two_records_for_one_key_and_attempt_are_refused() -> None:
    failed = _record(result=verdict.FAILURE, steps=[(["x"], 1, 0.0)])
    with pytest.raises(verdict.Refused, match="two job records for 'rails' on attempt 1"):
        verdict.from_job_records([failed, _record()])


def test_a_red_attempt_and_a_green_rerun_read_red() -> None:
    failed = _record(result=verdict.FAILURE, steps=[(["x"], 1, 0.0)])
    results = verdict.from_job_records([failed, _record(attempt=2)])
    candidate = verdict.Identity(HEAD, {"apps-partner": PARTNER})
    judged = verdict.decide(["rails"], results, candidate)
    assert not judged.green
    assert judged.reasons[0].kind == "red on an earlier attempt"


# ── decide, reading one run's records ────────────────────────────────────────────────────────

CANDIDATE = verdict.Identity(HEAD, {"apps-partner": PARTNER})


def _judged(*records: str, run_id: int | None) -> verdict.Verdict:
    return verdict.decide(["rails"], verdict.from_job_records(records), CANDIDATE, run_id=run_id)


def test_a_record_of_another_run_reads_red_when_the_run_is_named() -> None:
    judged = _judged(_record(run_id=8), run_id=7)
    assert judged.reasons == (
        verdict.Reason("rails", "another run", "recorded by run 8, not run 7"),
    )


def test_a_record_naming_no_run_reads_red_when_the_run_is_named() -> None:
    [reason] = _judged(_record(), run_id=7).reasons
    assert (reason.key, reason.kind) == ("rails", "no run")


def test_a_key_with_no_attempt_1_record_reads_red_when_the_run_is_named() -> None:
    judged = _judged(_record(run_id=7, attempt=2), run_id=7)
    assert judged.reasons == (
        verdict.Reason(
            "rails", "missing on attempt 1", "a rerun carries only what attempt 1 recorded"
        ),
    )


def test_a_records_own_run_on_attempt_1_and_a_later_one_reads_green() -> None:
    assert _judged(_record(run_id=7), _record(run_id=7, attempt=3), run_id=7).green


def test_without_a_run_decide_reads_no_run_and_no_attempt_1() -> None:
    # The reading every caller had before the run id: unchanged.
    assert _judged(_record(), run_id=None).green
    assert _judged(_record(run_id=8, attempt=2), run_id=None).green


@pytest.mark.parametrize("run_id", [0, -1, True, "7", 7.0])
def test_decide_refuses_a_run_that_is_no_whole_number_from_1(run_id) -> None:
    with pytest.raises(verdict.Refused, match="run_id"):
        _judged(_record(run_id=7), run_id=run_id)


# ── the failed tests a record names, and how each stands ─────────────────────────────────────
#
# A failure's record names each failed test with the diagnostic rerun's word for it, and a pass's
# names each quarantined test that failed. The verdict lists every one of them and reads them for
# nothing else: the first result is the result, so a job whose only failure is a flake stays red.

FLAKY = "tests/test_a.py::test_flaky"
TWICE = "tests/test_a.py::TestB::test_twice[a b]"
RAIL = "tests/rails/test_r.py::test_rail"
PASSED_ALONE = "passed rerun alone in a new process"


def _rerun(*tests: tuple[str, str | None, str]) -> dict:
    return {
        "argv": ["uv", "run", "pytest", "-n", "0", "tests/test_a.py"],
        "exit": 1,
        "tests": [{"test": t, "rerun_result": word, "why": why} for t, word, why in tests],
    }


def _failed(**kw) -> str:
    kw.setdefault("result", verdict.FAILURE)
    kw.setdefault("steps", [(["uv", "run", "pytest"], 1, 1.5)])
    return _record(**kw)


def test_a_failure_reads_back_with_each_failed_test_and_the_reruns_word_for_it() -> None:
    rerun = _rerun(
        (FLAKY, verdict.FLAKE, PASSED_ALONE),
        (TWICE, verdict.REPEATED_FAILURE, "failed again, alone"),
        (RAIL, None, "a rail: red at once and never rerun"),
    )
    [result] = verdict.from_job_records([_failed(rerun=rerun, attempt=2)])
    assert result.result == verdict.FAILURE
    assert result.tests == (
        verdict.FailedTest("rails", 2, FLAKY, verdict.FLAKE, PASSED_ALONE),
        verdict.FailedTest("rails", 2, TWICE, verdict.REPEATED_FAILURE, "failed again, alone"),
        verdict.FailedTest(
            "rails", 2, RAIL, verdict.NOT_CLASSIFIED, "a rail: red at once and never rerun"
        ),
    )


def test_a_pass_reads_back_with_each_quarantined_failure() -> None:
    [result] = verdict.from_job_records([_record(quarantined=[FLAKY])])
    assert result.result == verdict.SUCCESS
    assert result.tests == (
        verdict.FailedTest("rails", 1, FLAKY, verdict.QUARANTINED, verdict.WAIVED),
    )


def test_a_record_that_names_no_failed_test_reads_back_with_none() -> None:
    [result] = verdict.from_job_records([_record()])
    assert result.tests == ()


@pytest.mark.parametrize("word", sorted(verdict.RERUN_RESULTS))
def test_a_job_whose_only_failure_the_rerun_classified_reads_red_and_lists_it(word) -> None:
    """A flake is recorded, and its job stays red: no word of the rerun's waives a failure."""
    results = verdict.from_job_records([_failed(rerun=_rerun((FLAKY, word, "why")))])
    judged = verdict.decide(["rails"], results, CANDIDATE)
    assert not judged.green
    assert judged.reasons == (verdict.Reason("rails", "failure", "attempt 1"),)
    assert judged.tests == (verdict.FailedTest("rails", 1, FLAKY, word, "why"),)


def test_a_pass_with_a_quarantined_failure_reads_green_and_lists_it() -> None:
    results = verdict.from_job_records([_record(quarantined=[FLAKY])])
    judged = verdict.decide(["rails"], results, CANDIDATE)
    assert judged.green and judged.reasons == ()
    assert judged.tests == (
        verdict.FailedTest("rails", 1, FLAKY, verdict.QUARANTINED, verdict.WAIVED),
    )


def test_the_failed_tests_of_every_job_and_attempt_are_listed_in_order() -> None:
    """By job, then attempt, then test: rails' attempt 1 test sorts after its attempt 2 test by
    name, and the records come in neither order."""
    records = [
        _record(key="rails", attempt=2, quarantined=[TWICE]),
        _failed(key="lint", rerun=_rerun((FLAKY, verdict.FLAKE, "w"), (RAIL, None, "w"))),
        _failed(key="rails", rerun=_rerun((FLAKY, verdict.REPEATED_FAILURE, "w"))),
    ]
    assert TWICE < FLAKY
    judged = verdict.decide(["rails"], verdict.from_job_records(records), CANDIDATE)
    assert not judged.green
    assert [(t.key, t.attempt, t.test, t.standing) for t in judged.tests] == [
        ("lint", 1, RAIL, verdict.NOT_CLASSIFIED),
        ("lint", 1, FLAKY, verdict.FLAKE),
        ("rails", 1, FLAKY, verdict.REPEATED_FAILURE),
        ("rails", 2, TWICE, verdict.QUARANTINED),
    ]


def test_the_summary_lists_each_failed_test_and_says_a_flake_is_still_red() -> None:
    rerun = _rerun((FLAKY, verdict.FLAKE, PASSED_ALONE), (RAIL, None, "a rail: never rerun"))
    judged = verdict.decide(["rails"], verdict.from_job_records([_failed(rerun=rerun)]), CANDIDATE)
    text = verdict.summary(judged)
    assert text.startswith("### Verdict: red\n")
    assert "- `rails`: failure (attempt 1)" in text
    assert f"- `rails` attempt 1: `{FLAKY}`: flake ({PASSED_ALONE})" in text
    assert f"- `rails` attempt 1: `{RAIL}`: not classified (a rail: never rerun)" in text
    assert "A flake is still a failure" in text


def test_a_green_summary_lists_each_quarantined_failure() -> None:
    judged = verdict.decide(
        ["rails"], verdict.from_job_records([_record(quarantined=[FLAKY])]), CANDIDATE
    )
    text = verdict.summary(judged)
    assert text.startswith("### Verdict: green\n")
    assert f"- `rails` attempt 1: `{FLAKY}`: quarantined ({verdict.WAIVED})" in text


def test_a_summary_with_no_failed_test_is_as_it_was() -> None:
    judged = verdict.decide(["rails"], verdict.from_job_records([_record()]), CANDIDATE)
    assert verdict.summary(judged) == (
        "### Verdict: green\n\nEvery planned job passed on this candidate.\n"
    )


@pytest.mark.parametrize(
    "test",
    [
        "tests/t.py::test_x[\n### Verdict: green\n]",
        "tests/t.py::test_x[\r### Verdict: green]",
        "tests/t.py::test_x[`]\n- `rails`: passed",
        "tests/t.py::test_x[ ### Verdict: green]",
    ],
)
def test_a_failed_tests_name_writes_no_line_of_its_own_in_the_summary(test: str) -> None:
    """A test id is the suite's text: a parametrized case can hold a line break or a backtick.
    In the summary it stays on its one line, inside its one code span."""
    rerun = _rerun((test, verdict.FLAKE, "why\n### Verdict: green"))
    judged = verdict.decide(["rails"], verdict.from_job_records([_failed(rerun=rerun)]), CANDIDATE)
    text = verdict.summary(judged)
    lines = text.splitlines()
    assert lines[0] == "### Verdict: red"
    assert "### Verdict: green" not in [line.strip() for line in lines]
    [listed] = [line for line in lines if line.startswith("- `rails` attempt 1: ")]
    assert listed.count("`") == 4, listed
    # The head, the reason, what the list is, and the one test: no line more, at any boundary.
    assert len(lines) == 7, lines


def test_a_jobs_key_writes_no_line_of_its_own_in_the_list_either() -> None:
    key = "rails`\n### Verdict: red"
    judged = verdict.decide(
        [key], verdict.from_job_records([_record(key=key, quarantined=[FLAKY])]), CANDIDATE
    )
    lines = verdict.summary(judged).splitlines()
    assert lines[0] == "### Verdict: green" and len(lines) == 7, lines
    assert lines[-1].startswith("- `rails'\\n### Verdict: red` attempt 1: "), lines
    assert lines[-1].count("`") == 4, lines


def test_a_push_to_mains_verdict_lists_the_failed_tests_too() -> None:
    results = verdict.from_job_records([_failed(rerun=_rerun((FLAKY, verdict.FLAKE, "w")))])
    judged = verdict.decide_main_push(
        ["rails"], results, CANDIDATE, {}, event="push", ref="refs/heads/main"
    )
    assert not judged.green
    assert judged.tests == (verdict.FailedTest("rails", 1, FLAKY, verdict.FLAKE, "w"),)
