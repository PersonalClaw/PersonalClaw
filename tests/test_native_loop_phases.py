"""The native loop as five phases over one state record, held to the loop it replaced (#4015).

Splitting ``NativeAgentRuntime.stream`` into phases is a pure refactor, and "pure" is a claim a
passing suite cannot settle: the suite asserts what someone thought to assert, and the risk of
moving a loop is the order of two events, a field one ending forgot, a request the model is sent
one step early. So the bar is the bytes. Each scenario below drives the real runtime over a
scripted model and scripted tools, and records, in the order they happen:

* every event the turn yields, every field of it;
* every request the model receives: its messages, the tools offered, the model and the effort;
* what the turn raised, if it raised;
* the history the runtime keeps for its next turn, and the stop report.

The records were captured from the loop before the split and are committed under
``fixtures/native_loop_golden``; the split must reproduce each byte for byte. Each scenario covers
one path through the phases: a plain answer, a wave of calls, an approval allowed and one denied, a
steer after a tool and one while the model answers, Stop during a tool and while the model answers,
the step limit, a compaction, a retried inference error and one given up on, an overflow that
compacts and retries, a failover at the first step, an owed reply, and a tool definition the
provider refused.

Regenerating them is a deliberate act: run this module as a script
(``python tests/test_native_loop_phases.py``). There is no environment variable that rewrites
them, because a golden rewritten by the run under test blesses whatever that run did.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import enum
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "native_loop_golden"


# ── the record ─────────────────────────────────────────────────────────────────


def _plain(value: Any) -> Any:
    """*value* as JSON can hold it, or a ``TypeError`` for a type the golden has no form for, so a
    new kind of value in an event is a red here rather than a ``repr`` with an address in it."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, enum.Enum):
        return _plain(value.value)
    if isinstance(value, BaseException):
        return {"exception": type(value).__name__, "words": str(value)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {"dataclass": type(value).__name__, **fields}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    raise TypeError(f"the golden has no form for a {type(value).__name__}: {value!r}")


def _render(log: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n" for line in log)


# ── the scripted world ─────────────────────────────────────────────────────────


class _Model:
    """A model that replays *script*, one entry per request: a list of events (a callable in it
    is called at that point of the stream instead of yielded), an exception to raise before any
    output, or ``(events, exc)`` to stream the events and then raise."""

    supports_tools = True
    _model = "scripted"
    served_ref = "scripted:golden"

    def __init__(self, log: list[dict[str, Any]], script: list[Any]) -> None:
        self._log = log
        self._script = script
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        entry = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        self._log.append(
            {
                "model_call": {
                    "n": self.calls,
                    "model": model,
                    "effort": reasoning_effort,
                    "tools": sorted(
                        str((t.get("function") or {}).get("name")) for t in tools or []
                    ),
                    "messages": _plain(messages),
                }
            }
        )
        failure = None
        if isinstance(entry, BaseException):
            entry, failure = [], entry
        elif isinstance(entry, tuple):
            entry, failure = entry
        for item in entry:
            if callable(item):
                outcome = item()
                if asyncio.iscoroutine(outcome):
                    await outcome
                continue
            yield item
        if failure is not None:
            raise failure


def _tools(on_invoke: dict[str, Callable[[], Awaitable[None] | None]] | None = None):
    """A provider of the scripted tools, each answering in words that name its arguments.

    ``read_file`` is a name the dispatch plan reserves by path, so two reads of different files
    share a wave; every other tool here is unclassified, and runs alone. ``ask`` asks first.
    ``big`` answers with a result large enough for compaction to reclaim. *on_invoke* runs a
    hook while a tool runs: a Stop, a steer.
    """
    from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

    hooks = dict(on_invoke or {})

    class _Tools(ToolProvider):
        @property
        def name(self) -> str:
            return "golden"

        @property
        def display_name(self) -> str:
            return "Golden"

        async def list_tools(self):
            schema = {"type": "object", "properties": {"x": {"type": "string"}}}
            reads = {"requires_approval": False, "risk_level": RiskLevel.SAFE}
            return [
                ToolDefinition(name="echo", description="Echo x.", parameters=schema, **reads),
                ToolDefinition(
                    name="ask", description="Asks first.", parameters=schema, requires_approval=True
                ),
                ToolDefinition(
                    name="read_file",
                    description="Read a file.",
                    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                    **reads,
                ),
                ToolDefinition(
                    name="big", description="A large result.", parameters=schema, **reads
                ),
            ]

        async def invoke(self, tool_name, arguments):
            hook = hooks.get(tool_name)
            if hook is not None:
                outcome = hook()
                if asyncio.iscoroutine(outcome):
                    await outcome
            if tool_name == "read_file":
                return ToolResult(success=True, output=f"the text of {arguments.get('path')}")
            if tool_name == "big":
                return ToolResult(success=True, output="line of output\n" * 3000)
            return ToolResult(success=True, output=f"{tool_name}:{arguments.get('x', '')}")

    return _Tools()


def _text(text: str):
    from personalclaw.llm.events import EVENT_TEXT_CHUNK, AgentEvent

    return AgentEvent(kind=EVENT_TEXT_CHUNK, text=text)


def _thinking(text: str):
    from personalclaw.llm.events import EVENT_THINKING_CHUNK, AgentEvent

    return AgentEvent(kind=EVENT_THINKING_CHUNK, text=text)


def _call(call_id: str, tool: str, args: dict[str, Any]):
    from personalclaw.llm.events import EVENT_TOOL_CALL, AgentEvent

    return AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id=call_id, title=tool, tool_input=json.dumps(args)
    )


def _done(*, pct: float | None = 12.5, stop_reason: str = "end_turn", **usage: Any):
    from personalclaw.llm.events import EVENT_COMPLETE, AgentEvent

    tokens = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 40,
        "cache_creation_tokens": 8,
        "cost_usd": 0.0125,
        **usage,
    }
    return AgentEvent(kind=EVENT_COMPLETE, stop_reason=stop_reason, context_usage_pct=pct, **tokens)


