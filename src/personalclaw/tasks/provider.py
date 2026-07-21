"""Abstract base for task providers."""

from abc import ABC, abstractmethod
from typing import Any

from personalclaw.tasks.models import Task, TaskComment


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
    async def update_task(self, task_id: str, **fields: Any) -> Task | None: ...

    @abstractmethod
    async def delete_task(self, task_id: str) -> bool: ...

    async def get_comments(self, task_id: str) -> list[TaskComment]:
        return []

    async def add_comment(self, task_id: str, body: str, author: str = "") -> TaskComment | None:
        return None

    @property
    def readonly(self) -> bool:
        return False
