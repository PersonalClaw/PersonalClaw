"""Workflow data model — definitions, runs, the node algebra, and outcomes.

Deliberately dataclasses with explicit `to_dict`/`from_dict` rather than a validation
library: these shapes are persisted as JSON that must survive engine upgrades, so the
readers are **unknown-field-tolerant** by construction. A bundled template or
a flywheel-proposed diff written by an older engine has to load on a newer one, and a
strict parser would reject it.

Three rules the rest of the engine depends on:

* **A node's identity is its path**, not a uuid. `root.children[2].body` is addressable
  and stable across mutations that do not touch it, which is what lets a rewind
  invalidate exactly the affected journal region.
* **Outcomes are richer than done|failed.** `degraded`, `no_change`, `scope_violation`,
  `escalated` and `blocked` are first-class, because retrofitting them into journal keys
  and widget semantics later is far more painful than declaring them now.
* **Nothing here executes.** Models are pure data; the engine owns transitions. A model
  that could mutate run state would put two writers on the journal.
"""

from __future__ import annotations

import calendar
import re
import time
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
    VISUALIZE = "visualize"  # ONE bounded model call → a genui widget spec, no tools
    WAIT = "wait"
    GATE = "gate"
    SUBWORKFLOW = "subworkflow"


#: Kinds that hold children and therefore have no work of their own.
CONTAINER_KINDS = frozenset(
    {NodeKind.SEQUENCE, NodeKind.PARALLEL, NodeKind.FOREACH, NodeKind.LOOP, NodeKind.BRANCH}
)

#: The kinds that record no output of their own: a `sequence`, `parallel` or `foreach` runs the
#: steps inside it and produces nothing itself, so a `{{nodes.<id>…}}` naming one can never
#: resolve. Every other kind records one under its id: a step when it settles (a `branch` records
#: its routing, `{"case": label}`, and adds `produced`, what that case produced, once the case has
#: ended in success — `node_bindings.record_branch_outputs`), and a `loop` when it ends done, its
#: last cycle's output (`loop_convergence.finish_loop`). ONE definition: the validator refuses a
#: read of one of these when the spec is saved (`WF_UNSATISFIABLE_OUTPUT_REF`), and the binding
#: error of a run that meets one names them.
NO_OUTPUT_KINDS = frozenset({NodeKind.SEQUENCE, NodeKind.PARALLEL, NodeKind.FOREACH})

#: Kinds that consume model tokens AND take an author-tunable `model_tier`. `visualize`
#: is deliberately NOT here: it makes a model call but is pinned to the reasoning axis,
#: so a `model_tier` would mean nothing on it — and the
#: validator's `prompt` requirement keys off this set, which `visualize` (data+hint, no
#: prompt) must not trip.
LLM_KINDS = frozenset({NodeKind.STAGE, NodeKind.INFER})

#: Executor lanes. Derived from kind, never author-declared: a foreach over
#: minutes-long local-model actions must not head-of-line-block a run's LLM stages.
LANE_LLM = "llm"
LANE_IO = "io"
LANE_COMPUTE = "compute"


def lane_for(kind: NodeKind) -> str:
    # `visualize` shares the LLM lane: it makes a blocking model call, so scheduling it
    # on the compute lane would head-of-line-block zero-token reshaping behind a network
    # round-trip — the exact inversion the lanes exist to prevent.
    if kind in LLM_KINDS or kind is NodeKind.VISUALIZE:
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
    #: Runs until something OUTSIDE it says stop — a sibling in a `join: any` parallel
    #: completing, user cancellation, or the run's timeout. The cleanest expression of a
    #: watcher/monitor, and the only mode with no self-terminating condition, which is why
    #: it is the only one that requires an external reaper (`reap_watchers`) to be a
    #: bounded run rather than an immortal one.
    UNTIL_CANCELLED = "until_cancelled"


class ItemErrorPolicy(str, Enum):
    """What a `foreach` does about an item that FAILED. Three genuinely different answers, and
    the difference is observable at the RUN level — see `tick.foreach_outcome`, which is the
    single place the choice is made and which must branch on every member.

    The two axes are "how much of the fan-out still runs" and "does the failure count"; the
    members are the three useful combinations of them.
    """

    #: Stop starting new items the moment one has failed. The items already in flight finish;
    #: the un-started ones stay PENDING, so the container never reaches a terminal state and
    #: the run ends through the frontier's deadlock path — a FAILED run that did the least
    #: work it could get away with.
    HALT = "halt"
    #: Default: one bad item must not sink the fan-out. Every item runs, and a failure is
    #: TOLERATED — the container reports DEGRADED, which is a SUCCESS state, so the run
    #: completes. "I do not care about the failures."
    SKIP = "skip"
    #: Every item runs (never halts early, exactly like SKIP), and then the failures COUNT:
    #: the container reports the worst item verdict, so any failure fails the run. The
    #: per-item failures are journaled as one `items_collected` ledger record.
    #: "Run everything, then hand me the failures."
    COLLECT = "collect"


class OnError(str, Enum):
    """What a step's FAILURE means for the steps after it: its `config.on_error`. The engine reads
    exactly these two (`tick.fails_the_run`, `gate_answers.tolerates_failure`), so validation
    refuses any other value rather than let it behave as the default unannounced."""

    #: The default: the steps after it still run, and its failure still counts in the run's ending.
    NULL_CONTINUE = "null_continue"
    #: Its failure ends the run there, saying why.
    FAIL_RUN = "fail_run"


