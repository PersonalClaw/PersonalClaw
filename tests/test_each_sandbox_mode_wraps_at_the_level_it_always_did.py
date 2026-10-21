"""Each sandbox mode wraps a command at the level it always did.

A chat's Execution Boundary says what the sandbox hides from the commands a turn runs, so the level
a mode wraps at is read in one place, ``sandbox.wrap_level``, by the wrap and by that reading alike.
This table holds the wrap to the level each mode had before that one reading existed, on both
backends and for a mode no caller names, so moving the reading cannot move a mode's level.
"""

from __future__ import annotations

import pytest

from personalclaw import sandbox

#: Each mode and the level its wrap hides at; ``None`` is no wrap at all. ``auto`` and ``standard``
#: are ``standard``; ``cc`` and ``strict`` are themselves; anything else, a typo or a mode in the
#: wrong case included, is ``standard`` and never ``off``.
LEVELS: dict[str, str | None] = {
    "off": None,
    "auto": "standard",
    "standard": "standard",
    "cc": "cc",
    "strict": "strict",
    "": "standard",
    "Strict": "standard",
    "STRICT": "standard",
    "CC": "standard",
    "Off": "standard",
    "none": "standard",
    "namespace": "standard",
    "sandbox-exec": "standard",
    "strict ": "standard",
}


@pytest.mark.parametrize("backend", ["namespace", "sandbox-exec"])
@pytest.mark.parametrize(("mode", "level"), LEVELS.items(), ids=[repr(m) for m in LEVELS])
def test_each_mode_wraps_at_its_level(monkeypatch, backend, mode, level):
    asked: list[str] = []
    monkeypatch.setattr(sandbox, "detect_backend", lambda config_mode="auto": backend)
    monkeypatch.setattr(
        sandbox,
        "namespace_argv",
        lambda argv, lvl, *, network: asked.append(lvl) or ["launcher", "/tmp/l", *argv],
    )
    monkeypatch.setattr(
        sandbox,
        "sandbox_exec_argv",
        lambda argv, lvl, *, network: asked.append(lvl) or (["sandbox-exec", *argv], "/tmp/p"),
    )

    wrapped, _cleanup = sandbox._wrapped(["true"], mode, network=True)

    if level is None:
        assert (wrapped, asked) == (["true"], [])
    else:
        assert asked == [level]


@pytest.mark.parametrize(
    ("mode", "level"), [(m, lvl) for m, lvl in LEVELS.items() if lvl], ids=repr
)
def test_the_boundarys_reading_is_the_level_the_wrap_uses(mode, level):
    assert sandbox.wrap_level(mode) == level
