"""WorkflowProvider ABC — a CRUD backend for workflow definitions.

Stateless: there are no execution methods (workflows are definitions, not runs).
Clones the shape of TaskProvider.
"""

from abc import ABC, abstractmethod
from typing import Any

from personalclaw.workflows.models import Workflow, WorkflowScope


class WorkflowProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:  # "native", "project", ...
        ...

    @abstractmethod
    async def list_workflows(
        self,
        scope: WorkflowScope | None = None,
        scope_ref: str | None = None,
        tag: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[Workflow], int]: ...

    @abstractmethod
    async def get_workflow(self, workflow_id: str) -> Workflow | None: ...

    @abstractmethod
    async def create_workflow(self, **fields: Any) -> Workflow: ...

    @abstractmethod
    async def update_workflow(self, workflow_id: str, **fields: Any) -> Workflow | None: ...

    @abstractmethod
    async def delete_workflow(self, workflow_id: str) -> bool: ...

    @property
    def readonly(self) -> bool:
        return False
