"""Task provider registry — aggregates tasks across all registered backends."""

import asyncio
import logging
from typing import Any

from personalclaw.tasks import reconcile
from personalclaw.tasks.models import Task, TaskComment, TaskPriority
from personalclaw.tasks.provider import TaskProvider

logger = logging.getLogger(__name__)

_providers: dict[str, TaskProvider] = {}


def register_provider(provider: TaskProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> TaskProvider | None:
    return _providers.get(name)


def list_providers() -> list[str]:
    return list(_providers.keys())


def _ensure_native() -> None:
    if "native" not in _providers:
        from personalclaw.tasks.native import NativeTaskProvider

        register_provider(NativeTaskProvider())


async def list_all_tasks(
    status: str | None = None,
    assignee: str | None = None,
    project: str | None = None,
    task_list_id: str | None = None,
    provider_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Task], int]:
    """Aggregate tasks from all providers (or a specific one)."""
    _ensure_native()
    all_tasks: list[Task] = []
    sources = (
        {provider_filter: _providers[provider_filter]}
        if provider_filter and provider_filter in _providers
        else _providers
    )
    for prov in sources.values():
        try:
            tasks, _ = await prov.list_tasks(
                status=status, assignee=assignee, project=project, limit=500, offset=0
            )
            all_tasks.extend(tasks)
        except Exception:
            logger.warning("Task provider %s failed to list", prov.name, exc_info=True)

    if task_list_id:
        all_tasks = [t for t in all_tasks if t.task_list_id == task_list_id]
    all_tasks.sort(key=lambda t: t.updated_at or t.created_at, reverse=True)
    total = len(all_tasks)
    return all_tasks[offset : offset + limit], total


async def get_task(task_id: str, provider_name: str | None = None) -> Task | None:
    """Get a single task. If provider_name is given, query only that provider."""
    _ensure_native()
    if provider_name and provider_name in _providers:
        return await _providers[provider_name].get_task(task_id)
    for prov in _providers.values():
        task = await prov.get_task(task_id)
        if task:
            return task
    return None


async def create_task(provider_name: str = "native", **fields: Any) -> Task:
    _ensure_native()
    prov = _providers.get(provider_name)
    if not prov:
        raise ValueError(f"Unknown task provider: {provider_name}")
    if prov.readonly:
        raise ValueError(f"Provider '{provider_name}' is read-only")
    return await prov.create_task(**fields)


async def update_task(task_id: str, provider_name: str | None = None, **fields: Any) -> Task | None:
    _ensure_native()
    if provider_name and provider_name in _providers:
        prov = _providers[provider_name]
    else:
        for p in _providers.values():
            t = await p.get_task(task_id)
            if t:
                prov = p
                break
        else:
            return None
    if prov.readonly:
        raise ValueError(f"Provider '{prov.name}' is read-only")
    return await prov.update_task(task_id, **fields)


async def delete_task(task_id: str, provider_name: str | None = None) -> bool:
    _ensure_native()
    if provider_name and provider_name in _providers:
        prov = _providers[provider_name]
    else:
        for p in _providers.values():
            t = await p.get_task(task_id)
            if t:
                prov = p
                break
        else:
            return False
    if prov.readonly:
        raise ValueError(f"Provider '{prov.name}' is read-only")
    return await prov.delete_task(task_id)


async def get_comments(task_id: str, provider_name: str | None = None) -> list[TaskComment]:
    _ensure_native()
    if provider_name and provider_name in _providers:
        return await _providers[provider_name].get_comments(task_id)
    for prov in _providers.values():
        t = await prov.get_task(task_id)
        if t:
            return await prov.get_comments(task_id)
    return []


async def add_comment(
    task_id: str, body: str, author: str = "", provider_name: str | None = None
) -> TaskComment | None:
    _ensure_native()
    if provider_name and provider_name in _providers:
        return await _providers[provider_name].add_comment(task_id, body, author)
    for prov in _providers.values():
        t = await prov.get_task(task_id)
        if t:
            return await prov.add_comment(task_id, body, author)
    return None


async def task_graph(provider_filter: str | None = None) -> dict[str, Any]:
    """Adjacency + DependencyAnalysis for the writable native task set (seam S3).

    Only the native provider owns a mutable DAG; read-only providers (project
    runtime) don't participate in dependency analysis.
    """
    _ensure_native()
    prov = _providers.get(provider_filter or "native")
    if prov is None or not hasattr(prov, "graph"):
        prov = _providers["native"]
    return await asyncio.to_thread(prov.graph)  # type: ignore[attr-defined]


async def ready_tasks(project: str | None = None, task_list_id: str | None = None) -> list[Task]:
    """Tasks that can be started now (no unfinished prerequisites), optionally
    scoped to a project label or a task list."""
    tasks, _ = await list_all_tasks(project=project, task_list_id=task_list_id, limit=10_000)
    task_map = {t.id: t for t in tasks}
    ready_ids = set(reconcile.ready_task_ids(task_map))
    return [t for t in tasks if t.id in ready_ids]


async def search_tasks(
    query: str = "",
    statuses: list[str] | None = None,
    priorities: list[str] | None = None,
    tags: list[str] | None = None,
    project: str | None = None,
    task_list_id: str | None = None,
    sort_by: str = "relevance",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Task], int]:
    """Full task search: case-folded substring over title+description, plus
    status/priority/tag/project/task-list filters and a sort key
    (relevance|created_at|updated_at|priority)."""
    tasks, _ = await list_all_tasks(project=project, task_list_id=task_list_id, limit=10_000)
    q = (query or "").strip().lower()
    status_set = {s for s in (statuses or []) if s}
    prio_set = {p for p in (priorities or []) if p}
    tag_set = {t for t in (tags or []) if t}

    def _score(t: Task) -> float:
        if not q:
            return 0.0
        return 2.0 * t.title.lower().count(q) + 1.0 * (t.description or "").lower().count(q)

    matched: list[tuple[float, Task]] = []
    for t in tasks:
        if status_set and t.status.value not in status_set:
            continue
        if prio_set and t.priority.value not in prio_set:
            continue
        if tag_set and not (tag_set & set(t.labels)):
            continue
        if q:
            score = _score(t)
            if score <= 0:
                continue
        else:
            score = 0.0
        matched.append((score, t))

    if sort_by == "relevance" and q:
        matched.sort(key=lambda st: (st[0], st[1].created_at), reverse=True)
    elif sort_by == "priority":
        weight = {
            TaskPriority.CRITICAL: 5,
            TaskPriority.HIGH: 4,
            TaskPriority.MEDIUM: 3,
            TaskPriority.LOW: 2,
            TaskPriority.TRIVIAL: 1,
        }
        matched.sort(key=lambda st: (weight.get(st[1].priority, 3), st[1].created_at), reverse=True)
    elif sort_by == "created_at":
        matched.sort(key=lambda st: st[1].created_at, reverse=True)
    else:  # updated_at (default fallback)
        matched.sort(key=lambda st: st[1].updated_at or st[1].created_at, reverse=True)

    results = [t for _, t in matched]
    total = len(results)
    return results[offset : offset + limit], total
