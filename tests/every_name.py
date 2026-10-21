"""A stand-in in a product function's place, under every name a product module holds it by.

A test that asks "does this door go through that function?" puts a stand-in in the function's
place and drives the door: when the door's answer is the stand-in's, the door asked the function.
Setting the stand-in on the function's own module is not enough, since a module that imported the
function by name (``from x import f``) still holds the original, so :func:`put_in_every_name`
sets it under every name that holds the original in every loaded ``personalclaw`` module. A copy
of the function, or a second path that never calls it, is not replaced, which is what such a
test is there to catch.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest


def put_in_every_name(monkeypatch: pytest.MonkeyPatch, original: Any, stand_in: Any) -> None:
    """Set *stand_in* under every product module name that holds *original*, undone with
    *monkeypatch*. Fails when no product module holds it, so a renamed function cannot leave a
    test that replaced nothing."""
    held = [
        (module, name)
        for module in list(sys.modules.values())
        if getattr(module, "__name__", "").startswith("personalclaw")
        for name, value in list(vars(module).items())
        if value is original
    ]
    assert held, f"no product module holds {original!r}"
    for module, name in held:
        monkeypatch.setattr(module, name, stand_in)
