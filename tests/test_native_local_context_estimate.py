"""Rail: the native no-usage backstop divides by the window a LOCAL runtime SERVES (#2364).

`_estimated_context_pct` is the compaction trigger for providers that report no usage —
which is, in practice, the local ones: a loopback endpoint that rejects `stream_options`
never delivers a usage chunk, so `_last_context_pct` stays `None` forever and the char
estimate is the only thing that can ever cross `_COMPACT_THRESHOLD_PCT`.

It divided by `model_context_window(agent_model)`, and for a local model that table holds
the ARCHITECTURAL maximum: `llama3.1:8b` reads `llama3.1`'s 128000 while Ollama serves its
own `num_ctx`, 4096 by default. The estimate was therefore ~31x too small, and the backstop
was not merely inaccurate — it was unreachable: a history that had already overflowed the
real window scored ~3%, so compaction never fired and history grew until the provider
rejected the turn outright. (That rejection is the failure the sibling change recovers
from; this one keeps the loop from getting there.)

The fix passes local-ness in from the call site, because that is where the provider INSTANCE
is — `guardrails.model_call._is_local_provider` sniffs a loopback `_base_url`, and reusing it
keeps one definition of "local" instead of minting a second. A per-binding `context_window`
(popped out of the provider's options bag) overrides both.
"""

from __future__ import annotations

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.context_compaction import total_chars
from personalclaw.model_windows import LOCAL_SERVED_CONTEXT_WINDOW, model_context_window


class _Model:
    supports_tools = True

    def __init__(self, base_url: str = "", *, context_window: int | None = None) -> None:
        self._model = "s"
        self._base_url = base_url
        if context_window is not None:
            self.context_window = context_window


def _runtime(model: _Model, model_id: str = "llama3.1:8b") -> NativeAgentRuntime:
    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model=model_id),
        model_provider=model,
        tool_providers=[],
    )


def _history(chars: int) -> list[dict]:
    return [{"role": "user", "content": "X" * chars}]


#: A history sized to sit just over the threshold against the SERVED window and far under
#: it against the architectural one — the whole defect expressed as one number.
#: 70% of 4096 tokens at 3 chars/token ≈ 8601 chars; the same history is ~2.2% of 128000.
_OVER_THE_SERVED_WINDOW = 9600


class TestTheEstimateUsesTheServedWindow:
    def test_a_local_binding_estimates_against_the_served_window(self):
        rt = _runtime(_Model("http://127.0.0.1:11434/v1"))
        rt._messages = _history(_OVER_THE_SERVED_WINDOW)
        expected = (_OVER_THE_SERVED_WINDOW / 3.0) / LOCAL_SERVED_CONTEXT_WINDOW * 100.0
        assert rt._estimated_context_pct() == pytest.approx(expected)

    def test_the_same_history_on_a_cloud_binding_is_unchanged(self):
        """The control. A remote endpoint keeps the table's answer, so this change cannot
        start compacting cloud conversations 31x too early."""
        rt = _runtime(_Model("https://api.openai.com/v1"), model_id="gpt-4o")
        rt._messages = _history(_OVER_THE_SERVED_WINDOW)
        expected = (_OVER_THE_SERVED_WINDOW / 3.0) / model_context_window("gpt-4o") * 100.0
        assert rt._estimated_context_pct() == pytest.approx(expected)

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://localhost:11434/v1",
            "http://127.0.0.1:8080",
            "http://0.0.0.0:11434",
            "http://[::1]:11434",
        ],
    )
    def test_every_loopback_form_the_guard_recognises_counts_as_local(self, base_url):
        """Asserted through the runtime rather than by re-listing the patterns, so the two
        never drift: the guard owns the definition of local."""
        rt = _runtime(_Model(base_url))
        rt._messages = _history(_OVER_THE_SERVED_WINDOW)
        assert rt._estimated_context_pct() > 70.0

    def test_an_empty_history_is_still_unmeasured(self):
        """`None`, not 0.0 — an unknown gauge must not trigger compaction, and the local
        short-circuit must not turn "no history" into a measurement."""
        rt = _runtime(_Model("http://127.0.0.1:11434/v1"))
        rt._messages = []
        assert rt._estimated_context_pct() is None

    def test_a_declared_context_window_overrides_the_local_default(self):
        """The escape hatch, end to end: the operator raised `num_ctx`, declared it on the
        binding, and the estimate must follow the declaration rather than either default."""
        rt = _runtime(_Model("http://127.0.0.1:11434/v1", context_window=32_768))
        rt._messages = _history(_OVER_THE_SERVED_WINDOW)
        expected = (_OVER_THE_SERVED_WINDOW / 3.0) / 32_768 * 100.0
        assert rt._estimated_context_pct() == pytest.approx(expected)
        assert rt._estimated_context_pct() < 70.0, "a bigger declared window compacts later"


class TestTheBackstopIsReachable:
    """The consequence that matters. The estimate is internal; "compaction fires" is not."""

    def test_a_local_history_over_the_served_window_is_compacted(self):
        rt = _runtime(_Model("http://127.0.0.1:11434/v1"))
        rt._messages = _convo(6)
        assert rt._last_context_pct is None, "a local endpoint reports no usage — the premise"
        before = total_chars(rt._messages)
        rt._maybe_compact()
        assert total_chars(rt._messages) < before
        assert rt._compaction_saves and rt._compaction_saves[0] > 0

    def test_the_gauge_stays_unmeasured_even_after_the_estimate_triggers(self):
        """🪤 An estimate must never be displayed as a measurement. `_last_context_pct` is
        what the UI shows, and it has to stay `None` for a provider that reported nothing."""
        rt = _runtime(_Model("http://127.0.0.1:11434/v1"))
        rt._messages = _convo(6)
        rt._maybe_compact()
        assert rt._last_context_pct is None

    def test_the_same_history_on_a_cloud_binding_is_not_compacted(self):
        """The control: 128000 tokens really is that much room, so an identical history must
        be left alone. This is the assertion that fails if local-ness is ever hardcoded on."""
        rt = _runtime(_Model("https://api.openai.com/v1"), model_id="gpt-4o")
        rt._messages = _convo(6)
        before = total_chars(rt._messages)
        rt._maybe_compact()
        assert total_chars(rt._messages) == before
        assert rt._compaction_saves == []


def _convo(n_tool_rounds: int, tool_size: int = 2000) -> list[dict]:
    """A compactable history — verbose tool results, the shape the pass folds. Sized so
    that at 3 chars/token it is ~98% of a 4096-token served window and ~3% of 128000."""
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
