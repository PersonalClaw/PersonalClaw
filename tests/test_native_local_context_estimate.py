"""Rail: the native no-usage backstop divides by the window a LOCAL runtime SERVES (#2364).

`_estimated_context_pct` is the compaction trigger for providers that report no usage: an
OpenAI-compatible endpoint that rejects `stream_options` never delivers a usage chunk, so
`_last_context_pct` stays `None` forever and the char estimate is the only thing that can
ever cross `_COMPACT_THRESHOLD_PCT`.

🪤 "No usage" is NOT the same as "local", and conflating the two is how this bug survived a
green suite. Ollama — the runtime the loopback guard is aimed at — reports
`prompt_eval_count` on every turn, so a real Ollama binding takes the MEASURED branch and
never reaches anything this file's first two classes assert.
`TestALocalProviderThatDoesReportUsage` at the bottom covers that half; a green drive
against a live local model exercises only that class.

It divided by `model_context_window(agent_model)`, and for a local model that table holds
the ARCHITECTURAL maximum: `llama3.1:8b` reads `llama3.1`'s 128000 while the runtime serves
its own, smaller `num_ctx` (measured on Ollama 0.34.2: 32768 served against 262144
architectural). The estimate was therefore many times too small, and the backstop
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
        # 🪤 This asserts the STUB's contract, not a property of local endpoints. An earlier
        # version of this line read "a local endpoint reports no usage — the premise", which
        # is false and is why a green suite proved less than it looked like it did: Ollama
        # reports ``prompt_eval_count`` on every turn, so on a real local binding
        # ``_last_context_pct`` is NOT None and this whole estimate path never runs. The
        # measured arm below covers that half; here the point is only that the runtime is in
        # the unmeasured state, so what follows exercises the backstop rather than the gauge.
        assert rt._last_context_pct is None, "the stub reports no usage — unmeasured state"
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


class TestALocalProviderThatDoesReportUsage:
    """🪤 The other half, and the one a green Ollama drive cannot prove.

    This file's premise — "local means no usage" — is only true of an OpenAI-compatible
    endpoint that rejects ``stream_options``. Ollama, the local runtime the guard sniffs
    for, reports ``prompt_eval_count`` on every single turn, so on a real local binding
    the gauge is a number and the estimate path above is dead code. Both arms therefore
    have to exist: driving a live Ollama exercises only THIS class, and a green drive is
    not evidence that the backstop works.

    Which is exactly how the defect survived: the bundled Ollama provider reported a
    fabricated ``0.0`` rather than ``None``, so ``_maybe_compact`` took the measured
    branch with a number that could never cross the threshold, while the estimate the
    tests above cover was unreachable by construction.
    """

    def test_a_measured_gauge_is_used_instead_of_the_estimate(self):
        rt = _runtime(_Model("http://127.0.0.1:11434/v1"))
        rt._messages = _convo(6)
        # What a real Ollama turn leaves behind: input tokens over the SERVED window.
        rt._last_context_pct = 81.43
        before = total_chars(rt._messages)
        rt._maybe_compact()
        after = total_chars(rt._messages)
        assert after < before
        # The gauge stays MEASURED, scaled by what the pass actually reclaimed (the
        # documented optimistic reset in `_compact_now`) — it is not replaced by an
        # estimate and it does not fall back to `None`. Asserted as the scaling rule
        # rather than a literal so it cannot be satisfied by an unrelated number.
        assert rt._last_context_pct == pytest.approx(81.43 * (after / before))
        assert rt._last_context_pct is not None

    def test_a_fabricated_zero_suppresses_compaction_entirely(self):
        """The defect, stated as a rail. ``0.0`` fails the threshold AND is not ``None``,
        so it also makes the backstop unreachable: a history that the unmeasured arm above
        compacts is left untouched here. This is why the fix is ``None``, not a smaller
        number — any fabricated value disables both paths at once."""
        rt = _runtime(_Model("http://127.0.0.1:11434/v1"))
        rt._messages = _convo(6)
        rt._last_context_pct = 0.0
        before = total_chars(rt._messages)
        rt._maybe_compact()
        assert total_chars(rt._messages) == before
        assert rt._compaction_saves == []

    def test_the_same_history_unmeasured_is_compacted(self):
        """The discriminating control for the pair above: identical history, gauge
        ``None`` instead of ``0.0``, and the backstop fires. The ONLY difference between
        these two tests is the fabricated zero."""
        rt = _runtime(_Model("http://127.0.0.1:11434/v1"))
        rt._messages = _convo(6)
        rt._last_context_pct = None
        before = total_chars(rt._messages)
        rt._maybe_compact()
        assert total_chars(rt._messages) < before


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
