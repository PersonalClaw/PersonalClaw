"""An absent cost is not a zero cost, on the ledger primitive itself (#2566).

`ledger.reader.run_totals` seeded its accumulators at zero and summed
``rec.get("cost_usd", 0.0) or 0.0``. The primitive has TWO producers and only one books a cost:

* `workflows/journal.py::Journal.step_completed` writes `cost_usd` on every row — a ``0.0`` there is
  a real observation (a free local model).
* `loop/journal.py::LoopJournal.cycle` writes no `cost_usd` at all. Loop money lives in
  `usage/turns.jsonl` and `loop/manager.py::loop_spend` reads it there — a deliberate deviation
  recorded in that function's own docstring, because booking it here too would be one dollar in two
  stores.

So every loop that has ever run reported ``cost_usd: 0.0``, byte-identical to a genuinely-free run.
Measured on `origin/main` before this change, over two ledgers written by their REAL producers:

    LOOP (2 cycles, real spend in usage/turns.jsonl):
        {"cost_usd": 0.0, "steps_cached": 0, "steps_completed": 2, "steps_failed": 0, "tokens": 0}
    FREE RUN (2 steps, genuinely $0 local model):
        {"cost_usd": 0.0, "steps_cached": 0, "steps_completed": 2, "steps_failed": 0, "tokens": 0}
    keys that differ: NONE

The fix adds `priced` beside the existing keys rather than turning `cost_usd` into
``float | None``: the sum is legitimately a float and every caller's arithmetic depends on it, so
the honesty rides beside the number. The word is `usage_ledger`'s and `loop_spend`'s, unchanged —
``priced=False`` means the figure is a FLOOR and must render as unknown, never ``$0.00``. One word
for one fact across both money surfaces; a synonym would be the very defect shape this fix removes.

Both directions are asserted everywhere, because a suite that only covered the loop case would pass
with `priced` hard-coded to ``False`` and would then misreport every free local run as unmeasured.
"""

from __future__ import annotations

import ast

import pytest
from ledger_priced_shared import DISCLOSURE, _consumer_reason, _reads_money


