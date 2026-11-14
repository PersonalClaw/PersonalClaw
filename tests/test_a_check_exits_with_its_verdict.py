"""A check exits with its verdict: 1 when it found what it checks for, as the exit table says.

docs/reference/cli.md's exit table says exit 1 is "it ran and refused or failed: no gateway is
running, … a check found a problem". These commands printed their report and exited 0 whatever it
said, so a script that ran one read every answer as a pass:

* ``status`` exits 1 when no gateway of this home is running, or the one it reached went away
  (its report on stdout either way); one too busy to give its stats in 3 seconds is running, 0;
* ``eval`` exits 1 when a scenario failed, and ``eval-gate`` when the proposal regressed;
* ``backup export`` and ``footprint --reclaim`` exit 1 when another run holds the lock, so nothing
  was exported or reclaimed;
* ``project import`` exits 1 when it left something unchanged, as ``restore`` does;
* ``security audit`` and ``memory audit`` exit 1 when the scan found something, or memory could
  not be opened to scan, so nothing in it was checked (said on stderr).

A report that ran and is a state, not a check, keeps 0: ``incident status`` while incident mode is
on, and ``ablation`` when nothing is due.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
from types import SimpleNamespace

import pytest
from fakes import gateway_stand_in

from personalclaw import cli, cli_commands, cli_project


def _args(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(list(argv))


def _exit_of(call) -> int:
    """The exit status *call* ends with: its return value, the code it exits with, or 0."""
    try:
        code = call()
    except SystemExit as stop:
        code = stop.code
    return 0 if code is None else int(code)


# ── status ────────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch, unset_env):
    unset_env("PERSONALCLAW_PORT")
    h = tmp_path / "home"
    h.mkdir()
    (h / ".local_secret").write_text("synthetic-local-secret\n")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    from personalclaw.service import controller

    monkeypatch.setattr(controller, "this_homes_service", lambda: None)
    return h


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
def test_status_exits_1_with_no_gateway_running_and_says_so_on_stdout(home, capsys, flags):
    from personalclaw.cli_server import _status as status

    assert _exit_of(lambda: status(_args("status", *flags))) == 1
    out, err = capsys.readouterr()
    assert out, "the report is on stdout either way"
    assert err == ""
    if flags:
        assert json.loads(out)["running"] is False


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
def test_status_exits_0_while_this_homes_gateway_runs(home, capsys, flags):
    from personalclaw.cli_server import _status as status

    payload = {"cron": {"total": 1, "enabled": 1, "broken": 0}, "stats": {"total_turns": 7}}
    with gateway_stand_in(home, routes={("GET", "/api/status"): (200, payload)}) as gateway:
        gateway.record()
        assert _exit_of(lambda: status(_args("status", *flags))) == 0
    assert capsys.readouterr().out


def _stats_read_raises(monkeypatch, raised: BaseException) -> None:
    """Every ``/api/status`` read raises *raised*; the gateway answers every other request, so
    ``reach`` finds it running first."""
    import urllib.request

    from personalclaw import home_gateway

    real = home_gateway.open_loopback

    def stats_fail(request: urllib.request.Request, *args, **kwargs):
        if request.full_url.endswith("/api/status"):
            raise raised
        return real(request, *args, **kwargs)

    monkeypatch.setattr(home_gateway, "open_loopback", stats_fail)


#: A gateway too slow to answer: a read that timed out, and a connect that did.
_TIMEOUTS = pytest.mark.parametrize(
    "timeout",
    [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))],
    ids=["while-reading", "while-connecting"],
)


@_TIMEOUTS
@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
def test_status_reads_a_gateway_too_slow_for_its_stats_as_running(
    home, capsys, monkeypatch, flags, timeout
):
    """A busy gateway that takes over 3 seconds for its stats is running, exit 0: not gone."""
    from personalclaw.cli_server import _status as status

    with gateway_stand_in(home) as gateway:
        gateway.record()
        _stats_read_raises(monkeypatch, timeout)
        assert _exit_of(lambda: status(_args("status", *flags))) == 0
        said = gateway.port
    out = capsys.readouterr().out
    if flags:
        report = json.loads(out)
        assert (report["running"], report["port"], report["stats_unavailable"]) == (
            True,
            said,
            "timeout",
        )
    else:
        assert "running but did not answer for its stats within 3 seconds" in out


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
def test_status_exits_1_for_a_gateway_that_went_away_while_asked(home, capsys, monkeypatch, flags):
    from personalclaw import home_gateway
    from personalclaw.cli_server import _status as status

    with gateway_stand_in(home) as gateway:
        gateway.record()
        said = home_gateway.reach(None).gone()
        _stats_read_raises(
            monkeypatch, urllib.error.URLError(ConnectionRefusedError(61, "refused"))
        )
        assert _exit_of(lambda: status(_args("status", *flags))) == 1
    out = capsys.readouterr().out
    if flags:
        report = json.loads(out)
        assert (report["running"], report["stats_unavailable"]) == (False, "gone")
    else:
        assert said in out


# ── eval and eval-gate ────────────────────────────────────────────────────────


def _eval(monkeypatch, tmp_path, passed: list[bool]) -> int:
    """``eval`` over scenarios that pass as *passed* says, with no model called."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("personalclaw.evals.scenarios.install_library", lambda: None, raising=False)
    monkeypatch.setattr(
        "personalclaw.evals.scenarios.installed_dir", lambda: tmp_path, raising=False
    )
    scenario = SimpleNamespace(name="smoke", sessions=[], judge_criteria="", description="")
    monkeypatch.setattr(cli_commands, "load_scenario", lambda path: scenario)
    results = [
        SimpleNamespace(passed=ok, sessions=[], summary=lambda ok=ok: {"passed": ok})
        for ok in passed
    ]

    class _Runner:
        def __init__(self, **_kw) -> None:
            pass

        async def run_scenarios(self, scenarios):
            return results

    monkeypatch.setattr(cli_commands, "EvalRunner", _Runner)
    monkeypatch.setattr(cli_commands, "format_results", lambda rs: "# report")
    monkeypatch.setattr(cli_commands, "score_by_dimension", lambda rs: {})
    monkeypatch.setattr(
        cli_commands.AppConfig,
        "load",
        staticmethod(lambda: SimpleNamespace(create_provider_factory=lambda: None)),
    )
    import asyncio

    args = argparse.Namespace(all_scenarios=False, scenarios=None, judge=False)
    return _exit_of(lambda: asyncio.run(cli_commands._run_eval(args)))


