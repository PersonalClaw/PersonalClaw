"""The context gauge's four invariants, as PROPERTIES over the whole input range.

Issues #3405 / #3406. Two independent defects composed into one unusable instrument on a
local binding, and neither was visible to an example-based test:

* the denominator was an adapter-local fallback (128000) for a model the shared table has
  never heard of, so the gauge read 3.91× low and its *ceiling* was 25.19% — the 70%
  compaction trigger was arithmetically unreachable;
* the numerator was ``usage.prompt_tokens``, i.e. what the provider ACCEPTED, so on a
  runtime that truncates silently it peaked at 98.39% of the window and then collapsed to
  a flat 50.01% for every larger prompt. A full context read half empty.

**Why properties and not examples.** The landed denominator fix (#2364 / PR #3323) shipped
170 green targeted tests, and every one of them asserted the denominator — which is exactly
what a collapsing numerator cannot be caught by. The four properties below are the shape of
the defects rather than the numbers:

1. **Monotone** — within a binding, a larger served prompt never lowers the reading.
2. **In range** — ``0 <= pct <= 100`` or ``None``; an over-window context is
   representable (100) and never wraps to a small number.
3. **Reachable** — every threshold the product configures is crossable for every window
   the product can resolve, *including on a provider that truncates*. This is the one that
   fails on #3406's "peak is 25.19%, the trigger can never fire".
4. **Honest** — an undeclared, unlisted window produces UNKNOWN (``None``), never a
   confident number.

**The provider model is the load-bearing part of the fixture.** ``_reported`` is not "any
integer": a provider reports the tokens of the prompt it accepted, and it cannot accept
more than it was sent. Three shapes are swept, and the third is the one that matters:

* ``honest`` — reports the true count. Every cloud provider.
* ``capped`` — reports ``min(true, limit)``. Monotone in prompt size, so the gauge is
  trivially monotone; included as the broad class.
* ``ollama`` — reports the true count while the prompt FITS and ``window // 2 + 3`` once
  it does not. This is the measured Ollama 0.34.2 behaviour and it is **not** monotone: it
  is the shape that produced the 98.39% → 50.01% collapse. A property suite that only
  swept monotone reports would pass on the broken gauge.

**Text density is swept too, and that is not decoration.** An earlier version of this fix
detected truncation with a chars-per-token floor, and a live drive falsified it: a prompt of
repeated characters tokenizes at **7.99 chars/token** while the #3405 curve ran at **4.50**,
and no single floor straddles that — the measured false positive was a 998-character prompt
reading 100% full. The gauge is now ordinal (it compares a report against the largest prompt
this binding already measured) so it should be INDIFFERENT to density, and sweeping 2.0
through 12.0 chars/token is what proves that rather than assumes it.

**A sweep runs through ONE gauge, in increasing order, because that is the product.** The
gauge is per binding and its reference is the largest prompt that binding has been handed,
so "larger prompt ⇒ no lower reading" is a statement about a session as it grows. Feeding
sizes out of order would test a claim nothing makes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import (  # noqa: E402
    given,
    settings,
)
from hypothesis import strategies as st  # noqa: E402

from personalclaw.context_gauge import (  # noqa: E402
    FULL_PCT,
    ContextGauge,
    prompt_text_chars,
)
from personalclaw.model_windows import resolved_context_window  # noqa: E402

# ── the product's own thresholds, read from the product ───────────────────────────
#
# Imported, never restated: a property about "every configured threshold is reachable"
# that hardcodes 70.0 stops being about the product the moment someone retunes it.


def _configured_thresholds() -> list[float]:
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.config.loader import SessionConfig
    from personalclaw.session import _BG_RECYCLE_PCT

    return [
        NativeAgentRuntime._COMPACT_THRESHOLD_PCT,
        _BG_RECYCLE_PCT,
        SessionConfig().autocompact_pct,
    ]


#: Windows the product can actually resolve: values from ``model_tokens.json``, the measured
#: served Ollama window (32768), the conservative local floor (4096) and a small boundary
#: case. Not "any integer" — the reachability claim is about bindings the product supports,
#: and a 12-token window is not one.
_WINDOWS = st.sampled_from([1024, 4096, 8192, 32_768, 128_000, 200_000, 262_144, 1_000_000])

#: The three provider shapes — see this module's docstring. ``ollama`` is the measured one.
_SHAPES = st.sampled_from(["honest", "capped", "ollama"])

#: 🪤 REACHABILITY is claimed for these two only, and the exclusion is a real limitation
#: stated rather than hidden. ``capped`` reports ``min(true, window // 2 + 3)`` for EVERY
#: prompt size, so its report is monotone, never reaches the window, and never falls — it
#: carries no signal at all that the context is full, and it is indistinguishable from an
#: honest provider on a model whose window is twice what we were told. Only a
#: chars-per-token estimate could separate those two, and a live drive disproved that
#: approach (see this module's docstring: 4.50 vs 7.99 chars/token on one host). So the
#: claim is precise: a threshold is reachable on any provider that either reports at least a
#: window's worth OR reports fewer tokens for a larger prompt. No measured provider fails
#: both — Ollama, the one that truncates silently, reports the true count while the prompt
#: fits and therefore drops at the cliff, which is exactly what the ordinal rule catches.
_REACHABLE_SHAPES = st.sampled_from(["honest", "ollama"])

#: Characters per token, spanning well past both ends of anything measured: 4.50 on the
#: #3405 curve, 7.99 for a repeated-character filler on the same host, and CJK at the dense
#: end. The gauge must be indifferent to every one of them.
_DENSITIES = st.sampled_from([2.0, 3.0, 4.0, 4.5, 6.0, 7.99, 12.0])


def _true_tokens(sent_chars: int, chars_per_token: float) -> int:
    """What a tokenizer would really report for ``sent_chars`` characters of this text."""
    return int(sent_chars / chars_per_token)


def _reported(sent_chars: int, window: int, shape: str, density: float) -> int:
    """What a provider reports as ``prompt_tokens`` for a prompt of ``sent_chars``."""
    true = _true_tokens(sent_chars, density)
    if shape == "honest":
        return true
    if shape == "capped":
        return min(true, window // 2 + 3)
    # ``ollama``: the real thing. It evaluates the whole prompt while it fits, and past the
    # served window it silently keeps half of it and reports that — measured on 0.34.2 as
    # exactly ``window // 2 + 3`` for every prompt size above the cliff.
    return true if true <= window else window // 2 + 3


def _sweep(
    sizes: list[int],
    window: int | None,
    shape: str,
    density: float = 4.0,
) -> list[float | None]:
    """Drive ``sizes`` (ascending) through ONE gauge and return the readings."""
    gauge = ContextGauge()
    return [
        gauge.measure(
            reported_tokens=_reported(chars, window or 1, shape, density),
            sent_chars=chars,
            window=window,
        )
        for chars in sorted(sizes)
    ]


# ── 1. MONOTONE ───────────────────────────────────────────────────────────────────


class TestMonotone:
    """#3405's defect, stated as the property it violates."""

    @settings(max_examples=600, deadline=None)
    @given(
        window=_WINDOWS,
        sizes=st.lists(st.integers(min_value=0, max_value=2_000_000), min_size=2, max_size=12),
        shape=_SHAPES,
        density=_DENSITIES,
    )
    def test_a_larger_prompt_never_lowers_the_reading(
        self, window: int, sizes: list[int], shape: str, density: float
    ) -> None:
        readings = _sweep(sizes, window, shape, density)
        measured = [r for r in readings if r is not None]
        # Unmeasured is not a smaller number; it is no number, and it can only occur while
        # the provider has reported nothing — i.e. at the small end, before any measurement.
        first_measured = next((i for i, r in enumerate(readings) if r is not None), len(readings))
        assert all(r is None for r in readings[:first_measured])
        assert measured == sorted(measured), (
            f"window={window} provider={shape} density={density}: readings {readings} for "
            f"ascending sizes {sorted(sizes)} — the gauge FELL as the prompt grew"
        )

    def test_the_measured_ollama_cliff_no_longer_collapses(self) -> None:
        """The exact live sweep from #3405, with the MEASURED reports, not a model.

        Every ``(chars, prompt_tokens)`` pair below was read off live Ollama 0.34.2 /
        ``gemma4:12b`` against its 32768-token served window. Pre-fix the readings were
        0.74 → 50.91 → 67.87 → 81.42 → 98.39 → **50.01 → 50.01 → 50.01**: a context that
        was full and shedding input reporting half empty.
        """
        window = 32_768
        sweep = [
            (998, 241),
            (74_978, 16_681),
            (99_998, 22_241),
            (119_978, 26_681),
            (144_998, 32_241),
            (149_993, 16_387),
            (283_598, 16_387),
            (500_003, 16_387),
        ]
        gauge = ContextGauge()
        readings = [
            gauge.measure(reported_tokens=tokens, sent_chars=chars, window=window)
            for chars, tokens in sweep
        ]
        assert all(r is not None for r in readings), readings
        assert readings == sorted(readings), f"non-monotone across the measured sweep: {readings}"
        # The rising limb is reported as MEASURED, to the same figures the sweep recorded —
        # the fix must not buy monotonicity by flattening the honest readings.
        assert readings[4] == pytest.approx(32_241 / window * 100)
        assert readings[3] == pytest.approx(26_681 / window * 100)
        # …and every arm past the cliff reads full instead of 50.01%.
        assert readings[5:] == [FULL_PCT, FULL_PCT, FULL_PCT]

    def test_the_live_repeated_character_sweep_that_falsified_the_ratio_floor(self) -> None:
        """The SECOND live curve, driven on this host against the same model and window.

        Measured with a repeated-character filler, which tokenizes at 7.99 chars/token
        rather than the 4.50 of the curve above — so Ollama does not truncate until far
        later, and the pair of curves is what proves the gauge is density-indifferent.
        The 998-char row is the one that read **100% full** under the chars-per-token floor
        this replaced (142 tokens against a floor of 166), and the 283,598-char row is the
        collapse (16,387 tokens after 18,766 at a smaller prompt).
        """
        window = 32_768
        sweep = [
            (998, 142),
            (74_978, 9_389),
            (119_978, 15_014),
            (144_998, 18_142),
            (149_993, 18_766),
            (283_598, 16_387),
        ]
        gauge = ContextGauge()
        readings = [
            gauge.measure(reported_tokens=tokens, sent_chars=chars, window=window)
            for chars, tokens in sweep
        ]
        assert readings == sorted([r for r in readings if r is not None]), readings
        # The small prompt is a small reading, not a full one. This is the regression.
        assert readings[0] == pytest.approx(142 / window * 100)
        assert readings[0] is not None and readings[0] < 1.0
        # The honest limb stays measured…
        assert readings[4] == pytest.approx(18_766 / window * 100)
        # …and the collapse is caught: more characters, fewer tokens.
        assert readings[5] == FULL_PCT

    def test_compaction_may_lower_the_reading_because_the_prompt_really_shrank(self) -> None:
        """The other direction, which a naive high-water mark would break.

        Monotonicity is in PROMPT SIZE, not in time. Compaction rewrites history to be
        smaller, and the reading must be free to fall with it — a gauge that latched at its
        peak would tell a user compaction did nothing.
        """
        window = 32_768
        gauge = ContextGauge()
        grown = gauge.measure(reported_tokens=26_681, sent_chars=119_978, window=window)
        compacted = gauge.measure(reported_tokens=8_000, sent_chars=32_000, window=window)
        assert grown is not None and compacted is not None
        assert compacted < grown
        assert compacted == pytest.approx(8_000 / window * 100)


