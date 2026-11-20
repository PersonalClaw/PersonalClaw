"""An automation's agent that was stopped, or never ran, starts nothing chained after it.

🔴 The defect (Backlog: "Stopping an automation's agent still starts the automations chained after
it"). Automation B waits on automation A ("when A finishes, post the summary": a `run_completed`
trigger). A's action is an agent, and it is stopped while it works: by her Cancel in the
Background agents list, by a kill of its fan-out, or by a run budget. A's history row read
`stopped`, and B started anyway, as if A's work had finished. Only an agent whose start she
declined was skipped. A workflow run she cancels starts nothing (`run_finish.chain_after_run`), so
the two kinds of work disagreed. And a declined agent's held chain was never dropped, so a later
run of A picked up the path and depth of the one that never ran.

Now the gateway's own end of agent work (`_subagent_done`, through `_init_subagents`) asks one rule,
`subagent_endings.continues_its_chain`: work that ran and ended on its own, finished or failed,
runs what waits on it; work that was stopped, or never ran, runs nothing. The chain it carried is
dropped after every ending. Each member is the `SubagentInfo` its ending leaves: `cancel()` and
`cancel_fanout()` set `cancelled` (and `stopped_by_you` for hers), a start nobody allowed sets
`never_ran`, and a declined one `declined` as well.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from personalclaw import subagent_endings
from personalclaw.subagent import SubagentInfo
from personalclaw.triggers import chain

#: Automation A, whose agent action B waits on.
A = "clock:nightly-research"
#: The chain A's fire carried: it was itself run after another automation.
CARRIED = {chain.DEPTH_KEY: 1, chain.PATH_KEY: ["run_completed:before"]}
TASK = "Research the overnight changes and write a summary"


def _ended(**how: Any) -> SubagentInfo:
    """A's agent, ended as *how* says. Its parent session is the default for an automation's
    agent: none of its own (`run_prompt`'s ``session`` unset), a fresh one per fire."""
    return SubagentInfo(
        id="a1b2c3d4",
        task=TASK,
        parent_session_key="",
        done=True,
        trigger_id=A,
        **how,
    )


#: Each ending, as the path that ends it leaves the member.
HER_STOP = {"cancelled": True, "stopped_by_you": True, "error": "Cancelled by user"}
FANOUT_KILL = {"cancelled": True, "error": "Stopped with the rest of its fan-out"}
RUN_BUDGET = {"cancelled": True, "error": "Stopped: the run's budget of 20 turns is spent"}
NEVER_RAN = {"never_ran": True, "error": "Its start was not allowed in time, so it never ran"}
DECLINED = {"declined": True, "never_ran": True, "error": "spawn declined, so it never started"}
FAILED = {"error": "the model answered 503"}
FINISHED = {"result": "Three changes overnight; the summary is in the notes."}


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The gateway's own end of agent work, with what it chains kept, not fired."""
    from test_gateway import _make_orchestrator, _mock_sessions

    monkeypatch.setattr(chain, "_HELD", {})
    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.ctx_builder = MagicMock()
    orch.dashboard_state = None
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager_type,
    ):
        manager_type.return_value = MagicMock(
            running=[],
            running_agents_for=MagicMock(return_value=[]),
            get=MagicMock(return_value=None),
        )
        orch._init_subagents()
    orch.chained = []

    async def _keep(**kw: Any) -> None:
        orch.chained.append(kw)

    orch._fire_chained_triggers = _keep
    # Its history row is the settle's, and its tests' (`test_a_stop_records_the_run_it_cut_off`).
    monkeypatch.setattr("personalclaw.triggers.settle.settle_agent_run", lambda *a, **kw: False)
    monkeypatch.setattr("personalclaw.gateway.INJECTION_TIMEOUT", 0.01)
    return orch


