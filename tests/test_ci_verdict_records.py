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
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _all_green()
    records.update(_named(_record("suite (1/2)", quarantined=[FLAKY])))
    assert _run(_setup(tmp_path, records)) == 0
    out = capsys.readouterr().out
    assert out.startswith("### Verdict: green\n")
    assert f"- `suite (1/2)` attempt 1: `{FLAKY}`: quarantined" in out


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
    assert out.count("tests/test_x.py::test_") == 3000
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
