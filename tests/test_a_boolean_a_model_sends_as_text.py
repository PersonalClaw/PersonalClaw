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

The spellings, and what ``yes_or_no`` makes of each, are rows of the one table in
``test_safety_flags_reject_truthy_strings``. Each field here is read through the code that reads it
three times, once per answer, each a row of one of the three tests below; the journeys after them
drive the task tools and the loop end to end.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest
from table_rows import row
from test_safety_flags_reject_truthy_strings import WORD_NEITHER, WORD_NO, WORD_YES

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.tasks import registry
from personalclaw.tasks.models import (
    Task,
    coerce_task_field,
    normalize_action_plan_item,
    normalize_exit_criterion,
)

pytestmark = pytest.mark.security


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


# ── the fields, each through the code that reads it ──────────────────────────────────────────


async def _criterion(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> bool:
    """Whether an exit criterion sent *sent* is met, where it is written and where it is read."""
    criterion = normalize_exit_criterion({"description": CRITERION, "met": sent})
    met = criterion["met"]
    assert criterion["status"] == ("complete" if met else "incomplete")
    [written] = coerce_task_field("exit_criteria", [{"description": CRITERION, "met": sent}])
    assert written["met"] is met, "the write and the read disagree"
    task = Task.from_dict(
        {"id": "t1", "title": "T", "exit_criteria": [{"description": CRITERION, "met": sent}]}
    )
    assert task.can_mark_complete() is met
    assert task.incomplete_exit_criteria() == ([] if met else [CRITERION])
    return met


async def _plan_step(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> bool:
    return normalize_action_plan_item({"content": "build", "completed": sent}, 0)["completed"]


async def _repeatable(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> bool:
    """Whether ``task_list_create`` filed the list under Repeatable."""
    from personalclaw.tasks.hierarchy import HierarchyStore

    home = scratch / "home"
    (scratch / "ws").mkdir()
    mp.setattr("personalclaw.tasks.native.config_dir", lambda: home)
    mp.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: home)
    registry._providers.clear()
    try:
        made = await NativeBuiltinToolProvider(scratch / "ws").invoke(
            "task_list_create", {"name": "Weekly review", "repeatable": sent}
        )
        assert made.success, made.error
        project = HierarchyStore().get_project(made.output.split("project_id=")[1].rstrip(")"))
        assert project is not None
        return project.name == "Repeatable"
    finally:
        registry._providers.clear()


def _attended(kind: str):
    async def drive(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> bool:
        """Whether the run ``project_run_create`` made is attended."""
        from personalclaw.agents.native import sdlc_tools
        from personalclaw.loop import store

        mp.setattr("personalclaw.loop.files.config_dir", lambda: scratch)
        made = await sdlc_tools.project_create(
            {"kind": kind, "task": "Add a health endpoint to the order service", "attended": sent}
        )
        assert made.success, made.error
        [loop] = store.list_all()
        return loop.attended

    return drive


class Field(NamedTuple):
    """A boolean a model sends, how it is read, and what each answer reads as."""

    name: str
    read: Callable[[Path, pytest.MonkeyPatch, object], Awaitable[bool]]
    yes: bool
    no: bool
    neither: bool


#: Every field the defect reached. What spells neither is the field's safe value: not met, not
#: finished, not repeatable, attended.
FIELDS = [
    Field("task exit_criteria[].met", _criterion, True, False, False),
    Field("task action_plan[].completed", _plan_step, True, False, False),
    Field("task_list_create.repeatable", _repeatable, True, False, False),
    Field("project_run_create.attended, code", _attended("code"), True, False, True),
    Field("project_run_create.attended, goal", _attended("goal"), True, False, True),
]


async def _answers(subtests, tmp_path: Path, answer: str) -> None:
    """Each field sent the word for *answer* and, for a yes or a no, the real boolean too."""
    assert len(FIELDS) == 5, "a field was dropped from the table"
    assert len(FIELDS) == len({field.name for field in FIELDS}), "two fields share a name"
    sent = {"yes": (WORD_YES, True), "no": (WORD_NO, False), "neither": WORD_NEITHER}[answer]
    for at, field in enumerate(FIELDS):
        for value in sent:
            with row(subtests, field=field.name, sent=repr(value)):
                scratch = tmp_path / f"field-{at}-{value!r}"
                scratch.mkdir()
                with pytest.MonkeyPatch.context() as mp:
                    got = await field.read(scratch, mp, value)
                expected = getattr(field, answer)
                assert (
                    got is expected
                ), f"{field.name} sent {value!r} read {got!r}, not {expected!r}"


@pytest.mark.asyncio
async def test_every_field_a_model_sends_reads_a_yes_as_yes(subtests, tmp_path):
    await _answers(subtests, tmp_path, "yes")


@pytest.mark.asyncio
async def test_every_field_a_model_sends_reads_a_no_as_no(subtests, tmp_path):
    await _answers(subtests, tmp_path, "no")


@pytest.mark.asyncio
async def test_every_field_a_model_sends_reads_what_spells_neither_as_its_safe_value(
    subtests, tmp_path
):
    await _answers(subtests, tmp_path, "neither")


# ── a task does not close on a criterion sent as "false" ─────────────────────────────────────


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


class TestARunTheAgentCreates:
    @pytest.fixture(autouse=True)
    def _loops_home(self, monkeypatch, tmp_path):
        monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)

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
