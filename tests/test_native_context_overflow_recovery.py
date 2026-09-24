"""Rail: a LENGTH rejection in the native loop is recovered by compacting, once (#2364).

`workflows/compaction.is_context_overflow` — the shared "did the provider reject this for
length?" reader — had ZERO callers under `agents/`, so the one place a long chat actually
overflows could not tell that failure apart from any other. It did not merely go
unrecovered, it went recovered WRONG: `_inference_failure_mode` collapses a vendor
length-400 to `PROVIDER_ERROR`, which `is_retryable` answers True to, so the loop's single
per-turn retry was spent re-sending the byte-identical oversized history one backoff later.
The user paid for two guaranteed failures and got one error.

The recovery is the only retry in this loop that must change the PROMPT, so it is gated
ahead of the classifier, and the generic path is narrowed (`and not overflow`) rather than
`is_retryable` widened — that keeps the blind retry from ever seeing this failure class.

Each edge pinned here:

- overflow before anything visible → history compacted, prompt REBUILT smaller, one retry,
  the turn completes;
- a compaction that reclaims nothing → the exception propagates; retrying an identical
  prompt would burn a call to learn what the pass already proved;
- a second overflow in the same turn → propagates (the shared `inference_retried` latch,
  not a second one-shot flag);
- overflow after visible text or after a tool call → propagates, as for every other
  mid-stream failure;
- the anti-thrashing `_compaction_saves` list stays untouched — it belongs to the automatic
  trigger, and an entry here would latch threshold compaction off;
- the prompt-cache generation is bumped, because the prefix the provider cached is gone;
- a NON-overflow transient still takes the generic blind retry and compacts nothing.
"""

from __future__ import annotations

import pytest

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.context_compaction import total_chars
from personalclaw.guardrails.failure import FailureMode
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.workflows.compaction import is_context_overflow

pytestmark = pytest.mark.asyncio

# The wire shapes a real provider rejects a too-long prompt with. Asserted against the
# shared reader rather than trusted: if the pattern set moves, these stop being overflows
# and every test below would pass vacuously.
OVERFLOW_MESSAGES = (
    "prompt is too long: 205000 tokens > 200000 maximum",
    "context_length_exceeded",
    "This model's maximum context length is 8192 tokens",
)


def _text(t: str) -> AgentEvent:
    return AgentEvent(kind=EVENT_TEXT_CHUNK, text=t)


def _complete() -> AgentEvent:
    return AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


def _convo(n_tool_rounds: int, tool_size: int = 2000) -> list[dict]:
    """A history with verbose tool results — the same shape `test_context_compaction.py`
    and `test_compact_on_the_native_provider.py` build, so all three measure one pass."""
    msgs: list[dict] = [{"role": "user", "content": "system + first message"}]
    for i in range(n_tool_rounds):
        msgs.append(
            {
                "role": "assistant",
                "content": f"step {i}",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "bash", "arguments": f'{{"path": "src/f{i}.py"}}'},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "X" * tool_size})
    msgs.append({"role": "user", "content": "latest request"})
    return msgs


class _ScriptedModel:
    """A ModelProvider replaying a script, recording the prompt of EVERY attempt.

    `prompt_chars` is what makes the recovery checkable: "retried once" is also true of the
    blind retry this replaces, and the two differ only in what the second prompt contains.
    """

    supports_tools = True

    def __init__(self, script: list) -> None:
        self._script = script
        self.calls = 0
        self.prompts: list[list[dict]] = []

    @property
    def prompt_chars(self) -> list[int]:
        return [total_chars(p) for p in self.prompts]

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        idx = min(self.calls, len(self._script) - 1)
        self.calls += 1
        self.prompts.append([dict(m) for m in messages])
        entry = self._script[idx]
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, tuple):
            events, exc = entry
            for ev in events:
                yield ev
            raise exc
        for ev in entry:
            yield ev

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _run(tmp_path, model, monkeypatch, *, history: list[dict] | None = None):
    """Drive one turn over `history`; return (runtime, events, audit rows).

    `rt.compactions` counts calls to `_compact_now`, because "compacted exactly once" is
    half the close condition and a retry loop is exactly the shape that can do it twice.
    """
    rows: list = []
    monkeypatch.setattr(runtime_mod, "record_attempt", rows.append)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[NativeBuiltinToolProvider(tmp_path, sandbox_mode="none")],
        cwd=tmp_path,
    )
    await rt.start()
    rt.set_approval_policy("auto")
    if history is not None:
        rt._messages = history
    inner = rt._compact_now
    rt.compactions = 0

    def _counted(measured_pct):
        rt.compactions += 1
        return inner(measured_pct)

    rt._compact_now = _counted  # type: ignore[method-assign]
    events = [ev async for ev in rt.stream("go")]
    return rt, events, rows


# ── the premise ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("message", OVERFLOW_MESSAGES)
async def test_the_scripted_failures_really_are_overflows(message):
    """Not a tautology — the control for every test below. These messages are only
    overflows because `loop_middleware`'s pattern set says so, and that set is shared."""
    assert is_context_overflow(RuntimeError(message)) is True


