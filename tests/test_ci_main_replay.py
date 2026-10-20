"""``tools/ci/main_replay.py``: what a push to ``main`` may replay from its pull request's run.

Every case is a planted set of API answers and the verdict files attempt 1 wrote, each written by
``verdict.job_record`` (run-job's own writer), and the quarantined one by run-job itself. The
replay must replay a job only from attempt 1 of the first merge.yml pull_request run on the
merged head, green, on the pushed tree, and run every other job; and run everything on any
shape it cannot read.

Security-marked: a replay that should not happen is a false green on main.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from table_rows import row

from tools.ci import catalogue, jobs, main_replay, trust, verdict

pytestmark = pytest.mark.security

REPO = "example/project"
SHA = "a" * 40
BEFORE = "b" * 40
HEAD = "c" * 40
TREE = "d" * 40
CONTROLLER = "e" * 40
RUN = 900
WORKFLOW = "merge.yml"
RUNS = f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs"
ATTEMPT = f"/repos/{REPO}/actions/runs/{RUN}/attempts/1"
JOBS = f"{ATTEMPT}/jobs"
EVENTS = f"/repos/{REPO}/issues/7/events"
PULLS = f"/repos/{REPO}/commits/{SHA}/pulls"
STARTED = 1_800_000_000.0  # 2027-01-15T08:00:00Z
NOW = STARTED + 3600.0
TOOLCHAIN = {"python": "3.12.15", "uv": "0.13.1"}


def _job(job_id: str, reuse: str | None = "tree", **kw: Any) -> catalogue.Job:
    fields: dict[str, Any] = dict(
        id=job_id,
        tiers=("merge",),
        inputs=(":(glob)**",),
        external_inputs=(),
        toolchain=(),
        cache=(),
        security="ordinary",
        timeout_min=10,
        reuse=catalogue.parse_reuse(reuse),
        command=(catalogue.Step(("true",)),),
    )
    fields.update(kw)
    return catalogue.Job(**fields)


#: The plan: a tree job, a ttl job, a sharded tree job and a security job.
PLAN = (
    _job("rails"),
    _job("served", "ttl:6"),
    _job("web-unit", shards=2),
    _job("python-suite", security="security"),
)
REPLAYABLE = ("rails", "served", "web-unit (1/2)", "web-unit (2/2)")
KEYS = (*REPLAYABLE, "python-suite")


def _record(key: str, *, head: str = HEAD, **kw: Any) -> bytes:
    fields: dict[str, Any] = dict(
        key=key,
        result=verdict.SUCCESS,
        identity=verdict.Identity(head, {}),
        steps=[(["uv", "run", "pytest"], 0, 12.5)],
        attempt=1,
        quarantined=[],
    )
    fields.update(kw)
    return verdict.job_record(**fields).encode()


def _answers(head: str = HEAD) -> dict[str, Any]:
    """The one shape that replays: everything below agrees."""
    return {
        f"/repos/{REPO}/commits/{SHA}": {"commit": {"tree": {"sha": TREE}}},
        f"/repos/{REPO}/commits/{head}": {"commit": {"tree": {"sha": TREE}}},
        f"/repos/{REPO}/commits/{SHA}/pulls": [
            {
                "number": 7,
                "merge_commit_sha": SHA,
                "created_at": "2027-01-15T07:00:00Z",
                "merged_at": "2027-01-15T09:00:00Z",
                "base": {"ref": "main"},
                "head": {"sha": head},
            }
        ],
        f"/repos/{REPO}/compare/{BEFORE}...{head}": {"behind_by": 0, "ahead_by": 1},
        RUNS: {
            "total_count": 2,
            "workflow_runs": [
                {
                    "id": RUN,
                    "run_number": 50,
                    "head_sha": head,
                    "event": "pull_request",
                    "path": f".github/workflows/{WORKFLOW}",
                    "created_at": "2027-01-15T07:59:00Z",
                },
                {
                    "id": 901,
                    "run_number": 51,
                    "head_sha": head,
                    "event": "pull_request",
                    "path": f".github/workflows/{WORKFLOW}",
                    "created_at": "2027-01-15T08:30:00Z",
                },
            ],
        },
        EVENTS: [{"event": "labeled"}],
        ATTEMPT: {
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
            "run_started_at": "2027-01-15T08:00:00Z",
            "html_url": f"https://example.com/runs/{RUN}/attempts/1",
        },
        JOBS: {
            "total_count": len(KEYS),
            "jobs": [
                {"name": key, "status": "completed", "conclusion": "success", "run_attempt": 1}
                for key in KEYS
            ],
        },
    }


def _files() -> dict[str, bytes]:
    return {key: _record(key) for key in KEYS}


class _Api:
    """Planted answers, and every path read, so a read of a later attempt is seen."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.read: list[str] = []

    def __call__(self, path: str) -> Any:
        self.read.append(path)
        key = path.split("?", 1)[0]
        if key not in self.answers:
            raise OSError(f"no planted answer for {key}")
        value = self.answers[key]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def _kwargs(**changes: Any) -> dict[str, Any]:
    """decide()'s inputs for the shape that replays, with *changes*, for a call made directly."""
    kwargs: dict[str, Any] = dict(
        event="push",
        ref="refs/heads/main",
        repo=REPO,
        sha=SHA,
        before=BEFORE,
        plan=PLAN,
        records=lambda run_id: list(_files().values()),
        verified=lambda head: None,
        now=NOW,
        controller_revision=CONTROLLER,
        toolchain=TOOLCHAIN,
    )
    kwargs.update(changes)
    return kwargs


