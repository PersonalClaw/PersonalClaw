"""``python -m tools.ci verdict --records DIR --run-id N``: the per-job records a run's test stage
uploaded, judged with GitHub's ``toJSON(needs)`` against the plan's matrix keys. A planned key
with no record is missing, so a matrix entry that never started reads red; a record of another
head is stale; a record of another run, or of none, is not this run's; a record claiming a later
attempt than the run's is refused, and on a rerun a key attempt 1 left no record for stays red.
Each record must sit alone in the folder ``record_artifact`` names for it in this run."""

from __future__ import annotations

import json
import os
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
) -> str:
    steps = [(["true"], 0 if result == "success" else 1, 0.5)]
    return verdict.job_record(
        key=key,
        result=result,
        identity=verdict.Identity(head, {"apps": PARTNER}),
        steps=steps,
        attempt=attempt,
        run_id=run_id,
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
    return [
        "--plan", str(plan), "--needs", str(needs_file), "--records", str(folder),
        "--run-id", str(RUN),
        "--head", HEAD, "--partner", f"apps={PARTNER}",
        "--run-head", HEAD, "--run-partner", f"apps={PARTNER}",
    ]  # fmt: skip


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


def test_a_cancelled_matrix_job_in_needs_reads_red_even_with_every_record(tmp_path) -> None:
    needs = dict(NEEDS, test={"result": "cancelled", "outputs": {}})
    assert _run(_setup(tmp_path, _all_green(), needs=needs)) == 1


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
