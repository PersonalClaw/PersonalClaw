"""LV-7 — the skill-impact benchmark: register, preflight, reports and V4 reproduction.

Isolation: every test that touches state sets ``PERSONALCLAW_HOME`` to a ``tmp_path``.
``PERSONALCLAW_HOME`` is the safe lever because it is read per call and cached nowhere, and
``test_home_is_isolated`` asserts the redirect rather than trusting it — a suite that silently
ran against ``~/.personalclaw`` would write benchmark reports into the operator's real home.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from personalclaw.evals import child, learning_bench, provenance, scenarios

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "learning_benchmark.py"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home. Returns the path so a test can assert what landed where."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    return h


def test_home_is_isolated(home):
    """The negative rail: prove the redirect, do not assume it.

    ``patch(config_dir)`` misses import-bound stores, so this asserts the env lever actually
    moved the directory every write below resolves against."""
    from personalclaw.config import config_dir

    assert Path(config_dir()) == home
    assert Path(config_dir()) != Path.home() / ".personalclaw"


# ── the frozen register (§2.2 / §2.3) ────────────────────────────────────────


def test_register_is_exactly_ten_unique_tasks_over_unique_skills():
    assert len(learning_bench.BENCH_TASKS) == 10
    assert len({t.task_id for t in learning_bench.BENCH_TASKS}) == 10
    # One task per skill FAMILY (§2.2): two tasks over one skill would double-weight it.
    assert len({t.skill for t in learning_bench.BENCH_TASKS}) == 10


def test_every_register_skill_ships_as_a_bundled_skill():
    """A register row naming a skill that does not ship would run two identical arms."""
    bundled = REPO_ROOT / "src" / "personalclaw" / "skills" / "bundled"
    names = {p.name for p in bundled.iterdir() if p.is_dir()}
    missing = sorted(t.skill for t in learning_bench.BENCH_TASKS if t.skill not in names)
    assert missing == [], f"register names skills that do not ship: {missing}"


def test_every_register_task_ships_as_a_scenario_with_deterministic_assertions():
    """§2.1 and §6: only the four deterministic assertion types; `judge` is excluded because a
    scorer swap moves results further than most architecture deltas."""
    library = REPO_ROOT / "src" / "personalclaw" / "evals" / "library"
    for task in learning_bench.BENCH_TASKS:
        path = library / f"{task.task_id}.json"
        assert path.is_file(), f"{task.task_id} has no shipped scenario"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == task.task_id
        assert data["version"] == learning_bench.TASK_SET_VERSION
        assert data["dimensions"] == ["skill_impact"]
        assert data["fixture_home"], "a benchmark task must declare a fixture home"
        kinds = {
            a["type"] for s in data["sessions"] for t in s["turns"] for a in t.get("assertions", [])
        }
        assert kinds, f"{task.task_id} asserts nothing"
        assert "judge" not in kinds, f"{task.task_id} uses a judged assertion (§6 excludes it)"
        assert kinds <= {"contains", "not_contains", "regex", "equals"}


def test_task_set_fingerprint_names_every_task_even_when_absent(home, monkeypatch):
    """§2.3's anchor. An absent task is an EMPTY sha, never a missing key — a shorter dict
    would make a missing task look like one the set never included."""
    scenarios.install_library()
    fp = learning_bench.task_set_fingerprint()
    assert set(fp) == set(learning_bench.TASK_IDS)
    assert all(len(v) == 64 for v in fp.values())

    monkeypatch.setattr(learning_bench, "TASK_IDS", (*learning_bench.TASK_IDS, "sk_not_a_task"))
    fp2 = learning_bench.task_set_fingerprint()
    assert fp2["sk_not_a_task"] == ""


def test_task_for_is_a_closed_register():
    assert learning_bench.task_for("sk_grill").skill == "grill"
    assert learning_bench.task_for("sk_invented") is None


# ── preflight: no model is called ────────────────────────────────────────────


def test_preflight_reports_every_task_runnable_in_a_fresh_home(home):
    """The G1 closure, asserted at the call site: suppression is VERIFIED for all ten skills.

    This is the check LV-6 recorded as blocking — `max_triggered` clamps to 1, a fresh home
    force-syncs the bundled skills, and `suppressed_producers` is accuracy-derived — and it is
    the one that `arm_mask` + `skills/suppression.py` closed."""
    rows = learning_bench.preflight()
    assert len(rows) == 10
    not_runnable = {r.task_id: r.blockers for r in rows if not r.runnable}
    assert not_runnable == {}, f"preflight blockers in a fresh home: {not_runnable}"
    assert all(r.fixture_home for r in rows)
    assert all(r.suppression_verified for r in rows)


def test_preflight_blocks_a_task_whose_suppression_cannot_be_verified(home, monkeypatch):
    """The vacuity assertion for the check above: prove it CAN report not-runnable.

    A preflight that answered "runnable" for a home where suppression does nothing would pass
    every arm as measured while both arms carried the skill."""
    from personalclaw.evals import skills_bench

    class _Unverified:
        probe_chars = 120
        verified = False
        reason = "the suppressed arm's prompt STILL carries the body"

    monkeypatch.setattr(skills_bench, "verify_suppression", lambda *a, **k: _Unverified())
    rows = learning_bench.preflight(loader=object())
    assert all(not r.runnable for r in rows)
    assert all("suppression unverified" in " ".join(r.blockers) for r in rows)


def test_preflight_blocks_a_task_whose_scenario_is_not_installed(home, monkeypatch):
    scenarios.install_library()
    (Path(scenarios.installed_dir()) / "sk_grill.json").unlink()
    # Pin `install_library` to the STALE manifest: the real one would backfill the file we just
    # removed, and the state under test is a home whose library is genuinely incomplete.
    monkeypatch.setattr(scenarios, "install_library", lambda: scenarios.read_manifest() or {})
    monkeypatch.setattr(scenarios, "list_installed", lambda: ["sk_check_work"])
    rows = {r.task_id: r for r in learning_bench.preflight()}
    assert not rows["sk_grill"].scenario_present
    assert any("not installed" in b for b in rows["sk_grill"].blockers)


# ── report storage ───────────────────────────────────────────────────────────


def test_reports_round_trip_and_latest_is_newest(home):
    learning_bench.write_report("learnbench-20260101T000000Z", {"run_id": "a", "tasks": []})
    learning_bench.write_report("learnbench-20260202T000000Z", {"run_id": "b", "tasks": []})
    assert learning_bench.list_runs()[0] == "learnbench-20260202T000000Z"
    assert learning_bench.latest_report()["run_id"] == "b"
    assert learning_bench.read_report("learnbench-20260101T000000Z")["run_id"] == "a"
    assert learning_bench.read_report("nope") is None


def test_latest_report_walks_past_an_unreadable_newest(home):
    """One corrupt directory must not hide every earlier measurement."""
    learning_bench.write_report("learnbench-20260101T000000Z", {"run_id": "a", "tasks": []})
    bad = learning_bench.report_path("learnbench-20260303T000000Z")
    bad.write_text("{not json", encoding="utf-8")
    assert learning_bench.latest_report()["run_id"] == "a"


# ── V4 reproduction (§8) ─────────────────────────────────────────────────────


def _report(run_id: str, *, sha: str = "ab" * 32, verdict: str | None = "inconclusive") -> dict:
    return {
        "run_id": run_id,
        # Derived, not a literal: ES-6's v1 -> v2 bump made the parametrized mutation below
        # (`task_set_version=2`) equal to the fixture, so the "same task_set_version" condition
        # could no longer fail and the case was measuring nothing.
        "task_set_version": learning_bench.TASK_SET_VERSION,
        "task_set_fingerprint": {"sk_grill": sha},
        "pin": {"prompt_pack_sha256": "pp", "config_snapshot_ref": "cfg"},
        "tasks": [{"task_id": "sk_grill", "verdict": verdict, "verdict_class": verdict}],
    }


def test_reproduction_holds_when_every_stated_condition_holds():
    check = learning_bench.reproduction_check(_report("a"), _report("b"))
    assert check.reproduces is True
    assert check.verdict_changes == []
    assert set(check.conditions) == set(learning_bench.REPRODUCTION_CONDITIONS)
    assert all(check.conditions.values())


@pytest.mark.parametrize(
    ("mutate", "failing"),
    [
        (
            lambda r: r.update(task_set_version=learning_bench.TASK_SET_VERSION + 1),
            "same task_set_version",
        ),
        (
            lambda r: r.update(task_set_fingerprint={"sk_grill": "cd" * 32}),
            "same scenario_sha256 set",
        ),
        (lambda r: r["pin"].update(prompt_pack_sha256="other"), "same prompt_pack_sha256"),
        (lambda r: r["pin"].update(config_snapshot_ref="other"), "same config_snapshot_ref"),
        (
            lambda r: r["tasks"][0].update(verdict_class="skills_on_wins"),
            "same verdict class per task",
        ),
    ],
)
def test_each_stated_condition_can_independently_fail(mutate, failing):
    """The VACUITY ASSERTION for `reproduces`. A conjunction whose terms cannot each fail
    independently is a floor computed from the value it is meant to pin."""
    rerun = _report("b")
    mutate(rerun)
    check = learning_bench.reproduction_check(_report("a"), rerun)
    assert check.reproduces is False
    assert check.conditions[failing] is False
    assert sum(1 for ok in check.conditions.values() if not ok) == 1, check.conditions


def test_unmeasured_reproducing_unmeasured_is_NOT_a_reproduction():
    """The trap this predicate exists to refuse: two runs that measured nothing would otherwise
    agree perfectly and certify each other."""
    check = learning_bench.reproduction_check(
        _report("a", verdict=None), _report("b", verdict=None)
    )
    assert check.reproduces is False
    assert check.conditions["same verdict class per task"] is False
    assert check.verdict_changes == [{"task_id": "sk_grill", "baseline": None, "rerun": None}]


def test_reproduction_states_where_its_variance_comes_from():
    """ "Within stated variance" only means something if the variance is STATED. The payload
    carries the conditions AND cites the section that states them, so a tolerance invented by
    this code would be visibly missing its citation."""
    payload = learning_bench.reproduction_check(_report("a"), _report("b")).to_dict()
    assert payload["stated_variance"] == list(learning_bench.REPRODUCTION_CONDITIONS)
    assert learning_bench.PROTOCOL_DOC in payload["stated_variance_source"]
    assert "§8" in payload["stated_variance_source"]


# ── G3 / G4: the two metrics that used to die with the cell ──────────────────


class _Turn:
    def __init__(self, calls):
        self.tool_calls = list(calls)


class _Session:
    def __init__(self, turns):
        self.turns = turns


class _ScenarioResult:
    name = "sk_grill"
    total_assertions = 2
    passed_assertions = 1
    passed = False
    elapsed_secs = 1.5

    def __init__(self, sessions):
        self.sessions = sessions


def test_tool_calls_survive_the_child_payload():
    """G3: `TurnResult.tool_calls` was populated and then dropped by BOTH aggregation
    boundaries, making the protocol's declared `tool_calls` metric unreachable."""
    result = child.result_from_scenario(
        _ScenarioResult([_Session([_Turn(["a", "b"]), _Turn(["c"])]), _Session([_Turn([])])])
    )
    assert result["tool_calls"] == 3
    assert result["score"] == 0.5


