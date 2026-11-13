"""``python -m tools.ci verdict --records DIR --run-id N``: the per-job records a run's test stage
uploaded, judged with GitHub's ``toJSON(needs)`` against the plan's matrix keys. A planned key
with no record is missing, so a matrix entry that never started reads red; a record of another
head is stale; a record of another run, or of none, is not this run's; a record claiming a later
attempt than the run's is refused, and on a rerun a key attempt 1 left no record for stays red,
unless attempt 1's jobs show no runner took its job and attempt 2's record fills it.
Each record must sit alone in the folder ``record_artifact`` names for it in this run."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from tools.ci import __main__ as ci
from tools.ci import verdict

pytestmark = pytest.mark.security

HEAD = "a" * 40
OLD_HEAD = "b" * 40
PARTNER = "c" * 40
RUN = 4242

NEEDS = {
    "plan": {"result": "success", "outputs": {}},
    "test": {"result": "success", "outputs": {}},
}
KEYS = ["static", "suite (1/2)", "suite (2/2)"]


@pytest.fixture(autouse=True)
def _no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _record(
    key: str,
    *,
    result: str = "success",
    head: str = HEAD,
    attempt: int = 1,
    run_id: int | None = RUN,
    partners: dict[str, str] | None = None,
    **more: object,
) -> str:
    steps = [(["true"], 0 if result == "success" else 1, 0.5)]
    return verdict.job_record(
        key=key,
        result=result,
        identity=verdict.Identity(head, {"apps": PARTNER} if partners is None else partners),
        steps=steps,
        attempt=attempt,
        run_id=run_id,
        **more,  # type: ignore[arg-type]
    )


def _folder(key: str, attempt: int = 1, run_id: int = RUN) -> str:
    """The folder download-artifact gives the artifact this run's entry uploads."""
    return verdict.record_artifact(key, run_id=run_id, attempt=attempt)


def _named(*texts: str) -> dict[str, str]:
    """Each record in the folder its own key and attempt name in this run."""
    out: dict[str, str] = {}
    for text in texts:
        data = json.loads(text)
        out[_folder(data["key"], data["attempt"])] = text
    return out


def _setup(
    tmp_path: Path,
    records: dict[str, str],
    *,
    keys: list[str] = KEYS,
    needs: dict = NEEDS,
    key_partners: object = None,
) -> list[str]:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(keys))
    needs_file = tmp_path / "needs.json"
    needs_file.write_text(json.dumps(needs))
    folder = tmp_path / "records"
    folder.mkdir(exist_ok=True)
    for name, text in records.items():
        # download-artifact puts each artifact in a folder of its own.
        (folder / name).mkdir(parents=True, exist_ok=True)
        (folder / name / "record.json").write_text(text)
    args = [
        "--plan", str(plan), "--needs", str(needs_file), "--records", str(folder),
        "--run-id", str(RUN),
        "--head", HEAD, "--partner", f"apps={PARTNER}",
        "--run-head", HEAD, "--run-partner", f"apps={PARTNER}",
    ]  # fmt: skip
    if key_partners is not None:
        by_key = tmp_path / "key-partners.json"
        by_key.write_text(
            key_partners if isinstance(key_partners, str) else json.dumps(key_partners)
        )
        args += ["--key-partners", str(by_key)]
    return args


def _all_green(attempt: int = 1) -> dict[str, str]:
    return _named(*(_record(k, attempt=attempt) for k in KEYS))


def _run(args: list[str]) -> int:
    try:
        return ci.main(["verdict", *args])
    except SystemExit as exc:
        pytest.fail(f"SystemExit({exc.code}) escaped the dispatcher")


def _without(args: list[str], option: str) -> list[str]:
    at = args.index(option)
    return args[:at] + args[at + 2 :]


def test_every_planned_key_with_a_green_record_reads_green(tmp_path: Path) -> None:
    assert _run(_setup(tmp_path, _all_green())) == 0


def test_a_planned_key_with_no_record_reads_red_as_missing(tmp_path, capsys) -> None:
    records = _all_green()
    del records[_folder("suite (2/2)")]
    assert _run(_setup(tmp_path, records)) == 1
    assert "`suite (2/2)`: missing" in capsys.readouterr().out


def test_a_red_record_reads_red(tmp_path: Path) -> None:
    records = _all_green()
    records.update(_named(_record("suite (1/2)", result="failure")))
    assert _run(_setup(tmp_path, records)) == 1


def test_a_record_of_an_older_head_reads_red_as_stale(tmp_path, capsys) -> None:
    records = _all_green()
    records.update(_named(_record("static", head=OLD_HEAD)))
    assert _run(_setup(tmp_path, records)) == 1
    assert "`static`: stale head" in capsys.readouterr().out


# A record names only the partners its own job read: the merge tier's apps-contract names the
# apps checkout, and every other entry names none. ``--key-partners`` gives each planned key's
# partners from the plan, and each record must name exactly those.

#: Each key's partners, as the plan gives them: suite (2/2) stands in for apps-contract.
KEY_PARTNERS = {"static": [], "suite (1/2)": [], "suite (2/2)": ["apps"]}


def _merge_tier_records(apps: str = PARTNER, **more: dict[str, str]) -> dict[str, str]:
    partners = {"static": {}, "suite (1/2)": {}, "suite (2/2)": {"apps": apps}}
    partners.update({key.replace("_", " "): named for key, named in more.items()})
    return _named(*(_record(key, partners=named) for key, named in partners.items()))


def test_records_naming_their_own_partners_or_none_read_green(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _merge_tier_records(), key_partners=KEY_PARTNERS)
    assert _run(args) == 0, capsys.readouterr().out


def test_a_record_of_an_older_partner_head_reads_red_as_stale(tmp_path, capsys) -> None:
    assert (
        _run(_setup(tmp_path, _merge_tier_records(apps=OLD_HEAD), key_partners=KEY_PARTNERS)) == 1
    )
    out = capsys.readouterr().out
    assert "`suite (2/2)`: stale partner" in out
    assert "`static`" not in out


def test_a_partner_record_naming_none_reads_red(tmp_path, capsys) -> None:
    records = _merge_tier_records()
    records.update(_named(_record("suite (2/2)", partners={})))
    assert _run(_setup(tmp_path, records, key_partners=KEY_PARTNERS)) == 1
    assert (
        "`suite (2/2)`: stale partner (names no apps, which its job reads"
        in capsys.readouterr().out
    )


def test_a_record_of_a_job_that_reads_no_partner_naming_one_reads_red(tmp_path, capsys) -> None:
    records = _merge_tier_records()
    records.update(_named(_record("static", partners={"apps": PARTNER})))
    assert _run(_setup(tmp_path, records, key_partners=KEY_PARTNERS)) == 1
    assert "`static`: stale partner" in capsys.readouterr().out


def test_a_partner_the_candidate_does_not_name_reads_red(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _merge_tier_records(), key_partners=KEY_PARTNERS)
    assert _run(_without(_without(args, "--partner"), "--run-partner")) == 1
    out = capsys.readouterr().out
    assert "`suite (2/2)`: stale partner" in out
    assert "which the candidate (none) does not name" in out


def test_without_key_partners_a_record_is_held_to_the_candidates_partners(tmp_path, capsys) -> None:
    # Fail closed: with no partners by key, a record naming none of the candidate's is stale.
    assert _run(_setup(tmp_path, _merge_tier_records())) == 1
    out = capsys.readouterr().out
    assert "`static`: stale partner" in out and "`suite (1/2)`: stale partner" in out


