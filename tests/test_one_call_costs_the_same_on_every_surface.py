"""A model call costs one figure, and Usage, the daily budget and the model-call log all show it.

A Code-loop planning session on Claude Opus through Bedrock read $2.50 in Usage, $3.60 in the
model-call log and $3.95 on the daily budget meter. Three things let the figures part:

* The calls of a turn that ended in an error were in no usage row. The usage row of an agent's turn
  was written from the turn's closing event only, and a turn the daily dollar cap stopped (or any
  other failure) has none, so the five calls it had already made, and been charged for, reached the
  meter and the call log and never Usage ($1.11 of the gap). The budget meter's figure is the
  whole day's, every session's, which is the rest.
* Each surface priced the call itself. The guard priced each call at the model it was built for and
  charged the meter and wrote the call log from that; the usage row priced the turn's summed tokens
  again at the model that answered. An agent whose definition names another model than its
  provider entry's default was charged for the entry's model and shown at its own, and a provider's
  own cost on one call of a turn was taken as the cost of the whole turn.
* The native loop dated its own attempt rows by a monotonic clock (they read as 1973) and named the
  guard's class as their provider.

So the guard prices each call once, at the model it was asked to call, and that one price is
charged to the meter, written to the call log, and carried on the call's closing event; the agent's
turn sums those prices into its usage row instead of pricing the turn again; and a turn that ends
in an error first says what its finished calls spent, so its usage row is written for them.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
from personalclaw.agents.native import runtime as runtime_mod
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.guardrails.budgets import Budget, SpendMeter
from personalclaw.guardrails.failure import BudgetExceededError
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.llm_helpers import stream_and_collect
from personalclaw.routing import rates as rates_mod
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from personalclaw.usage_ledger import Attribution, recorder

OPUS = "global.anthropic.claude-opus-5-5"
SONNET = "global.anthropic.claude-sonnet-5-5"
PLANNING = Attribution(source="loop", session_key="dashboard:loop-plan-a1")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


def _usage(home: Path) -> list[dict]:
    path = home / "usage" / "turns.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _calls(home: Path) -> list[dict]:
    path = home / "model_calls.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _guard_rows(home: Path) -> list[dict]:
    """The rows of the calls the guard made (the native loop writes its own beside them)."""
    return [row for row in _calls(home) if row["use_case"] == "loops"]


def _done(**usage) -> AgentEvent:
    return AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn", **usage)


def _step(n: int, **usage) -> list[AgentEvent]:
    return [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=f"c{n}", title="look", tool_input="{}"),
        _done(**usage),
    ]


def _answer(**usage) -> list[AgentEvent]:
    return [AgentEvent(kind=EVENT_TEXT_CHUNK, text="The plan has three tasks."), _done(**usage)]


class _Bedrock:
    """A Bedrock model entry whose default model is Sonnet, replaying one scripted inference per
    request (an exception in the script is raised instead), and keeping the model each request
    named."""

    supports_tools = True

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.asked: list[str] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.asked.append(model or "")
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        for event in step:
            yield event

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _Look(ToolProvider):
    @property
    def name(self) -> str:
        return "look"

    @property
    def display_name(self) -> str:
        return "Look"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="look",
                description="Read the repository.",
                parameters={"type": "object"},
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output="three modules, one test each")


def _planner(
    home: Path,
    meter: SpendMeter,
    script: list,
    *,
    names: str = OPUS,
    built_for: str = OPUS,
    cap: float = 0.0,
) -> tuple[NativeAgentRuntime, _Bedrock]:
    """A planning agent whose definition names *names*, on the Bedrock entry, through a guard
    built for *built_for* (what the resolution seam built the entry for) under a day's cap."""
    model = _Bedrock(script)
    guard = wrap_model_call_guard(
        model,
        use_case="loops",
        provider_name="bedrock",
        model=built_for,
        budget=Budget(max_dollars=cap),
        meter=meter,
    )
    # What the resolution seam stamps on the provider it built (``provider_bridge``).
    guard.served_ref = f"bedrock:{built_for}"
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="planner", provider="native", model=names),
        model_provider=guard,
        tool_providers=[_Look()],
        cwd=home,
    )
    runtime.set_approval_policy("auto")
    return runtime, model


