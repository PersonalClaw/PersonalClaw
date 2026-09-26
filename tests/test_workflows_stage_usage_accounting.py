"""A spawned `stage`'s token/cost usage must reach the RUN ROW, and a spawn must be counted.

The defect, measured on the owner's live instance: run ``61899886`` of ``general-project``
finished eight nodes against a REMOTE provider (``openrouter:openrouter/free``, which returns
usage in its response) over 26 minutes and reported ``total_tokens: 0`` and ``agent_count: 0``.

Two independent causes, both wiring gaps rather than bad measurements:

1. **`total_tokens`.** `_apply` books an awaited dispatch's spend
   (``self.run.total_tokens += int(result.tokens)``, ``controller.py:3331``) and a spawned `stage`
   returns at the RUNNING branch far above that line. Its real completion is settled out of band by
   `_reconcile_dispatched_stages`, which read `error`/`reaped`/`result` off `SubagentInfo` and
   ignored the `input_tokens`/`output_tokens`/`cost_usd`/`model` sitting beside them. So a template
   whose only leaves are stages counted nothing. `general-project` is exactly that shape —
   ``loop[sequence[stage, stage]]``, two leaves, both `kind: stage` — which is why the owner's run
   could not have reported anything else.
2. **`agent_count`.** The field had ONE assignment in the entire tree: `WorkflowRun.from_dict`
   reading back its own persisted zero (``models.py:1146``). A declared dataclass field, a
   `to_dict` key and a ``DEFAULT 0`` SQLite column with no writer anywhere — so every run ever
   recorded reports zero agents.

**The spend itself was never lost.** `SubagentInfo.input_tokens`/`output_tokens`/`cost_usd` are
populated from the child's ``EVENT_COMPLETE`` (``subagent.py:2239-2252``) and already feed both the
guardrails spend meter (``subagent.py:1491``, which stops a fan-out mid-flight) and the usage ledger
(``_record_subagent_usage``). What was broken is the run-level ROLL-UP — which matters beyond
display, because `RunBudget.max_tokens` is enforced by reading that roll-up
(`_budget_exceeded`, ``controller.py:4498``). A cap over a counter that never moves is a safety
control that reads as present and cannot fire; `test_a_token_cap_can_actually_fire_on_a_stage_run`
is the arm that pins it.

Note what the fake in `test_workflows_stage_completion` says about itself — "the subset of
`SubagentInfo` that a completion is read from" — and that its subset contains no usage fields at
all. That test is why the settle path exists; this one is why it accounts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    InstanceState,
    RunBudget,
    RunStatus,
    WorkflowRun,
)

STAGE_PATH = "root.children[0]"

#: Deliberate, distinct, and NOT round: the assertions below are equalities against
#: `IN + OUT`, so a transposition or a half-read (input only, output only) fails rather than
#: coincidentally passing. A mock hard-coding "some positive number" would prove nothing about
#: which fields the settle path reads.
IN_TOKENS = 1301
OUT_TOKENS = 407
TOTAL_TOKENS = IN_TOKENS + OUT_TOKENS  # 1708
COST_USD = 0.004271
MODEL = "openrouter/free"

RUN_TIMEOUT = 6.0


class _Info:
    """`SubagentInfo` as the settle path sees it — INCLUDING the usage fields.

    `input_tokens`/`output_tokens`/`cost_usd`/`model` are real attributes of the shipped
    dataclass (``subagent.py:337-339``, ``:333``), set before `done` is ever True. Modelling
    them here is what makes this fixture able to observe the gap: the fake in
    `test_workflows_stage_completion` omits them, so under it a settle path that reads nothing
    and one that reads everything are indistinguishable.
    """

    def __init__(self, agent_id: str, *, usage: bool = True) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""
        self.model = MODEL if usage else ""
        self.input_tokens = IN_TOKENS if usage else 0
        self.output_tokens = OUT_TOKENS if usage else 0
        self.cost_usd = COST_USD if usage else 0.0


class _FakeSubagents:
    """`SubagentManager` on the two methods this path uses: `spawn` and `get`."""

    def __init__(
        self, *, error: str = "", usage: bool = True, never_finishes: bool = False
    ) -> None:
        self.infos: dict[str, _Info] = {}
        self.spawns: list[dict[str, Any]] = []
        self.gets: list[str] = []
        self._error = error
        self._usage = usage
        self._never_finishes = never_finishes

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"sub{len(self.spawns) + 1}", usage=self._usage)
        self.spawns.append(kw)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        """Finish on the first lookup. Keyed on the LOOKUP, never on the wall clock: a sleep
        would measure tick cadence and a frozen clock would read "not yet" forever."""
        self.gets.append(agent_id)
        info = self.infos.get(agent_id)
        if info is not None and not self._never_finishes:
            info.done = True
            info.error = self._error
            info.result = "" if self._error else "the stage's answer"
            info.reaped = bool(self._error)
        return info


class _NoUsageManager(_FakeSubagents):
    """A manager that does not model usage AT ALL — no `input_tokens` attribute to read.

    The injected-dependency guard: a stand-in (or a future runtime that reports nothing) must
    read as zero, not crash the tick. `getattr` defaults are what make that true, and a bare
    `info.input_tokens` would raise here.
    """

    def spawn(self, **kw: Any) -> Any:
        class _Bare:
            def __init__(self, agent_id: str) -> None:
                self.id = agent_id
                self.done = False
                self.error = ""
                self.result = ""
                self.reaped = False

        info = _Bare(f"sub{len(self.spawns) + 1}")
        self.spawns.append(kw)
        self.infos[info.id] = info  # type: ignore[assignment]
        return info


def _spec(stages: int = 1) -> dict[str, Any]:
    return {
        "name": "stage-usage",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "stage", "id": f"work{i}", "config": {"prompt": f"do thing {i}"}}
                for i in range(stages)
            ],
        },
    }


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real `RunController` over a real spec, with only the subagent manager faked."""
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    def _build(
        *,
        stages: int = 1,
        budget: RunBudget | None = None,
        manager: type[_FakeSubagents] = _FakeSubagents,
        **kw: Any,
    ) -> tuple[RunController, _FakeSubagents]:
        fake = manager(**kw)
        spec = _spec(stages)
        run = store.create(
            WorkflowRun(id="", workflow_name="stage-usage", budget=budget or RunBudget())
        )
        store.write_spec(run.id, spec)
        controller = RunController(
            run, spec, services=EngineServices(subagents=fake, cwd=str(tmp_path))
        )
        return controller, fake

    return _build


