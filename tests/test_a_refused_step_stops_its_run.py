"""A step that refuses stops its run, and the run says why in the step's own words.

🔴 Before, for a step whose command printed its reason as JSON and exited non-zero (how the
bundled optimize-harness preflight refuses a run with no budget):

* the step's failure read "action failed", its reason left in the step's JSON output;
* a step declaring `on_error: fail_run` stopped what was scheduled after it and ended nothing, so
  the run ended "run deadlocked: no runnable nodes and none in flight";
* and without the declaration, a loop whose cycle ended in skipped steps (their producer had
  failed) never moved to its next cycle, and the run ended the same way.

Now the command's own reason is the step's failure, a step that declares its failure ends the run
ends it there with that reason ("… failed: <reason>, so nothing after it ran"), and a cycle the
frontier finished by skipping is read at its boundary like any other.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from personalclaw.workflows import handlers
from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio

#: The reason a step's command gives for refusing, in the JSON answer it prints.
REASON = (
    "this step refuses to start without a positive budget, since a search with no budget has no end"
)

LABEL = "Check what the run was given"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


@pytest.fixture(autouse=True)
def _providers() -> None:
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()


def _step(command: str, **config: Any) -> dict[str, Any]:
    return {
        "kind": "action",
        "id": "check",
        "label": LABEL,
        "config": {"provider": "bash", **config, "with": {"command": command, "timeout": 60}},
    }


#: The command prints its reason as one JSON object and exits 1, as the bundled steps refuse.
REFUSES = "printf '%s' '" + json.dumps({"ok": False, "error": REASON}) + "'; exit 1"


def _sequence(*children: dict[str, Any]) -> dict[str, Any]:
    return {"name": "refusal", "root": {"kind": "sequence", "id": "s", "children": list(children)}}


AFTER = {"kind": "transform", "id": "after", "label": "Carry on", "config": {"expr": "carried on"}}


async def _run(spec: dict[str, Any], **services: Any) -> tuple[RunController, RunStatus]:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    ctl = RunController(run, spec, services=EngineServices(**services))
    return ctl, await ctl.run_to_completion(timeout=60)


class _Recorder:
    """Stands in for a step's provider: a refused command is a list entry, never a command run."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, action_config: dict[str, Any], ctx: Any, timeout: int = 30) -> Any:
        from personalclaw.action_providers.base import ActionResult

        self.calls.append(dict(action_config))
        return ActionResult(success=True, stdout="done")


# ── the step's failure is its command's own reason ───────────────────────────


async def test_a_failed_steps_reason_is_the_one_its_command_gave() -> None:
    """🔴 Red before: "action failed", with the reason only in the step's output."""
    ctl, _status = await _run(_sequence(_step(REFUSES)))

    inst = ctl.instances["root.children[0]"]
    assert inst.state == InstanceState.FAILED
    assert inst.failure is not None and inst.failure.cause_plain == REASON
    assert "gave this reason itself" in inst.failure.remediation
    assert store.read_output(ctl.run.id, "root.children[0]") == {"ok": False, "error": REASON}


async def test_a_step_whose_command_says_nothing_is_named_by_its_exit_status() -> None:
    """🔴 Red before: "action failed"."""
    ctl, _status = await _run(_sequence(_step("exit 3")))

    failure = ctl.instances["root.children[0]"].failure
    assert failure is not None and failure.cause_plain == "the command exited with status 3"


async def test_a_step_that_did_not_declare_it_still_lets_the_run_continue_past_it() -> None:
    """CONTROL: `on_error: null_continue` is the default, and stays so. What follows runs, and the
    run ends failed saying it continued past the step, now in the step's own words."""
    ctl, status = await _run(_sequence(_step(REFUSES), AFTER))

    assert status == RunStatus.FAILED
    assert ctl.instances["root.children[1]"].state == InstanceState.DONE
    assert ctl.run.error_message == f"The run continued past “{LABEL}”, which failed: {REASON}."


# ── a step whose failure ends the run stops it there ─────────────────────────


async def test_a_step_that_declares_its_failure_ends_the_run_stops_it_and_says_why() -> None:
    """🔴 Red before: the step after it was never scheduled, nothing ended the run, and it ended
    "run deadlocked: no runnable nodes and none in flight"."""
    ctl, status = await _run(_sequence(_step(REFUSES, on_error="fail_run"), AFTER))

    assert status == RunStatus.FAILED
    assert ctl.run.error_message == f"“{LABEL}” failed: {REASON}, so nothing after it ran."
    after = ctl.instances["root.children[1]"]
    assert after.state == InstanceState.SKIPPED
    assert after.degraded_reason == f"not run: “{LABEL}” failed"
    started = {r["instance_path"] for r in J.ledger(ctl.run.id) if r["kind"] == J.STEP_STARTED}
    assert "root.children[1]" not in started, "a step after the refusal ran"


