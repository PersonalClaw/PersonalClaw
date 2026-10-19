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


def test_a_record_reads_back_as_the_result_it_names() -> None:
    [result] = verdict.from_job_records([_record(attempt=2)])
    assert result == verdict.JobResult(
        "rails", verdict.SUCCESS, verdict.Identity(HEAD, {"apps-partner": PARTNER}), 2
    )


def test_a_record_holds_what_a_reuse_store_reads() -> None:
    data = json.loads(_record(quarantined=["tests/test_a.py::test_x"], plan="offline", workers=2))
    assert data["schema"] == verdict.JOB_RECORD_SCHEMA
    assert data["rerun"] is False
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
        (_with(head_sha="a" * 39), "head_sha must be a full object id"),
        (_with(head_sha=HEAD + "\n"), "head_sha must be a full object id"),
        (_with(external_revisions={"apps-partner": "short"}), "must be a full object id"),
        (_with(external_revisions=[]), "external_revisions must be an object"),
        (_with(attempt=0), "attempt must be a whole number from 1"),
        (_with(attempt=True), "attempt must be a whole number from 1"),
        (_with(rerun="no"), "rerun must be true or false"),
        (_with(quarantined=[""]), "quarantined must be a list of test ids"),
        (_with(quarantined=None), "quarantined must be a list of test ids"),
        (_with(plan="guessed"), "plan is 'guessed'"),
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
