"""Workflow data model — definitions, runs, the node algebra, and outcomes.

Deliberately dataclasses with explicit `to_dict`/`from_dict` rather than a validation
library: these shapes are persisted as JSON that must survive engine upgrades, so the
readers are **unknown-field-tolerant** by construction (WF2-R12). A bundled template or
a flywheel-proposed diff written by an older engine has to load on a newer one, and a
strict parser would reject it.

Three rules the rest of the engine depends on:

* **A node's identity is its path**, not a uuid. `root.children[2].body` is addressable
  and stable across mutations that do not touch it, which is what lets a rewind
  invalidate exactly the affected journal region.
* **Outcomes are richer than done|failed.** `degraded`, `no_change`, `scope_violation`,
  `escalated` and `blocked` are first-class, because retrofitting them into journal keys
  and widget semantics later is far more painful than declaring them now (WF2-R5).
* **Nothing here executes.** Models are pure data; the engine owns transitions. A model
  that could mutate run state would put two writers on the journal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── identity + format ────────────────────────────────────────────────────────

#: A def name: lowercase, hyphen-separated, filesystem-safe (it becomes a directory).
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: Graph-spec format version. MINOR bumps are additive-only and readers tolerate
#: unknown fields; a MAJOR bump would mean a spec this engine cannot honour.
SPEC_SEMVER = "1.0"


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


# ── node algebra ─────────────────────────────────────────────────────────────


class NodeKind(str, Enum):
    """The construct algebra. A spec is a TREE of containers — the tree renders
    directly as the progress widget, which is why containers are nodes rather than
    edges. DAG shapes inside `parallel` come from per-child `needs`."""

    SEQUENCE = "sequence"
    PARALLEL = "parallel"
    FOREACH = "foreach"
    LOOP = "loop"
    STAGE = "stage"  # one subagent execution (tools, session)
    INFER = "infer"  # ONE bounded model call — no tools, no session
    BRANCH = "branch"  # conditional dispatch on a binding
    TRANSFORM = "transform"  # zero-token pure data reshaping
    ACTION = "action"  # zero-token action-provider dispatch
    WAIT = "wait"
    GATE = "gate"
    SUBWORKFLOW = "subworkflow"


#: Kinds that hold children and therefore have no work of their own.
CONTAINER_KINDS = frozenset(
    {NodeKind.SEQUENCE, NodeKind.PARALLEL, NodeKind.FOREACH, NodeKind.LOOP, NodeKind.BRANCH}
)

#: Kinds that consume model tokens — the only ones a `model_tier` means anything on.
LLM_KINDS = frozenset({NodeKind.STAGE, NodeKind.INFER})

#: Executor lanes (WF2-R21). Derived from kind, never author-declared: a foreach over
#: minutes-long local-model actions must not head-of-line-block a run's LLM stages.
LANE_LLM = "llm"
LANE_IO = "io"
LANE_COMPUTE = "compute"


def lane_for(kind: NodeKind) -> str:
    if kind in LLM_KINDS:
        return LANE_LLM
    if kind in (NodeKind.ACTION, NodeKind.SUBWORKFLOW):
        return LANE_IO
    return LANE_COMPUTE


class JoinMode(str, Enum):
    ALL = "all"
    ANY = "any"
    QUORUM = "quorum"


class LoopMode(str, Enum):
    COUNTED = "counted"
    UNTIL = "until"
    UNTIL_DRY = "until_dry"  # clean-streak termination


class ItemErrorPolicy(str, Enum):
    HALT = "halt"
    SKIP = "skip"  # default: one bad item must not sink the fan-out
    COLLECT = "collect"


class GateKind(str, Enum):
    APPROVAL = "approval"
    VERIFY_COMMAND = "verify_command"
    VERIFY_SCRIPT = "verify_script"
    EVENT = "event"
    EXPRESSION = "expression"
    #: An ordered static→runtime→system ladder with per-criterion hard thresholds. A hard
    #: failure at any rung fails the gate — never averaged, because averaging lets a
    #: confident model pass a gate it structurally failed (WF2-R3).
    LADDER = "ladder"
    #: An LLM judge returning the CLOSED verdict enum (PASS|RETRY|ESCALATE|REJECT), run in
    #: a session distinct from the producing node unless `self_judge` is set.
    JUDGE = "judge"


class SessionMode(str, Enum):
    FRESH = "fresh"
    CONTINUOUS = "continuous"


@dataclass
class Node:
    """One spec node. Kind-specific fields live in `config` rather than in a subclass
    per kind: the spec is JSON that older engines must still read, and a tagged union
    keeps the tolerant-reader rule cheap (an unknown config key is ignored, not fatal).

    `id` is author-facing and only needs to be unique among siblings — bindings address
    nodes by id, and the engine addresses instances by path.
    """

    kind: NodeKind
    id: str = ""
    children: list[Node] = field(default_factory=list)
    body: Node | None = None  # foreach/loop
    cases: dict[str, Node] = field(default_factory=dict)  # branch
    default_case: Node | None = None  # branch
    config: dict[str, Any] = field(default_factory=dict)
    #: Intra-`parallel` DAG edges: sibling ids that must finish first.
    needs: list[str] = field(default_factory=list)
    #: Unknown fields from a newer spec, preserved so a round-trip is lossless.
    extra: dict[str, Any] = field(default_factory=dict)

    # ── derived ──

    @property
    def lane(self) -> str:
        return lane_for(self.kind)

    @property
    def is_container(self) -> bool:
        return self.kind in CONTAINER_KINDS

    def child_nodes(self) -> list[Node]:
        """Every structural child, whatever the container shape."""
        out = list(self.children)
        if self.body is not None:
            out.append(self.body)
        out.extend(self.cases.values())
        if self.default_case is not None:
            out.append(self.default_case)
        return out

    # ── serialization ──

    _KNOWN = frozenset({"kind", "id", "children", "body", "cases", "default", "config", "needs"})

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind.value}
        if self.id:
            d["id"] = self.id
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        if self.body is not None:
            d["body"] = self.body.to_dict()
        if self.cases:
            d["cases"] = {k: v.to_dict() for k, v in self.cases.items()}
        if self.default_case is not None:
            d["default"] = self.default_case.to_dict()
        if self.config:
            d["config"] = dict(self.config)
        if self.needs:
            d["needs"] = list(self.needs)
        d.update(self.extra)  # round-trip anything a newer engine wrote
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Node:
        """Tolerant read. An unrecognized `kind` raises (the engine cannot schedule what
        it cannot dispatch), but unknown *fields* are preserved in `extra`."""
        raw_kind = str((d or {}).get("kind", "")).strip()
        try:
            kind = NodeKind(raw_kind)
        except ValueError as exc:
            raise ValueError(f"unknown node kind {raw_kind!r}") from exc
        body = d.get("body")
        default = d.get("default")
        return cls(
            kind=kind,
            id=str(d.get("id", "") or ""),
            children=[cls.from_dict(c) for c in (d.get("children") or [])],
            body=cls.from_dict(body) if isinstance(body, dict) else None,
            cases={k: cls.from_dict(v) for k, v in (d.get("cases") or {}).items()},
            default_case=cls.from_dict(default) if isinstance(default, dict) else None,
            config=dict(d.get("config") or {}),
            needs=[str(n) for n in (d.get("needs") or [])],
            extra={k: v for k, v in (d or {}).items() if k not in cls._KNOWN},
        )


def walk(node: Node, path: str = "root") -> list[tuple[str, Node]]:
    """Depth-first `(path, node)` pairs. The path IS the instance key the engine uses,
    so its shape is a contract: `root.children[0]`, `root.body`, `root.cases[hit]`."""
    out = [(path, node)]
    for i, child in enumerate(node.children):
        out.extend(walk(child, f"{path}.children[{i}]"))
    if node.body is not None:
        out.extend(walk(node.body, f"{path}.body"))
    for label, case in node.cases.items():
        out.extend(walk(case, f"{path}.cases[{label}]"))
    if node.default_case is not None:
        out.extend(walk(node.default_case, f"{path}.default"))
    return out


# ── outcomes (WF2-R5) ────────────────────────────────────────────────────────


class InstanceState(str, Enum):
    """A node instance's lifecycle. Wider than done|failed on purpose.

    `DEGRADED` is a SUCCESS with a machine-readable reason: an optional capability was
    absent and the node carried on. Templates that would otherwise die when a token is
    missing keep working, and the provenance stays visible downstream.
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    #: Parked on something external — a `wait` deadline or a `gate` awaiting an answer.
    #: Distinct from RUNNING because it consumes no executor slot: a run can sit in
    #: WAITING for hours without holding a lane, and the watchdog wakes it.
    WAITING = "waiting"
    DONE = "done"
    DEGRADED = "degraded"  # done, with a degraded_reason
    FAILED = "failed"
    SKIPPED = "skipped"
    NO_CHANGE = "no_change"  # inherits prior results; downstream need not re-run
    SCOPE_VIOLATION = "scope_violation"
    DISCARDED = "discarded"
    ESCALATED = "escalated"  # circuit breaker tripped
    BLOCKED = "blocked"  # e.g. protocol_violation — never a silent hang
    CANCELLED = "cancelled"