def test_tool_call_count_is_zero_not_a_crash_for_a_result_with_no_sessions():
    assert child.tool_call_count(object()) == 0


def test_spend_reads_the_cells_own_audit_rows(home):
    """G4: `model_calls.jsonl` lives in the cell's throwaway home and was thrown away with it.
    `estimated` is carried through because §4 requires any published ratio to say so."""
    (home / "model_calls.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"tokens_in": 10, "tokens_out": 5, "dollars_est": 0.01}),
                "",
                "{not json",
                json.dumps({"tokens_in": 1, "tokens_out": 2, "estimated": True}),
            ]
        ),
        encoding="utf-8",
    )
    spend = child.spend_from_home()
    assert spend["observed"] is True
    assert spend["attempts"] == 2
    assert spend["tokens"] == 18
    assert spend["estimated"] is True
    assert spend["tokens_recorded"] is True
    assert spend["unrecorded_attempts"] == 0


def test_absent_audit_file_reads_as_UNOBSERVED_not_as_zero_spend(home):
    """The vacuity assertion for the test above, and the substantive point: "we could not see
    the spend" and "this arm was free" are different facts, and only one of them makes a token
    ratio meaningless."""
    spend = child.spend_from_home()
    assert spend["observed"] is False
    # `tokens: None` rather than an ABSENT key (#2540). The point of the original assertion was
    # that no ZERO may be published here, and that still holds strictly — what changed is that
    # every consumer now reads one shape: the nullable scalar plus the `*_recorded` flag that says
    # why it is null (`provenance` "the representation rule"). Key absence made each reader invent
    # its own `.get("tokens") or 0`, which is how the zero got published in the first place.
    assert spend["tokens"] is None
    assert spend["tokens_recorded"] is False
    assert "no model_calls.jsonl" in spend["reason"]


