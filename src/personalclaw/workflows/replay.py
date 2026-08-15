"""`workflow replay <run_id>` — re-drive a completed run's decision path and say where it moved.

This is the difference between a resume CACHE and a replay. The journal already serves cached
SUCCESS states on resume, but that only skips redoing work; it never asks *would this run take the
same path today?* — the question a template edit makes urgent and the one a resume cannot answer.
Replay asks it structurally: it re-drives the PURE :func:`~personalclaw.workflows.tick.frontier`
against the run's OWN recorded responses and diffs the resulting trajectory against the one the run
actually took, reporting the FIRST node where they part ways.

Nothing here is retrofitted onto the run — replay is possible because the nondeterminism envelope a
run depended on is already journaled:

* **provider responses** are spilled by `output_ref` (PP-4) — the recorded-response provider hands
  each node back its own recorded output, keyed by that ref, so downstream routing and downstream
  prompts see exactly what the run saw;
* **the resolved prompt** is stored by the controller's `_store_prompt` — so the ORIGINAL
  trajectory carries the prompt each node actually ran, and the replay re-resolves the prompt FRESH
  from the (possibly edited) spec and compares. A prompt edit shows up here and nowhere else;
* **the wall clock** is the one thing `frontier()` cannot supply — it is pure and reads no clock.
  The controller reads it in `_wake_due_nodes` and now journals that read as a `clock_read` event
  (PP-6). Replay resolves a parked node against that RECORDED clock, so a `wait` lands at the same
  point in the trajectory instead of against a live clock that could never match.

**Divergence is a first-class outcome, not a failure.** A template edit is SUPPOSED to diverge; the
verb's job is to name the first node that moved, not to fail. `replay_run` returns cleanly whether
the trajectory is byte-identical or divergent — the caller reads :attr:`ReplayResult.identical` and,
when it is False, :attr:`ReplayResult.first_divergence`.

This module lives on the workflow side, not under `personalclaw.ledger`: reconstructing a trajectory
needs node paths, epochs and `frontier()` — all workflow concepts — and the ledger package may not
import the engine (that seam is what lets a second producer carry a ledger). The ledger's job here
is the reader half: it stored the envelope; this reads it back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from personalclaw.ledger import CLOCK_READ, hash_value
from personalclaw.workflows import execution_hints
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.engine import resolve_config
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    InstanceState,
    Node,
    NodeKind,
    walk,
)
from personalclaw.workflows.tick import frontier

#: A run can only take finitely many steps; this caps a pathological re-drive that never
#: reaches `complete` (a spec shape the driver does not fully model) rather than spinning.
_MAX_TICKS_PER_NODE = 8


# ── the trajectory ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrajectoryStep:
    """One node's place in a run's decision path.

    Equality IS the diff: two steps are the same iff every field matches. The discriminating
    field is `prompt_hash` — the resolved prompt is a node's real input, and comparing the
    RE-RESOLVED prompt (replay) against the RECORDED one (original) is what catches a template
    edit at the node it edited and a perturbed upstream response at that node's first consumer.
    `clock` is non-empty only for a node the wall clock resolved (a `wait`/`gate`), and carries
    the recorded value so a replay that read a live clock instead diverges here.
    """

    path: str
    node_id: str
    kind: str
    state: str
    prompt_hash: str
    output_ref: str
    #: The recorded wall-clock value this node was resolved against, or "" for a node that did
    #: not depend on the clock. A float rounded like the `clock_read` envelope so the two compare.
    clock: str = ""


@dataclass
class Divergence:
    """The first node whose replayed step differs from the original, and how."""

    index: int
    path: str
    node_id: str
    field: str
    original: Any
    replayed: Any

    def describe(self) -> str:
        return (
            f"node {self.node_id or self.path!r} (step {self.index}) diverged on {self.field}: "
            f"recorded {self.original!r}, replayed {self.replayed!r}"
        )


@dataclass
class ReplayResult:
    """The outcome of a replay. `identical` and `first_divergence` are the two things a caller
    reads; the two trajectories are kept for a detailed diff or a UI."""

    run_id: str
    identical: bool
    original: list[TrajectoryStep] = field(default_factory=list)
    replayed: list[TrajectoryStep] = field(default_factory=list)
    first_divergence: Divergence | None = None


# ── the recorded envelope ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Recorded:
    """One node's recorded terminal outcome, as the ledger holds it."""

    state: str
    output_ref: str
    prompt_ref: str


