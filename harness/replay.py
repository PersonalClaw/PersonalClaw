"""Event-trace replay + metrics (§2.2, Python side) — offline, no gateway.

Reads an NDJSON trace (recorded by ``personalclaw.trace_recorder``) and folds it into
regression metrics that gate against checked-in baselines:

- ``duplicate_event_rate`` — fraction of events that repeat a dedup key. For workflow
  events the key is ``key|type|seq`` (per WF2-R11's specified dedup key); where no ``seq``
  exists, a per-type structural fingerprint (the ClawX fallback) so a genuine re-emit is
  counted but distinct events are not.
- ``event_fanout_ratio`` — events / distinct keys (a proxy for over-broadcast).
- ``order_violation_count`` — events whose ``seq`` goes backwards within a key.
- ``reconnect_loss_count`` — gaps in an otherwise-contiguous ``seq`` sequence per key.
- per-stream p50/p95 inter-event latency (seconds).

This module reads traces only — it never imports core, and core never imports it. The
FE-fold half of replay (chat coalescer / run fold) is the vitest driver in
``web/src/harness/replay.test.ts``; this Python side covers the backend streams.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TraceEvent:
    """One recorded event line."""

    ts: float
    stream: str
    key: str
    type: str
    payload: Any
    seq: int | None = None

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "TraceEvent":
        return cls(
            ts=float(obj.get("ts", 0.0)),
            stream=str(obj.get("stream", "")),
            key=str(obj.get("key", "")),
            type=str(obj.get("type", "")),
            payload=obj.get("payload"),
            seq=obj.get("seq"),
        )


def load_trace(path: str | Path) -> list[TraceEvent]:
    """Load one NDJSON trace file into a list of events (malformed lines skipped)."""
    events: list[TraceEvent] = []
    p = Path(path)
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(TraceEvent.from_json(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return events


def load_scenario(trace_dir: str | Path) -> list[TraceEvent]:
    """Load and merge every ``*.ndjson`` file under ``trace_dir`` into one time-ordered
    event list (a scenario recording spans several stream files). Sorted by ``ts``."""
    d = Path(trace_dir)
    events: list[TraceEvent] = []
    for f in sorted(d.glob("*.ndjson")):
        events.extend(load_trace(f))
    events.sort(key=lambda e: e.ts)
    return events


def _dedup_key(e: TraceEvent) -> str:
    """The dedup identity of an event. Uses ``seq`` when present (the WF2-R11 key shape
    ``key|type|seq``); otherwise a structural fingerprint over the JSON-serialized payload
    (the ClawX fallback) so a true re-emit counts but two distinct same-type events don't.
    """
    if e.seq is not None:
        return f"{e.key}|{e.type}|{e.seq}"
    try:
        fp = json.dumps(e.payload, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        fp = repr(e.payload)
    return f"{e.key}|{e.type}|{fp}"


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (0..100). Empty → 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0, min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[rank]


@dataclass
class Metrics:
    """Replay metrics for a trace. Compared against baselines in :mod:`harness.baselines`."""

    event_count: int = 0
    distinct_keys: int = 0
    duplicate_event_rate: float = 0.0
    event_fanout_ratio: float = 0.0
    order_violation_count: int = 0
    reconnect_loss_count: int = 0
    latency_p50: dict[str, float] = field(default_factory=dict)
    latency_p95: dict[str, float] = field(default_factory=dict)
    #: The terminal state the workflow event-fold law reconstructs (WF2-R11), present only
    #: for a scenario carrying a workflow SSE projection. ``None`` for a non-workflow trace,
    #: so existing (loop/inbox) baselines are untouched. See :func:`fold_workflow`.
    fold: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "event_count": self.event_count,
            "distinct_keys": self.distinct_keys,
            "duplicate_event_rate": round(self.duplicate_event_rate, 6),
            "event_fanout_ratio": round(self.event_fanout_ratio, 4),
            "order_violation_count": self.order_violation_count,
            "reconnect_loss_count": self.reconnect_loss_count,
            "latency_p50": {k: round(v, 4) for k, v in sorted(self.latency_p50.items())},
            "latency_p95": {k: round(v, 4) for k, v in sorted(self.latency_p95.items())},
        }
        if self.fold is not None:
            d["fold"] = self.fold
        return d


def compute_metrics(events: list[TraceEvent]) -> Metrics:
    """Fold a trace into :class:`Metrics`. Pure; deterministic for a given event list."""
    m = Metrics(event_count=len(events))
    if not events:
        return m

    # Duplicate rate over dedup keys.
    seen: set[str] = set()
    duplicates = 0
    keys: set[str] = set()
    for e in events:
        keys.add(e.key)
        dk = _dedup_key(e)
        if dk in seen:
            duplicates += 1
        else:
            seen.add(dk)
    m.distinct_keys = len(keys)
    m.duplicate_event_rate = duplicates / len(events)
    m.event_fanout_ratio = len(events) / len(keys) if keys else 0.0

    # Order violations + reconnect loss, per key, over events that carry a seq.
    per_key_seqs: dict[str, list[int]] = {}
    for e in events:
        if e.seq is not None:
            per_key_seqs.setdefault(e.key, []).append(e.seq)
    for seqs in per_key_seqs.values():
        for a, b in zip(seqs, seqs[1:]):
            if b < a:
                m.order_violation_count += 1
        # Reconnect loss: gaps in the monotonic run (b > a + 1). Only count forward gaps.
        ordered = sorted(set(seqs))
        for a, b in zip(ordered, ordered[1:]):
            if b > a + 1:
                m.reconnect_loss_count += b - a - 1

    # Per-stream inter-event latency percentiles.
    per_stream_ts: dict[str, list[float]] = {}
    for e in events:
        per_stream_ts.setdefault(e.stream, []).append(e.ts)
    for stream, tss in per_stream_ts.items():
        tss.sort()
        deltas = [b - a for a, b in zip(tss, tss[1:]) if b >= a]
        m.latency_p50[stream] = _percentile(deltas, 50)
        m.latency_p95[stream] = _percentile(deltas, 95)

    return m


def metrics_for_scenario(trace_dir: str | Path) -> Metrics:
    """Convenience: load a scenario dir and compute its metrics.

    When the scenario carries a workflow journal→SSE projection (events on a ``workflow:``
    key), the terminal state of the event-fold law (:func:`fold_workflow`) is attached as
    ``Metrics.fold`` so a baseline can pin the invariant — a format change that breaks the
    fold changes this dict and trips the baseline compare (SV-5, Success Criterion #4).
    """
    events = load_scenario(trace_dir)
    m = compute_metrics(events)
    wf = [e for e in events if e.stream == "sse" and e.key.startswith("workflow:")]
    if wf:
        m.fold = fold_workflow(wf)
    return m


# ── workflow journal → SSE projection fold (§2.1 / §2.3, WF2-R11) ─────────────
#
# The Python mirror of ``web/src/pages/workflows/workflowFold.ts``. The FOLD LAW: folding a
# run's SSE events over its (empty, for a from-start recording) snapshot reconstructs exactly
# the state the server would report. This is the invariant SV-5 gates: a change to the
# journal→projection event format that breaks the law changes the terminal fold, so the
# checked-in baseline (:mod:`harness.baselines`) stops matching and the compare FAILS.
#
# Three guards make the law survive rewind + reconnect — the same three the TS fold enforces:
#   1. dedup by deterministic ``event_id`` (a reconnect re-delivers events);
#   2. epoch supersede-drop (an in-flight event from a rewound epoch must not resurrect state);
#   3. node-keyed patches with a per-node ``seq`` floor (out-of-order delivery must not regress
#      a node from done back to running).

_TERMINAL_NODE = frozenset(
    {
        "done",
        "degraded",
        "failed",
        "skipped",
        "no_change",
        "scope_violation",
        "discarded",
        "escalated",
        "blocked",
        "cancelled",
    }
)
_TERMINAL_RUN = frozenset({"complete", "failed", "cancelled", "escalated"})
_COALESCING_UNWRAP = "workflow_batch"


def _batch_members(payload: Any) -> list[tuple[str, Any]]:
    """Unwrap a ``workflow_batch`` frame into its ordered ``(event, payload)`` members.

    Mirrors the FE ``unwrapBatch``: coalescing is a transport optimization, so a batch must
    fold to the exact same sequence the members would have produced unbatched. A malformed
    member is dropped rather than folded as an unknown event.
    """
    raw = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, Any]] = []
    for m in raw:
        if isinstance(m, dict) and isinstance(m.get("event"), str):
            out.append((m["event"], m.get("payload")))
    return out


@dataclass
class _WfState:
    """The folded view-model — the Python analogue of ``WorkflowViewModel``. Only the fields
    the fold law pins are kept; the terminal state serializes to :meth:`snapshot`."""

    run_id: str = ""
    status: str = ""
    nodes: dict[str, str] = field(default_factory=dict)  # instance_path -> state
    node_ids: dict[str, str] = field(default_factory=dict)  # instance_path -> node_id
    epoch: int = 0
    seen: set[str] = field(default_factory=set)
    node_seq: dict[str, int] = field(default_factory=dict)
    dropped: int = 0

    def snapshot(self) -> dict[str, Any]:
        done = sum(1 for st in self.nodes.values() if st in _TERMINAL_NODE)
        total = len(self.nodes)
        return {
            "status": self.status,
            "nodes": dict(sorted(self.nodes.items())),
            "done": done,
            "total": total,
            "progress": round(done / total, 4) if total else 0.0,
            "epoch": self.epoch,
            "dropped": self.dropped,
            "live": self.status not in _TERMINAL_RUN,
        }


def _apply_wf_event(vm: _WfState, event: str, payload: Any) -> None:
    """Fold ONE workflow event into ``vm`` in place. Applies the three guards, then patches."""
    env = payload if isinstance(payload, dict) else {}

    if event == _COALESCING_UNWRAP:
        for sub_event, sub_payload in _batch_members(env):
            _apply_wf_event(vm, sub_event, sub_payload)
        return

    # Guard 1 — an event for a different run is not ours (a shared-hub bug would be silent).
    run_id = env.get("run_id")
    if run_id and vm.run_id and run_id != vm.run_id:
        vm.dropped += 1
        return
    if run_id and not vm.run_id:
        vm.run_id = str(run_id)

    # Guard 2 — dedup by deterministic id (a reconnect replay re-delivers events).
    event_id = env.get("event_id")
    if event_id and event_id in vm.seen:
        vm.dropped += 1
        return

    # Guard 3 — epoch supersede-drop (an event from a rewound epoch must not resurrect state).
    epoch = env.get("epoch")
    epoch_i = epoch if isinstance(epoch, int) else vm.epoch
    if epoch_i < vm.epoch:
        vm.dropped += 1
        return

    if event_id:
        vm.seen.add(str(event_id))
    vm.epoch = max(vm.epoch, epoch_i)

    if event in ("workflow_node_started", "workflow_node_done", "workflow_gate_resolved"):
        default_state = "running" if event == "workflow_node_started" else "done"
        _patch_node(vm, env, default_state)
    elif event == "workflow_run_update":
        status = env.get("status")
        if isinstance(status, str):
            vm.status = status
    elif event == "workflow_progress":
        incoming = env.get("nodes")
        if isinstance(incoming, list):
            for n in incoming:
                if isinstance(n, dict) and isinstance(n.get("instance_path"), str):
                    vm.nodes[n["instance_path"]] = str(n.get("state", ""))
    # needs_input/attention/spec_updated/forked/mutation_rejected + task-projection events do
    # not move node/run terminal state, so they are no-ops for the fold-law snapshot.


def _patch_node(vm: _WfState, env: dict[str, Any], default_state: str) -> None:
    """Patch ONE node by instance path, enforcing the per-node seq floor (guard 3, node half)."""
    path = env.get("instance_path")
    if not isinstance(path, str) or not path:
        return
    seq = env.get("seq")
    if isinstance(seq, int):
        applied = vm.node_seq.get(path)
        if applied is not None and seq < applied:
            vm.dropped += 1
            return
        vm.node_seq[path] = seq
    status = env.get("status")
    state = status if isinstance(status, str) and status else default_state
    vm.nodes[path] = state
    node_id = env.get("node_id")
    if isinstance(node_id, str) and node_id:
        vm.node_ids[path] = node_id


def fold_workflow(events: list[TraceEvent]) -> dict[str, Any]:
    """Fold a workflow SSE projection trace into its terminal state (the fold-law subject).

    ``events`` are the trace's ``sse`` events on a ``workflow:<run_id>`` key, in recorded
    (time) order. Deterministic and pure — no wall-clock, no randomness — so the terminal
    dict is a stable baseline value. Returns the :meth:`_WfState.snapshot` dict.
    """
    vm = _WfState()
    for e in events:
        _apply_wf_event(vm, e.type, e.payload)
    return vm.snapshot()


# ── MCP record/replay-as-fake-server (§2.1 rider) ────────────────────────────


class FakeMcpServer:
    """Replays a recorded ``mcp`` trace as a deterministic offline tool server.

    Built from a trace's ``mcp`` events (recorded at ``mcp_client.call_tool`` — each carries
    ``{tool, arguments, ok, output}``). :meth:`call_tool` looks up the recorded response for
    a ``(tool, arguments)`` pair and returns it, so a tool integration can be debugged
    offline against exactly what the real server returned — the mcporter record/replay
    shape. Deterministic: identical calls in the trace return in recorded order.
    """

    def __init__(self, events: list[TraceEvent]) -> None:
        # Map (tool, canonical-args) -> queue of recorded (ok, output) responses, so a
        # repeated call returns successive recorded results rather than only the first.
        self._responses: dict[tuple[str, str], list[tuple[bool, str]]] = {}
        self._cursor: dict[tuple[str, str], int] = {}
        for e in events:
            if e.stream != "mcp" or e.type != "call_tool":
                continue
            payload = e.payload if isinstance(e.payload, dict) else {}
            tool = str(payload.get("tool", ""))
            args_key = _canonical_args(payload.get("arguments"))
            key = (tool, args_key)
            self._responses.setdefault(key, []).append(
                (bool(payload.get("ok", False)), str(payload.get("output", "")))
            )

    @classmethod
    def from_trace_dir(cls, trace_dir: str | Path) -> "FakeMcpServer":
        return cls(load_scenario(trace_dir))

    def call_tool(self, tool: str, arguments: dict | None = None) -> tuple[bool, str]:
        """Return the recorded ``(ok, output)`` for this call, or a miss tuple.

        Successive identical calls return successive recorded responses (then the last is
        repeated). A call with no recording returns ``(False, "no recorded response …")`` so
        an offline replay surfaces the gap rather than silently fabricating success.
        """
        key = (tool, _canonical_args(arguments))
        responses = self._responses.get(key)
        if not responses:
            return False, f"no recorded response for tool {tool!r} with these arguments"
        idx = self._cursor.get(key, 0)
        resp = responses[min(idx, len(responses) - 1)]
        self._cursor[key] = idx + 1
        return resp

    def recorded_calls(self) -> int:
        """Total recorded call responses (for test assertions)."""
        return sum(len(v) for v in self._responses.values())


def _canonical_args(arguments: object) -> str:
    """A stable string key for a tool's arguments dict (order-independent)."""
    try:
        return json.dumps(arguments, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        return repr(arguments)
