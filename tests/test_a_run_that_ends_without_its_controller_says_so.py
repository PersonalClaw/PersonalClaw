"""A run that ends without its controller tells the trigger that started it, as any run does.

A run's end is told to the trigger that started it (`run_finish.report_to_its_trigger`) and handed
to what waits on it (`run_finish.chain_after_run`) by its controller's one terminal writer. Two
endings are written by the watchdog instead, with no controller: a run whose spec cannot be read is
failed, and a run whose steps all ended while no controller was driving it is closed. Both now say
how the run ended on the trigger's route too: before, the trigger that started the run never heard
from it again.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.models import (
    InstanceState,
    NodeInstance,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.watchdog import WorkflowWatchdog

TRIGGER = "clock:nightly-digest"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    return tmp_path


def _watched() -> tuple[WorkflowWatchdog, list[tuple[str, dict[str, Any]]], list[tuple[str, str]]]:
    reports: list[tuple[str, dict[str, Any]]] = []
    chained: list[tuple[str, str]] = []
    watchdog = WorkflowWatchdog(
        None,
        EngineServices(
            report_to_trigger=lambda trigger_id, **kw: reports.append((trigger_id, kw)),
            run_ended=lambda run, **kw: chained.append((run.id, kw["status"])),
        ),
    )
    return watchdog, reports, chained


def _started_by_a_trigger() -> WorkflowRun:
    return store.create(
        WorkflowRun(
            id="",
            workflow_name="nightly-digest",
            status=RunStatus.RUNNING,
            origin=RunOrigin(kind=OriginKind.HOOK, trigger_id=TRIGGER),
        )
    )


def test_a_run_whose_spec_cannot_be_read_tells_its_trigger_it_failed():
    watchdog, reports, chained = _watched()
    run = _started_by_a_trigger()

    asyncio.run(watchdog._fail(run, "run spec is missing or unreadable"))

    assert reports == [
        (TRIGGER, {"error": "run spec is missing or unreadable", "summary": "", "run_id": run.id})
    ]
    assert chained == [(run.id, "failed")]


def test_a_run_closed_after_its_steps_ended_tells_its_trigger_and_what_waits_on_it():
    watchdog, reports, chained = _watched()
    run = _started_by_a_trigger()
    spec = {
        "name": "nightly-digest",
        "root": {"kind": "stage", "id": "digest", "config": {"prompt": "write the digest"}},
    }
    store.write_spec(run.id, spec)
    store.write_state(run.id, {"root": NodeInstance(path="root", state=InstanceState.DONE)})

    assert watchdog._reap_if_finished(run, spec) is True

    assert store.get(run.id).status is RunStatus.COMPLETE
    assert reports == [(TRIGGER, {"error": "", "summary": "", "run_id": run.id})]
    assert chained == [(run.id, "complete")]