async def _plan(runtime: NativeAgentRuntime) -> str:
    await runtime.start()
    return await stream_and_collect(
        runtime, "Plan the change.", on_complete=recorder(runtime, PLANNING)
    )


def _three_figures(home: Path, meter: SpendMeter) -> tuple[float, float, float]:
    return (
        round(sum(row["cost_usd"] for row in _usage(home)), 6),
        round(meter.day_totals().dollars, 6),
        round(sum(row["dollars_est"] for row in _guard_rows(home)), 6),
    )


# ── One call ─────────────────────────────────────────────────────────────────────────────────


def test_one_call_costs_the_same_in_usage_the_budget_and_the_call_log(home):
    meter = SpendMeter(config_dir=home)
    usage = dict(
        input_tokens=1_234,
        output_tokens=567,
        cache_read_tokens=8_910,
        cache_creation_tokens=1_112,
    )
    runtime, _ = _planner(home, meter, [_answer(**usage)])

    asyncio.run(_plan(runtime))

    # Opus 5.5: $4 in, $20 out, $0.20 a cache read, $5 a cache write, per 1M tokens.
    expected = (1_234 * 4 + 567 * 20 + 8_910 * 0.2 + 1_112 * 5) / 1e6
    assert _three_figures(home, meter) == (expected, expected, expected)
    (row,) = _usage(home)
    (call,) = _guard_rows(home)
    assert row["audit_ids"] == [call["audit_id"]]
    assert (row["cache_read_tokens"], row["cache_creation_tokens"]) == (8_910, 1_112)


def test_a_call_to_the_model_an_agent_names_is_charged_as_that_model(home):
    """The guard was built for the entry's default model (Sonnet); the agent's definition names
    Opus, and Opus answers. The meter and the call log were charged Sonnet's price while Usage
    showed Opus's."""
    meter = SpendMeter(config_dir=home)
    runtime, model = _planner(
        home,
        meter,
        [_answer(input_tokens=100_000, output_tokens=10_000)],
        names=OPUS,
        built_for=SONNET,
    )

    asyncio.run(_plan(runtime))

    assert model.asked == [OPUS]
    assert _three_figures(home, meter) == (0.6, 0.6, 0.6)
    (call,) = _guard_rows(home)
    assert call["model"] == OPUS


def test_a_turn_whose_calls_are_priced_differently_sums_each_calls_own_price(home):
    """A provider's own cost on one call of a turn was read as the cost of the whole turn."""
    meter = SpendMeter(config_dir=home)
    runtime, _ = _planner(
        home,
        meter,
        [
            _step(1, input_tokens=10_000, output_tokens=100, cost_usd=0.05),
            _answer(input_tokens=100_000, output_tokens=0),
        ],
    )

    asyncio.run(_plan(runtime))

    assert _three_figures(home, meter) == (0.45, 0.45, 0.45)
    (row,) = _usage(home)
    assert len(row["audit_ids"]) == 2


# ── A turn that ends in an error ─────────────────────────────────────────────────────────────