class RecordedResponses:
    """The recorded-response provider — a node's OWN output, keyed off `output_ref` (PP-4).

    A completed run journaled a terminal event per executed leaf, each carrying the `output_ref`
    the run's output was spilled to. Replay hands that same output back so downstream routing and
    downstream prompts see what the run saw — the run does not re-call any model. Keyed by output
    ref rather than recomputed, because the whole point is to reproduce the recorded response, not
    a fresh one.
    """

    def __init__(self, run_id: str, by_path: dict[str, _Recorded]) -> None:
        self._run_id = run_id
        self._by_path = by_path

    def has(self, path: str) -> bool:
        return path in self._by_path

    def state(self, path: str) -> InstanceState:
        rec = self._by_path.get(path)
        if rec is None:
            return InstanceState.FAILED
        try:
            return InstanceState(rec.state)
        except ValueError:
            return InstanceState.FAILED

    def output_ref(self, path: str) -> str:
        rec = self._by_path.get(path)
        return rec.output_ref if rec else ""

    def output(self, path: str) -> Any:
        """The recorded output value, read by `output_ref`."""
        rec = self._by_path.get(path)
        if rec is None or not rec.output_ref:
            return None
        return _read_ref(self._run_id, rec.output_ref)

    def prompt_hash(self, path: str) -> str:
        """The hash of the RECORDED resolved prompt, read by its `resolved_prompt_ref`.

        This is the original trajectory's prompt field — what the node actually ran on.
        """
        rec = self._by_path.get(path)
        prompt = _read_ref(self._run_id, rec.prompt_ref) if rec and rec.prompt_ref else ""
        return hash_value(prompt if isinstance(prompt, str) else "")


class RecordedClock:
    """The recorded wall clock, substituted for the live one at replay (PP-6).

    Built from the run's `clock_read` envelope, keyed by the instance path the clock resolved. A
    node the run resolved against the clock gets its recorded value back; everything else gets "".
    This is the seam that makes a `wait`'s resolution reproducible — reading `time.time()` here
    instead would put a live timestamp in the trajectory that the recorded run could never match,
    which is exactly what the determinism test asserts against.
    """

    def __init__(self, by_path: dict[str, str]) -> None:
        self._by_path = by_path

    def now(self, path: str) -> str:
        return self._by_path.get(path, "")

    def has(self, path: str) -> bool:
        return path in self._by_path


# ── reading the run back ─────────────────────────────────────────────────────────


def _read_ref(run_id: str, ref: str) -> Any:
    """Read a spilled output body strictly by its run-relative `output_ref`.

    Both `outputs/` and `artifacts/` bodies live under the run dir; the ref is resolved and
    confined there so a crafted ref cannot read outside the run. Returns the `output` field or
    None. This is the mechanism the recorded-response provider keys off — the ref, not a recompute.
    """
    if not ref:
        return None
    root = store.run_dir(run_id).resolve()
    try:
        target = (root / ref).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8")).get("output")
    except (OSError, ValueError):
        return None


#: The terminal events that place a node on the trajectory, with the state each implies.
_TERMINAL_KINDS = {
    journal_mod.STEP_COMPLETED: None,  # state read off the event
    journal_mod.STEP_CACHED: None,
    journal_mod.STEP_FAILED: InstanceState.FAILED.value,
    journal_mod.STEP_SKIPPED: InstanceState.SKIPPED.value,
}


def load_recorded(run_id: str) -> tuple[RecordedResponses, RecordedClock, list[dict[str, Any]]]:
    """Fold the run's ledger into the recorded-response provider, the recorded clock, and the raw
    terminal events (in journal order) the original trajectory is projected from.

    Last write per path wins, so a rewound-and-re-run node contributes its latest outcome — the
    same last-write-wins rule the resume cache fold uses.
    """
    events = journal_mod.ledger(run_id)
    by_path: dict[str, _Recorded] = {}
    clock_by_path: dict[str, str] = {}
    terminal: list[dict[str, Any]] = []
    for rec in events:
        kind = rec.get("kind")
        if kind == CLOCK_READ:
            path = str(rec.get("instance_path", "") or "")
            if path:
                clock_by_path[path] = _fmt_clock(rec.get("clock"))
            continue
        if kind in _TERMINAL_KINDS:
            path = str(rec.get("instance_path", "") or "")
            if not path:
                continue
            forced = _TERMINAL_KINDS[kind]
            state = forced if forced is not None else str(rec.get("state", "") or "")
            by_path[path] = _Recorded(
                state=state,
                output_ref=str(rec.get("output_ref", "") or ""),
                prompt_ref=str(rec.get("resolved_prompt_ref", "") or ""),
            )
            terminal.append(rec)
    return RecordedResponses(run_id, by_path), RecordedClock(clock_by_path), terminal


