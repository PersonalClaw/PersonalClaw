"""A checklist element that is neither text nor an object is refused, not stored as an empty item.

An exit criterion is text, or an object with its text in ``description``; an action-plan step is
text, or an object with its text in ``content``. Anything else a caller put in either list (a
number, a boolean, null, a list) was stored as an EMPTY criterion: it had no words to show, no
update could meet it, and its task could never close. A write now refuses it and says where it
is and what to send; a read drops it. A lone value of that kind sent as the whole list was read
as no criteria at all, so ``exit_criteria: true`` on an update wiped the checklist and the task
closed with nothing met. A write refuses that too.

And ``task_update`` told the model to "Complete the exit criteria before marking the task done"
after EVERY refusal, a malformed label or an unfinished prerequisite included. That remedy now
rides only the refusal it is true of.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.tasks import registry
from personalclaw.tasks.models import Task, coerce_task_field
from personalclaw.tasks.native import NativeTaskProvider

CRITERION_HELP = "send each criterion as text, or as an object with its text in 'description'"
STEP_HELP = "send each step as text, or as an object with its text in 'content'"
GATE_HINT = "Complete the exit criteria before marking the task done."


@pytest.fixture()
def provider(tmp_path):
    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path):
        yield NativeTaskProvider()


@pytest.fixture
def tools(tmp_path):
    registry._providers.clear()
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "home"
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=home),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=home),
    ):
        yield NativeBuiltinToolProvider(ws)
    registry._providers.clear()


def _stored(home: Path, task_id: str) -> dict:
    return json.loads((home / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))


def _created(output: str) -> str:
    return output.split("created task ")[1].split(":")[0]


class TestAWriteRefusesIt:
    @pytest.mark.parametrize(
        "element, kind",
        [(5, "a number"), (1.5, "a number"), (True, "a boolean"), (None, "null")]
        + [(["tests pass"], "a list")],
    )
    def test_a_criterion_is_refused_saying_where_it_is(self, element, kind):
        with pytest.raises(ValueError) as refused:
            coerce_task_field("exit_criteria", ["tests pass", element])
        assert str(refused.value) == f"exit_criteria item 2 is {kind}: {CRITERION_HELP}"

    def test_a_step_is_refused_the_same_way(self):
        with pytest.raises(ValueError) as refused:
            coerce_task_field("action_plan", [3, "build"])
        assert str(refused.value) == f"action_plan item 1 is a number: {STEP_HELP}"

    @pytest.mark.parametrize(
        "sent, kind", [(True, "a boolean"), (False, "a boolean"), (7, "a number")]
    )
    def test_a_lone_value_is_refused_rather_than_read_as_no_criteria(self, sent, kind):
        with pytest.raises(ValueError) as refused:
            coerce_task_field("exit_criteria", sent)
        assert str(refused.value) == f"exit_criteria is {kind}, not a list: {CRITERION_HELP}"

    def test_ordinary_criteria_and_steps_still_save(self):
        criteria = coerce_task_field(
            "exit_criteria", ["tests pass", {"description": "docs updated", "met": True}]
        )
        assert [(c["description"], c["met"]) for c in criteria] == [
            ("tests pass", False),
            ("docs updated", True),
        ]
        steps = coerce_task_field("action_plan", ["build", {"content": "upload"}])
        assert [s["content"] for s in steps] == ["build", "upload"]
        assert coerce_task_field("exit_criteria", None) == []
        assert coerce_task_field("exit_criteria", []) == []


class TestAReadDropsIt:
    def test_a_stored_element_that_is_no_criterion_does_not_hold_its_task_open(self):
        task = Task.from_dict(
            {
                "id": "t1",
                "title": "T",
                "exit_criteria": [{"description": "tests pass", "met": True}, 5, None],
                "action_plan": [7, "build"],
            }
        )
        assert [c["description"] for c in task.exit_criteria] == ["tests pass"]
        assert task.can_mark_complete()
        assert [(s["sequence"], s["content"]) for s in task.action_plan] == [(0, "build")]

    def test_a_lone_value_still_reads_as_no_criteria(self):
        assert coerce_task_field("exit_criteria", True, strict=False) == []


class TestTheStore:
    @pytest.mark.asyncio
    async def test_create_refuses_it_and_files_nothing(self, provider, tmp_path):
        with pytest.raises(ValueError, match="exit_criteria item 2 is a number"):
            await provider.create_task(title="t", exit_criteria=["tests pass", 5])
        assert list((tmp_path / "tasks").glob("t-*.json")) == []

    @pytest.mark.asyncio
    async def test_a_lone_true_on_update_does_not_wipe_the_checklist(self, provider, tmp_path):
        created = await provider.create_task(title="t", exit_criteria=["review copy"])
        with pytest.raises(ValueError, match="exit_criteria is a boolean, not a list"):
            await provider.update_task(created.id, exit_criteria=True, status="done")
        stored = _stored(tmp_path, created.id)
        assert stored["status"] == "open"
        assert [c["description"] for c in stored["exit_criteria"]] == ["review copy"]


class TestTheTool:
    @pytest.mark.asyncio
    async def test_task_update_refuses_it_and_keeps_the_checklist(self, tools):
        made = await tools.invoke(
            "task_create", {"title": "Publish", "exit_criteria": ["Proofread"]}
        )
        tid = _created(made.output)
        bad = await tools.invoke("task_update", {"id": tid, "exit_criteria": ["Proofread", 5]})
        assert not bad.success
        assert bad.error == f"exit_criteria item 2 is a number: {CRITERION_HELP}"
        assert GATE_HINT not in (bad.recovery_hints or [])
        task = await registry.get_task(tid)
        assert [c["description"] for c in task.exit_criteria] == ["Proofread"]

    @pytest.mark.asyncio
    async def test_task_create_refuses_it_and_files_nothing(self, tools):
        bad = await tools.invoke(
            "task_create", {"title": "Publish", "exit_criteria": ["Proofread", None]}
        )
        assert not bad.success
        assert bad.error == f"exit_criteria item 2 is null: {CRITERION_HELP}"
        listed = await tools.invoke("task_list", {})
        assert "Publish" not in listed.output

    @pytest.mark.asyncio
    async def test_the_exit_criteria_remedy_rides_only_its_own_refusal(self, tools):
        made = await tools.invoke(
            "task_create", {"title": "Publish", "exit_criteria": ["Proofread"]}
        )
        tid = _created(made.output)
        label = await tools.invoke("task_update", {"id": tid, "labels": [{"a": 1}]})
        assert not label.success and GATE_HINT not in (label.recovery_hints or [])

        first = _created((await tools.invoke("task_create", {"title": "Draft"})).output)
        then = _created(
            (await tools.invoke("task_create", {"title": "Send", "depends_on": [first]})).output
        )
        waiting = await tools.invoke("task_update", {"id": then, "status": "done"})
        assert not waiting.success and "waiting on unfinished prerequisite" in waiting.error
        assert GATE_HINT not in (waiting.recovery_hints or [])

        gated = await tools.invoke("task_update", {"id": tid, "status": "done"})
        assert not gated.success and "unfinished exit criteria — Proofread" in gated.error
        assert gated.recovery_hints == [GATE_HINT]
