"""Native filesystem-backed task provider.

Stores tasks as individual JSON files under PERSONALCLAW_HOME/tasks/.
"""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from personalclaw.config.loader import config_dir
from personalclaw.tasks import reconcile
from personalclaw.tasks.models import (
    Task,
    TaskComment,
    TaskDependency,
    TaskPriority,
    TaskStatus,
)
from personalclaw.tasks.provider import TaskProvider


def create_provider(config: dict[str, Any] | None = None) -> "NativeTaskProvider":
    return NativeTaskProvider()


def _tasks_dir() -> Path:
    return config_dir() / "tasks"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class NativeTaskProvider(TaskProvider):
    """Filesystem task provider — one JSON file per task."""

    @property
    def name(self) -> str:
        return "native"

    def _ensure_dir(self) -> Path:
        d = _tasks_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _task_path(self, task_id: str) -> Path:
        return self._ensure_dir() / f"{task_id}.json"

    def _comment_count(self, task_id: str) -> int:
        """Number of comments on a task (length of its ``_comments_<id>.json``)."""
        f = self._ensure_dir() / f"_comments_{task_id}.json"
        if not f.exists():
            return 0
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return len(data) if isinstance(data, list) else 0
        except Exception:
            return 0

    def _read_task(self, path: Path, label_cache: dict | None = None) -> Task | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["provider"] = self.name
            task = Task.from_dict(data)
            # project is a DERIVED, read-only label — always resolve it from the
            # task's task list (its project's name) at read time, so a renamed
            # project propagates and stale stored values (e.g. legacy loop ids
            # written into `project` before the hierarchy existed) never leak.
            task.project = self._derive_project_label(task.task_list_id, cache=label_cache)
            # comment_count is a derived presentation value (not a stored field) —
            # stamp it so list/card/detail can show the comment badge.
            task._comment_count = self._comment_count(task.id)  # type: ignore[attr-defined]
            return task
        except Exception:
            return None

    def _write_task(self, task: Task) -> None:
        path = self._task_path(task.id)
        path.write_text(json.dumps(task.to_dict(), indent=2), encoding="utf-8")

    def _all_tasks(self) -> list[Task]:
        d = self._ensure_dir()
        cache: dict[str, str] = {}
        tasks = []
        for f in sorted(d.glob("*.json")):
            if f.name.startswith("_"):
                continue
            t = self._read_task(f, label_cache=cache)
            if t:
                tasks.append(t)
        return tasks

    def _task_map(self) -> dict[str, Task]:
        return {t.id: t for t in self._all_tasks()}

    def _derive_project_label(self, task_list_id: str, cache: dict | None = None) -> str:
        """A task's ``project`` label = its task list's project name. A task with no
        task list has no project label (empty string) — never a stale id. ``cache``
        (task_list_id → label) avoids re-reading project files per task in a list."""
        if not task_list_id:
            return ""
        if cache is not None and task_list_id in cache:
            return cache[task_list_id]
        label = ""
        try:
            from personalclaw.tasks.hierarchy import HierarchyStore

            store = HierarchyStore()
            tl = store.get_task_list(task_list_id)
            if tl:
                project = store.get_project(tl.project_id)
                if project:
                    label = project.name
        except Exception:
            label = ""
        if cache is not None:
            cache[task_list_id] = label
        return label

    @staticmethod
    def _coerce_dependencies(value: Any) -> list[TaskDependency]:
        """Accept either a list of edge dicts or a flat list of prerequisite ids
        (treated as BLOCKS edges) from older / simpler callers."""
        # A bare scalar (a single id dict/string, e.g. an LLM passing depends_on:
        # "task-123" instead of ["task-123"]) must be wrapped — iterating it would
        # treat a string's CHARACTERS as separate prerequisite ids, fabricating
        # garbage edges that block the task on nonexistent tasks forever.
        if isinstance(value, (str, dict, TaskDependency)):
            value = [value]
        out: list[TaskDependency] = []
        for item in value or []:
            if isinstance(item, TaskDependency):
                out.append(item)
            elif isinstance(item, dict):
                out.append(TaskDependency.from_dict(item))
            elif isinstance(item, str) and item.strip():
                out.append(TaskDependency(depends_on_task_id=item.strip()))
        return out

    async def list_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
        project: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        def _list() -> tuple[list[Task], int]:
            tasks = self._all_tasks()
            if status:
                tasks = [t for t in tasks if t.status.value == status]
            if assignee:
                tasks = [t for t in tasks if t.assignee == assignee]
            if project:
                tasks = [t for t in tasks if t.project == project]
            total = len(tasks)
            return tasks[offset : offset + limit], total

        return await asyncio.to_thread(_list)

    async def get_task(self, task_id: str) -> Task | None:
        path = self._task_path(task_id)
        return await asyncio.to_thread(self._read_task, path)

    async def create_task(self, **fields: Any) -> Task:
        def _create() -> Task:
            task_id = f"t-{uuid.uuid4().hex[:8]}"
            now = _now_iso()
            # Accept depends_on (flat) or dependencies (typed) — both → typed edges.
            dep_src = fields.get("dependencies", fields.get("depends_on", []))
            dependencies = self._coerce_dependencies(dep_src)
            task_list_id = fields.get("task_list_id", "")
            task = Task(
                id=task_id,
                title=fields.get("title", "Untitled"),
                status=TaskStatus(fields.get("status", "open")),
                description=fields.get("description", ""),
                provider=self.name,
                # project is a derived, read-only label (the task list's project
                # name) — resolved here and re-resolved on every read.
                project=self._derive_project_label(task_list_id),
                task_list_id=task_list_id,
                dependencies=dependencies,
                assignee=fields.get("assignee", ""),
                priority=TaskPriority.normalize(fields.get("priority", "medium")),
                labels=fields.get("labels", []),
                due=fields.get("due", ""),
                order=float(fields.get("order", 0.0) or 0.0),
                exit_criteria=fields.get("exit_criteria", []),
                action_plan=fields.get("action_plan", []),
                notes=fields.get("notes", []),
                research_notes=fields.get("research_notes", []),
                execution_notes=fields.get("execution_notes", []),
                agent_instructions_template=fields.get("agent_instructions_template", ""),
                created_at=now,
                updated_at=now,
            )
            tasks = self._task_map()
            # Server-authoritative cycle rejection (hard error).
            cycle = reconcile.would_create_cycle(
                {**tasks, task.id: task}, task.id, task.prerequisite_ids()
            )
            if cycle:
                raise reconcile.DependencyCycleError(cycle)
            tasks[task.id] = task
            reconcile.classify_manual_block(task, tasks)
            self._write_task(task)
            # A new prerequisite/dependent can shift block state across the set.
            for changed in reconcile.reconcile_blocked_status(tasks, task.id):
                self._write_task(changed)
            return task

        return await asyncio.to_thread(_create)

    async def update_task(self, task_id: str, **fields: Any) -> Task | None:
        """Apply ``fields``, reject cycles, reconcile dependency-driven status, and
        return the edited task. The full set of tasks whose status changed via
        cascade is exposed on ``task._reconciled`` for the handler to return."""
        def _update() -> Task | None:
            tasks = self._task_map()
            task = tasks.get(task_id)
            if not task:
                return None
            status_or_deps_changed = False
            for key, val in fields.items():
                if key == "status":
                    try:
                        new_status = TaskStatus(val)
                    except ValueError:
                        # An invalid status must be a loud 400, not a silent no-op:
                        # the old `continue` made PUT /api/tasks/{id} return 200 with
                        # the task unchanged for the natural guess "completed" (the
                        # board column is even labeled Completed). The agent tool
                        # layer normalizes LLM synonyms before calling here; every
                        # other caller should hear the truth (handler maps
                        # ValueError → 400 with the valid set named).
                        raise ValueError(
                            f"invalid status {val!r} — use one of: "
                            + ", ".join(s.value for s in TaskStatus)
                        ) from None
                    # Exit-criteria gate: a task can only be completed when every
                    # exit criterion is complete.
                    if new_status == TaskStatus.DONE and not task.can_mark_complete():
                        raise ValueError(
                            "cannot complete: unfinished exit criteria — "
                            + ", ".join(task.incomplete_exit_criteria())
                        )
                    task.status = new_status
                    status_or_deps_changed = True
                elif key in ("dependencies", "depends_on"):
                    task.dependencies = self._coerce_dependencies(val)
                    status_or_deps_changed = True
                elif key == "priority":
                    task.priority = TaskPriority.normalize(val)
                elif key == "project":
                    # project is a derived label, never set directly.
                    continue
                elif hasattr(task, key) and key not in ("id", "provider", "created_at"):
                    setattr(task, key, val)
            # Re-derive the project label if the task list changed.
            if "task_list_id" in fields:
                task.project = self._derive_project_label(task.task_list_id)
            # Reject a dependency edit that introduces a cycle.
            cycle = reconcile.would_create_cycle(tasks, task.id, task.prerequisite_ids())
            if cycle:
                raise reconcile.DependencyCycleError(cycle)
            task.updated_at = _now_iso()
            # Stamp manual vs auto block for the directly-edited task, then cascade.
            reconcile.classify_manual_block(task, tasks)
            self._write_task(task)
            changed: list[Task] = [task]
            if status_or_deps_changed:
                for c in reconcile.reconcile_blocked_status(tasks, task.id):
                    if c.id != task.id:
                        self._write_task(c)
                        changed.append(c)
            task._reconciled = changed  # type: ignore[attr-defined]
            return task

        return await asyncio.to_thread(_update)

    async def delete_task(self, task_id: str) -> bool:
        def _delete() -> bool:
            path = self._task_path(task_id)
            if not path.exists():
                return False
            path.unlink()
            # Removing a prerequisite can unblock its dependents — reconcile.
            tasks = self._task_map()
            # Drop edges that pointed at the deleted task so the graph stays clean.
            for t in tasks.values():
                kept = [d for d in t.dependencies if d.depends_on_task_id != task_id]
                if len(kept) != len(t.dependencies):
                    t.dependencies = kept
                    self._write_task(t)
            # Re-evaluate every former dependent (their prereq set shrank).
            for t in list(tasks.values()):
                for changed in reconcile.reconcile_blocked_status(tasks, t.id):
                    self._write_task(changed)
            return True

        return await asyncio.to_thread(_delete)

    def graph(self) -> dict[str, Any]:
        """Adjacency + DependencyAnalysis over this provider's tasks (for /graph)."""
        tasks = self._task_map()
        analysis = reconcile.analyze(tasks)
        edges = [
            {"from": tid, "to": dep.depends_on_task_id, "type": dep.dependency_type.value}
            for tid, t in tasks.items()
            for dep in t.dependencies
            if dep.depends_on_task_id in tasks
        ]
        return {
            "tasks": [t.to_dict() for t in tasks.values()],
            "edges": edges,
            "analysis": analysis.to_dict(),
        }

    async def get_comments(self, task_id: str) -> list[TaskComment]:
        def _get() -> list[TaskComment]:
            comments_file = self._ensure_dir() / f"_comments_{task_id}.json"
            if not comments_file.exists():
                return []
            try:
                data = json.loads(comments_file.read_text(encoding="utf-8"))
                return [
                    TaskComment(
                        id=c["id"],
                        task_id=task_id,
                        author=c.get("author", ""),
                        body=c.get("body", ""),
                        created_at=c.get("created_at", ""),
                    )
                    for c in data
                ]
            except Exception:
                return []

        return await asyncio.to_thread(_get)

    async def add_comment(self, task_id: str, body: str, author: str = "") -> TaskComment | None:
        def _add() -> TaskComment | None:
            path = self._task_path(task_id)
            if not path.exists():
                return None
            comments_file = self._ensure_dir() / f"_comments_{task_id}.json"
            try:
                data = json.loads(comments_file.read_text(encoding="utf-8"))
            except Exception:
                data = []
            comment = {
                "id": f"c-{uuid.uuid4().hex[:8]}",
                "author": author or "user",
                "body": body,
                "created_at": _now_iso(),
            }
            data.append(comment)
            comments_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return TaskComment(
                id=comment["id"],
                task_id=task_id,
                author=comment["author"],
                body=comment["body"],
                created_at=comment["created_at"],
            )

        return await asyncio.to_thread(_add)


Provider = NativeTaskProvider
