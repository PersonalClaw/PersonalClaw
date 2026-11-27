"""A run a Temporary or Incognito chat started is never made an eval case.

Such a run keeps its chat's mode (``workflows.ownership.run_mode``): its work writes nothing to
memory, reaches no model but the chat's own, and is deleted with the chat. The eval harvest did not
look at that mode. ``personalclaw eval harvest`` wrote the run's inputs into the scenario library in
the home, where they outlived the chat, and the template optimizer (``candidate_score.load_cases``)
built cases from it and sent them to its arms and judge.

Now the harvest skips such a run, and one whose chat's mode cannot be read, with its own reason,
for every caller. A case written before from a run that is still kept and says it is a Temporary or
Incognito chat's is removed, the next time anything harvests or installs the library. One whose
chat cannot be read, by its mode or its run record, is kept where it is, since a damaged record must
not quietly remove a case the owner kept, and left out of every eval: the harvested suite, the
scenario a study or the matrix names, and ``personalclaw eval``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3

import pytest

from personalclaw.evals import candidate_score
from personalclaw.evals import harvest as hv
from personalclaw.evals import scenarios as sc
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

#: The word the report and the CLI give a private chat's run.
PRIVATE = "private_run"
WORKFLOW = "daily_digest"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _run(mode: str = "", *, root: str = "", words: str = "my words") -> WorkflowRun:
    """A finished run, journaled the way the controller does; *mode* is its chat's."""
    extra = {"memory_mode": mode} if mode else {}
    run = store_mod.create(
        WorkflowRun(id="", workflow_name=WORKFLOW, extra=extra, root_run_id=root)
    )
    run.status = RunStatus.COMPLETE
    run = store_mod.save(run)
    j = journal_mod.Journal(run.id)
    j.run_started(WORKFLOW, inputs={"ask": words}, spec_version=1)
    j.step_completed("root.fetch", "fetch", epoch=1, cache_key="ck", state=InstanceState.DONE)
    j.run_finished(RunStatus.COMPLETE.value, elapsed_secs=1.0, tokens=1)
    return run


def _keep_as(run: WorkflowRun, mode: str) -> None:
    """Record *mode* on a run already kept, as a run harvested before the check carries it."""
    run.extra = {**run.extra, "memory_mode": mode}
    store_mod.save(run)


def _reasons(report: hv.HarvestReport) -> dict[str, str]:
    return {entry["run_id"]: entry["reason"] for entry in report.skipped}


@pytest.mark.parametrize("mode", ["incognito", "temporary", "a-mode-this-build-does-not-know"])
def test_a_private_chats_run_is_skipped_with_its_own_reason(home, mode):
    """🔴 Red before: harvested like any other run."""
    private, ordinary = _run(mode), _run()

    report = hv.harvest(write=False)

    assert [case.run_id for case in report.cases] == [ordinary.id]
    assert _reasons(report) == {private.id: PRIVATE}
    assert hv.case_from_run(private) == (None, PRIVATE)
    assert PRIVATE in hv.SKIP_REASONS


def test_a_run_started_from_a_private_chats_run_is_skipped(home):
    """🔴 Red before: a subworkflow or a fork of an Incognito chat's run, which carries no mode of
    its own, was harvested. Whose run it is is judged at its tree's root."""
    root = _run("incognito")
    child = _run(root=root.id)

    report = hv.harvest(write=False)

    assert _reasons(report) == {root.id: PRIVATE, child.id: PRIVATE}
    assert report.cases == []


def test_a_harvest_writes_no_case_from_a_private_chats_run(home):
    """🔴 Red before: the case, with the chat's words, was written into the home's library."""
    _run("incognito", words="said in an incognito chat")
    _run("temporary", words="said in a temporary chat")
    ordinary = _run(words="said in an ordinary chat")

    report = hv.harvest(write=True)

    assert [case.run_id for case in report.cases] == [ordinary.id]
    assert report.skipped_by_reason() == {PRIVATE: 2}
    library = "".join(path.read_text(encoding="utf-8") for path in sc.installed_dir().iterdir())
    assert "said in an ordinary chat" in library
    assert "incognito chat" not in library
    assert "temporary chat" not in library


def test_the_template_optimizer_scores_no_private_chats_run(home):
    """🔴 Red before: the search built cases from it and sent them to its arms and judge."""
    ordinary = [_run() for _ in range(candidate_score.MIN_CASES)]
    private = _run("incognito")

    cases = candidate_score.load_cases(WORKFLOW)

    run_ids = {case["harvest"]["run_id"] for case in cases}
    assert private.id not in run_ids
    assert run_ids == {run.id for run in ordinary}


