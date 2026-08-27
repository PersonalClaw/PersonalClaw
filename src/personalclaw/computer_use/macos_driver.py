"""The macOS accessibility driver's operation layer (`DCU-3`, §3.2).

:mod:`~personalclaw.computer_use.driver_host` finds this module by importing
``personalclaw.computer_use.macos_driver`` and calling ``op_<name>`` — that resolution IS the
call site this atom lands, and until now it returned ``None`` and every desktop tool answered
``ERR_COMPUTER_USE_DRIVER_UNAVAILABLE``. Nothing else about the containment story changes: the
dispatch chain, the ceilinged spawn and the SEL audit were all live before this module existed.

**This process holds no authority** and re-states no policy — ``driver_host``'s docstring is the
contract and ``test_the_driver_child_makes_no_policy_decision`` asserts it. What this module does
own is the one check only the *acting* process can make honestly: the tree is re-walked here, at
the moment of the act, and the fingerprint the dispatch computed at screening time is compared
again. The dispatch's own re-walk (``service._require_fresh_element``) happens before the secure
field screen and the SEL row, so between it and the act there is a window in which the operator
can drag the window or a dialog can appear. Re-checking here closes it. The two checks are not a
duplicated policy — the dispatch decides *whether to try*, this decides *whether the tree it is
about to touch is still the one that was approved*.

**No ctypes here.** Every OS call goes through :mod:`~personalclaw.computer_use.macos_ffi`, so
the entire OS-input surface of this feature is one module an operator can read end to end, and a
test can substitute a recording double for it to prove which calls a given op makes — which is
how "the pointer does not move" becomes an assertion instead of a claim.
"""

from __future__ import annotations

from typing import Any, Callable

from personalclaw.computer_use import macos_ffi as ffi
from personalclaw.computer_use.types import DriverError, DriverRefusal, Element, fingerprint_of

#: The refusal an operator fixes in System Settings, distinct from every other failure because
#: the fix is a checkbox rather than a retry. ``service._run_driver`` honours this code from the
#: child (it is a refusal, never a grant), so the FIX reaches the model intact instead of being
#: flattened into "the driver failed".
ERR_AX_PERMISSION = "ERR_COMPUTER_USE_AX_PERMISSION"
ERR_DRIVER_UNAVAILABLE = "ERR_COMPUTER_USE_DRIVER_UNAVAILABLE"
ERR_DRIVER_FAILED = "ERR_COMPUTER_USE_DRIVER_FAILED"
ERR_STALE_INDEX = "ERR_COMPUTER_USE_STALE_INDEX"

#: ``computer_scroll``'s directions, as line deltas. Positive vertical scrolls the content down.
_DIRECTIONS = {
    "up": (1, 0),
    "down": (-1, 0),
    "left": (0, 1),
    "right": (0, -1),
}
_DEFAULT_SCROLL_LINES = 3

#: The click methods this driver implements. ``service._click_method`` has already validated the
#: name and guaranteed ``auto`` never widens onto a pointer method; this mapping only has to
#: refuse a method it does not implement rather than fall back to one it does.
_POINTER_METHODS = ("located", "global")


def _permission_refusal(detail: str) -> DriverError:
    return DriverError(
        ERR_AX_PERMISSION,
        f"macOS has not granted this process accessibility access: {detail}",
        why=(
            "Reading a window's accessibility tree and activating an element both require the "
            "Accessibility permission, which only a human can grant — it is deliberately not "
            "something a program can turn on for itself."
        ),
        fix=(
            "Open System Settings > Privacy & Security > Accessibility, click +, and add the "
            "binary running PersonalClaw's gateway (its own python executable, not a terminal "
            "app), then restart the gateway. Nothing was clicked, typed or changed."
        ),
    )


def _unavailable_refusal(detail: str) -> DriverError:
    return DriverError(
        ERR_DRIVER_UNAVAILABLE,
        f"the macOS accessibility driver cannot run here: {detail}",
        why="This build reached the macOS driver but the OS frameworks it needs did not load.",
        fix="Nothing happened on the desktop. Desktop computer use needs macOS.",
    )


def _failed_refusal(detail: str, fix: str) -> DriverError:
    return DriverError(
        ERR_DRIVER_FAILED,
        f"the macOS accessibility call did not complete: {detail}",
        why="The OS reported an error rather than performing the operation.",
        fix=fix,
    )


