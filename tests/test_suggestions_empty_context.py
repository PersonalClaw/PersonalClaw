"""An empty instance must fall back to the canned suggestions on EVERY day of the year.

`suggestions._build_context` used to append a `## Current Time` section unconditionally, and
`generate_suggestions` guards on `len(context) < 50`. A lone time section measures 44-53 characters
depending on how long today's weekday and month names are, so the guard's outcome was decided by the
calendar:

    "Friday, May 01 2026 at 07:30"        ->  44 chars  -> falls back (correct)
    "Wednesday, September 02 2026 at 07:30" -> 53 chars  -> passes the guard, calls the LLM

Counted over 2026: 253 days fell back and **113 days did not** — a third of the year spent asking a
model for suggestions from a context containing nothing but a timestamp. And the first
`/api/suggestions` call AWAITS that generation for up to 45s (`api_suggestions` blocks while
`generated_at == 0`), so a new user's chat chips appeared either instantly or after most of a minute
depending on the day of the week.

🪤 THE DATES HERE ARE THE POINT, NOT DECORATION. A single frozen date would pass against the buggy
code roughly two days in three, which is the worst kind of test — green on most CI runs and red on
Wednesdays. The parametrisation pins both extremes of the old straddle plus the exact boundary, so a
regression cannot hide in a lucky calendar.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from personalclaw import suggestions

# (label, frozen date) — chosen by measuring the OLD context length these produced.
# `Wednesday, September` is the longest weekday+month pair in the year; `Fri, May` the shortest.
_STRADDLE_DATES = [
    ("shortest — fell back even when buggy", datetime(2026, 5, 1, 7, 30)),  # len 44
    ("one under the old threshold", datetime(2026, 1, 6, 7, 30)),  # len 49
    ("exactly the old threshold", datetime(2026, 1, 1, 7, 30)),  # len 50
    ("longest — called the LLM when buggy", datetime(2026, 9, 2, 7, 30)),  # len 53
]


@pytest.fixture
def empty_state(tmp_path, monkeypatch):
    """A DashboardState stand-in for an instance that knows nothing yet.

    Every source `_build_context` reads is neutralised, and `config_dir` is pointed at `tmp_path`
    so the automations read cannot reach the real `~/.personalclaw`. `config_dir` is imported
    INSIDE the function under test, so patching the loader's attribute is what actually takes
    effect — patching a name bound at import time in this module would not.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    memory = SimpleNamespace(
        # The pristine templates `_build_context` compares against verbatim.
        read_preferences=lambda: "# User Preferences\n\n<!-- Learned from conversations -->",
        read_projects=lambda: "# Active Projects\n\n<!-- Current work context -->",
        read_recent_history=lambda days=2: "",
    )

    def _must_not_take_a_session(*a, **k):
        raise AssertionError(
            "an empty instance took the background agent session — the guard let it through"
        )

    # A named failure, not an AttributeError. Mutation-testing this file showed the difference:
    # with a bare namespace, restoring the bug made the end-to-end test die on a missing attribute,
    # which reads like a broken test rather than a caught regression.
    with patch("personalclaw.context.ContextBuilder.get_memory_for", return_value=memory):
        yield SimpleNamespace(
            conversation_log=None,
            sessions=SimpleNamespace(
                get_or_create=_must_not_take_a_session, release=lambda *a, **k: None
            ),
        )


@pytest.mark.parametrize("label,when", _STRADDLE_DATES, ids=[d[0] for d in _STRADDLE_DATES])
def test_empty_instance_builds_no_context_on_any_date(empty_state, label, when):
    with patch("personalclaw.suggestions.datetime") as dt:
        dt.now.return_value = when
        ctx = suggestions._build_context(empty_state)
    assert ctx == "", (
        f"{label}: an instance with no memory, no sessions and no automations produced "
        f"{len(ctx)} characters of context ({ctx!r}). If this is a time section, the guard in "
        f"generate_suggestions is calendar-dependent again."
    )


@pytest.mark.parametrize("label,when", _STRADDLE_DATES, ids=[d[0] for d in _STRADDLE_DATES])
def test_the_timestamp_never_reaches_the_guard(empty_state, label, when):
    """The specific regression: the time section must not be what gets the guard over 50."""
    with patch("personalclaw.suggestions.datetime") as dt:
        dt.now.return_value = when
        ctx = suggestions._build_context(empty_state)
    assert "Current Time" not in ctx, (
        f"{label}: `_build_context` is appending the clock again. It measures 44-53 chars on its "
        f"own, which straddles the `len(context) < 50` guard and hands a third of the year to the "
        f"LLM on a context that says only what time it is."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("label,when", _STRADDLE_DATES, ids=[d[0] for d in _STRADDLE_DATES])
async def test_empty_instance_returns_the_fallback_without_an_llm_turn(empty_state, label, when):
    """End to end: no prompt is rendered and no session is taken, on any date."""
    with (
        patch("personalclaw.suggestions.datetime") as dt,
        patch("personalclaw.prompt_providers.runtime.render_use_case_prompt") as render,
    ):
        dt.now.return_value = when
        got = await suggestions.generate_suggestions(empty_state)

    assert got == suggestions._FALLBACK_SUGGESTIONS, f"{label}: expected the canned list"
    # The real assertion. Reaching the prompt means reaching `sessions.get_or_create` and a 45s
    # await on the very first /api/suggestions call.
    assert not render.called, (
        f"{label}: an empty instance rendered the suggestions prompt, which means it took the "
        f"background session and blocked the first request for up to 45s."
    )


def test_the_time_section_still_reaches_a_real_context():
    """The clock is not lost — it moved. A context that EARNED an LLM turn must still carry it."""
    section = suggestions._time_context()
    assert section.startswith("## Current Time\n")
    # And the length that caused all this, asserted so the straddle range above stays honest.
    assert 40 <= len(section) <= 60, (
        f"the lone time section is {len(section)} chars; the parametrised dates above were chosen "
        f"to straddle a 50-char guard, so re-measure them if this moved."
    )