#: States after which a node will not run again without an explicit mutation.
#: BLOCKED belongs here: it is "the engine refused to proceed and a human must decide"
#: — leaving it schedulable would relaunch-and-refuse forever, the silent hang the
#: state exists to prevent. (Its absence also made `_ROOT_TO_RUN[BLOCKED]` unreachable.)
TERMINAL_STATES = frozenset(
    {
        InstanceState.DONE,
        InstanceState.DEGRADED,
        InstanceState.FAILED,
        InstanceState.SKIPPED,
        InstanceState.NO_CHANGE,
        InstanceState.SCOPE_VIOLATION,
        InstanceState.DISCARDED,
        InstanceState.ESCALATED,
        InstanceState.BLOCKED,
        InstanceState.CANCELLED,
    }
)

#: States that count as "this node produced a usable output".
SUCCESS_STATES = frozenset({InstanceState.DONE, InstanceState.DEGRADED, InstanceState.NO_CHANGE})

#: A running or finished node must never be edited — the frozen-region invariant.
FROZEN_STATES = TERMINAL_STATES | {InstanceState.RUNNING}


class FailureClass(str, Enum):
    """Why a node failed, which decides whether the scheduler may retry it.

    Only TRANSIENT and NETWORK are retryable: retrying a USER error (a malformed
    prompt) or a PERMISSION error burns budget to reach the same failure.
    """

    USER = "user"
    TRANSIENT = "transient"
    NETWORK = "network"
    PERMISSION = "permission"
    PROTOCOL = "protocol"
    BUDGET = "budget"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


