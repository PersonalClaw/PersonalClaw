"""Native filesystem-backed task provider. Stores tasks as individual JSON files under
PERSONALCLAW_HOME/tasks/.
"""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from personalclaw.config.loader import config_dir
from personalclaw.record_ids import record_path
from personalclaw.tasks import reconcile
from personalclaw.tasks.models import (
    TASK_FIELD_COERCERS,
    Task,
    TaskComment,
    TaskDependency,
    TaskPriority,
    TaskStatus,
    WorkflowTaskBinding,
)
from personalclaw.tasks.models import coerce_task_field as models_coerce
from personalclaw.tasks.provider import TaskProvider
from personalclaw.workflows import pool

logger = logging.getLogger(__name__)

#: Fields an update never writes: identity and provenance. `project` is excluded separately in the
#: update loop because it is DERIVED (re-resolved from the task list on every read), not immutable.
_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"id", "provider", "created_at"})


def _coerce_binding(raw: Any) -> "WorkflowTaskBinding | None":
    """Accept a typed binding OR its dict form; anything else is no binding. Both shapes arrive
    in practice — the engine passes the dataclass, and a REST/tool caller passes JSON.
    Refusing either would push the coercion out to every call site, and the site that forgot
    would create a task the engine does not own while the board shows it as managed.
    """
    if isinstance(raw, WorkflowTaskBinding):
        return raw
    if isinstance(raw, dict) and raw:
        return WorkflowTaskBinding.from_dict(raw)
    return None


def create_provider(config: dict[str, Any] | None = None) -> "NativeTaskProvider":
    return NativeTaskProvider()


def _tasks_dir() -> Path:
    return config_dir() / "tasks"


def _record_task_tombstone(task_id: str) -> None:
    """Append a sync-only delete marker for a hard-deleted task (DAS-6c-iii).

    The row id in the ``tasks`` shard is the file stem (the task id), and the side-log
    lives at the entry dir root, so record it there. Best-effort — a failed breadcrumb
    must never turn into a failed delete."""
    try:
        from personalclaw.durability.tombstones import record_tombstone

        record_tombstone(_tasks_dir(), task_id, now=_now_iso())
    except Exception:  # noqa: BLE001 — the delete already happened; the marker is a nicety
        pass


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _current_username() -> str:
    """The owner's attribution handle, or ``""``. Never raises — attribution decorates a write,
    so it must never be the reason one fails.
    """
    try:
        from personalclaw.identity import current_username

        return current_username()
    except Exception:
        return ""


