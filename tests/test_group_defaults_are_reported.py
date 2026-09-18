"""The configured per-surface group defaults are reportable while the feature is OFF (issue 573).

`resolve_default_groups` short-circuits to `None` when `tools.groups_enabled` is off — correctly,
because that IS the runtime answer: with groups disabled nothing is filtered. The bug was that
`GET /api/tools/groups` read the runtime function to report CONFIGURATION, and its `or set()`
flattened `None` into `[]`, a value the response documents as "every group". So on the shipped
default every surface claimed to have no default, while three of them ship one.

    groups_enabled(): False        ← shipped default
      chat / background / loops / orchestration  ->  [] [] [] []

    DEFAULT_GROUP_DEFAULTS
      background     -> ['core', 'memory']
      orchestration  -> ['core', 'memory']
      loops          -> ['core', 'workflows', 'subagents']

Two facts had one function: *what is configured* and *what is active right now*. They are split
here — `configured_default_groups` ignores the flag, `resolve_default_groups` layers it on — with
one implementation underneath, so they cannot drift.

**The runtime contract is unchanged and these tests hold it.** `agents/native/runtime.py` assigns
`resolve_default_groups(surface)` straight into `_active_groups`, where `None` means "every group
active, skip filtering entirely". A fix that made that function consult defaults with the flag off
would silently start filtering tools on every install that never enabled the feature — a far worse
bug than the misreport it would have fixed.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.tool_providers import groups as groups_mod

SURFACES = ("chat", "background", "loops", "orchestration")


@pytest.fixture
def flag(monkeypatch):
    """Flip `groups_enabled` without touching a config file."""

    def _set(enabled: bool) -> None:
        monkeypatch.setattr(groups_mod, "groups_enabled", lambda: enabled)

    return _set


# ── configuration vs runtime ─────────────────────────────────────────────────────────────────


def test_the_configured_defaults_read_the_same_with_the_flag_off(flag):
    """🔑 The defect: the built-in defaults exist regardless of the flag, so reading them must
    not depend on it."""
    flag(True)
    on = {s: groups_mod.configured_default_groups(s) for s in SURFACES}
    flag(False)
    off = {s: groups_mod.configured_default_groups(s) for s in SURFACES}
    assert on == off, "the configured defaults changed when the feature flag moved"
    assert off["loops"] == {"core", "workflows", "subagents"}
    assert off["background"] == {"core", "memory"}
    assert off["orchestration"] == {"core", "memory"}


def test_chat_genuinely_has_no_default(flag):
    """🪤 `None` for chat is the TRUE answer, not the bug. A fix that invented a default for
    every surface would change interactive chat's tool block, which the feature promises not to
    do until someone configures it."""
    flag(True)
    assert groups_mod.configured_default_groups("chat") is None
    flag(False)
    assert groups_mod.configured_default_groups("chat") is None


def test_the_runtime_answer_still_fails_open_while_disabled(flag):
    """The contract `runtime.py` depends on: with groups off, EVERY surface is unfiltered.

    This is the assertion that would catch the tempting wrong fix — dropping the flag check from
    `resolve_default_groups` so the endpoint reads correctly would start filtering tools on every
    install that never opted in.
    """
    flag(False)
    for surface in SURFACES:
        assert groups_mod.resolve_default_groups(surface) is None, surface


def test_the_runtime_answer_applies_the_defaults_once_enabled(flag):
    flag(True)
    assert groups_mod.resolve_default_groups("loops") == {"core", "workflows", "subagents"}
    assert groups_mod.resolve_default_groups("chat") is None


def test_the_two_agree_whenever_the_feature_is_on(flag):
    """One implementation, two named answers: enabled, they must be identical for every surface.
    Divergence would mean the split had grown a second copy of the lookup."""
    flag(True)
    for surface in SURFACES:
        assert groups_mod.resolve_default_groups(surface) == groups_mod.configured_default_groups(
            surface
        ), surface


# ── config overrides still land, and core is still implied ───────────────────────────────────


def test_a_configured_override_is_honored_with_the_flag_off(flag, monkeypatch):
    """The endpoint's whole purpose is to show what a user configured. An override that only
    became visible after enabling the feature would leave the same blind spot one layer down."""
    flag(False)

    class _Tools:
        group_defaults: dict[str, Any] = {"chat": ["memory"]}

    class _Cfg:
        tools = _Tools()

        @staticmethod
        def load() -> "_Cfg":
            return _Cfg()

    monkeypatch.setitem(
        __import__("sys").modules, "personalclaw.config.loader", type("m", (), {"AppConfig": _Cfg})
    )
    assert groups_mod.configured_default_groups("chat") == {"core", "memory"}


def test_an_unreadable_config_falls_back_to_the_built_ins(flag, monkeypatch):
    """Fail-soft, the direction the whole module fails: a broken config must not empty the
    report, or a config typo would look exactly like the bug this fixes."""
    flag(False)

    class _Boom:
        @staticmethod
        def load():
            raise RuntimeError("unreadable")

    monkeypatch.setitem(
        __import__("sys").modules, "personalclaw.config.loader", type("m", (), {"AppConfig": _Boom})
    )
    assert groups_mod.configured_default_groups("loops") == {"core", "workflows", "subagents"}


def test_an_explicit_star_still_means_every_group(flag, monkeypatch):
    flag(False)

    class _Tools:
        group_defaults: dict[str, Any] = {"loops": ["*"]}

    class _Cfg:
        tools = _Tools()

        @staticmethod
        def load() -> "_Cfg":
            return _Cfg()

    monkeypatch.setitem(
        __import__("sys").modules, "personalclaw.config.loader", type("m", (), {"AppConfig": _Cfg})
    )
    assert groups_mod.configured_default_groups("loops") is None


def test_an_unknown_surface_reads_as_chat(flag):
    """The lookup's existing fallback, pinned: a surface nobody configured is not an error."""
    flag(False)
    assert groups_mod.configured_default_groups("no-such-surface") is None
    assert groups_mod.configured_default_groups("") is None


# ── the endpoint reads the configuration, and the tile shows it ──────────────────────────────


def test_the_handler_reports_configuration_not_the_runtime_answer():
    """A source rail on the ONE line that caused this: the handler must read the configuration
    function. Behaviourally the two are indistinguishable whenever the flag is on, which is
    exactly why this drifted — and why the assertion is on the call, not on a response body."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "personalclaw"
        / "dashboard"
        / "handlers"
        / "tools.py"
    ).read_text()
    window = src[src.index("surfaces = {") : src.index("surfaces = {") + 400]
    assert "configured_default_groups" in window, (
        "the groups endpoint is reading the runtime answer again — with the flag off it will "
        "report every surface as 'every group'"
    )
    assert "resolve_default_groups" not in window


def test_the_tile_no_longer_hides_the_defaults_while_disabled():
    """The frontend half, and the reason the backend fix is not inert.

    `ToolGroupsTile` rendered the surface block behind `{data.enabled && ...}`, so the corrected
    map would have been invisible on exactly the installs it fixes — the shipped default. A
    reader could not answer "would turning this on change anything?" without turning it on.
    """
    from pathlib import Path

    tile = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "src"
        / "pages"
        / "tools"
        / "ToolGroupsTile.tsx"
    ).read_text()
    assert "{data.enabled && (" not in tile, "the surface-defaults block is gated on the flag again"
    # …and the tense is carried by the heading, so the disabled state does not read as a claim
    # about what is happening now.
    assert "What each surface would start with" in tile
    assert "Groups are off, so every surface currently starts with every group" in tile
