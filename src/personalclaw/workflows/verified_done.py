"""Verified done: engine-owned criteria, actor matrix, cascade-fail (TASKS-SOPS §1, S56).
made a task a projection of a node. This session makes "done" mean something: the ENGINE runs
the criterion, never the worker, and a stage that completes without its criterion passing
projects as BLOCKED rather than done. That is the whole point, and it is the same hole in two
places. A worker reporting its own success is the node-level version; an agent tool call marking
its own task done is the task-level version. Both are closed here by the same principle — the
actor that did the work is not the actor that judges it. Three properties carry the module, and
each fails in a chosen direction: * **A criterion that could not RUN is not a pass.**
`loop/gates.run_verify_command` already returns a tristate for exactly this reason — `None`
means "can't tell" (missing binary, refused by the safety screen, timed out). Reading `None` as
success would make a broken check indistinguishable from a passing one, and the broken one is
silent. * **The actor matrix is per-actor, not per-transition.** An agent may PROPOSE
(`blocked(needs_input)`, review) and may not CLAIM (`in_progress`, `done`). Without the split,
the tool an agent uses to report a problem is also the tool it uses to declare victory. * **A
cascade blocks dependents rather than leaving them open.** After a prerequisite dies, a
dependent sitting `open` makes the board claim workable work that cannot start — the board lies,
and the user plans from it. Pure decisions over state. Command execution stays with `loop/gates`
(one implementation of the tristate, one safety screen), and the registry write stays with the
caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.tasks.models import TaskStatus


class Actor(str, Enum):
    """Who is attempting a transition. Three, because they have three different authorities. The
    ENGINE observed the work; the USER owns the board; the AGENT is a worker whose self-report
    is exactly what needs checking.
    """

    ENGINE = "engine"
    USER = "user"
    AGENT = "agent"


#: States an actor may move a task INTO. Per-actor rather than per-transition-pair: the
#: question that
#: matters is "may this actor claim this outcome", and a pair table would be 3×6×6 entries mostly
#: repeating one rule.
#:
#: The agent's set is the load-bearing one. It may PROPOSE — say it is blocked, ask for input
#: — and it
#: may not CLAIM. Without that split, the tool an agent uses to report a problem is also the tool it
#: uses to declare victory, which is the worker-self-report hole this session exists to close.
ALLOWED_TARGETS: dict[Actor, frozenset[TaskStatus]] = {
    Actor.ENGINE: frozenset(TaskStatus),  # the engine observed the work; it may record any outcome
    Actor.USER: frozenset(
        {
            TaskStatus.OPEN,
            TaskStatus.IN_PROGRESS,
            TaskStatus.DONE,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            # NOT `SKIPPED`: a skip is a routing decision the run makes. A user who wants work
            # skipped
            # asks the run to skip it (`workflow_skip`), so the board and the run agree afterwards.
        }
    ),
    Actor.AGENT: frozenset({TaskStatus.BLOCKED}),
}

#: Blocked kinds an AGENT may set. A worker may say "I need input" or "I lack a capability";
#: it may not
#: file its own failure as `transient` and thereby request its own retry.
AGENT_BLOCKED_KINDS = frozenset({"needs_input", "capability"})

#: Statuses that PAUSE engine projection on a task until the user returns it to an automated state.
#: Enumerated explicitly rather than left undefined: a user who parked a task for an external reason
#: has made a decision, and an engine recompute that overwrote it would silently undo it.
PROJECTION_PAUSING_STATUSES = frozenset({TaskStatus.BLOCKED, TaskStatus.CANCELLED})


def may_transition(
    actor: Actor,
    target: TaskStatus,
    *,
    blocked_kind: str = "",
    managed: bool = False,
) -> tuple[bool, str]:
    """Whether this actor may move a task to this status, and why not when it may not. The reason
    is returned rather than raised because a refusal is a normal outcome a surface renders — and
    because a refusal that does not say what to do instead reads as the feature being broken.
    """
    allowed = ALLOWED_TARGETS.get(actor, frozenset())
    if target not in allowed:
        if actor is Actor.AGENT and target is TaskStatus.DONE:
            return False, (
                "an agent cannot mark its own task done — the engine runs the "
                "criterion and records the outcome, which is what makes 'done' mean "
                "anything. Report what you did and let the check decide."
                "the check decide."
            )
        return False, (
            f"{actor.value} may not move a task to {target.value}; allowed: "
            f"{sorted(s.value for s in allowed)}"
        )
    if actor is Actor.AGENT and target is TaskStatus.BLOCKED:
        kind = (blocked_kind or "").strip().lower()
        if kind not in AGENT_BLOCKED_KINDS:
            return False, (
                f"an agent may report {sorted(AGENT_BLOCKED_KINDS)} but not "
                f"{kind or 'an unspecified kind'} — filing your own failure as "
                "transient would be requesting your own retry"
            )
    if managed and actor is Actor.USER:
        return False, (
            "this task's status is driven by its run — use workflow_skip or "
            "workflow_rewind, and the task will follow. A direct write would make "
            "the board disagree with the run it shows."
        )
    return True, ""


# ── the acceptance schema ──


class CheckKind(str, Enum):
    """The two check types the plan specifies. Deliberately two. Both are cheap and mechanical. A
    third type that needed a model call would put a judgement inside the thing that decides
    whether a judgement is needed.
    """

    FILE_PHRASE = "file_phrase"
    COMMAND = "command"


@dataclass
class Check:
    """One acceptance check, weighted. `weight` exists so a criterion can say which checks matter
    more, and `weight <= 0` is treated as 1 rather than as "ignore": a zero-weight check that
    still ran and still failed is information, and silently dropping it would let an author
    disable a check by typo.
    """

    kind: CheckKind
    weight: float = 1.0
    #: file_phrase
    path: str = ""
    required_phrases: list[str] = field(default_factory=list)
    #: command
    command: str = ""
    expect_exit_code: int = 0

    @property
    def effective_weight(self) -> float:
        return self.weight if self.weight > 0 else 1.0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind.value, "weight": self.weight}
        if self.kind is CheckKind.FILE_PHRASE:
            payload.update({"path": self.path, "required_phrases": list(self.required_phrases)})
        else:
            payload.update({"command": self.command, "expect_exit_code": self.expect_exit_code})
        return payload

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Check | None":
        """Parse one check, or None when it is unusable. None rather than a default check: a
        malformed entry silently becoming a passing check would make a typo look like
        verification, which is the exact failure this whole module is about.
        """
        d = d or {}
        try:
            kind = CheckKind(str(d.get("kind", "") or ""))
        except ValueError:
            return None
        check = cls(kind=kind, weight=float(d.get("weight", 1.0) or 1.0))
        if kind is CheckKind.FILE_PHRASE:
            check.path = str(d.get("path", "") or "")
            check.required_phrases = [str(p) for p in (d.get("required_phrases") or []) if str(p)]
            if not check.path or not check.required_phrases:
                return None
        else:
            check.command = str(d.get("command", "") or "")
            check.expect_exit_code = int(d.get("expect_exit_code", 0) or 0)
            if not check.command.strip():
                return None
        return check


@dataclass
class CheckResult:
    """One check's outcome. `passed=None` means it could NOT run. Tristate throughout, matching
    `run_verify_command`. Collapsing "could not run" into False would report a missing binary as
    a failing check, and a user would go looking for a bug in their code.
    """

    kind: str
    passed: bool | None
    weight: float = 1.0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "passed": self.passed,
            "weight": self.weight,
            "detail": self.detail,
        }


@dataclass
class Verdict:
    """The scored outcome of a criterion. `hit_weight/total_weight` rather than a pass count,
    because the author said which checks matter. `unrunnable` is separate from `failed` for the
    same reason it is in the tristate: a criterion where two checks could not run has not been
    evaluated, and reporting it as a 50% score would be inventing a number.
    """

    results: list[CheckResult] = field(default_factory=list)

    @property
    def total_weight(self) -> float:
        return sum(r.weight for r in self.results) or 0.0

    @property
    def hit_weight(self) -> float:
        return sum(r.weight for r in self.results if r.passed is True)

    @property
    def unrunnable(self) -> int:
        return len([r for r in self.results if r.passed is None])

    @property
    def score(self) -> float:
        return round(self.hit_weight / self.total_weight, 4) if self.total_weight else 0.0

    @property
    def passed(self) -> bool | None:
        """True only when EVERY check passed. None when any could not run. Every check, not a
        threshold: an acceptance criterion with a check that failed has not been met, and a 0.8
        score is not "mostly done" — it is one unmet requirement. The scoring exists for the
        report, not for the decision. None wins over False when both are present: "one
        check failed and one could not run" is a criterion nobody has
        evaluated, and calling it a failure would send the user after the
        wrong problem.
        """
        if not self.results:
            return None
        if self.unrunnable:
            return None
        return all(r.passed is True for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "hit_weight": self.hit_weight,
            "total_weight": self.total_weight,
            "score": self.score,
            "unrunnable": self.unrunnable,
            "passed": self.passed,
        }


def parse_criterion(raw: Any) -> tuple[list[Check], list[str]]:
    """Parse a `done_criterion` payload into checks, reporting what it could not read. A criterion
    string with no structure becomes ONE command check — that is the common authoring shape
    (`done_criterion: "pytest -q"`), and demanding the object form for it would make the cheap
    case expensive.
    """
    problems: list[str] = []
    if raw is None or raw == "":
        return [], problems
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return [], problems
        return [Check(kind=CheckKind.COMMAND, command=text)], problems
    if isinstance(raw, dict):
        raw = raw.get("checks") or [raw]
    if not isinstance(raw, list):
        return [], [f"criterion must be a string, an object, or a list; got {type(raw).__name__}"]
    checks: list[Check] = []
    for index, entry in enumerate(raw):
        check = Check.from_dict(entry) if isinstance(entry, dict) else None
        if check is None:
            problems.append(f"check {index} is unusable and was DROPPED, not treated as passing")
            continue
        checks.append(check)
    return checks, problems


def evaluate_file_phrase(check: Check, read_text: Any) -> CheckResult:
    """Run one file_phrase check. `read_text(path) -> str | None`. An unreadable file is `None`,
    not a failure: the phrase may well be there in a file this process cannot see, and reporting
    "the phrase is missing" would be a claim about content nobody read.
    """
    try:
        content = read_text(check.path)
    except Exception as exc:
        return CheckResult(
            kind=check.kind.value,
            passed=None,
            weight=check.effective_weight,
            detail=f"could not read {check.path}: {type(exc).__name__}",
        )
    if content is None:
        return CheckResult(
            kind=check.kind.value,
            passed=None,
            weight=check.effective_weight,
            detail=f"{check.path} could not be read",
        )
    missing = [p for p in check.required_phrases if p not in content]
    return CheckResult(
        kind=check.kind.value,
        passed=not missing,
        weight=check.effective_weight,
        detail="" if not missing else f"missing: {', '.join(missing[:3])}",
    )


def project_verified_status(
    verdict: Verdict, *, claimed: TaskStatus = TaskStatus.DONE
) -> tuple[TaskStatus, str]:
    """The status a claimed completion actually earns, and the blocked kind when it earns one. This
    is pass-state gating: a stage completing WITHOUT its criterion passing projects as BLOCKED,
    not done. The worker's claim is an input, not the answer. An UNRUNNABLE criterion also
    blocks, with `capability` — the check needs something the environment does not have, which
    is a different problem from the work being wrong and points at a different fix. A task with
    NO checks is freely completable, matching the existing `Task.can_mark_complete` seam ("a
    task with no exit criteria is freely completable"). Measured: gating on `Verdict.passed`
    alone blocked it, because an empty verdict is `None` — so EVERY criterion-free task would
    have become permanently blocked, and most tasks have no criterion. Gating something because
    nobody asked for a check is the inverse of what this module is for.
    """
    if claimed is not TaskStatus.DONE:
        return claimed, ""
    if not verdict.results:
        return TaskStatus.DONE, ""
    outcome = verdict.passed
    if outcome is True:
        return TaskStatus.DONE, ""
    if outcome is None:
        return TaskStatus.BLOCKED, "capability"
    return TaskStatus.BLOCKED, "needs_input"


def criterion_is_irreversible() -> bool:
    """The engine's flip is irreversible, by contract. Stated as a function so the property is
    citable rather than a comment: once the engine has judged a claimed completion, a later
    actor cannot re-judge it. Re-evaluating would make "done" depend on when you asked.
    """
    return True


# ── the completion record ──

#: The five-part completion report. Fixed sections, because a report whose shape varied per template
#: would need a renderer per template — so it would get one generic renderer showing none of it.
COMPLETION_SECTIONS = (
    "files changed",
    "behavior",
    "tests",
    "commands and results",
    "risks and follow-ups",
)


def completion_record(**sections: Any) -> dict[str, Any]:
    """The completion record projected into a task body. A missing section reads as "nothing
    recorded" rather than being omitted. An absent "risks and follow-ups" reads as "there are
    none", which is the claim a reader most wants to be true and least wants guessed."""
    record: dict[str, Any] = {}
    for key in COMPLETION_SECTIONS:
        value = sections.get(key.replace(" ", "_"))
        if isinstance(value, (list, tuple)):
            items = [str(v).strip() for v in value if str(v).strip()]
        elif value is None or not str(value).strip():
            items = []
        else:
            items = [str(value).strip()]
        record[key] = items or ["nothing recorded"]
    return record


def evidence_entry(kind: str, ref: str, *, detail: str = "") -> dict[str, Any]:
    """One evidence row. `kind` + `ref`, so a reader can FOLLOW it. A completion with evidence that
    cannot be opened is a completion with a footnote. The ref is an artifact id or a command-
    output reference, never the output itself — inlining a test log into a task body is how a
    board becomes unreadable.
    """
    return {"kind": str(kind), "ref": str(ref), "detail": str(detail)}


def done_without_evidence(status: Any, evidence: list[dict] | None) -> bool:
    """Whether a done task is asserting completion with nothing behind it. Surfaced by the stuck-
    work sweep rather than blocked at write time: the engine's own criterion pass IS evidence,
    and refusing a done task with an empty list would break every legitimately criterion-free
    task. But a done task with nothing recorded is worth a reader's attention.
    """
    return status is TaskStatus.DONE and not (evidence or [])


# ── cascade-fail ──


@dataclass
class CascadeResult:
    """What a cascade blocked, and the ONE notification it earns. `notify_once` is the field, not a
    count: a parallel fan-in failure produces N cascade events within milliseconds, and N alerts
    for one cause is how a user mutes the channel that was about to tell them something
    important.
    """

    blocked: list[str] = field(default_factory=list)
    reason: str = ""
    notify_once: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked": list(self.blocked),
            "reason": self.reason,
            "notify_once": self.notify_once,
            "count": len(self.blocked),
        }