RETRYABLE_CLASSES = frozenset({FailureClass.TRANSIENT, FailureClass.NETWORK})


@dataclass
class Failure:
    """A typed failure. `cause_plain` and `remediation` are DIFFERENT things — the
    widget renders the remediation as an actionable next step, and collapsing them
    leaves the user with an error and no idea what to do."""

    failure_class: FailureClass = FailureClass.INTERNAL
    cause_plain: str = ""
    remediation: str = ""
    recoverable: bool = False
    terminal_reason: str = ""
    suggestion: str = ""

    @property
    def retryable(self) -> bool:
        return self.failure_class in RETRYABLE_CLASSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.failure_class.value,
            "cause_plain": self.cause_plain,
            "remediation": self.remediation,
            "recoverable": self.recoverable,
            "retryable": self.retryable,
            "terminal_reason": self.terminal_reason,
            "suggestion": self.suggestion,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Failure:
        raw = str((d or {}).get("class", "internal"))
        try:
            fc = FailureClass(raw)
        except ValueError:
            fc = FailureClass.INTERNAL  # tolerant: an unknown class is not fatal
        return cls(
            failure_class=fc,
            cause_plain=str(d.get("cause_plain", "") or ""),
            remediation=str(d.get("remediation", "") or ""),
            recoverable=bool(d.get("recoverable", False)),
            terminal_reason=str(d.get("terminal_reason", "") or ""),
            suggestion=str(d.get("suggestion", "") or ""),
        )


@dataclass
class FailureSignature:
    """A 4-layer localization record for cheap cross-run diffing (WF2-R5)."""

    failing_node: str = ""
    stage: str = ""
    layer: str = ""  # routing | execution | verification | governance
    reason: str = ""
    input_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "failing_node": self.failing_node,
            "stage": self.stage,
            "layer": self.layer,
            "reason": self.reason,
            "input_hash": self.input_hash,
        }


# ── run status ───────────────────────────────────────────────────────────────


class RunStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    NEEDS_INPUT = "needs_input"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.ESCALATED}
)


class OriginKind(str, Enum):
    CHAT = "chat"
    SCHEDULE = "schedule"
    EVENT = "event"
    HOOK = "hook"
    IDLE = "idle"
    SUBAGENT_TOOL = "subagent-tool"
    MANUAL = "manual"
    API = "api"


class OverlapPolicy(str, Enum):
    """What a trigger-origin start does when the previous run is still going."""

    SKIP = "skip"  # default — a per-minute trigger must not stack runs
    QUEUE = "queue"
    CANCEL_PREVIOUS = "cancel_previous"