def _fmt_clock(value: Any) -> str:
    """Render a clock value the SAME way on the recorded and replayed side, so a match is a
    byte match. The envelope rounds to 6 places; mirror that and stringify, so the trajectory
    compares a stable token rather than a float whose repr could drift."""
    if not isinstance(value, (int, float)):
        return ""
    return f"{float(value):.6f}"


def _original_trajectory(
    terminal: list[dict[str, Any]],
    responses: RecordedResponses,
    clock: RecordedClock,
    node_kind: dict[str, str],
) -> list[TrajectoryStep]:
    """Project the trajectory the run ACTUALLY took, from its terminal events in journal order.

    The prompt hash comes from the recorded `resolved_prompt_ref` — the prompt the node truly ran
    — and the clock from the recorded envelope. This is ground truth: what a byte-identical replay
    must reproduce.
    """
    steps: list[TrajectoryStep] = []
    for rec in terminal:
        path = str(rec.get("instance_path", "") or "")
        steps.append(
            TrajectoryStep(
                path=path,
                node_id=str(rec.get("node_id", "") or ""),
                kind=node_kind.get(path, ""),
                state=responses.state(path).value,
                prompt_hash=responses.prompt_hash(path),
                output_ref=responses.output_ref(path),
                clock=clock.now(path),
            )
        )
    return steps


# ── the re-drive ────────────────────────────────────────────────────────────────


def _redrive(
    root: Node,
    inputs: dict[str, Any],
    responses: RecordedResponses,
    clock: RecordedClock,
    *,
    single_active_feature: bool,
) -> list[TrajectoryStep]:
    """Re-drive `frontier()` against the recorded responses and build the replayed trajectory.

    `frontier()` is READ, never modified: this loop feeds it the accumulating `states`/`outputs`
    exactly as the controller does, admits its `ready` leaves, resolves them from the recorded
    response, and lets the next `frontier()` derive routing from the outputs it produced. The
    prompt is re-resolved FRESH from the (possibly edited) node here — that fresh hash against the
    recorded one is the whole divergence signal.
    """
    node_by_path = {path: node for path, node in walk(root)}
    states: dict[str, InstanceState] = {}
    outputs: dict[str, Any] = {}
    steps: list[TrajectoryStep] = []
    budget = _MAX_TICKS_PER_NODE * (len(node_by_path) + 1)

    for _ in range(budget):
        fr = frontier(
            root,
            dict(states),
            outputs=dict(outputs),
            inputs=inputs,
            single_active_feature=single_active_feature,
        )
        if fr.complete:
            break
        progressed = False
        # Untaken branch legs are SKIPPED before the ready work, matching the controller's order
        # (`for path in fr.to_skip` precedes launching). Sorted so two replays agree.
        for path in sorted(fr.to_skip):
            if states.get(path) == InstanceState.SKIPPED:
                continue
            node = node_by_path.get(path)
            states[path] = InstanceState.SKIPPED
            steps.append(
                TrajectoryStep(
                    path=path,
                    node_id=node.id if node else "",
                    kind=node.kind.value if node else "",
                    state=InstanceState.SKIPPED.value,
                    prompt_hash=hash_value(""),
                    output_ref="",
                )
            )
            progressed = True
        # `fr.ready` is already path-sorted by `frontier` (deterministic admission).
        for item in fr.ready:
            steps.append(_replay_one(item.node, item.path, outputs, inputs, responses, clock))
            states[item.path] = responses.state(item.path)
            if item.node.id and responses.state(item.path) in SUCCESS_STATES:
                outputs[item.node.id] = responses.output(item.path)
            progressed = True
        if not progressed:
            # Nothing skipped, nothing ready, not complete — a shape the driver does not model
            # (an unresolved wait with no recorded clock, say). Stop rather than spin; the diff
            # then reports the trajectories as they stand.
            break
    return steps