async def _runtime(log: list[dict[str, Any]], script: list[Any], **options: Any):
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    tools = options.pop("tools", None) or _tools()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="Golden", provider="native", model="golden-1"),
        model_provider=_Model(log, script),
        tool_providers=[tools],
        **options,
    )
    await rt.start()
    return rt


async def _drive(
    rt: Any, log: list[dict[str, Any]], message: str = "Do the task.", *, answer: str = ""
) -> None:
    """Run one turn, recording each event as it comes; answer an approval ask with *answer*."""
    from personalclaw.llm.events import EVENT_PERMISSION_REQUEST

    try:
        async for ev in rt.stream(message):
            log.append({"event": _plain(ev)})
            if ev.kind == EVENT_PERMISSION_REQUEST:
                if answer == "allow":
                    await rt.approve_tool(ev.request_id)
                else:
                    await rt.reject_tool(ev.request_id)
    except Exception as exc:  # noqa: BLE001 — what the turn raised is part of the record
        log.append({"raised": _plain(exc)})
    log.append({"history": _plain(rt._messages)})
    log.append(
        {
            "after": {
                "served_model_ref": rt.served_model_ref,
                "stop_report": _plain(rt.last_stop_report()),
                "undelivered_steers": rt.undelivered_steers(),
            }
        }
    )


def _no_backoff() -> contextlib.AbstractContextManager[Any]:
    import personalclaw.agents.native.runtime as runtime_mod

    return mock.patch.object(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)


# ── the scenarios, one path through the phases each ──────────────────────────


async def _plain_answer(log: list[dict[str, Any]]) -> None:
    rt = await _runtime(log, [[_thinking("Easy."), _text("Four"), _text("."), _done()]])
    await _drive(rt, log, "What is two and two?")


