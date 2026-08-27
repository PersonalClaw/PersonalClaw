"""The Linux desktop driver: declared, resolvable, and honestly refusing (`DCU-6`, §3.6).

The peer of :mod:`~personalclaw.computer_use.windows_driver`, and its docstring carries the
reasoning for both — why the mapping had to become true, why the seven handlers are written out
instead of generated, and why nothing here may import an OS library at module level. Only the
two platform facts below differ.

Linux has one wrinkle Windows does not, recorded so nobody reads this module as a smaller job
than it is: AT-SPI is only present when an accessibility bus is actually running, so a real
driver here refuses along *two* axes — this one (no implementation) and, once implemented, "the
session exposes no a11y bus", which is an operator-fixable condition and therefore a different
code from this one. Wayland narrows it further: an AT-SPI client can read a tree that a
compositor will still not let it synthesise input into. None of that is decided here; it is
noted because the refusal below must not be mistaken for the whole Linux story.
"""

from __future__ import annotations

from typing import Any

from personalclaw.computer_use import unsupported_platform

#: Exactly what ``platform.system()`` returns here — the key ``DRIVER_MODULES`` is looked up by.
PLATFORM = "Linux"

#: The accessibility API a real driver here will speak, named in the refusal so an operator
#: learns *what* is missing rather than only that something is.
ACCESSIBILITY_API = "AT-SPI"


def _refuse(op: str) -> dict[str, Any]:
    return unsupported_platform.refusal(PLATFORM, ACCESSIBILITY_API, op)


def op_list_apps(request: dict[str, Any]) -> dict[str, Any]:
    """Refuses: no AT-SPI client exists here to enumerate applications, and an empty list would
    read to a model as "you have nothing open"."""
    return _refuse("list_apps")


def op_snapshot(request: dict[str, Any]) -> dict[str, Any]:
    """Refuses: no AT-SPI element walk exists, so no window can be indexed."""
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