def _drive(controller: RunController) -> RunStatus:
    return asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))


def _completed_rows(run_id: str) -> list[dict[str, Any]]:
    return journal_mod.ledger(run_id, kinds={journal_mod.STEP_COMPLETED})


# ── the run row ──────────────────────────────────────────────────────────────


def test_a_settled_stage_adds_its_tokens_to_the_RUN_TOTAL(wired):
    """THE defect. On unmodified `main` this is 0 for any number of completed stages."""
    controller, fake = wired()
    status = _drive(controller)

    assert status is RunStatus.COMPLETE, f"the run did not finish (status={status.value})"
    assert fake.spawns, "no stage dispatched, so this test proves nothing"
    assert controller.run.total_tokens == TOTAL_TOKENS, (
        f"the run booked {controller.run.total_tokens} tokens for a stage that reported "
        f"{IN_TOKENS} in + {OUT_TOKENS} out — the roll-up reads neither field"
    )


def test_the_run_total_is_the_SUM_over_every_stage_not_the_last_one(wired):
    """Three stages, so `+=` is distinguishable from `=`. A run that reported only its final
    node's spend would under-count a fan-out exactly where a fan-out is most expensive — the
    failure `engine.py:1673` already records for judge sampling."""
    controller, fake = wired(stages=3)
    _drive(controller)

    assert len(fake.spawns) == 3, f"expected three spawns, got {len(fake.spawns)}"
    assert controller.run.total_tokens == 3 * TOTAL_TOKENS, (
        f"three stages at {TOTAL_TOKENS} tokens each booked {controller.run.total_tokens} — "
        "the roll-up overwrites instead of accumulating"
    )


def test_the_spawn_is_counted_into_AGENT_COUNT(wired):
    """`agent_count` had no writer at all before this: its only assignment in the tree was
    `from_dict` reading back the persisted zero."""
    controller, fake = wired(stages=3)
    _drive(controller)

    assert controller.run.agent_count == 3, (
        f"three subagents were spawned and the run reports {controller.run.agent_count} — "
        "`agent_count` is still a column nothing increments"
    )


