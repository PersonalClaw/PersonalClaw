"""PCS-7: the per-turn cache fragment states the split, the hit rate and the saving.

Before this change the "Turn complete" line collapsed both cache numbers into one
pre-summed total (``12,400 + 1,200 → "13,600 cached"``) and showed neither the hit
rate nor the money. That total cannot express the only thing the surface exists to
say: a cache READ is the saving, a cache WRITE is what you paid for it, and one
number is the sum of a credit and a debit.

Every honesty rule below is a defect this repo has shipped on a neighbouring chip
(see ``test_context_pct_honesty.py`` for the ``context_pct`` original), so each gets
its own case, asserted on the RENDERED line rather than on a field:

* no cache activity → the line is byte-identical to the pre-PCS-7 one;
* ``cache_hit_pct=None`` prints no percentage at all, while a measured ``0.0``
  prints ``0% hit`` — an empty cache is a real answer;
* ``cache_saved_usd=None`` prints ``saved unpriced``, never ``$0.0000``;
* a NEGATIVE saving prints WITH its sign — that is the ordinary first turn, which
  only writes the cache, and clamping it would turn a real cost into a fake zero.

The last test is a call-site rail, by AST: it is the one that catches someone
re-summing the two counts back into a single keyword later, which is the exact
defect being fixed here.
"""

from __future__ import annotations

import ast
from pathlib import Path

from personalclaw.dashboard import chat_runner
from personalclaw.dashboard.chat_runner import _turn_complete_line

# The pre-PCS-7 rendering of a priced turn that touched no cache. Asserted as an
# exact string (not a substring) so a stray " · " separator or a reordered fragment
# reddens here instead of quietly shipping.
_NO_CACHE_LINE = (
    "Turn complete: 3 events, 1 tool calls, context 42% · $0.0123 · 1,200 in / 340 out tokens"
)


def _line(**over) -> str:
    base: dict = dict(
        events=3,
        tool_calls=1,
        context_pct=42.0,
        input_tokens=1200,
        output_tokens=340,
        cost_usd=0.0123,
        priced=True,
    )
    base.update(over)
    return _turn_complete_line(**base)


class TestNoCacheActivity:
    def test_line_is_byte_identical_to_the_pre_change_line(self):
        # Zero cache tokens must not add a fragment, a separator, or a "saved" claim.
        assert _line() == _NO_CACHE_LINE
        assert _line(cache_read_tokens=0, cache_creation_tokens=0) == _NO_CACHE_LINE

    def test_no_cache_activity_never_claims_a_saving(self):
        # Even if a caller hands over derived numbers, no cache tokens means there is
        # nothing to report — a "saved" chip on an uncached turn is a fabrication.
        line = _line(cache_hit_pct=0.0, cache_saved_usd=0.0)
        assert line == _NO_CACHE_LINE
        assert "saved" not in line
        assert "cache" not in line


class TestAllThreeFacts:
    def test_read_write_hit_rate_and_saving_all_appear(self):
        line = _line(
            cache_read_tokens=12400,
            cache_creation_tokens=1200,
            cache_hit_pct=84.0,
            cache_saved_usd=0.0231,
        )
        assert "cache 84% hit (12,400 read / 1,200 written)" in line
        assert "saved $0.0231" in line
        # The pre-existing fragments are untouched and still lead the line.
        assert line.startswith(_NO_CACHE_LINE)

    def test_the_two_counts_are_never_summed(self):
        # 12,400 + 1,200 = 13,600 — the old rendering. Both operands must survive
        # separately and the sum must appear nowhere.
        line = _line(cache_read_tokens=12400, cache_creation_tokens=1200)
        assert "12,400 read" in line
        assert "1,200 written" in line
        assert "13,600" not in line

    def test_a_write_only_turn_still_reports(self):
        # The first turn of a session: nothing read, the whole prompt written.
        line = _line(cache_creation_tokens=1200)
        assert "(0 read / 1,200 written)" in line


