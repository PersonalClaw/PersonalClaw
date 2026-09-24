"""Tests for the event-trace replay substrate (Session 3): recorder, metrics, baselines.

The recorder half lives in core (`personalclaw.trace_recorder`); the metrics/baseline half
lives in the harness. These prove: recording is off by default (zero overhead), redaction
happens at write, metrics fold correctly (duplicate rate / order violations), and the
baseline gate catches a regression + a missing scenario.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import baselines, replay
from personalclaw import trace_recorder

# ── recorder (core side) ─────────────────────────────────────────────────────


def test_recorder_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_TRACE_DIR", raising=False)
    trace_recorder.reset_for_test()
    assert trace_recorder.is_recording() is False
    # record() is a silent no-op when off (must not raise, must not create files).
    trace_recorder.record("sse", "loop:x", "evt", {"a": 1})


def test_recorder_writes_ndjson_and_redacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_TRACE_DIR", str(tmp_path))
    trace_recorder.reset_for_test()
    assert trace_recorder.is_recording() is True
    # A payload string containing a credential the redactor recognizes (an AWS access key
    # id) must be redacted at write. NOTE: security.redact() is narrower than "all
    # secrets" — it catches AWS keys + exfil URLs but not e.g. bare `sk-`/`ghp_` tokens
    # (recorded as a DISCOVERY in the Self-Verification execution log). The recorder
    # applies whatever redact() catches; this asserts the recorder wires it in, using a
    # value redact() genuinely recognizes.
    trace_recorder.record(
        "mcp", "srv", "call_tool", {"arguments": {"aws": "AKIAIOSFODNN7EXAMPLE here"}}
    )
    files = list(tmp_path.glob("*.ndjson"))
    assert len(files) == 1
    line = files[0].read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert entry["stream"] == "mcp" and entry["type"] == "call_tool"
    # The recognized credential must not survive verbatim in the at-rest trace.
    assert "AKIAIOSFODNN7EXAMPLE" not in line
    assert "REDACTED" in line
    trace_recorder.reset_for_test()


def test_recorder_never_raises_on_bad_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_TRACE_DIR", str(tmp_path))
    trace_recorder.reset_for_test()

    class Unserializable:
        def __repr__(self) -> str:
            return "safe-repr"

    # Must not raise even on a non-JSONable payload (falls back to repr).
    trace_recorder.record("ws", "notification", "note", {"obj": Unserializable()})
    trace_recorder.reset_for_test()


# ── metrics (harness side) ────────────────────────────────────────────────────


def _ev(**kw):
    base = {"ts": 0.0, "stream": "sse", "key": "loop:x", "type": "e", "payload": {}}
    base.update(kw)
    return replay.TraceEvent.from_json(base)


def test_duplicate_event_rate_by_seq_key() -> None:
    events = [
        _ev(type="a", seq=0),
        _ev(type="a", seq=1),
        _ev(type="a", seq=1),  # dup of the previous (same key|type|seq)
    ]
    m = replay.compute_metrics(events)
    assert m.duplicate_event_rate == 1 / 3


def test_order_violation_detected() -> None:
    events = [_ev(seq=0), _ev(seq=1), _ev(seq=0)]  # seq goes backwards
    m = replay.compute_metrics(events)
    assert m.order_violation_count == 1


def test_reconnect_loss_detected() -> None:
    events = [_ev(seq=0), _ev(seq=1), _ev(seq=4)]  # gap 1→4 loses 2 and 3
    m = replay.compute_metrics(events)
    assert m.reconnect_loss_count == 2


def test_clean_stream_has_zero_dupes_and_violations() -> None:
    events = [_ev(type="a", seq=i) for i in range(5)]
    m = replay.compute_metrics(events)
    assert m.duplicate_event_rate == 0.0
    assert m.order_violation_count == 0
    assert m.reconnect_loss_count == 0


def test_fingerprint_fallback_when_no_seq() -> None:
    # Without seq, identical payloads dedupe; distinct payloads don't.
    events = [
        _ev(type="x", payload={"n": 1}),
        _ev(type="x", payload={"n": 1}),  # dup
        _ev(type="x", payload={"n": 2}),  # distinct
    ]
    m = replay.compute_metrics(events)
    assert m.duplicate_event_rate == 1 / 3


# ── baselines gate ────────────────────────────────────────────────────────────


def test_shipped_scenarios_pass_baseline() -> None:
    """The checked-in replay fixtures pass their baselines (Success Criterion #4 shape)."""
    results = baselines.check_baselines()
    assert results, "there should be shipped replay scenarios"
    failed = [r for r in results if not r.ok]
    assert not failed, "shipped replay scenarios breached baseline:\n" + "\n".join(
        f"  {r.scenario}: {r.failures}" for r in failed
    )


def test_regression_breaches_baseline(tmp_path: Path) -> None:
    """A trace that violates order (a re-introduced ordering bug) fails the gate."""
    scen = tmp_path / "regression"
    scen.mkdir()
    # seqs go backwards → order_violation_count > 0 → breaches the hard threshold of 0.
    lines = [
        {"ts": 1.0, "stream": "sse", "key": "loop:r", "type": "e", "seq": 0, "payload": {}},
        {"ts": 1.1, "stream": "sse", "key": "loop:r", "type": "e", "seq": 2, "payload": {}},
        {"ts": 1.2, "stream": "sse", "key": "loop:r", "type": "e", "seq": 1, "payload": {}},
    ]
    (scen / "sse-loop_r.ndjson").write_text(
        "\n".join(json.dumps(x) for x in lines), encoding="utf-8"
    )
    (tmp_path / "baselines.json").write_text(
        json.dumps(
            {
                "scenarios": {
                    "regression": {
                        "metrics": {"latency_p95": {}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    results = baselines.check_baselines(tmp_path)
    reg = next(r for r in results if r.scenario == "regression")
    assert not reg.ok
    assert any("order_violation" in f for f in reg.failures)


def test_missing_scenario_recording_fails(tmp_path: Path) -> None:
    """A baseline entry with no recording on disk fails (missing-scenario-fails)."""
    (tmp_path / "baselines.json").write_text(
        json.dumps({"scenarios": {"ghost": {"metrics": {}}}}), encoding="utf-8"
    )
    results = baselines.check_baselines(tmp_path)
    ghost = next(r for r in results if r.scenario == "ghost")
    assert not ghost.ok
    assert any("missing" in f for f in ghost.failures)


def test_loosened_threshold_without_rationale_fails(tmp_path: Path) -> None:
    scen = tmp_path / "loose"
    scen.mkdir()
    (scen / "sse-x.ndjson").write_text(
        json.dumps({"ts": 1.0, "stream": "sse", "key": "k", "type": "e", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "baselines.json").write_text(
        json.dumps(
            {
                "scenarios": {
                    "loose": {
                        "metrics": {},
                        "thresholds": {"duplicate_event_rate_max": 0.9},  # loosened, no why
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    results = baselines.check_baselines(tmp_path)
    loose = next(r for r in results if r.scenario == "loose")
    assert not loose.ok
    assert any("rationale" in f for f in loose.failures)
