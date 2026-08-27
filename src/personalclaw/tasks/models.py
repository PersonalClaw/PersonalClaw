"""Canonical task entity — the unified model across all task providers. A task carries a typed
DAG of dependencies (``dependencies``: prerequisite edges with a :class:`DependencyType`), an
exit-criteria checklist, an action plan, phased notes, and an agent-instructions template.
Status propagates along the DAG via the reconciliation service (see ``reconcile.py``):
finishing every prerequisite auto-unblocks a dependent; a manual block is never auto-cleared.
Hierarchy: a task belongs to a TaskList, which belongs to a Project (Project → TaskList →
Task). ``task_list_id`` is the structural link; ``project`` is a denormalized project-id label
kept for fast grouping/filtering.
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
    #: A branch the run declined, or work a rewind made unnecessary (TASKS-SOPS §1, R12).
    #: ONE new member, not a status explosion: the WHY of a block lives in
    #: `Task.blocked_kind`, and per-surface display labels are configuration. Measured
    #: before adding it: `from_dict` coerced an unknown status to OPEN, so a skipped task
    #: read back as work still to do — silently, on the board the user plans from.
    SKIPPED = "skipped"


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
    """Canonical exit criterion: ``{description, status, comment}``. Accepts a plain string, the
    legacy ``{description, met: bool}`` shape, or the canonical shape. ``met`` is emitted
    (derived from ``status``) so older readers keep working.
    """
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
    """Canonical action-plan item: ``{sequence, content, completed}``. Accepts a plain string,
    the legacy ``{description, completed}`` shape, or the canonical ``{sequence, content}``
    shape. ``description`` is emitted as an alias of ``content`` for older readers.
    """
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
    """Coerce exit_criteria / action_plan input to a LIST before per-item normalize. A bare
    scalar (a single criterion/step passed as a string or dict — a plausible caller/LLM
    mistake, e.g. exit_criteria="tests pass" instead of ["tests pass"]) must be wrapped:
    iterating a bare string would treat its CHARACTERS as separate items, fabricating ~N
    single-char criteria that can never be 'met' → the task is permanently un-completable.
    None/non-iterable → empty.
    """
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
    """Canonical note: ``{content, timestamp}`` (carries any legacy ``phase``/ ``created_at``
    through for back-compat readers).
    """
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
class WorkflowTaskBinding:
    """What ties a Task to the run that owns it (TASKS-SOPS §1). `managed` is the load-bearing
    flag, and it has three real configurations rather than two: * `managed=True` — the engine
    drives the status. A user write is rejected at the façade. * `managed=False` WITH a
    binding — a task the workflow PRODUCED as output. Provenance is recorded so the board can
    show where it came from, but nobody tracks its completion. * no binding at all — a
    standalone task the user owns entirely. Collapsing the middle case into "unmanaged" would
    lose the provenance; collapsing it into "managed" would make the engine responsible for
    work it only suggested.
    """

    run_id: str
    node_id: str
    node_path: str = ""
    managed: bool = True
    #: Dedup key. Per-file JSON storage means idempotency is dedup-by-lookup, not a
    #: transaction — so a resume or rewind that re-materializes has to recognize its own
    #: earlier work by content, not by hoping the write is atomic.
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "node_id": self.node_id,
            "node_path": self.node_path,
            "managed": self.managed,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkflowTaskBinding":
        d = d or {}
        return cls(
            run_id=str(d.get("run_id", "") or ""),
            node_id=str(d.get("node_id", "") or ""),
            node_path=str(d.get("node_path", "") or ""),
            # Defaults to True to match the dataclass, but an EXPLICIT false is honored. A
            # produced task whose flag was dropped on read would become engine-managed and
            # have its status overwritten by a run that never tracked it.
            managed=bool(d.get("managed", True)),
            fingerprint=str(d.get("fingerprint", "") or ""),
        )


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
    # WHO created this task, as an attribution handle (TEAM-SHARED-ENTITIES §1).
    # Additive with an empty default: pre-existing task JSON has no such key and
    # reads back as "" — no attribution, i.e. today's behavior. Distinct from
    # `assignee`, which is who should DO it.
    author: str = ""
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
    # ── workflow projection (TASKS-SOPS §1, S55) ──
    #: The run/node this task projects, when any. `None` = standalone.
    workflow_binding: "WorkflowTaskBinding | None" = None
    #: WHY a blocked task is blocked, as a field rather than a status explosion. An unknown
    #: kind degrades to a plain `blocked` badge on every surface (R12).
    blocked_kind: str = ""  # "" | needs_input | capability | transient | dependency
    #: A short human line about current state — what the node is doing right now.
    preview: str = ""
    #: The criterion the ENGINE runs to decide done. Copied from the node's verify clause at
    #: materialization, so a later spec edit cannot retroactively change what a finished task
    #: was judged against.
    done_criterion: str = ""
    #: What established completion. A done task with no evidence is a claim.
    evidence: list[dict] = field(default_factory=list)
    #: Per-attempt records for a retried node.
    attempts: list[dict] = field(default_factory=list)
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
        # The binding's OWN serializer, not `asdict`'s nested walk: one place decides its wire
        # shape, so adding a field there cannot silently skip this path.
        d["workflow_binding"] = (
            self.workflow_binding.to_dict() if self.workflow_binding is not None else None
        )
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

    def belongs_to(self, username: str) -> bool:
        """Whether this task is ``username``'s work (TEAM-SHARED-ENTITIES §2.1). Assignee decides
        when there is one; an UNASSIGNED task falls back to its author, because "I wrote it
        and nobody picked it up" is still my work. With no username configured every task
        belongs to the owner — a single-user install must behave exactly as it does today, and
        that is also the honest answer: with no identity there is nobody else for a task to
        belong to.
        """
        owner = (username or "").strip().lower()
        if not owner:
            return True
        assignee = (self.assignee or "").strip().lower()
        if assignee:
            return assignee == owner
        author = (self.author or "").strip().lower()
        # Unattributed-and-unassigned tasks are the owner's: they predate
        # attribution, so treating them as foreign would empty the counters.
        return not author or author == owner

    def can_mark_complete(self) -> bool:
        """A task may be completed only when every exit criterion is complete (a task with no
        exit criteria is freely completable).
        """
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
        """Read a persisted task. NEVER raises on a field it cannot use.

        Every field goes through `coerce_task_field(..., strict=False)` — the same table the write
        path uses, in salvage mode. That is what makes this method's tolerance a rule rather than a
        per-field accident: `order` used to be coerced with a bare `float()`, which RAISED on a
        value an unguarded `PUT` had already stored, so the task answered 404 on every read while
        its file sat on disk holding its id (#387). A record with one unusable field now loads with
        that field defaulted — visible, editable, deletable — which is also how a home poisoned
        before this fix recovers without a migration.
        """
        from_field = coerce_task_field

        # Typed dependencies, migrating any legacy flat `depends_on` list on read. `depends_on` is a
        # legacy KEY rather than a field, so it is resolved here and not in the coercion table.
        deps_raw = d.get("dependencies") or d.get("depends_on")

        return cls(
            id=from_field("id", d.get("id"), strict=False),
            title=from_field("title", d.get("title"), strict=False),
            status=from_field("status", d.get("status", "open"), strict=False),
            description=from_field("description", d.get("description"), strict=False),
            provider=from_field("provider", d.get("provider"), strict=False),
            project=from_field("project", d.get("project"), strict=False),
            task_list_id=from_field("task_list_id", d.get("task_list_id"), strict=False),
            dependencies=from_field("dependencies", deps_raw, strict=False),
            author=from_field("author", d.get("author"), strict=False),  # absent pre-attribution
            assignee=from_field("assignee", d.get("assignee"), strict=False),
            priority=from_field("priority", d.get("priority", "medium"), strict=False),
            labels=from_field("labels", d.get("labels"), strict=False),
            due=from_field("due", d.get("due"), strict=False),
            order=from_field("order", d.get("order"), strict=False),
            # Measured: `asdict` put the binding in `to_dict` while `from_dict` dropped it, so a
            # materialized task read back as STANDALONE — losing engine ownership after one
            # reload, which is exactly the state where a user's manual write would be accepted.
            # Measured: `asdict` put the binding in `to_dict` while `from_dict` dropped it, so a
            # materialized task read back as STANDALONE — losing engine ownership after one
            # reload, which is exactly the state where a user's manual write would be accepted.
            workflow_binding=from_field(
                "workflow_binding", d.get("workflow_binding"), strict=False
            ),
            blocked_kind=from_field("blocked_kind", d.get("blocked_kind"), strict=False),
            preview=from_field("preview", d.get("preview"), strict=False),
            done_criterion=from_field("done_criterion", d.get("done_criterion"), strict=False),
            evidence=from_field("evidence", d.get("evidence"), strict=False),
            attempts=from_field("attempts", d.get("attempts"), strict=False),
            exit_criteria=from_field("exit_criteria", d.get("exit_criteria"), strict=False),
            action_plan=from_field("action_plan", d.get("action_plan"), strict=False),
            notes=from_field("notes", d.get("notes"), strict=False),
            research_notes=from_field("research_notes", d.get("research_notes"), strict=False),
            execution_notes=from_field("execution_notes", d.get("execution_notes"), strict=False),
            agent_instructions_template=from_field(
                "agent_instructions_template", d.get("agent_instructions_template"), strict=False
            ),
            blocked_reason_kind=from_field(
                "blocked_reason_kind", d.get("blocked_reason_kind"), strict=False
            ),
            created_at=from_field("created_at", d.get("created_at"), strict=False),
            updated_at=from_field("updated_at", d.get("updated_at"), strict=False),
            url=from_field("url", d.get("url"), strict=False),
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


# ── The ONE coercion table for a task field ──────────────────────────────────
#
# **Why this exists.** A task field was coerced in some places and not others, in BOTH the read
# and the write path, and the inconsistency was the bug rather than any single missing check.
# `from_dict` coerced `order` with `float()` and `preview` with `str()`, but took `title`,
# `description` and `labels` verbatim. `update_task` had typed branches for `status`,
# `dependencies` and `priority`, and for everything else a catch-all
# `setattr(task, key, val)` with no check at all. Measured consequences, each its own report:
#
#   * `PUT {"order": "abc"}` → 200, then `float("abc")` raised on every read, so the task
#     answered 404 everywhere while its file stayed on disk (#387).
#   * `labels: "not-an-array"` persisted as a bare string through BOTH create and update, and
#     `labels.slice(...).map` took the whole Tasks page into an error boundary (#386).
#   * `PUT {"description": 12345}` → `(12345).lower()` in the search scorer, so
#     `POST /api/tasks/search` answered 500 for EVERY query, not only ones that would match
#     the poisoned task (#388).
#   * `PUT {"exit_criteria": "via put"}` iterated the string CHARACTER BY CHARACTER, fabricating
#     one criterion per letter — each un-meetable, so the task became permanently
#     un-completable. `__post_init__` guards exactly this, and only at construction (#818).
#
# So the coercion is a TABLE, exhaustive over `Task`'s fields, and both paths go through it.
# `tests/test_task_field_coercion.py` asserts the exhaustiveness, which is what makes this
# extensible: a field added to `Task` without a coercer reds rather than becoming the next
# field nobody coerced.
#
# **Read salvages, write refuses.** The same function serves both, because two functions would
# drift — but the two paths want opposite things from a value they cannot use:
#
#   * a WRITE must refuse it (`strict=True` → `ValueError` → the handler's 400). Accepting junk
#     is what created every case above.
#   * a READ must salvage it (`strict=False` → the field's default). "A broken row never
#     disappears" is this store's stated rule, and a record that 404s is worse than one with a
#     defaulted field: you cannot fix, or even see, what the API says is absent. This is also
#     what recovers a task ALREADY poisoned by the bug, with no migration — the `order: "abc"`
#     record loads with `order: 0.0` and is editable and deletable again.


def _as_text(value: Any, *, strict: bool) -> str:
    """A scalar becomes its text; a CONTAINER is refused.

    `str({"a": 1})` is `"{'a': 1}"` — technically a string and never what anyone meant, and it is
    what put an object where the frontend expected a label (React error #31). A number, though, is
    a plausible slip with an obvious reading: `description=12345` means `"12345"`.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if strict:
        raise ValueError(f"expected text, got {type(value).__name__}")
    return ""


def _as_number(value: Any, *, strict: bool) -> float:
    """A number, or a refusal. Never a silently-stored string.

    This is `order`, and it is the field that proved the point: an unparseable value written by a
    200 made every later read raise inside a bare `except`, so the task was simultaneously absent
    (404) and present (on disk, holding its id).
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):  # bool is an int subclass; `order: true` is not an ordering
        if strict:
            raise ValueError("expected a number, got a boolean")
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        if strict:
            raise ValueError(f"expected a number, got {value!r}") from None
        return 0.0


def _as_text_list(value: Any, *, strict: bool) -> list[str]:
    """A list of text. A bare scalar is WRAPPED, never iterated.

    Wrapping matches `_as_item_list`'s rule for the planning fields and for the same reason: a
    caller passing one label as a string means one label, and iterating it would produce one entry
    per character. Elements are coerced individually, so `[{"a": 1}, 42]` cannot reach a renderer
    that expects strings.
    """
    if value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        return [_as_text(value, strict=strict)]
    if isinstance(value, dict):
        if strict:
            raise ValueError("expected a list of text, got an object")
        return []
    try:
        items = list(value)
    except TypeError:
        if strict:
            raise ValueError(f"expected a list of text, got {type(value).__name__}")
        return []
    return [_as_text(v, strict=strict) for v in items]


def _as_dict_list(normalizer: Any, *, indexed: bool = False) -> Any:
    """Build a coercer for a list-of-dict field from its existing per-item normalizer.

    Deliberately reuses `normalize_exit_criterion` / `normalize_action_plan_item` /
    `normalize_note` rather than restating their shapes: those ARE the canonical forms, they
    already accept the legacy spellings, and a second opinion here would let the read and the
    write disagree about what a note is.
    """

    def _coerce(value: Any, *, strict: bool) -> list[dict]:
        items = _as_item_list(value)
        if indexed:
            return [normalizer(item, i) for i, item in enumerate(items)]
        return [normalizer(item) for item in items]

    return _coerce


def _as_open_dict_list(value: Any, *, strict: bool) -> list[dict]:
    """A list of dicts whose SHAPE this module does not own.

    `evidence` and `attempts` carry whatever the engine records — `{"kind": "gate", "node": …}`
    for a gate, a per-attempt record for a retry — so they get the list-ification every planning
    field gets and nothing more. Normalizing them the way notes are normalized DESTROYS the record:
    the first draft of this table routed them through `normalize_note` and
    `test_the_ENGINE_completion_path_persists_evidence` caught it, rewriting
    `{"kind": "gate", "node": "check"}` to `{"content": "", "timestamp": ""}`.

    Which is the general point: a coercion table is only safe where the shape is actually known.
    """
    items = _as_item_list(value)
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
        elif strict:
            raise ValueError(f"expected a list of objects, got {type(item).__name__}")
    return out


def _as_dependencies(value: Any, *, strict: bool) -> list["TaskDependency"]:
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        value = [value]
    out: list[TaskDependency] = []
    try:
        items = list(value)
    except TypeError:
        if strict:
            raise ValueError("expected a list of dependencies")
        return []
    for item in items:
        if isinstance(item, TaskDependency):
            out.append(item)
        elif isinstance(item, dict):
            out.append(TaskDependency.from_dict(item))
        elif isinstance(item, str) and item.strip():
            # The legacy flat `depends_on` spelling: a bare id means a BLOCKS edge.
            out.append(
                TaskDependency(depends_on_task_id=item, dependency_type=DependencyType.BLOCKS)
            )
        elif strict:
            raise ValueError(f"not a dependency: {item!r}")
    return out


def _as_status(value: Any, *, strict: bool) -> "TaskStatus":
    if isinstance(value, TaskStatus):
        return value
    try:
        return TaskStatus(value)
    except ValueError:
        if strict:
            raise ValueError(
                f"invalid status {value!r} — use one of: " + ", ".join(s.value for s in TaskStatus)
            ) from None
        return TaskStatus.OPEN


def _as_priority(value: Any, *, strict: bool) -> "TaskPriority":
    return value if isinstance(value, TaskPriority) else TaskPriority.normalize(value)


def _as_binding(value: Any, *, strict: bool) -> "WorkflowTaskBinding | None":
    if value is None:
        return None
    if isinstance(value, WorkflowTaskBinding):
        return value
    if isinstance(value, dict):
        return WorkflowTaskBinding.from_dict(value)
    if strict:
        raise ValueError("workflow_binding must be an object or null")
    return None


#: EXHAUSTIVE over `Task`'s fields — `tests/test_task_field_coercion.py` asserts it.
TASK_FIELD_COERCERS: dict[str, Any] = {
    "id": _as_text,
    "title": _as_text,
    "status": _as_status,
    "description": _as_text,
    "provider": _as_text,
    "project": _as_text,
    "task_list_id": _as_text,
    "dependencies": _as_dependencies,
    "author": _as_text,
    "assignee": _as_text,
    "priority": _as_priority,
    "labels": _as_text_list,
    "due": _as_text,
    "order": _as_number,
    "exit_criteria": _as_dict_list(normalize_exit_criterion),
    "action_plan": _as_dict_list(normalize_action_plan_item, indexed=True),
    "notes": _as_dict_list(normalize_note),
    "research_notes": _as_dict_list(normalize_note),
    "execution_notes": _as_dict_list(normalize_note),
    "agent_instructions_template": _as_text,
    "blocked_reason_kind": _as_text,
    "workflow_binding": _as_binding,
    "blocked_kind": _as_text,
    "preview": _as_text,
    "done_criterion": _as_text,
    "evidence": _as_open_dict_list,
    "attempts": _as_open_dict_list,
    "created_at": _as_text,
    "updated_at": _as_text,
    "url": _as_text,
}


def coerce_task_field(name: str, value: Any, *, strict: bool = True) -> Any:
    """Coerce one task field to its declared type. The ONE place that decides.

    `strict=True` (a WRITE) raises `ValueError` on a value it cannot use, which the handlers
    already map to a 400. `strict=False` (a READ) falls back to the field's default so a record
    already written by an unguarded caller still loads — see the section comment above.

    An unknown field name is always a `ValueError`: `update_task` used `hasattr` to decide what to
    set, so a typo'd key silently did nothing, and a caller could not tell a rejected edit from an
    applied one.
    """
    coercer = TASK_FIELD_COERCERS.get(name)
    if coercer is None:
        raise ValueError(f"unknown task field {name!r}")
    return coercer(value, strict=strict)


@dataclass
class Project:
    """A first-class work unit at the top of the hierarchy. A project ties together everything a
    user does on one logical effort — Goal Loops, Code projects, manually-created Tasks (and
    optionally Artifacts) — and owns a **context directory** (``projects/<id>/``) where that
    context consolidates for continuation across features and sessions. It MAY bind an
    existing ``workspace_dir`` (a codebase on disk); when bound, the project's per-workspace
    git worktrees live under its context dir so several projects can operate on one workspace
    without colliding. With no workspace bound, the context dir itself is the working area.
    Names are unique and LLM-generated/maintained until the user renames manually
    (``name_locked``). ``Personal``/``Repeatable`` are protected defaults.
    """

    id: str
    name: str
    is_builtin: bool = False
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

    def is_builtin_project(self) -> bool:
        return self.is_builtin or self.name in DEFAULT_PROJECTS

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_builtin"] = self.is_builtin_project()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        name = d.get("name", "")
        return cls(
            id=d.get("id", ""),
            name=name,
            is_builtin=bool(d.get("is_builtin", d.get("is_default", False)))
            or name in DEFAULT_PROJECTS,
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
    """A mid-level container belonging to a :class:`Project`. Tasks belong to a task list
    (``Task.task_list_id``); the list belongs to a project (``project_id``).
    """

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