def _end(gateway: Any, member: SubagentInfo) -> list[dict[str, Any]]:
    """A's fire held its chain while its agent worked; the agent ends as *member*. What the
    gateway chained after it."""
    chain.hold_chain(A, {"trigger_id": A, **CARRIED})

    async def _run() -> None:
        await gateway._announce_subagents([member])
        await asyncio.gather(*list(gateway._background_tasks))

    asyncio.run(_run())
    return gateway.chained


@pytest.mark.parametrize(
    "how",
    [
        pytest.param(HER_STOP, id="her-stop"),
        pytest.param(FANOUT_KILL, id="fan-out-kill"),
        pytest.param(RUN_BUDGET, id="run-budget"),
        pytest.param(NEVER_RAN, id="never-ran"),
    ],
)
def test_an_agent_that_was_stopped_or_never_ran_starts_nothing_after_it(
    gateway: Any, how: dict[str, Any]
) -> None:
    """🔴 Before: B started after each of these, as if A's work had finished."""
    assert _end(gateway, _ended(**how)) == [], "what waits on A started after work that didn't end"
    assert chain.held_chain(A) == {}, "the chain A carried is still held for a later run of A"


def test_an_agent_she_declined_starts_nothing_and_leaves_no_chain_held(gateway: Any) -> None:
    """🔴 Before: nothing started (that half held), but the chain its fire carried stayed held, and
    the next run of A to end picked up its path and depth."""
    assert _end(gateway, _ended(**DECLINED)) == []
    assert chain.held_chain(A) == {}, "a declined agent's chain is still held for a later run of A"


@pytest.mark.parametrize(
    "how",
    [pytest.param(FINISHED, id="finished"), pytest.param(FAILED, id="failed")],
)
def test_an_agent_that_ended_on_its_own_starts_what_waits_on_it_once(
    gateway: Any, how: dict[str, Any]
) -> None:
    """A run that finished, or failed, has ended: what waits on it is told, with the chain it
    carried, as a workflow run's end does."""
    [chained] = _end(gateway, _ended(**how))
    assert chained["source_trigger"] == A
    assert chained["payload"] == CARRIED
    assert chain.held_chain(A) == {}


#: A second automation, B, whose agent ends in the same batch as A's.
B = "clock:hourly-digest"
CARRIED_B = {chain.DEPTH_KEY: 2, chain.PATH_KEY: ["run_completed:before", "run_completed:mid"]}


def test_each_agent_in_one_batch_is_decided_on_its_own_ending(gateway: Any) -> None:
    """Two automations' agents end in one batch (the manager hands the gateway a parent's endings
    together): A's finished and B's was stopped. What waits on A runs, with A's chain, and nothing
    waits on B's; both chains are dropped. The rule is asked of each member, not of the batch."""
    chain.hold_chain(A, {"trigger_id": A, **CARRIED})
    chain.hold_chain(B, {"trigger_id": B, **CARRIED_B})
    finished = _ended(**FINISHED)
    stopped = SubagentInfo(
        id="e5f6a7b8",
        task="Digest the hour",
        parent_session_key="",
        done=True,
        trigger_id=B,
        **HER_STOP,
    )

    async def _run() -> None:
        await gateway._announce_subagents([stopped, finished])
        await asyncio.gather(*list(gateway._background_tasks))

    asyncio.run(_run())
    assert [(kw["source_trigger"], kw["payload"]) for kw in gateway.chained] == [(A, CARRIED)]
    assert chain.held_chain(A) == {} and chain.held_chain(B) == {}


@pytest.mark.parametrize(
    ("how", "continues"),
    [
        (HER_STOP, False),
        (FANOUT_KILL, False),
        (RUN_BUDGET, False),
        (NEVER_RAN, False),
        (DECLINED, False),
        (FAILED, True),
        (FINISHED, True),
        # A flag that isn't True (a mock's attribute, a stray string) is not an ending.
        ({"cancelled": "yes", "never_ran": 1}, True),
    ],
)
def test_the_rule_continues_only_work_that_ran_and_was_not_stopped(
    how: dict[str, Any], continues: bool
) -> None:
    assert subagent_endings.continues_its_chain(_ended(**how)) is continues