def _decide(
    answers: dict[str, Any] | None = None,
    files: dict[str, bytes] | list[bytes] | None = None,
    *,
    plan: tuple[catalogue.Job, ...] = PLAN,
    verified: str | None = None,
    now: float = NOW,
    api: _Api | None = None,
    head: str = HEAD,
    **overrides: str,
) -> main_replay.Replay:
    api = api or _Api(answers if answers is not None else _answers())
    planted = _files() if files is None else files
    texts = list(planted.values()) if isinstance(planted, dict) else planted
    asked: list[int] = []
    checked: list[str] = []

    def records(run_id: int) -> list[bytes]:
        asked.append(run_id)
        return texts

    def check(of: str) -> str | None:
        checked.append(of)
        return verified

    replay = main_replay.decide(
        api, **_kwargs(plan=plan, records=records, verified=check, now=now, **overrides)
    )
    assert set(asked) <= {RUN}, asked
    # The merge verdict is asked of the pull request's head, never of the pushed commit.
    assert checked in ([], [head]), checked
    return replay


def _replayed(replay: main_replay.Replay) -> set[str]:
    return {d.key for d in replay.jobs if d.replayed}


def _edit(path: str, edit):
    def plant(answers: dict[str, Any]) -> None:
        edit(answers[path])

    return plant


def _set(path: str, value: Any):
    def plant(answers: dict[str, Any]) -> None:
        answers[path] = value

    return plant


def _job_of(name: str, **fields: Any):
    def edit(page: dict[str, Any]) -> None:
        for job in page["jobs"]:
            if job["name"] == name:
                job.update(fields)

    return _edit(JOBS, edit)


# ── The one shape that replays ──────────────────────────────────────────────────────────────