def test_a_provider_that_reported_no_usage_is_not_a_cell_that_spent_nothing(home):
    """#2540, at the boundary the defect lived on.

    Two cells, both `observed: true`, both totalling zero tokens, and only one of them a
    measurement:

    * A — two attempts that COMPLETED and reported no usage. Ollama's OpenAI-compatible endpoint
      intermittently omits the `usage` block, `llm/openai.py` starts its counters at 0 and
      `guardrails/model_call.py` coerces the absent value to 0, so the audit row is written with
      zeros for a call that really spent. MEASURED on `main`: this came back
      `{observed: true, attempts: 2, tokens: 0}`.
    * B — two attempts that never completed. Zero is the truth; there was no usage to report.

    On `main` these two dicts were EQUAL, so the §4 token denominator got a `0` from both.
    """
    rows_a = [
        {"tokens_in": 0, "tokens_out": 0, "dollars_est": 0.0, "passed": True},
        {"tokens_in": 0, "tokens_out": 0, "dollars_est": 0.0, "passed": True},
    ]
    rows_b = [
        {"tokens_in": 0, "tokens_out": 0, "dollars_est": 0.0, "passed": False},
        {"tokens_in": 0, "tokens_out": 0, "dollars_est": 0.0, "passed": False},
    ]
    audit = home / "model_calls.jsonl"

    audit.write_text("\n".join(json.dumps(r) for r in rows_a), encoding="utf-8")
    a = child.spend_from_home()
    audit.write_text("\n".join(json.dumps(r) for r in rows_b), encoding="utf-8")
    b = child.spend_from_home()

    assert a != b, "the whole defect was that these two were the same dict"
    # A: the provider did not report. UNRECORDED, and never a zero.
    assert a["observed"] is True and a["attempts"] == 2
    assert a["tokens_recorded"] is False
    assert a["unrecorded_attempts"] == 2
    assert a["tokens"] is None and a["tokens_in"] is None and a["tokens_out"] is None
    # B: a recorded zero, which IS a measurement.
    assert b["observed"] is True and b["attempts"] == 2
    assert b["tokens_recorded"] is True
    assert b["unrecorded_attempts"] == 0
    assert b["tokens"] == 0
    # `dollars_est` stays a number in BOTH. It is a real estimate over real attempts and only the
    # token count is absent — the asymmetry #2630 refused to fold into `priced`.
    assert a["dollars_est"] == 0.0 and b["dollars_est"] == 0.0