async def _tool_wave(log: list[dict[str, Any]]) -> None:
    rt = await _runtime(
        log,
        [
            [
                _text("Reading both."),
                _call("c1", "read_file", {"path": "a.txt"}),
                _call("c2", "read_file", {"path": "b.txt"}),
                _call("c3", "echo", {"x": "after"}),
                _done(stop_reason="tool_use"),
            ],
            [_text("Both read."), _done(pct=20.0)],
        ],
    )
    await _drive(rt, log)


async def _approval(log: list[dict[str, Any]], answer: str) -> None:
    rt = await _runtime(
        log,
        [
            [_call("c1", "ask", {"x": "please"}), _done(stop_reason="tool_use")],
            [_text("Done."), _done()],
        ],
    )
    await _drive(rt, log, answer=answer)


async def _approval_allowed(log: list[dict[str, Any]]) -> None:
    await _approval(log, "allow")


async def _approval_denied(log: list[dict[str, Any]]) -> None:
    await _approval(log, "deny")


async def _steer_after_a_tool(log: list[dict[str, Any]]) -> None:
    queued: list[str] = []
    tools = _tools({"echo": lambda: queued.append("Use the second file instead.")})
    rt = await _runtime(
        log,
        [
            [_call("c1", "echo", {"x": "one"}), _done(stop_reason="tool_use")],
            [_text("Switching to the second file."), _done()],
        ],
        tools=tools,
    )
    rt.set_steer_source(lambda: [queued.pop(0)] if queued else [])
    await _drive(rt, log)


async def _steer_while_answering(log: list[dict[str, Any]]) -> None:
    queued: list[str] = []
    rt = await _runtime(
        log,
        [
            [_text("Here is a long"), lambda: queued.append("Shorter, please."), _done()],
            [_text("Short."), _done()],
        ],
    )
    rt.set_steer_source(lambda: [queued.pop(0)] if queued else [])
    await _drive(rt, log)


async def _stop_during_a_tool(log: list[dict[str, Any]]) -> None:
    box: list[Any] = []
    tools = _tools({"echo": lambda: box[0].cancel()})
    rt = await _runtime(
        log,
        [
            [
                _call("c1", "echo", {"x": "first"}),
                _call("c2", "big", {"x": "second"}),
                _done(stop_reason="tool_use"),
            ],
            [_text("never asked"), _done()],
        ],
        tools=tools,
    )
    box.append(rt)
    await _drive(rt, log)


async def _stop_while_answering(log: list[dict[str, Any]]) -> None:
    box: list[Any] = []
    rt = await _runtime(
        log,
        [
            [
                _text("Calling it."),
                _call("c1", "echo", {"x": "never"}),
                lambda: box[0].cancel(),
                _done(stop_reason="tool_use"),
            ],
        ],
    )
    box.append(rt)
    await _drive(rt, log)


async def _step_limit(log: list[dict[str, Any]]) -> None:
    rt = await _runtime(
        log,
        [
            [_call("c1", "echo", {"x": "1"}), _done(stop_reason="tool_use")],
            [_call("c2", "echo", {"x": "2"}), _done(stop_reason="tool_use")],
            [_call("c3", "echo", {"x": "3"}), _done(stop_reason="tool_use")],
        ],
        max_turns=2,
    )
    await _drive(rt, log)


def _six_big_calls(pct: float) -> list[Any]:
    """Six large results in one step: compaction prunes all but the newest four."""
    return [*(_call(f"c{i}", "big", {"x": str(i)}) for i in range(1, 7)), _done(pct=pct)]


async def _compaction(log: list[dict[str, Any]]) -> None:
    rt = await _runtime(log, [_six_big_calls(95.0), [_text("Summarised."), _done(pct=30.0)]])
    await _drive(rt, log)


async def _retried(log: list[dict[str, Any]]) -> None:
    with _no_backoff():
        rt = await _runtime(log, [RuntimeError("HTTP 500"), [_text("Second try."), _done()]])
        await _drive(rt, log)


