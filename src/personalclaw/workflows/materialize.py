"""Tasks as a projection of run state: materialization, dedup, caps (TASKS-SOPS §1 — S55). A
materialized Task is a VIEW of a node, not a second copy of the truth. That single sentence
decides everything here: * **The engine owns the status of a managed task**, so a direct user
write is rejected at the write façade rather than merged. Two writers on one status field
produce a board that disagrees with the run it is showing, and the user believes the board. *
**Dedup is by content, not by transaction.** Per-file JSON storage gives no atomic check-and-
create, so a resume or rewind that re-materializes has to recognize its own earlier work. The
fingerprint is that recognition. * **A fan-out gets a cap, not a task per item.** Twenty
parallel leaves is a readable board; two hundred is a board nobody opens, and the collapse-to-
a-counter is what keeps the surface useful rather than complete. The projection table is the
other half. `TaskStatus` gains exactly ONE member (`SKIPPED`) and the WHY of a block lives in
`blocked_kind`, because a status per reason is a state fork every surface then has to re-
implement — and the surface that forgets is the one that shows a stale column. Pure functions
over node/instance state. The registry call is the caller's: `plan_materialization` decides
what should exist, so the rules are testable without a task store on disk.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from personalclaw.tasks.models import TaskStatus, WorkflowTaskBinding
from personalclaw.workflows.models import InstanceState

#: Materialized tasks per fan-out before the board collapses them into a parent with a counter.
#: Twenty is a readable column; two hundred is a column nobody opens, and a board that is complete
#: but unreadable is worse than one that says "18 of 200 items, 3 blocked".
FANOUT_TASK_CAP = 20

#: Node kinds that do NOT earn a task by default. A container is a scheduling policy, and a
#: zero-token transform is plumbing — a board row for either is a row the user cannot act on, and
#: every unactionable row makes the actionable ones harder to find.
NON_MATERIALIZING_KINDS = frozenset(
    {"sequence", "parallel", "foreach", "loop", "branch", "transform", "wait"}
)

#: The node-config opt-out. Named on the node rather than inferred, because "this stage is internal"
#: is an author's judgement and no heuristic gets it right for a helper judge.
OPT_OUT_KEY = "materialize_task"


#: How a node instance's state projects onto a task status. The table is DATA, not a chain of ifs,
#: so it can be read as a contract and asserted exhaustively — a missing case in an if-chain is a
#: silent fallthrough, and the fallthrough here would report real work as OPEN.
STATE_TO_STATUS: dict[InstanceState, TaskStatus] = {
    InstanceState.PENDING: TaskStatus.OPEN,
    InstanceState.READY: TaskStatus.OPEN,
    InstanceState.RUNNING: TaskStatus.IN_PROGRESS,
    InstanceState.WAITING: TaskStatus.BLOCKED,
    InstanceState.DONE: TaskStatus.DONE,
    InstanceState.DEGRADED: TaskStatus.DONE,
    InstanceState.FAILED: TaskStatus.BLOCKED,
    InstanceState.SKIPPED: TaskStatus.SKIPPED,
    InstanceState.CANCELLED: TaskStatus.CANCELLED,
    # The five states an earlier version of this table MISSED, each of which fell through to
    # OPEN —
    # so a tripped circuit breaker, a scope violation and a protocol-violation block all read on the
    # board as ordinary work still to do. Filled from the engine's OWN classification rather than
    # guessed: `SUCCESS_STATES` contains `no_change`, and `TERMINAL_STATES` contains the other four.
    #
    #: A node that inherited prior results — the engine counts it as SUCCESS, so the task is done.
    InstanceState.NO_CHANGE: TaskStatus.DONE,
    #: The circuit breaker tripped. Terminal and NOT success: the work stopped without finishing.
    InstanceState.ESCALATED: TaskStatus.BLOCKED,
    #: Wrote outside its declared scope. Terminal, and a block the user has to look at.
    InstanceState.SCOPE_VIOLATION: TaskStatus.BLOCKED,
    #: A protocol violation — "never a silent hang", per the engine's own comment.
    InstanceState.BLOCKED: TaskStatus.BLOCKED,
    #: A rewind discarded this instance. SKIPPED rather than cancelled: the run moved past it, which
    #: is the same thing a declined branch means to someone reading the board.
    InstanceState.DISCARDED: TaskStatus.SKIPPED,
}

#: Failure classes mapped to the WHY of a block. A `blocked_kind` the surface does not recognize
#: degrades to a plain `blocked` badge — which is why an unknown class is safe to pass through
#: rather than something to normalize away.
FAILURE_TO_BLOCKED_KIND = {
    "permission": "capability",
    "budget": "capability",
    "transient": "transient",
    "network": "transient",
    "timeout": "transient",
}


def project_status(state: InstanceState) -> TaskStatus:
    """One node state as a task status. A state absent from the table returns OPEN rather than
    raising: a projection that crashed on a thirteenth engine state would take down the board
    for every task, and OPEN is the reading that keeps the work visible. The exhaustiveness
    test is what keeps that fallback from being load-bearing.
    """
    return STATE_TO_STATUS.get(state, TaskStatus.OPEN)


def project_blocked_kind(
    state: InstanceState, *, failure_class: str = "", waiting_on_human: bool = False
) -> str:
    """The WHY of a block, or "" when the task is not blocked. `DEGRADED` deliberately projects
    to DONE with no blocked kind: a degraded node SUCCEEDED with a machine-readable reason,
    and filing it as blocked would put completed work in the column the user scans for
    problems.
    """
    if project_status(state) is not TaskStatus.BLOCKED:
        return ""
    if waiting_on_human:
        return "needs_input"
    if state is InstanceState.WAITING:
        # Waiting with no human ask is a dependency wait — the node is fine, its inputs are not.
        return "dependency"
    # The engine states that carry their OWN reason. A failure class would tell you nothing extra
    # here: a tripped breaker is a breaker, and a scope violation is not "transient".
    if state is InstanceState.ESCALATED:
        return "capability"
    if state in (InstanceState.SCOPE_VIOLATION, InstanceState.BLOCKED):
        return "needs_input"
    return FAILURE_TO_BLOCKED_KIND.get((failure_class or "").strip().lower(), "")


def fingerprint(*, source_ref: str = "", title: str = "", body: str = "") -> str:
    """The dedup key: `sha1(source_ref or title+body)[:16]`. A `source_ref` wins when present
    because it is stable across a re-worded title — a rewind that re-materialized a node whose
    label had been edited would otherwise create a second task for one piece of work, and the
    board would show it twice. Truncated to 16 hex chars: this is a dedup key inside one run's
    task list, not a security digest, and a full hash makes the stored record harder to
    eyeball for no gain.
    """
    basis = (source_ref or "").strip() or f"{(title or '').strip()}\n{(body or '').strip()}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]  # noqa: S324 — dedup, not crypto


def should_materialize(node: dict[str, Any]) -> tuple[bool, str]:
    """Whether a node earns a task, and why not when it does not. Three refusals, in order of
    authority: an explicit opt-out (the author decided), a container or zero-token kind (a
    board row nobody can act on), and a node with no id (a task that cannot be addressed by
    the engine that owns it).
    """
    cfg = node.get("config") or {}
    if cfg.get(OPT_OUT_KEY) is False:
        return False, f"node declares {OPT_OUT_KEY}: false"
    kind = str(node.get("kind", "") or "")
    if kind in NON_MATERIALIZING_KINDS:
        return False, f"{kind} nodes produce no actionable work of their own"
    if not str(node.get("id", "") or "").strip():
        return False, "node has no id, so the engine could not address its task"
    return True, ""


@dataclass
class TaskSpec:
    """One task the projection says should exist. `body` is behavior-first by contract: what to
    build, acceptance checkboxes, blocked-by. File paths and code snippets are prohibited
    because they go stale — the exception being decision-rich artifacts (schemas, state
    machines, type shapes), which are the thing a reader cannot reconstruct.
    """

    title: str
    binding: WorkflowTaskBinding
    body: str = ""
    done_criterion: str = ""
    status: TaskStatus = TaskStatus.OPEN
    blocked_kind: str = ""
    preview: str = ""

    def to_fields(self) -> dict[str, Any]:
        """The kwargs for `registry.create_task`. Goes through the FAÇADE, so a non-native task
        provider keeps working rather than being bypassed by a direct native write.
        """
        return {
            "title": self.title,
            "description": self.body,
            "status": self.status.value,
            "workflow_binding": self.binding,
            "done_criterion": self.done_criterion,
            "blocked_kind": self.blocked_kind,
            "preview": self.preview,
        }


#: Body sections, in the order a reader needs them. Behavior first: someone picking up the
#: task needs
#: to know what it IS before what proves it, and a body that opened with acceptance criteria
#: reads as
#: a checklist for work nobody described.
BODY_SECTIONS = ("what to build", "acceptance", "blocked by")

#: Patterns a body must not contain. File paths and code snippets go stale the moment the tree
#: moves,
#: and a task body that confidently names a moved file is worse than one that says nothing — the
#: reader trusts it and looks in the wrong place.
_STALE_BODY_MARKERS = ("```", ".py:", ".ts:", "/src/", "line ")


def body_issues(body: str) -> list[str]:
    """Lint a task body against the §1 contract. Advisory, and it says why. Advisory rather than
    refusing: a body with a code snippet is still a body, and dropping the task to enforce a
    formatting rule would lose the work. But it is reported, because the staleness is real and
    the author is the only one who can fix it.
    """
    text = body or ""
    issues: list[str] = []
    lowered = text.lower()
    for marker in _STALE_BODY_MARKERS:
        if marker in lowered:
            issues.append(
                f"body contains {marker!r} — file paths and code snippets go stale, "
                "and a body that confidently names a moved file sends the reader to "
                "the wrong place"
            )
            break
    if text.strip() and "acceptance" not in lowered:
        issues.append(
            "body has no acceptance section — without one, 'done' is whatever the reader decides"
        )
    return issues


def build_body(what: str, acceptance: list[str], blocked_by: list[str] | None = None) -> str:
    """Assemble a §1-shaped body. Acceptance criteria render as checkboxes because a checkbox is
    a thing a person can tick and a sentence is not — the `done_criterion` the engine runs is
    a separate machine check, and the two are deliberately not the same field.
    """
    parts = [f"**What to build**\n\n{what.strip()}"]
    if acceptance:
        checks = "\n".join(f"- [ ] {c.strip()}" for c in acceptance if c.strip())
        parts.append(f"**Acceptance**\n\n{checks}")
    if blocked_by:
        names = "\n".join(f"- {b}" for b in blocked_by if b)
        parts.append(f"**Blocked by**\n\n{names}")
    return "\n\n".join(parts)


@dataclass
class MaterializationPlan:
    """What to create, what already exists, and what was capped. `existing` is returned rather
    than silently skipped: a resume that reported "0 tasks created" with no further detail is
    indistinguishable from a resume that failed to materialize anything.
    """

    create: list[TaskSpec] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    capped: int = 0
    cap_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "create": [s.to_fields() for s in self.create],
            "existing": list(self.existing),
            "skipped": list(self.skipped),
            "capped": self.capped,
            "cap_note": self.cap_note,
        }


def plan_materialization(
    run_id: str,
    nodes: list[dict[str, Any]],
    *,
    existing_tasks: list[Any] | None = None,
    cap: int = FANOUT_TASK_CAP,
) -> MaterializationPlan:
    """Decide which nodes need a task, deduping against what already exists. Dedup runs on TWO
    keys, and both are needed. `(run_id, node_id)` catches the same node being re-
    materialized; the FINGERPRINT catches the same work arriving under a different node id —
    which is what a rewind-then-replan produces. Checking only the first would duplicate the
    work; checking only the second would collide two genuinely different nodes whose titles
    happen to match.
    """
    plan = MaterializationPlan()
    seen_pairs: set[tuple[str, str]] = set()
    seen_prints: set[str] = set()
    for task in existing_tasks or []:
        binding = getattr(task, "workflow_binding", None)
        if binding is None:
            continue
        seen_pairs.add((binding.run_id, binding.node_id))
        if binding.fingerprint:
            seen_prints.add(binding.fingerprint)

    for node in nodes:
        ok, why = should_materialize(node)
        node_id = str(node.get("id", "") or "")
        if not ok:
            plan.skipped.append(f"{node_id or '<no id>'}: {why}")
            continue
        cfg = node.get("config") or {}
        title = str(cfg.get("label") or node_id)
        print_key = fingerprint(
            source_ref=str(node.get("source_ref", "") or ""),
            title=title,
            body=str(cfg.get("prompt", "") or ""),
        )
        if (run_id, node_id) in seen_pairs:
            plan.existing.append(f"{node_id}: already materialized for this run")
            continue
        if print_key in seen_prints:
            plan.existing.append(f"{node_id}: fingerprint {print_key} already materialized")
            continue

        if len(plan.create) >= max(1, cap):
            plan.capped += 1
            continue

        seen_pairs.add((run_id, node_id))
        seen_prints.add(print_key)
        plan.create.append(
            TaskSpec(
                title=title,
                binding=WorkflowTaskBinding(
                    run_id=run_id,
                    node_id=node_id,
                    node_path=str(node.get("path", "") or ""),
                    managed=True,
                    fingerprint=print_key,
                ),
                body=str(cfg.get("task_body", "") or ""),
                done_criterion=str(cfg.get("done_means", "") or ""),
            )
        )
    if plan.capped:
        plan.cap_note = (
            f"{len(plan.create)} of {len(plan.create) + plan.capped} items materialized "
            f"(cap {cap}); the rest are represented by the parent node's counter — a board that is "
            "complete but unreadable is worse than one that says how much it is not showing"
        )
    return plan


def managed(task: Any) -> bool:
    """Whether the engine owns this task's status. A task with NO binding is not managed, and a
    binding with `managed=False` is not either — that is the produced-task case, where the
    workflow created the work but tracks nothing about it.
    """
    binding = getattr(task, "workflow_binding", None)
    return bool(binding is not None and binding.managed)


#: Fields a user may never write directly on a managed task. The status is the obvious one; the rest
#: are engine projections, and a user edit to `evidence` would be a human asserting the machine's
#: finding.
ENGINE_OWNED_FIELDS = frozenset(
    {"status", "blocked_kind", "preview", "done_criterion", "evidence", "attempts"}
)


def reject_write(task: Any, fields: dict[str, Any]) -> str:
    """Why this write must be refused, or "" when it may proceed. Refused rather than merged. Two
    writers on one status field produce a board that disagrees with the run it is showing, and
    the user believes the board. The message names the ALTERNATIVE, because a refusal that
    does not say what to do instead reads as the feature being broken.
    """
    if not managed(task):
        return ""
    attempted = sorted(set(fields or {}) & ENGINE_OWNED_FIELDS)
    if not attempted:
        return ""
    binding = getattr(task, "workflow_binding", None)
    run_id = getattr(binding, "run_id", "") or "the owning run"
    return (
        f"{', '.join(attempted)} on this task {'is' if len(attempted) == 1 else 'are'} driven by "
        f"run {run_id} — use workflow_skip or workflow_rewind to change what the run does, and the "
        "task will follow. A direct write would make the board disagree with the run it shows."
    )


def progress_line(done: int, total: int, blocked: int = 0) -> str:
    """The parent-with-counter line a capped fan-out collapses into. Names the blocked count
    separately from the incomplete count, because "18 of 200" and "18 of 200, 3 blocked" call
    for different actions and the first hides the second.
    """
    if total <= 0:
        return ""
    line = f"{done} of {total} complete"
    if blocked:
        line += f", {blocked} blocked"
    return line