# ── 2. IN RANGE ───────────────────────────────────────────────────────────────────


class TestInRange:
    @settings(max_examples=400, deadline=None)
    @given(
        window=_WINDOWS,
        sent_chars=st.integers(min_value=0, max_value=2_000_000),
        reported=st.integers(min_value=-5, max_value=5_000_000),
    )
    def test_the_reading_is_a_percentage_or_nothing(
        self, window: int, sent_chars: int, reported: int
    ) -> None:
        """Unconstrained numerator on purpose: the RANGE must hold for any report at all,
        including a provider that reports more tokens than its own window."""
        pct = ContextGauge().measure(reported_tokens=reported, sent_chars=sent_chars, window=window)
        if pct is None:
            return
        assert 0.0 <= pct <= FULL_PCT, f"{reported} tok / {window} window rendered {pct}%"

    @settings(max_examples=200, deadline=None)
    @given(window=_WINDOWS, over=st.integers(min_value=0, max_value=1_000_000))
    def test_past_the_window_is_representable_and_does_not_wrap(
        self, window: int, over: int
    ) -> None:
        """A context at or past its window reads FULL — not ``pct % 100``, not the
        400.07% the raw division produced against a 4096 floor, and above all not a
        small number."""
        pct = ContextGauge().measure(
            reported_tokens=window + over,
            sent_chars=(window + over) * 4,
            window=window,
        )
        assert pct == FULL_PCT