def _sha256(value: object) -> str:
    """The digest of a value's canonical JSON, as a reader of the receipt recomputes it."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def test_a_green_first_attempt_on_the_pushed_tree_replays_every_reusable_job() -> None:
    replay = _decide()
    assert replay.replays, replay.reason
    assert _replayed(replay) == set(REPLAYABLE)
    by_key = {d.key: d for d in replay.jobs}
    assert [d.key for d in replay.jobs] == list(KEYS)
    assert not by_key["python-suite"].replayed and "never" in by_key["python-suite"].reason
    assert (replay.pull_request, replay.head, replay.tree, replay.run) == (7, HEAD, TREE, RUN)
    for key in REPLAYABLE:
        assert by_key[key].run_url == f"https://example.com/runs/{RUN}/attempts/1"
    assert by_key["served"].ttl_age_seconds == 3600.0


def test_its_receipt_carries_every_receipt_field_and_each_decision() -> None:
    replay = _decide()
    assert replay.receipt is not None
    read = trust.receipt_from(replay.receipt)
    assert (read.controller_revision, read.head_sha, read.tree_oid) == (CONTROLLER, SHA, TREE)
    assert dict(read.toolchain) == TOOLCHAIN
    assert read.external_revisions == {}
    digests = dict(read.evidence_digests)
    assert set(digests) == {"plan", "attempt-1-jobs", *(f"record:{key}" for key in REPLAYABLE)}
    # Each digest is recomputed from what was planted: the plan's keys and classes, attempt 1's
    # job list as the API answered it, and each replayed verdict file's bytes.
    assert digests["plan"] == _sha256(
        [
            ["rails", "tree"],
            ["served", "ttl:6"],
            ["web-unit (1/2)", "tree"],
            ["web-unit (2/2)", "tree"],
            ["python-suite", "never"],
        ]
    )
    assert digests["attempt-1-jobs"] == _sha256(_answers()[JOBS]["jobs"])
    for key in REPLAYABLE:
        assert digests[f"record:{key}"] == "sha256:" + hashlib.sha256(_record(key)).hexdigest()
    assert [j["key"] for j in replay.receipt["jobs"]] == list(KEYS)
    assert replay.receipt["jobs"] == [d.to_json() for d in replay.jobs]
    jobs_out = {j["key"]: j for j in replay.receipt["jobs"]}
    assert jobs_out["rails"]["decision"] == "replayed"
    assert jobs_out["python-suite"]["decision"] == "run"
    assert replay.receipt["source_run"] == {
        "workflow": WORKFLOW,
        "id": RUN,
        "attempt": 1,
        "url": f"https://example.com/runs/{RUN}/attempts/1",
        "pull_request": 7,
        "head": HEAD,
    }
    lines = replay.lines()
    assert f"replayed rails from {WORKFLOW} run {RUN}, attempt 1" in lines
    assert any(line.startswith("ran python-suite: ") for line in lines)


def test_the_receipt_digests_change_with_what_was_read() -> None:
    """A digest that ignored its input would be the same for another job list or plan."""
    first = _decide().receipt
    answers = _answers()
    answers[JOBS]["jobs"][0]["html_url"] = "https://example.com/jobs/1"
    other_jobs = _decide(answers).receipt
    other_plan = _decide(plan=PLAN[:2]).receipt
    assert first and other_jobs and other_plan
    digests = first["evidence_digests"]
    assert other_jobs["evidence_digests"]["attempt-1-jobs"] != digests["attempt-1-jobs"]
    assert other_plan["evidence_digests"]["plan"] != digests["plan"]


def test_the_merge_verdict_is_asked_of_the_pull_requests_head() -> None:
    asked: list[str] = []

    def verified(head: str) -> str | None:
        asked.append(head)
        return None

    replay = main_replay.decide(_Api(_answers()), **_kwargs(verified=verified))
    assert asked == [HEAD], "the head the pull request's run tested, not the pushed commit"
    assert _replayed(replay) == set(REPLAYABLE)


def test_the_keys_are_run_jobs_own() -> None:
    for job in PLAN:
        shards = [None] if job.shards == 1 else range(1, job.shards + 1)
        assert main_replay.keys_of(job) == [jobs.key_of(job, s) for s in shards]


# ── Shapes that run every job ───────────────────────────────────────────────────────────────


def _no_runs(answers: dict[str, Any]) -> None:
    answers[RUNS] = {"total_count": 0, "workflow_runs": []}


def _first_run(**fields: Any):
    def edit(page: dict[str, Any]) -> None:
        page["workflow_runs"][0].update(fields)

    return _edit(RUNS, edit)


def _second_pull(answers: dict[str, Any]) -> None:
    pulls = answers[PULLS]
    pulls.append({**pulls[0], "number": 8})


def _pull(**fields: Any):
    return _edit(PULLS, lambda pulls: pulls[0].update(fields))


def _uppercase_head(answers: dict[str, Any]) -> None:
    """Every answer agrees, about a head named in uppercase."""
    answers.clear()
    answers.update(_answers("C" * 40))


def _beside(**fields: Any):
    """Another run in the listing, beside the head's own."""

    def edit(page: dict[str, Any]) -> None:
        page["workflow_runs"].append({**page["workflow_runs"][0], **fields})
        page["total_count"] += 1

    return _edit(RUNS, edit)


