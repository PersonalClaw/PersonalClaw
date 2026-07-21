"""Canonical task entity — the unified model across all task providers.

A task carries a typed DAG of dependencies (``dependencies``: prerequisite edges
with a :class:`DependencyType`), an exit-criteria checklist, an action plan,
phased notes, and an agent-instructions template. Status propagates along the DAG
via the reconciliation service (see ``reconcile.py``): finishing every prerequisite
auto-unblocks a dependent; a manual block is never auto-cleared.

Hierarchy: a task belongs to a TaskList, which belongs to a Project
(Project → TaskList → Task). ``task_list_id`` is the structural link; ``project``
is a denormalized project-id label kept for fast grouping/filtering.
"""

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class TaskStatus(enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


# A task in a terminal state satisfies any dependency that points at it.
TERMINAL_STATUSES = (TaskStatus.DONE, TaskStatus.CANCELLED)


class TaskPriority(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    TRIVIAL = "trivial"

    @classmethod
    def normalize(cls, value: Any) -> "TaskPriority":
        """Coerce free input to a known rung; unknown → MEDIUM."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.MEDIUM


class DependencyType(str, enum.Enum):
    BLOCKS = "BLOCKS"  # prerequisite must finish before this task can start
    REQUIRED_FOR = "REQUIRED_FOR"  # softer link: informational, does not gate status


@dataclass
class TaskDependency:
    """A prerequisite edge: this task depends on ``depends_on_task_id``."""

    depends_on_task_id: str
    dependency_type: DependencyType = DependencyType.BLOCKS

    def to_dict(self) -> dict[str, Any]:
        return {
            "depends_on_task_id": self.depends_on_task_id,
            "dependency_type": self.dependency_type.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskDependency":
        raw_type = d.get("dependency_type", DependencyType.BLOCKS.value)
        try:
            dtype = DependencyType(raw_type)
        except ValueError:
            dtype = DependencyType.BLOCKS
        return cls(depends_on_task_id=str(d.get("depends_on_task_id", "")), dependency_type=dtype)


class ExitCriteriaStatus(str, enum.Enum):
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"


def normalize_exit_criterion(item: Any) -> dict:
    """Canonical exit criterion: ``{description, status, comment}``.

    Accepts a plain string, the legacy ``{description, met: bool}`` shape, or the
    canonical shape. ``met`` is emitted (derived from ``status``) so older readers
    keep working."""
    if isinstance(item, str):
        desc, status, comment = item, ExitCriteriaStatus.INCOMPLETE.value, ""
    elif isinstance(item, dict):
        desc = str(item.get("description") or item.get("criteria") or "")
        if "status" in item:
            raw = str(item["status"]).strip().lower()
            status = (
                ExitCriteriaStatus.COMPLETE.value
                if raw in ("complete", "completed", "done", "true", "met")
                else ExitCriteriaStatus.INCOMPLETE.value
            )
        else:
            status = (
                ExitCriteriaStatus.COMPLETE.value
                if bool(item.get("met"))
                else ExitCriteriaStatus.INCOMPLETE.value
            )
        comment = str(item.get("comment") or "")
    else:
        desc, status, comment = "", ExitCriteriaStatus.INCOMPLETE.value, ""
    return {
        "description": desc,
        "status": status,
        "comment": comment,
        "met": status == ExitCriteriaStatus.COMPLETE.value,
    }


def normalize_action_plan_item(item: Any, index: int) -> dict:
    """Canonical action-plan item: ``{sequence, content, completed}``.

    Accepts a plain string, the legacy ``{description, completed}`` shape, or the
    canonical ``{sequence, content}`` shape. ``description`` is emitted as an alias
    of ``content`` for older readers."""
    if isinstance(item, str):
        content, completed = item, False
    elif isinstance(item, dict):
        content = str(item.get("content") or item.get("description") or "")
        completed = bool(item.get("completed"))
    else:
        content, completed = "", False
    seq = item.get("sequence", index) if isinstance(item, dict) else index
    try:
        seq = int(seq)
    except (TypeError, ValueError):
        seq = index
    return {"sequence": seq, "content": content, "description": content, "completed": completed}


def _as_item_list(value: Any) -> list:
    """Coerce exit_criteria / action_plan input to a LIST before per-item normalize.

    A bare scalar (a single criterion/step passed as a string or dict — a plausible
    caller/LLM mistake, e.g. exit_criteria="tests pass" instead of ["tests pass"])
    must be wrapped: iterating a bare string would treat its CHARACTERS as separate
    items, fabricating ~N single-char criteria that can never be 'met' → the task is
    permanently un-completable. None/non-iterable → empty."""
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        return [value]
    if isinstance(value, list):
        return value
    try:
        return list(value)
    except TypeError:
        return []


def normalize_note(item: Any) -> dict:
    """Canonical note: ``{content, timestamp}`` (carries any legacy ``phase``/
    ``created_at`` through for back-compat readers)."""
    if isinstance(item, str):
        return {"content": item, "timestamp": ""}
    if isinstance(item, dict):
        out = {
            "content": str(item.get("content") or ""),
            "timestamp": str(item.get("timestamp") or item.get("created_at") or ""),
        }
        if item.get("phase"):
            out["phase"] = item["phase"]
        return out
    return {"content": "", "timestamp": ""}


@dataclass
class Task:
    id: str
    title: str
    status: "TaskStatus" = TaskStatus.OPEN
    description: str = ""
    provider: str = ""
    project: str = ""  # denormalized project-id label (grouping/filter)
    task_list_id: str = ""  # structural parent (Project → TaskList → Task)
    dependencies: list[TaskDependency] = field(default_factory=list)
    assignee: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    labels: list[str] = field(default_factory=list)
    due: str = ""
    order: float = 0.0  # intra-column ordering for kanban reorder
    # Rich planning fields
    exit_criteria: list[dict] = field(default_factory=list)  # [{description, status, comment}]
    action_plan: list[dict] = field(default_factory=list)  # [{sequence, content, completed}]
    notes: list[dict] = field(default_factory=list)  # general notes [{content, timestamp}]
    research_notes: list[dict] = field(default_factory=list)  # research-phase notes
    execution_notes: list[dict] = field(default_factory=list)  # execution-phase notes
    agent_instructions_template: str = ""
    # Dependency-driven status bookkeeping
    blocked_reason_kind: str = ""  # "" | "auto" | "manual"
    created_at: str = ""
    updated_at: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        # Coerce the list-valued planning fields to actual lists at the Task boundary,
        # so a bare scalar passed by a caller/LLM (exit_criteria="tests pass" instead
        # of ["tests pass"]) can't be iterated CHARACTER-by-character downstream —
        # which would fabricate single-char criteria/steps and (for exit_criteria)
        # permanently block completion. One chokepoint guards every iteration site.
        self.exit_criteria = _as_item_list(self.exit_criteria)
        self.action_plan = _as_item_list(self.action_plan)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["priority"] = self.priority.value
        d["dependencies"] = [dep.to_dict() for dep in self.dependencies]
        d["exit_criteria"] = [normalize_exit_criterion(e) for e in self.exit_criteria]
        d["action_plan"] = [
            normalize_action_plan_item(a, i) for i, a in enumerate(self.action_plan)
        ]
        d["notes"] = [normalize_note(n) for n in self.notes]
        d["research_notes"] = [normalize_note(n) for n in self.research_notes]
        d["execution_notes"] = [normalize_note(n) for n in self.execution_notes]
        # block_reason is derived per-read by the reconcile service (needs the
        # full task set); callers that want it call attach_block_reason().
        return d

    def can_mark_complete(self) -> bool:
        """A task may be completed only when every exit criterion is complete
        (a task with no exit criteria is freely completable)."""
        return all(
            normalize_exit_criterion(e)["status"] == ExitCriteriaStatus.COMPLETE.value
            for e in self.exit_criteria
        )

    def incomplete_exit_criteria(self) -> list[str]:
        """Descriptions of the exit criteria not yet complete (for error messages)."""
        return [
            n["description"]
            for n in (normalize_exit_criterion(e) for e in self.exit_criteria)
            if n["status"] != ExitCriteriaStatus.COMPLETE.value
        ]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        raw_status = d.get("status", "open")
        try:
            status = TaskStatus(raw_status)
        except ValueError:
            status = TaskStatus.OPEN

        # Typed dependencies, migrating any legacy flat `depends_on` list on read.
        deps_raw = d.get("dependencies")
        if deps_raw:
            dependencies = [TaskDependency.from_dict(x) for x in deps_raw if isinstance(x, dict)]
        else:
            dependencies = [
                TaskDependency(depends_on_task_id=str(pid), dependency_type=DependencyType.BLOCKS)
                for pid in (d.get("depends_on") or [])
                if str(pid).strip()
            ]

        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            status=status,
            description=d.get("description", ""),
            provider=d.get("provider", ""),
            project=d.get("project", ""),
            task_list_id=d.get("task_list_id", ""),
            dependencies=dependencies,
            assignee=d.get("assignee", ""),
            priority=TaskPriority.normalize(d.get("priority", "medium")),
            labels=d.get("labels", []),
            due=d.get("due", ""),
            order=float(d.get("order", 0.0) or 0.0),
            exit_criteria=[normalize_exit_criterion(e) for e in (d.get("exit_criteria") or [])],
            action_plan=[
                normalize_action_plan_item(a, i) for i, a in enumerate(d.get("action_plan") or [])
            ],
            notes=[normalize_note(n) for n in (d.get("notes") or [])],
            research_notes=[normalize_note(n) for n in (d.get("research_notes") or [])],
            execution_notes=[normalize_note(n) for n in (d.get("execution_notes") or [])],
            agent_instructions_template=d.get("agent_instructions_template", ""),
            blocked_reason_kind=d.get("blocked_reason_kind", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            url=d.get("url", ""),
        )

    def prerequisite_ids(self) -> list[str]:
        """Ids this task depends on via a BLOCKS edge (the status-gating set)."""
        return [
            dep.depends_on_task_id
            for dep in self.dependencies
            if dep.dependency_type == DependencyType.BLOCKS and dep.depends_on_task_id
        ]


@dataclass
class TaskComment:
    id: str
    task_id: str
    author: str
    body: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Hierarchy: Project → TaskList → Task ──

# The two protected, always-present projects. ``Personal`` is the catch-all for
# work created without a chosen project; ``Repeatable`` hosts resettable lists.
DEFAULT_PROJECTS = ("Personal", "Repeatable")


@dataclass
class Project:
    """A first-class work unit at the top of the hierarchy.

    A project ties together everything a user does on one logical effort — Goal
    Loops, Code projects, manually-created Tasks (and optionally Artifacts) — and
    owns a **context directory** (``projects/<id>/``) where that context
    consolidates for continuation across features and sessions. It MAY bind an
    existing ``workspace_dir`` (a codebase on disk); when bound, the project's
    per-workspace git worktrees live under its context dir so several projects can
    operate on one workspace without colliding. With no workspace bound, the
    context dir itself is the working area.

    Names are unique and LLM-generated/maintained until the user renames manually
    (``name_locked``). ``Personal``/``Repeatable`` are protected defaults.
    """

    id: str
    name: str
    is_default: bool = False
    status: str = "active"  # active | archived
    workspace_dir: str = ""  # bound codebase dir; "" = context dir is the workspace
    name_locked: bool = False  # user renamed manually → LLM stops auto-renaming
    agent_instructions_template: str = ""
    # User-authored project brief — the goal/scope/background of this effort. Stored on
    # the project and injected as shared CONTEXT for every agent working on any session
    # or loop scoped under it (distinct from agent_instructions_template, which is
    # operating-procedure guidance; the brief is the WHAT/WHY of the project).
    brief: str = ""
    created_at: str = ""
    updated_at: str = ""

    def is_default_project(self) -> bool:
        return self.is_default or self.name in DEFAULT_PROJECTS

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_default"] = self.is_default_project()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        name = d.get("name", "")
        return cls(
            id=d.get("id", ""),
            name=name,
            is_default=bool(d.get("is_default", False)) or name in DEFAULT_PROJECTS,
            status=str(d.get("status") or "active"),
            workspace_dir=str(d.get("workspace_dir") or ""),
            name_locked=bool(d.get("name_locked", False)),
            agent_instructions_template=d.get("agent_instructions_template", ""),
            brief=str(d.get("brief") or ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class TaskList:
    """A mid-level container belonging to a :class:`Project`. Tasks belong to a
    task list (``Task.task_list_id``); the list belongs to a project
    (``project_id``)."""

    id: str
    name: str
    project_id: str
    agent_instructions_template: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskList":
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            project_id=d.get("project_id", ""),
            agent_instructions_template=d.get("agent_instructions_template", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
