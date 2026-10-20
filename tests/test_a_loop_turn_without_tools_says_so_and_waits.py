"""A turn that runs without tools says so, and the loop it was a cycle of waits for its owner.

A loop refused before it starts on a model that can't use tools can still come to run without them
after it started: Settings → Models moved Loops onto such a model, the chain served a later model in
place of the head, or the model refused the tools a call offered it (a server answers a model that
can't use them with a 400, and the provider retried without them). Each of those turns ran with
nothing but an INFO line in the gateway log, and the loop's worker was asked three more times to
"use your file-write tools" it did not have.

Now the runtime records which model it runs on without tools (``tool_less_model``), the loop's live
activity says it, the cycle driver asks the worker nothing more, and the loop waits
(``needs_input``) with one Inbox item naming the model and what to choose instead. Positive
controls throughout: a model that uses tools starts with its tools and is held for nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.inbox import InboxStore, ItemStatus
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds
from personalclaw.loop import store as loop_store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus


class _Model:
    """A model provider that answers every call in text. ``refuses_on`` is the call (1-based) on
    which it refuses the tools it is offered, as a server refuses a model that can't use them, and
    from which it says it takes none."""

    served_ref = "Pocket:tiny-1"

    def __init__(self, *, uses_tools: bool = True, refuses_on: int = 0) -> None:
        self.supports_tools = uses_tools
        self.refuses_on = refuses_on
        self.offered: list[bool] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.offered.append(bool(tools))
        if tools and len(self.offered) == self.refuses_on:
            self.supports_tools = False
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Done, I wrote the finding.")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    async def shutdown(self) -> None:
        return None


def _runtime(tmp_path: Path, model: _Model) -> NativeAgentRuntime:
    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="tiny-1"),
        model_provider=model,  # type: ignore[arg-type]
        tool_providers=[NativeBuiltinToolProvider(tmp_path, sandbox_mode="none")],
        cwd=tmp_path,
    )


async def _turn(rt: NativeAgentRuntime, text: str = "go") -> list[AgentEvent]:
    rt.set_approval_policy("auto")
    return [ev async for ev in rt.stream(text)]


# ── the runtime ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_runtime_on_a_model_without_tools_names_it_and_offers_none(tmp_path, caplog):
    model = _Model(uses_tools=False)
    rt = _runtime(tmp_path, model)

    with caplog.at_level(logging.WARNING, logger="personalclaw.agents.native.runtime"):
        await rt.start()
    await _turn(rt)

    assert any(
        "“Pocket:tiny-1” can't use tools, so it runs without them" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]
    assert model.offered == [False]
    assert rt.tool_less_model == "Pocket:tiny-1"


@pytest.mark.asyncio
async def test_a_runtime_on_a_model_that_uses_tools_offers_them(tmp_path):
    model = _Model(uses_tools=True)
    rt = _runtime(tmp_path, model)

    await rt.start()
    await _turn(rt)

    assert model.offered == [True]
    assert getattr(rt, "tool_less_model", "") == ""


@pytest.mark.asyncio
async def test_a_model_that_refuses_its_tools_mid_run_is_run_without_them_after(tmp_path):
    model = _Model(uses_tools=True, refuses_on=1)
    rt = _runtime(tmp_path, model)
    await rt.start()

    await _turn(rt)
    await _turn(rt, "again")

    assert model.offered == [True, False], "no later call is offered the tools it refused"
    assert rt.tool_less_model == "Pocket:tiny-1", "the runtime names the model it runs without"


def test_the_spend_guard_reads_the_wrapped_models_declaration_at_each_ask():
    from personalclaw.guardrails.model_call import ModelCallGuard

    inner = _Model(uses_tools=True)
    guard = ModelCallGuard(inner, use_case="loops", provider_name="Pocket", model="tiny-1")
    assert guard.supports_tools is True

    inner.supports_tools = False

    assert guard.supports_tools is False, "a copy taken at wrap time kept answering yes"


# ── what a loop's turn says ────────────────────────────────────────────────────────────────


class _Session:
    def __init__(self, key: str, app: str) -> None:
        self.key = key
        self._app = app
        self._last_turn_declined: list = []


class _Broadcasts:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def broadcast_ws(self, kind: str, data: dict) -> None:
        self.sent.append((kind, data))


class _Runtime:
    def __init__(self, model: str) -> None:
        self.tool_less_model = model


def test_a_loops_turn_says_live_that_its_model_cant_use_tools():
    from personalclaw.dashboard.chat_utils import NO_TOOLS_ACTIVITY_KIND, tools_said

    state, worker = _Broadcasts(), _Session("loop-a1b2c3d4", "loop")

    assert tools_said(state, worker, _Runtime("Pocket:tiny-1")) == "Pocket:tiny-1"
    assert state.sent == [
        (
            "activity_event",
            {
                "session": "loop-a1b2c3d4",
                "kind": NO_TOOLS_ACTIVITY_KIND,
                "text": (
                    "“Pocket:tiny-1” can't use tools, so it runs without them: it can't read or "
                    "write a file, run a command or look anything up."
                ),
            },
        )
    ]
    # Already said for this turn: the turn's end does not say it twice.
    assert tools_said(state, worker, _Runtime("Pocket:tiny-1"), said="Pocket:tiny-1")
    assert len(state.sent) == 1


def test_a_turn_with_its_tools_and_a_plain_chat_say_nothing():
    from personalclaw.dashboard.chat_utils import tools_said

    state = _Broadcasts()

    assert tools_said(state, _Session("loop-a1b2c3d4", "loop"), _Runtime("")) == ""
    assert tools_said(state, _Session("chat-1", ""), _Runtime("Pocket:tiny-1")) == "Pocket:tiny-1"
    assert state.sent == []


# ── one cycle, through the gateway's cycle driver ─────────────────────────────────────────


@pytest.fixture
def cycle_home(monkeypatch, tmp_path):
    """The home a cycle of the real cycle driver writes, as its own tests give it."""
    import personalclaw.tasks.native as nat

    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("personalclaw.triggers.nudge._INSTANCE", None)
    kinds.ensure_loaded()
    return tmp_path


def _one_cycle(tmp_path: Path, *, without_tools: str):
    """One cycle of a loop's worker whose runtime runs on a model that can't use tools
    (*without_tools* names it) or on one that can (``""``), answering in prose."""
    from unittest.mock import AsyncMock, MagicMock

    from test_a_cycle_she_declined_is_not_asked_again import _attended_goal_loop, _Cycle, _says

    loop = _attended_goal_loop()
    cycle = _Cycle(tmp_path, loop, [_says("Done, I wrote the finding.")], {})
    cycle.client.tool_less_model = without_tools
    cycle.state.sessions.get_provider = MagicMock(return_value=cycle.client)
    cycle.state.sessions.reset = AsyncMock()
    return loop, cycle.run()


def test_a_cycle_run_without_tools_is_said_once_and_its_worker_is_not_asked_again(cycle_home):
    """🔴 Before: one INFO line, then three re-prompts telling the worker to use the file-write
    tools it did not have, and the loop ran on to its next cycle."""
    from personalclaw.dashboard.chat_utils import NO_TOOLS_ACTIVITY_KIND

    loop, cycle = _one_cycle(cycle_home, without_tools="Pocket:tiny-1")

    assert cycle.reprompts() == [] and len(cycle.prompts) == 1
    said = [
        c.args[1]["text"]
        for c in cycle.state.broadcast_ws.call_args_list
        if c.args[0] == "activity_event" and c.args[1].get("kind") == NO_TOOLS_ACTIVITY_KIND
    ]
    assert said == [
        "“Pocket:tiny-1” can't use tools, so it runs without them: it can't read or write a "
        "file, run a command or look anything up."
    ]
    assert loop_store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    assert loop_files.pending_question(loop.id)["no_tools"] is True
    assert cycle.state.sessions.reset.await_count == 1, "its resume builds the worker afresh"


def test_a_cycle_with_its_tools_is_asked_for_its_finding_as_before(cycle_home):
    loop, cycle = _one_cycle(cycle_home, without_tools="")

    assert len(cycle.reprompts()) == 3, "one turn, then three asks for the finding it owes"
    assert loop_store.get(loop.id).status == LoopStatus.RUNNING.value
    assert loop_files.pending_question(loop.id) is None


# ── the loop waits ─────────────────────────────────────────────────────────────────────────


class _InboxSvc:
    def __init__(self, store: InboxStore) -> None:
        self.inbox = store


class _Sessions:
    """The session manager as far as a hold reaches it: each worker's runtime, and its reset."""

    def __init__(self, runtimes: dict[str, Any]) -> None:
        self.runtimes = runtimes
        self.reset_keys: list[str] = []

    def get_provider(self, key: str) -> Any:
        return self.runtimes.get(key)

    async def reset(self, key: str) -> None:
        self.reset_keys.append(key)