def _twice(name: str):
    """Attempt 1's list names the job twice: red, then green."""

    def edit(page: dict[str, Any]) -> None:
        [job] = [job for job in page["jobs"] if job["name"] == name]
        page["jobs"].insert(0, {**job, "conclusion": "failure"})
        page["total_count"] += 1

    return _edit(JOBS, edit)


RUN_EVERYTHING = {
    # The pull request merged as the pushed commit, and its head.
    "no pull request": _set(PULLS, []),
    "two pull requests": _second_pull,
    "a pull request merged as another commit": _pull(merge_commit_sha="f" * 40),
    "a pull request naming the commit but not merged": _pull(merged_at=None),
    "a pull request into another branch": _pull(base={"ref": "release"}),
    "a head that is not a full commit id": _uppercase_head,
    "another tree": _set(f"/repos/{REPO}/commits/{HEAD}", {"commit": {"tree": {"sha": "f" * 40}}}),
    "a head behind main": _set(f"/repos/{REPO}/compare/{BEFORE}...{HEAD}", {"behind_by": 1}),
    # The head's merge.yml pull_request runs.
    "no merge.yml run": _no_runs,
    "more runs than one page holds": _edit(RUNS, lambda p: p.update(total_count=500)),
    "runs with no count": _edit(RUNS, lambda p: p.pop("total_count")),
    "runs with a count that is not a number": _edit(RUNS, lambda p: p.update(total_count="2")),
    "runs with a count that is true": _edit(RUNS, lambda p: p.update(total_count=True)),
    "a first run of pr.yml": _first_run(path=".github/workflows/pr.yml"),
    "a first run of another workflow": _first_run(path=".github/workflows/ci.yml"),
    "a first run with no path": _first_run(path=None),
    "a pr.yml run of another head in the listing": _beside(
        id=902, run_number=52, head_sha="f" * 40, path=".github/workflows/pr.yml"
    ),
    "a pr.yml push run in the listing": _beside(
        id=903, run_number=53, event="push", path=".github/workflows/pr.yml"
    ),
    "a listing entry that is not a run": _edit(RUNS, lambda p: p["workflow_runs"].append("902")),
    "a first run made before the pull request": _first_run(created_at="2027-01-15T06:00:00Z"),
    # The pull request's events.
    "a changed base": _set(EVENTS, [{"event": "base_ref_changed"}]),
    "a base changed automatically": _set(EVENTS, [{"event": "automatic_base_change_succeeded"}]),
    "more events than one page holds": _set(EVENTS, [{"event": "labeled"}] * 100),
    "events that are not a list": _set(EVENTS, {"message": "Not Found"}),
    # The first run's attempt 1.
    "attempt 1 still running": _edit(ATTEMPT, lambda a: a.update(status="in_progress")),
    "attempt 1 cancelled": _edit(ATTEMPT, lambda a: a.update(conclusion="cancelled")),
    "attempt 1 timed out": _edit(ATTEMPT, lambda a: a.update(conclusion="timed_out")),
    "an attempt that is not 1": _edit(ATTEMPT, lambda a: a.update(run_attempt=2)),
    "no start time": _edit(ATTEMPT, lambda a: a.pop("run_started_at")),
    "a start time with no zone": _edit(
        ATTEMPT, lambda a: a.update(run_started_at="2027-01-15T08:00:00")
    ),
    # Attempt 1's jobs.
    "jobs over one page": _edit(JOBS, lambda p: p.update(total_count=500)),
    "jobs with no count": _edit(JOBS, lambda p: p.pop("total_count")),
    "a job of a later attempt in the list": _job_of("rails", run_attempt=2),
    "a job named twice in attempt 1's list, red then green": _twice("rails"),
    # Any other answer it cannot read.
    "an api error": _set(JOBS, OSError("rate limited")),
    "an answer that is not an object": _set(ATTEMPT, ["not", "an", "object"]),
}