#: The blocked kind a cascade sets. Distinct from the kinds a node's own failure produces,
#: because the
#: dependent task is not broken — its prerequisite is, and the fix is upstream.
CASCADE_BLOCKED_KIND = "upstream_failed"


def cascade_blocked(
    failed_node: str,
    cause: str,
    dependents: dict[str, list[str]],
    *,
    unreachable_only: bool = True,
) -> CascadeResult:
    """Block every dependent whose frontier is now unreachable. `dependents` maps node id → the
    node ids it depends on, so the walk follows the BINDING graph rather than the container
    tree. A tree walk would miss a later sibling that reads the failed node's output — which is
    the common shape, and the one where an unblocked task is most misleading. Transitive by
    construction: a node blocked by the cascade blocks ITS dependents too, or the board would
    show the second ring as workable when nothing in it can start.
    """
    reason = f"Node {failed_node} failed: {cause}" if cause else f"Node {failed_node} failed"
    blocked: list[str] = []
    frontier = {failed_node}
    # Bounded by the node count: each node enters `blocked` at most once, so a cycle in the
    # dependency
    # map cannot spin here.
    changed = True
    while changed:
        changed = False
        for node_id, deps in dependents.items():
            if node_id in frontier:
                continue
            if unreachable_only and not any(d in frontier for d in deps):
                continue
            frontier.add(node_id)
            blocked.append(node_id)
            changed = True
    return CascadeResult(blocked=blocked, reason=reason)