def test_eval_exits_1_when_a_scenario_failed(monkeypatch, tmp_path, capsys):
    assert _eval(monkeypatch, tmp_path, [True, False]) == 1
    assert "Overall: 1/2 scenarios passed" in capsys.readouterr().out


def test_eval_exits_0_when_every_scenario_passed(monkeypatch, tmp_path, capsys):
    assert _eval(monkeypatch, tmp_path, [True, True]) == 0
    assert "Overall: 2/2 scenarios passed" in capsys.readouterr().out


def _gate(monkeypatch, before: float, after: float) -> int:
    from personalclaw.evals import gate
    from personalclaw.learning import proposals

    report = gate.GateReport(state="gated", reason="", run_id="g1")
    report.before["mean_score"], report.after["mean_score"] = before, after
    monkeypatch.setattr(gate, "gate_subset", lambda: SimpleNamespace(members=[]))
    monkeypatch.setattr(proposals, "get", lambda pid: SimpleNamespace(kind="prompt"))
    monkeypatch.setattr(gate, "gate_proposal", lambda pid, **kw: report)
    return _exit_of(lambda: cli_commands._eval_gate(_args("eval-gate", "p1")))


def test_eval_gate_exits_1_on_a_regression(monkeypatch, capsys):
    assert _gate(monkeypatch, before=0.9, after=0.6) == 1
    assert "REGRESSION" in capsys.readouterr().out


