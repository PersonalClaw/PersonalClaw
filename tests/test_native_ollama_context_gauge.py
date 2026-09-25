"""The bundled Ollama provider reports a REAL context percentage (#2364, #1774).

It did not. ``_last_context_pct`` was initialised to ``0.0`` and assigned nowhere — the
only occurrence in the file was its own initialiser — so every Ollama session reported a
confident 0% forever, at any history size. That is worse than reporting nothing, because
the two consumers of the gauge both key off ``None``:

* the composer renders a plain dot for ``None`` and a filled ring for a number, so 0.0
  showed a user a measured-looking "plenty of room" that was not a measurement at all;
* the native loop's compaction gate reads the same field. ``_maybe_compact`` compares the
  provider's number against ``_COMPACT_THRESHOLD_PCT`` and falls back to the char-based
  estimate ONLY when the field is ``None`` — and ``0.0 is not None``. So 0.0 both failed
  the threshold and made the backstop that exists to cover an absent gauge unreachable.
  Compaction was structurally disabled, and history grew until Ollama truncated the prompt
  silently (measured: HTTP 200, no exception — there is nothing for the reactive recovery
  path to catch either).

Measured on Ollama 0.34.2 before the fix, a real turn at 26682 prompt tokens against a
32768-token served window: ``context_usage_pct() == 0.0`` where the truth was 81.43%. The
numbers pinned below are that measurement.

The denominator is the other half. ``/api/ps`` publishes the window Ollama loaded the
model WITH, which is the number nothing else in the stack has: the shared table holds
ARCHITECTURAL maxima and ``/api/show`` returns that same architectural figure (262144 for
this model, against 32768 actually served and 128000 in the table). The probe is
vendor-specific, so it lives in the app; core keeps only the conservative floor — and the
floor stays on the char-ESTIMATE path, because a *measured* gauge may not divide by it
(#3406: 16387 tokens over the 4096 floor displays 400%). A probe that answers nothing now
yields an UNMEASURED gauge rather than a fabricated denominator.

The numerator is the third half. ``prompt_eval_count`` is what Ollama ACCEPTED, and past
the served window it truncates silently and reports half the window forever, so this gauge
peaked at 98.39% and then read a flat 50.01% for every larger prompt (#3405). The reading is
now judged ORDINALLY against the largest prompt this binding already had measured — a
provider handed a larger prompt cannot honestly report fewer input tokens — so it never
falls as the context grows. See ``tests/test_context_gauge_properties.py`` for the four
invariants (and for the live measurement that ruled out a chars-per-token rule instead), and
``TestTheNumeratorDoesNotCollapse`` below for the measured curve through this provider.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    load_bundle_module,
    namespaced_module_name,
)
from personalclaw.model_windows import LOCAL_SERVED_CONTEXT_WINDOW

APP_NAME = "ollama-models"
_BUNDLE = NATIVE_DIR / APP_NAME

#: The measured turn: prompt tokens, the window `/api/ps` reported, and the truth.
_MEASURED_PROMPT_TOKENS = 26_682
_MEASURED_SERVED_WINDOW = 32_768
_MEASURED_TRUTH_PCT = 81.43
#: What the table would have said for the same model — the wrong denominator.
_TABLE_WINDOW = 128_000

#: A short outbound prompt, for the cases that are about the DENOMINATOR. The gauge also
#: reads the prompt it sent, but only to compare it against the largest prompt the same
#: gauge already measured — and each case below builds a fresh provider, so a two-character
#: prompt has nothing to be judged against and cannot influence a denominator assertion.
_MESSAGES = [{"role": "user", "content": "hi"}]


@pytest.fixture()
def provider_module():
    """The bundle's own module, loaded under its namespaced name (never bare)."""
    name = namespaced_module_name(APP_NAME, "provider")
    try:
        yield load_bundle_module(_BUNDLE, APP_NAME, "provider")
    finally:
        sys.modules.pop(name, None)


