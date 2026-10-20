"""A bench's cost for a model is unknown when a call had no price, and a known $0 on this machine.

The judge bench and the model bake-off read what a candidate's calls cost from the model-call log.
They summed ``dollars_est`` and read a zero total as unknown. So a candidate one of whose calls
nothing priced read as costing what its priced calls did, a floor presented as its cost, and it
could win "cheapest adequate"; and a model on this machine, whose calls are a known $0, read as
unknown and could never win it. The log says which figures are prices now
(``AttemptRecord.priced``).
"""

from __future__ import annotations

import json
import time

import pytest

from personalclaw.evals import bakeoff, judge_bench

READERS = [judge_bench._audit_cost_since, bakeoff._audit_cost_since]


@pytest.fixture
def audit(tmp_path, monkeypatch):
    """Write the model-call log the benches read, in the test's own home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)

    def write(*rows: dict) -> None:
        (tmp_path / "model_calls.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    return write


def _call(dollars: float, *, priced: bool) -> dict:
    """One call's row as the spend guard writes it."""
    return {
        "ts": time.time(),
        "use_case": "background",
        "model": "judge-model",
        "passed": True,
        "dollars_est": dollars,
        "priced": priced,
    }


@pytest.mark.parametrize("reader", READERS)
def test_a_call_nothing_priced_makes_the_cost_unknown(audit, reader):
    started = time.time() - 1
    audit(_call(0.02, priced=True), _call(0.0, priced=False))

    assert reader(started, "background") == (None, "judge-model")


@pytest.mark.parametrize("reader", READERS)
def test_a_model_on_this_machine_costs_a_known_zero(audit, reader):
    started = time.time() - 1
    audit(_call(0.0, priced=True), _call(0.0, priced=True))

    assert reader(started, "background") == (0.0, "judge-model")


@pytest.mark.parametrize("reader", READERS)
def test_calls_all_priced_cost_their_sum(audit, reader):
    started = time.time() - 1
    audit(_call(0.02, priced=True), _call(0.01, priced=True))

    assert reader(started, "background") == (pytest.approx(0.03), "judge-model")
