"""Tests for the SV-5 WF2 replay scenarios — the journal-format gate (Success Criterion #4).

SV-5 adds two required replay scenarios on top of the Session-3 substrate:

- ``workflow-journal-projection`` — a workflow run's journal folded into its per-run SSE
  projection, green against the current journal format;
- ``rewind-during-stream`` — a rewind (epoch bump) mid-stream, proving the fold's epoch
  supersede-drop guard drops a stale in-flight event.

Both carry a ``fold`` baseline that pins the event-fold law (WF2-R11): folding the recorded
projection reconstructs exactly one terminal state. These prove the four halves of SC#4:

1. both scenarios are recorded + green against their checked-in baselines;
2. a format change that breaks the event-fold law fails the compare;
3. a missing required scenario fails the run outright;
4. the workflow journal→SSE projection is recordable through the existing SSE tap (no
   engine change) — asserted by driving the recorder at the same seam the gateway uses.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import baselines, replay
from personalclaw import trace_recorder

# The two required WF2 scenarios this atom lands.
_WJP = "workflow-journal-projection"
_RWD = "rewind-during-stream"


def _traces() -> Path:
    return baselines.traces_dir()


# ── 1. both scenarios recorded + green against baseline ───────────────────────


def test_workflow_scenarios_present_and_green() -> None:
    """workflow-journal-projection + rewind-during-stream are recorded and within baseline."""
    results = {r.scenario: r for r in baselines.check_baselines()}
    for scen in (_WJP, _RWD):
        assert scen in results, f"{scen} must be a gated scenario"
        assert results[scen].ok, f"{scen} breached baseline: {results[scen].failures}"


def test_workflow_journal_projection_fold_is_pinned() -> None:
    """The journal-projection baseline pins the event-fold terminal state (the gate itself)."""
    m = replay.metrics_for_scenario(_traces() / _WJP)
    assert m.fold is not None, "the scenario must produce a workflow fold"
    # A clean 3-node run folds to a complete run with all nodes done and nothing dropped.
    assert m.fold["status"] == "complete"
    assert m.fold["done"] == m.fold["total"] == 3
    assert m.fold["dropped"] == 0


def test_rewind_scenario_proves_epoch_supersede_drop() -> None:
    """The rewind scenario's fold DROPS the stale epoch-0 event and lands on epoch 1."""
    m = replay.metrics_for_scenario(_traces() / _RWD)
    assert m.fold is not None
    # The stale epoch-0 node_done that arrived after the rewind bumped the epoch is dropped.
    assert m.fold["dropped"] == 1, "the epoch supersede-drop guard must fire"
    assert m.fold["epoch"] == 1
    assert m.fold["status"] == "complete" and m.fold["done"] == 3


# ── 2. a broken event-fold law fails the compare ──────────────────────────────