def test_agent_count_is_counted_at_the_SPAWN_so_a_LIVE_run_is_not_zero(wired):
    """The reason the counter lives in `_apply`'s RUNNING branch and not beside the token
    roll-up in the settle: a user watching a run with live workers must not read zero agents.

    Driven through the REAL tick loop against a child that never reports `done`, so the
    observation is taken while the stage is genuinely in flight rather than at a hand-picked
    tick boundary. The loop is left running by the timeout, which is the state a user watching
    a long stage is actually looking at.
    """
    controller, fake = wired(never_finishes=True)
    asyncio.run(controller.run_to_completion(timeout=2.0))

    inst = controller.instances[STAGE_PATH]
    assert inst.subagent_id, "the spawn was never observed, so this proves nothing about timing"
    assert inst.state is InstanceState.RUNNING, (
        "the premise is a LIVE stage — this test must observe the count before the node "
        f"settles, and it is already {inst.state.value}"
    )
    assert fake.gets, "the reconciler never polled, so the stage was not actually live"
    assert controller.run.agent_count == 1, (
        "a run with one live subagent reports "
        f"{controller.run.agent_count} agents — the count waits for the settle"
    )
    assert (
        controller.run.total_tokens == 0
    ), "tokens were booked before the child reported any — a spawn is not a spend"
    # And it is VISIBLE: the store is what `service.status()` reads.
    stored = store.get(controller.run.id)
    assert (
        stored is not None and stored.agent_count == 1
    ), "the live run's agent count never reached the run row, so no surface can see it"


def test_a_re_applied_RUNNING_result_does_not_inflate_the_count(wired):
    """Exactly-once per distinct child, driven through the REAL `_apply`.

    The counter is gated on the subagent id CHANGING, so a result applied twice — or a retry
    that re-dispatches the same node and gets the same child back — adds nothing. Driving
    `_apply` matters here: re-deriving the guard's own condition inside the test would assert
    the test's arithmetic rather than the controller's.
    """
    from personalclaw.workflows import tick as tick_mod
    from personalclaw.workflows.controller import _InFlight
    from personalclaw.workflows.engine import NodeResult
    from personalclaw.workflows.journal import CacheKey

    controller, fake = wired()
    node = controller.root.children[0]
    entry = _InFlight(
        # `_apply`'s RUNNING branch never touches the task — it is the awaited-result plumbing,
        # and a spawned stage's result arrives having already finished. Passed as None rather
        # than a fabricated live task, so nothing here depends on an event loop.
        task=None,  # type: ignore[arg-type]
        ready=tick_mod.ReadyNode(path=STAGE_PATH, node=node, lane="llm"),
        started=0.0,
        last_progress=0.0,
        cache_key=CacheKey(path=STAGE_PATH, epoch=0, inputs_hash="y", spec_hash="x"),
    )
    running = NodeResult(state=InstanceState.RUNNING, output={"subagent_id": "sub1"})

    for _ in range(3):
        controller._apply(entry, running)

    assert controller.instances[STAGE_PATH].subagent_id == "sub1"
    assert controller.run.agent_count == 1, (
        f"the same subagent id was counted {controller.run.agent_count} times — the guard is "
        "not on the id changing, so a re-applied result inflates the count"
    )


# ── the ledger row under it ──────────────────────────────────────────────────


def test_the_step_completed_row_carries_the_tokens_model_and_cost(wired):
    """The run row and the rows under it must agree, or a reader reconciling them gets two
    answers. `tokens` on this row is also what `run_totals()["tokens_recorded"]` keys off
    (``ledger/reader.py:108``) — the value `_prepare` pre-charges a capped resume from.
    """
    controller, fake = wired()
    _drive(controller)

    rows = _completed_rows(controller.run.id)
    assert len(rows) == 1, f"expected one completed step, got {len(rows)}: {rows}"
    row = rows[0]
    assert int(row.get("tokens", 0)) == TOTAL_TOKENS, f"ledger row booked no tokens: {row}"
    assert row.get("model") == MODEL, f"the child's model never reached the ledger: {row}"
    assert float(row.get("cost_usd", 0.0)) == pytest.approx(
        COST_USD
    ), f"the child's cost never reached the ledger: {row}"


def test_the_ledger_aggregate_reports_the_spend_as_RECORDED(wired):
    """`tokens_recorded` False is what makes a capped resume PAUSE with "token spend
    unrecorded" (``controller.py:642``). A stage-only run could therefore not resume under a
    cap at all — the token gap broke resume as well as reporting."""
    controller, fake = wired()
    _drive(controller)

    totals = journal_mod.run_totals(controller.run.id)
    assert totals.get("tokens_recorded") is True, f"the ledger reports an unknown spend: {totals}"
    assert int(totals.get("tokens") or 0) == TOTAL_TOKENS, totals