# ── 3. REACHABLE ──────────────────────────────────────────────────────────────────


class TestEveryThresholdIsReachable:
    """#3406's defect: the gauge's CEILING was 25.19%, so no threshold could ever fire."""

    def test_the_thresholds_are_the_products_own(self) -> None:
        """Failability control for this whole class: prove the thresholds resolved, are
        real percentages, and include the 70% the two issues name."""
        thresholds = _configured_thresholds()
        assert len(thresholds) == 3, thresholds
        assert all(0 < t < 100 for t in thresholds), thresholds
        assert 70.0 in thresholds, thresholds

    def test_the_capped_adversary_really_is_unreachable_so_the_exclusion_is_honest(
        self,
    ) -> None:
        """The exclusion's own control. If ``capped`` ever became reachable, the trap note on
        ``_REACHABLE_SHAPES`` would be stale prose and the shape should go back in the
        sweep — so the limitation is asserted, not asserted-away."""
        window, density = 1024, 2.0
        sizes = [int(window * density * f) for f in (0.1, 0.5, 1.0, 4.0, 100.0)]
        peak = max((r or 0.0) for r in _sweep(sizes, window, "capped", density))
        assert peak < min(_configured_thresholds()), (
            f"a provider that caps every report below its window now reaches {peak}% — "
            "the documented limitation no longer holds, put 'capped' back in the sweep"
        )

    @settings(max_examples=300, deadline=None)
    @given(window=_WINDOWS, shape=_REACHABLE_SHAPES, density=_DENSITIES)
    def test_growing_the_prompt_crosses_every_configured_threshold(
        self, window: int, shape: str, density: float
    ) -> None:
        """The prompt sweep is WINDOW- and DENSITY-relative, because "fill the context"
        means different char counts for a 4096-token binding and a 1M-token one. A fixed
        char ladder would pass for small windows and fail vacuously for large ones.

        ``_REACHABLE_SHAPES`` excludes the ``capped`` adversary, and its trap note says why
        that is a limitation rather than a convenience."""
        full = window * density
        sizes = [int(full * f) for f in (0.1, 0.5, 0.9, 1.0, 1.5, 4.0)]
        peak = max((r or 0.0) for r in _sweep(sizes, window, shape, density))
        for threshold in _configured_thresholds():
            assert peak >= threshold, (
                f"window={window} provider={shape} density={density}: the highest value "
                f"this gauge can report is {peak}%, so the {threshold}% trigger can never fire"
            )

    def test_the_undeclared_gemma4_binding_that_capped_at_25_percent(self) -> None:
        """#3406's headline, driven through the real resolution rather than a literal.

        ``gemma4`` is absent from ``model_tokens.json``, which is what made the old
        adapter fallback (128000) the denominator and capped the gauge at 25.19%. The
        binding now has no window at all until one is declared — and with one declared,
        every threshold is reachable.
        """
        assert resolved_context_window("gemma4:12b") is None, (
            "the table now lists gemma4 — this control's premise is stale, pick another "
            "model the table has never heard of"
        )
        undeclared = ContextGauge().measure(
            reported_tokens=32_241,
            sent_chars=144_998,
            window=resolved_context_window("gemma4:12b"),
        )
        assert undeclared is None, f"an undeclared window must report nothing, got {undeclared}"

        # The string is what the UI actually writes — see model_windows.declared_context_window.
        declared = resolved_context_window("gemma4:12b", override="32768")
        assert declared == 32_768
        gauge = ContextGauge()
        peak = max(
            gauge.measure(reported_tokens=tok, sent_chars=chars, window=declared) or 0.0
            for chars, tok in ((144_998, 32_241), (500_003, 16_387))
        )
        assert peak >= max(_configured_thresholds())

    def test_the_undeclared_binding_reaches_the_TRIGGER_through_the_backstop(self) -> None:
        """🪤 The half that makes the invariant above mean something.

        "Undeclared ⇒ no number" would be a hollow fix if it merely moved the fabrication
        somewhere quieter: a compaction trigger that can never fire is the defect, and an
        unmeasured gauge cannot cross a percentage threshold any more than a wrong one can.
        It does not have to. ``_maybe_compact`` consults the char ESTIMATE precisely when
        the measured gauge is ``None``, and #3406's third numbered claim is that this path
        was *unreachable* on a live Ollama binding — the provider reports usage on every
        turn, so ``_last_context_pct`` was never ``None`` and the backstop never ran.

        Declining to measure an undeclared window is what restores it. So the trigger is
        reachable for BOTH binding shapes, by two different routes, and this asserts the
        route the undeclared shape takes — with the measured gauge's silence as its control.
        """
        from personalclaw.agents.native.runtime import NativeAgentRuntime
        from personalclaw.agents.provider import AgentRuntimeDefinition

        class _LocalModel:
            supports_tools = True
            _model = "gemma4:12b"
            _base_url = "http://127.0.0.1:11434/v1"

        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", provider="native", model="gemma4:12b"),
            model_provider=_LocalModel(),
            tool_providers=[],
        )
        runtime._messages = [{"role": "user", "content": "X" * 500_003}]

        # BEFORE: the measured gauge produced a number for this binding (128000 implied),
        # which both under-read AND made this backstop unreachable. Now it produces none.
        assert (
            ContextGauge().measure(
                reported_tokens=16_387,
                sent_chars=500_003,
                window=resolved_context_window("gemma4:12b"),
            )
            is None
        )
        estimate = runtime._estimated_context_pct()
        assert estimate is not None, "the backstop must be able to answer for a local binding"
        for threshold in _configured_thresholds():
            assert estimate >= threshold, (
                f"an undeclared local binding carrying 500k chars estimates {estimate}%, so "
                f"the {threshold}% trigger still cannot fire — the fabrication only moved"
            )


