"""A command a workflow hands a workflow it starts waits for your yes, as one it runs itself does.

A ``subworkflow`` step gives the run it starts its inputs (``config.inputs``), and so does a
``run-workflow`` action (``config.with.inputs``). The run it starts holds each value its parent's
definition writes as yours (`command_inputs.handed`), so its steps run that value as a command you
chose. 🔴 Before, a save through any door but yours wrote one with nobody asked: no value a step
hands another workflow was a command the save writes. So an agent could save a workflow that
starts code-project or goal-pursuit-verifiable with a verify command of its own, and have it run.
Now each value a step hands a workflow it starts is a command the definition writes, and its save
waits for your yes (`test_a_command_an_agent_saves_waits_for_your_yes`).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from test_a_command_an_agent_saves_waits_for_your_yes import _root, home  # noqa: F401 - fixture

from personalclaw.workflows import command_inputs, service
from personalclaw.workflows.versions import AGENT, APP, OWNER

pytestmark = pytest.mark.security

ITS_OWN = "echo agent-written-check"


#: The task a parent hands its child beside the command: text it writes too, and asked about too.
TASK = {"task": "Tidy the repo."}


def _subworkflow(template: str, name: str, value: Any, task: dict = TASK) -> dict[str, Any]:
    return {
        "kind": "subworkflow",
        "id": "child",
        "config": {"ref": template, "inputs": {**task, name: value}},
    }


def _run_workflow(template: str, name: str, value: Any, task: dict = TASK) -> dict[str, Any]:
    return {
        "kind": "action",
        "id": "child",
        "config": {
            "provider": "run-workflow",
            "with": {"workflow": template, "inputs": {**task, name: value}},
        },
    }


STARTS = {"a subworkflow step": _subworkflow, "a run-workflow action": _run_workflow}
#: Bundled templates whose steps run the input as a command.
CHECKS = [("goal-pursuit-verifiable", "verify_command"), ("code-project", "guard_command")]


def _save(step: dict[str, Any], *, saved_by: str, owner_allowed: bool = False):
    return asyncio.run(
        service.author_def(
            name="starts-a-check",
            root=_root(step),
            description="starts a checked workflow",
            saved_by=saved_by,
            owner_allowed=owner_allowed,
            strict=False,
        )
    )


@pytest.mark.parametrize(("template", "name"), CHECKS)
@pytest.mark.parametrize("start", sorted(STARTS))
@pytest.mark.parametrize("saved_by", [AGENT, APP])
def test_a_command_it_hands_a_workflow_it_starts_waits_for_your_yes(
    start, template, name, saved_by
):
    """🔴 S2. Before: saved with nobody asked, and the child ran the agent's command."""
    out = _save(STARTS[start](template, name, ITS_OWN), saved_by=saved_by)
    assert out.get("code") == "WF_DEF_NEEDS_OWNER_YES", out
    [asked] = [s for s in out.get("steps") or [] if ITS_OWN in " ".join(s.get("may") or [])]
    assert asked["label"] == f"input `{name}` that step child hands the workflow it starts", asked
    assert out.get("consent") == command_inputs.WRITES_A_COMMAND, out


@pytest.mark.parametrize("start", sorted(STARTS))
def test_your_own_save_and_your_yes_save_it(start):
    step = STARTS[start]("code-project", "guard_command", ITS_OWN)
    assert _save(step, saved_by=OWNER).get("ok") is True
    again = _save(step, saved_by=AGENT)
    assert again.get("code") != "WF_DEF_NEEDS_OWNER_YES", "an unchanged value asks again"
    changed = STARTS[start]("code-project", "guard_command", "echo another")
    assert _save(changed, saved_by=AGENT).get("code") == "WF_DEF_NEEDS_OWNER_YES"
    assert _save(changed, saved_by=AGENT, owner_allowed=True).get("ok") is True


@pytest.mark.parametrize("start", sorted(STARTS))
def test_a_value_that_reads_only_what_you_chose_hands_no_command(start):
    """The control: handing on the parent's own input is what its owner gives that run."""
    spec = {
        "inputs": {"cmd": {"type": "string", "required": False}},
        "root": _root(STARTS[start]("code-project", "guard_command", "{{inputs.cmd}}", task={})),
    }
    assert command_inputs.written_commands(None, spec) == []