class GateKind(str, Enum):
    APPROVAL = "approval"
    VERIFY_COMMAND = "verify_command"
    VERIFY_SCRIPT = "verify_script"
    EVENT = "event"
    EXPRESSION = "expression"
    #: An ordered static→runtime→system ladder with per-criterion hard thresholds. A hard
    #: failure at any rung fails the gate — never averaged, because averaging lets a
    #: confident model pass a gate it structurally failed.
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
    #: The author's human-readable name for this node — what a surface shows a user instead of
    #: the snake_case `id`. A FIRST-CLASS field rather than an `extra` key it used to survive as:
    #: every bundled definition writes it at the node level (92 of them), and while it was
    #: unknown the one consumer that needed it (`materialize.plan_materialization`, which titles
    #: a projected Task) read `config.label` and found nothing, so every materialized task was
    #: titled with its raw node id (#382). Optional, and an empty label means "no better name
    #: than the id" — never a validation failure.
    label: str = ""
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

    _KNOWN = frozenset(
        {"kind", "id", "label", "children", "body", "cases", "default", "config", "needs"}
    )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind.value}
        if self.id:
            d["id"] = self.id
        if self.label:
            d["label"] = self.label
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
            label=str(d.get("label", "") or ""),
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


#: A loop/foreach iteration marker: `@2` or `#3`. Anchored on the digits so a `#` inside a node
#: id cannot be mistaken for one.
INSTANCE_MARKER_RE = re.compile(r"[@#]\d+")


def spec_path(path: str) -> str:
    """An instance path → the SPEC path `walk` produced it from.

    `root.body#3` and `root.body@2` are instances of the same spec node; the state map is keyed by
    instance, but every spec lookup — the node's id, its config, its declared deps — needs the
    shared path.

    Removes each marker IN PLACE rather than truncating at the first or last one, because a marker
    is only trailing when the body is a leaf. Give a loop or foreach a CONTAINER body and the
    marker lands mid-path, which is the shape every bundled loop template uses:

    * truncating broke the spec lookup for anything BELOW a marker —
      `root.children[0].body@0.children[0]` became `root.children[0].body`, so a `wait` nested in a
      loop body resolved to the body SEQUENCE. Measured live: `_wake_due_nodes` read it as a gate
      and every cycle failed with "gate timed out with no answer", for a template holding no gate.
    * and it broke every run SURFACE the same way (#3371): the node list labelled each body
      sibling with the body's id, so two rows both read `step` instead of `work` and `judge`, and
      `inspect_node`/`output()` answered `WF_NODE_NOT_RUN` for a body node that had run and FAILED.
      A loop-body node was reachable under no id at all.
    """
    return INSTANCE_MARKER_RE.sub("", path)


def instance_order(path: str) -> list[Any]:
    """The sort key that orders instance paths NUMERICALLY on their indices.

    A plain string sort puts `children[10]` before `children[2]` and `body@10` before `body@2`, so
    "oldest first" and "the last instance" silently become wrong at the tenth iteration or item:
    a binding's window kept the wrong cycles, `previous.output` returned the wrong one, and the
    node inspector and `output()` answered with item 9 of an eleven-item fan-out. Ten in is late
    enough that no short test sees it. Digit runs compare as numbers and text as text, left to
    right, as the run view's `instancePathOrder` does, so the views and the engine agree.
    """
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r"(\d+)", path)]


#: The instance's OWN marker — the one on its last segment, so `root.body@0.children[1].body#2`
#: matches `#2` and not `@0`.
TRAILING_MARKER_RE = re.compile(r"[@#]\d+$")


def sibling_group(path: str) -> str:
    """An instance path → the group of instances it was expanded ALONGSIDE.

    The right key for *counting* instances, where `spec_path` is wrong: `spec_path` removes EVERY
    marker, so two iterations of a loop containing a three-item fan-out collapse into ONE group of
    six and the fan-out's `[i/total]` denominator becomes the item count times the iteration count
    (#3403 — `main` reported 6 where the answer was 3, for six rows all claiming to be items 1–3).
    Dropping only the trailing marker keeps each expansion distinct: iteration 0's
    `…children[1].body#0` groups with its `…#1`/`…#2` siblings, never with iteration 1's.

    Still `spec_path`, not this, for every SPEC lookup (a node's id, config, declared deps) — those
    need the shared definition path, which is exactly what removing all the markers produces.
    """
    return TRAILING_MARKER_RE.sub("", path)


#: A LOOP iteration marker specifically (`@2`), capturing the number. Distinct from the foreach
#: marker (`#3`) because only a loop has an iteration counter to advance.
_LOOP_MARKER_RE = re.compile(r"@(\d+)")


def loop_parent(path: str) -> tuple[str | None, int]:
    """`root.children[0].body@2` → `("root.children[0]", 2)`.

    The marker need not END the path. A loop whose body is a CONTAINER puts its leaf work
    deeper — `root.children[1].body@0.children[2]` — and the old form required the path to end
    at `@N`, so `int("0.children[2]")` raised, `advance_loop` returned silently, the loop never
    advanced, and the run deadlocked after exactly one iteration. Measured live, and five
    shipped templates use container-bodied loops.

    The INNERMOST marker wins, so a loop nested inside another loop's body advances itself
    rather than its parent.
    """
    matches = list(_LOOP_MARKER_RE.finditer(path))
    if not matches:
        return None, 0
    match = matches[-1]
    body = path[: match.start()]
    if not body.endswith(".body"):
        return None, 0
    return body[: -len(".body")], int(match.group(1))


# ── run timestamps ───────────────────────────────────────────────────────────