def _replay_one(
    node: Node,
    path: str,
    outputs: dict[str, Any],
    inputs: dict[str, Any],
    responses: RecordedResponses,
    clock: RecordedClock,
) -> TrajectoryStep:
    """Execute one leaf against its recorded response, re-resolving its prompt FRESH.

    The recorded response supplies the state and (elsewhere) the output; the prompt is
    RE-RESOLVED from this node's current config so an edit to it is what the trajectory shows.
    A node the run resolved against the clock reads its recorded value THROUGH the seam.
    """
    ctx = BindingContext(inputs=dict(inputs), node_outputs=dict(outputs))
    prompt = _resolve_prompt(node, ctx)
    state = responses.state(path)
    # The clock read — through the recorded seam, never `time.time()`. Only a node the run
    # actually resolved against the clock (a wait/gate) carries a value; the seam returns "" for
    # every other node, which is what keeps the trajectory of a clock-free run clock-free.
    clock_value = clock.now(path) if clock.has(path) else ""
    return TrajectoryStep(
        path=path,
        node_id=node.id,
        kind=node.kind.value,
        state=state.value,
        prompt_hash=hash_value(prompt),
        output_ref=responses.output_ref(path),
        clock=clock_value,
    )


def _resolve_prompt(node: Node, ctx: BindingContext) -> str:
    """The node's resolved prompt, re-computed exactly as the dispatcher stored it.

    `dispatch_infer`/`dispatch_action`/`dispatch_wait` all resolve config and read `prompt`; a
    node kind with no prompt resolves to "" (its `prompt_hash` then never moves, so an edit to a
    non-prompt node is simply not a prompt divergence). A binding that cannot resolve is treated as
    an empty prompt rather than raised — a replay reports where the run moved, it does not re-fail.
    """
    if node.kind not in (NodeKind.INFER, NodeKind.ACTION, NodeKind.WAIT, NodeKind.VISUALIZE):
        return ""
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return ""
    return str(cfg.get("prompt", "") or "")


# ── the diff ────────────────────────────────────────────────────────────────────


def _diff(original: list[TrajectoryStep], replayed: list[TrajectoryStep]) -> Divergence | None:
    """The FIRST node where the two trajectories part ways, or None if identical.

    Compared positionally, field by field, so the report names both the node and WHAT moved — a
    prompt-hash divergence is a template/input change, a state divergence is a routing change, a
    clock divergence is a broken clock seam. A length mismatch is reported at the first index the
    shorter trajectory lacks.
    """
    for i in range(max(len(original), len(replayed))):
        if i >= len(original):
            step = replayed[i]
            return Divergence(i, step.path, step.node_id, "extra_node", None, step.path)
        if i >= len(replayed):
            step = original[i]
            return Divergence(i, step.path, step.node_id, "missing_node", step.path, None)
        a, b = original[i], replayed[i]
        if a == b:
            continue
        for fname in ("path", "node_id", "kind", "state", "prompt_hash", "output_ref", "clock"):
            av, bv = getattr(a, fname), getattr(b, fname)
            if av != bv:
                return Divergence(i, b.path or a.path, b.node_id or a.node_id, fname, av, bv)
    return None


# ── the verb ─────────────────────────────────────────────────────────────────────


def replay_run(run_id: str) -> ReplayResult:
    """Replay one completed run and report whether — and where — its trajectory has moved.

    Loads the run's CURRENT spec (so a mid-run edit is what replay measures), re-drives
    `frontier()` against the run's recorded responses and recorded clock, and diffs the result
    against the trajectory the run actually took. Divergence is a clean outcome, not an error —
    `ReplayResult.identical` is False and `first_divergence` names the node.
    """
    spec = store.read_spec(run_id)
    if spec is None:
        raise ReplayError(f"run {run_id!r} has no spec to replay")
    run = store.get(run_id)
    inputs = dict(run.inputs) if run and run.inputs else {}
    responses, clock, terminal = load_recorded(run_id)
    if not terminal:
        raise ReplayError(f"run {run_id!r} has no recorded steps to replay")

    root = Node.from_dict(spec.get("root") or {"kind": "sequence"})
    node_kind = {path: node.kind.value for path, node in walk(root)}
    single_active = execution_hints.from_runtime_hints(
        spec.get("runtime_hints")
    ).single_active_feature

    original = _original_trajectory(terminal, responses, clock, node_kind)
    replayed = _redrive(root, inputs, responses, clock, single_active_feature=single_active)
    divergence = _diff(original, replayed)
    return ReplayResult(
        run_id=run_id,
        identical=divergence is None,
        original=original,
        replayed=replayed,
        first_divergence=divergence,
    )


class ReplayError(Exception):
    """A run cannot be replayed at all — no spec, or no recorded steps. Distinct from a
    DIVERGENCE, which is a normal, successful replay outcome."""