def test_every_shape_it_cannot_trust_runs_every_job(subtests: Any) -> None:
    assert len(RUN_EVERYTHING) == 36
    for name, plant in RUN_EVERYTHING.items():
        with row(subtests, shape=name):
            answers = _answers()
            plant(answers)
            replay = _decide(answers)
            assert not replay.replays, name
            assert _replayed(replay) == set(), name
            assert [d.key for d in replay.jobs] == list(KEYS), name
            assert all(d.reason for d in replay.jobs), name


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"event": "pull_request"}, id="a pull_request event"),
        pytest.param({"event": "pull_request_target"}, id="a pull_request_target event"),
        pytest.param({"event": "workflow_dispatch"}, id="a dispatch"),
        pytest.param({"ref": "refs/heads/release"}, id="a push to another branch"),
        pytest.param({"ref": "refs/pull/7/merge"}, id="a pull request's merge ref"),
        pytest.param({"before": "0" * 40}, id="a branch's first push"),
        pytest.param({"before": "b" * 39}, id="a short previous commit"),
        pytest.param({"sha": SHA + "\n"}, id="a pushed commit and a newline"),
        pytest.param({"sha": SHA.upper()}, id="an uppercase pushed commit"),
    ],
)
def test_anything_but_a_push_to_main_replays_nothing_and_reads_nothing(overrides) -> None:
    api = _Api(_answers())
    replay = _decide(api=api, **overrides)
    assert not replay.replays and _replayed(replay) == set()
    assert api.read == [], "nothing is read for a run that may never replay"
    assert replay.receipt is None


@pytest.mark.parametrize(
    "refusal",
    [
        pytest.param("the controller's merge-verdict on this head is red", id="a refusal"),
        pytest.param("", id="an empty refusal"),
    ],
)
def test_a_head_the_merge_verdict_does_not_count_runs_every_job(refusal: str) -> None:
    """Only None counts: an empty refusal is still a refusal."""
    replay = _decide(verified=refusal)
    assert not replay.replays and _replayed(replay) == set()
    assert "merge-verdict" in replay.reason


def test_a_verdict_check_that_raises_runs_every_job() -> None:
    def boom(head: str) -> str | None:
        raise RuntimeError("the check-runs read failed")

    replay = main_replay.decide(_Api(_answers()), **_kwargs(verified=boom))
    assert _replayed(replay) == set() and "check-runs read failed" in replay.reason


@pytest.mark.parametrize(
    "other",
    [
        pytest.param({"head_sha": "f" * 40}, id="a run of another head"),
        pytest.param({"event": "push"}, id="a push run on the head"),
    ],
)
def test_a_lower_numbered_run_that_is_not_the_heads_pull_request_run_is_never_read(
    other: dict[str, str],
) -> None:
    """Listed beside the head's runs, numbered first and green on attempt 1, it is still not the
    first run: the replay comes from run 900, and run 1 is never read."""
    answers = _answers()
    runs = answers[RUNS]["workflow_runs"]
    runs.append({**runs[0], "id": 1, "run_number": 1, **other})
    answers[RUNS]["total_count"] += 1
    answers[f"/repos/{REPO}/actions/runs/1/attempts/1"] = answers[ATTEMPT]
    answers[f"/repos/{REPO}/actions/runs/1/attempts/1/jobs"] = answers[JOBS]
    api = _Api(answers)
    replay = _decide(api=api)
    assert replay.run == RUN
    assert _replayed(replay) == set(REPLAYABLE)
    assert not [p for p in api.read if "/runs/1/" in p], api.read