@pytest.mark.parametrize(
    ("name", "text", "why"),
    [
        ("not JSON", "{", "--key-partners is not JSON"),
        ("not an object", json.dumps(list(KEY_PARTNERS)), "a mapping of"),
        (
            "a planned key missing",
            json.dumps({"static": [], "suite (1/2)": []}),
            "name no partners for",
        ),
        (
            "a key outside the plan",
            json.dumps({**KEY_PARTNERS, "lint": []}),
            "which is not planned",
        ),
        ("a partner that is not a name", json.dumps({**KEY_PARTNERS, "static": [1]}), "a list of"),
    ],
)
def test_key_partners_it_cannot_read_are_refused(tmp_path, capsys, name, text, why) -> None:
    assert _run(_setup(tmp_path, _merge_tier_records(), key_partners=text)) == 2, name
    assert why in capsys.readouterr().err


def test_key_partners_without_records_are_refused(tmp_path, capsys) -> None:
    args = _without(
        _without(_setup(tmp_path, {}, key_partners=KEY_PARTNERS), "--records"), "--run-id"
    )
    assert _run(args) == 2
    assert "--key-partners is read only with --records" in capsys.readouterr().err


def test_key_partners_given_twice_are_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _merge_tier_records(), key_partners=KEY_PARTNERS)
    assert _run([*args, "--key-partners", args[-1]]) == 2
    assert "argument --key-partners: given twice" in capsys.readouterr().err


def test_records_are_read_as_naming_their_own_partners() -> None:
    (result,) = verdict.from_job_records([_record("static", partners={})])
    assert result.own_partners


def test_needs_results_carry_the_runs_whole_identity() -> None:
    results = verdict.from_needs(json.dumps(NEEDS), verdict.Identity(HEAD, {}))
    assert results and not any(r.own_partners for r in results)


def test_a_cancelled_matrix_job_in_needs_reads_red_even_with_every_record(tmp_path) -> None:
    needs = dict(NEEDS, test={"result": "cancelled", "outputs": {}})
    assert _run(_setup(tmp_path, _all_green(), needs=needs)) == 1


# ── a push to main's replaced shard (merge.yml's results, --superseded) ───────────────────

NEWER = "e" * 40


def _replaced(tmp_path: Path) -> list[str]:
    """``suite (2/2)`` never started, so it left no record, and its stage reads cancelled in
    needs; superseded.py proved a newer push's waiting shard replaced it."""
    records = _all_green()
    del records[_folder("suite (2/2)")]
    args = _setup(tmp_path, records, needs=dict(NEEDS, test={"result": "cancelled", "outputs": {}}))
    proof = tmp_path / "superseded.json"
    proof.write_text(json.dumps({"suite (2/2)": NEWER}))
    return [*args, "--superseded", str(proof)]


def test_a_push_to_main_reads_a_replaced_shard_and_its_stage_superseded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    assert _run(_replaced(tmp_path)) == 1, "a replaced shard is still red"
    out = capsys.readouterr().out
    assert "superseded" in out and NEWER in out, out
    assert "missing" not in out and "cancelled" not in out, out


def test_superseded_is_refused_on_any_run_but_a_push_to_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/41/merge")
    assert _run(_replaced(tmp_path)) == 2
    assert "only a push to refs/heads/main" in capsys.readouterr().err


def test_superseded_that_is_not_a_mapping_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    args = _replaced(tmp_path)
    Path(args[-1]).write_text("[]")
    assert _run(args) == 2


# ── the failed tests the records name ────────────────────────────────────────────────────────

FLAKY = "tests/test_x.py::test_flaky"


def test_a_record_whose_only_failure_is_a_flake_reads_red_and_lists_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unquarantined flaky failure reads red: the rerun's word is recorded, in the output and
    the step summary, and waives nothing."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    rerun = {
        "argv": ["pytest", "-n", "0", FLAKY],
        "exit": 0,
        "tests": [{"test": FLAKY, "rerun_result": "flake", "why": "passed rerun alone"}],
    }
    records = _all_green()
    records.update(_named(_record("suite (1/2)", result="failure", rerun=rerun)))
    assert _run(_setup(tmp_path, records)) == 1
    out = capsys.readouterr().out
    assert "- `suite (1/2)`: failure (attempt 1)" in out
    listed = f"- `suite (1/2)` attempt 1: `{FLAKY}`: flake (passed rerun alone)"
    assert listed in out
    assert listed in summary.read_text()


def test_the_same_failure_under_a_quarantine_entry_reads_green_and_is_listed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    records = _all_green()
    records.update(_named(_record("suite (1/2)", quarantined=[FLAKY])))
    assert _run(_setup(tmp_path, records)) == 0
    out = capsys.readouterr().out
    assert out.startswith("### Verdict: green\n")
    assert f"- `suite (1/2)` attempt 1: `{FLAKY}`: quarantined" in out


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
def test_a_changes_run_with_no_changed_list_waives_no_quarantined_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    """The runner's event says the run is a change's; with no changed list to show the change
    cannot reach the quarantined test, its failure is red, and says why."""
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    records = _all_green()
    records.update(_named(_record("suite (1/2)", quarantined=[FLAKY])))
    assert _run(_setup(tmp_path, records)) == 1
    out = capsys.readouterr().out
    assert f"- `suite (1/2)`: {verdict.UNWAIVED} (`{FLAKY}` failed under quarantine" in out
    assert "given no changed list" in out


def test_the_step_summary_lists_failed_tests_within_its_bound_and_the_log_lists_every_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub refuses a step summary over 1 MiB whole, and with it the verdict's reasons: a mass
    failure's summary lists the failed tests within ``verdict.SUMMARY_TESTS_BYTES`` and says how
    many more the log lists, and the log lists every one."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    records = _all_green()
    for key in ("suite (1/2)", "suite (2/2)"):
        tests = [
            {
                "test": f"tests/test_x.py::test_{n:04}[{key[7]}-{'y' * 400}]",
                "rerun_result": "repeated failure",
                "why": "failed again alone",
            }
            for n in range(1500)
        ]
        rerun = {"argv": ["pytest", "-n", "0"], "exit": 1, "tests": tests}
        records.update(_named(_record(key, result="failure", rerun=rerun)))
    assert _run(_setup(tmp_path, records)) == 1
    out = capsys.readouterr().out
    written = summary.read_text()
    assert len(written.encode()) <= 1024 * 1024
    assert written.startswith("### Verdict: red\n")
    assert "- `suite (1/2)`: failure (attempt 1)" in written
    assert "- `suite (2/2)`: failure (attempt 1)" in written
    # The log's list, apart from the notices that name the first few for the retry note.
    listed = "".join(line for line in out.splitlines(True) if not line.startswith("::notice "))
    assert listed.count("tests/test_x.py::test_") == 3000
    shown = written.count("tests/test_x.py::test_")
    assert 0 < shown < 3000
    assert written.endswith(f"- {3000 - shown} more failed tests: the log lists every one.\n")
    assert out.startswith(written.rsplit("\n- ", 1)[0])


# ── the run a record names ───────────────────────────────────────────────────────────────────


def test_a_record_of_another_run_in_this_runs_folder_reads_red(tmp_path, capsys) -> None:
    records = _all_green()
    records[_folder("static")] = _record("static", run_id=111)
    assert _run(_setup(tmp_path, records)) == 1
    assert f"`static`: another run (recorded by run 111, not run {RUN})" in capsys.readouterr().out