# ── def-side records ─────────────────────────────────────────────────────────


@dataclass
class InputParam:
    type: str = "string"
    required: bool = False
    default: Any = None
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "help": self.help,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InputParam:
        return cls(
            type=str((d or {}).get("type", "string") or "string"),
            required=bool(d.get("required", False)),
            default=d.get("default"),
            help=str(d.get("help", "") or ""),
        )


@dataclass
class RunBudget:
    """Soft caps. A breach PAUSES the run resumably rather than killing it — the user
    can extend and continue, which is the difference between a budget and a bomb."""

    max_tokens: int = 0  # 0 = unlimited
    max_cost: float = 0.0
    max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "max_cost": self.max_cost,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunBudget:
        d = d or {}
        return cls(
            max_tokens=int(d.get("max_tokens", 0) or 0),
            max_cost=float(d.get("max_cost", 0.0) or 0.0),
            max_retries=int(d.get("max_retries", 3) or 3),
        )


@dataclass
class RunDefaults:
    model_tier: str = "standard"
    effort: str = ""
    max_concurrency: int = 0  # 0 = use the config default
    node_timeout_total_secs: int = 0
    node_timeout_stall_secs: int = 0
    budget: RunBudget = field(default_factory=RunBudget)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_tier": self.model_tier,
            "effort": self.effort,
            "max_concurrency": self.max_concurrency,
            "node_timeout_total_secs": self.node_timeout_total_secs,
            "node_timeout_stall_secs": self.node_timeout_stall_secs,
            "budget": self.budget.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunDefaults:
        d = d or {}
        return cls(
            model_tier=str(d.get("model_tier", "standard") or "standard"),
            effort=str(d.get("effort", "") or ""),
            max_concurrency=int(d.get("max_concurrency", 0) or 0),
            node_timeout_total_secs=int(d.get("node_timeout_total_secs", 0) or 0),
            node_timeout_stall_secs=int(d.get("node_timeout_stall_secs", 0) or 0),
            budget=RunBudget.from_dict(d.get("budget") or {}),
        )


@dataclass
class DefMetadata:
    """Declared, not inferred. `requirements` is what a run-start preflight checks so a
    missing binary or credential fails BEFORE tokens are spent (Slice 6)."""

    risk: str = "low"
    capabilities: list[str] = field(default_factory=list)
    requirements: dict[str, list[str]] = field(default_factory=dict)  # binaries/credentials
    steering_examples: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "capabilities": list(self.capabilities),
            "requirements": {k: list(v) for k, v in self.requirements.items()},
            "steering_examples": [dict(e) for e in self.steering_examples],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DefMetadata:
        d = d or {}
        reqs = d.get("requirements") or {}
        return cls(
            risk=str(d.get("risk", "low") or "low"),
            capabilities=[str(c) for c in (d.get("capabilities") or [])],
            requirements={
                str(k): [str(x) for x in (v or [])]
                for k, v in (reqs.items() if isinstance(reqs, dict) else [])
            },
            steering_examples=[
                {str(k): str(val) for k, val in e.items()}
                for e in (d.get("steering_examples") or [])
                if isinstance(e, dict)
            ],
        )