def test_an_unreadable_verdict_file_runs_every_job(subtests: Any) -> None:
    """A record with no quarantined list is refused by the reader itself, so it runs every job:
    stricter than running only its own job, and what that file held is not known."""
    good = list(_files().values())
    planted = {
        "not JSON": b"{",
        "not UTF-8": b"\xff\xfe",
        "a record with no quarantined list": _record("rails").replace(
            b'"quarantined": [],\n ', b""
        ),
        "a record of another schema": _record("rails").replace(b"job-record/2", b"job-record/1"),
    }
    assert len(planted) == 4
    for name, bad in planted.items():
        with row(subtests, file=name):
            replay = _decide(files=[bad, *good[1:]])
            assert _replayed(replay) == set(), name
            assert "cannot be read" in replay.reason, replay.reason


def test_verdict_files_that_are_not_a_list_run_every_job() -> None:
    def records(run_id: int) -> Any:
        return iter(list(_files().values()))

    replay = main_replay.decide(_Api(_answers()), **_kwargs(records=records))
    assert _replayed(replay) == set() and "not a list" in replay.reason


# ── Shapes that run one job and replay the rest ─────────────────────────────────────────────


def _file(key: str, **kw: Any):
    def plant(files: dict[str, bytes]) -> None:
        files[key] = _record(key, **kw)

    return plant


def _drop(key: str):
    def plant(files: dict[str, bytes]) -> None:
        del files[key]

    return plant


def _again(key: str):
    """A second verdict file for *key*, as green as the first."""

    def plant(files: dict[str, bytes]) -> None:
        files[f"{key}, again"] = _record(key)

    return plant


def _both(*plants):
    def plant(answers: dict[str, Any]) -> None:
        for each in plants:
            each(answers)

    return plant


#: Each shape, the answers' plant and the files' plant, both on rails's own entries.
RUN_ONE = {
    # A run with a red job concludes failure, as GitHub reports it; its green jobs still replay.
    "red in attempt 1": (
        _both(
            _job_of("rails", conclusion="failure"),
            _edit(ATTEMPT, lambda a: a.update(conclusion="failure")),
        ),
        None,
    ),
    "cancelled in attempt 1": (_job_of("rails", conclusion="cancelled"), None),
    "skipped in attempt 1": (_job_of("rails", conclusion="skipped"), None),
    "still running in attempt 1": (_job_of("rails", status="in_progress", conclusion=None), None),
    "running in attempt 1 yet naming success": (
        _job_of("rails", status="in_progress", conclusion="success"),
        None,
    ),
    "not in attempt 1's jobs": (
        _edit(JOBS, lambda p: p["jobs"].pop(0) and p.update(total_count=len(KEYS) - 1)),
        None,
    ),
    "no verdict file": (None, _drop("rails")),
    "a verdict file of attempt 2": (None, _file("rails", attempt=2)),
    "a red verdict file": (None, _file("rails", result=verdict.FAILURE)),
    "a verdict file of another head": (
        None,
        _file("rails", identity=verdict.Identity("f" * 40, {})),
    ),
    "a verdict file that read a partner": (
        None,
        _file("rails", identity=verdict.Identity(HEAD, {"apps-partner": "9" * 40})),
    ),
    "two verdict files for it": (None, _again("rails")),
}


def test_a_job_attempt_1_did_not_pass_runs_and_the_rest_replay(subtests: Any) -> None:
    assert len(RUN_ONE) == 12
    for name, (answers_plant, files_plant) in RUN_ONE.items():
        with row(subtests, shape=name):
            answers, files = _answers(), _files()
            if answers_plant:
                answers_plant(answers)
            if files_plant:
                files_plant(files)
            replay = _decide(answers, files)
            assert _replayed(replay) == set(REPLAYABLE) - {"rails"}, name
            [rails] = [d for d in replay.jobs if d.key == "rails"]
            assert rails.reason, name