def test_a_local_record_naming_no_run_reads_red(tmp_path, capsys) -> None:
    # run-job writes run_id null off GitHub: a local result never stands in for GitHub's.
    records = _all_green()
    records[_folder("suite (1/2)")] = _record("suite (1/2)", run_id=None)
    assert _run(_setup(tmp_path, records)) == 1
    assert "`suite (1/2)`: no run" in capsys.readouterr().out


def test_records_of_another_run_in_that_runs_folders_are_refused(tmp_path, capsys) -> None:
    records = {_folder(k, run_id=111): _record(k, run_id=111) for k in KEYS}
    assert _run(_setup(tmp_path, records)) == 2
    err = capsys.readouterr().err
    assert _folder("static", run_id=111) in err and _folder("static") in err


@pytest.mark.parametrize("name", ["anything-0", "ci-record-static", "ci-record-static-4242-2"])
def test_a_renamed_artifact_folder_is_refused(tmp_path, capsys, name) -> None:
    records = _all_green()
    records[name] = records.pop(_folder("static"))
    assert _run(_setup(tmp_path, records)) == 2
    assert f"{name} holds the record of 'static' on attempt 1" in capsys.readouterr().err


def test_a_second_upload_of_one_record_is_refused(tmp_path, capsys) -> None:
    records = _all_green()
    records["ci-record-static-again"] = _record("static", result="failure")
    assert _run(_setup(tmp_path, records)) == 2
    assert "ci-record-static-again holds the record of 'static'" in capsys.readouterr().err


def test_two_records_in_one_artifact_folder_are_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    folder = tmp_path / "records" / _folder("static")
    (folder / "other.json").write_text(_record("suite (1/2)", result="failure"))
    assert _run(args) == 2
    assert "holds 2 entries, not one record" in capsys.readouterr().err


def test_an_attempt_1_folder_holding_an_attempt_2_record_is_refused(tmp_path, capsys) -> None:
    records = _all_green()
    records[_folder("static", 1)] = _record("static", attempt=2)
    assert _run([*_setup(tmp_path, records), "--attempt", "2"]) == 2
    assert "holds the record of 'static' on attempt 2" in capsys.readouterr().err


def test_records_without_the_runs_id_are_refused(tmp_path, capsys) -> None:
    args = _without(_setup(tmp_path, _all_green()), "--run-id")
    assert _run(args) == 2
    assert "--records needs --run-id" in capsys.readouterr().err


def test_a_run_id_without_records_is_refused(tmp_path, capsys) -> None:
    args = _without(_setup(tmp_path, _all_green()), "--records")
    assert _run(args) == 2
    assert "--run-id is read only with --records" in capsys.readouterr().err


@pytest.mark.parametrize(
    "run_id",
    ["0", "-1", "x", "1.5", "", " 7", "7\n", "+7", "1" * 20, "\u0661", "\uff12", "7\u0661"],
)
def test_a_run_id_that_is_not_a_whole_number_from_1_is_refused(tmp_path, capsys, run_id) -> None:
    args = _setup(tmp_path, _all_green())
    args[args.index("--run-id") + 1] = run_id
    assert _run(args) == 2
    assert "--run-id must be a whole number from 1" in capsys.readouterr().err


def test_a_run_id_given_twice_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    assert _run([*args, "--run-id", str(RUN)]) == 2
    assert "--run-id: given twice" in capsys.readouterr().err


# ── attempts ─────────────────────────────────────────────────────────────────────────────────


def test_a_red_on_an_earlier_attempt_stays_red_beside_a_later_green(tmp_path) -> None:
    records = _all_green()
    records.update(_named(_record("static", result="failure", attempt=1)))
    records.update(_named(_record("static", attempt=2)))
    assert _run([*_setup(tmp_path, records), "--attempt", "2"]) == 1


def test_a_rerun_carries_every_key_attempt_1_recorded_green(tmp_path) -> None:
    assert _run([*_setup(tmp_path, _all_green()), "--attempt", "2"]) == 0


def test_a_rerun_of_every_job_reads_green_when_both_attempts_are(tmp_path) -> None:
    records = {**_all_green(1), **_all_green(2)}
    assert _run([*_setup(tmp_path, records), "--attempt", "2"]) == 0


def test_a_rerun_never_fills_a_record_attempt_1_left_empty(tmp_path, capsys) -> None:
    # Attempt 1's runner for suite (2/2) died before it wrote a record; "re-run failed jobs"
    # gives attempt 2, whose rerun is green. A red is never rerun into green, and neither is a
    # record attempt 1 never wrote.
    records = _named(_record("static"), _record("suite (1/2)"), _record("suite (2/2)", attempt=2))
    assert _run([*_setup(tmp_path, records), "--attempt", "2"]) == 1
    assert "`suite (2/2)`: missing on attempt 1" in capsys.readouterr().out


# ── a job no runner took on attempt 1 ────────────────────────────────────────────────────────

#: The planned key attempt 1 left with no record, in these tests.
_LEFT = "suite (2/2)"
#: Attempt 1's records of the rest, and the rerun's green record of the one left.
_RERUN = (_record("static"), _record("suite (1/2)"), _record(_LEFT, attempt=2))


def _api_job(
    name: str,
    *,
    conclusion: object = "success",
    status: str = "completed",
    steps: int = 3,
    runner: object = "GitHub Actions 7",
    runner_id: object = 7,
    run_attempt: int = 1,
) -> dict[str, object]:
    """One job as the Actions API lists it on an attempt; ``...`` leaves a runner field out."""
    job: dict[str, object] = {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "run_id": RUN,
        "run_attempt": run_attempt,
        "steps": [{"name": f"step {n}", "conclusion": "success"} for n in range(1, steps + 1)],
    }
    if runner is not ...:
        job["runner_name"] = runner
    if runner_id is not ...:
        job["runner_id"] = runner_id
    return job


#: The job GitHub cancels when no runner took it ("The job was not acquired by Runner of type
#: hosted even after multiple attempts"): completed and cancelled, no step, no runner name or id.
_NEVER_ACQUIRED = {"conclusion": "cancelled", "steps": 0, "runner": "", "runner_id": None}


def _listing(tmp_path: Path, jobs: list[dict[str, object]]) -> list[str]:
    path = tmp_path / "first-jobs.json"
    path.write_text(json.dumps([{"total_count": len(jobs), "jobs": jobs}]))
    return ["--first-jobs", str(path)]


def _first_jobs(tmp_path: Path, left: dict[str, object], *, attempt: int = 1) -> list[str]:
    """Attempt 1's jobs, each green but the one left, as ``gh api --paginate --slurp`` lists
    them, given as --first-jobs."""
    jobs = [_api_job(k, run_attempt=attempt) for k in KEYS if k != _LEFT]
    jobs.append(_api_job(_LEFT, run_attempt=attempt, **left))  # type: ignore[arg-type]
    return _listing(tmp_path, jobs)


@pytest.mark.parametrize(
    ("runner", "runner_id"),
    [("", None), (None, None), ("", 0), (None, 0)],
    ids=["no runner name or id", "null name, no id", "no name, id 0", "null name, id 0"],
)
def test_a_job_no_runner_took_on_attempt_1_is_filled_by_attempt_2s_green_record(
    tmp_path, capsys, runner, runner_id
) -> None:
    # No step ran, so it left no record and no result to hide: attempt 1's listing says so, and
    # the rerun's green attempt-2 record stands for it.
    left = {**_NEVER_ACQUIRED, "runner": runner, "runner_id": runner_id}
    args = [*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *_first_jobs(tmp_path, left)]
    assert _run(args) == 0, capsys.readouterr().out