# ── 4. HONEST ABOUT AN UNDECLARED WINDOW ──────────────────────────────────────────


class TestUnknownWindow:
    @settings(max_examples=200, deadline=None)
    @given(
        sent_chars=st.integers(min_value=0, max_value=2_000_000),
        reported=st.integers(min_value=1, max_value=1_000_000),
    )
    def test_no_window_means_no_number(self, sent_chars: int, reported: int) -> None:
        """Whatever the provider reported, a window we cannot claim yields no reading —
        never a percentage of a default, which is what "measured and 3.91× wrong" was."""
        for window in (None, 0, -1):
            assert (
                ContextGauge().measure(
                    reported_tokens=reported, sent_chars=sent_chars, window=window
                )
                is None
            )

    @settings(max_examples=300, deadline=None)
    @given(
        model=st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != ""),
        window=_WINDOWS,
    )
    def test_a_declaration_is_the_only_thing_that_can_invent_a_window(
        self, model: str, window: int
    ) -> None:
        """For ANY model id, resolution is either the table's own answer or the
        declaration — never a fallback. Paired so a collapse in either direction reds:
        the declared arm must produce the declared number, the undeclared arm must
        produce the table's answer or nothing."""
        assert resolved_context_window(model, override=window) == window
        undeclared = resolved_context_window(model)
        assert undeclared is None or undeclared > 0