def now_stamp() -> str:
    """Now, in the UTC `...Z` form every `started_at` / `completed_at` on a run is written in."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def stamp_epoch(ts: str | None) -> float:
    """Parse a UTC `...Z` stamp to a real epoch.

    `calendar.timegm`, NOT `time.mktime`: mktime reads the struct as LOCAL time, which
    shifts a UTC stamp by the machine's offset. Here it is only ever used as a DIFFERENCE
    of two stamps, so equal offsets cancelled and elapsed time came out right — except
    across a DST boundary, where the two offsets differ and the run's duration was off by
    an hour.
    """
    if not ts:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return 0.0


# ── outcomes ─────────────────────────────────────────────────────────────────


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
    #: A person said no: Deny on an approval gate, or on a step that stopped for them. Not a
    #: failure — nothing went wrong, and `on_error` is a failure policy — and not a pass: nothing
    #: after it runs, and the run ends `declined` (`gate_answers.end_at_gate`).
    DECLINED = "declined"


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
        InstanceState.DECLINED,
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

#: The `terminal_reason` of a step a control refused before it did anything: the action denylist
#: (`guardrails.denylist`), the limits an app holds on a run that is its work (`apps.app_work`),
#: the nesting cap. What the step was for never happened, and the steps after it were written
#: expecting it to have, so its run stops there and says why (`gate_answers.stopping_gate`) the
#: way a step declaring `on_error: fail_run` stops it, whatever the refused step declares: a
#: failure policy does not walk past a control's refusal.
REFUSED_TO_START = "refused_to_start"


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
    #: When a retry can run, as wall-clock epoch seconds. Set only for a retryable failure whose
    #: model provider's circuit breaker is OPEN: a retry before this instant is refused in
    #: microseconds without reaching the provider, so offering it then is offering a failure.
    #: ``None`` for everything else (retry now, or never), and then absent from ``to_dict``.
    retry_at: float | None = None
    #: The model providers a retryable failure's calls went to: the circuit breakers a retry
    #: passes through. Kept so a later read can ask them again, because a breaker can open AFTER
    #: the step failed (any other call to the same provider counts). Absent from ``to_dict`` when
    #: empty.
    providers: list[str] = field(default_factory=list)

    @property
    def retryable(self) -> bool:
        return self.failure_class in RETRYABLE_CLASSES

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "class": self.failure_class.value,
            "cause_plain": self.cause_plain,
            "remediation": self.remediation,
            "recoverable": self.recoverable,
            "retryable": self.retryable,
            "terminal_reason": self.terminal_reason,
            "suggestion": self.suggestion,
        }
        if self.retry_at is not None:
            out["retry_at"] = round(self.retry_at, 3)
        if self.providers:
            out["providers"] = list(self.providers)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Failure:
        raw = str((d or {}).get("class", "internal"))
        try:
            fc = FailureClass(raw)
        except ValueError:
            fc = FailureClass.INTERNAL  # tolerant: an unknown class is not fatal
        retry_at = d.get("retry_at")
        providers = d.get("providers")
        return cls(
            failure_class=fc,
            cause_plain=str(d.get("cause_plain", "") or ""),
            remediation=str(d.get("remediation", "") or ""),
            recoverable=bool(d.get("recoverable", False)),
            terminal_reason=str(d.get("terminal_reason", "") or ""),
            suggestion=str(d.get("suggestion", "") or ""),
            retry_at=float(retry_at) if isinstance(retry_at, (int, float)) else None,
            providers=(
                [p for p in providers if isinstance(p, str) and p]
                if isinstance(providers, list)
                else []
            ),
        )


@dataclass
class FailureSignature:
    """A 4-layer localization record for cheap cross-run diffing."""

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


class LifecyclePhase(str, Enum):
    """What a work-unit status MEANS, separate from the word a noun spells it with.

    "One status vocabulary" is not a rename. `LoopStatus` and `RunStatus` each
    carry members the other cannot express — `loop_run_map.STATUS_VOCABULARY_DELTA` measures the
    ten orphans — and, the part no rename can reconcile, they disagreed about the terminality of
    the SAME word: `failed` stamped an end timestamp and refused any further transition on a run,
    while a failed loop is one of `ACTION_SOURCE_STATES["resume"]`'s five sources.

    That was never drift between two vocabularies. It is **two properties collapsed into one
    set**:

    * **phase** — has the unit stopped producing? A property of the STATE, so it is declared
      once, here, and shared by every noun.
    * **terminality** — may it still transition? A property of the NOUN's lifetime, because a
      run is one attempt while a loop is a campaign OF attempts. Retrying a run means creating
      another run; "retrying" a loop means the same loop moves again.

    So `ENDED` is declared once, each noun names which of its ended states remain resumable, and
    terminality is **derived** from the two. `failed` is then ended for both nouns and terminal
    for only one, with no contradiction and no membership set hand-maintained beside an enum.

    The four phases are exhaustive over both vocabularies by rail
    (`tests/test_lifecycle_phase_vocabulary.py`), which is what makes them a vocabulary rather
    than a convenience: a new status member has no phase until someone decides which one, and
    the rail refuses the merge until they do.
    """

    PRELAUNCH = "prelaunch"  # nothing has executed yet; the spec is still editable
    ACTIVE = "active"  # a worker is, or should be, armed
    ATTENTION = "attention"  # progress stopped pending a human/supervisor act; a worker re-arms
    ENDED = "ended"  # stopped producing; an end timestamp is stamped on arrival


class RunStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    NEEDS_INPUT = "needs_input"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"
    #: A person declined an approval the run needed (`InstanceState.DECLINED`). Its own ending,
    #: not `failed` (nothing went wrong) and not `cancelled` (the run was not stopped from the
    #: outside): the run's error names the gate and who declined it.
    DECLINED = "declined"


#: Every `RunStatus` member's :class:`LifecyclePhase`. Exhaustive in both directions by rail.
RUN_PHASES: dict[RunStatus, LifecyclePhase] = {
    RunStatus.DRAFT: LifecyclePhase.PRELAUNCH,
    RunStatus.RUNNING: LifecyclePhase.ACTIVE,
    RunStatus.PAUSED: LifecyclePhase.ATTENTION,
    RunStatus.NEEDS_INPUT: LifecyclePhase.ATTENTION,
    RunStatus.COMPLETE: LifecyclePhase.ENDED,
    RunStatus.FAILED: LifecyclePhase.ENDED,
    RunStatus.CANCELLED: LifecyclePhase.ENDED,
    RunStatus.ESCALATED: LifecyclePhase.ENDED,
    RunStatus.DECLINED: LifecyclePhase.ENDED,
}

#: Run states that have stopped producing. Read as "an end timestamp belongs here".
ENDED_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    status for status, phase in RUN_PHASES.items() if phase is LifecyclePhase.ENDED
)

#: Ended run states a run may still LEAVE. Deliberately empty, and that emptiness is the
#: decision rather than an omission: a run is one attempt, so every way it can stop is a way it
#: stops for good. `service.request_cancel` refuses a run that is already ended and
#: `service.delete_run` refuses one that is not, and those two guards are only coherent together
#: while nothing leaves an ended run. The loop side's counterpart is NOT empty — see
#: `loop.loop:RESUMABLE_ENDED_STATUSES`, which is exactly where `failed` stops meaning the same
#: thing for the two nouns, stated once instead of implied by two disagreeing frozensets.
RESUMABLE_ENDED_RUN_STATUSES: frozenset[RunStatus] = frozenset()

TERMINAL_RUN_STATUSES: frozenset[RunStatus] = ENDED_RUN_STATUSES - RESUMABLE_ENDED_RUN_STATUSES

#: How a sentence says a run ended: "the workflow run <phrase>". One table, because two surfaces
#: say it — the error a stage's stopped subagent ends with, and the refusal a door gives for an
#: approval that run left behind — and they must not describe one ending two ways.
_RUN_ENDING_PHRASES: dict[RunStatus, str] = {
    RunStatus.CANCELLED: "was cancelled",
    RunStatus.FAILED: "failed",
    RunStatus.COMPLETE: "has finished",
    RunStatus.ESCALATED: "has stopped",
    RunStatus.DECLINED: "was declined",
}


def run_ending(status: RunStatus) -> str:
    """How a run with *status* ended, as the rest of "the workflow run …". A status added
    without a phrase is worded by its own value, so the sentence still reads."""
    return _RUN_ENDING_PHRASES.get(status, f"is {status.value}")


def cancelled_because(reason: str) -> str:
    """The ending of a run that something other than its owner stopped, from the clause its
    cancel carried (`store.request_cancel`): "Stopped because its loop “Release notes” was
    stopped." "" for a Cancel its owner pressed, which needs no sentence."""
    clause = " ".join(str(reason or "").split()).rstrip(" .")
    return f"Stopped because {clause}." if clause else ""


