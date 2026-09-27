"""Abstract base for task providers."""

from abc import ABC, abstractmethod
from typing import Any

from personalclaw.tasks.models import Task, TaskComment


class StaleTaskWrite(Exception):
    """A write named a base revision the stored task no longer has, so nothing was written.

    Carries the task as it was stored at the moment of the comparison, so the caller can answer
    with the refusal every document write uses (``stale_write.stale_write_refusal``) against
    exactly the copy the store compared.
    """

    def __init__(self, task: Task) -> None:
        super().__init__(f"task {task.id} changed since the write's base was read")
        self.task = task


class TaskProvider(ABC):
    """Provider interface for task backends.

    Each provider surfaces tasks from a single source (filesystem, external
    API, etc.). The aggregation layer queries all registered providers and
    merges results.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier (e.g. 'native', 'project')."""
        ...

    @abstractmethod
    async def list_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
        project: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        """Return (tasks, total_count) with optional filters."""
        ...

    @abstractmethod
    async def get_task(self, task_id: str) -> Task | None: ...

    @abstractmethod
    async def create_task(self, **fields: Any) -> Task: ...

    @abstractmethod
    async def update_task(
        self, task_id: str, *, base_revision: str | None = None, **fields: Any
    ) -> Task | None:
        """Apply ``fields`` to the task; ``None`` when there is no such task.

        ``base_revision`` is the revision a whole-form save was built from (``If-Match``):
        when it is given, the write lands only if the stored task still has it
        (``models.task_revision``) — compared and written as ONE step, so no other writer can
        land in between — and otherwise raises :class:`StaleTaskWrite` having written
        nothing. ``None`` is a write that replaces no copy of the task (a status change, a
        server-side writer) and is applied to what is stored.
        """
        ...

    @abstractmethod
    async def delete_task(self, task_id: str) -> bool: ...

    async def get_comments(self, task_id: str) -> list[TaskComment]:
        return []

    async def add_comment(self, task_id: str, body: str, author: str = "") -> TaskComment | None:
        return None

    async def delete_comment(self, task_id: str, comment_id: str) -> bool:
        """Remove one comment; False when there is nothing to remove.

        Non-abstract like its comment siblings: a provider whose backend has no comment
        concept inherits "nothing to delete" rather than being forced to stub it.
        """
        return False

    @property
    def readonly(self) -> bool:
        return False