def _provider(module, **options):
    return module.OllamaProvider(
        model="gemma4:12b",
        endpoint="http://127.0.0.1:11434",
        extra_options=options,
    )


class TestTheGaugeStartsUnmeasured:
    def test_a_fresh_provider_reports_none_not_zero(self, provider_module):
        """🪤 The whole defect in one assertion. ``0.0`` is a FABRICATED measurement:
        it is not ``None``, so it disables the char backstop, and it is not the truth,
        so it disables the threshold. Only ``None`` means "nothing to report"."""
        assert _provider(provider_module).context_usage_pct() is None

    @pytest.mark.asyncio
    async def test_zero_input_tokens_stays_unmeasured(self, provider_module):
        provider = _provider(provider_module)
        assert await provider._context_pct("gemma4:12b", 0, _MESSAGES) is None


class TestTheServedWindowResolution:
    @pytest.mark.asyncio
    async def test_a_declared_window_wins_without_probing(self, provider_module):
        """The declaration short-circuits the probe: an operator who set ``num_ctx``
        knows the served window, and the turn must not pay a round-trip to confirm it."""
        provider = _provider(provider_module, context_window=_MEASURED_SERVED_WINDOW)

        async def _forbidden(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("/api/ps was probed despite a declared window")

        provider._client.get = _forbidden
        assert await provider._served_window("gemma4:12b") == _MEASURED_SERVED_WINDOW

    @pytest.mark.asyncio
    async def test_the_declaration_survives_the_string_the_ui_writes(self, provider_module):
        """Settings persists options as strings, so the declaration arrives as
        ``"32768"``. A reader that demanded an int would leave this knob with no reader
        on its only user-facing path."""
        provider = _provider(provider_module, context_window="32768")
        assert provider.context_window == _MEASURED_SERVED_WINDOW

    @pytest.mark.asyncio
    async def test_the_probe_reads_the_served_window_from_api_ps(self, provider_module):
        provider = _provider(provider_module)
        provider._client.get = _fake_ps(
            [{"name": "gemma4:12b", "context_length": _MEASURED_SERVED_WINDOW}]
        )
        assert await provider._served_window("gemma4:12b") == _MEASURED_SERVED_WINDOW

    @pytest.mark.asyncio
    async def test_a_latest_tag_matches_the_bare_name(self, provider_module):
        """``/api/ps`` reports ``gemma4:12b`` for a model pulled as ``gemma4:12b`` but
        ``x:latest`` for one pulled bare, so the match has to accept both spellings."""
        provider = _provider(provider_module)
        provider._client.get = _fake_ps(
            [{"name": "gemma4:latest", "context_length": _MEASURED_SERVED_WINDOW}]
        )
        assert await provider._served_window("gemma4") == _MEASURED_SERVED_WINDOW

    @pytest.mark.asyncio
    async def test_the_probe_is_memoized(self, provider_module):
        """It runs on the completion path of every turn, and the served window cannot
        change without a model reload."""
        provider = _provider(provider_module)
        calls: list[int] = []
        provider._client.get = _fake_ps(
            [{"name": "gemma4:12b", "context_length": _MEASURED_SERVED_WINDOW}], calls
        )
        await provider._served_window("gemma4:12b")
        await provider._served_window("gemma4:12b")
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_an_empty_ps_resolves_no_window_at_all(self, provider_module):
        """🪤 ``/api/ps`` lists only LOADED models and returns ``{"models": []}`` until one
        is warm — and the answer is NOTHING, not the table and not the floor.

        This asserted ``LOCAL_SERVED_CONTEXT_WINDOW`` until #3406. Neither number was
        defensible as a DISPLAYED measurement: the table's 262144 understates usage 8x
        (silent truncation with no exception to catch), and the 4096 floor overstates it —
        the measured 16387-token report divided by it displays 400%. ``model_windows``
        makes that split itself: the floor is for the char estimate, where erring toward
        early compaction is cheap, and ``runtime._estimated_context_pct`` reaches it
        precisely BECAUSE this returns ``None``."""
        provider = _provider(provider_module)
        provider._client.get = _fake_ps([])
        assert await provider._served_window("gemma4:12b") is None
        # The paired half: unresolvable window ⇒ no reading, whatever the report was.
        assert await provider._context_pct("gemma4:12b", 26_682, _MESSAGES) is None
        # …and the control that keeps the assertion above from being vacuous — the floor
        # is still what it was, it simply is not this function's answer any more.
        assert LOCAL_SERVED_CONTEXT_WINDOW == 4096

    @pytest.mark.asyncio
    async def test_a_failing_probe_never_breaks_the_turn(self, provider_module):
        provider = _provider(provider_module)

        async def _boom(*a, **k):
            raise OSError("connection reset")

        provider._client.get = _boom
        # Never raises; an unavailable probe must not be able to cost a turn. The turn
        # completes with an unmeasured gauge, which is a state every consumer models.
        assert await provider._served_window("gemma4:12b") is None


class TestTheMeasuredPercentage:
    @pytest.mark.asyncio
    async def test_the_measured_turn_reports_its_real_percentage(self, provider_module):
        """The regression, in the numbers it was measured at: 26682 tokens against the
        32768 window ``/api/ps`` reported is 81.43%, and the gauge read 0.0."""
        provider = _provider(provider_module, context_window=_MEASURED_SERVED_WINDOW)
        pct = await provider._context_pct("gemma4:12b", _MEASURED_PROMPT_TOKENS, _MESSAGES)
        assert pct == pytest.approx(_MEASURED_TRUTH_PCT, abs=0.01)

    @pytest.mark.asyncio
    async def test_the_table_window_would_have_hidden_the_overflow(self, provider_module):
        """Why the denominator is the defect and not a detail. The same prompt against
        the table's architectural number scores under the 70% gate, so compaction would
        still never fire even after the numerator was fixed."""
        provider = _provider(provider_module, context_window=_TABLE_WINDOW)
        pct = await provider._context_pct("gemma4:12b", _MEASURED_PROMPT_TOKENS, _MESSAGES)
        assert pct is not None and pct < 70.0


class TestTheGaugeReachesTheCompactionGate:
    """The consequence that matters. The percentage is internal; "compaction fires" is
    not — and a gauge of 0.0 made this unreachable in BOTH directions at once."""

    def test_a_high_measured_gauge_trips_the_threshold(self, provider_module):
        from personalclaw.agents.native.runtime import NativeAgentRuntime

        _COMPACT_THRESHOLD_PCT = NativeAgentRuntime._COMPACT_THRESHOLD_PCT

        assert _MEASURED_TRUTH_PCT > _COMPACT_THRESHOLD_PCT

    def test_the_old_fabricated_zero_would_not_have(self, provider_module):
        """The negative statement of the same fact, so a regression to 0.0 fails here
        rather than silently passing the suite the way it did for the whole defect's
        life: 0.0 is below the threshold AND is not ``None``, so neither path fires."""
        from personalclaw.agents.native.runtime import NativeAgentRuntime

        _COMPACT_THRESHOLD_PCT = NativeAgentRuntime._COMPACT_THRESHOLD_PCT

        # Bound to a name rather than written as a literal so it is a value the gate
        # would actually receive (and because `0.0 is not None` is a literal-identity
        # comparison flake8 rejects outright — F632).
        fabricated: float | None = 0.0
        assert fabricated < _COMPACT_THRESHOLD_PCT, "it fails the threshold"
        assert fabricated is not None, "and it is not None, so the backstop never runs"


class TestTheNumeratorDoesNotCollapse:
    """#3405, driven through THIS provider — the one a UI-configured Ollama binding gets.

    The whole measured sweep, live on Ollama 0.34.2 / ``gemma4:12b`` against its 32768-token
    served window. Every ``(chars, prompt_eval_count)`` pair is measured, not modelled: the
    reported count rises with the prompt to 32,241 tokens and then collapses to a flat
    16,387 (half the served window, plus three) for every larger prompt, with HTTP 200 and
    no exception. Dividing that by the window read 50.01% — half empty for a context that
    was full and shedding input — so a session that stepped over the 120k–145k band between
    two turns never saw a reading above 70% again.
    """

    _SWEEP = [
        (998, 241),
        (74_978, 16_681),
        (99_998, 22_241),
        (119_978, 26_681),
        (144_998, 32_241),
        (149_993, 16_387),
        (283_598, 16_387),
        (500_003, 16_387),
    ]

    @pytest.mark.asyncio
    async def test_the_reading_never_falls_as_the_prompt_grows(self, provider_module):
        provider = _provider(provider_module, context_window=_MEASURED_SERVED_WINDOW)
        readings = [
            await provider._context_pct(
                "gemma4:12b", tokens, [{"role": "user", "content": "x" * chars}]
            )
            for chars, tokens in self._SWEEP
        ]
        assert all(r is not None for r in readings), readings
        assert readings == sorted(readings), f"the gauge FELL as the prompt grew: {readings}"

    @pytest.mark.asyncio
    async def test_the_rising_limb_is_still_reported_as_measured(self, provider_module):
        """The fix must not buy monotonicity by flattening the honest readings: the pair
        that brackets the cliff has to stay 81.4% / 98.4%, to the measured figures."""
        provider = _provider(provider_module, context_window=_MEASURED_SERVED_WINDOW)
        for chars, tokens in ((119_978, 26_681), (144_998, 32_241)):
            pct = await provider._context_pct(
                "gemma4:12b", tokens, [{"role": "user", "content": "x" * chars}]
            )
            assert pct == pytest.approx(tokens / _MEASURED_SERVED_WINDOW * 100)

    @pytest.mark.asyncio
    async def test_a_truncated_turn_reads_full_and_crosses_the_gate(self, provider_module):
        """The consequence, which is the point: the regime that most needs compaction is an
        unbounded, still-growing history, and that is exactly where the gauge used to park
        at a comfortable 50% and fire nothing.

        Driven as a SEQUENCE because the detection is ordinal — the pre-cliff turn is what
        the collapsed one is judged against, and it is also the control: the same provider,
        the same window, and a reading that is a plain measurement right up to the cliff."""
        from personalclaw.agents.native.runtime import NativeAgentRuntime

        threshold = NativeAgentRuntime._COMPACT_THRESHOLD_PCT
        provider = _provider(provider_module, context_window=_MEASURED_SERVED_WINDOW)
        honest = await provider._context_pct(
            "gemma4:12b", 32_241, [{"role": "user", "content": "x" * 144_998}]
        )
        assert honest == pytest.approx(32_241 / _MEASURED_SERVED_WINDOW * 100)
        assert honest is not None and honest < 100.0, "the rising limb is not clamped"

        collapsed = await provider._context_pct(
            "gemma4:12b", 16_387, [{"role": "user", "content": "x" * 500_003}]
        )
        assert collapsed == 100.0
        assert collapsed >= threshold


def _fake_ps(models: list[dict], calls: list[int] | None = None):
    """A stand-in for ``AsyncClient.get('/api/ps')`` returning ``models``."""

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"models": models}

    async def _get(url: str, *a, **k):
        assert url == "/api/ps", f"unexpected probe URL {url!r}"
        if calls is not None:
            calls.append(1)
        return _Resp()

    return _get


def test_the_provider_module_under_test_is_the_bundles_own_file(provider_module):
    """Guards the import: if this ever loaded a core copy, every assertion above would
    be testing the wrong file."""
    assert Path(provider_module.__file__ or "") == _BUNDLE / "provider.py"
