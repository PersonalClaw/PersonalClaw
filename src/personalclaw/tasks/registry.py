"""Task provider registry — aggregates tasks across all registered backends."""

import asyncio
import logging
import time
from typing import Any

from personalclaw.tasks import reconcile
from personalclaw.tasks.models import Task, TaskComment, TaskPriority
from personalclaw.tasks.provider import TaskProvider

logger = logging.getLogger(__name__)

_providers: dict[str, TaskProvider] = {}

#: The widest page any single caller may take, and the width each provider is asked for.
#: One number because the HTTP route and the agent's ``task_list`` tool are two doors onto
#: this same aggregation: a page cap they each picked separately is how ``limit`` came to
#: mean two different things on the two surfaces (#2984).
MAX_TASK_PAGE = 500


class UnknownTaskProvider(ValueError):
    """A ``provider`` name this registry does not recognize.

    A ``ValueError`` subclass so the write paths' existing ``except ValueError`` handling
    still catches it, and its own type so a handler can answer 400 *naming the provider*
    rather than matching prose.
    """


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


def _resolve(name: str | None) -> TaskProvider | None:
    """One provider name → the provider it addresses, or ``None`` meaning "every provider".

    This function exists because the same ``if name and name in _providers … else <all
    providers>`` shape was written out in seven places, and it conflated two inputs that
    are not alike: **no provider given** (legitimately "search them all") and **a name I
    do not recognize** (only ever a client error). Reading the second as the first meant a
    scoped call silently acted on a record it had not addressed — ``DELETE
    /api/tasks/{id}?provider=jira`` deleted the *native* task and answered ``{"ok": true}``
    (#2983). ``create_task`` was the one door that raised on an unknown name; this is that
    behavior, once, for all eight.

    The sibling artifacts registry already resolves the same pluggable-provider design this
    way (``_providers.get(name or "native")`` → a handler refusal), so the shape is being
    made consistent rather than invented.
    """
    _ensure_native()
    if not name:
        return None
    prov = _providers.get(name)
    if prov is None:
        raise UnknownTaskProvider(f"Unknown task provider: {name}")
    return prov


def _resolve_one(name: str | None) -> TaskProvider:
    """The single provider a call that must name exactly one addresses; falsy → native.

    ``create_task`` cannot mean "all providers", so an absent or empty name is the DEFAULT
    provider here rather than a wildcard — the one place the two readings differ.
    """
    _ensure_native()
    prov = _providers.get(name or "native")
    if prov is None:
        raise UnknownTaskProvider(f"Unknown task provider: {name}")
    return prov


async def _routed(task_id: str, provider_name: str | None) -> TaskProvider | None:
    """The provider a by-id call addresses: the one NAMED, or whichever holds the id.

    Raises :class:`UnknownTaskProvider` for a name that is not registered. The
    holder search is what "no name given" *means*; it is never what an unrecognized
    name degrades to — with two providers registered that fallback would let
    ``?provider=A`` mutate a task owned by B.
    """
    prov = _resolve(provider_name)
    if prov is not None:
        return prov
    for candidate in _providers.values():
        if await candidate.get_task(task_id):
            return candidate
    return None