def test_a_job_no_runner_took_and_never_rerun_stays_missing(tmp_path, capsys) -> None:
    records = _named(_record("static"), _record("suite (1/2)"))
    args = [*_setup(tmp_path, records), "--attempt", "2", *_first_jobs(tmp_path, _NEVER_ACQUIRED)]
    assert _run(args) == 1
    assert f"`{_LEFT}`: missing (no result for a planned job)" in capsys.readouterr().out


def test_a_job_no_runner_took_whose_rerun_failed_stays_red(tmp_path, capsys) -> None:
    records = _named(
        _record("static"), _record("suite (1/2)"), _record(_LEFT, result="failure", attempt=2)
    )
    args = [*_setup(tmp_path, records), "--attempt", "2", *_first_jobs(tmp_path, _NEVER_ACQUIRED)]
    assert _run(args) == 1
    assert f"`{_LEFT}`: failure (attempt 2)" in capsys.readouterr().out


def test_a_job_no_runner_took_is_filled_by_attempt_2_alone(tmp_path, capsys) -> None:
    # Attempt 2's runner took the job and died with no record; a green attempt 3 does not stand
    # for it: only attempt 2's record fills what attempt 1 never ran.
    records = _named(_record("static"), _record("suite (1/2)"), _record(_LEFT, attempt=3))
    args = [*_setup(tmp_path, records), "--attempt", "3", *_first_jobs(tmp_path, _NEVER_ACQUIRED)]
    assert _run(args) == 1
    assert f"`{_LEFT}`: missing on attempt 2" in capsys.readouterr().out


def test_every_later_attempt_must_be_green_for_a_job_no_runner_took(tmp_path, capsys) -> None:
    records = _named(
        _record("static"),
        _record("suite (1/2)"),
        _record(_LEFT, result="failure", attempt=2),
        _record(_LEFT, attempt=3),
    )
    args = [*_setup(tmp_path, records), "--attempt", "3", *_first_jobs(tmp_path, _NEVER_ACQUIRED)]
    assert _run(args) == 1
    assert f"`{_LEFT}`: red on an earlier attempt" in capsys.readouterr().out


@pytest.mark.parametrize(
    "left",
    [
        pytest.param({"conclusion": "failure", "steps": 5}, id="listed as failed"),
        pytest.param(_NEVER_ACQUIRED, id="listed as no runner took it"),
    ],
)
def test_a_job_that_ran_and_failed_on_attempt_1_stays_red_after_a_green_rerun(
    tmp_path, capsys, left
) -> None:
    # Attempt 1 recorded a red, so it ran: a rerun never turns that green, whatever the listing
    # says of its job.
    records = _named(
        _record("static"),
        _record("suite (1/2)"),
        _record(_LEFT, result="failure"),
        _record(_LEFT, attempt=2),
    )
    args = [*_setup(tmp_path, records), "--attempt", "2", *_first_jobs(tmp_path, left)]
    assert _run(args) == 1
    assert f"`{_LEFT}`: red on an earlier attempt" in capsys.readouterr().out


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"status": "in_progress"}, id="still going"),
        pytest.param({"status": "queued"}, id="queued"),
        pytest.param({"status": "waiting"}, id="waiting"),
        pytest.param({"conclusion": "failure"}, id="failed"),
        pytest.param({"conclusion": "success"}, id="passed"),
        pytest.param({"conclusion": "skipped"}, id="skipped"),
        pytest.param({"conclusion": None}, id="completed with no conclusion"),
        pytest.param({"steps": 1}, id="one step"),
        pytest.param({"runner": "GitHub Actions 9"}, id="a runner name"),
        pytest.param({"runner": ...}, id="no runner name listed"),
        pytest.param({"runner_id": 9}, id="a runner id"),
        pytest.param({"runner_id": ...}, id="no runner id listed"),
    ],
)
def test_a_job_the_listing_shows_any_other_way_stays_missing_on_attempt_1(
    tmp_path, capsys, field
) -> None:
    # Each differs from a job no runner took in one field only: a job a runner took, or one the
    # listing describes any other way, may have written what a rerun would hide.
    left = {**_NEVER_ACQUIRED, **field}
    args = [*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *_first_jobs(tmp_path, left)]
    assert _run(args) == 1
    assert f"`{_LEFT}`: missing on attempt 1" in capsys.readouterr().out


def test_another_jobs_no_runner_does_not_stand_for_the_keys_own(tmp_path, capsys) -> None:
    # Jobs match planned keys by their exact name: a never-acquired "suite" is no "suite (2/2)".
    jobs = [_api_job(k) for k in KEYS if k != _LEFT]
    jobs += [_api_job(_LEFT, conclusion="cancelled", steps=2), _api_job("suite", **_NEVER_ACQUIRED)]
    args = [*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *_listing(tmp_path, jobs)]
    assert _run(args) == 1
    assert f"`{_LEFT}`: missing on attempt 1" in capsys.readouterr().out


def test_a_listing_that_names_no_job_of_the_key_fills_nothing(tmp_path, capsys) -> None:
    jobs = [_api_job(k) for k in KEYS if k != _LEFT]
    args = [*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *_listing(tmp_path, jobs)]
    assert _run(args) == 1
    assert f"`{_LEFT}`: missing on attempt 1" in capsys.readouterr().out


def test_two_unexpanded_stages_in_attempt_1s_jobs_still_read(tmp_path, capsys) -> None:
    # GitHub lists a stage cancelled before it expanded under `matrix.key`: two of them name no
    # planned key, so the listing reads.
    jobs = [_api_job(k) for k in KEYS if k != _LEFT] + [_api_job(_LEFT, **_NEVER_ACQUIRED)]
    jobs += [_api_job("matrix.key", conclusion="cancelled", steps=0, runner="", runner_id=None)] * 2
    args = [*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *_listing(tmp_path, jobs)]
    assert _run(args) == 0, capsys.readouterr().out


def test_a_planned_job_named_twice_in_attempt_1s_jobs_is_refused(tmp_path, capsys) -> None:
    jobs = [_api_job(k) for k in KEYS] + [_api_job(_LEFT, **_NEVER_ACQUIRED)]
    args = [*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *_listing(tmp_path, jobs)]
    assert _run(args) == 2
    err = capsys.readouterr().err
    assert f"attempt 1's jobs (--first-jobs) name the planned job '{_LEFT}' twice" in err


