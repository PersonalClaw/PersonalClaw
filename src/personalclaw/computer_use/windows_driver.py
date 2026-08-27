"""The Windows desktop driver: declared, resolvable, and honestly refusing (`DCU-6`, §3.6).

``driver_host.DRIVER_MODULES["Windows"]`` has named this module since `DCU-4`, and until this
atom the module did not exist — so ``resolve_driver("Windows")`` returned ``None`` and a Windows
operator's answer came from the *no driver at all* fallback, whose FIX says "nothing to
configure". Two things were wrong with that. The mapping was a claim the tree did not honour (a
name pointing at nothing is the shape a real Windows driver's own ``ImportError`` would also
present as, so the two were indistinguishable), and the refusal told an operator nothing they
could act on. Now the mapping is true, resolution succeeds, and the refusal is this platform's
own: it names UI Automation as the missing piece and macOS as the thing that works today. The
wording lives once in :mod:`~personalclaw.computer_use.unsupported_platform`.

**The seven handlers are written out rather than generated.** ``driver_host.run_op`` resolves
them by ``getattr(driver, f"op_{op}")``, so a loop that ``setattr``-ed them would work and would
be invisible: ``git grep op_click`` would not find the Windows answer, the package's
public-surface census (which parses ``FunctionDef`` nodes) would see an empty module, and a
future implementer would have no obvious place to put the first real handler. Seven one-line
delegations is the protocol surface this driver must present, not a duplicated decision — every
word of the refusal is shared, and each function is the seam one real UIA implementation replaces.

**No OS import here, at module level or anywhere.** This module is imported *inside the
gateway's process* by ``resolve_driver``, on macOS as readily as on Windows (the tests that
matter run on macOS), so a top-level ``import comtypes`` would make a missing dependency into a
dead gateway rather than a refused tool. A real driver's Windows-only imports belong inside the
handler that needs them, behind the same typed-refusal discipline ``macos_ffi`` uses.
"""

from __future__ import annotations

from typing import Any

from personalclaw.computer_use import unsupported_platform

#: Exactly what ``platform.system()`` returns here — it is the key ``DRIVER_MODULES`` is looked
#: up by, and the word the refusal shows an operator.
PLATFORM = "Windows"

#: The accessibility API a real driver here will speak. Named in the refusal so a Windows
#: operator learns *what* is missing rather than only that something is.
ACCESSIBILITY_API = "UI Automation"


def _refuse(op: str) -> dict[str, Any]:
    return unsupported_platform.refusal(PLATFORM, ACCESSIBILITY_API, op)


def op_list_apps(request: dict[str, Any]) -> dict[str, Any]:
    """Refuses. Unlike macOS — where listing apps needs no accessibility grant, deliberately —
    there is no UIA code here to enumerate anything, so an empty list would be a lie."""
    return _refuse("list_apps")


def op_snapshot(request: dict[str, Any]) -> dict[str, Any]:
    """Refuses: no UIA element walk exists, so no window can be indexed."""
    return _refuse("snapshot")


def op_click(request: dict[str, Any]) -> dict[str, Any]:
    """Refuses: nothing is pressed and the pointer does not move."""
    return _refuse("click")


def op_type(request: dict[str, Any]) -> dict[str, Any]:
    """Refuses: no keystroke is posted."""
    return _refuse("type")


def op_set_value(request: dict[str, Any]) -> dict[str, Any]:
    """Refuses: no element value is written."""
    return _refuse("set_value")


def op_scroll(request: dict[str, Any]) -> dict[str, Any]:
    """Refuses: no scroll event is posted."""
    return _refuse("scroll")


def op_perform_action(request: dict[str, Any]) -> dict[str, Any]:
    """Refuses: no advertised action is invoked."""
    return _refuse("perform_action")
