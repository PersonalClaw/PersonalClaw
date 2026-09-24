"""A genui FORM widget emitted by a workflow gate resolves the gate and the run ADVANCES.

AMBIENT-SURFACES §5.4 / atom AS-6, second routing case: *"workflow-emitted widgets (a skill
or workflow node emits a genui tree as a gate's prompt) → the action resolves the run's
wait/gate node through the resume-target path — closing the loop from generated UI back into
execution."*

🪤 THE TEST THIS ATOM COULD HAVE SHIPPED INSTEAD. A test that asserts "the resolver was
called" — or that calls `run_to_completion` again by hand after resuming — stays GREEN while
the run never moves. `controller.resume`'s own comment records that exact defect: the answer
landed, the node flipped DONE, and the run "sat there forever with its downstream nodes never
launched", because every unit test drove the loop itself afterwards. So every test here
resumes and then WAITS for the run's own loop, and asserts the POST-GATE node produced its
output. Nothing in this file drives the engine after the answer.

The gate's prompt carries the widget markup the FE renders (`<widget kind="genui">`), which
is why the prompt is asserted to survive the round trip intact: the FE detects the block in
`ask.prompt` (`findGenUiBlock`) and, when it is there, renders the tree with THIS gate
declared as the action producer. A prompt the engine rewrote would leave the FE rendering the
tree as text.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.workflows import human_input as HI
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


#: The genui tree a gate node authors as its prompt. `Form(...)`'s submit is the action.
WIDGET = (
    'Log the expense.\n\n<widget kind="genui" title="Expense">\n'
    'f = Form(title: "Log an expense", fields: ["amount", "vendor"], '
    'action: "log_expense", submit: "Log expense")\n</widget>'
)


def _spec() -> dict:
    """A form gate followed by a node that can only run AFTER the gate is answered.

    The trailing `after` node is the whole instrument: its output is the difference between
    "the gate was answered" and "the run advanced".
    """
    return {
        "name": "expense",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "gate",
                    "id": "ask",
                    "config": {
                        # The GATE kind is `approval` (the only kind that parks for a human);
                        # the ASK kind is a separate field the engine reads as `ask_kind`
                        # (`engine._ask_payload`). Writing `kind: "form"` here instead fails the
                        # node with "unknown gate kind" and raises NO continuation at all — which
                        # is how this test first failed.
                        "kind": "approval",
                        "ask_kind": "form",
                        "prompt": WIDGET,
                        "fields": [{"name": "amount"}, {"name": "vendor"}],
                        "timeout_secs": 0,
                    },
                },
                {"kind": "transform", "id": "after", "config": {"expr": {"logged": True}}},
            ],
        },
    }


async def _parked() -> tuple[RunController, str]:
    """A real run parked on a real form gate, with a real spec on disk."""
    spec = _spec()
    run = store.create(WorkflowRun(id="", workflow_name="expense", mode="background"))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    await controller.run_to_completion(timeout=20)
    return controller, HI.list_continuations(run.id)[0].token


async def _settled(controller: RunController, timeout: float = 20.0) -> RunStatus:
    """Wait for the run's OWN loop to finish. Deliberately not `run_to_completion`: driving
    the engine here is what would hide a resume that never restarted the loop."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if controller.run.status in (RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.CANCELLED):
            return controller.run.status
        await asyncio.sleep(0.05)
    return controller.run.status


class TestTheGateIsReallyWaiting:
    """The precondition. Without it, a passing "the run advanced" is unfalsifiable — a run
    that never blocked would advance on its own."""

    async def test_the_run_parks_and_the_post_gate_node_has_not_run(self) -> None:
        controller, token = await _parked()
        assert token, "the gate must have raised a continuation to answer"
        assert controller.run.status == RunStatus.NEEDS_INPUT
        assert "after" not in controller._outputs

    async def test_the_widget_markup_survives_into_the_ask_the_frontend_reads(self) -> None:
        """The FE detects the genui block in `ask.prompt`. An engine that rewrote or dropped
        the prompt would leave the widget rendering as text with no producer."""
        controller, token = await _parked()
        cont = HI.load_continuation(controller.run.id, token)
        assert cont is not None
        assert '<widget kind="genui"' in cont.ask.get("prompt", "")
        assert 'action: "log_expense"' in cont.ask["prompt"]


class TestTheRunAdvances:
    async def test_a_form_submission_resolves_the_gate_and_the_run_advances(self) -> None:
        """The atom's clause, end to end: the widget's payload IS the gate answer, and the
        node after the gate produces its output without anybody driving the engine."""
        controller, token = await _parked()
        result = controller.resume(token, {"amount": "12.40", "vendor": "Acme"})
        assert result["ok"] is True and result["approved"] is True

        status = await _settled(controller)
        assert status == RunStatus.COMPLETE, "the run must reach the end, not merely unblock"
        assert controller._outputs.get("after") == {"logged": True}, (
            "the POST-GATE node's output is the proof the run moved; a resolver that only "
            "marked the gate DONE would leave this missing"
        )

    async def test_the_submitted_values_are_what_the_gate_recorded(self) -> None:
        """A form whose values are dropped on the way in would advance the run with an empty
        answer — the run moves, and the user's input is gone."""
        controller, token = await _parked()
        controller.resume(token, {"amount": "12.40", "vendor": "Acme"})
        await _settled(controller)
        assert controller._outputs.get("ask", {}).get("answer") == {
            "amount": "12.40",
            "vendor": "Acme",
        }

    async def test_the_token_is_single_use(self) -> None:
        """A double-clicked submit must not replay one answer into two resolutions."""
        controller, token = await _parked()
        controller.resume(token, {"amount": "1", "vendor": "A"})
        again = controller.resume(token, {"amount": "1", "vendor": "A"})
        assert again["ok"] is False
        assert again["code"] in ("WF_RESUME_UNKNOWN_TOKEN", "WF_RESUME_ALREADY_USED")

    async def test_an_unknown_token_does_not_advance_the_run(self) -> None:
        """The falsification leg for the clause above: with a bad token the SAME assertions
        must fail, so "the run advanced" is evidence about the answer and not about time."""
        controller, _token = await _parked()
        refused = controller.resume("0" * 32, {"amount": "1", "vendor": "A"})
        assert refused["ok"] is False
        await asyncio.sleep(0.3)
        assert controller.run.status == RunStatus.NEEDS_INPUT
        assert "after" not in controller._outputs