def test_eval_gate_exits_0_when_the_proposal_holds_up(monkeypatch, capsys):
    assert _gate(monkeypatch, before=0.6, after=0.9) == 0
    assert "REGRESSION" not in capsys.readouterr().out


# ── backup export and footprint --reclaim ─────────────────────────────────────


def test_backup_export_exits_1_while_another_export_runs(tmp_path, capsys):
    from personalclaw.concurrency import single_flight
    from personalclaw.durability import shards

    with single_flight("shard-export") as acquired:
        assert acquired
        code = shards.backup_cmd(_args("backup", "export", str(tmp_path / "out")))
    out, err = capsys.readouterr()
    assert (code, out) == (1, "")
    assert "nothing was exported" in err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
def test_footprint_reclaim_exits_1_while_another_reclaim_runs(capsys, flags):
    from personalclaw.concurrency import single_flight
    from personalclaw.durability import footprint

    with single_flight("footprint-reclaim") as acquired:
        assert acquired
        code = footprint.footprint_cmd(_args("footprint", "--reclaim", *flags))
    out, err = capsys.readouterr()
    assert code == 1
    assert "nothing was reclaimed" in err
    if flags:
        assert json.loads(out)["reclaim"] is None, "the document still prints"
    else:
        assert out == "", "a reclaim that did not run is said on stderr alone"


def test_footprint_reclaim_exits_0_when_it_ran(capsys):
    from personalclaw.durability import footprint

    assert footprint.footprint_cmd(_args("footprint", "--reclaim", "--json")) == 0
    assert json.loads(capsys.readouterr().out)["reclaim"] is not None


# ── project import ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("left", [["a link at projects/p"], []], ids=["left", "all"])
def test_project_import_exits_1_when_it_left_something_unchanged(
    monkeypatch, tmp_path, capsys, left
):
    from personalclaw.config import loader
    from personalclaw.tasks import hierarchy

    archive = tmp_path / "a.pcproj"
    archive.write_bytes(b"x")
    plan = SimpleNamespace(
        project_name="Plans", accepted=[1], refused=[], secrets_expected=[], ok=True
    )
    monkeypatch.setattr(cli_project.pa, "projects_folder", lambda home: None)
    monkeypatch.setattr(cli_project.pa, "read_archive_plan", lambda *a, **kw: (plan, None))
    monkeypatch.setattr(
        cli_project.pa,
        "import_project",
        lambda *a, **kw: (SimpleNamespace(name="Plans", id="p1"), ["e1"], list(left)),
    )
    monkeypatch.setattr(
        hierarchy, "HierarchyStore", lambda: SimpleNamespace(list_projects=lambda: [])
    )
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "personalclaw.workflows.project_export.import_summary", lambda plan, **kw: "Plans"
    )
    code = cli_project._import(
        argparse.Namespace(archive=str(archive), passphrase="", dry_run=False)
    )
    out, err = capsys.readouterr()
    assert code == (1 if left else 0)
    assert "Imported as Plans" in out
    assert ("Left unchanged" in err) == bool(left)


# ── security audit and memory audit ───────────────────────────────────────────

_FOUND = {"file": "h.jsonl", "warning": "reads a key", "snippet": "cat ~/.ssh/id_rsa"}
_MEM = {"type": "semantic", "key": "k", "warning": "an instruction", "value": "ignore all"}


@pytest.mark.parametrize(
    "history, memory, code",
    [([_FOUND], [], 1), ([], [_MEM], 1), ([], [], 0)],
    ids=["history", "memory", "clean"],
)
def test_security_audit_exits_1_when_it_found_something(monkeypatch, capsys, history, memory, code):
    monkeypatch.setattr(cli_commands, "scan_history", lambda folder: history)
    monkeypatch.setattr(cli_commands, "scan_memory", lambda: memory)
    assert _exit_of(lambda: cli_commands._security(_args("security", "audit"))) == code
    out, err = capsys.readouterr()
    assert out and err == "", "the report is on stdout either way"