@dataclass
class WorkflowDef:
    """A reusable graph spec. Versioned on every save so a run can pin the spec it
    started from and a mutation can be diffed against its predecessor."""

    name: str
    root: Node
    version: int = 1
    spec_semver: str = SPEC_SEMVER
    description: str = ""
    source: str = "user"  # user | bundled
    provenance: str = "user"  # authoring actor: chat | user
    inputs: dict[str, InputParam] = field(default_factory=dict)
    defaults: RunDefaults = field(default_factory=RunDefaults)
    metadata: DefMetadata = field(default_factory=DefMetadata)
    on_overlap: OverlapPolicy = OverlapPolicy.SKIP
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    _KNOWN = frozenset(
        {
            "name",
            "root",
            "version",
            "spec_semver",
            "description",
            "source",
            "provenance",
            "inputs",
            "defaults",
            "metadata",
            "on_overlap",
            "tags",
            "created_at",
            "updated_at",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "spec_semver": self.spec_semver,
            "description": self.description,
            "source": self.source,
            "provenance": self.provenance,
            "inputs": {k: v.to_dict() for k, v in self.inputs.items()},
            "defaults": self.defaults.to_dict(),
            "metadata": self.metadata.to_dict(),
            "on_overlap": self.on_overlap.value,
            "root": self.root.to_dict(),
            "tags": list(self.tags),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowDef:
        d = d or {}
        raw_overlap = str(d.get("on_overlap", "skip") or "skip")
        try:
            overlap = OverlapPolicy(raw_overlap)
        except ValueError:
            overlap = OverlapPolicy.SKIP
        root_raw = d.get("root")
        if not isinstance(root_raw, dict):
            raise ValueError("workflow def has no root node")
        return cls(
            name=str(d.get("name", "") or ""),
            root=Node.from_dict(root_raw),
            version=int(d.get("version", 1) or 1),
            spec_semver=str(d.get("spec_semver", SPEC_SEMVER) or SPEC_SEMVER),
            description=str(d.get("description", "") or ""),
            source=str(d.get("source", "user") or "user"),
            provenance=str(d.get("provenance", "user") or "user"),
            inputs={str(k): InputParam.from_dict(v) for k, v in (d.get("inputs") or {}).items()},
            defaults=RunDefaults.from_dict(d.get("defaults") or {}),
            metadata=DefMetadata.from_dict(d.get("metadata") or {}),
            on_overlap=overlap,
            tags=[str(t) for t in (d.get("tags") or [])],
            created_at=str(d.get("created_at", "") or ""),
            updated_at=str(d.get("updated_at", "") or ""),
            extra={k: v for k, v in d.items() if k not in cls._KNOWN},
        )


# ── run-side records ─────────────────────────────────────────────────────────


@dataclass
class RunOrigin:
    kind: OriginKind = OriginKind.MANUAL
    session_key: str = ""
    tool_call_id: str = ""
    trigger_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "session_key": self.session_key,
            "tool_call_id": self.tool_call_id,
            "trigger_id": self.trigger_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunOrigin:
        d = d or {}
        try:
            kind = OriginKind(str(d.get("kind", "manual") or "manual"))
        except ValueError:
            kind = OriginKind.MANUAL
        return cls(
            kind=kind,
            session_key=str(d.get("session_key", "") or ""),
            tool_call_id=str(d.get("tool_call_id", "") or ""),
            trigger_id=str(d.get("trigger_id", "") or ""),
        )


@dataclass
class WorkflowRun:
    """One execution. `root_run_id` is propagated through subworkflow spawns and forks
    and indexed with status, so the whole tree of a run is one query rather than a
    recursive walk (WF2-R13)."""

    id: str
    workflow_name: str
    status: RunStatus = RunStatus.DRAFT
    spec_version: int = 1
    inputs: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    origin: RunOrigin = field(default_factory=RunOrigin)
    parent_run_id: str | None = None
    root_run_id: str = ""
    spawned_by_node_id: str | None = None
    branch_key: str | None = None
    forked_from: dict[str, Any] | None = None
    project_id: str = ""
    task_list_id: str = ""
    mode: str = "background"  # blocking | background
    budget: RunBudget = field(default_factory=RunBudget)
    pinned: bool = False
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float = 0.0
    total_tokens: int = 0
    agent_count: int = 0
    error_message: str = ""
    attention: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A run is its own root until a spawn/fork says otherwise. Defaulting here
        # rather than at every call site keeps the tree query total.
        if not self.root_run_id:
            self.root_run_id = self.id

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES

    _KNOWN = frozenset(
        {
            "id",
            "workflow_name",
            "status",
            "spec_version",
            "inputs",
            "intent",
            "origin",
            "parent_run_id",
            "root_run_id",
            "spawned_by_node_id",
            "branch_key",
            "forked_from",
            "project_id",
            "task_list_id",
            "mode",
            "budget",
            "pinned",
            "created_at",
            "started_at",
            "completed_at",
            "elapsed_seconds",
            "total_tokens",
            "agent_count",
            "error_message",
            "attention",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "workflow_name": self.workflow_name,
            "status": self.status.value,
            "spec_version": self.spec_version,
            "inputs": dict(self.inputs),
            "intent": self.intent,
            "origin": self.origin.to_dict(),
            "parent_run_id": self.parent_run_id,
            "root_run_id": self.root_run_id,
            "spawned_by_node_id": self.spawned_by_node_id,
            "branch_key": self.branch_key,
            "forked_from": self.forked_from,
            "project_id": self.project_id,
            "task_list_id": self.task_list_id,
            "mode": self.mode,
            "budget": self.budget.to_dict(),
            "pinned": self.pinned,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "total_tokens": self.total_tokens,
            "agent_count": self.agent_count,
            "error_message": self.error_message,
            "attention": self.attention,
        }
        d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowRun:
        d = d or {}
        try:
            status = RunStatus(str(d.get("status", "draft") or "draft"))
        except ValueError:
            status = RunStatus.DRAFT
        return cls(
            id=str(d.get("id", "") or ""),
            workflow_name=str(d.get("workflow_name", "") or ""),
            status=status,
            spec_version=int(d.get("spec_version", 1) or 1),
            inputs=dict(d.get("inputs") or {}),
            intent=str(d.get("intent", "") or ""),
            origin=RunOrigin.from_dict(d.get("origin") or {}),
            parent_run_id=d.get("parent_run_id"),
            root_run_id=str(d.get("root_run_id", "") or ""),
            spawned_by_node_id=d.get("spawned_by_node_id"),
            branch_key=d.get("branch_key"),
            forked_from=d.get("forked_from"),
            project_id=str(d.get("project_id", "") or ""),
            task_list_id=str(d.get("task_list_id", "") or ""),
            mode=str(d.get("mode", "background") or "background"),
            budget=RunBudget.from_dict(d.get("budget") or {}),
            pinned=bool(d.get("pinned", False)),
            created_at=str(d.get("created_at", "") or ""),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            elapsed_seconds=float(d.get("elapsed_seconds", 0.0) or 0.0),
            total_tokens=int(d.get("total_tokens", 0) or 0),
            agent_count=int(d.get("agent_count", 0) or 0),
            error_message=str(d.get("error_message", "") or ""),
            attention=d.get("attention"),
            extra={k: v for k, v in d.items() if k not in cls._KNOWN},
        )


@dataclass
class NodeInstance:
    """Per-node run state. `epoch` is what makes rewind safe: journal keys are stamped
    with it, so a replayed region from a superseded epoch can never be mistaken for a
    cache hit on the current one."""

    path: str
    state: InstanceState = InstanceState.PENDING
    epoch: int = 0
    attempt: int = 0
    #: Edges this node considered and did NOT take — recorded when a `branch` routes or
    #: a gate rejects. The frontier marks a declined edge's target SKIPPED (terminal), so
    #: a downstream join proceeds instead of waiting forever on it (WF2-R18). Explicit
    #: rather than inferred: routing among cases says nothing about a sibling whose
    #: `needs` merely names this node.
    declined_edges: list[str] = field(default_factory=list)
    degraded_reason: str = ""
    failure: Failure | None = None
    started_at: str | None = None
    completed_at: str | None = None
    output_ref: str = ""  # outputs/<path-hash>.json, or an artifact pointer
    tokens: int = 0
    #: Unix deadline for a WAITING node — when the engine should look at it again.
    #: PERSISTED, not in-memory: a `wait` or a timed gate must survive a gateway
    #: restart. Held only in memory, a restart would leave every waiting run parked
    #: forever with nothing scheduled to wake it.
    wake_at: float = 0.0
    #: A short label for the `foreach` item this instance is processing (WF2-R5) — what makes
    #: "[3/12] auth.py" possible. PERSISTED because it is the only durable record of WHICH item
    #: an instance was: the items list is re-resolved from a binding, and after the upstream
    #: output changed (or a reload) the label would otherwise be unrecoverable. Empty for a
    #: non-iterated node.
    item_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "state": self.state.value,
            "epoch": self.epoch,
            "attempt": self.attempt,
            "declined_edges": list(self.declined_edges),
            "degraded_reason": self.degraded_reason,
            "failure": self.failure.to_dict() if self.failure else None,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "output_ref": self.output_ref,
            "tokens": self.tokens,
            "wake_at": self.wake_at,
            "item_label": self.item_label,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NodeInstance:
        d = d or {}
        try:
            state = InstanceState(str(d.get("state", "pending") or "pending"))
        except ValueError:
            state = InstanceState.PENDING
        fail = d.get("failure")
        return cls(
            path=str(d.get("path", "") or ""),
            state=state,
            epoch=int(d.get("epoch", 0) or 0),
            attempt=int(d.get("attempt", 0) or 0),
            declined_edges=[str(e) for e in (d.get("declined_edges") or [])],
            degraded_reason=str(d.get("degraded_reason", "") or ""),
            failure=Failure.from_dict(fail) if isinstance(fail, dict) else None,
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            output_ref=str(d.get("output_ref", "") or ""),
            tokens=int(d.get("tokens", 0) or 0),
            wake_at=float(d.get("wake_at", 0.0) or 0.0),
            item_label=str(d.get("item_label", "") or ""),
        )