async def _given_up(log: list[dict[str, Any]]) -> None:
    with _no_backoff():
        rt = await _runtime(
            log,
            [
                [_call("c1", "echo", {"x": "1"}), _done(stop_reason="tool_use")],
                RuntimeError("HTTP 500"),
                RuntimeError("HTTP 500 again"),
            ],
        )
        await _drive(rt, log)


async def _overflow_compacts_and_retries(log: list[dict[str, Any]]) -> None:
    with _no_backoff():
        rt = await _runtime(
            log,
            [
                _six_big_calls(50.0),
                RuntimeError("context_length_exceeded: the prompt is too long"),
                [_text("Fits now."), _done()],
            ],
        )
        await _drive(rt, log)


async def _owed_reply(log: list[dict[str, Any]]) -> None:
    rt = await _runtime(
        log,
        [
            [_call("c1", "echo", {"x": "1"}), _done(stop_reason="tool_use")],
            [_done()],
            [_text("Here is the answer."), _done()],
        ],
    )
    await _drive(rt, log)


async def _tool_definition_rejected(log: list[dict[str, Any]]) -> None:
    rt = await _runtime(
        log, [RuntimeError("Invalid schema for function 'echo': 'x' is not allowed here")]
    )
    await _drive(rt, log)


async def _fails_over_at_the_first_step(log: list[dict[str, Any]]) -> None:
    """The turn's model fails before it says anything, twice, and the next model of its chain
    answers: the chat's own failover, through the real registry and the real runtime builder."""
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
    from personalclaw.providers import provider_bridge

    class _Entry:
        supports_tools = False

        def __init__(self, entry: str, model: str) -> None:
            self.entry, self.model = entry, model
            self.served_ref = f"{entry}:{model}"

        async def start(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

        async def complete(self, messages: list[dict], **kw: Any):
            log.append(
                {
                    "model_call": {
                        "entry": self.entry,
                        "model": kw.get("model"),
                        "effort": kw.get("reasoning_effort"),
                        "tools": kw.get("tools"),
                        "messages": _plain(messages),
                    }
                }
            )
            if self.entry == "down":
                raise RuntimeError("Error code: 529 - overloaded_error")
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered by {self.served_ref}")
            yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    registry = ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type="scripted",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=8192,
        ),
        lambda *, entry, session_key=None, **kw: _Entry(entry.name, str(kw.get("model") or "")),
    )
    registry.register_entry(ProviderEntry(name="down", type="scripted", model="down-1"))
    registry.register_entry(ProviderEntry(name="up", type="scripted", model="up-1"))
    active = {"chat": ["down:down-1", "up:up-1"]}
    with (
        _no_backoff(),
        mock.patch("personalclaw.llm.registry.get_default_registry", lambda: registry),
        mock.patch("personalclaw.providers.use_cases.load_active_models", lambda: active),
    ):
        rt = provider_bridge._build_native_runtime(
            use_case="chat",
            session_key="dashboard:golden",
            agent=None,
            model_override=None,
            cwd=None,
        )
        await rt.start()
        rt.set_approval_policy("auto")
        rt.announce_failover()
        await _drive(rt, log, "Name a colour.")


SCENARIOS: dict[str, Callable[[list[dict[str, Any]]], Awaitable[None]]] = {
    "plain_answer": _plain_answer,
    "tool_wave": _tool_wave,
    "approval_allowed": _approval_allowed,
    "approval_denied": _approval_denied,
    "steer_after_a_tool": _steer_after_a_tool,
    "steer_while_answering": _steer_while_answering,
    "stop_during_a_tool": _stop_during_a_tool,
    "stop_while_answering": _stop_while_answering,
    "step_limit": _step_limit,
    "compaction": _compaction,
    "retried": _retried,
    "given_up": _given_up,
    "overflow_compacts_and_retries": _overflow_compacts_and_retries,
    "owed_reply": _owed_reply,
    "tool_definition_rejected": _tool_definition_rejected,
    "fails_over_at_the_first_step": _fails_over_at_the_first_step,
}


async def _capture(name: str) -> str:
    log: list[dict[str, Any]] = []
    await SCENARIOS[name](log)
    return _render(log)


