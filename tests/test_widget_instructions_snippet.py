"""Widget density decides HOW OFTEN, never WHAT A WIDGET CAN DO (issue 2263).

`dashboard.widget_density` is documented — in `_meta` and in the Settings row's own hint — as
"how aggressively the agent uses inline widgets": `more` encourages them, `less` limits them to
when markdown is clearly insufficient. Nothing in either description mentions capability.

But the snippet is one file with two variants, and the `less` branch carried no
``### Interactive Widgets`` section at all. So a user who picked `less` — a FREQUENCY preference —
silently removed the platform's only return channel from the model's instructions. The model then
rendered a widget with a Submit button and no ``data-action``, the click did nothing, and nothing
said why: the field report is exactly that, twice, months apart, ending with the agent asking the
user to copy 200 lines of text out of the widget and paste them into chat.

The runtime channel was never missing — `widget-action` has been on the wire the whole time. The
instructions for reaching it were.
"""

from __future__ import annotations

import pytest

from personalclaw.prompt_providers.runtime import render_snippet_block

DENSITIES = ("more", "less")


def _snippet(density: str) -> str:
    return render_snippet_block("widget-instructions", {"density": density})


@pytest.mark.parametrize("density", DENSITIES)
def test_every_density_documents_the_return_channel(density):
    """🔑 The defect. `data-action` is the ONLY way a widget can send data back, so a variant that
    omits it ships widgets that look interactive and are not."""
    text = _snippet(density)
    assert "data-action" in text, f"density={density!r} never mentions the return channel"
    assert "data-payload" in text, f"density={density!r} omits the payload attribute"


@pytest.mark.parametrize("density", DENSITIES)
def test_every_density_warns_off_the_form_trap(density):
    """The authoring trap that produced the field report: a `<form>` looks like the obvious way to
    submit, and the sandbox (`allow-scripts`, no `allow-forms`) silently blocks it."""
    text = _snippet(density).lower()
    assert "<form>" in text or "form" in text
    assert "sandbox" in text, f"density={density!r} does not say why a form cannot work"


@pytest.mark.parametrize("density", DENSITIES)
def test_every_density_still_teaches_the_widget_tag(density):
    """Vacuity floor: the sections above are worthless if the variant lost the basics."""
    assert "<widget" in _snippet(density)


def test_the_densities_still_differ_in_FREQUENCY():
    """The setting must keep doing what it says. If both variants became identical this rail would
    pass while the preference had silently stopped meaning anything."""
    more, less = _snippet("more"), _snippet("less")
    assert more != less
    assert "clearly insufficient" in less, "the `less` variant must still hold back"
    assert "earns its place" in more, "the `more` variant must still encourage"


# ── the caller normalizes, because the snippet cannot ────────────────────────────────────────


def test_the_snippet_itself_renders_EMPTY_for_an_unknown_density():
    """🪤 MEASURED, and the reason the normalization below exists rather than a comment saying it is
    fine: the snippet's conditional knows exactly two values, and a third produces NOTHING —
    "more" 2212 chars, "less" 1240, "sideways" 0. This pins the engine behaviour the caller guards
    against, so a future template change that fixes it will red this test and invite the guard's
    removal rather than leaving it unexplained."""
    assert _snippet("sideways") == ""
    assert _snippet("") == ""


@pytest.mark.parametrize("bad", ["sideways", "", "MORE ", "medium"])
def test_the_block_a_session_gets_is_never_blank(bad, monkeypatch):
    """The field-visible guarantee. `widget_density` is enum-constrained on the edit path, so only a
    hand-edited config.json reaches this — and it would have deleted the whole block, leaving a
    model that emits raw `<widget>` markup into the transcript with no idea the tag exists."""
    from personalclaw import context as ctx

    class _Dash:
        widget_density = bad

    class _Cfg:
        dashboard = _Dash()

    monkeypatch.setattr(ctx.AppConfig, "load", staticmethod(lambda: _Cfg()))
    text = ctx.ContextBuilder._widget_block("dashboard:abc")
    assert "<widget" in text, f"density={bad!r} blanked the instructions"
    assert "data-action" in text, "the fall-back variant owes the return channel too"


def test_a_non_dashboard_session_still_gets_nothing():
    """Vacuity floor for the normalization: channel and CLI sessions have no widget host, and the
    block is deliberately empty for them. A guard that filled it everywhere would put dashboard-only
    instructions into a Slack turn."""
    from personalclaw import context as ctx

    assert ctx.ContextBuilder._widget_block("slack:C123") == ""
    assert ctx.ContextBuilder._widget_block("") == ""


def test_both_declared_densities_survive_the_normalization(monkeypatch):
    """And the preference still works: a valid value passes through, not flattened to a default."""
    from personalclaw import context as ctx

    class _Dash:
        widget_density = "less"

    class _Cfg:
        dashboard = _Dash()

    monkeypatch.setattr(ctx.AppConfig, "load", staticmethod(lambda: _Cfg()))
    text = ctx.ContextBuilder._widget_block("dashboard:abc")
    assert "clearly insufficient" in text, "a valid `less` must still render the `less` variant"