def test_a_turn_the_dollar_cap_stops_keeps_the_calls_it_made_in_usage(home):
    meter = SpendMeter(config_dir=home)
    runtime, model = _planner(
        home,
        meter,
        [
            _step(
                1,
                input_tokens=40_000,
                output_tokens=1_000,
                cache_read_tokens=100_000,
                cache_creation_tokens=4_000,
            ),
            _step(2, input_tokens=30_000, output_tokens=2_000, cache_read_tokens=140_000),
            _answer(input_tokens=10, output_tokens=10),
        ],
        cap=0.5,
    )

    with pytest.raises(BudgetExceededError):
        asyncio.run(_plan(runtime))

    assert len(model.asked) == 2, "the third call was refused before it was sent"
    assert _three_figures(home, meter) == (0.408, 0.408, 0.408)
    (row,) = _usage(home)
    assert (row["source"], row["session_key"]) == ("loop", "dashboard:loop-plan-a1")
    assert sorted(row["audit_ids"]) == sorted(
        r["audit_id"] for r in _guard_rows(home) if r["passed"]
    )
    assert (row["input_tokens"], row["output_tokens"]) == (70_000, 3_000)


def test_a_turn_that_fails_before_any_call_finished_writes_no_usage_row(home):
    meter = SpendMeter(config_dir=home)
    runtime, _ = _planner(
        home, meter, [ValueError("the request was malformed"), ValueError("still malformed")]
    )

    with pytest.raises(ValueError):
        asyncio.run(_plan(runtime))

    assert _usage(home) == []
    assert meter.day_totals().dollars == 0.0


def test_the_native_loops_own_attempt_rows_are_dated_now_and_name_the_entry(home):
    meter = SpendMeter(config_dir=home)
    runtime, _ = _planner(
        home,
        meter,
        [RuntimeError("HTTP 500"), _answer(input_tokens=1_000, output_tokens=100)],
    )
    before = time.time()

    asyncio.run(_plan(runtime))

    rows = [row for row in _calls(home) if row["use_case"] == "native_loop"]
    assert rows, "premise: a retried inference writes the loop's own rows"
    for row in rows:
        assert before - 1 <= row["ts"] <= time.time() + 1, row["ts"]
        assert (row["provider"], row["model"]) == ("bedrock", OPUS)


# ── The chat seam a planning session runs through ────────────────────────────────────────────


async def _dashboard(home: Path, runtime: NativeAgentRuntime):
    from personalclaw.config import AppConfig
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.memory import MemoryStore
    from personalclaw.session import SessionManager
    from personalclaw.skills import SkillsLoader

    sessions = SessionManager(AppConfig(), provider_factory=lambda *_a, **_kw: runtime)
    log = ConversationLog(base_dir=home / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    (home / "ws").mkdir()
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=home / "ws"),
        skills=SkillsLoader(skills_path=home / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


@pytest.mark.asyncio
async def test_a_chat_turn_the_dollar_cap_stops_keeps_the_calls_it_made_in_usage(home):
    from personalclaw.dashboard.chat_runner import run_chat

    meter = SpendMeter(config_dir=home)
    runtime, model = _planner(
        home,
        meter,
        [
            _step(1, input_tokens=100_000, output_tokens=1_000),
            _answer(input_tokens=10, output_tokens=10),
        ],
        cap=0.3,
    )
    await runtime.start()
    state = await _dashboard(home, runtime)
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        chat = state.get_or_create_session()
        chat.append("user", "Plan the change.", "msg msg-u")
        chat.task = asyncio.ensure_future(run_chat(state, chat, "Plan the change."))
        for _ in range(500):
            if not chat.running and not chat._queue:
                break
            await asyncio.sleep(0.01)

    assert len(model.asked) == 1
    # The refusal is said in its own words (which ceiling, what was spent, how it is lifted),
    # not as "an error PersonalClaw doesn't recognize".
    (said,) = [m["content"] for m in chat.messages if m.get("role") == "error"]
    assert said == (
        "The daily dollar budget is spent ($0.42 of $0.30): raise Max dollars / day in Settings "
        "→ Guardrails (0 removes the cap), or wait for it to reset at midnight."
    )
    (row,) = _usage(home)
    assert row["cost_usd"] == pytest.approx(0.42) == meter.day_totals().dollars
    assert row["audit_ids"] == [_guard_rows(home)[0]["audit_id"]]
