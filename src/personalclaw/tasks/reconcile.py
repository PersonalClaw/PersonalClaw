"""Dependency-driven status reconciliation + DAG analysis (seam S3).

This is the provider-agnostic engine that makes the task DAG *mean* something:

* :func:`detect_cycle` / :func:`would_create_cycle` — server-authoritative cycle
  rejection on write (the client also guards, but the server is the source of
  truth). Cycles are a hard error.
* :func:`reconcile_blocked_status` — after any write that touches status or
  dependencies, re-evaluate the changed task and its transitive dependents:
  auto-block a task whose prerequisites aren't all terminal, auto-unblock one
  whose prerequisites are now all terminal. A *manual* block is never auto-cleared.
* :func:`analyze` — a :class:`DependencyAnalysis` (critical path, leaf tasks,
  completion %, detected cycles) powering the graph view.

The functions operate on an in-memory ``{id: Task}`` map; the caller (registry /
provider) is responsible for loading the map, calling these, and persisting the
returned changed set. Cancelling a prerequisite counts as terminal → it unblocks
dependents (a cancelled blocker is "resolved"). All graph walks are cycle-tolerant
so a defensive back-edge never hangs the server.

Built in P5a (#15); reused by P5b workflows (#16) and P7a project decompose (#19).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from personalclaw.tasks.models import TERMINAL_STATUSES, Task, TaskStatus


class DependencyCycleError(ValueError):
    """Raised when a dependency edge would introduce a cycle."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__("dependency cycle detected: " + " → ".join(cycle))


def _prereq_map(tasks: dict[str, Task]) -> dict[str, list[str]]:
    """task_id → list of prerequisite ids (BLOCKS edges, filtered to known tasks)."""
    return {tid: [p for p in t.prerequisite_ids() if p in tasks] for tid, t in tasks.items()}


def detect_cycle(tasks: dict[str, Task]) -> list[str]:
    """Return one cycle as an id path (``[a, b, a]``) if the BLOCKS graph has one,
    else ``[]``. DFS with a recursion stack."""
    prereqs = _prereq_map(tasks)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {tid: WHITE for tid in tasks}
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        color[node] = GRAY
        stack.append(node)
        for nxt in prereqs.get(node, []):
            if color.get(nxt, BLACK) == GRAY:
                # back-edge — slice the stack from the first occurrence
                i = stack.index(nxt)
                return stack[i:] + [nxt]
            if color.get(nxt, BLACK) == WHITE:
                found = visit(nxt)
                if found:
                    return found
        color[node] = BLACK
        stack.pop()
        return []

    for tid in tasks:
        if color[tid] == WHITE:
            found = visit(tid)
            if found:
                return found
    return []


def would_create_cycle(
    tasks: dict[str, Task], task_id: str, new_prereq_ids: list[str]
) -> list[str]:
    """Return the cycle path if giving ``task_id`` ``new_prereq_ids`` as BLOCKS
    prerequisites would create a cycle, else ``[]``. Pure check (does not mutate)."""
    prereqs = _prereq_map(tasks)
    prereqs[task_id] = [p for p in new_prereq_ids if p in tasks]
    # DFS from task_id: if we can reach task_id again, it's a cycle.
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {tid: WHITE for tid in tasks}
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        color[node] = GRAY
        stack.append(node)
        for nxt in prereqs.get(node, []):
            if color.get(nxt, BLACK) == GRAY:
                i = stack.index(nxt)
                return stack[i:] + [nxt]
            if color.get(nxt, BLACK) == WHITE:
                found = visit(nxt)
                if found:
                    return found
        color[node] = BLACK
        stack.pop()
        return []

    return visit(task_id)


def _dependents_map(tasks: dict[str, Task]) -> dict[str, list[str]]:
    """prerequisite_id → list of task ids that depend on it (reverse BLOCKS edges)."""
    rev: dict[str, list[str]] = {tid: [] for tid in tasks}
    for tid, t in tasks.items():
        for p in t.prerequisite_ids():
            if p in rev:
                rev[p].append(tid)
    return rev


def block_reason(task: Task, tasks: dict[str, Task]) -> dict:
    """Derive ``{is_blocked, blocking_task_ids, blocking_task_titles, message}``
    from the task's unfinished prerequisites."""
    unfinished = [
        p
        for p in task.prerequisite_ids()
        if p in tasks and tasks[p].status not in TERMINAL_STATUSES
    ]
    if not unfinished:
        return {
            "is_blocked": False,
            "blocking_task_ids": [],
            "blocking_task_titles": [],
            "message": "",
        }
    titles = [tasks[p].title for p in unfinished]
    return {
        "is_blocked": True,
        "blocking_task_ids": unfinished,
        "blocking_task_titles": titles,
        "message": "Waiting on: " + ", ".join(titles),
    }


