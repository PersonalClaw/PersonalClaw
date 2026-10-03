"""A boolean a model sends as text is read as the word it spells, never by Python's truthiness.

The task and loop tools declare their flags boolean, and a model sends one as text as often as
not. Each was read with ``bool()`` or an identity check, and ``bool("false")`` is True:

* an exit criterion sent as ``"met": "false"`` was stored as met, so its task closed before its
  work was done, and so did the loop that lands a task's work once the task is done;
* an action-plan step sent as ``"completed": "false"`` was stored as finished;
* ``task_list_create`` with ``"repeatable": "false"`` filed the list under Repeatable;
* ``project_run_create`` with ``"attended": "false"`` made an attended run when the agent had
  said the run goes unattended.

Only a real boolean, or a word that spells one (``safety_flags.yes_or_no``), decides. Anything
else (a blank, an unrecognised word, a number) is the field's safe value: not met, not finished,
not repeatable, attended.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.tasks import registry
from personalclaw.tasks.models import (
    Task,
    coerce_task_field,
    normalize_action_plan_item,
    normalize_exit_criterion,
)

#: Each way a caller spells yes, and no. A real boolean included: it must still pass through.
YES = [True, "true", "TRUE", " yes ", "1", "on"]
NO = [False, "false", "False", " no ", "0", "off"]
#: What spells neither, so each field reads it as its safe value. A number is a type confused
#: with a boolean, and a blank says nothing.
NEITHER = ["", "   ", "maybe", "met", 1, 0, 1.5, None, [], {"met": True}]

CRITERION = "Proofread by a second reader"


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


def _created(output: str) -> str:
    return output.split("created task ")[1].split(":")[0]


class TestTheReading:
    @pytest.mark.parametrize("value", YES)
    def test_a_word_for_yes_is_yes(self, value):
        from personalclaw.safety_flags import yes_or_no

        assert yes_or_no(value) is True

    @pytest.mark.parametrize("value", NO)
    def test_a_word_for_no_is_no(self, value):
        from personalclaw.safety_flags import yes_or_no

        assert yes_or_no(value) is False

    @pytest.mark.parametrize("value", NEITHER)
    def test_anything_else_is_neither(self, value):
        from personalclaw.safety_flags import yes_or_no

        assert yes_or_no(value) is None


class TestAnExitCriterion:
    @pytest.mark.parametrize("sent", YES)
    def test_a_yes_is_met(self, sent):
        criterion = normalize_exit_criterion({"description": CRITERION, "met": sent})
        assert (criterion["status"], criterion["met"]) == ("complete", True)

    @pytest.mark.parametrize("sent", NO + NEITHER)
    def test_anything_else_is_not_met(self, sent):
        criterion = normalize_exit_criterion({"description": CRITERION, "met": sent})
        assert (criterion["status"], criterion["met"]) == ("incomplete", False)

    @pytest.mark.parametrize("sent", NO + NEITHER)
    def test_the_write_and_the_read_agree(self, sent):
        [written] = coerce_task_field("exit_criteria", [{"description": CRITERION, "met": sent}])
        assert written["met"] is False
        task = Task.from_dict(
            {"id": "t1", "title": "T", "exit_criteria": [{"description": CRITERION, "met": sent}]}
        )
        assert task.can_mark_complete() is False
        assert task.incomplete_exit_criteria() == [CRITERION]


class TestAnActionPlanStep:
    @pytest.mark.parametrize("sent", YES)
    def test_a_yes_is_finished(self, sent):
        assert normalize_action_plan_item({"content": "build", "completed": sent}, 0)["completed"]

    @pytest.mark.parametrize("sent", NO + NEITHER)
    def test_anything_else_is_not(self, sent):
        step = normalize_action_plan_item({"content": "build", "completed": sent}, 0)
        assert step["completed"] is False


class TestATaskDoesNotCloseOnMetSentAsFalse:
    @pytest.mark.asyncio
    async def test_through_the_task_tools(self, tools):
        made = await tools.invoke(
            "task_create",
            {"title": "Publish the newsletter", "exit_criteria": [{"description": CRITERION}]},
        )
        tid = _created(made.output)
        sent = await tools.invoke(
            "task_update",
            {"id": tid, "exit_criteria": [{"description": CRITERION, "met": "false"}]},
        )
        assert sent.success and "0/1 criteria" in sent.output
        refused = await tools.invoke("task_update", {"id": tid, "status": "done"})
        assert not refused.success
        assert f"unfinished exit criteria — {CRITERION}" in refused.error
        # In one call, too: the criteria a write sends are judged as it leaves them.
        both = await tools.invoke(
            "task_update",
            {
                "id": tid,
                "exit_criteria": [{"description": CRITERION, "met": "false"}],
                "status": "done",
            },
        )
        assert not both.success and CRITERION in both.error
        assert (await registry.get_task(tid)).status.value == "open"
        # A yes sent as text is honoured.
        done = await tools.invoke(
            "task_update",
            {
                "id": tid,
                "exit_criteria": [{"description": CRITERION, "met": "true"}],
                "status": "done",
            },
        )
        assert done.success and "[done]" in done.output and "1/1 criteria" in done.output

    @pytest.mark.asyncio
    async def test_the_loop_cannot_close_it_either(self, tools):
        """A loop's scheduler closes a task whose worker wrote its finding, then lands its work
        (``tasks_link.mark_task_done``); that close goes through the same gate."""
        from personalclaw.loop import tasks_link

        made = await tools.invoke(
            "task_create",
            {"title": "Publish the newsletter", "exit_criteria": [{"description": CRITERION}]},
        )
        tid = _created(made.output)
        await tools.invoke(
            "task_update",
            {"id": tid, "exit_criteria": [{"description": CRITERION, "met": "false"}]},
        )
        assert await tasks_link.mark_task_done(tid) is False
        assert (await registry.get_task(tid)).status.value == "open"


class TestATaskListMarkedRepeatable:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "sent, repeatable",
        [(True, True), ("true", True), ("yes", True), (False, False), ("false", False)]
        + [("no", False), ("0", False), ("maybe", False), (1, False)],
    )
    async def test_it_is_filed_under_repeatable_only_on_a_yes(self, tools, sent, repeatable):
        from personalclaw.tasks.hierarchy import HierarchyStore

        made = await tools.invoke("task_list_create", {"name": "Weekly review", "repeatable": sent})
        assert made.success, made.error
        project_id = made.output.split("project_id=")[1].rstrip(")")
        project = HierarchyStore().get_project(project_id)
        assert project is not None
        assert (project.name == "Repeatable") is repeatable


class TestARunTheAgentCreates:
    @pytest.fixture(autouse=True)
    def _loops_home(self, monkeypatch, tmp_path):
        monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)

    @pytest.mark.parametrize("kind", ["code", "goal"])
    @pytest.mark.parametrize(
        "sent, attended",
        [(False, False), ("false", False), ("no", False), ("0", False), ("off", False)]
        + [(True, True), ("true", True), ("", True), ("maybe", True), (0, True)],
    )
    def test_it_goes_unattended_only_on_a_no(self, kind, sent, attended):
        from personalclaw.agents.native import sdlc_tools
        from personalclaw.loop import store

        made = asyncio.run(
            sdlc_tools.project_create(
                {
                    "kind": kind,
                    "task": "Add a health endpoint to the order service",
                    "attended": sent,
                }
            )
        )
        assert made.success, made.error
        [loop] = store.list_all()
        assert loop.attended is attended

    @pytest.mark.parametrize("kind", ["code", "goal"])
    def test_a_run_nobody_said_was_unattended_asks(self, kind):
        from personalclaw.agents.native import sdlc_tools
        from personalclaw.loop import store

        made = asyncio.run(
            sdlc_tools.project_create(
                {"kind": kind, "task": "Add a health endpoint to the order service"}
            )
        )
        assert made.success, made.error
        [loop] = store.list_all()
        assert loop.attended is True