# ── each phase says what comes next ────────────────────────────────────────────


async def _phase(rt: Any, st: Any, name: str) -> tuple[list[Any], Any]:
    """Run one phase of *rt* on *st*: its events, and what it says comes next."""
    events = [ev async for ev in getattr(rt, f"_{name}_phase")(st)]
    return events, st.next


@pytest.mark.asyncio
async def test_each_phase_returns_its_next_step() -> None:
    """One turn driven a phase at a time: each phase's every way out sets its ``Next``."""
    from personalclaw.agents.native.loop_state import LoopState, Next
    from personalclaw.llm.events import EVENT_COMPLETE, EVENT_STEER, EVENT_TOOL_RESULT

    log: list[dict[str, Any]] = []
    queued: list[str] = []
    rt = await _runtime(
        log,
        [
            [_call("c1", "echo", {"x": "1"}), _done(stop_reason="tool_use")],
            [_done()],
            [_text("A long"), lambda: queued.append("Shorter."), _done()],
            [_text("Short."), _done()],
        ],
    )
    rt.set_steer_source(lambda: [queued.pop(0)] if queued else [])
    st = LoopState()
    await rt._open_turn(st, "Do the task.")

    # Step 1: the model calls a tool, the call runs, and a steer that came meanwhile is taken.
    assert await _phase(rt, st, "compact") == ([], Next.CONTINUE)
    assert st.steps == 1
    assert (await _phase(rt, st, "infer"))[1] is Next.CONTINUE
    assert [call.tool_call_id for call in st.calls] == ["c1"]
    assert await _phase(rt, st, "end") == ([], Next.CONTINUE), "calls to run: on to dispatch"
    events, after = await _phase(rt, st, "dispatch")
    assert after is Next.CONTINUE and [ev.kind for ev in events][-1] == EVENT_TOOL_RESULT
    queued.append("Use the second file.")
    events, after = await _phase(rt, st, "steer")
    assert after is Next.CONTINUE and [(ev.kind, ev.text) for ev in events] == [
        (EVENT_STEER, "Use the second file.")
    ]
    assert await _phase(rt, st, "steer") == ([], Next.CONTINUE), "no steer: on all the same"

    # Step 2: the model says nothing after running a tool, so it is asked once for its answer.
    assert (await _phase(rt, st, "compact"))[1] is Next.CONTINUE
    assert (await _phase(rt, st, "infer"))[1] is Next.CONTINUE
    assert await _phase(rt, st, "end") == ([], Next.BREAK), "the owed reply: the next step"

    # Step 3: a steer arrives while the model answers without tools: the next step takes it in.
    assert (await _phase(rt, st, "compact"))[1] is Next.CONTINUE
    assert (await _phase(rt, st, "infer"))[1] is Next.CONTINUE
    events, after = await _phase(rt, st, "end")
    assert after is Next.BREAK and [ev.text for ev in events] == ["Shorter."]

    # Step 4: an answer with no tool calls ends the turn, said by the phase.
    assert (await _phase(rt, st, "compact"))[1] is Next.CONTINUE
    assert (await _phase(rt, st, "infer"))[1] is Next.CONTINUE
    events, after = await _phase(rt, st, "end")
    assert after is Next.RETURN
    assert [(ev.kind, ev.stop_reason, ev.num_turns) for ev in events] == [
        (EVENT_COMPLETE, "end_turn", 4)
    ]
    assert st.steers == ["Use the second file.", "Shorter."]