def ended_because(phrase: str, reason: str) -> str:
    """*phrase* ("the workflow run that asked for it was cancelled"), saying why when something
    other than its owner ended the run, from the clause its cancel carried: "… because its chat
    turn was stopped". *phrase* alone for a Cancel its owner pressed."""
    clause = " ".join(str(reason or "").split()).rstrip(" .")
    return f"{phrase} because {clause}" if clause else phrase


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
    """What a trigger-origin start does when the previous run is still going.

    🔴 The branch is `workflows.overlap.decide`, exhaustive with a raising tail, and it is
    the ONLY place that decides. `QUEUE` shipped as a member nothing branched on: the
    run-workflow provider compared against `SKIP` and `CANCEL_PREVIOUS` and let `queue` fall
    through to create+launch, so the one policy whose name promises ordering started a
    CONCURRENT run — the exact behaviour `SKIP`'s comment below says the default exists to
    prevent. A new member must add its own branch there rather than inherit one.
    """

    #: Default. A prior is still going ⇒ nothing is created and nothing starts. A per-minute
    #: trigger must not stack runs.
    SKIP = "skip"
    #: A prior is still going ⇒ the start is PERSISTED as an unlaunched run and started when
    #: that prior ends. Ordering, not concurrency. Capped at `overlap.MAX_QUEUE_DEPTH`; a
    #: start dropped by the cap says so in its outcome rather than reading as queued.
    QUEUE = "queue"
    #: A prior is still going ⇒ cancel it, then start now. The newest fire wins.
    CANCEL_PREVIOUS = "cancel_previous"


# ── def-side records ─────────────────────────────────────────────────────────


@dataclass
class InputParam:
    type: str = "string"
    required: bool = False
    default: Any = None
    help: str = ""
    #: Which `Loop` column a loop-kind launch puts in this input, if any.
    #:
    #: Declared HERE rather than in a table keyed by template name, because the parameter's name is
    #: the template's own choice — measured, the five templates the loop kinds resolve to spell the
    #: task `task`, `brief` and `question` — and a table describing another file's input block is a
    #: copy that drifts. `loop_aliases.template_intake` reads it; `LOOP_INTAKE_FIELDS` closes the
    #: vocabulary and the validator reports a typo at authoring time.
    #:
    #: Empty for every input that is not a loop-kind intake point, which is almost all of them.
    loop_field: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "help": self.help,
        }
        # Omitted when unset rather than serialized as "": every input in the library would
        # otherwise gain a key that means nothing, on a surface (`GET /api/workflows/defs`) the
        # launch dialog renders field by field.
        if self.loop_field:
            d["loop_field"] = self.loop_field
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InputParam:
        return cls(
            type=str((d or {}).get("type", "string") or "string"),
            required=bool(d.get("required", False)),
            default=d.get("default"),
            help=str(d.get("help", "") or ""),
            loop_field=str(d.get("loop_field", "") or "").strip().lower(),
        )


@dataclass
class RunBudget:
    """A run's caps: tokens (``max_tokens``) and dollars (``max_cost``), ``0`` for no cap.

    Soft caps, held where the run books what each attempt at a step used
    (`step_usage.charge`). Reaching one PAUSES the run resumably rather than killing it: its
    owner raises the cap and resumes, which is the difference between a budget and a bomb
    (`resilience.check_budget`). A definition declares them as `defaults.budget`; a run that
    another run starts is held inside what that run has left (:meth:`within`).

    A step's retries are its own `retry.max_attempts`, not a run-wide count.
    """

    max_tokens: int = 0
    max_cost: float = 0.0

    @property
    def is_unlimited(self) -> bool:
        return self.max_tokens <= 0 and self.max_cost <= 0.0

    def left_after(self, tokens: int, dollars: float) -> RunBudget:
        """What these caps leave once *tokens* and *dollars* are spent: the rest of each cap,
        held at the least amount there is when nothing is left, since ``0`` reads as no cap."""
        return RunBudget(
            max_tokens=max(1, self.max_tokens - int(tokens)) if self.max_tokens > 0 else 0,
            max_cost=(
                round(max(MIN_COST_CAP, self.max_cost - float(dollars)), 6)
                if self.max_cost > 0
                else 0.0
            ),
        )

    def within(self, outer: RunBudget) -> RunBudget:
        """The tighter of these caps and *outer*'s in each dimension, where no cap loses to any
        cap: what a run another run starts is held to (:meth:`left_after` of the other's)."""

        def tighter(own: float, theirs: float) -> float:
            return min(own, theirs) if own > 0 and theirs > 0 else max(own, theirs)

        return RunBudget(
            max_tokens=int(tighter(self.max_tokens, outer.max_tokens)),
            max_cost=round(tighter(self.max_cost, outer.max_cost), 6),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"max_tokens": self.max_tokens, "max_cost": self.max_cost}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunBudget:
        """Tolerant of a stored shape: a negative cap reads as none. A definition's own values
        are checked where it is saved (`validator`), so a malformed one never reaches here."""
        d = d or {}
        return cls(
            max_tokens=max(0, int(d.get("max_tokens", 0) or 0)),
            max_cost=max(0.0, float(d.get("max_cost", 0.0) or 0.0)),
        )