def test_a_case_written_before_is_removed_while_its_run_is_kept(home):
    """🔴 Red before: a case harvested from a private chat's run stayed in the library."""
    private, ordinary, gone = _run(), _run(), _run()
    hv.harvest(write=True)
    _keep_as(private, "incognito")
    assert store_mod.delete(gone.id)

    report = hv.harvest(write=True)

    names = {path.stem for path in sc.installed_dir().iterdir()}
    assert report.dropped == [hv.case_name(WORKFLOW, private.id)]
    assert hv.case_name(WORKFLOW, private.id) not in names
    assert hv.case_name(WORKFLOW, ordinary.id) in names
    # A case whose run is gone cannot be told apart, and stays.
    assert hv.case_name(WORKFLOW, gone.id) in names
    assert hv.case_name(WORKFLOW, private.id) not in (sc.read_manifest() or {})["scenarios"]

    assert hv.harvest(write=True).dropped == []
    assert {path.stem for path in sc.installed_dir().iterdir()} == names


def test_an_eval_run_removes_such_a_case_before_it_reads_the_library(home):
    """🔴 Red before: ``personalclaw eval`` installs the library and then runs every case in it."""
    private = _run()
    hv.harvest(write=True)
    _keep_as(private, "temporary")

    sc.install_library()

    assert not (sc.installed_dir() / f"{hv.case_name(WORKFLOW, private.id)}.json").exists()


def test_the_harvested_suite_never_reads_such_a_case(home):
    """🔴 Red before: a study or a skill bench scored it."""
    private, ordinary = _run(), _run()
    hv.harvest(write=True)
    _keep_as(private, "incognito")

    suite = hv.load_harvested_suite(workflow_name=WORKFLOW)

    assert [case["harvest"]["run_id"] for case in suite] == [ordinary.id]


def test_the_cli_names_the_reason_and_what_it_removed(home, capsys):
    """🔴 Red before: the dry run listed the private chat's run as a case."""
    from personalclaw import cli_commands

    earlier = _run()
    hv.harvest(write=True)
    _keep_as(earlier, "incognito")
    _run("incognito")
    _run()

    cli_commands._eval_harvest(
        argparse.Namespace(workflow="", limit=0, dry_run=True, list_suite=False)
    )
    assert f"2 x {PRIVATE}" in capsys.readouterr().out

    cli_commands._eval_harvest(
        argparse.Namespace(workflow="", limit=0, dry_run=False, list_suite=False)
    )
    assert "Removed 1 case(s) built earlier from runs of Temporary or Incognito chats" in (
        capsys.readouterr().out
    )


class _Ran(Exception):
    """Raised by the stand-in runner, so ``personalclaw eval`` stops at the cases it would run."""


@pytest.fixture
def eval_ran(monkeypatch):
    """``personalclaw eval`` up to the scenarios it hands its runner, with no model reached."""
    from personalclaw import cli_commands

    ran: list[str] = []

    class Runner:
        def __init__(self, **_kwargs):
            pass

        async def run_scenarios(self, scenarios):
            ran.extend(scenario.name for scenario in scenarios)
            raise _Ran

    class Config:
        @staticmethod
        def load():
            return Config()

        def create_provider_factory(self):
            return None

    monkeypatch.setattr(cli_commands, "EvalRunner", Runner)
    monkeypatch.setattr(cli_commands, "AppConfig", Config)
    return ran


def _eval(**kwargs) -> None:
    from personalclaw import cli_commands

    asyncio.run(cli_commands._run_eval(argparse.Namespace(judge=False, **kwargs)))


@pytest.mark.parametrize("unreadable_by", ["its mode", "its run record"])
def test_a_case_whose_chat_cannot_be_read_is_left_out_and_kept(
    home, monkeypatch, caplog, capsys, eval_ran, unreadable_by
):
    """🔴 Red before: the harvested suite read it, a study could name it, and ``personalclaw eval``
    ran it. It is not removed: only a run that says it is a private chat's removes its case."""
    unreadable, ordinary = _run(), _run()
    hv.harvest(write=True)
    name = hv.case_name(WORKFLOW, unreadable.id)
    if unreadable_by == "its mode":
        _keep_as(unreadable, "a-mode-this-build-does-not-know")
    else:
        real_get = store_mod.get

        def get(run_id):
            if run_id == unreadable.id:
                raise sqlite3.DatabaseError("database disk image is malformed")
            return real_get(run_id)

        monkeypatch.setattr(store_mod, "get", get)
    caplog.set_level(logging.WARNING, logger="personalclaw.evals.harvest")

    report = hv.harvest(write=True)
    sc.install_library()

    assert (sc.installed_dir() / f"{name}.json").is_file()
    assert report.dropped == []
    assert hv.withheld_cases() == {name: hv.WITHHELD_UNREADABLE}
    assert any(unreadable.id in record.getMessage() for record in caplog.records)
    suite = hv.load_harvested_suite(workflow_name=WORKFLOW)
    assert [case["harvest"]["run_id"] for case in suite] == [ordinary.id]
    with pytest.raises(sc.ScenarioLibraryError, match="left out of every eval"):
        sc.resolve_scenario_path(name)

    with pytest.raises(_Ran):
        _eval(all_scenarios=True, scenarios=[])
    assert name not in eval_ran
    assert hv.case_name(WORKFLOW, ordinary.id) in eval_ran

    with pytest.raises(SystemExit):
        _eval(all_scenarios=False, scenarios=[name])
    assert "left out of every eval" in capsys.readouterr().err