def test_a_job_green_only_on_a_later_attempt_runs_and_the_later_attempt_is_never_read() -> None:
    answers = _answers()
    _job_of("rails", conclusion="failure")(answers)
    answers[f"/repos/{REPO}/actions/runs/{RUN}/attempts/2"] = {
        "status": "completed",
        "conclusion": "success",
    }
    answers[f"/repos/{REPO}/actions/runs/{RUN}/attempts/2/jobs"] = {"total_count": 0, "jobs": []}
    api = _Api(answers)
    replay = _decide(api=api)
    assert "rails" not in _replayed(replay)
    assert not [p for p in api.read if "/attempts/2" in p or "/runs/901" in p], api.read


def test_a_job_green_only_in_a_later_run_runs() -> None:
    """The first run decides; a second, green run on the same head is never read."""
    answers = _answers()
    _job_of("rails", conclusion="failure")(answers)
    api = _Api(answers)
    replay = _decide(api=api)
    assert "rails" not in _replayed(replay)
    assert not [p for p in api.read if "/runs/901" in p]


def test_the_first_run_is_the_lowest_numbered_whichever_order_the_api_lists_them_in() -> None:
    answers = _answers()
    answers[RUNS]["workflow_runs"].reverse()
    assert _decide(answers).run == RUN


@pytest.mark.parametrize(
    ("age", "replayed"),
    [
        pytest.param(0.0, True, id="just started"),
        pytest.param(6 * 3600 - 1, True, id="inside its ttl"),
        pytest.param(6 * 3600, False, id="at its ttl"),
        pytest.param(6 * 3600 + 1, False, id="past its ttl"),
        pytest.param(-1.0, False, id="started in the future"),
    ],
)
def test_a_ttl_job_replays_only_inside_its_ttl(age: float, replayed: bool) -> None:
    replay = _decide(now=STARTED + age)
    [served] = [d for d in replay.jobs if d.key == "served"]
    assert served.replayed is replayed
    assert served.ttl_age_seconds == age
    assert "rails" in _replayed(replay)


def test_a_sharded_job_replays_only_when_every_shard_does() -> None:
    answers = _answers()
    _job_of("web-unit (2/2)", conclusion="failure")(answers)
    replay = _decide(answers)
    by_key = {d.key: d for d in replay.jobs}
    assert not by_key["web-unit (1/2)"].replayed
    assert "another shard" in by_key["web-unit (1/2)"].reason
    assert not by_key["web-unit (2/2)"].replayed
    assert "rails" in _replayed(replay)


@pytest.mark.parametrize(
    ("job", "why"),
    [
        pytest.param(_job("rails", "never"), "its class is never", id="class never"),
        pytest.param(_job("rails", None), "its class is never", id="no class"),
        pytest.param(
            _job("rails", security="security"),
            "security evidence is never replayed (class never)",
            id="security",
        ),
        pytest.param(
            _job("rails", "ttl:24", live=True),
            "live evidence is never replayed (class never)",
            id="live",
        ),
        pytest.param(
            _job("rails", external_inputs=("advisory-feed",)),
            "live evidence is never replayed (class never)",
            id="a live feed",
        ),
    ],
)
def test_a_never_job_runs_whatever_attempt_1_did(job: catalogue.Job, why: str) -> None:
    """Refused by the never rule itself, before its attempt-1 job or verdict file is judged."""
    replay = _decide(plan=(job,))
    [decision] = replay.jobs
    assert not decision.replayed and decision.reuse == "never"
    assert decision.reason == why


# ── The quarantine: a file run-job writes ──────────────────────────────────────────────────