def test_a_push_to_mains_rerun_fills_a_job_no_runner_took(
    tmp_path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    proof = tmp_path / "superseded.json"
    proof.write_text("{}")
    args = [*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", "--superseded", str(proof)]
    assert _run([*args, *_first_jobs(tmp_path, _NEVER_ACQUIRED)]) == 0, capsys.readouterr().out


def test_a_push_to_mains_rerun_is_filled_by_attempt_2_alone(
    tmp_path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    proof = tmp_path / "superseded.json"
    proof.write_text("{}")
    records = _named(_record("static"), _record("suite (1/2)"), _record(_LEFT, attempt=3))
    args = [*_setup(tmp_path, records), "--attempt", "3", "--superseded", str(proof)]
    assert _run([*args, *_first_jobs(tmp_path, _NEVER_ACQUIRED)]) == 1
    assert f"`{_LEFT}`: missing on attempt 2" in capsys.readouterr().out


@pytest.mark.parametrize("runner_id", [None, 0], ids=["no runner id", "runner id 0"])
def test_a_push_to_mains_attempt_1_reads_a_proven_replaced_shard_superseded(
    tmp_path, capsys, monkeypatch: pytest.MonkeyPatch, runner_id
) -> None:
    """A shard its concurrency group replaced before any runner took it is one no runner took:
    given attempt 1's jobs on attempt 1 it would read not acquired, and the proof names it."""
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    left = _first_jobs(tmp_path, {**_NEVER_ACQUIRED, "runner_id": runner_id})
    assert _run([*_replaced(tmp_path), "--attempt", "1", *left]) == 1
    out = capsys.readouterr().out
    assert f"`{_LEFT}`: superseded (by {NEWER})" in out, out
    assert f"`test`: superseded (by {NEWER})" in out, out
    assert "not acquired" not in out and verdict.NOT_ACQUIRED_NOTICE not in out, out


def test_a_push_to_mains_attempt_1_with_no_proof_reads_not_acquired(
    tmp_path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    args = _replaced(tmp_path)
    Path(args[-1]).write_text("{}")
    left = _first_jobs(tmp_path, _NEVER_ACQUIRED)
    assert _run([*args, "--attempt", "1", *left]) == 1
    out = capsys.readouterr().out
    assert f"`{_LEFT}`: not acquired" in out, out
    assert "superseded" not in out and verdict.NOT_ACQUIRED_NOTICE not in out, out


def _main_push_on_attempt_1(proven: dict[str, str]) -> verdict.Verdict:
    results = [
        *verdict.from_job_records([_record("static"), _record("suite (1/2)")]),
        *verdict.from_needs(
            json.dumps(dict(NEEDS, test={"result": "cancelled", "outputs": {}})),
            verdict.Identity(HEAD, {"apps": PARTNER}),
        ),
    ]
    first = [_run_of("static"), _run_of("suite (1/2)"), _run_of(_LEFT)]
    first[:2] = [
        replace(j, conclusion="success", steps=3, runner="r", runner_id=7) for j in first[:2]
    ]
    return verdict.decide_main_push(
        KEYS,
        results,
        verdict.Identity(HEAD, {"apps": PARTNER}),
        proven,
        event="push",
        ref="refs/heads/main",
        run_id=RUN,
        first_jobs=first,
        attempt=1,
    )


def test_decide_main_push_reads_a_proven_shard_no_runner_took_superseded() -> None:
    found = _main_push_on_attempt_1({_LEFT: NEWER})
    assert found.superseded_by == NEWER, found
    assert [r.kind for r in found.reasons] == [verdict.SUPERSEDED, verdict.SUPERSEDED], found
    assert found.not_acquired == ()


def test_decide_main_push_on_attempt_1_reads_an_unproven_shard_not_acquired() -> None:
    found = _main_push_on_attempt_1({})
    assert (_LEFT, verdict.NOT_ACQUIRED) in [(r.key, r.kind) for r in found.reasons], found
    assert found.superseded_by == "" and found.not_acquired == ()


# On attempt 1 itself, attempt 1's own listing names a key no runner took: still red, and a red
# for those keys alone (a stage `needs` calls abandoned aside) says so in a notice of its own.

_ABANDONED_NEEDS = dict(NEEDS, test={"result": "abandoned", "outputs": {}})


@pytest.mark.parametrize("needs", [NEEDS, _ABANDONED_NEEDS], ids=["stage green", "abandoned"])
def test_attempt_1_names_a_job_no_runner_took_in_its_notice(tmp_path, capsys, needs) -> None:
    records = _named(_record("static"), _record("suite (1/2)"))
    args = [*_setup(tmp_path, records, needs=needs), *_first_jobs(tmp_path, _NEVER_ACQUIRED)]
    assert _run(args) == 1
    out = capsys.readouterr().out
    assert f"`{_LEFT}`: not acquired (no runner took its job" in out, out
    assert f"::notice title={verdict.NOT_ACQUIRED_NOTICE}::{_LEFT}\n" in out, out


@pytest.mark.parametrize(
    ("records", "needs", "left"),
    [
        pytest.param(
            (_record("static", result="failure"), _record("suite (1/2)")),
            NEEDS,
            _NEVER_ACQUIRED,
            id="another job failed",
        ),
        pytest.param(
            (_record("static"), _record("suite (1/2)")),
            dict(NEEDS, test={"result": "failure", "outputs": {}}),
            _NEVER_ACQUIRED,
            id="a stage failed",
        ),
        pytest.param(
            (_record("static"), _record("suite (1/2)")),
            NEEDS,
            {**_NEVER_ACQUIRED, "steps": 2},
            id="a runner took it",
        ),
    ],
)
def test_attempt_1_red_for_anything_more_writes_no_notice(
    tmp_path, capsys, records, needs, left
) -> None:
    args = [*_setup(tmp_path, _named(*records), needs=needs), *_first_jobs(tmp_path, left)]
    assert _run(args) == 1
    assert verdict.NOT_ACQUIRED_NOTICE not in capsys.readouterr().out


def test_attempt_1_without_its_jobs_reads_missing_and_writes_no_notice(tmp_path, capsys) -> None:
    records = _named(_record("static"), _record("suite (1/2)"))
    assert _run(_setup(tmp_path, records)) == 1
    out = capsys.readouterr().out
    assert f"`{_LEFT}`: missing (no result for a planned job)" in out
    assert verdict.NOT_ACQUIRED_NOTICE not in out


def test_a_rerun_that_still_lacks_the_job_writes_no_notice(tmp_path, capsys) -> None:
    records = _named(_record("static"), _record("suite (1/2)"))
    args = [*_setup(tmp_path, records), "--attempt", "2", *_first_jobs(tmp_path, _NEVER_ACQUIRED)]
    assert _run(args) == 1
    assert verdict.NOT_ACQUIRED_NOTICE not in capsys.readouterr().out


def test_never_acquired_never_reads_a_field_the_listing_did_not_give_as_none() -> None:
    base = verdict.JobRun("k", "completed", "cancelled", (), 0, "", None, True)
    assert verdict.never_acquired(base)
    for missing in (
        {"steps": None},
        {"runner": None},
        {"runner_id_listed": False},
    ):
        job = replace(base, **missing)  # type: ignore[arg-type]
        assert not verdict.never_acquired(job), missing


@pytest.mark.parametrize(
    "field",
    [{"steps": False}, {"steps": 0.0}, {"runner_id": False}, {"runner_id": 0.0}],
    ids=["steps False", "steps 0.0", "runner id False", "runner id 0.0"],
)
def test_never_acquired_reads_no_zero_that_is_not_a_whole_number(field) -> None:
    base = verdict.JobRun("k", "completed", "cancelled", (), 0, "", 0, True)
    assert verdict.never_acquired(base)
    assert not verdict.never_acquired(replace(base, **field))


# The notice reads attempt 1's listing too: every job but results completed success or skipped,
# or no runner took it and it is a key read not acquired. And it sets aside only a stage job the
# listing names no job of.

_TAKEN = {"conclusion": "success", "steps": 3, "runner": "GitHub Actions 7", "runner_id": 7}


def _attempt_1_listing(tmp_path: Path, *more: dict[str, object]) -> list[str]:
    """Attempt 1's jobs: each key green but the one left, which no runner took, and *more*."""
    jobs = [_api_job(k) for k in KEYS if k != _LEFT]
    jobs.append(_api_job(_LEFT, **_NEVER_ACQUIRED))  # type: ignore[arg-type]
    return _listing(tmp_path, [*jobs, *more])


def test_a_leg_that_failed_after_its_green_record_writes_no_notice(tmp_path, capsys) -> None:
    """Its record says success, but the listing shows the job failed after it (a step past the
    upload): the stage's word alone may say abandoned, and the listing says otherwise."""
    records = _named(_record("static"), _record("suite (1/2)"))
    jobs = [_api_job("static"), _api_job("suite (1/2)", conclusion="failure")]
    jobs.append(_api_job(_LEFT, **_NEVER_ACQUIRED))  # type: ignore[arg-type]
    args = [*_setup(tmp_path, records, needs=_ABANDONED_NEEDS), *_listing(tmp_path, jobs)]
    assert _run(args) == 1
    out = capsys.readouterr().out
    assert f"`{_LEFT}`: not acquired" in out, out
    assert verdict.NOT_ACQUIRED_NOTICE not in out, out


@pytest.mark.parametrize(
    "job",
    [
        pytest.param(_api_job("shard-plan"), id="a job a runner took"),
        pytest.param(_api_job("shard-plan", conclusion=None, status="in_progress"), id="running"),
    ],
)
def test_a_needs_job_the_listing_names_is_never_set_aside_as_abandoned(
    tmp_path, capsys, job
) -> None:
    records = _named(_record("static"), _record("suite (1/2)"))
    needs = dict(_ABANDONED_NEEDS, **{"shard-plan": {"result": "abandoned", "outputs": {}}})
    args = [*_setup(tmp_path, records, needs=needs), *_attempt_1_listing(tmp_path, job)]
    assert _run(args) == 1
    assert verdict.NOT_ACQUIRED_NOTICE not in capsys.readouterr().out


@pytest.mark.parametrize(
    "job",
    [
        pytest.param(_api_job("changes", conclusion=None, status="in_progress"), id="running"),
        pytest.param(_api_job("changes", conclusion="failure"), id="failed"),
        pytest.param(_api_job("changes", conclusion="neutral"), id="neutral"),
        pytest.param(_api_job("other", **_NEVER_ACQUIRED), id="no runner took a job outside"),
    ],
)
def test_a_listing_with_any_job_but_results_unclean_writes_no_notice(tmp_path, capsys, job) -> None:
    records = _named(_record("static"), _record("suite (1/2)"))
    args = [*_setup(tmp_path, records), *_attempt_1_listing(tmp_path, job)]
    assert _run(args) == 1
    out = capsys.readouterr().out
    assert f"`{_LEFT}`: not acquired" in out, out
    assert verdict.NOT_ACQUIRED_NOTICE not in out, out


def test_the_red_runs_listing_shape_still_writes_the_notice(tmp_path, capsys) -> None:
    """As the three red runs listed attempt 1: the jobs before the stages by their ids, each
    stage's entries, a stage the plan left empty under its unexpanded name, results running, and
    one entry no runner took; ``needs`` calls that entry's stage abandoned."""
    records = _named(_record("static"), _record("suite (1/2)"))
    needs = {
        "tree-check": {"result": "success", "outputs": {}},
        "changes": {"result": "success", "outputs": {}},
        "shard-plan": {"result": "success", "outputs": {}},
        "build": {"result": "success", "outputs": {}},
        "test": {"result": "abandoned", "outputs": {}},
        "test-2": {"result": "skipped", "outputs": {}},
    }
    more = [
        _api_job("tree-check"),
        _api_job("changes"),
        _api_job("shard-plan"),
        _api_job("${{ matrix.key }}", conclusion="skipped", steps=0, runner="", runner_id=0),
        _api_job("results", conclusion=None, status="in_progress"),
    ]
    args = [*_setup(tmp_path, records, needs=needs), *_attempt_1_listing(tmp_path, *more)]
    assert _run(args) == 1
    out = capsys.readouterr().out
    assert f"::notice title={verdict.NOT_ACQUIRED_NOTICE}::{_LEFT}\n" in out, out


def test_the_notice_escapes_what_a_workflow_command_reads() -> None:
    red = verdict.Verdict(green=False, not_acquired=("a%b", "c\nd"))
    assert verdict.not_acquired_notices(red) == (
        f"::notice title={verdict.NOT_ACQUIRED_NOTICE}::a%25b, c\\nd\n"
    )


def test_a_red_verdict_step_writes_at_most_ten_notices_the_jobs_no_runner_took_first() -> None:
    tests = tuple(
        verdict.FailedTest(_LEFT, 1, f"tests/test_t.py::test_{n}", verdict.QUARANTINED)
        for n in range(12)
    )
    red = verdict.Verdict(
        green=False,
        reasons=(verdict.Reason(_LEFT, verdict.NOT_ACQUIRED, "no runner took its job"),),
        tests=tests,
        not_acquired=(_LEFT,),
    )
    lines = verdict.notices(red).splitlines()
    assert len(lines) == 10 and all(line.startswith("::notice ") for line in lines), lines
    assert lines[0] == f"::notice title={verdict.NOT_ACQUIRED_NOTICE}::{_LEFT}"
    assert all(f"title={verdict.FAILED_NOTICE}::" in line for line in lines[1:])


# ── attempt 1's jobs, read ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["not json", "{}", "[]", json.dumps([{"total_count": 1, "jobs": []}])],
    ids=["not JSON", "no pages", "an empty list", "a job unseen"],
)
def test_unreadable_attempt_1s_jobs_judge_nothing(tmp_path, capsys, text) -> None:
    # Fails closed: a listing the verdict cannot read is unreadable input, never no listing.
    listing = tmp_path / "first-jobs.json"
    listing.write_text(text)
    args = [*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", "--first-jobs", str(listing)]
    assert _run(args) == 2
    assert "verdict: unreadable input: attempt 1's jobs (--first-jobs)" in capsys.readouterr().err


def test_attempt_1s_jobs_are_refused_without_records(tmp_path, capsys) -> None:
    args = _without(_without(_setup(tmp_path, _all_green()), "--records"), "--run-id")
    assert _run([*args, "--attempt", "2", *_first_jobs(tmp_path, _NEVER_ACQUIRED)]) == 2
    assert "--first-jobs is read only with --records" in capsys.readouterr().err


def test_a_later_attempts_jobs_given_as_attempt_1s_are_refused(tmp_path, capsys) -> None:
    left = _first_jobs(tmp_path, _NEVER_ACQUIRED, attempt=2)
    assert _run([*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *left]) == 2
    assert "not run 4242 attempt 1" in capsys.readouterr().err


@pytest.mark.parametrize(
    "runner",
    [7, ["a"], True, False, 0, [], {}],
    ids=["7", "a list", "True", "False", "0", "[]", "{}"],
)
def test_a_runner_name_that_is_not_a_string_is_refused(tmp_path, capsys, runner) -> None:
    left = _first_jobs(tmp_path, {**_NEVER_ACQUIRED, "runner": runner})
    assert _run([*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *left]) == 2
    assert "has a runner name that is not a string" in capsys.readouterr().err


@pytest.mark.parametrize("runner_id", ["7", 1.5, True, False, [], {}])
def test_a_runner_id_that_is_not_a_whole_number_is_refused(tmp_path, capsys, runner_id) -> None:
    left = _first_jobs(tmp_path, {**_NEVER_ACQUIRED, "runner_id": runner_id})
    assert _run([*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *left]) == 2
    assert "has a runner id that is not a whole number" in capsys.readouterr().err


def _this_attempts_jobs(**field: object) -> str:
    """The run's jobs on attempt 1, as the flake rule's ``--jobs`` gives them (no plan)."""
    return json.dumps([{"total_count": 1, "jobs": [{**_api_job("static"), **field}]}])


@pytest.mark.parametrize("runner", [7, False], ids=["7", "False"])
def test_the_flake_rules_listing_refuses_a_runner_name_that_is_not_a_string(runner) -> None:
    with pytest.raises(verdict.Refused, match="has a runner name that is not a string"):
        verdict.read_jobs(_this_attempts_jobs(runner_name=runner), run_id=RUN, attempt=1)


@pytest.mark.parametrize("runner_id", ["7", 1.5], ids=["'7'", "1.5"])
def test_the_flake_rules_listing_refuses_a_runner_id_that_is_not_a_whole_number(
    runner_id,
) -> None:
    with pytest.raises(verdict.Refused, match="has a runner id that is not a whole number"):
        verdict.read_jobs(_this_attempts_jobs(runner_id=runner_id), run_id=RUN, attempt=1)


@pytest.mark.parametrize(
    "field",
    [{"run_attempt": True}, {"run_attempt": 1.0}, {"run_id": 4242.0}],
    ids=["attempt true", "attempt 1.0", "run 4242.0"],
)
def test_a_listing_whose_run_or_attempt_is_no_whole_number_is_refused(
    tmp_path, capsys, field
) -> None:
    jobs = [_api_job(k) for k in KEYS if k != _LEFT]
    jobs.append({**_api_job(_LEFT, **_NEVER_ACQUIRED), **field})  # type: ignore[arg-type]
    args = [*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *_listing(tmp_path, jobs)]
    assert _run(args) == 2
    assert "not run 4242 attempt 1" in capsys.readouterr().err


def test_attempt_1s_jobs_given_twice_are_refused(tmp_path, capsys) -> None:
    left = _first_jobs(tmp_path, _NEVER_ACQUIRED)
    assert _run([*_setup(tmp_path, _named(*_RERUN)), "--attempt", "2", *left, *left]) == 2
    assert "--first-jobs: given twice" in capsys.readouterr().err


# decide() and decide_main_push() hold attempt 1's jobs to the run and attempt 1 themselves, so a
# caller of the library (the merge controller) cannot fill a key from another attempt's listing.


def _decide_on(first: list[verdict.JobRun], **kw: object) -> verdict.Verdict:
    results = verdict.from_job_records(list(_named(*_RERUN).values()))
    candidate = verdict.Identity(HEAD, {"apps": PARTNER})
    return verdict.decide(
        KEYS, results, candidate, first_jobs=first, **kw  # type: ignore[arg-type]
    )


def _run_of(name: str, *, run_id: int | None = RUN, run_attempt: int | None = 1) -> verdict.JobRun:
    return verdict.JobRun(
        name,
        "completed",
        "cancelled",
        (),
        0,
        "",
        None,
        True,
        run_id=run_id,
        run_attempt=run_attempt,
    )


@pytest.mark.parametrize(
    ("first", "kw", "says"),
    [
        pytest.param(
            lambda: [_run_of(_LEFT, run_attempt=2)], {"run_id": RUN}, "attempt 2", id="attempt 2"
        ),
        pytest.param(
            lambda: [_run_of(_LEFT, run_id=7)], {"run_id": RUN}, "run 7", id="another run"
        ),
        pytest.param(
            lambda: [_run_of(_LEFT, run_attempt=None)], {"run_id": RUN}, "None", id="no attempt"
        ),
        pytest.param(
            lambda: [_run_of(_LEFT), _run_of(_LEFT)], {"run_id": RUN}, "twice", id="twice"
        ),
        pytest.param(lambda: [_run_of(_LEFT)], {}, "run id", id="no run id"),
    ],
)
def test_decide_refuses_attempt_1s_jobs_of_another_run_or_attempt(first, kw, says) -> None:
    with pytest.raises(verdict.Refused, match=says):
        _decide_on(first(), **kw)


def test_decide_fills_from_attempt_1s_own_jobs() -> None:
    assert _decide_on([_run_of(_LEFT)], run_id=RUN, attempt=2).green


@pytest.mark.parametrize("attempt", [0, True, "2", 1.0], ids=["0", "True", "'2'", "1.0"])
def test_decide_refuses_an_attempt_that_is_not_a_whole_number_from_1(attempt) -> None:
    # True == 1: without the refusal, it would read as attempt 1.
    with pytest.raises(verdict.Refused, match="attempt must be a whole number from 1"):
        _decide_on([_run_of(_LEFT)], run_id=RUN, attempt=attempt)


@pytest.mark.parametrize(
    "first",
    [
        pytest.param(lambda: [_run_of(_LEFT, run_attempt=True)], id="attempt True"),
        pytest.param(lambda: [_run_of(_LEFT, run_attempt=1.0)], id="attempt 1.0"),
        pytest.param(lambda: [_run_of(_LEFT, run_id=4242.0)], id="run 4242.0"),
    ],
)
def test_decide_refuses_attempt_1s_jobs_whose_run_or_attempt_is_no_whole_number(first) -> None:
    with pytest.raises(verdict.Refused, match="not run 4242 attempt 1"):
        _decide_on(first(), run_id=RUN, attempt=2)


def test_decide_refuses_a_result_of_a_later_attempt_than_the_runs() -> None:
    with pytest.raises(verdict.Refused, match="of attempt 2, after the run's attempt 1"):
        _decide_on([_run_of(_LEFT)], run_id=RUN, attempt=1)


def test_a_planned_key_abandoned_beside_one_no_runner_took_writes_no_notice() -> None:
    """Only a stage job outside the plan is set aside: a planned key's own odd word is its own
    red, here a key the listing names no job of."""
    results = [
        verdict.JobResult("static", "abandoned", verdict.Identity(HEAD, {"apps": PARTNER}), 1, RUN),
        *verdict.from_job_records([_record("suite (1/2)")]),
    ]
    first = [
        replace(_run_of("suite (1/2)"), conclusion="success", steps=3, runner="r", runner_id=7),
        _run_of(_LEFT),
    ]
    found = verdict.decide(
        KEYS,
        results,
        verdict.Identity(HEAD, {"apps": PARTNER}),
        run_id=RUN,
        first_jobs=first,
        attempt=1,
    )
    assert not found.green
    assert (_LEFT, verdict.NOT_ACQUIRED) in [(r.key, r.kind) for r in found.reasons], found
    assert found.not_acquired == (), found


def test_a_record_claiming_a_later_attempt_than_the_run_is_refused(tmp_path, capsys) -> None:
    records = _all_green()
    records.update(_named(_record("static", attempt=2)))
    assert _run(_setup(tmp_path, records)) == 2
    assert "attempt 2" in capsys.readouterr().err


def test_needs_is_read_as_the_runs_own_attempt(tmp_path, capsys) -> None:
    # A red in needs on attempt 2 is attempt 2's red: the summary names the run's attempt,
    # never attempt 1, so a reader can tell which attempt a red belongs to.
    needs = dict(NEEDS, test={"result": "failure", "outputs": {}})
    assert _run([*_setup(tmp_path, _all_green(), needs=needs), "--attempt", "2"]) == 1
    assert "`test`: unplanned failure (attempt 2:" in capsys.readouterr().out


@pytest.mark.parametrize(
    # int() reads a digit of another script as a number ("1\u0661" is 11): only ASCII is read.
    "attempt",
    ["0", "-1", "one", "1.5", "", "1000000", "+1", " 1", "\u0661", "\uff12", "1\u0661"],
)
def test_an_attempt_that_is_not_a_whole_number_from_1_is_refused(tmp_path, capsys, attempt) -> None:
    args = _setup(tmp_path, _all_green())
    assert _run([*args, "--attempt", attempt]) == 2
    assert "--attempt must be a whole number from 1 to 999999" in capsys.readouterr().err


def test_the_largest_attempt_is_read(tmp_path) -> None:
    assert _run([*_setup(tmp_path, _all_green()), "--attempt", "999999"]) == 0


# ── needs and the plan ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["web-build", "static"])
def test_a_needs_job_named_as_a_planned_key_is_refused(tmp_path, capsys, key) -> None:
    # A planned key is met only by its record: a needs job of the same name would stand in for
    # a record that was never written.
    needs = dict(NEEDS, **{key: {"result": "success", "outputs": {}}})
    records = _named(_record("suite (1/2)"), _record("suite (2/2)"))
    keys = [*KEYS, "web-build"]
    assert _run(_setup(tmp_path, records, keys=keys, needs=needs)) == 2
    assert f"needs names {key}, which is a planned key" in capsys.readouterr().err


# ── what the folder may hold ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "text", "why"),
    [
        ("not-json", "{", "is not JSON"),
        ("hand-written", json.dumps({"key": "static", "result": "success"}), "must hold exactly"),
    ],
)
def test_a_record_it_cannot_read_is_refused(tmp_path, capsys, name, text, why) -> None:
    records = _all_green()
    records[_folder("static")] = text
    assert _run(_setup(tmp_path, records)) == 2
    assert why in capsys.readouterr().err


def test_a_file_larger_than_any_record_is_refused_unread(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    path = tmp_path / "records" / _folder("static") / "record.json"
    # Valid JSON padded past 1 MB, so only the size check can refuse it.
    path.write_text(" " * 1_000_001 + _record("static"))
    assert _run(args) == 2
    assert "larger than any job record" in capsys.readouterr().err


def test_a_record_of_1_mb_is_read(tmp_path) -> None:
    args = _setup(tmp_path, _all_green())
    path = tmp_path / "records" / _folder("static") / "record.json"
    text = _record("static")
    path.write_text(" " * (1_000_000 - len(text.encode())) + text)
    assert _run(args) == 0


def test_a_records_folder_that_does_not_exist_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    args[args.index("--records") + 1] = str(tmp_path / "absent")
    assert _run(args) == 2
    assert "cannot read the records folder" in capsys.readouterr().err


def test_an_empty_records_path_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    args[args.index("--records") + 1] = ""
    assert _run(args) == 2
    assert "cannot read the records folder" in capsys.readouterr().err


def test_a_records_folder_that_is_a_link_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "records", target_is_directory=True)
    args[args.index("--records") + 1] = str(link)
    assert _run(args) == 2
    assert "not a folder" in capsys.readouterr().err


def test_an_empty_records_folder_reads_every_planned_key_missing(tmp_path, capsys) -> None:
    assert _run(_setup(tmp_path, {})) == 1
    out = capsys.readouterr().out
    assert all(f"`{k}`: missing" in out for k in KEYS)


def test_records_given_twice_are_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    assert _run([*args, "--records", str(tmp_path)]) == 2
    assert "--records: given twice" in capsys.readouterr().err


def test_a_record_file_that_is_a_link_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    outside = tmp_path / "outside.json"
    outside.write_text(_record("static"))
    path = tmp_path / "records" / _folder("static") / "record.json"
    path.unlink()
    path.symlink_to(outside)
    assert _run(args) == 2
    assert "holds a link" in capsys.readouterr().err


def test_an_artifact_folder_that_is_a_link_is_refused(tmp_path, capsys) -> None:
    # The link holds a red for a planned key beside the green records: read or skipped, it
    # would change the verdict, so it is refused.
    records = _all_green()
    del records[_folder("suite (1/2)")]
    args = _setup(tmp_path, records)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "record.json").write_text(_record("suite (1/2)", result="failure"))
    (tmp_path / "records" / _folder("suite (1/2)")).symlink_to(outside, target_is_directory=True)
    assert _run(args) == 2
    assert "holds a link" in capsys.readouterr().err


def test_a_folder_inside_an_artifact_folder_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    inner = tmp_path / "records" / _folder("static") / "inner"
    inner.mkdir()
    (inner / "record.json").write_text(_record("suite (1/2)", result="failure"))
    assert _run(args) == 2
    assert "holds 2 entries, not one record" in capsys.readouterr().err


def test_an_artifact_folder_holding_only_a_folder_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    folder = tmp_path / "records" / _folder("static")
    (folder / "record.json").unlink()
    (folder / "inner").mkdir()
    (folder / "inner" / "record.json").write_text(_record("static", result="failure"))
    assert _run(args) == 2
    assert "is not a record file" in capsys.readouterr().err


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads any folder")
def test_an_artifact_folder_it_cannot_read_is_refused(tmp_path, capsys) -> None:
    records = _all_green()
    records[_folder("suite (2/2)")] = _record("suite (2/2)", result="failure")
    args = _setup(tmp_path, records)
    folder = tmp_path / "records" / _folder("suite (2/2)")
    folder.chmod(0)
    try:
        assert _run(args) == 2
    finally:
        folder.chmod(0o700)
    assert "cannot read" in capsys.readouterr().err


def test_a_file_beside_the_artifact_folders_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    (tmp_path / "records" / "record.json").write_text(_record("static", result="failure"))
    assert _run(args) == 2
    assert "is not an artifact's folder" in capsys.readouterr().err


def test_an_empty_artifact_folder_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    (tmp_path / "records" / _folder("web-build")).mkdir()
    assert _run(args) == 2
    assert "holds 0 entries, not one record" in capsys.readouterr().err


def test_a_record_file_not_named_json_is_refused(tmp_path, capsys) -> None:
    args = _setup(tmp_path, _all_green())
    folder = tmp_path / "records" / _folder("static")
    (folder / "record.json").rename(folder / "record.txt")
    assert _run(args) == 2
    assert "is not a record file" in capsys.readouterr().err


# ── the artifact name ────────────────────────────────────────────────────────────────────────


def test_the_record_artifact_name_carries_the_key_run_and_attempt() -> None:
    assert verdict.record_artifact("suite (2/3)", run_id=77, attempt=2) == (
        "ci-record-suite-2-3-77-2"
    )
    assert verdict.record_artifact("static", run_id=1, attempt=1) == "ci-record-static-1-1"


@pytest.mark.parametrize(
    ("key", "run_id", "attempt"),
    [
        ("static", 0, 1),
        ("static", 1, 0),
        ("static", True, 1),
        ("static", 1, True),
        ("static", None, 1),
        ("static", 1, 1.0),
        ("", 1, 1),
        ("()", 1, 1),
    ],
)
def test_a_record_artifact_of_no_job_run_or_attempt_is_refused(key, run_id, attempt) -> None:
    with pytest.raises(verdict.Refused):
        verdict.record_artifact(key, run_id=run_id, attempt=attempt)