# ── the ordinal rule itself, and the two ways it must not err ─────────────────────


class TestTheOrdinalTruncationRule:
    """The mechanism that replaced a chars-per-token floor, and why.

    The rule is one sentence — *a provider handed a larger prompt cannot honestly report
    fewer input tokens* — and its whole merit is that it contains no ratio. These cases pin
    that it is not quietly density-dependent again.
    """

    @settings(max_examples=300, deadline=None)
    @given(window=_WINDOWS, density=_DENSITIES, chars=st.integers(1_000, 2_000_000))
    def test_an_honest_provider_is_never_accused_at_any_density(
        self, window: int, density: float, chars: int
    ) -> None:
        """The failure the live drive caught: a 998-character prompt reading 100% full.
        An honest report — the true count for the prompt, whatever the density — must read
        as a measurement, never as truncation, unless it genuinely fills the window."""
        true = _true_tokens(chars, density)
        pct = ContextGauge().measure(reported_tokens=true, sent_chars=chars, window=window)
        if true <= 0:
            assert pct is None
        elif true >= window:
            assert pct == FULL_PCT
        else:
            assert pct == pytest.approx(true / window * 100)

    def test_the_998_char_false_positive_that_falsified_the_ratio_floor(self) -> None:
        """Named explicitly, because it is the measurement that changed the design. Live:
        998 characters of repeated text reported 142 tokens (7.03 chars/token). A floor at
        6 chars/token put the bar at 166, so the report looked 'too small to be this
        prompt' and the gauge said FULL — for a prompt using 0.4% of the window."""
        pct = ContextGauge().measure(reported_tokens=142, sent_chars=998, window=32_768)
        assert pct == pytest.approx(142 / 32_768 * 100)
        assert pct is not None and pct < 1.0

    def test_a_smaller_prompt_reporting_fewer_tokens_is_not_truncation(self) -> None:
        """The rule's precondition is load-bearing in both halves: fewer tokens for a
        SMALLER prompt is just a smaller prompt."""
        gauge = ContextGauge()
        assert gauge.measure(reported_tokens=20_000, sent_chars=100_000, window=32_768) is not None
        small = gauge.measure(reported_tokens=1_000, sent_chars=4_000, window=32_768)
        assert small == pytest.approx(1_000 / 32_768 * 100)

    def test_a_truncated_report_does_not_become_the_new_reference(self) -> None:
        """Once caught, it must STAY caught. If the truncated figure advanced the mark, the
        next (larger) prompt would compare against 16,387 instead of 32,241 and the collapse
        would re-arm — the gauge would read full once and then fall back to 50%."""
        gauge = ContextGauge()
        gauge.measure(reported_tokens=32_241, sent_chars=144_998, window=32_768)
        assert gauge.measure(reported_tokens=16_387, sent_chars=149_993, window=32_768) == FULL_PCT
        for chars in (200_000, 283_598, 500_003, 1_000_000):
            assert (
                gauge.measure(reported_tokens=16_387, sent_chars=chars, window=32_768) == FULL_PCT
            ), f"the collapse re-armed at {chars} chars"

    def test_a_cold_gauge_has_no_reference_and_says_so_by_being_flat(self) -> None:
        """The documented limitation, asserted rather than left as prose. A binding whose
        very FIRST prompt is already truncated has nothing to compare against, so it reads
        the provider's figure — and then never falls below it."""
        gauge = ContextGauge()
        first = gauge.measure(reported_tokens=16_387, sent_chars=500_003, window=32_768)
        assert first == pytest.approx(16_387 / 32_768 * 100)
        later = gauge.measure(reported_tokens=16_387, sent_chars=1_000_000, window=32_768)
        assert later is not None and later >= first, "flat is acceptable; falling is not"


