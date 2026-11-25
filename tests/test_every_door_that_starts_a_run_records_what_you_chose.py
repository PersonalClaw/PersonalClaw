"""Every door that starts a run as yours records what you gave it, so its steps run it as yours.

A step runs a command, or works in a folder, an input names only with the definition's default or
a value its owner chose (`command_inputs.Chosen`), and a run that records nothing holds nothing.
So a door that started a run as hers without recording her values would leave her own runs unable
to run their checks. Each door that starts one as hers goes through `service.start_run` with
``started_by=""``, which records them (`command_inputs.stamped`), and the run keeps that record:

* the Run button (`handlers.api_run_start`, her request read by `command_inputs.of_request`);
* a loop started from the Loops page (`loop_routes`, the same reading, through `start_kind_run`),
  whose every cycle runs inside that one run;
* a batch and a proposal she allowed (`batch_start`, `proposals_contract`: ``started_by=""``);
* an automation or a schedule she made (`run_workflow_provider._record`, rows in
  `test_a_step_runs_only_the_command_you_chose`);
* a run a step starts and a fork (`handed_by`, `carried`, rows there too);
* a restart, which reads the run, its record with it, from the store (row here).

The optimize-harness CLI starts no run: its subcommands are the steps of a run started at one of
these doors (row here, through the template's own preflight input).

`test_every_start_of_a_run_says_who_started_it` holds every production call to naming who
started it, so no door starts a run as hers by leaving it out.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from test_a_run_someone_else_starts_names_no_command import _request
from test_an_automation_runs_the_workflow_version_its_owner_allowed import (  # noqa: F401
    _native_store,
)

from personalclaw.workflows import command_inputs, service, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.versions import OWNER


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_PORT", "10771")
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    return tmp_path


SPEC = {
    "name": "checked",
    "inputs": {"check": {"type": "string", "required": False, "default": "make check"}},
    "root": {
        "kind": "sequence",
        "id": "root",
        "children": [
            {
                "kind": "gate",
                "id": "verify",
                "config": {"kind": "verify_command", "verify": {"command": "{{inputs.check}}"}},
            }
        ],
    },
}


def _saved() -> None:
    out = asyncio.run(
        service.author_def(
            name=SPEC["name"],
            root=SPEC["root"],
            inputs=SPEC["inputs"],
            description="runs a check",
            saved_by=OWNER,
            strict=False,
        )
    )
    assert out.get("ok") is True, out


def _holds(run: Any, spec: dict[str, Any], name: str) -> bool:
    return command_inputs.Chosen.of(spec, run.inputs, run.extra).chose(name)


def test_her_run_button_records_the_values_she_gives(monkeypatch):
    from personalclaw.workflows import handlers as H

    _saved()
    monkeypatch.setattr(H, "_guard", lambda *a, **k: None)
    asyncio.run(H.api_run_start(_request({}, user="owner")))
    [run] = store.all_runs()
    assert run.inputs.get("check") == "id" and _holds(run, SPEC, "check"), run.extra


def test_a_run_read_back_as_a_restart_reads_it_holds_what_she_chose():
    _saved()
    out = asyncio.run(
        service.start_run(name="checked", inputs={"check": "make lint"}, started_by="")
    )
    # No supervisor drives it here, so it is created and not started: what a restart reads.
    assert out.get("run_id"), out
    again = store.get(out["run_id"])
    assert again is not None and _holds(again, SPEC, "check"), again


def test_the_optimize_harness_preflight_reads_her_target_as_hers():
    """The optimize-harness CLI's `PC_OPT_SUBJECT` is the template's `target_template` input, and
    a run she starts holds what she gave it."""
    template = read_template("optimize-harness")
    assert template is not None
    spec = template.to_dict()
    assert "target_template" in command_inputs.readers(spec), "vacuity: a step runs it"
    given = {"target_template": "code-project"}
    record = command_inputs.stamped({}, given, "")
    assert command_inputs.Chosen.of(spec, given, record).chose("target_template")
    assert not command_inputs.Chosen.of(
        spec, given, command_inputs.stamped({}, given, command_inputs.AGENT)
    ).chose("target_template"), "the control: another's start records none of it"