def test_a_partly_reporting_cell_publishes_no_partial_token_sum(home):
    """One attempt reported 300 tokens and one reported none. The sum is not this cell's spend.

    Publishing `300` would be the same bias in a subtler form: a denominator that is quietly wrong
    while the cell reports success. So the count is UNRECORDED and the count of silent attempts is
    reported beside it.
    """
    (home / "model_calls.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"tokens_in": 200, "tokens_out": 100, "passed": True}),
                json.dumps({"tokens_in": 0, "tokens_out": 0, "passed": True}),
            ]
        ),
        encoding="utf-8",
    )
    spend = child.spend_from_home()
    assert spend["observed"] is True and spend["attempts"] == 2
    assert spend["tokens_recorded"] is False
    assert spend["unrecorded_attempts"] == 1
    assert spend["tokens"] is None
    assert spend["tokens"] != 300


# ── the ONE COMMAND (the entry point a user types) ───────────────────────────


def _run_cli(*args, home_path: Path) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    env["PERSONALCLAW_HOME"] = str(home_path)
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(REPO_ROOT),
        env=env,
    )


def test_the_runner_script_exists_where_the_docs_say_it_does():
    assert RUNNER.is_file(), f"the one command is missing: {RUNNER}"


def test_preflight_command_runs_and_reports_all_ten_runnable(tmp_path):
    """The CALL SITE, not the helper: `python scripts/learning_benchmark.py --preflight` is the
    command the protocol's one-command claim rests on, so it is invoked as a user would."""
    h = tmp_path / "cli-home"
    h.mkdir()
    proc = _run_cli("--preflight", home_path=h)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "task set v2 — 10 task(s)" in proc.stdout
    assert "all 10 task(s) runnable" in proc.stdout
    assert "[NOT]" not in proc.stdout