# ── the recovery ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("message", OVERFLOW_MESSAGES)
async def test_overflow_is_compacted_and_retried_once(tmp_path, monkeypatch, message):
    model = _ScriptedModel([RuntimeError(message), [_text("hi"), _complete()]])
    rt, events, rows = await _run(tmp_path, model, monkeypatch, history=_convo(10))

    assert model.calls == 2, "exactly one retry — never a storm"
    assert rt.compactions == 1, "compacted EXACTLY once — a second pass is a thrash"
    assert any(ev.kind == EVENT_COMPLETE for ev in events), "the turn must COMPLETE"
    assert [ev.text for ev in events if ev.kind == EVENT_TEXT_CHUNK] == ["hi"]
    # The assertion the blind retry cannot satisfy: the second prompt is SMALLER.
    first, second = model.prompt_chars
    assert second < first, f"the retry re-sent the same oversized history ({first} → {second})"
    assert total_chars(rt._messages) < first, "the runtime's own history must shrink too"


async def test_the_retry_carries_no_correction_note(tmp_path, monkeypatch):
    """The generic retry appends a volatile user note. For THIS failure a note makes the
    prompt longer, i.e. guarantees the second failure the recovery exists to avoid."""
    model = _ScriptedModel([RuntimeError("prompt is too long"), [_text("ok"), _complete()]])
    await _run(tmp_path, model, monkeypatch, history=_convo(10))
    retried = model.prompts[1]
    assert not [m for m in retried if m.get("role") == "user" and m.get("_volatile")]


async def test_the_prompt_cache_prefix_is_invalidated_exactly_once(tmp_path, monkeypatch):
    """Compaction rewrote history, so a cached prefix now describes messages that are gone;
    the retry must not be markable against it.

    Pinned as ``== 1``, not ``>= 1``: the generation counter is also the witness for "history
    compacted exactly once", and a loop that compacts twice before re-sending would satisfy
    a ``>=`` while burning the reclaim the second pass cannot repeat.
    """
    model = _ScriptedModel([RuntimeError("context_length_exceeded"), [_text("ok"), _complete()]])
    rt, _events, _rows = await _run(tmp_path, model, monkeypatch, history=_convo(10))
    assert rt.compactions == 1
    assert rt._cache_generation == 1


async def test_the_audit_records_the_failed_attempt_and_the_passing_retry(tmp_path, monkeypatch):
    model = _ScriptedModel([RuntimeError("prompt is too long"), [_text("ok"), _complete()]])
    _rt, _events, rows = await _run(tmp_path, model, monkeypatch, history=_convo(10))
    assert [(r.attempt, r.passed, r.strategy) for r in rows] == [
        (1, False, "direct"),
        (2, True, "retry"),
    ]
    # Recorded under the mode the taxonomy actually assigns a length-400, not a new one:
    # widening FailureMode for overflow is what would have made the BLIND retry eligible.
    assert rows[0].failure_mode == FailureMode.PROVIDER_ERROR.value
    assert all(r.use_case == "native_loop" for r in rows)


async def test_the_anti_thrash_saves_list_is_not_polluted(tmp_path, monkeypatch):
    """🪤 `should_compact` refuses when the last two saves each reclaimed <10%, and a
    skipped pass records nothing — so an entry appended here could latch threshold
    compaction off for the session with no way to clear it."""
    model = _ScriptedModel([RuntimeError("prompt is too long"), [_text("ok"), _complete()]])
    rt, _events, _rows = await _run(tmp_path, model, monkeypatch, history=_convo(10))
    assert rt._compaction_saves == []


# ── the edges where recovery must NOT happen ──────────────────────────────────


async def test_a_compaction_that_reclaims_nothing_propagates(tmp_path, monkeypatch):
    """THE control that separates this fix from the one it replaces. A short history has no
    middle to fold, so the retry would be byte-identical — and before the change the
    generic path took it anyway, because a length-400 reads as a retryable provider error.
    `calls == 1` is only true if the overflow is excluded from the blind retry."""
    model = _ScriptedModel([RuntimeError("prompt is too long"), [_text("ok"), _complete()]])
    with pytest.raises(RuntimeError, match="too long"):
        await _run(tmp_path, model, monkeypatch, history=[{"role": "user", "content": "hi"}])
    assert model.calls == 1


async def test_a_second_overflow_in_the_same_turn_propagates(tmp_path, monkeypatch):
    model = _ScriptedModel(
        [RuntimeError("prompt is too long"), RuntimeError("prompt is too long again")]
    )
    with pytest.raises(RuntimeError, match="again"):
        await _run(tmp_path, model, monkeypatch, history=_convo(10))
    assert model.calls == 2, "one latch for the turn, shared with the generic retry"


async def test_overflow_after_visible_output_propagates(tmp_path, monkeypatch):
    """Compacting and re-streaming would re-emit text the user already read."""
    model = _ScriptedModel(
        [([_text("partial")], RuntimeError("prompt is too long")), [_complete()]]
    )
    with pytest.raises(RuntimeError, match="too long"):
        await _run(tmp_path, model, monkeypatch, history=_convo(10))
    assert model.calls == 1


# ── the control: every OTHER failure keeps its existing behaviour ──────────────


async def test_a_transient_is_still_retried_blind_and_compacts_nothing(tmp_path, monkeypatch):
    """A 5xx is not about prompt size. Compacting one would silently destroy history to
    fix a network blip, so the generic path must still re-send the SAME prompt."""
    model = _ScriptedModel([RuntimeError("HTTP 500"), [_text("ok"), _complete()]])
    rt, events, _rows = await _run(tmp_path, model, monkeypatch, history=_convo(10))
    assert model.calls == 2
    assert any(ev.kind == EVENT_COMPLETE for ev in events)
    first, second = model.prompt_chars
    assert second == first, "the transient retry must re-send the identical prompt"
    assert rt.compactions == 0, "a 5xx must not destroy history"
    assert rt._cache_generation == 0, "nothing was compacted, so no prefix went stale"