def test_the_run_row_is_PERSISTED_so_the_status_api_can_see_it(wired):
    """`service.status()` is a pure store read (`store.get(run_id)`), so a counter that lives
    only on the controller object is invisible to every surface. `_persist_state` writes
    instances only."""
    controller, fake = wired()
    _drive(controller)

    stored = store.get(controller.run.id)
    assert stored is not None
    assert stored.total_tokens == TOTAL_TOKENS, (
        f"the persisted run row reports {stored.total_tokens} tokens while the controller holds "
        f"{controller.run.total_tokens} — the API reads the row, not the object"
    )
    assert stored.agent_count == 1, f"the persisted row reports {stored.agent_count} agents"


# ── the safety control ──────────────────────────────────────────────────────


def test_a_token_cap_can_actually_fire_on_a_stage_run(wired):
    """🔴 The half that is worse than a display bug.

    `_budget_exceeded` (``controller.py:4498``) enforces `RunBudget.max_tokens` by reading
    `run.total_tokens` — the counter the stage path never moved. So on `main` a capped
    stage-bodied run spends without bound and the cap never trips: a guardrail that reads as
    configured and cannot enforce. The cap here is deliberately BELOW one stage's spend, so a
    single settled child must already exceed it.
    """
    cap = TOTAL_TOKENS - 1
    controller, fake = wired(stages=3, budget=RunBudget(max_tokens=cap))
    status = _drive(controller)

    assert controller.run.total_tokens >= cap, (
        f"the run booked {controller.run.total_tokens} against a cap of {cap} — the meter "
        "cannot see the spend, so nothing can enforce the ceiling"
    )
    assert status is RunStatus.PAUSED, (
        f"a run that spent {controller.run.total_tokens} tokens under a {cap}-token cap ended "
        f"{status.value} — the cap did not fire"
    )
    assert "budget" in (controller.run.error_message or "").lower(), controller.run.error_message
    # The cap must stop the run BEFORE the remaining stages spend: three leaves, and the run
    # pauses having dispatched fewer than all of them.
    assert (
        len(fake.spawns) < 3
    ), f"all {len(fake.spawns)} stages ran despite the cap being exceeded after the first"


# ── the failed child ────────────────────────────────────────────────────────


def test_a_reaped_stage_records_its_spend_on_the_instance_the_row_and_the_run(wired):
    """A reaped child burned its whole deadline, so a record claiming it spent nothing is the
    most misleading row in the ledger.

    A failed attempt charges the run and journals its usage on BOTH settle paths, the same way
    `_apply` does for an awaited dispatch: a failed attempt is not free, and a token cap that only
    saw successes could be walked past by a step that keeps failing. `run_totals` folds the
    `step_failed` row, so the ledger and the run row agree.
    """
    controller, fake = wired(error="Reaped after 900s (exceeded 900s deadline) [stage]")
    _drive(controller)

    inst = controller.instances[STAGE_PATH]
    assert inst.state is InstanceState.FAILED, inst.state
    assert (
        inst.tokens == TOTAL_TOKENS
    ), f"a reaped subagent that spent {TOTAL_TOKENS} tokens left {inst.tokens} on its node"
    assert controller.run.total_tokens == TOTAL_TOKENS

    (row,) = journal_mod.ledger(controller.run.id, kinds={journal_mod.STEP_FAILED})
    assert (row["tokens"], row["model"], row["cost_usd"]) == (TOTAL_TOKENS, MODEL, COST_USD)
    assert journal_mod.run_totals(controller.run.id)["tokens"] == TOTAL_TOKENS


def test_a_manager_that_reports_no_usage_reads_as_zero_and_does_not_crash(wired):
    """The injected-dependency guard. `EngineServices.subagents` is injected, so a stand-in
    without usage attributes must read zero rather than raise inside the tick — an exception in
    the reconciler would strand every RUNNING stage in the run."""
    controller, fake = wired(manager=_NoUsageManager)
    status = _drive(controller)

    assert status is RunStatus.COMPLETE, f"the tick crashed on a usage-less manager: {status}"
    assert controller.instances[STAGE_PATH].state is InstanceState.DONE
    assert controller.run.total_tokens == 0
    assert controller.run.agent_count == 1, "the spawn is still countable without usage fields"