def _page(limit: int, offset: int) -> tuple[int, int]:
    """A slice window that cannot address the far end of the list.

    ``all_tasks[offset : offset + limit]`` with ``limit=-1`` is ``[0:-1]`` — a silently
    SHORT page, returned 200 beside a ``total`` computed before the slice, so the response
    advertised 6 while carrying 5 (#2984). With ``offset=-2`` it is ``[-2:0]``, an empty
    page. Floors only, no ceiling: the page cap belongs to the surface that serves a page,
    and the internal projections (``ready_tasks``, ``search_tasks``) legitimately ask for
    the whole corpus through here.
    """
    return max(1, int(limit)), max(0, int(offset))


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
    limit, offset = _page(limit, offset)
    named = _resolve(provider_filter)
    sources = [named] if named is not None else list(_providers.values())
    all_tasks: list[Task] = []
    for prov in sources:
        try:
            tasks, _ = await prov.list_tasks(
                status=status, assignee=assignee, project=project, limit=MAX_TASK_PAGE, offset=0
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
    prov = await _routed(task_id, provider_name)
    return await prov.get_task(task_id) if prov is not None else None


async def create_task(provider_name: str = "native", **fields: Any) -> Task:
    prov = _resolve_one(provider_name)
    if prov.readonly:
        raise ValueError(f"Provider '{prov.name}' is read-only")
    return await prov.create_task(**fields)


async def update_task(task_id: str, provider_name: str | None = None, **fields: Any) -> Task | None:
    prov = await _routed(task_id, provider_name)
    if prov is None:
        return None
    if prov.readonly:
        raise ValueError(f"Provider '{prov.name}' is read-only")
    return await prov.update_task(task_id, **fields)


async def delete_task(task_id: str, provider_name: str | None = None) -> bool:
    prov = await _routed(task_id, provider_name)
    if prov is None:
        return False
    if prov.readonly:
        raise ValueError(f"Provider '{prov.name}' is read-only")
    return await prov.delete_task(task_id)


async def get_comments(task_id: str, provider_name: str | None = None) -> list[TaskComment]:
    prov = await _routed(task_id, provider_name)
    return await prov.get_comments(task_id) if prov is not None else []


async def add_comment(
    task_id: str, body: str, author: str = "", provider_name: str | None = None
) -> TaskComment | None:
    prov = await _routed(task_id, provider_name)
    if prov is None:
        return None
    return await prov.add_comment(task_id, body, author)


async def delete_comment(task_id: str, comment_id: str, provider_name: str | None = None) -> bool:
    """Remove one comment. Routes exactly like the other write paths, including the
    read-only refusal — a projection provider must not be asked to forget a record it
    does not own."""
    prov = await _routed(task_id, provider_name)
    if prov is None:
        return False
    if prov.readonly:
        raise ValueError(f"Provider '{prov.name}' is read-only")
    return await prov.delete_comment(task_id, comment_id)


async def task_graph(provider_filter: str | None = None) -> dict[str, Any]:
    """Adjacency + DependencyAnalysis for the writable native task set (seam S3).

    Only the native provider owns a mutable DAG; read-only providers (project
    runtime) don't participate in dependency analysis.

    The fallback below is DELIBERATE and is the one door :func:`_resolve` is not applied
    to: an unaddressable ``provider_filter`` here means "this provider has no graph", which
    is a true statement about a registered provider too, so answering the native graph is
    the honest read rather than a refusal.
    """
    _ensure_native()
    prov = _providers.get(provider_filter or "native")
    if prov is None or not hasattr(prov, "graph"):
        prov = _providers["native"]
    return await asyncio.to_thread(prov.graph)  # type: ignore[attr-defined]


def _iso_to_epoch(text: str) -> float:
    """An ISO timestamp as epoch seconds; 0.0 when absent or unparseable.

    Unparseable reads as 0.0 rather than raising: a malformed `updated_at` should cost a task its
    recency tie-break, not take down the whole ready projection.
    """
    raw = (text or "").strip()
    if not raw:
        return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _rank_ready(ready: list[Task], task_map: dict[str, Task], *, now: float) -> list[Task]:
    """Order a ready set through the unified admission core (PP-13).

    This function is the whole of the impurity the core refuses to carry: it reads the wall clock
    and the lease sidecars, builds one `AdmissionState`, and hands the pure comparator a snapshot.
    `admission.ready` then applies the SAME composed policy list the engine's frontier gets — the
    three `PP-11` capacity rules abstain on a `RESOURCE` request, and `Lease` is the one that speaks
    — so the board's exclusions and the engine's are one mechanism rather than two that agree today.

    The projection asks as `admission.OBSERVER`, an identity that never takes a lease, so a task
    another holder is actively holding is excluded while one whose lease has EXPIRED is not.

    `blocks_count` is counted over the FULL task map, not the ready subset: a ready task's value
    comes from the blocked tasks waiting on it, so counting only among its ready peers would score
    every bottleneck at zero — the exact opposite of the ranking's purpose.
    """
    from personalclaw.workflows import admission, pool

    dependents: dict[str, int] = {}
    for task in task_map.values():
        for prereq in task.prerequisite_ids():
            dependents[prereq] = dependents.get(prereq, 0) + 1

    leases: dict[str, pool.Lease] = {}
    for task in ready:
        lease = pool.read_lease(task.id)
        if lease is not None:
            leases[task.id] = lease

    state = admission.AdmissionState(now=now, holder=admission.OBSERVER, leases=leases)
    policies = admission.default_policies(
        admission.Limits(), single_active_feature=False, state=state
    )
    items = [
        admission.ReadyItem(
            item_id=task.id,
            title=task.title,
            priority=task.priority.value,
            blocks_count=dependents.get(task.id, 0),
            overdue=bool(task.due) and 0.0 < _iso_to_epoch(task.due) <= now,
            updated_at=_iso_to_epoch(task.updated_at),
        )
        for task in ready
    ]
    by_id = {task.id: task for task in ready}
    return [by_id[item.item_id] for item in admission.ready(items, policies)]


async def ready_tasks(
    project: str | None = None,
    task_list_id: str | None = None,
    *,
    mine_only: bool = True,
) -> list[Task]:
    """Tasks that can be started now (no unfinished prerequisites), RANKED, optionally
    scoped to a project label or a task list.

    ``mine_only`` (default True) is the load-bearing guarantee from
    TEAM-SHARED-ENTITIES §2.1: a multi-tenant provider may return tasks assigned to
    other people, and those must never be counted or picked as the owner's work. This
    is the ONE funnel every work-selection path goes through — the ready-count
    endpoint and the agent's next-task tool both call it — so filtering here means
    nobody has to remember to filter downstream.

    Dependency readiness is still computed over the FULL set: a task of mine blocked
    by a colleague's unfinished prerequisite is genuinely not ready, and filtering
    before reconciliation would call it startable.

    The ORDER, and the exclusion of work another holder is leasing, come from the unified
    admission core (`PP-13`). Before that this funnel returned provider order — so the one
    projection every work-selection surface reads had no ranking at all, while a second,
    complete ranking sat unwired in `pool.frontier`. Ranking LAST, after the ownership filter, is
    deliberate: an excluded colleague's task must not consume a position, and its dependents are
    still counted because readiness and value are different questions.
    """
    tasks, _ = await list_all_tasks(project=project, task_list_id=task_list_id, limit=10_000)
    task_map = {t.id: t for t in tasks}
    ready_ids = set(reconcile.ready_task_ids(task_map))
    ready = [t for t in tasks if t.id in ready_ids]
    if mine_only:
        from personalclaw.identity import current_username

        owner = current_username()
        if owner:
            ready = [t for t in ready if t.belongs_to(owner)]
    now = time.time()
    return await asyncio.to_thread(_rank_ready, ready, task_map, now=now)


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
    # The same slice, so the same window (#2984) — this door reached it through its own
    # `int(body.get("limit", 50))` with no clamp at all.
    limit, offset = _page(limit, offset)
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