def test_dry_run_command_plans_paired_cells_over_fixture_homes_and_calls_nothing(tmp_path):
    h = tmp_path / "cli-home"
    h.mkdir()
    proc = _run_cli("--dry-run", "--trials", "5", home_path=h)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "arms: skills_on / skills_off on axis 'arm_mask'" in proc.stdout
    assert "100 cells" in proc.stdout
    assert proc.stdout.count("fixture=empty") == 10
    # Nothing was written into the home's report tree: a plan is not a run.
    assert not (h / "evals" / "learning_bench").exists()


def test_dry_run_refuses_a_task_outside_the_frozen_register(tmp_path):
    h = tmp_path / "cli-home"
    h.mkdir()
    proc = _run_cli("--dry-run", "--task", "sk_invented", home_path=h)
    assert proc.returncode != 0
    assert "not in the frozen register" in proc.stderr + proc.stdout


def test_run_in_an_unbound_home_writes_an_UNMEASURED_report_not_a_zero(tmp_path):
    """§3: the pin is the comparability claim, and `run_matrix` refuses an incomplete one before
    a cell spawns. That refusal must surface as a skipped task carrying the store's own sentence
    — and the report must say NOTHING was measured rather than publish zeros."""
    h = tmp_path / "cli-home"
    h.mkdir()
    proc = _run_cli("--run", "--task", "sk_grill", "--trials", "1", home_path=h)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "incomplete RunPin" in proc.stdout
    assert "NOTHING was measured" in proc.stdout

    reports = sorted((h / "evals" / "learning_bench").iterdir())
    assert len(reports) == 1
    report = json.loads((reports[0] / "report.json").read_text(encoding="utf-8"))
    assert report["measured_tasks"] == 0
    assert report["tasks"] == []
    assert len(report["skipped"]) == 1
    assert "incomplete RunPin" in report["skipped"][0]["blockers"][0]
    # The thresholds are RECORDED from harness/fanout_measure, not restated by the report.
    assert report["thresholds"]["source"] == "harness/fanout_measure.py"
    assert report["thresholds"]["inconclusive_band_points"] == 5.0
    assert report["task_set_version"] == learning_bench.TASK_SET_VERSION
    assert len(report["task_set_fingerprint"]) == 10


# ══ #2562 — the report STATES its schema; no consumer infers it from key presence ══


def test_a_pre_ES17_report_and_a_null_binding_report_are_separable_by_the_stated_schema(home):
    """MEASURED on `main`: both said `report_schema: 1` and both answered `None` to
    `.get("provider_binding")`, so the only thing that told them apart was `'provider_binding' in
    report` — the workaround the panel carried, which in the issue's own words "leaves every other
    future consumer to rediscover the same trick".

    `report_schema` shipped with LV-7 (f0c7788a8) and ES-17 (a5a6d5696) added the field without
    moving it. Adding a field is a schema change.
    """
    common = {
        "created_at": "2026-09-07T00:00:00+00:00",
        "protocol_doc": learning_bench.PROTOCOL_DOC,
        "task_set_version": learning_bench.TASK_SET_VERSION,
        "task_set_fingerprint": {"sk_grill": "a" * 64},
        "trials_per_arm": 5,
        "arms": ["skills_on", "skills_off"],
        "tasks": [],
        "skipped": [],
        "measured_tasks": 0,
        "absent_cells": 0,
    }
    legacy = dict(common, run_id="legacy", report_schema=1)  # provenance never recorded
    unbound = dict(  # provenance recorded, and it says nothing was bound
        common,
        run_id="unbound",
        report_schema=learning_bench.REPORT_SCHEMA,
        provider_binding=None,
    )
    learning_bench.write_report("legacy", legacy)
    learning_bench.write_report("unbound", unbound)
    a = learning_bench.read_report("legacy") or {}
    b = learning_bench.read_report("unbound") or {}

    # The state that made them indistinguishable is still true of the VALUE...
    assert a.get("provider_binding") == b.get("provider_binding") is None
    # ...and the STATED schema is what separates them now.
    assert learning_bench.report_schema(a) == 1
    assert learning_bench.report_schema(b) == learning_bench.REPORT_SCHEMA
    assert learning_bench.provenance_recorded(a) is False
    assert learning_bench.provenance_recorded(b) is True