class TestHitPctHonesty:
    def test_unmeasured_states_no_percentage(self):
        line = _line(cache_read_tokens=12400, cache_creation_tokens=1200, cache_hit_pct=None)
        assert "% hit" not in line
        assert "0% hit" not in line
        # The split still renders — an unknown hit rate must not suppress the counts.
        assert "cache (12,400 read / 1,200 written)" in line

    def test_measured_zero_states_zero(self):
        line = _line(cache_read_tokens=0, cache_creation_tokens=1200, cache_hit_pct=0.0)
        assert "cache 0% hit" in line

    def test_unmeasured_and_measured_zero_disagree(self):
        # THE load-bearing assertion: both cases collapse to one output the moment a
        # consumer starts folding a legitimate 0 into the absent marker.
        unmeasured = _line(cache_creation_tokens=1200, cache_hit_pct=None)
        measured_zero = _line(cache_creation_tokens=1200, cache_hit_pct=0.0)
        assert unmeasured != measured_zero


class TestSavedUsdHonesty:
    def test_unpriced_model_says_unpriced_not_zero(self):
        line = _line(cache_read_tokens=12400, cache_creation_tokens=1200, cache_saved_usd=None)
        assert "saved unpriced" in line
        # A missing price rendered as money would read as "the cache saved nothing".
        assert "$0.00" not in line
        assert "$" not in line.split("saved")[-1]

    def test_measured_zero_saving_is_money_not_unpriced(self):
        line = _line(cache_read_tokens=12400, cache_creation_tokens=1200, cache_saved_usd=0.0)
        assert "saved $0.0000" in line
        assert "unpriced" not in line

    def test_negative_saving_keeps_its_sign(self):
        line = _line(cache_creation_tokens=1200, cache_saved_usd=-0.0004)
        # Assert the SIGN, not merely that a number showed up: an abs()/max(0, …) in
        # the render path prints "saved $0.0004" and passes a number-only assertion
        # while stating the exact opposite of the truth.
        assert "saved -$0.0004" in line
        assert "-" in line.split("saved")[-1]
        assert "saved $0.0004" not in line

    def test_negative_and_positive_savings_disagree(self):
        loss = _line(cache_creation_tokens=1200, cache_saved_usd=-0.0004)
        gain = _line(cache_creation_tokens=1200, cache_saved_usd=0.0004)
        assert loss != gain


class TestCallSiteRail:
    """By AST, not by grep: a docstring naming a keyword is not a call passing one."""

    @staticmethod
    def _turn_complete_line_calls() -> list[ast.Call]:
        src = Path(chat_runner.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_turn_complete_line"
        ]

    def test_the_walk_actually_found_the_call(self):
        # Vacuity guard: a rail that matches nothing looks clean and asserts nothing.
        calls = self._turn_complete_line_calls()
        assert calls, "no _turn_complete_line(...) call found in chat_runner.py"

    def test_call_site_passes_the_split_counts_and_no_summed_keyword(self):
        for call in self._turn_complete_line_calls():
            kwargs = {kw.arg for kw in call.keywords}
            # A ** splat would hide the keywords from this rail entirely.
            assert None not in kwargs, "call uses ** — the keyword rail cannot see it"
            assert "cache_read_tokens" in kwargs
            assert "cache_creation_tokens" in kwargs
            # The defect being fixed: one pre-summed total in place of the split.
            assert "cache_tokens" not in kwargs
            assert "cache_hit_pct" in kwargs
            assert "cache_saved_usd" in kwargs

    def test_the_derived_numbers_come_from_the_shared_primitives(self):
        # No second counter store (the atom's "reusing stats.py counters" clause): the
        # hit rate and the saving are computed by the shared helpers, at the call site.
        src = Path(chat_runner.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "cache_hit_pct" in called
        assert "cache_savings_usd" in called