@pytest.mark.asyncio
async def test_a_stop_ends_the_turn_in_the_phase_that_sees_it() -> None:
    from personalclaw.agents.native.loop_state import LoopState, Next
    from personalclaw.cancellation import CANCEL_USER
    from personalclaw.llm.events import EVENT_COMPLETE

    log: list[dict[str, Any]] = []
    rt = await _runtime(log, [[_call("c1", "echo", {"x": "1"}), _done(stop_reason="tool_use")]])
    st = LoopState()
    await rt._open_turn(st, "Do the task.")
    assert (await _phase(rt, st, "compact"))[1] is Next.CONTINUE
    assert (await _phase(rt, st, "infer"))[1] is Next.CONTINUE
    rt._cancel.request(reason=CANCEL_USER)
    events, after = await _phase(rt, st, "end")
    assert after is Next.RETURN and events[0].stop_reason == "stopped_by_user"
    assert rt._messages[-1]["role"] == "tool", "the call the stop left is answered in the history"

    # A stop that comes between steps ends the turn as the next step opens, before it counts.
    st = LoopState()
    await rt._open_turn(st, "Again.")
    rt._cancel.request(reason=CANCEL_USER)
    events, after = await _phase(rt, st, "compact")
    assert after is Next.RETURN and st.steps == 0
    assert [(ev.kind, ev.stop_reason) for ev in events] == [(EVENT_COMPLETE, "stopped_by_user")]


# ── the goldens ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_a_scripted_turn_yields_the_same_events_as_before(name: str) -> None:
    golden = GOLDEN_DIR / f"{name}.jsonl"
    assert golden.is_file(), f"{golden} is missing: record it from the loop before the change"
    expected = golden.read_text(encoding="utf-8")
    actual = await _capture(name)
    if actual != expected:
        got, want = actual.splitlines(), expected.splitlines()
        first = next(
            (i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want))
        )
        raise AssertionError(
            f"{name}: the turn differs from the loop before the split at record {first + 1} "
            f"({len(got)} records, the golden has {len(want)}):\n"
            f"  now:    {got[first] if first < len(got) else '<none>'}\n"
            f"  golden: {want[first] if first < len(want) else '<none>'}"
        )


def test_every_scenario_has_a_golden_and_every_golden_a_scenario() -> None:
    held = sorted(path.stem for path in GOLDEN_DIR.glob("*.jsonl"))
    assert held == sorted(SCENARIOS)


def test_each_golden_reaches_the_path_it_is_named_for() -> None:
    """A golden that silently stopped reaching its branch would still replay equal: each holds the
    event or record that proves its path ran."""
    reached = {
        "plain_answer": '"stop_reason": "end_turn"',
        "tool_wave": '"tool_call_id": "c2"',
        "approval_allowed": '"kind": "permission_request"',
        "approval_denied": "declined",
        "steer_after_a_tool": '"kind": "steer"',
        "steer_while_answering": '"text": "Shorter, please."',
        "stop_during_a_tool": '"stop_reason": "stopped_by_user"',
        "stop_while_answering": '"stop_reason": "stopped_by_user"',
        "step_limit": '"stop_reason": "max_turns"',
        "compaction": '"kind": "compaction_status"',
        "retried": '"kind": "retrying"',
        "given_up": '"kind": "spent"',
        "overflow_compacts_and_retries": '"kind": "compaction_status"',
        "owed_reply": '"text": "Here is the answer."',
        "tool_definition_rejected": '"exception": "ToolSchemaRejected"',
        "fails_over_at_the_first_step": '"kind": "model_substitution"',
    }
    assert sorted(reached) == sorted(SCENARIOS)
    for name, needle in reached.items():
        text = (GOLDEN_DIR / f"{name}.jsonl").read_text(encoding="utf-8")
        assert needle in text, f"{name}: its golden never reaches {needle}"


# ── deliberate regeneration ────────────────────────────────────────────────────


def _regenerate() -> None:
    """Record every scenario, each in a home of its own, never the default one."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="native-loop-golden-"))
    try:
        for name in sorted(SCENARIOS):
            home = root / name
            home.mkdir()
            os.environ["PERSONALCLAW_HOME"] = str(home)
            text = asyncio.run(_capture(name))
            (GOLDEN_DIR / f"{name}.jsonl").write_text(text, encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"wrote {len(SCENARIOS)} goldens to {GOLDEN_DIR}")


if __name__ == "__main__":
    sys.exit(_regenerate())
