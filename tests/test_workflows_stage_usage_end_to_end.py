"""The stage usage chain END TO END, with nothing mocked on the usage path.

`test_workflows_stage_usage_accounting.py` asserts the roll-up against a fake manager. That
proves the controller reads the right fields, and it cannot prove the fields are the ones a real
provider fills — a fake that sets `input_tokens` is still the test asserting its own number.

So this file drives the whole chain with REAL code at every link that touches a token count:

    ScriptedProvider (real, from a JSON script on disk)
      → LLMEvent(EVENT_COMPLETE, input_tokens=…, output_tokens=…)   ← real adapter event
      → SubagentManager._run_inner  (``subagent.py:2239-2252``)     ← real population
      → SubagentInfo.input_tokens / output_tokens / cost_usd
      → RunController._reconcile_dispatched_stages                  ← real roll-up
      → run.total_tokens  →  store  →  journal.run_totals()

Only the SessionManager is stood in for, and only to hand the real manager the real
`ScriptedProvider` as its session provider — the seam a live gateway fills with an ACP client.
`ScriptedProvider` is the shipped offline fixture (``llm/scripted.py``): a real
`ModelProvider` whose reply AND token counts come out of the script file, so the numbers
asserted here are the numbers a provider reported, not numbers this test invented.

The script's counts are deliberately distinct and non-round so a half-read (input only, output
only) or a transposition cannot pass by coincidence.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

STAGE_PATH = "root.children[0]"

#: What the PROVIDER reports, out of the script file below.
SCRIPT_IN = 2411
SCRIPT_OUT = 613
SCRIPT_TOTAL = SCRIPT_IN + SCRIPT_OUT  # 3024


@pytest.fixture
def scripted_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home with the scripted-provider opt-in pointed at a real script file.

    Both conditions the fixture's own gate requires (``llm/scripted.py``): the env var names a
    readable script, and `PERSONALCLAW_HOME` is set and is not the real home. The provider
    re-checks them in `start()` as well as `__init__`, so setting them here is what makes it
    constructible at all — it refuses to run against a default home by design.
    """
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "version": 1,
                "on_exhausted": "repeat_last",
                "turns": [
                    {
                        "text": "ACK",
                        "stop_reason": "end_turn",
                        "usage": {
                            "input_tokens": SCRIPT_IN,
                            "output_tokens": SCRIPT_OUT,
                            "cache_creation_tokens": 0,
                            "cache_read_tokens": 0,
                        },
                    }
                ],
            }
        )
    )
    from personalclaw.llm.registry import SCRIPTED_PROVIDER_ENV

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    return tmp_path


def _sessions_over(provider: Any) -> MagicMock:
    """A SessionManager stand-in that hands out *provider* — the one seam kept artificial.

    Shaped exactly like `tests/test_subagent.py::_mock_sessions`, except that `stream` is the
    REAL provider's bound method rather than an empty async generator. That single substitution
    is what turns the existing subagent fixture into a usage-reporting one.
    """
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    return sessions


def _ctx_builder() -> MagicMock:
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("do the thing", None))
    ctx.hooks.on_tool_call = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    return ctx


def _spec() -> dict[str, Any]:
    return {
        "name": "stage-usage-e2e",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "work", "config": {"prompt": "do the thing"}}],
        },
    }


@pytest.fixture
def driven(scripted_home: Path, monkeypatch: pytest.MonkeyPatch):
    """A real `RunController` over a real `SubagentManager` over a real `ScriptedProvider`."""
    from personalclaw.llm.scripted import ScriptedProvider
    from personalclaw.subagent import SubagentManager

    def _build() -> tuple[RunController, Any, Any]:
        provider = ScriptedProvider()
        manager = SubagentManager(
            sessions=_sessions_over(provider),
            ctx_builder=_ctx_builder(),
            # YOLO is approval priority 1 (``subagent.py:1027``). The alternative is wiring an
            # interactive approval callback, which would make this test about the approval path.
            is_yolo=lambda: True,
        )
        run = store.create(WorkflowRun(id="", workflow_name="stage-usage-e2e"))
        store.write_spec(run.id, _spec())
        controller = RunController(
            run,
            _spec(),
            # No `cwd`: `SubagentManager.spawn` validates a non-empty cwd against the operator's
            # allow-roots (`~/workspace`, `~/workplace`) and REFUSES a tmp_path, which would make
            # this test measure the cwd guard instead of the usage chain. An empty cwd is the
            # shipped "wherever the gateway runs" case and skips that check.
            services=EngineServices(subagents=manager, cwd=""),
        )
        return controller, manager, provider

    return _build