def test_broken_fold_law_fails_compare(tmp_path: Path) -> None:
    """A format change that breaks the event-fold law fails the baseline compare (SC#4).

    Simulated by corrupting the fold: a projection whose terminal fold diverges from the
    checked-in baseline (a node the format change left 'running' instead of 'done') must
    trip the exact-match fold gate.
    """
    scen = tmp_path / "wf-corrupt"
    scen.mkdir()
    # A run that never completes its second node — the divergence a fold-law break produces.
    lines = [
        {
            "ts": 1.0,
            "stream": "sse",
            "key": "workflow:c",
            "type": "workflow_run_update",
            "payload": {
                "run_id": "c",
                "event_id": "c-evt-1",
                "seq": 1,
                "epoch": 0,
                "status": "running",
            },
        },
        {
            "ts": 1.1,
            "stream": "sse",
            "key": "workflow:c",
            "type": "workflow_node_done",
            "payload": {
                "run_id": "c",
                "event_id": "c-evt-2",
                "seq": 2,
                "epoch": 0,
                "instance_path": "root.children[0]",
                "node_id": "n0",
                "status": "done",
            },
        },
        {
            "ts": 1.2,
            "stream": "sse",
            "key": "workflow:c",
            "type": "workflow_node_started",
            "payload": {
                "run_id": "c",
                "event_id": "c-evt-3",
                "seq": 3,
                "epoch": 0,
                "instance_path": "root.children[1]",
                "node_id": "n1",
            },
        },
    ]
    (scen / "sse-workflow_c.ndjson").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )
    # The baseline pins a DIFFERENT (complete, 2/2 done) terminal fold — the pre-change law.
    (tmp_path / "baselines.json").write_text(
        json.dumps(
            {
                "scenarios": {
                    "wf-corrupt": {
                        "metrics": {
                            "latency_p95": {},
                            "fold": {
                                "status": "complete",
                                "nodes": {"root.children[0]": "done", "root.children[1]": "done"},
                                "done": 2,
                                "total": 2,
                                "progress": 1.0,
                                "epoch": 0,
                                "dropped": 0,
                                "live": False,
                            },
                        },
                        "thresholds": {"event_fanout_ratio_max": 12.0},
                        "rationale": "test fixture",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    results = baselines.check_baselines(tmp_path)
    reg = next(r for r in results if r.scenario == "wf-corrupt")
    assert not reg.ok
    assert any("event-fold law broke" in f for f in reg.failures), reg.failures


def test_missing_projection_when_fold_pinned_fails(tmp_path: Path) -> None:
    """A baseline that pins a fold but whose recording has NO workflow projection fails.

    This is the other half of the fold gate: if the journal→SSE projection events vanished
    (a format change that stopped emitting them), the trace folds to nothing and the pinned
    baseline can no longer be satisfied — which must be loud, not a silent pass.
    """
    scen = tmp_path / "no-projection"
    scen.mkdir()
    # An SSE trace on a NON-workflow key: produces metrics but no workflow fold.
    (scen / "sse-loop_x.ndjson").write_text(
        json.dumps(
            {"ts": 1.0, "stream": "sse", "key": "loop:x", "type": "queued", "seq": 0, "payload": {}}
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "baselines.json").write_text(
        json.dumps({"scenarios": {"no-projection": {"metrics": {"fold": {"status": "complete"}}}}}),
        encoding="utf-8",
    )
    results = baselines.check_baselines(tmp_path)
    reg = next(r for r in results if r.scenario == "no-projection")
    assert not reg.ok
    assert any("fold invariant missing" in f for f in reg.failures), reg.failures


# ── 3. a missing required scenario fails the run ──────────────────────────────


def test_missing_required_scenario_fails(tmp_path: Path) -> None:
    """A NAMED required scenario absent from disk fails the run outright (SC#4).

    Distinct from a baseline-entry-with-no-recording: here BOTH the recording and any
    baseline entry are gone, which a disk scan alone would read as 'simply not present'.
    The named required set is what turns that silent drop into a failure.
    """
    # An empty traces dir with an empty baselines file: no recordings, no baseline entries.
    (tmp_path / "baselines.json").write_text(json.dumps({"scenarios": {}}), encoding="utf-8")
    results = baselines.check_baselines(tmp_path)
    by_scen = {r.scenario: r for r in results}
    for scen in (_WJP, _RWD):
        assert scen in by_scen, f"{scen} absence must be reported as a failure"
        assert not by_scen[scen].ok
        assert any("missing" in f for f in by_scen[scen].failures)


def test_required_set_contains_both_workflow_scenarios() -> None:
    """The two WF2 scenarios are declared required (the gate is not opt-in)."""
    assert _WJP in baselines.REQUIRED_SCENARIOS
    assert _RWD in baselines.REQUIRED_SCENARIOS


# ── 4. the journal→SSE projection is recordable through the existing tap ──────


def test_workflow_projection_recordable_via_sse_tap(tmp_path: Path, monkeypatch) -> None:
    """Recording a workflow projection needs NO engine change — the SSE tap covers it.

    The gateway publishes workflow events through ``SseRegistry.publish`` on a
    ``workflow:<run_id>`` key, which already calls ``trace_recorder.record('sse', key, ...)``.
    Driving that same seam here produces a scenario the fold reads — proving §2.1's
    'no tap needed' claim end to end, and that a recorded projection round-trips through the
    Python fold to the same terminal state the checked-in fixture pins.
    """
    monkeypatch.setenv("PERSONALCLAW_TRACE_DIR", str(tmp_path))
    trace_recorder.reset_for_test()
    key = "workflow:live"
    frames = [
        (
            "workflow_run_update",
            {"run_id": "live", "event_id": "live-evt-1", "seq": 1, "epoch": 0, "status": "running"},
        ),
        (
            "workflow_node_started",
            {
                "run_id": "live",
                "event_id": "live-evt-2",
                "seq": 2,
                "epoch": 0,
                "instance_path": "root.children[0]",
                "node_id": "a",
            },
        ),
        (
            "workflow_node_done",
            {
                "run_id": "live",
                "event_id": "live-evt-3",
                "seq": 3,
                "epoch": 0,
                "instance_path": "root.children[0]",
                "node_id": "a",
                "status": "done",
            },
        ),
        (
            "workflow_run_update",
            {
                "run_id": "live",
                "event_id": "live-evt-4",
                "seq": 4,
                "epoch": 0,
                "status": "complete",
            },
        ),
    ]
    # This is exactly what dashboard/sse.py:148 does inside SseRegistry.publish.
    for event, payload in frames:
        trace_recorder.record("sse", key, event, payload)
    trace_recorder.reset_for_test()

    m = replay.metrics_for_scenario(tmp_path)
    assert m.fold is not None, "the recorded workflow projection must fold"
    assert m.fold["status"] == "complete"
    assert m.fold["done"] == m.fold["total"] == 1
    assert m.fold["dropped"] == 0


def test_fold_is_deterministic_and_pure() -> None:
    """The fold is deterministic (no wall-clock/random) — a baseline value is stable."""
    events = replay.load_scenario(_traces() / _WJP)
    wf = [e for e in events if e.stream == "sse" and e.key.startswith("workflow:")]
    assert replay.fold_workflow(wf) == replay.fold_workflow(wf)
    # A full replay (reconnect) of the same events is idempotent: every re-delivery is a
    # dedup no-op, so the terminal fold is unchanged and only 'dropped' grows.
    once = replay.fold_workflow(wf)
    twice = replay.fold_workflow(wf + wf)
    assert twice["status"] == once["status"]
    assert twice["nodes"] == once["nodes"]
    assert twice["dropped"] == once["dropped"] + len(wf)