def reconcile_blocked_status(tasks: dict[str, Task], changed_id: str) -> list[Task]:
    """Re-evaluate ``changed_id`` and its transitive dependents, auto-blocking /
    auto-unblocking by dependency state. Mutates the tasks in place and returns
    the set that actually changed status/blocked_reason_kind.

    Rules:
    - A task with ≥1 non-terminal prerequisite is effectively blocked.
    - auto-block: open/in_progress + unfinished prereq → blocked (kind="auto").
    - auto-unblock: blocked + no unfinished prereq + kind=="auto" → open.
    - A manual block (kind=="manual") is never auto-touched.
    - Cycle-tolerant: a visited set bounds the walk.
    """
    if changed_id not in tasks:
        return []
    dependents = _dependents_map(tasks)

    # Affected = changed task + all its transitive dependents (cycle-tolerant BFS).
    affected: list[str] = []
    seen: set[str] = set()
    queue = [changed_id]
    while queue:
        tid = queue.pop(0)
        if tid in seen:
            continue
        seen.add(tid)
        affected.append(tid)
        queue.extend(dependents.get(tid, []))

    changed: list[Task] = []
    for tid in affected:
        t = tasks[tid]
        if t.blocked_reason_kind == "manual":
            continue  # never auto-touch a manual block
        unfinished = [
            p
            for p in t.prerequisite_ids()
            if p in tasks and tasks[p].status not in TERMINAL_STATUSES
        ]
        if unfinished and t.status in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS):
            t.status = TaskStatus.BLOCKED
            t.blocked_reason_kind = "auto"
            changed.append(t)
        elif not unfinished and t.status == TaskStatus.BLOCKED and t.blocked_reason_kind == "auto":
            t.status = TaskStatus.OPEN
            t.blocked_reason_kind = ""
            changed.append(t)
    return changed


def classify_manual_block(task: Task, tasks: dict[str, Task]) -> None:
    """When a write sets a task to ``blocked``, stamp the reason kind: "manual" if
    it has no unfinished prerequisite (user is blocking for an external reason),
    else "auto". Call this for the directly-edited task before reconciling."""
    if task.status != TaskStatus.BLOCKED:
        # Leaving blocked clears the kind (handled by reconcile for auto; do manual here).
        if task.blocked_reason_kind == "manual":
            task.blocked_reason_kind = ""
        return
    unfinished = [
        p
        for p in task.prerequisite_ids()
        if p in tasks and tasks[p].status not in TERMINAL_STATUSES
    ]
    task.blocked_reason_kind = "auto" if unfinished else "manual"


def ready_task_ids(tasks: dict[str, Task]) -> list[str]:
    """Tasks that can be started now: not in a terminal state and with every
    BLOCKS prerequisite already terminal (done/cancelled). A task with no
    prerequisites is ready."""
    out: list[str] = []
    for tid, t in tasks.items():
        if t.status in TERMINAL_STATUSES:
            continue
        prereqs = [p for p in t.prerequisite_ids() if p in tasks]
        if all(tasks[p].status in TERMINAL_STATUSES for p in prereqs):
            out.append(tid)
    return out


# ── Dependency analysis (graph view) ──


@dataclass
class DependencyAnalysis:
    completion_pct: float = 0.0
    leaf_task_ids: list[str] = field(default_factory=list)  # no dependents
    root_task_ids: list[str] = field(default_factory=list)  # no prerequisites
    critical_path: list[str] = field(default_factory=list)  # longest prereq chain
    cycles: list[list[str]] = field(default_factory=list)
    # tasks with ≥2 dependents, sorted by dependent count desc: [{id, dependents}]
    bottleneck_tasks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "completion_pct": round(self.completion_pct, 1),
            "leaf_task_ids": self.leaf_task_ids,
            "root_task_ids": self.root_task_ids,
            "critical_path": self.critical_path,
            "cycles": self.cycles,
            "bottleneck_tasks": self.bottleneck_tasks,
        }


def analyze(tasks: dict[str, Task]) -> DependencyAnalysis:
    """Compute a DependencyAnalysis over the BLOCKS graph (cycle-tolerant)."""
    if not tasks:
        return DependencyAnalysis()
    prereqs = _prereq_map(tasks)
    dependents = _dependents_map(tasks)

    done = sum(1 for t in tasks.values() if t.status == TaskStatus.DONE)
    completion = 100.0 * done / len(tasks)

    leaves = [tid for tid in tasks if not dependents.get(tid)]
    roots = [tid for tid in tasks if not prereqs.get(tid)]

    cycle = detect_cycle(tasks)
    cycles = [cycle] if cycle else []

    # Longest prerequisite chain (critical path). Memoized DFS, cycle-guarded.
    best_len: dict[str, int] = {}
    best_path: dict[str, list[str]] = {}
    visiting: set[str] = set()

    def longest(node: str) -> list[str]:
        if node in best_path:
            return best_path[node]
        if node in visiting:
            return [node]  # cycle guard — don't recurse
        visiting.add(node)
        best: list[str] = []
        for p in prereqs.get(node, []):
            cand = longest(p)
            if len(cand) > len(best):
                best = cand
        path = [node] + best
        visiting.discard(node)
        best_path[node] = path
        best_len[node] = len(path)
        return path

    critical: list[str] = []
    if not cycles:
        for tid in tasks:
            cand = longest(tid)
            if len(cand) > len(critical):
                critical = cand
        critical = list(reversed(critical))  # root → … → leaf order

    # Bottlenecks: tasks ≥2 dependents block, sorted by how many depend on them.
    bottlenecks = [
        {"id": tid, "dependents": len(deps)}
        for tid, deps in sorted(dependents.items(), key=lambda kv: len(kv[1]), reverse=True)
        if len(deps) >= 2
    ]

    return DependencyAnalysis(
        completion_pct=completion,
        leaf_task_ids=leaves,
        root_task_ids=roots,
        critical_path=critical,
        cycles=cycles,
        bottleneck_tasks=bottlenecks,
    )