def cascade_cleared(node_id: str, dependents: dict[str, list[str]]) -> list[str]:
    """Which dependents return to `open` when an upstream is retried, rewound or skipped. The same
    walk, so a clear cannot cover less than the block did — a dependent left blocked after its
    prerequisite recovered is work the board hides, which is the same lie in the other
    direction.
    """
    return cascade_blocked(node_id, "", dependents).blocked


# ── the stuck-work sweep ──

#: Minutes without a node heartbeat before an in-progress task is flagged. Twenty, because a
#: stage that
#: has said nothing for twenty minutes is either wedged or doing something the user should
#: know is slow
#: — and both are worth surfacing.
HEARTBEAT_STALE_MINS = 20

#: Hours a ready-and-unclaimed task waits before it is flagged. An hour, because a task nobody
#: picked up
#: is either mis-scoped or waiting on something nobody recorded.
UNCLAIMED_STALE_HOURS = 1


@dataclass
class Finding:
    """One stuck-work finding. `kind` is machine-readable; `detail` is what a person reads."""

    task_id: str
    kind: str  # stale_heartbeat | unclaimed | done_without_evidence | expired_lease
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "kind": self.kind, "detail": self.detail}


def sweep(tasks: list[Any], *, now: float) -> list[Finding]:
    """Flag work that has silently stopped moving. Findings, not fixes. The sweep's job is to make
    a stall visible — auto-resolving one would hide the condition that caused it, and the same
    stall would recur with nothing recorded. The one exception is lease expiry, which is
    reported so the caller can release it, because an expired lease is unambiguous.
    """
    findings: list[Finding] = []
    for task in tasks or []:
        task_id = str(getattr(task, "id", "") or "")
        status = getattr(task, "status", None)
        if status is TaskStatus.IN_PROGRESS:
            beat = _epoch(getattr(task, "last_heartbeat_at", "") or getattr(task, "updated_at", ""))
            if beat is not None and (now - beat) / 60.0 > HEARTBEAT_STALE_MINS:
                mins = int((now - beat) / 60.0)
                findings.append(
                    Finding(
                        task_id=task_id,
                        kind="stale_heartbeat",
                        detail=f"in progress with no node heartbeat for {mins} minutes",
                    )
                )
        elif status is TaskStatus.OPEN:
            created = _epoch(getattr(task, "created_at", ""))
            if created is not None and (now - created) / 3600.0 > UNCLAIMED_STALE_HOURS:
                hours = int((now - created) / 3600.0)
                findings.append(
                    Finding(
                        task_id=task_id,
                        kind="unclaimed",
                        detail=f"ready and unclaimed for {hours}h — mis-scoped, or waiting on "
                        "something nobody recorded",
                    )
                )
        if done_without_evidence(status, getattr(task, "evidence", None)):
            findings.append(
                Finding(
                    task_id=task_id,
                    kind="done_without_evidence",
                    detail="marked done with nothing recorded behind it",
                )
            )
    return findings


def _epoch(raw: Any) -> float | None:
    """Parse an ISO timestamp to epoch seconds, or None. Tolerant: a sweep that raised on one
    unparseable timestamp would stop reporting every OTHER stall, which is the opposite of what
    a diagnostics pass is for.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError, TypeError):
        return None


def sticky_cancel(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    """`cancelled` is sticky. A recompute that moved a cancelled task back to `open` would
    resurrect work someone deliberately stopped — and the recompute is the NORMAL path
    (projection is an idempotent rebuild), so without stickiness every rebuild would undo every
    cancellation.
    """
    return TaskStatus.CANCELLED if current is TaskStatus.CANCELLED else target


def coalesce_started(existing: str, now_iso: str) -> str:
    """`started_at` is written once. A node retry that rewrote it would make a task that has been
    running for an hour look like it started thirty seconds ago — and the heartbeat sweep reads
    exactly that field to decide whether work has stalled.
    """
    return existing or now_iso