def test_the_shipped_schema_is_at_or_above_the_one_that_records_provenance():
    """ES-17 added the field and left the version at 1, which is the whole defect. A report may
    never state a schema below its own contents."""
    assert learning_bench.REPORT_SCHEMA >= learning_bench.PROVENANCE_SCHEMA
    assert learning_bench.PROVENANCE_SCHEMA == 2
    assert learning_bench.REPORT_SCHEMA == 2


def test_a_report_stating_NO_schema_is_unrecorded_and_not_v1():
    """The defaulting trap one level up. A truncated or hand-edited artifact cannot say what it
    recorded, and reading that as "v1, so nothing was recorded" is the same absent-versus-declared
    collapse this bump exists to end."""
    assert learning_bench.report_schema({}) is None
    assert learning_bench.report_schema(None) is None
    assert learning_bench.report_schema({"report_schema": "two"}) is None
    assert learning_bench.provenance_recorded({}) is False
    assert learning_bench.provenance_recorded({"report_schema": 1}) is False
    assert learning_bench.provenance_recorded({"report_schema": 2}) is True
    assert learning_bench.provenance_recorded({"report_schema": 3}) is True


def test_reproduction_check_states_both_schemas_rather_than_leaving_them_inferred(home):
    """#2562 asked for exactly this: "have `reproduction_check` or the route say which schema a
    report was written under so a reader isn't inferring it from key presence."

    Reported BESIDE the conditions and never AS one: §8 states four equalities plus verdict class,
    and a fifth condition invented here would be code inventing variance the protocol did not state.
    """
    baseline = {"run_id": "a", "report_schema": 1, "tasks": []}
    rerun = {"run_id": "b", "report_schema": 2, "tasks": []}
    payload = learning_bench.reproduction_check(baseline, rerun).to_dict()
    assert payload["baseline_report_schema"] == 1
    assert payload["rerun_report_schema"] == 2
    assert list(payload["stated_variance"]) == list(learning_bench.REPRODUCTION_CONDITIONS)
    assert len(payload["conditions"]) == len(learning_bench.REPRODUCTION_CONDITIONS)
    notes = " ".join(payload["notes"])
    assert "before provenance and spend recording" in notes
    assert "different schemas (1 vs 2)" in notes
    # A schema-less side says so with the ONE word.
    silent = learning_bench.reproduction_check({"run_id": "a"}, {"run_id": "b"}).to_dict()
    assert silent["baseline_report_schema"] is None
    assert provenance.UNRECORDED in " ".join(silent["notes"])


# ══ #2540 — the §4 token denominator, at the site that builds it ══════════════
#
# 🔑 THIS BLOCK EXISTS BECAUSE A MUTANT SURVIVED. Deleting the whole
# `tokens_recorded`/`unrecorded_cells` guard out of `_verdict_for_task` — the exact site #2540
# names, the one that feeds `Trial.tokens` — left every other test in this repo GREEN, including
# the vocabulary rail (which is a source-TEXT scan and still saw the surviving `tokens_recorded`
# assignments, so it could not tell the guard had gone inert). A guard nothing exercises is the
# inert-control shape this project keeps rediscovering, so the guard is driven here.


def _runner_module():
    """`scripts/learning_benchmark.py` as an importable module.

    Imported rather than shelled out to because what needs asserting is one pure function's
    output, and a subprocess would only tell us the whole run exited 0.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_learning_benchmark_under_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Cell:
    """The `CellResult` shape `_verdict_for_task` reads, with a real artifact on disk."""

    def __init__(self, arm: str, score: float, artifact_ref: str):
        self.coords = {"arm_mask": arm}
        self.outcome = "passed"
        self.score = score
        self.artifact_ref = artifact_ref


def _cell_with_spend(tmp_path: Path, name: str, arm: str, spend: dict) -> _Cell:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(
        json.dumps({"parsed": {"ok": True, "score": 0.8, "tool_calls": 1, "spend": spend}}),
        encoding="utf-8",
    )
    return _Cell(arm, 0.8, str(d))


_RECORDED = {
    "observed": True,
    "attempts": 1,
    "tokens_recorded": True,
    "unrecorded_attempts": 0,
    "tokens": 1000,
    "dollars_est": 0.01,
    "estimated": False,
}
_UNRECORDED = {
    "observed": True,
    "attempts": 2,
    "tokens_recorded": False,
    "unrecorded_attempts": 2,
    "tokens": None,
    "dollars_est": 0.0,
    "estimated": False,
}


def _verdict_over(tmp_path: Path, spends: list[dict]):
    """One task's verdict over `spends`, three cells per arm so the trial floor is met."""
    runner = _runner_module()
    from personalclaw.evals import skills_bench

    task = learning_bench.task_for("sk_grill")
    assert task is not None
    cells = []
    for arm in (skills_bench.ARM_SURFACED, skills_bench.ARM_SUPPRESSED):
        for i, spend in enumerate(spends):
            cells.append(_cell_with_spend(tmp_path, f"{arm}-{i}", arm, spend))
    return runner._verdict_for_task(task, cells)


