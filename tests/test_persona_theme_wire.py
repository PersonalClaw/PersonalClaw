"""The persona reaches the model, and the three lists cannot drift (issue 650).

The picker sold "a terse operator voice" while the backend's complete injection path
read a `color_theme` field NO client ever wrote — a live reader of an unwritten key.
The frontend now sends the active personality's theme on every chat POST; these rails
hold the backend half: the closed theme set matches the shipped persona snippet files
exactly (the drift that let Claw Arcade promise a voice with no snippet, and lumon
ship a snippet with no picker entry, now at least stays visible in ONE place), and
injection still works end-to-end for each theme.
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.dashboard import chat_utils as CU


def _snippet_themes() -> set[str]:
    d = Path(CU.__file__).resolve().parents[1] / "config" / "prompt_snippets"
    return {p.stem.removeprefix("persona-") for p in d.glob("persona-*.md")}


def test_theme_set_matches_shipped_persona_snippets_exactly():
    """Every theme the injector accepts has a snippet file, and every persona-*.md
    snippet is reachable through a theme — one authority, drift fails loudly."""
    assert set(CU._PERSONA_THEMES) == _snippet_themes()


def test_claw_arcade_is_a_persona_theme_now():
    """The picker's 'playful, high-energy voice' promise is backed (issue 650's
    reconciliation: the entry promised a voice with no snippet and no theme)."""
    assert "claw-arcade" in CU._PERSONA_THEMES


def test_injection_prepends_each_theme_persona_on_a_new_session():
    for theme in sorted(CU._PERSONA_THEMES):
        out = CU._maybe_inject_persona("hello", theme, True)
        assert out != "hello", f"{theme}: persona must be injected on a new session"
        assert "hello" in out
        assert CU._maybe_inject_persona("hello", theme, False) == "hello"


def test_unknown_or_empty_theme_injects_nothing():
    assert CU._maybe_inject_persona("hello", "", True) == "hello"
    assert CU._maybe_inject_persona("hello", "no-such-theme", True) == "hello"