class _State:
    def __init__(self, store: InboxStore) -> None:
        from personalclaw.dashboard.sse import SseRegistry

        self._inbox_svc = _InboxSvc(store)
        self._sessions: dict[str, Any] = {}
        self._sse = SseRegistry()
        self.notified: list[tuple] = []
        self.sessions = _Sessions({})

    def notify(self, *args: Any, **kwargs: Any) -> None:
        self.notified.append(args)

    def broadcast_ws(self, *_a: Any, **_kw: Any) -> None:
        pass

    def loop_sse(self):
        return self._sse

    def push_refresh(self, *_kinds: Any) -> None:
        pass


class _NudgeRow:
    def __init__(self, name: str) -> None:
        self.id = f"row-{name}"
        self.session_name = name
        self.active = True


class _Svc:
    def __init__(self, names: list[str]) -> None:
        self.rows = {n: _NudgeRow(n) for n in names}

    def list_all(self):
        return list(self.rows.values())

    def get_by_session(self, name):
        return self.rows.get(name)

    async def update(self, row_id, *, active):
        for row in self.rows.values():
            if row.id == row_id:
                row.active = active


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _State:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    kinds.ensure_loaded()
    store = InboxStore()
    store.load()
    state = _State(store)
    from personalclaw.inbox_providers import native_source

    monkeypatch.setattr(native_source, "_dashboard_state", state, raising=False)
    return state