def _stale(detail: str) -> DriverRefusal:
    return DriverRefusal(
        DriverError(
            ERR_STALE_INDEX,
            f"the element index can no longer be acted on: {detail}",
            why=(
                "An element index only means anything against the tree it came from. The window "
                "was re-walked at the moment of acting and no longer matches the snapshot the "
                "index was taken from, so pressing that position would press whatever now sits "
                "there."
            ),
            fix="Call computer_snapshot again and act on an index from the new snapshot.",
        )
    )


def _guarded(handler: Callable[[dict[str, Any]], dict[str, Any]]) -> Any:
    """Turn every failure into a typed envelope, and never let one escape as an exception.

    ``driver_host.run_op`` would catch a raise and report it as ``ERR_..._DRIVER_UNAVAILABLE``,
    which is true but tells an operator nothing — a missing Accessibility tick and a wedged
    application would read identically. Converting here keeps each reason its own code.
    """

    def run(request: dict[str, Any]) -> dict[str, Any]:
        try:
            return handler(request)
        except DriverRefusal as refusal:
            return refusal.error.to_dict()
        except ffi.AXPermissionDenied as exc:
            return _permission_refusal(str(exc)).to_dict()
        except ffi.FFIUnavailable as exc:
            return _unavailable_refusal(str(exc)).to_dict()
        except ffi.AppNotFound as exc:
            return _failed_refusal(
                str(exc),
                "Call computer_list_apps to see what is running, and open the app first.",
            ).to_dict()
        except ffi.AXCallFailed as exc:
            return _failed_refusal(
                str(exc),
                "Nothing was changed on the desktop. Re-snapshot and try again; the element may "
                "not support this operation.",
            ).to_dict()

    run.__name__ = handler.__name__
    run.__doc__ = handler.__doc__
    return run


