"""Project as the sole work umbrella (WORK-CONTAINERS §1, §5.2, §6.1 — S46).

Project already exists and is already the right shape — named, briefed, owning a `context/` dir and
`worktrees/`, with protected `Personal`/`Repeatable` defaults. This module adds only what the run
engine needs from it, and deliberately adds no second umbrella noun: **no sub-projects, no
milestones, no org semantics.** That is the enterprise slope the plan names explicitly.

Three things land here:

* **Run→project binding.** Every run resolves a project, auto-creating one rather than allowing an
  orphan. The cost is one project row per orphan run, which is what loops already do.
* **Truthful lifecycle (§5.2).** The board must never lie after a crash. A run record is written
  BEFORE a concurrency slot is acquired, so `queued` is distinguishable from `running`; and the boot
  sweep checks whether the execution SUBSTRATE actually died before calling a run a zombie — a
  worktree run whose worktree survived a gateway restart is `suspended` with a Resume affordance,
  not aborted. Sweeping it would destroy recoverable work and report success.
* **Claim leases (§1.5).** Concurrent co-tenant sessions are real today, so a leaf a second worker
  could pick up needs an exclusive TTL'd claim. The lease is advisory-but-recorded: the holder is
  mirrored onto the row so the board can render the claim, which is the half that makes a stuck
  claim visible instead of mysterious.

The projections here are PURE — they take run records and return board rows. Storage stays in
`store.py`, project resolution in `projects.py`. That is what lets the "never lies" properties be
tested without a gateway.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.workflows.models import TERMINAL_RUN_STATUSES, OriginKind, RunStatus

logger = logging.getLogger(__name__)

#: A claim lease is short by design. An hour is long enough for any single leaf and short enough
#: that a crashed holder frees the work without an admin step — an indefinite lease turns one dead
#: worker into a permanently stuck task.
MAX_LEASE_SECS = 3600
DEFAULT_LEASE_SECS = 900


class BoardState(str, Enum):
    """The Work board's own state vocabulary — a PROJECTION of `RunStatus`, not a second source.

    It exists because the board answers a different question than the engine does. `RunStatus` says
    what the engine is doing; the board says what the USER should do, which is why `NEEDS_INPUT`
    sorts above everything and why `queued` and `suspended` are separate from `working` even though
    the engine has one status for two of them.
    """

    NEEDS_INPUT = "needs_input"
    WORKING = "working"
    QUEUED = "queued"
    SUSPENDED = "suspended"
    REVIEW = "review"
    DONE = "done"


#: Board order. Needs-input is pinned FIRST and unconditionally: it is the only group where the run
#: is stopped waiting on the person reading the board, so burying it under twelve working rows is
#: how a run sits blocked overnight.
BOARD_ORDER = (
    BoardState.NEEDS_INPUT,
    BoardState.WORKING,
    BoardState.QUEUED,
    BoardState.SUSPENDED,
    BoardState.REVIEW,
    BoardState.DONE,
)

#: Origins whose runs are collapsed by default, drawn from the ENGINE's `OriginKind` rather than
#: hand-typed strings. Measured: an earlier version named `housekeeping` and `heartbeat`, neither of
#: which exists in `OriginKind` — a collapse rule keyed on a value that can never occur is a rule
#: that never fires, and nothing reports it. A batch-compiled subagent run is one the user did not
#: individually ask for, so it is collapsed; a board whose top rows are noise stops being read.
COLLAPSED_ORIGINS = frozenset({OriginKind.SUBAGENT_TOOL.value})

#: Origins suppressed from attention indicators (counts, pills, notifications) even when blocked.
#: An IDLE-origin run is one the system started on its own initiative, so a badge from it is
#: attention the user never asked to spend — and a badge that fires for something unrequested trains
#: them to ignore badges.
UNATTENDED_ORIGINS = frozenset({OriginKind.IDLE.value})


class Completeness(str, Enum):
    """How much of a projection is actually known.

    Carried on every projection because a board that renders an inferred state identically to a
    known one is a board that lies confidently. `PARTIAL` after a crash is useful; `PARTIAL`
    presented as `COMPLETE` is worse than an error.
    """

    COMPLETE = "complete"
    INFERRED = "inferred"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass
class Claim:
    """An exclusive TTL'd claim on a run or task.

    `holder` is a session key rather than a pid: the point is to tell a HUMAN who has it, and a pid
    that died is indistinguishable from one that never existed. Expiry is absolute rather than a
    duration so a clock read at render time answers "is this still held" without knowing when it was
    taken.
    """

    holder: str
    expires_at: float
    taken_at: float = 0.0
    renewals: int = 0

    def expired(self, now: float) -> bool:
        return now >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "holder": self.holder,
            "expires_at": self.expires_at,
            "taken_at": self.taken_at,
            "renewals": self.renewals,
        }


def claim(
    holder: str, *, now: float, ttl: int = DEFAULT_LEASE_SECS, existing: Claim | None = None
) -> tuple[Claim | None, str]:
    """Take a claim, or refuse with a reason.

    Refusal reasons are returned rather than raised because "someone else has this" is a normal
    outcome a board renders, not an error a worker crashes on.

    A claim held by the SAME holder renews rather than being refused — a worker that lost its
    in-memory state and re-claimed its own work would otherwise be locked out of it until the TTL
    expired, which is a self-inflicted stall.
    """
    if not holder:
        return None, "no holder"
    ttl = max(1, min(int(ttl), MAX_LEASE_SECS))
    if existing is not None and not existing.expired(now):
        if existing.holder == holder:
            return (
                Claim(
                    holder=holder,
                    expires_at=now + ttl,
                    taken_at=existing.taken_at or now,
                    renewals=existing.renewals + 1,
                ),
                "",
            )
        return None, f"held by {existing.holder} for another {int(existing.expires_at - now)}s"
    return Claim(holder=holder, expires_at=now + ttl, taken_at=now), ""


def release(existing: Claim | None, holder: str) -> tuple[Claim | None, str]:
    """Release a claim. Only the holder may.

    A release that let anyone drop anyone's claim would make the lease advisory in the one direction
    that matters — a second worker could steal work mid-execution by releasing first.
    """
    if existing is None:
        return None, ""
    if existing.holder != holder:
        return existing, f"held by {existing.holder}, not {holder}"
    return None, ""


@dataclass
class Substrate:
    """Whether a run's execution substrate outlived the process that was driving it.

    This is the distinction §5.2 turns on. `alive=True` means the work is recoverable — a worktree
    still on disk, a container still up — so the run is SUSPENDED and resumable. `alive=False` means
    the work is gone and the run is honestly aborted.
    """

    kind: str = "inline"  # inline | worktree | container
    alive: bool = False
    detail: str = ""

    @property
    def isolated(self) -> bool:
        """Whether the substrate is separable from the gateway process at all.

        An inline run's substrate IS the process, so it can never survive a restart — reporting one
        as suspended would offer a Resume that cannot work.
        """
        return self.kind in ("worktree", "container")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "alive": self.alive, "detail": self.detail}


@dataclass
class SweepDecision:
    """What the boot sweep decided about one stale run, and why.

    The reason is part of the contract: a run marked aborted with "server restarted" is legible,
    while one that silently changed state is a support question.
    """

    run_id: str
    status: RunStatus | None = None
    board_state: BoardState = BoardState.DONE
    reason: str = ""
    resumable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value if self.status else "",
            "board_state": self.board_state.value,
            "reason": self.reason,
            "resumable": self.resumable,
        }


def sweep_decision(run: Any, substrate: Substrate) -> SweepDecision:
    """Decide one stale run's fate at boot. The substrate check comes FIRST.

    Marking every stale `running` run aborted is the obvious implementation and it is wrong: an
    isolated run whose worktree survived the restart has recoverable work, and aborting it destroys
    that work while reporting success. So an isolated-and-alive substrate yields `suspended` with a
    Resume affordance; everything else is honestly aborted.

    A run already in a terminal state is left ALONE. Re-deciding a completed run would let a boot
    sweep overwrite a real outcome with an inferred one.
    """
    run_id = str(getattr(run, "id", "") or "")
    status = getattr(run, "status", None)
    if status in TERMINAL_RUN_STATUSES:
        return SweepDecision(
            run_id=run_id,
            status=status,
            board_state=board_state_for(run),
            reason="already terminal — left untouched",
        )
    if substrate.isolated and substrate.alive:
        return SweepDecision(
            run_id=run_id,
            status=RunStatus.PAUSED,
            board_state=BoardState.SUSPENDED,
            reason=f"{substrate.kind} substrate survived the restart — resumable",
            resumable=True,
        )
    return SweepDecision(
        run_id=run_id,
        status=RunStatus.CANCELLED,
        board_state=BoardState.DONE,
        reason="server restarted" if not substrate.isolated else "substrate is gone",
    )


def board_state_for(run: Any) -> BoardState:
    """Project one run onto the board's vocabulary.

    `queued` is derived from the record existing WITHOUT a start time, which is why §5.2 asks that
    the record be written before a slot is acquired: without that ordering, a run waiting for a slot
    indistinguishable from one that is running, and the board reports work in flight that has not
    begun.
    """
    status = getattr(run, "status", None)
    if status == RunStatus.NEEDS_INPUT:
        return BoardState.NEEDS_INPUT
    if status == RunStatus.PAUSED:
        return BoardState.SUSPENDED
    if status == RunStatus.RUNNING:
        return BoardState.QUEUED if not getattr(run, "started_at", "") else BoardState.WORKING
    if status == RunStatus.DRAFT:
        return BoardState.QUEUED
    if status == RunStatus.ESCALATED:
        return BoardState.REVIEW
    return BoardState.DONE


@dataclass
class BoardRow:
    """One row on the Work board.

    `collapsed` and `attention` are separate flags because they answer different questions: a
    subagent-tool run is collapsed (visual noise) but still counts toward attention if it is
    blocked, while a heartbeat run counts toward neither.
    """

    run_id: str
    title: str
    state: BoardState
    origin: str = ""
    project_id: str = ""
    claim: Claim | None = None
    collapsed: bool = False
    attention: bool = False
    resumable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "title": self.title,
            "state": self.state.value,
            "origin": self.origin,
            "project_id": self.project_id,
            "claim": self.claim.to_dict() if self.claim else None,
            "collapsed": self.collapsed,
            "attention": self.attention,
            "resumable": self.resumable,
        }


def board_row(run: Any, *, claim_record: Claim | None = None, now: float = 0.0) -> BoardRow:
    """One run as a board row, with its claim and its attention posture.

    An EXPIRED claim is dropped rather than rendered: a claim badge naming a holder that no longer
    holds it is worse than no badge, because it tells the user the work is taken when it is free.
    """
    origin = _origin_kind(run)
    state = board_state_for(run)
    held = claim_record if (claim_record and not claim_record.expired(now)) else None
    return BoardRow(
        run_id=str(getattr(run, "id", "") or ""),
        title=str(getattr(run, "workflow_name", "") or "") or "(unnamed run)",
        state=state,
        origin=origin,
        project_id=str(getattr(run, "project_id", "") or ""),
        claim=held,
        collapsed=origin in COLLAPSED_ORIGINS,
        attention=(state is BoardState.NEEDS_INPUT and origin not in UNATTENDED_ORIGINS),
        resumable=state is BoardState.SUSPENDED,
    )


def _origin_kind(run: Any) -> str:
    """The run's origin as its enum VALUE.

    `WorkflowRun.origin` is a `RunOrigin` object, not a string — reading it with `str(...)` yielded
    a dataclass repr, so every origin comparison silently failed and no run was ever collapsed or
    suppressed. Two inert rules from one wrong type read.
    """
    origin = getattr(run, "origin", None)
    kind = getattr(origin, "kind", None)
    if isinstance(kind, OriginKind):
        return kind.value
    return str(kind or origin or "")


def group_board(rows: list[BoardRow]) -> list[dict[str, Any]]:
    """Group rows into the board's ordered sections.

    EMPTY sections are omitted. A board rendering "Suspended (0)" spends a heading on the absence of
    a problem, and six such headings push the rows the user came for below the fold.
    """
    by_state: dict[BoardState, list[BoardRow]] = {}
    for row in rows:
        by_state.setdefault(row.state, []).append(row)
    out: list[dict[str, Any]] = []
    for state in BOARD_ORDER:
        group = by_state.get(state) or []
        if not group:
            continue
        out.append(
            {
                "state": state.value,
                "count": len(group),
                "attention": sum(1 for r in group if r.attention),
                "rows": [r.to_dict() for r in group],
            }
        )
    return out


def attention_count(rows: list[BoardRow]) -> int:
    """The count pill. Only rows that genuinely want a human, so a badge means something."""
    return sum(1 for row in rows if row.attention)


@dataclass
class Section:
    """One section of the `/work` aggregation, with its own success or failure.

    The isolation is the design: five heterogeneous sources fail independently, and one broken or
    slow source must degrade ONE section rather than the whole first paint. A single try/catch
    around the aggregate would make a stale legacy-loop reader take down the run list.
    """

    name: str
    items: list[Any] = field(default_factory=list)
    status: str = "ok"  # ok | loading | error
    error: str = ""
    loaded_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "items": list(self.items),
            "status": self.status,
            "error": self.error,
            "loadedAt": self.loaded_at,
        }


def collect_sections(
    sources: dict[str, Any], *, now: float = 0.0
) -> tuple[list[dict[str, Any]], Completeness]:
    """Run each source under its OWN try/catch and report per-section status.

    Returns the sections plus the aggregate completeness, which is what stops a partially-failed
    board from being read as a complete one. A caller that only got the sections would have to infer
    completeness from the absence of items — and an empty section and a failed section look
    identical.
    """
    sections: list[dict[str, Any]] = []
    failures = 0
    for name, source in sources.items():
        section = Section(name=name, loaded_at=now)
        try:
            items = source() if callable(source) else source
            section.items = list(items or [])
        except Exception as exc:  # one source, one section
            failures += 1
            section.status = "error"
            section.error = f"{type(exc).__name__}: {exc}"
            logger.debug("work section %r failed", name, exc_info=True)
        sections.append(section.to_dict())
    if not sources:
        return sections, Completeness.COMPLETE
    if failures == len(sources):
        return sections, Completeness.ERROR
    return sections, (Completeness.PARTIAL if failures else Completeness.COMPLETE)


def project_block(brief: str, overview: str, instructions: str) -> str:
    """The project context block injected into EVERY session inside the project.

    Three fields in a fixed order, each labelled. Merging them into one blob would lose the
    distinction the plan draws: the brief is the what/why (stable), the overview is current state
    (revised in place), and the instructions are operating procedure. An agent that cannot tell the
    goal from the current state will treat a finished sub-goal as still open.

    Returns "" when nothing is set — an empty labelled block reads as "this project has no goal",
    which is a claim about the project rather than about the data.
    """
    parts: list[str] = []
    if (brief or "").strip():
        parts.append("PROJECT BRIEF (the goal and scope of this effort):\n" + brief.strip())
    if (overview or "").strip():
        parts.append(
            "PROJECT OVERVIEW (current state — what the project now knows):\n" + overview.strip()
        )
    if (instructions or "").strip():
        parts.append("PROJECT INSTRUCTIONS (how to operate here):\n" + instructions.strip())
    return "\n\n".join(parts)


#: The three wayfinder ledgers, and what each is FOR. Named here rather than in a UI label so the
#: promotion test travels with the mechanism: the fog bucket exists so "not yet a task" work has a
#: home, and its promotion test is whether the question can now be stated precisely.
LEDGERS = {
    "decisions": (
        "one line per resolved gate or run outcome, linking the run — an index, not a store"
    ),
    "fog": "questions not yet precise enough to be tasks; promote when the question can be stated",
    "out_of_scope": "gist + reason + link; revisited only if the brief is redrawn",
}


def ledger_entry(kind: str, text: str, *, link: str = "", reason: str = "") -> dict[str, Any]:
    """One append-only ledger line.

    `out_of_scope` requires a REASON. An out-of-scope entry without one is indistinguishable from
    something that was forgotten, and the whole value of the bucket is that revisiting it later is
    cheap because the reasoning is recorded.
    """
    if kind not in LEDGERS:
        raise ValueError(f"unknown ledger {kind!r}; expected one of {sorted(LEDGERS)}")
    entry: dict[str, Any] = {"kind": kind, "text": (text or "").strip()}
    if link:
        entry["link"] = link
    if kind == "out_of_scope" and not (reason or "").strip():
        entry["reason"] = "no reason recorded"
    elif reason:
        entry["reason"] = reason.strip()
    return entry