@pytest.mark.parametrize("memory, code", [([_MEM], 1), ([], 0)], ids=["found", "clean"])
def test_memory_audit_exits_1_when_it_found_something(monkeypatch, capsys, memory, code):
    monkeypatch.setattr(cli_commands, "scan_memory", lambda: memory)
    assert _exit_of(lambda: cli_commands._memory_cmd(_args("memory", "audit"))) == code
    out, err = capsys.readouterr()
    assert out and err == ""


#: What both audits say when memory could not be opened to scan, for an ``init`` that raised this.
_NOT_SCANNED = (
    "Error: memory could not be opened to scan (OSError: disk I/O error), so nothing in it was "
    "checked."
)


def _memory_cannot_be_opened(monkeypatch) -> None:
    from personalclaw.vector_memory import VectorMemoryStore

    def init(self, *args, **kwargs):
        raise OSError("disk I/O error")

    monkeypatch.setattr(VectorMemoryStore, "init", init)


@pytest.mark.parametrize("history", [[], [_FOUND]], ids=["history-clean", "history-found"])
def test_security_audit_exits_1_when_memory_could_not_be_opened_to_scan(
    monkeypatch, capsys, history
):
    """A scan that never ran is not one that found nothing: history's report stands, memory is not
    called clean, and why nothing in it was checked is on stderr."""
    monkeypatch.setattr(cli_commands, "scan_history", lambda folder: history)
    _memory_cannot_be_opened(monkeypatch)
    assert _exit_of(lambda: cli_commands._security(_args("security", "audit"))) == 1
    out, err = capsys.readouterr()
    assert ("suspicious entries found" if history else "No suspicious tool usage found") in out
    assert "vector memory" not in out
    assert err.strip() == _NOT_SCANNED


@pytest.mark.parametrize("where", ["opening", "scanning"])
def test_memory_audit_exits_1_when_memory_could_not_be_opened_to_scan(monkeypatch, capsys, where):
    """Whether the command's own open of memory failed or the scan's, nothing was checked: said on
    stderr, exit 1, stdout empty."""
    if where == "opening":
        _memory_cannot_be_opened(monkeypatch)
    else:
        from personalclaw.security_audit import MemoryNotScanned

        def scan():
            raise MemoryNotScanned("OSError: disk I/O error")

        monkeypatch.setattr(cli_commands, "scan_memory", scan)
    assert _exit_of(lambda: cli_commands._memory_cmd(_args("memory", "audit"))) == 1
    out, err = capsys.readouterr()
    assert (out, err.strip()) == ("", _NOT_SCANNED)


# ── the reports that keep 0 ───────────────────────────────────────────────────


def test_incident_status_exits_0_while_incident_mode_is_on(capsys):
    from personalclaw.cli import _incident_cmd

    _incident_cmd(_args("incident", "on", "--reason", "drill"))
    try:
        capsys.readouterr()
        assert _exit_of(lambda: _incident_cmd(_args("incident", "status"))) == 0
        assert "Incident mode ACTIVE" in capsys.readouterr().out
    finally:
        _incident_cmd(_args("incident", "off"))


def test_ablation_exits_0_when_nothing_is_due(monkeypatch, capsys):
    from personalclaw.evals import ablation

    component = SimpleNamespace(
        component_id="c1", kind="prompt", target="t", subject="s", arms=lambda: ["on", "off"]
    )
    monkeypatch.setattr(ablation, "pick_component", lambda: component)
    monkeypatch.setattr(ablation, "validate_component", lambda c: None)
    monkeypatch.setattr(ablation, "due", lambda: False)
    monkeypatch.setattr(ablation, "_cadence_days", lambda: 30)
    assert _exit_of(lambda: cli_commands._ablation(_args("ablation"))) == 0
    assert "Not due yet" in capsys.readouterr().out
