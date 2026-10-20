"""A task that is created, edited, commented on or deleted says so to every open dashboard.

The surfaces that list tasks (the phone companion's Tasks lane, Home's ready list) re-read on the
gateway's `tasks` refresh hint. Nothing sent one, so a task finished by the agent's `task_update`,
by a loop or from another device stayed open on every page left open until a reload. Every door
writes a task through the registry, whichever provider holds it, so the registry's writes send the
hint, as the loop store's writes send `loops`: one seam, every caller. A write that is refused, or
that finds nothing to change, changed nothing and says nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from personalclaw.tasks import registry
from personalclaw.tasks.models import Task
from personalclaw.tasks.provider import TaskProvider


class _Dashboard:
    def __init__(self) -> None:
        self.hints: list[tuple[str, ...]] = []

    def push_refresh(self, *kinds: str) -> None:
        self.hints.append(kinds)


@pytest.fixture
def dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Dashboard]:
    from personalclaw.inbox_providers import native_source

    board = _Dashboard()
    monkeypatch.setattr(native_source, "_dashboard_state", board, raising=False)
    # A registry of this test's own, so the native provider it registers writes into tmp_path.
    monkeypatch.setattr(registry, "_providers", {})
    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path):
        yield board


@pytest.mark.asyncio
async def test_creating_a_task_is_announced(dashboard):
    await registry.create_task(title="Fix the feed parser")
    assert dashboard.hints == [("tasks",)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edit",
    [{"status": "done"}, {"status": "in_progress"}, {"title": "Fix the feed parser, then tag"}],
    ids=["finished", "started", "renamed"],
)
async def test_every_edit_is_announced(dashboard, edit):
    task = await registry.create_task(title="Fix the feed parser")
    dashboard.hints.clear()
    assert await registry.update_task(task.id, **edit) is not None
    assert dashboard.hints == [("tasks",)]


@pytest.mark.asyncio
async def test_an_edit_that_changes_nothing_says_nothing(dashboard):
    task = await registry.create_task(title="Fix the feed parser")
    dashboard.hints.clear()
    # Not a status: refused, and nothing is written.
    with pytest.raises(ValueError):
        await registry.update_task(task.id, status="finished")
    # No such task.
    assert await registry.update_task("t-00000000", status="done") is None
    assert dashboard.hints == []


@pytest.mark.asyncio
async def test_deleting_a_task_is_announced_and_a_missing_one_is_not(dashboard):
    task = await registry.create_task(title="Fix the feed parser")
    dashboard.hints.clear()
    assert await registry.delete_task(task.id) is True
    assert dashboard.hints == [("tasks",)]
    assert await registry.delete_task(task.id) is False
    assert dashboard.hints == [("tasks",)], "nothing changed, so nothing is announced"


@pytest.mark.asyncio
async def test_a_comment_is_announced_because_a_row_counts_them(dashboard):
    task = await registry.create_task(title="Fix the feed parser")
    dashboard.hints.clear()
    comment = await registry.add_comment(task.id, "The parser trips on an empty feed.")
    assert comment is not None
    assert dashboard.hints == [("tasks",)]
    assert await registry.delete_comment(task.id, comment.id) is True
    assert dashboard.hints == [("tasks",), ("tasks",)]
    assert await registry.delete_comment(task.id, comment.id) is False
    assert await registry.add_comment("t-00000000", "on nothing") is None
    assert dashboard.hints == [("tasks",), ("tasks",)], "nothing changed, so nothing is announced"


class _AppTasks(TaskProvider):
    """A provider an app registers: it keeps its tasks itself, and core writes them through it."""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}

    @property
    def name(self) -> str:
        return "example-tracker"

    async def list_tasks(self, status=None, assignee=None, project=None, limit=50, offset=0):
        rows = list(self.tasks.values())
        return rows[offset : offset + limit], len(rows)

    async def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def create_task(self, **fields: Any) -> Task:
        task = Task(id=f"ex-{len(self.tasks) + 1}", title=fields.get("title", ""))
        self.tasks[task.id] = task
        return task

    async def update_task(self, task_id: str, *, base_revision=None, **fields: Any) -> Task | None:
        task = self.tasks.get(task_id)
        if task is not None and "title" in fields:
            task.title = fields["title"]
        return task

    async def delete_task(self, task_id: str) -> bool:
        return self.tasks.pop(task_id, None) is not None


@pytest.mark.asyncio
async def test_a_task_an_app_holds_is_announced_too(dashboard):
    registry.register_provider(_AppTasks())
    task = await registry.create_task("example-tracker", title="Reply to the vendor")
    await registry.update_task(task.id, "example-tracker", title="Reply to the vendor today")
    assert await registry.delete_task(task.id, "example-tracker") is True
    assert dashboard.hints == [("tasks",)] * 3


@pytest.mark.asyncio
async def test_a_headless_registry_still_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """No dashboard (a CLI run, a test): the hint has nobody to reach, and the write still lands."""
    from personalclaw.inbox_providers import native_source

    monkeypatch.setattr(native_source, "_dashboard_state", None, raising=False)
    monkeypatch.setattr(registry, "_providers", {})
    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path):
        task = await registry.create_task(title="Fix the feed parser")
        assert (await registry.update_task(task.id, status="done")) is not None
        done = await registry.get_task(task.id)
    assert done is not None and done.status.value == "done"