def test_the_token_denominator_refuses_when_a_cell_reported_no_usage(tmp_path, home):
    """The site #2540 names: `int(spend.get("tokens") or 0)` fed the denominator a 0 for a cell
    that genuinely spent, while the cell still reported `observed: true`.

    Driven through the REAL `_verdict_for_task`, over real cell artifacts on disk, so the guard is
    exercised rather than merely present in the file.
    """
    tv = _verdict_over(tmp_path, [_RECORDED, _RECORDED, _UNRECORDED])
    assert tv.tokens_recorded is False
    assert tv.unrecorded_spend_cells == 2  # one per arm
    assert tv.verdict == "tokens_unrecorded"
    assert tv.token_ratio is None
    # `spend_observed` is UNTOUCHED — the cells could read their own audit files. Two facts.
    assert tv.spend_observed is True


def test_a_cell_artifact_written_before_the_flag_existed_also_refuses(tmp_path, home):
    """An ABSENT `tokens_recorded` is unrecorded too: a cell artifact from before #2540 never
    recorded whether its provider reported usage, so its token count cannot be vouched for."""
    legacy = {k: v for k, v in _RECORDED.items() if k != "tokens_recorded"}
    tv = _verdict_over(tmp_path, [legacy, legacy, legacy])
    assert tv.tokens_recorded is False
    assert tv.verdict == "tokens_unrecorded"
    assert tv.token_ratio is None


def test_a_fully_reporting_run_still_gets_its_ratio(tmp_path, home):
    """THE VACUITY FLOOR for the two tests above. If the guard fired unconditionally, the §4
    metric would be permanently refused and both tests would still pass — so the ordinary case has
    to produce a real ratio over real numbers."""
    tv = _verdict_over(tmp_path, [_RECORDED, _RECORDED, _RECORDED])
    assert tv.tokens_recorded is True
    assert tv.unrecorded_spend_cells == 0
    assert tv.verdict != "tokens_unrecorded"
    assert tv.token_ratio == 1.0  # identical spend on both arms
    assert tv.arms["skills_on"]["tokens"] == 3000


def test_the_run_level_spend_facts_are_derived_from_the_task_rows(tmp_path, home):
    """🔑 A SECOND SURVIVING MUTANT. Replacing the run-level `tokens_recorded` with a hardcoded
    `True` left every test green: the field was an expression inlined into the report literal, so
    nothing could reach it. It is a function now, and this drives it.

    The claim it makes is strong — "no token ratio anywhere in this table is evidence" — so it has
    to be derived from the rows rather than asserted by the writer.
    """
    runner = _runner_module()

    clean = _verdict_over(tmp_path / "clean", [_RECORDED, _RECORDED, _RECORDED])
    dirty = _verdict_over(tmp_path / "dirty", [_RECORDED, _RECORDED, _UNRECORDED])

    # One unreporting task makes the RUN unrecorded, and the count is the SUM across tasks.
    mixed = runner.run_spend_facts([clean, dirty])
    assert mixed["tokens_recorded"] is False
    assert mixed["unrecorded_spend_cells"] == dirty.unrecorded_spend_cells == 2

    # VACUITY FLOOR: an all-reporting run says so, so the field is not stuck at `False` either.
    ok = runner.run_spend_facts([clean, clean])
    assert ok["tokens_recorded"] is True
    assert ok["unrecorded_spend_cells"] == 0

    # A run that verdicted NOTHING records `True` — nothing failed to report because nothing
    # reported. `measured_tasks: 0` beside it is what says so, and it is the state the
    # unbound-home CLI run above actually produces.
    empty = runner.run_spend_facts([])
    assert empty == {"tokens_recorded": True, "unrecorded_spend_cells": 0}