#: The least dollar cap a run is held to when what holds it has nothing left: a millionth of a
#: dollar, the precision a step row books cost at. ``0`` would read as no cap at all.
MIN_COST_CAP = 0.000001


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


#: Accepted surfacing modes. An unknown value reads as `off` rather than a surfacing mode: a typo
#: must not silently START surfacing a def, which is the direction that spends tokens and injects
#: text the author did not intend.
_SURFACE_MODES = frozenset({"off", "passive", "suggest"})

#: Accepted escalation modes. Unknown reads as `manual` — the mode that materializes nothing.
_ESCALATION_MODES = frozenset({"manual", "auto"})


def _surface_mode(value: Any) -> str:
    word = str(value or "").strip().lower()
    return word if word in _SURFACE_MODES else "off"


def _escalation(value: Any) -> str:
    word = str(value or "").strip().lower()
    return word if word in _ESCALATION_MODES else "manual"


def _non_negative_int(value: Any) -> int:
    """Coerce to a non-negative int; anything unparseable is 0 (= "no cadence").

    Negative is clamped rather than kept: a negative cadence would make every comparison read as
    overdue, so a fat-fingered `-7` would nag forever.
    """
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass
class DefMetadata:
    """Declared, not inferred. `requirements` is what a run-start preflight checks so a
    missing binary or credential fails BEFORE tokens are spent."""

    risk: str = "low"
    capabilities: list[str] = field(default_factory=list)
    requirements: dict[str, list[str]] = field(default_factory=dict)  # binaries/credentials
    steering_examples: list[dict[str, str]] = field(default_factory=list)

    # The matchable surface. TYPED FIELDS rather than an open metadata
    # dict, because `from_dict` drops anything it does not name — measured, annotating all 18
    # bundled templates with `keywords` left the matcher reading 0/18, so it ran entirely on
    # description overlap while reporting matches at 0.02-0.22 confidence. A control that is
    # present and inert.
    #
    #: T1's inverted-index terms. What a user would actually type.
    keywords: list[str] = field(default_factory=list)
    #: T2's strongest signal: what the template PRODUCES. An intent resembles its desired output
    #: far more than it resembles prose about a workflow.
    example_outputs: list[str] = field(default_factory=list)
    #: T3's constraint — which intent shapes this template serves. Empty means shape-agnostic.
    shapes: list[str] = field(default_factory=list)
    #: Rendered when this template is a REJECTED near-match, so the miss explains itself.
    when_not_to_use: str = ""
    #: A cheaper route for a trivial intent (direct answer / one subagent) instead of a full run.
    lighter_path: str = ""
    #: Named starter parameterizations offered before any generation.
    presets: list[str] = field(default_factory=list)
    #: Free-text phrases for T4's embedding tie-break.
    match_text: str = ""

    # The surfacing contract's def-side fields. TYPED for the same measured
    # reason the block above records — `from_dict` drops what it does not name, so a field kept in
    # an open dict is a field the matcher reads as absent while the author believes it is set.
    #
    #: The surfacing ladder (`off` | `passive` | `suggest`). `off` for a NEW def: OpenSquilla
    #: shipped auto-trigger-by-default and retreated to manual-first after pasted content kept
    #: firing workflows. Explicit `/workflow <name>` invocation always works regardless.
    surface_mode: str = "off"
    #: Verbatim guidance injected in passive mode, between fence markers.
    agent_digest: str = ""
    #: One-line summary for the chip and the list. Not a description — `when_to_use` is that.
    summary: str = ""
    #: When a reader should reach for this def.
    when_to_use: str = ""
    #: Cadence channel: days between intended runs. 0 means the author did not ask to be
    #: nagged — the same reading `ttl: 0` gets.
    cadence_days: int = 0
    #: Cadence escalation mode (`manual` | `auto`). Auto materializes at most one task per day
    #: while overdue.
    escalation: str = "manual"
    #: Fingerprint packs this def belongs to. A def with no pack is not pack-gated.
    packs: list[str] = field(default_factory=list)
    #: Declared hand-off edges: `[{target_def, condition, context_fields, ...}]`.
    hands_off_to: list[dict[str, Any]] = field(default_factory=list)
    #: Blueprint mode: render as a guided conversation rather than injected text.
    guided: bool = False
    #: Whether this template appears as a SKILL on
    #: ``GET /a2a/agent-card`` and is startable through ``POST /a2a/tasks``.
    #:
    #: 🔴 Defaults to FALSE and must stay that way. Publishing is a per-template decision
    #: the owner makes in the template detail UI, because the alternative — published by
    #: default with an opt-out — means turning the A2A surface on silently exposes every
    #: template the user ever authored, including ones whose declared *inputs* are the
    #: sensitive part. Read with ``is True`` below for the same reason ``guided`` is: a
    #: truthy-but-not-True value on disk (``"false"``, ``1``, ``{}``) must not publish a
    #: template, and only an exact ``true`` does.
    a2a_published: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "capabilities": list(self.capabilities),
            "requirements": {k: list(v) for k, v in self.requirements.items()},
            "steering_examples": [dict(e) for e in self.steering_examples],
            "keywords": list(self.keywords),
            "example_outputs": list(self.example_outputs),
            "shapes": list(self.shapes),
            "when_not_to_use": self.when_not_to_use,
            "lighter_path": self.lighter_path,
            "presets": list(self.presets),
            "match_text": self.match_text,
            "surface_mode": self.surface_mode,
            "agent_digest": self.agent_digest,
            "summary": self.summary,
            "when_to_use": self.when_to_use,
            "cadence_days": self.cadence_days,
            "escalation": self.escalation,
            "packs": list(self.packs),
            "hands_off_to": [dict(h) for h in self.hands_off_to],
            "guided": self.guided,
            "a2a_published": self.a2a_published,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DefMetadata:
        d = d or {}
        reqs = d.get("requirements") or {}
        return cls(
            risk=str(d.get("risk", "low") or "low"),
            capabilities=[str(c) for c in (d.get("capabilities") or [])],
            keywords=[str(k) for k in (d.get("keywords") or [])],
            example_outputs=[str(o) for o in (d.get("example_outputs") or [])],
            shapes=[str(sh) for sh in (d.get("shapes") or [])],
            when_not_to_use=str(d.get("when_not_to_use", "") or ""),
            lighter_path=str(d.get("lighter_path", "") or ""),
            presets=[str(pr) for pr in (d.get("presets") or [])],
            match_text=str(d.get("match_text", "") or ""),
            surface_mode=_surface_mode(d.get("surface_mode")),
            agent_digest=str(d.get("agent_digest", "") or ""),
            summary=str(d.get("summary", "") or ""),
            when_to_use=str(d.get("when_to_use", "") or ""),
            cadence_days=_non_negative_int(d.get("cadence_days")),
            escalation=_escalation(d.get("escalation")),
            packs=[str(pk) for pk in (d.get("packs") or [])],
            hands_off_to=[dict(h) for h in (d.get("hands_off_to") or []) if isinstance(h, dict)],
            guided=d.get("guided") is True,
            a2a_published=d.get("a2a_published") is True,
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
    provenance: str = "user"  # who saved this version (`versions.SAVERS`), as its door says
    inputs: dict[str, InputParam] = field(default_factory=dict)
    defaults: RunDefaults = field(default_factory=RunDefaults)
    metadata: DefMetadata = field(default_factory=DefMetadata)
    on_overlap: OverlapPolicy = OverlapPolicy.SKIP
    tags: list[str] = field(default_factory=list)
    #: Opaque to the engine CORE: rendered into stage prompts via bindings
    #: (`{{defaults.runtime_hints.judge.rubric}}`) and consumed by a small set of
    #: engine-ENFORCED invariants (judge isolation, the actor transition rule, the
    #: proof precondition). Deliberately a free dict rather than a typed dataclass —
    #: templates author it as YAML, and a schema here would force every new hint
    #: through a core change. `judge_contract.hints_from_dict` parses the half the
    #: engine enforces, leniently, defaulting to the STRICT reading.
    runtime_hints: dict[str, Any] = field(default_factory=dict)
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
            "runtime_hints",
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
            "runtime_hints": dict(self.runtime_hints),
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
            runtime_hints=(
                dict(d["runtime_hints"]) if isinstance(d.get("runtime_hints"), dict) else {}
            ),
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


def run_work_id(run_id: str) -> str:
    """How a trigger's history row names the workflow run its fire started
    (`ActionResult.work_id`), so the row can say how that run ended when it does
    (`triggers.settle.settle_workflow_run`)."""
    return f"workflow:{run_id}"


@dataclass
class WorkflowRun:
    """One execution. `root_run_id` is propagated through subworkflow spawns, forks and the runs a
    step starts, and indexed with status, so the whole tree of a run is one query rather than a
    recursive walk."""

    id: str
    workflow_name: str
    status: RunStatus = RunStatus.DRAFT
    spec_version: int = 1
    inputs: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    origin: RunOrigin = field(default_factory=RunOrigin)
    parent_run_id: str | None = None
    root_run_id: str = ""
    #: The step of `parent_run_id` that started this run and left it running (a `run-workflow`
    #: step's run): it is its parent's work, and its ending is its own to say, since the step said
    #: only that it launched it. A subworkflow node's child, which the node waits on, names the
    #: node in its origin instead.
    spawned_by_node_id: str | None = None
    branch_key: str | None = None
    forked_from: dict[str, Any] | None = None
    project_id: str = ""
    mode: str = "background"  # blocking | background
    budget: RunBudget = field(default_factory=RunBudget)
    pinned: bool = False
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float = 0.0
    total_tokens: int = 0
    #: What the run's steps booked in dollars (`step_usage.charge`), beside `total_tokens`: what a
    #: dollar cap is held to, and a floor whenever `unpriced_steps` is above zero.
    total_cost_usd: float = 0.0
    #: Attempts whose spend no price covered, in full or at all: a model nothing prices, or calls
    #: cut off before they reported. A dollar cap cannot count what they spent.
    unpriced_steps: int = 0
    #: How many of those its owner let the run go on past (`service.resume_run`). A dollar cap
    #: pauses the run on each one beyond these.
    unpriced_allowed: int = 0
    agent_count: int = 0
    error_message: str = ""
    attention: dict[str, Any] | None = None
    #: The run's SPARSE `SupervisorPolicy` overlay. A template
    #: is SHARED across runs, so it structurally cannot hold a per-instance user setting —
    #: the declared defaults stay where they are (`supervisor_policy.KIND_CONVERGENCE`, a
    #: template's `supervisor:` block) and this dict holds ONLY the knobs this run overrode
    #: (`supervisor_policy.OVERRIDABLE_POLICY_KEYS`). A run that overrides nothing carries `{}`
    #: and persists no state. The write seam (`store.set_policy_overrides`) is strict about
    #: keys; this reader is tolerant — see `from_dict` below.
    policy_overrides: dict[str, Any] = field(default_factory=dict)
    #: Attribution, never authority. `owner_username` answers "whose
    #: run is this" and `origin_harness` "which machine minted it" — both OPTIONAL, stamped at
    #: `store.create` from the shipped primitives (`identity.current_username()` and
    #: `durability.shards.machine_id`), never invented here. Empty is today's behaviour: a run with
    #: no attribution recorded is the owner's, exactly as `Task.author`/`Trigger.author` already
    #: decided — so a foreign row must SAY whose it is. Distinct from the mutation `actor` KIND
    #: (how a change was made, not who), which is untouched.
    owner_username: str = ""
    origin_harness: str = ""
    #: The loop kind this run was started AS, through `POST /api/loops` (a ported loop kind
    #: IS a run). ``""`` for every run that is not a loop — a template started from the Workflows
    #: page, a chat tool, a trigger. This is what makes "a run-backed loop is a loop" answerable
    #: from the run row: the loop listing selects on it, and nothing resolves a TEMPLATE back to a
    #: kind (the one-way rule `loop_aliases` states) — the kind the user asked for is recorded at
    #: the door, the same way `origin` records how the run was started.
    loop_kind: str = ""
    #: The user-facing title of a run started as a loop (the loop's `name`). ``""`` for a run that
    #: is not one, whose surfaces go on labelling it by its template. A declared field rather than a
    #: key in `extra`, because it is shown on every loop surface and `extra` is a tolerant-reader
    #: spillover, not a place a first-class value can be relied on to survive.
    title: str = ""
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
            "mode",
            "budget",
            "pinned",
            "created_at",
            "started_at",
            "completed_at",
            "elapsed_seconds",
            "total_tokens",
            "total_cost_usd",
            "unpriced_steps",
            "unpriced_allowed",
            "agent_count",
            "error_message",
            "attention",
            "policy_overrides",
            "owner_username",
            "origin_harness",
            "loop_kind",
            "title",
        }
    )

    def belongs_to(self, username: str) -> bool:
        """Whether this run is ``username``'s work (mirroring `Task.belongs_to`).

        A run has no assignee, so `owner_username` alone decides. With no username configured
        every run belongs to the owner — a single-user install must behave exactly as it does
        today, and it is the honest answer: with no identity there is nobody else a run could
        belong to. An UNATTRIBUTED run (empty `owner_username` — written before this field, or
        from an unattributed origin) is likewise the owner's, so a foreign row must SAY whose it
        is. That is the same bargain the tasks and triggers seams already struck; it is what keeps
        a foreign-authored run out of the owner's "my runs" count without excluding pre-plan rows.
        """
        owner = (username or "").strip().lower()
        if not owner:
            return True
        author = (self.owner_username or "").strip().lower()
        return not author or author == owner

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
            "mode": self.mode,
            "budget": self.budget.to_dict(),
            "pinned": self.pinned,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "unpriced_steps": self.unpriced_steps,
            "unpriced_allowed": self.unpriced_allowed,
            "agent_count": self.agent_count,
            "error_message": self.error_message,
            "attention": self.attention,
            "policy_overrides": dict(self.policy_overrides),
            "owner_username": self.owner_username,
            "origin_harness": self.origin_harness,
            "loop_kind": self.loop_kind,
            "title": self.title,
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
            mode=str(d.get("mode", "background") or "background"),
            budget=RunBudget.from_dict(d.get("budget") or {}),
            pinned=bool(d.get("pinned", False)),
            created_at=str(d.get("created_at", "") or ""),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            elapsed_seconds=float(d.get("elapsed_seconds", 0.0) or 0.0),
            total_tokens=int(d.get("total_tokens", 0) or 0),
            total_cost_usd=float(d.get("total_cost_usd", 0.0) or 0.0),
            unpriced_steps=int(d.get("unpriced_steps", 0) or 0),
            unpriced_allowed=int(d.get("unpriced_allowed", 0) or 0),
            agent_count=int(d.get("agent_count", 0) or 0),
            error_message=str(d.get("error_message", "") or ""),
            attention=d.get("attention"),
            # Tolerant on the overlay's CONTENTS, deliberately: a key this engine does not
            # recognize (written by a newer core) is preserved so a downgrade round-trips it
            # instead of dropping it on the next save; `apply_policy_overrides` is where an
            # unrecognized key is ignored at resolution time. Strictness lives at the WRITE
            # seam (`store.set_policy_overrides`), the one place a bad key can be refused
            # before it is persisted.
            policy_overrides=dict(d.get("policy_overrides") or {}),
            # Deserialization defaults to "" (today's behaviour = the owner's), NOT to
            # `current_username()`: a pre-plan row that never carried attribution must read back
            # byte-identical but for the empty default, and the create-time stamp is the ONE place
            # a live username/machine_id is minted. Mirrors `Task.author`'s empty-is-owner rule.
            owner_username=str(d.get("owner_username", "") or ""),
            origin_harness=str(d.get("origin_harness", "") or ""),
            loop_kind=str(d.get("loop_kind", "") or ""),
            title=str(d.get("title", "") or ""),
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
    #: a downstream join proceeds instead of waiting forever on it. Explicit
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
    #: A short label for the `foreach` item this instance is processing — what makes
    #: "[3/12] auth.py" possible. PERSISTED because it is the only durable record of WHICH item
    #: an instance was: the items list is re-resolved from a binding, and after the upstream
    #: output changed (or a reload) the label would otherwise be unrecoverable. Empty for a
    #: non-iterated node.
    item_label: str = ""
    #: How many items the `foreach` that produced this instance resolved — the `12` in "[3/12]".
    #: 0 for a node that is not a fan-out item.
    #:
    #: THE denominator, computed once where the items are resolved (`tick._visit_foreach`) and
    #: carried here so the REST node list and the live `workflow_node_started` event report the
    #: same number. Both used to derive it independently and both were wrong (#3403): the REST
    #: list counted instances sharing a `spec_path` (6 for a three-item fan-out in a two-iteration
    #: loop) and the event stream counted the instance map AT DISPATCH, when it holds only the
    #: items dispatched so far — so a twelve-item fan-out streamed `[2/2] [3/3] … [12/12]`, a
    #: denominator carrying no information at all. A count cannot be right at dispatch; the
    #: resolved item count can, so it is what travels.
    #:
    #: PERSISTED, like `item_label`, and stamped on every dispatch rather than only the first: a
    #: rewind that re-expands the fan-out over a different list re-stamps the items it re-runs,
    #: which is what keeps this from going stale.
    item_total: int = 0
    #: The subagent this instance dispatched, for a `stage` node. `dispatch_stage` spawns and
    #: returns RUNNING immediately, so the node's real completion arrives out of band and
    #: `stage_settlement.reconcile_dispatched_stages` needs a way back to the spawn.
    #:
    #: PERSISTED for the same reason as `wake_at`: a gateway restart that re-adopts this run
    #: must still be able to ask who was doing the work. It is a FOREIGN KEY, not a record --
    #: liveness stays owned by `SubagentManager.get` -- and it is per-INSTANCE because a
    #: `foreach` fan-out of stages has one subagent per leaf.
    subagent_id: str = ""
    #: The no-double-execution claim the CURRENT attempt of this instance holds, and the holder
    #: identity it holds it with (#3533). Travels from `engine.dispatch_stage` on `NodeResult` and
    #: is handed back by `_reconcile_dispatched_stages` when the attempt settles FAILED — which is
    #: what makes a retry of a failed instance possible at all. Until it existed the claim had one
    #: way out, its 900s TTL, so a FAILED stage refused its own retry for fifteen minutes with
    #: `another worker holds the claim on this node` and the run then reported COMPLETE.
    #:
    #: PERSISTED for the same reason as `subagent_id`: the lease FILE survives the process, so a
    #: restarted gateway that re-adopts this run must still be able to give the claim back. Cleared
    #: the moment it is released, so a non-empty value always names a claim that is still held.
    claim_target: str = ""
    claim_holder: str = ""
    #: The start the owner allowed for this instance's current attempt, for a `stage` node: the
    #: digest of what it asked (`engine.stage_request_key`) and when she answered. The approval
    #: registry forgets an answer with the process, so without this a restart that cut the
    #: attempt off asked her again, with a new approval, on every surface, and the run waited for
    #: her again. With it, the resumed attempt starts on her answer when it asks the same thing
    #: (`SubagentManager._spawn_grant`); anything else asks: another request, a new attempt after
    #: this one settled, a rewind, an answer older than the stage's own time limit.
    #:
    #: PERSISTED for the reason `subagent_id` is. Written when the controller sees her Allow
    #: (`stage_settlement.reconcile_dispatched_stages`), kept across a restart and a pause, and
    #: cleared when the attempt settles and when a rewind resets the instance.
    approved_request: str = ""
    approved_at: float = 0.0
    #: True when THIS instance's terminal output was served from the resume/rewind cache
    #: rather than freshly produced. The `step_cached` ledger event is the durable
    #: record; this is the projection a status read can answer from without scanning it, which
    #: is what lets the run view mark cached rows on a page load rather than only on the live
    #: event stream.
    #:
    #: Written at exactly the two points in `RunController._launch` where a node's
    #: outcome-origin is decided -- True on a cache hit, False on a fresh dispatch -- so a
    #: rewind that re-runs the node clears it by construction rather than by a reset site
    #: remembering to. Read ONLY for a terminal instance (`_nodes_of` gates on that): between a
    #: rewind and the re-dispatch the instance is PENDING and this still holds the previous
    #: epoch's answer, which is stale for a node that has not run yet.
    cached: bool = False
    #: "ran on X instead of Y: why" for each model call of the current attempt that a later entry
    #: of the user's chain served because the model the step asked for could not. PERSISTED: the
    #: ledger row is written once as the step settles, and a run opened tomorrow reads its node
    #: list from this state file. Cleared at every dispatch (`RunController._launch`), like
    #: `cached`, so it always describes the current attempt.
    model_substituted: list[str] = field(default_factory=list)
    #: What this step asks the person while it WAITS on one — a gate's prompt, a parked action's
    #: question, an action's clarification (`Ask.to_dict()`). Written each time it begins to wait,
    #: and read by `gate_answers.ensure_continuation` for the ask it mints. PER STEP, and
    #: persisted, because `run.attention` is one slot for the whole run: two steps waiting at once
    #: (parallel gates) both asked what the later one had written there.
    ask: dict[str, Any] = field(default_factory=dict)
    #: The calls its owner answered with Deny while this step's current attempt worked
    #: (`SubagentInfo.declined_calls`, as `declined_calls.declined_step` keeps them), for a
    #: `stage`: her decision for the work, which its loop's next cycle must not put to her again by
    #: itself (`declines`). PERSISTED, so a run opened tomorrow, or adopted after a restart, still
    #: says what she declined; cleared at every dispatch, like `cached`.
    declined: list[dict[str, Any]] = field(default_factory=list)

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
            "item_total": self.item_total,
            "subagent_id": self.subagent_id,
            "claim_target": self.claim_target,
            "claim_holder": self.claim_holder,
            "approved_request": self.approved_request,
            "approved_at": self.approved_at,
            "cached": self.cached,
            "model_substituted": list(self.model_substituted),
            "ask": dict(self.ask),
            "declined": [dict(step) for step in self.declined],
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
            item_total=int(d.get("item_total", 0) or 0),
            subagent_id=str(d.get("subagent_id", "") or ""),
            claim_target=str(d.get("claim_target", "") or ""),
            claim_holder=str(d.get("claim_holder", "") or ""),
            approved_request=str(d.get("approved_request", "") or ""),
            approved_at=float(d.get("approved_at", 0.0) or 0.0),
            cached=bool(d.get("cached", False)),
            model_substituted=[str(s) for s in (d.get("model_substituted") or []) if s],
            ask=dict(d.get("ask") or {}) if isinstance(d.get("ask"), dict) else {},
            declined=[dict(s) for s in (d.get("declined") or []) if isinstance(s, dict)],
        )