@pytest.fixture()
def ledger_home(monkeypatch, tmp_path):
    """A real loop store + run store in an isolated home, via the env var the loader honors."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


# ── the two producers, each writing through its own real emitter ──


def _loop_ledger(loop_id: str = "abc12345", cycles: int = 2) -> str:
    """Write a real LOOP ledger with `LoopJournal.cycle` — the producer that books no cost."""
    from personalclaw.loop.journal import LoopJournal

    journal = LoopJournal(loop_id)
    for cycle in range(1, cycles + 1):
        journal.cycle(
            cycle,
            {"summary": f"cycle {cycle}", "stage": "implement", "_source_file": f"f{cycle}.json"},
        )
    return loop_id


def _run_ledger(run_id: str, steps: list[dict]) -> str:
    """Write a real RUN ledger with `Journal.step_completed` — the producer that books a cost."""
    from personalclaw.workflows import journal as J
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

    run_store.save(WorkflowRun(id=run_id, workflow_name="priced-tmpl", status=RunStatus.RUNNING))
    journal = J.Journal(run_id)
    for i, step in enumerate(steps):
        journal.step_completed(
            f"n{i}",
            f"n{i}",
            epoch=0,
            cache_key="",
            state=InstanceState.DONE,
            **step,
        )
    return run_id


def _loop_totals(loop_id: str) -> dict:
    from personalclaw.ledger.reader import run_totals
    from personalclaw.loop import files as loop_files

    return run_totals(loop_files, loop_id)


# ── the primitive: absent is not zero, in both directions ──


def test_a_loop_shaped_ledger_reports_its_cost_as_UNPRICED(ledger_home):
    """The retirement-critical case. A loop `step_completed` carries no cost key at all.

    Once a Loop becomes a `WorkflowRun`, loop-shaped rows flow through run-side readers, and a
    surface trusting `run_totals["cost_usd"]` would report a paid loop as free.
    """
    totals = _loop_totals(_loop_ledger())
    assert totals["steps_completed"] == 2, "vacuity floor: the loop producer wrote nothing"
    assert totals["priced"] is False
    # The float is still a float — every caller's arithmetic keeps working. It is just a FLOOR now,
    # and `priced` is what says so.
    assert totals["cost_usd"] == 0.0
    # And the count is untouched: a step DID complete, which needs no cost key. `cycles_completed`
    # reads exactly this and was honest for both producers all along.
    from personalclaw.loop.journal import cycles_completed

    assert cycles_completed("abc12345") == 2


def test_a_genuinely_free_run_reports_its_zero_as_PRICED(ledger_home):
    """The other direction, and the half that stops `priced` from being hard-coded False.

    A run on a free local model books ``cost_usd: 0.0`` — a measurement. Reporting it as unpriced
    would mirror the same defect: the user told "unknown" about a cost that is known to be nothing.
    """
    from personalclaw.workflows import journal as J

    _run_ledger("run-free", [{"tokens": 0, "cost_usd": 0.0, "model": "local/qwen"}] * 2)
    totals = J.run_totals("run-free")
    assert totals["steps_completed"] == 2, "vacuity floor: the run producer wrote nothing"
    assert totals["priced"] is True, "a measured zero must not be reported as unpriced"
    assert totals["cost_usd"] == 0.0


def test_the_two_zeros_are_now_DISTINGUISHABLE(ledger_home):
    """The measured before/after, as a test: only the two disclosures separate the two facts.

    Before this change the two dicts were byte-identical (see the module docstring). Asserting the
    SET of differing keys is deliberate: ``priced`` distinguishes the cost and ``unpriced_steps``
    counts the rows that make it a floor, while ``tokens`` and ``tokens_recorded`` are #2630's
    nullable-scalar representation of the independent token fact. Nothing else about the
    aggregate may drift.
    """
    from personalclaw.workflows import journal as J

    loop = _loop_totals(_loop_ledger())
    _run_ledger("run-free2", [{"tokens": 0, "cost_usd": 0.0}] * 2)
    free = J.run_totals("run-free2")
    assert loop["steps_completed"] == free["steps_completed"] == 2, "vacuity floor"
    differing = {k for k in set(loop) | set(free) if loop.get(k) != free.get(k)}
    assert differing == {"priced", "unpriced_steps", "tokens", "tokens_recorded"}, differing
    assert loop["priced"] is False and free["priced"] is True
    assert loop["unpriced_steps"] == 2 and free["unpriced_steps"] == 0
    assert loop["tokens"] is None and loop["tokens_recorded"] is False
    assert free["tokens"] == 0 and free["tokens_recorded"] is True


def test_one_unpriced_step_taints_a_mixed_run(ledger_home):
    """One unpriced constituent taints the total — `usage_ledger._fold`'s rule, not a new one.

    A partially-costed run reports the dollars it DID see, marked as a floor. Dropping the figure
    would throw away a real measurement; presenting it as complete is the defect.
    """
    from personalclaw.ledger.writer import EVENTS_FILE
    from personalclaw.workflows import journal as J
    from personalclaw.workflows import store as run_store

    _run_ledger("run-mixed", [{"tokens": 100, "cost_usd": 0.25}])
    # A second `step_completed` with NO cost key — what a loop-shaped row looks like on a run store.
    run_store.append_jsonl(
        "run-mixed", EVENTS_FILE, {"kind": J.STEP_COMPLETED, "node_id": "loopish", "cycle": 1}
    )
    totals = J.run_totals("run-mixed")
    assert totals["steps_completed"] == 2, "vacuity floor: the second row was not appended"
    assert totals["priced"] is False
    assert totals["cost_usd"] == pytest.approx(0.25), "the dollars actually seen are still reported"


def test_an_explicit_null_cost_reads_unpriced_like_an_absent_one(ledger_home):
    """`introspection._carried`'s rule: absent and present-holding-nothing are one fact.

    A producer that writes ``cost_usd: null`` has recorded no cost just as surely as one that writes
    no key, and a total that called the first priced would be honest about the schema and wrong
    about the money.
    """
    from personalclaw.ledger.writer import EVENTS_FILE
    from personalclaw.workflows import journal as J
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    run_store.save(WorkflowRun(id="run-null", workflow_name="t", status=RunStatus.RUNNING))
    run_store.append_jsonl(
        "run-null", EVENTS_FILE, {"kind": J.STEP_COMPLETED, "node_id": "n", "cost_usd": None}
    )
    totals = J.run_totals("run-null")
    assert totals["steps_completed"] == 1, "vacuity floor"
    assert totals["priced"] is False


def test_an_empty_ledger_reports_priced_exactly_as_usage_ledger_does(ledger_home):
    """No completed steps ⇒ ``priced: True``, matching `usage_ledger._blank_agg`.

    "Nothing completed" is already stated by ``steps_completed: 0`` and needs no second hedge — and
    matching the sibling surface's blank aggregate is what keeps ONE word meaning one thing. Read
    out of `_blank_agg` rather than written as ``True`` here, so the two cannot drift apart.
    """
    from personalclaw import usage_ledger
    from personalclaw.workflows import journal as J
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    run_store.save(WorkflowRun(id="run-empty", workflow_name="t", status=RunStatus.RUNNING))
    totals = J.run_totals("run-empty")
    assert totals["steps_completed"] == 0
    assert totals["priced"] is usage_ledger._blank_agg()["priced"]


# ── token recorded-ness: the nullable-scalar sibling (#2630) ──


@pytest.mark.parametrize(
    "run_id,steps,expected_tokens,expected_recorded",
    [
        (
            "tokens-all-recorded",
            [{"tokens": 100, "cost_usd": 0.0}, {"tokens": 200, "cost_usd": 0.0}],
            300,
            True,
        ),
        (
            "tokens-some-null",
            [{"tokens": 100, "cost_usd": 0.0}, {"tokens": None, "cost_usd": 0.0}],
            None,
            False,
        ),
        (
            "tokens-key-absent",
            [{"tokens": 100, "cost_usd": 0.0}, {"cost_usd": 0.0}],
            None,
            False,
        ),
        (
            "tokens-recorded-zero",
            [{"tokens": 0, "cost_usd": 0.0}],
            0,
            True,
        ),
    ],
)
def test_run_totals_separates_unrecorded_tokens_from_recorded_zero(
    ledger_home, run_id, steps, expected_tokens, expected_recorded
):
    """Absent/null taint the count; a real zero remains a measurement."""
    from personalclaw.ledger.writer import EVENTS_FILE
    from personalclaw.workflows import journal as J
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    run_store.save(WorkflowRun(id=run_id, workflow_name="t", status=RunStatus.RUNNING))
    for index, payload in enumerate(steps):
        run_store.append_jsonl(
            run_id,
            EVENTS_FILE,
            {"kind": J.STEP_COMPLETED, "node_id": f"n{index}", **payload},
        )

    totals = J.run_totals(run_id)
    assert totals["steps_completed"] == len(steps), "vacuity floor: no step was aggregated"
    assert totals["tokens"] == expected_tokens
    assert totals["tokens_recorded"] is expected_recorded
    assert totals["priced"] is True, "the token disclosure must not widen the money flag"


# ── one word for one fact: the same key, the same polarity, on both money surfaces ──


def test_the_word_priced_means_the_same_thing_on_both_money_surfaces(ledger_home):
    """`loop_spend`'s `priced` and `run_totals`' `priced` are one vocabulary, not two.

    This is the reason the fix reuses the word instead of minting `has_cost`/`cost_known`: the
    project already had a name for "this money figure is a FLOOR", on the very surface that reads
    loop money. Both are exercised HERE, in one test, so a future divergence in polarity or spelling
    reds rather than being discovered by a user comparing two panels.
    """
    from personalclaw import usage_ledger
    from personalclaw.loop.manager import loop_spend, usage_key

    loop_id = "abc12345"
    # An unpriced turn on the loop's own worker session — what `loop_spend` reads, under the key
    # the chat seam writes a worker's turns under.
    usage_ledger.record_turn(
        usage_ledger.TurnUsage(
            ts="2026-09-07T00:00:00+00:00",
            session_key=usage_key(loop_id),
            source="loop",
            agent="",
            provider="ollama",
            model="local/qwen",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.0,
            priced=False,
        )
    )
    spend = loop_spend(loop_id)
    assert spend["turns"] == 1, "vacuity floor: the turn ledger recorded nothing"
    assert spend["priced"] is False, "loop_spend's own vocabulary"

    # The same loop's LEDGER totals, from the same fact, using the same key with the same polarity.
    ledger_totals = _loop_totals(_loop_ledger(loop_id))
    assert ledger_totals["priced"] is False
    assert "priced" in spend and "priced" in ledger_totals, "one spelling, not two"

    # …and a fully-priced turn flips it on both, so the agreement is not a shared constant.
    usage_ledger.record_turn(
        usage_ledger.TurnUsage(
            ts="2026-09-07T00:00:01+00:00",
            session_key=usage_key("bcd23456"),
            source="loop",
            agent="",
            provider="anthropic",
            model="claude",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.25,
            priced=True,
        )
    )
    assert loop_spend("bcd23456")["priced"] is True


# ── the introspection projection carries the same fact ──


def test_run_stats_carries_priced_and_agrees_with_the_primitive(ledger_home):
    """`RunStats.cost_usd` stays an accumulating float — the decided trade (#2566 ruling 4).

    The strip needs a number to sort by, so the float stays; `priced` is what stops the trade from
    being silent. It must agree with the primitive's answer for the same ledger, or the cockpit and
    the run row would disagree about whether the same dollar was measured.
    """
    from personalclaw.loop.journal import ledger as loop_ledger_read
    from personalclaw.workflows import journal as J
    from personalclaw.workflows.introspection import run_stats

    loop_id = _loop_ledger()
    loop_stats = run_stats(loop_id, loop_ledger_read(loop_id), elapsed_secs=0.0)
    assert loop_stats.steps_completed == 2, "vacuity floor"
    assert loop_stats.priced is False
    assert loop_stats.priced is _loop_totals(loop_id)["priced"]
    assert loop_stats.to_dict()["priced"] is False

    _run_ledger("run-free3", [{"tokens": 0, "cost_usd": 0.0}])
    free_stats = run_stats("run-free3", J.ledger("run-free3"), elapsed_secs=0.0)
    assert free_stats.priced is True, "a measured zero must not be reported as unpriced"
    assert free_stats.priced is J.run_totals("run-free3")["priced"]
    assert free_stats.to_dict()["priced"] is True


def test_a_template_card_is_tainted_by_one_unpriced_run(ledger_home):
    """The aggregate's version: a percentile drawn from a partly-uncosted sample is a FLOOR.

    Both directions, because a card that reported `priced: False` unconditionally would put a "≥" on
    every template in the product and teach the user to ignore it.
    """
    from personalclaw.workflows.introspection import RunStats, template_card

    priced_runs = [RunStats(run_id="a", cost_usd=0.02), RunStats(run_id="b", cost_usd=0.06)]
    clean = template_card("t", priced_runs).to_dict()
    assert clean["runs"] == 2, "vacuity floor"
    assert clean["priced"] is True
    assert clean["cost_p95"] == pytest.approx(0.06)

    tainted = template_card("t", [*priced_runs, RunStats(run_id="c", priced=False)]).to_dict()
    assert tainted["priced"] is False
    # The percentiles are still computed — a floor, not a blank.
    assert tainted["cost_p50"] > 0

    # An empty sample is priced, for the same reason an empty ledger is: there is no unpriced
    # constituent, and `runs: 0` already says the card has no sample.
    assert template_card("t", []).to_dict()["priced"] is True


# ── the rail: a new consumer cannot read `cost_usd` without consulting `priced` ──


_VIOLATION_SNIPPET = """
def render_cost(run_id):
    totals = run_totals(store, run_id)
    return f"${totals['cost_usd']:.4f}"
"""

_COMPLIANT_SNIPPET = """
def render_cost(run_id):
    totals = run_totals(store, run_id)
    if not totals["priced"]:
        return "not recorded"
    return f"${totals['cost_usd']:.4f}"
"""


def test_the_rail_reds_on_a_planted_violation_and_passes_its_fix():
    """The rail's own both-directions self-check, run against source it does not read from disk.

    A structural rail that cannot itself be shown to fire is decoration. Two synthetic snippets
    differing ONLY in the `priced` consultation are checked here so this file proves its detector
    works independently of whatever the tree currently happens to contain — and independently of the
    census floors below, which measure a different thing (coverage, not sensitivity).
    """
    bad = ast.parse(_VIOLATION_SNIPPET).body[0]
    good = ast.parse(_COMPLIANT_SNIPPET).body[0]
    assert _consumer_reason(bad, "") == "calls a money producer"
    assert _consumer_reason(good, "") == "calls a money producer"
    assert _reads_money(bad) and _reads_money(good), "the detector must see the dollar read"
    assert DISCLOSURE not in ast.unparse(bad), "the planted violation must be a violation"
    assert DISCLOSURE in ast.unparse(good), "…and its fix must not be"