def _text(request: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = request.get(key)
    text = value.strip() if isinstance(value, str) else ""
    if required and not text:
        raise DriverRefusal(
            _failed_refusal(
                f"the request carried no {key!r}",
                "This is an internal defect in the dispatch, not something to retry.",
            )
        )
    return text


def _fresh(request: dict[str, Any]) -> tuple[int, Any, Element]:
    """Re-walk, re-verify the fingerprint, and return the element to act on.

    An ABSENT fingerprint is refused rather than treated as "no check requested". The dispatch
    always sends one for an element-indexed operation, so a request without one is either
    malformed or an attempt to act on an unverified tree, and both must fail closed — a driver
    that skipped the comparison when the field was missing would make the whole guard optional
    from the caller's side.
    """
    app = _text(request, "app")
    expected = _text(request, "fingerprint")
    raw_index = request.get("element_index")
    if not isinstance(raw_index, int) or isinstance(raw_index, bool):
        raise DriverRefusal(
            _failed_refusal(
                f"element_index is {type(raw_index).__name__}, not an integer",
                "This is an internal defect in the dispatch, not something to retry.",
            )
        )
    walk = ffi.walk_window(app)
    actual = fingerprint_of(walk.elements)
    if actual != expected:
        raise _stale("the window has changed since it was walked")
    if not 0 <= raw_index < len(walk.elements):
        raise _stale(
            f"index {raw_index} is outside the {len(walk.elements)} element(s) now exposed"
        )
    return ffi.resolve_app_pid(app), walk.handles[raw_index], walk.elements[raw_index]


@_guarded
def op_list_apps(request: dict[str, Any]) -> dict[str, Any]:
    """Every running bundled application. The dispatch narrows this to the allowlist (step 7).

    Needs no accessibility permission, deliberately: an operator arming the capability should be
    able to discover the exact app name to allowlist before granting anything else.
    """
    return {"apps": ffi.list_gui_apps()}


@_guarded
def op_snapshot(request: dict[str, Any]) -> dict[str, Any]:
    """Walk one application's front window into an indexed tree plus its fingerprint."""
    app = _text(request, "app")
    walk = ffi.walk_window(app)
    return {
        "app": app,
        "fingerprint": fingerprint_of(walk.elements),
        "elements": [element.to_dict() for element in walk.elements],
        "truncated": walk.truncated,
    }


@_guarded
def op_click(request: dict[str, Any]) -> dict[str, Any]:
    """Activate an element, or — only when explicitly named — click a coordinate.

    The ``auto`` branch calls :func:`macos_ffi.press` and NOTHING else: no mouse event is
    created, so the operator's pointer cannot move as a result of an ordinary click. There is no
    fallback from ``auto`` onto a coordinate method when a press fails, because that fallback is
    exactly how a cursor moves by accident (§3 floor 2).
    """
    method = _text(request, "click_method", required=False) or "auto"
    if method not in ("auto",) + _POINTER_METHODS:
        raise DriverRefusal(
            _failed_refusal(
                f"{method!r} is not a click method this driver implements",
                "Use 'auto' unless you specifically need a coordinate click.",
            )
        )
    if method == "auto":
        _pid, handle, element = _fresh(request)
        ffi.press(handle)
        return {"clicked": {"index": element.index, "role": element.role}, "method": "auto"}

    x, y = _coordinate(request, "x"), _coordinate(request, "y")
    if method == "located":
        ffi.click_located(ffi.resolve_app_pid(_text(request, "app")), x, y)
        return {"clicked": {"x": x, "y": y}, "method": "located", "pointer_moved": False}
    ffi.click_global(x, y)
    return {"clicked": {"x": x, "y": y}, "method": "global", "pointer_moved": True}


def _coordinate(request: dict[str, Any], key: str) -> float:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DriverRefusal(
            _failed_refusal(
                f"a coordinate click needs a numeric {key!r}",
                "Pass x and y as numbers, or use the default element press instead.",
            )
        )
    return float(value)


@_guarded
def op_type(request: dict[str, Any]) -> dict[str, Any]:
    """Focus the element and type into it. Posts key events to the target process only.

    No mouse event and no cursor warp: the element is focused through the accessibility API and
    the keys are delivered with ``CGEventPostToPid``, so the pointer stays put and the keystrokes
    cannot land in a different application.
    """
    text = request.get("text")
    if not isinstance(text, str):
        raise DriverRefusal(
            _failed_refusal(
                f"'text' is {type(text).__name__}, not a string",
                "Pass the text to type as a string.",
            )
        )
    pid, handle, element = _fresh(request)
    ffi.focus(handle)
    ffi.type_text(pid, text)
    return {"typed": {"index": element.index, "characters": len(text)}}


@_guarded
def op_set_value(request: dict[str, Any]) -> dict[str, Any]:
    """Set the element's value through the accessibility API. Posts no event at all."""
    value = request.get("value")
    if not isinstance(value, str):
        raise DriverRefusal(
            _failed_refusal(
                f"'value' is {type(value).__name__}, not a string",
                "Pass the value to set as a string.",
            )
        )
    _pid, handle, element = _fresh(request)
    ffi.set_value(handle, value)
    return {"set_value": {"index": element.index, "characters": len(value)}}


@_guarded
def op_scroll(request: dict[str, Any]) -> dict[str, Any]:
    """Scroll the element's process by whole lines. Posts a wheel event, never a mouse move."""
    direction = _text(request, "direction")
    if direction not in _DIRECTIONS:
        raise DriverRefusal(
            _failed_refusal(
                f"{direction!r} is not a scroll direction",
                f"Use one of: {', '.join(sorted(_DIRECTIONS))}.",
            )
        )
    amount = request.get("amount")
    lines = amount if isinstance(amount, int) and not isinstance(amount, bool) else None
    lines = lines if lines and lines > 0 else _DEFAULT_SCROLL_LINES
    pid, _handle, element = _fresh(request)
    vertical, horizontal = _DIRECTIONS[direction]
    ffi.scroll(pid, vertical * lines, horizontal * lines)
    return {"scrolled": {"index": element.index, "direction": direction, "lines": lines}}


@_guarded
def op_perform_action(request: dict[str, Any]) -> dict[str, Any]:
    """Perform a named accessibility action the element itself advertises.

    The action must be one the freshly-walked element lists. An arbitrary action name is refused
    rather than passed to the OS: the tool's contract says "an action from the element's own
    actions list", and honouring one the element does not advertise would let a model reach a
    behaviour the snapshot never disclosed.
    """
    action = _text(request, "action")
    _pid, handle, element = _fresh(request)
    if action not in element.actions:
        raise DriverRefusal(
            _failed_refusal(
                f"element {element.index} does not advertise the action {action!r}",
                "Use an action from that element's own 'actions' list in the snapshot"
                + (f" ({', '.join(element.actions)})." if element.actions else " (it has none)."),
            )
        )
    ffi.perform_action(handle, action)
    return {"performed": {"index": element.index, "action": action}}