class TestPromptTextChars:
    """The size of what we SENT. Compared only against itself, so it must be monotone in the
    prompt and must not be dominated by a part whose token cost is fixed and small."""

    def test_both_wire_shapes_count(self) -> None:
        openai_shaped = [
            {"role": "user", "content": "abcde"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "x", "arguments": '{"p":1}'}}],
            },
            {"role": "tool", "tool_call_id": "t", "content": "1234"},
        ]
        assert prompt_text_chars(openai_shaped) == 5 + len('{"p":1}') + 4

        anthropic_shaped = [
            {"role": "user", "content": [{"type": "text", "text": "abc"}]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t", "content": "defg"}],
            },
        ]
        assert prompt_text_chars(anthropic_shaped) == 3 + 4

    def test_an_image_part_counts_zero_rather_than_its_repr(self) -> None:
        """Otherwise a turn that merely attached a screenshot would look like a far larger
        prompt than the one before it, and the ordinal comparison would fire on the
        attachment rather than on any real growth."""
        data_url = "data:image/png;base64," + ("A" * 50_000)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        assert prompt_text_chars(messages) == 2

        from personalclaw.context_compaction import total_chars

        assert total_chars(messages) > 50_000, (
            "the two counters no longer disagree — if total_chars stopped counting the "
            "image repr, this pair is no longer proving anything"
        )

    @settings(max_examples=200, deadline=None)
    @given(sizes=st.lists(st.integers(min_value=0, max_value=5_000), min_size=1, max_size=20))
    def test_it_is_monotone_in_the_prompt(self, sizes: list[int]) -> None:
        """The property the whole ordinal comparison rests on: appending content can only
        raise the number."""
        running = 0
        history: list[dict] = []
        for size in sizes:
            history.append({"role": "user", "content": "x" * size})
            current = prompt_text_chars(history)
            assert current >= running
            running = current