async def _fire_task_complete(task: Task) -> None:
    """Fire the `TaskComplete` lifecycle hook for a task that just finished.

    Swallows everything. A hook is an OBSERVER of a task edit, not a participant: a user's broken
    script must not turn a successful `PUT /api/tasks/{id}` into a 500, and the task is already
    written by the time this runs. The event/context shape comes from `pool.lifecycle_payload`, so
    the payload and the edge rule live together rather than being restated here.
    """
    try:
        from personalclaw.hooks import get_global_hook_store

        store = get_global_hook_store()
        if store is None:
            return
        binding = getattr(task, "workflow_binding", None)
        payload = pool.lifecycle_payload(
            task_id=task.id,
            title=task.title,
            status=task.status.value,
            run_id=getattr(binding, "run_id", "") or "",
            node_id=getattr(binding, "node_id", "") or "",
        )
        await store.fire(payload["event"], context=payload["context"])
    except Exception:  # noqa: BLE001 - an observer never fails the write it observed
        logger.debug("TaskComplete hook fire failed", exc_info=True)


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
        """Resolve a task id to its record, refusing an id that is not one segment.

        ``task_id`` arrives from ``/api/tasks/{task_id}`` unvalidated through handler →
        registry → provider (#471), and this is the one expression all four verbs share,
        so the guard belongs here rather than at three call sites.
        """
        return record_path(self._ensure_dir(), task_id, kind="task_id")

    def _comments_path(self, task_id: str) -> Path:
        """Resolve a task's sidecar comments file — same guard, different template.

        ``add_comment`` writes this file whenever ``<id>.json`` is readable, so an
        unguarded template here is an arbitrary-overwrite primitive even though the id
        never names it directly.
        """
        return record_path(self._ensure_dir(), task_id, prefix="_comments_", kind="task_id")

    def _comment_count(self, task_id: str) -> int:
        """Number of comments on a task (length of its ``_comments_<id>.json``).

        Fails soft, including on an unsafe id: this is a derived badge value stamped
        during ``_read_task``, so a refusal here must not make the task itself
        unreadable. The verbs that act on comments use :meth:`_comments_path` directly
        and do surface the refusal.
        """
        try:
            f = self._comments_path(task_id)
            if not f.exists():
                return 0
            data = json.loads(f.read_text(encoding="utf-8"))
            return len(data) if isinstance(data, list) else 0
        except Exception:  # noqa: BLE001 — incl. UnsafeRecordId; see the docstring
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
        """A task's ``project`` label = its task list's project name. A task with no task list
        has no project label (empty string) — never a stale id. ``cache`` (task_list_id →
        label) avoids re-reading project files per task in a list.
        """
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
        """Accept either a list of edge dicts or a flat list of prerequisite ids (treated as
        BLOCKS edges) from older / simpler callers.
        """
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

            # 🔴 Every CALLER-supplied field goes through the same coercion table `from_dict` and
            # `update_task` use. It used to be enumerated here with a per-field ad-hoc rule —
            # `float()` on `order`, `str()` on `preview`, and nothing at all on `title`,
            # `description` or `labels` — which is how `labels: "not-an-array"` was persisted bare
            # by BOTH create and update (#386), and how create and update came to disagree about
            # the same field (#456: POST 500'd on a non-string scalar where PUT `str()`-coerced it).
            #
            # It also closes the latent bug the `workflow_binding` comment below used to record:
            # "this provider builds its Task field-by-field, so a new model field is dropped on
            # create unless it is named". The coercion is shared now, so the only thing this list
            # still owns is WHICH fields a caller may set — and the rail
            # (`tests/test_task_field_coercion.py`) checks that against `Task`'s fields.
            def _given(name: str, default: Any = None) -> Any:
                return models_coerce(name, fields.get(name, default), strict=True)

            # Accept depends_on (flat) or dependencies (typed) — both → typed edges. The coercer
            # handles both shapes, including the legacy bare-id form.
            dep_src = fields.get("dependencies", fields.get("depends_on", []))
            task_list_id = _given("task_list_id", "")
            task = Task(
                id=task_id,
                title=_given("title", "Untitled"),
                status=_given("status", "open"),
                description=_given("description", ""),
                provider=self.name,
                # project is a derived, read-only label (the task list's project
                # name) — resolved here and re-resolved on every read.
                project=self._derive_project_label(task_list_id),
                task_list_id=task_list_id,
                dependencies=models_coerce("dependencies", dep_src, strict=True),
                # Attribution (TEAM-SHARED-ENTITIES §1): an explicit author wins;
                # otherwise stamp the owner's handle. Unset handle → "" → today's
                # behavior (no attribution).
                author=_given("author", "") or _current_username(),
                assignee=_given("assignee", ""),
                priority=_given("priority", "medium"),
                labels=_given("labels", []),
                due=_given("due", ""),
                order=_given("order", 0.0),
                exit_criteria=_given("exit_criteria", []),
                action_plan=_given("action_plan", []),
                notes=_given("notes", []),
                research_notes=_given("research_notes", []),
                execution_notes=_given("execution_notes", []),
                agent_instructions_template=_given("agent_instructions_template", ""),
                # Workflow projection (TASKS-SOPS §1, S55). Still enumerated: this list is what a
                # caller may SET, and the binding once round-tripped through `to_dict`/`from_dict`
                # and still arrived empty from `create_task` because it was missing here.
                workflow_binding=_given("workflow_binding"),
                blocked_kind=_given("blocked_kind", ""),
                preview=_given("preview", ""),
                done_criterion=_given("done_criterion", ""),
                evidence=_given("evidence", []),
                attempts=_given("attempts", []),
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
        """Apply ``fields``, reject cycles, reconcile dependency-driven status, and return the
        edited task. The full set of tasks whose status changed via cascade is exposed on
        ``task._reconciled`` for the handler to return.
        """

        def _update() -> Task | None:
            tasks = self._task_map()
            task = tasks.get(task_id)
            if not task:
                return None
            # The pre-edit status, for the edge-triggered completion event below. Captured BEFORE
            # the field loop because the loop mutates `task` in place.
            previous_status = task.status.value
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
                    # Dependency gate: a task cannot complete while a prerequisite is
                    # still open. The exit-criteria gate above enforced only the task's
                    # OWN checklist, so a kanban drag (or any PUT status=done) could mark
                    # a task done with a non-terminal BLOCKS prerequisite — leaving a
                    # `blocked_reason_kind="auto"` DONE row that reconcile never clears and
                    # counting it toward graph completion. Same shape and 400-mapping as
                    # the exit-criteria refusal; uses reconcile's own unfinished-prereq
                    # predicate so the gate and the auto-block logic can never disagree.
                    if new_status == TaskStatus.DONE:
                        blocked = reconcile.block_reason(task, tasks)
                        if blocked["is_blocked"]:
                            raise ValueError(
                                "cannot complete: waiting on unfinished prerequisite — "
                                + ", ".join(blocked["blocking_task_titles"])
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
                elif key in _IMMUTABLE_FIELDS:
                    # Identity and provenance are not editable.
                    continue
                elif key not in TASK_FIELD_COERCERS:
                    # NOT a task field. Ignored, as before — deliberately not a 400, because the
                    # dashboard's own edit form posts `project_id`, which is not a `Task` field:
                    # `_attach_project_general_list` resolves and POPS it on the create path and
                    # is not called on update, so refusing unknown keys here would break Save on
                    # the task detail screen. Logged rather than silent, since the same branch also
                    # swallows a typo'd field name.
                    #
                    # (That `project_id` is accepted on create and ignored on update is its own
                    # gap — choosing a project while editing silently discards the choice. Filed as
                    # #2142; it belongs to that fix, not to the coercion this change is about.)
                    logger.debug("update_task ignoring unknown field %r on %s", key, task_id)
                    continue
                else:
                    # 🔴 EVERY OTHER FIELD IS COERCED, and an uncoercible value is REFUSED.
                    #
                    # This was `setattr(task, key, val)` behind a `hasattr` check: no type check at
                    # all, and `__post_init__`/`from_dict`'s normalization runs only at
                    # construction, so an update was the one write that never re-validated. What
                    # that accepted, each measured:
                    #
                    #   `order: "abc"`        → 200, then every read raised and the task 404'd
                    #                           everywhere while its file stayed on disk (#387)
                    #   `labels: "a-string"`  → persisted bare; `labels.slice().map` took the whole
                    #                           Tasks page into an error boundary (#386)
                    #   `description: 12345`  → `(12345).lower()` in the search scorer, so EVERY
                    #                           search answered 500 (#388)
                    #   `exit_criteria: "x"`  → iterated CHARACTER BY CHARACTER, one un-meetable
                    #                           criterion per letter, task never completable (#818)
                    #
                    # A refusal is a `ValueError`, which both handlers already map to a 400.
                    task_setattr = models_coerce(key, val, strict=True)
                    setattr(task, key, task_setattr)
            # Re-derive the project label if the task list changed.
            if "task_list_id" in fields:
                task.project = self._derive_project_label(task.task_list_id)
            # Reject a dependency edit that introduces a cycle.
            cycle = reconcile.would_create_cycle(tasks, task.id, task.prerequisite_ids())
            if cycle:
                raise reconcile.DependencyCycleError(cycle)
            task.updated_at = _now_iso()
            # Stamp manual vs auto block ONLY when this write explicitly set status
            # (a user deliberately blocking for an external reason). A write that
            # merely edits dependencies must NOT reclassify: removing the last
            # prerequisite from an auto-blocked task would otherwise be stamped
            # "manual" and stranded blocked forever, since reconcile skips manual
            # blocks (#775). With status untouched, let reconcile (un)block by prereqs.
            if "status" in fields:
                reconcile.classify_manual_block(task, tasks)
            changed: list[Task] = [task]
            if status_or_deps_changed:
                for c in reconcile.reconcile_blocked_status(tasks, task.id):
                    if c.id != task.id:
                        changed.append(c)
            # Persist AFTER reconcile so a cascade that (un)blocks the edited task
            # itself is durable — reconcile mutates it in place, and writing before
            # the cascade would freeze the pre-reconcile status on disk (#775).
            for c in changed:
                self._write_task(c)
            task._reconciled = changed  # type: ignore[attr-defined]
            task._completed_edge = pool.should_fire_completion(  # type: ignore[attr-defined]
                previous_status, task.status.value
            )
            return task

        edited = await asyncio.to_thread(_update)
        # TASKS-SOPS §5 R10: fire the task-completion lifecycle hook. Measured in S60 —
        # `TaskComplete` is declared in `hooks.HOOK_EVENTS`, allowlisted in
        # `validation.ALLOWED_HOOK_EVENTS` and rendered by the hook UI, and NO call site in the
        # repo ever fired it, so a user could configure "when a task finishes" and get nothing.
        # EDGE-triggered (`should_fire_completion`): an idempotent projection recompute is the
        # normal path for workflow-bound tasks, and a level-triggered fire would emit one hook per
        # rebuild.
        if edited is not None and getattr(edited, "_completed_edge", False):
            # APE-2: the SAME completion edge is also the `task.completed` platform-event
            # emit site — one edge, two observers (the user's `TaskComplete` hook below and
            # apps that declared the subscription). Identifiers only: no title, so the
            # event grants TIMING, not task content an app's `api` scope may not cover.
            from personalclaw.apps.app_events import TASK_COMPLETED
            from personalclaw.apps.app_events import emit as emit_platform_event

            emit_platform_event(
                TASK_COMPLETED, {"task_id": edited.id, "status": edited.status.value}
            )
            await _fire_task_complete(edited)
        return edited

    async def delete_task(self, task_id: str) -> bool:
        def _delete() -> bool:
            path = self._task_path(task_id)
            if not path.exists():
                return False
            path.unlink()
            # Sync-only delete marker (DAS-6c-iii): the hard unlink above is the store's
            # truth; this breadcrumb lets the delete propagate across machines instead of a
            # peer resurrecting the task. Best-effort — never fails the delete.
            _record_task_tombstone(task_id)
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
            comments_file = self._comments_path(task_id)
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
            comments_file = self._comments_path(task_id)
            try:
                data = json.loads(comments_file.read_text(encoding="utf-8"))
            except Exception:
                data = []
            comment = {
                "id": f"c-{uuid.uuid4().hex[:8]}",
                # Attribution: explicit author, else the owner's handle, else the
                # historical "user" placeholder so existing readers see no change.
                "author": author or _current_username() or "user",
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

    async def delete_comment(self, task_id: str, comment_id: str) -> bool:
        def _delete() -> bool:
            comments_file = self._comments_path(task_id)
            if not comments_file.exists():
                return False
            try:
                data = json.loads(comments_file.read_text(encoding="utf-8"))
            except Exception:
                return False
            if not isinstance(data, list):
                return False
            kept = [c for c in data if isinstance(c, dict) and c.get("id") != comment_id]
            if len(kept) == len(data):
                return False
            # The sidecar stays on disk when it empties: `_comment_count` reads its
            # length, and an absent file already means zero, so both spellings agree.
            comments_file.write_text(json.dumps(kept, indent=2), encoding="utf-8")
            return True

        return await asyncio.to_thread(_delete)


Provider = NativeTaskProvider