_QUARANTINED_JUNIT = """<testsuites><testsuite>
<testcase classname="tests.test_a" name="test_flaky">
  <properties><property name="quarantine_issue" value="4000"/></properties>
  <skipped type="pytest.xfail" message="quarantined"/>
</testcase>
</testsuite></testsuites>"""


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Planted",
        GIT_AUTHOR_EMAIL="planted@example.invalid",
        GIT_COMMITTER_NAME="Planted",
        GIT_COMMITTER_EMAIL="planted@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return done.stdout.strip()


def _run_job_record(tmp_path: Path, junit: str | None) -> tuple[str, bytes]:
    """Run a planted job through run-job's step path; return the head and the record it wrote."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("reports/\n/.local/\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "planted")
    program = (
        "import os; os.makedirs('reports', exist_ok=True);"
        f" open('reports/junit.xml', 'w').write({junit or '<testsuites/>'!r})"
    )
    job = _job("rails", command=(catalogue.Step((sys.executable, "-c", program)),))
    job = dataclasses.replace(job, junit=("reports/junit.xml",))
    host = jobs.Host(root=repo, family="ubuntu-", path=os.environ.get("PATH", os.defpath))
    outcome = jobs.run_one(
        catalogue.Catalogue((job,)), job, None, host, jobs.Options(), tmp_path / "work"
    )
    assert outcome.record is not None, outcome.reasons
    return _git(repo, "rev-parse", "HEAD"), outcome.record.read_bytes()


@pytest.mark.parametrize(
    ("junit", "replayed"),
    [
        pytest.param(_QUARANTINED_JUNIT, False, id="a quarantined failure"),
        pytest.param(None, True, id="no quarantined failure (the control)"),
    ],
)
def test_a_pass_with_a_quarantined_failure_is_never_replayed(
    tmp_path: Path, junit: str | None, replayed: bool
) -> None:
    head, record = _run_job_record(tmp_path, junit)
    answers = _answers(head)
    answers[JOBS] = {
        "total_count": 1,
        "jobs": [
            {"name": "rails", "status": "completed", "conclusion": "success", "run_attempt": 1}
        ],
    }
    replay = _decide(answers, [record], plan=(_job("rails"),), head=head)
    [decision] = replay.jobs
    assert decision.replayed is replayed, decision.reason
    if not replayed:
        assert "quarantined" in decision.reason


# ── The module ──────────────────────────────────────────────────────────────────────────────


def test_bad_caller_inputs_are_refused_before_anything_is_read(subtests: Any) -> None:
    """A now that is not a finite number would pass every ttl bound (NaN compares false)."""
    bad: dict[str, dict[str, Any]] = {
        "a short controller": {"controller_revision": "e" * 39},
        "a controller and a newline": {"controller_revision": CONTROLLER + "\n"},
        "no toolchain": {"toolchain": {}},
        "an empty toolchain version": {"toolchain": {"python": ""}},
        "a toolchain version that is not text": {"toolchain": {"python": 3}},
        "now not a number": {"now": math.nan},
        "now infinite": {"now": math.inf},
        "now a bool": {"now": True},
        "now as text": {"now": "1800003600"},
        "no workflow": {"workflow": ""},
        "a workflow path": {"workflow": "x/merge.yml"},
        "a workflow that is not text": {"workflow": 123},
        "a plan naming a job twice": {"plan": (*PLAN, _job("rails"))},
        "a plan of names": {"plan": ("rails",)},
        "no pushed commit": {"sha": None},
        "no previous commit": {"before": None},
        "no event": {"event": None},
        "no ref": {"ref": None},
        "no repository": {"repo": None},
    }
    for name, change in bad.items():
        with row(subtests, input=name):
            api = _Api(_answers())
            with pytest.raises(main_replay.Refused):
                main_replay.decide(api, **_kwargs(**change))
            assert api.read == []


def test_it_imports_only_the_standard_library_and_its_siblings() -> None:
    source = Path(main_replay.__file__).read_text(encoding="utf-8")
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            assert (node.module or "").split(".")[0] in allowed, node.module