async def test_the_run_page_reads_the_refusal_where_it_reads_a_run() -> None:
    """The read the run page and `GET /api/workflows/runs/{id}` serve says it too: the run's
    error, and the step's row."""
    ctl, _status = await _run(_sequence(_step(REFUSES, on_error="fail_run"), AFTER))

    shown = handlers.shown_status(service.status(ctl.run.id))
    assert shown["status"] == RunStatus.FAILED.value
    assert REASON in shown["error"]
    rows = {row["node_id"]: row for row in shown["nodes"]}
    assert rows["check"]["failure"]["cause_plain"] == REASON
    assert rows["after"]["state"] == InstanceState.SKIPPED.value


async def test_the_way_forward_is_what_the_steps_own_reason_names() -> None:
    """The run page's way forward (`attention.remedy`) under a step that said why it stopped: its
    reason names what to change, and that is as often what the run was started with (a budget of
    0) as the step. 🔴 Red before: "fails the same way until the step changes: change the step that
    gave up", under a reason that named the run's own input."""
    ctl, _status = await _run(_sequence(_step(REFUSES, on_error="fail_run"), AFTER))

    remedy = handlers.shown_status(service.status(ctl.run.id))["attention"]["remedy"]
    assert "what the run was started with" in remedy, remedy
    assert "change the step that gave up" not in remedy, remedy


async def test_a_step_that_gave_no_reason_is_still_the_step_to_change() -> None:
    """CONTROL: a command that said nothing of its own keeps the step's way forward."""
    ctl, _status = await _run(_sequence(_step("exit 3", on_error="fail_run"), AFTER))

    remedy = handlers.shown_status(service.status(ctl.run.id))["attention"]["remedy"]
    assert "change the step that gave up" in remedy, remedy


async def test_a_step_a_control_refused_stops_the_run_whatever_it_declares() -> None:
    """The action denylist refuses a step that would stop PersonalClaw before its provider runs.
    A control's refusal is not a failure a step's `on_error` walks past: the run stops there, in
    the denylist's own words, and nothing after it runs. 🔴 Red before: the run went on past the
    refusal as past any failure, and ran the step after it. The command never reaches a shell
    either way: the provider is a recorder."""
    stop = {
        "kind": "action",
        "id": "stop",
        "label": "Stop for the night",
        "config": {
            "provider": "bash",
            "on_error": "null_continue",
            "with": {"command": "personalclaw stop"},
        },
    }
    recorder = _Recorder()
    ctl, status = await _run(_sequence(stop, AFTER), get_provider=lambda name: recorder)

    assert recorder.calls == [], "the refused command reached its provider"
    assert status == RunStatus.FAILED
    ending = ctl.run.error_message
    assert ending.startswith(
        "“Stop for the night” failed: blocked by the guardrails denylist: self_destruct:stop — "
    ), ending
    assert ending.endswith(", so nothing after it ran."), ending
    assert ctl.instances["root.children[1]"].state == InstanceState.SKIPPED


# ── a loop cycle whose last steps were skipped is read at its boundary ───────


def _loop(condition: str) -> dict[str, Any]:
    """A loop of at most two cycles whose first step refuses, and whose second reads it."""
    return {
        "kind": "loop",
        "id": "search",
        "label": "Keep trying",
        "config": {"mode": "until", "condition": condition, "max_iterations": 2},
        "body": {
            "kind": "sequence",
            "id": "cycle",
            "children": [
                _step(REFUSES),
                {"kind": "transform", "id": "use", "config": {"expr": "{{nodes.check.output}}"}},
            ],
        },
    }


def _cycles(ctl: RunController) -> list[dict[str, Any]]:
    return [r for r in J.ledger(ctl.run.id) if r["kind"] == J.ITERATION]


async def test_a_loop_whose_cycles_end_in_skipped_steps_runs_to_its_budget() -> None:
    """Each cycle's first step fails and the step reading it is skipped, so no settle reads the
    cycle's end. 🔴 Red before: the loop waited on its first cycle forever and the run ended
    "run deadlocked". Now each cycle is read at its boundary, the loop runs to its budget, and it
    is handed to a person with the step that failed as the reason."""
    ctl, status = await _run(_sequence(_loop("{{last.output.done | default(false)}}")))

    assert status == RunStatus.ESCALATED, ctl.run.error_message
    assert "deadlocked" not in ctl.run.error_message
    assert "2 of 2 cycles failed" in ctl.run.error_message
    assert f"“{LABEL}” failed: {REASON}" in ctl.run.error_message
    assert len(_cycles(ctl)) == 2
    assert ctl.instances["root.children[0].body@1.children[1]"].state == InstanceState.SKIPPED


async def test_a_loop_that_cannot_read_its_exit_condition_is_not_done() -> None:
    """Its condition reads the failed step's output, so it cannot be read. The loop stops, since it
    cannot tell whether it is done, and is handed to a person naming the step that failed; DONE
    would hand the steps after it its output as a result. 🔴 Red before: "run deadlocked"."""
    ctl, status = await _run(_sequence(_loop("{{last.output.done}}"), AFTER))

    assert status == RunStatus.ESCALATED, ctl.run.error_message
    assert f"“{LABEL}” failed: {REASON}" in ctl.run.error_message
    assert ctl.instances["root.children[0]"].state == InstanceState.ESCALATED
    assert ctl.instances["root.children[0]"].output_ref == ""
    assert [r["outcome"] for r in _cycles(ctl)] == ["condition_unresolvable"]