def _running_loop() -> Loop:
    loop = loop_store.create(Loop(id="", name="Tidy the guide", kind="goal", task="tidy it"))
    loop_store.update_status(loop.id, LoopStatus.RUNNING)
    return loop


@pytest.mark.asyncio
async def test_a_cycle_run_without_tools_puts_its_loop_on_hold_naming_the_model(live):
    loop = _running_loop()
    key = f"loop-{loop.id}"
    worker = _Session(key, "loop")
    live._sessions[key] = worker
    live.sessions.runtimes[f"dashboard:{key}"] = _Runtime("Pocket:tiny-1")
    svc = _Svc([key])

    assert await W.LoopWatchdog(live, svc).hold_without_tools(worker) is True

    assert loop_store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    question = loop_files.pending_question(loop.id)
    assert question["no_tools"] is True
    assert question["question"] == (
        "Its worker ran this cycle without tools: “Pocket:tiny-1” can't use tools, and a cycle "
        "counts only for the finding it writes. Choose a model that uses tools for Loops in "
        "Settings → Models. Then resume the loop."
    )
    assert svc.rows[key].active is False, "no next cycle runs while it waits"
    assert live.sessions.reset_keys == [f"dashboard:{key}"], "a resume builds the worker afresh"
    (row,) = [i for i in live._inbox_svc.inbox.items.values() if i.refs.get("loop") == loop.id]
    assert row.status == ItemStatus.PENDING.value
    assert row.message.startswith("Loop paused — its model can't use tools")
    assert "“Pocket:tiny-1” can't use tools" in row.message
    assert len(live.notified) == 1, live.notified


@pytest.mark.asyncio
async def test_a_cycle_with_its_tools_holds_nothing(live):
    loop = _running_loop()
    worker = _Session(f"loop-{loop.id}", "loop")

    assert await W.LoopWatchdog(live, _Svc([worker.key])).hold_without_tools(worker) is False

    assert loop_store.get(loop.id).status == LoopStatus.RUNNING.value
    assert loop_files.pending_question(loop.id) is None
    assert live.notified == []