def test_the_provider_reports_usage_and_the_RUN_ROW_ends_up_with_it(driven):
    """🔴 The end-to-end assertion the brief asked for: a run completes and its
    `total_tokens` equals what the provider reported, through real code the whole way.

    Every number here is the script's. Nothing in the assertion path sets a token count.
    """
    controller, manager, provider = driven()
    status = asyncio.run(controller.run_to_completion(timeout=25.0))

    inst = controller.instances[STAGE_PATH]
    # The premises first, so a green cannot be a run that never spawned or never settled.
    assert inst.subagent_id, "no subagent was spawned — the chain never started"
    child = manager.get(inst.subagent_id)
    assert child is not None and child.done, f"the child never completed: {child}"
    assert not child.error, f"the child failed, so no usage was reported: {child.error}"

    # The REAL population site (`subagent.py:2239`) read the REAL provider event.
    assert (child.input_tokens, child.output_tokens) == (SCRIPT_IN, SCRIPT_OUT), (
        f"the subagent recorded {child.input_tokens}/{child.output_tokens} where the script "
        f"reported {SCRIPT_IN}/{SCRIPT_OUT} — the adapter seam, not the roll-up"
    )

    assert inst.state is InstanceState.DONE, inst.state
    assert status is RunStatus.COMPLETE, f"the run did not complete (status={status.value})"

    # …and the roll-up carried it to the run row.
    assert controller.run.total_tokens == SCRIPT_TOTAL, (
        f"the provider reported {SCRIPT_TOTAL} tokens and the run row says "
        f"{controller.run.total_tokens}"
    )
    assert controller.run.agent_count == 1, controller.run.agent_count


def test_the_persisted_row_and_the_ledger_agree_with_the_provider(driven):
    """What a SURFACE sees. `service.status()` and `GET /api/workflows/runs` both read the
    store, and the flywheel reads the ledger; all three must arrive at the provider's number."""
    controller, manager, provider = driven()
    asyncio.run(controller.run_to_completion(timeout=25.0))

    stored = store.get(controller.run.id)
    assert stored is not None
    assert (
        stored.total_tokens == SCRIPT_TOTAL
    ), f"the API reads the store, and the store says {stored.total_tokens}"
    assert stored.agent_count == 1, stored.agent_count

    totals = journal_mod.run_totals(controller.run.id)
    assert totals.get("tokens_recorded") is True, totals
    assert int(totals.get("tokens") or 0) == SCRIPT_TOTAL, totals

    rows = journal_mod.ledger(controller.run.id, kinds={journal_mod.STEP_COMPLETED})
    assert len(rows) == 1, rows
    assert int(rows[0].get("tokens", 0)) == SCRIPT_TOTAL, rows[0]


def test_the_control_the_script_is_what_supplies_the_number(scripted_home: Path):
    """The vacuity control. If `ScriptedProvider` reported zero — as an empty-stream fake does
    — every assertion above would be satisfied by a roll-up that reads nothing, and this file
    would prove the opposite of what it claims. So drive the provider directly and show the
    script is the source of the counts.
    """
    from personalclaw.llm.events import EVENT_COMPLETE
    from personalclaw.llm.scripted import ScriptedProvider

    provider = ScriptedProvider()

    async def _collect() -> list[Any]:
        await provider.start()
        return [ev async for ev in provider.stream("do the thing")]

    events = asyncio.run(_collect())
    finals = [e for e in events if e.kind == EVENT_COMPLETE]
    assert len(finals) == 1, f"the fixture emitted {len(finals)} terminal events: {events}"
    assert (finals[0].input_tokens, finals[0].output_tokens) == (
        SCRIPT_IN,
        SCRIPT_OUT,
    ), "the script file is not the source of the token counts asserted in this file"
