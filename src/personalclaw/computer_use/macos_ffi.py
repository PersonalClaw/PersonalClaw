"""The macOS accessibility FFI — the one module in this package that touches the OS (`DCU-3`).

**ctypes, not pyobjc, and that is the plan's own choice** (§3.2 names "macOS accessibility driver
over ctypes FFI"). It is also the cheaper one: every symbol this module needs lives in a system
framework that is present on every macOS install, so desktop computer use adds **no dependency
at all** — not even an optional extra. A ``pyobjc-framework-*`` extra would have to be installed
before the capability worked, would need an absent-import refusal path of its own, and would put
a compiled wheel in the way of a feature whose real gate is a TCC permission the operator grants
by hand. ``ctypes.util``-free absolute framework paths are used because they resolve without a
dyld search and fail loudly if a future macOS moves them.

**Nothing here runs at import time.** :func:`_load` is lazy and cached, and every entry point
converts a load failure into :class:`FFIUnavailable`. That is not tidiness: this module is
reachable from the gateway's process (via ``driver_host.resolve_driver``), and a module-level
framework load that raised on a non-Darwin build — or on a Darwin build where a framework had
moved — would turn an unavailable capability into a gateway that will not start.
``tests/test_computer_use_macos_driver.py::test_importing_the_ffi_touches_no_framework`` asserts
it by AST rather than by trusting this paragraph.

**The pointer boundary is drawn in this module and nowhere else.** Exactly one function warps
the operator's real cursor — :func:`click_global` — and it is the only caller of
``CGWarpMouseCursorPosition`` in the codebase. Element activation (:func:`press`,
:func:`perform_action`), value setting and typing post no mouse event of any kind, which is what
makes §3 floor 2's "the pointer never moves by accident" a property of the code rather than an
intention.
"""

from __future__ import annotations

import ctypes
import platform
from dataclasses import dataclass
from typing import Any

from personalclaw.computer_use.types import MAX_DEPTH, MAX_ELEMENTS, Element, WindowWalk

#: Absolute framework paths. Not ``ctypes.util.find_library``: these three are guaranteed by the
#: OS, and an absolute path that has moved is a clear failure rather than a silent fallback onto
#: whatever else the loader found.
_APPLICATION_SERVICES = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
_LIBSYSTEM = "/usr/lib/libSystem.dylib"

_UTF8 = 0x08000100
_AX_VALUE_CGPOINT = 1
_AX_VALUE_CGSIZE = 2

#: ``kAXErrorSuccess`` and the two failures that mean "the operator has not granted this".
_AX_SUCCESS = 0
_AX_API_DISABLED = -25211
_AX_NOT_TRUSTED_ERRORS = frozenset({_AX_API_DISABLED})

#: ``kCGEventLeftMouseDown`` / ``Up`` and the scroll unit. Named rather than inlined so the two
#: coordinate paths read as the deliberate exceptions they are.
_LEFT_MOUSE_DOWN = 1
_LEFT_MOUSE_UP = 2
_SCROLL_UNIT_LINE = 1

#: ``kCGHIDEventTap`` — the global event stream. Used by exactly one function
#: (:func:`click_global`), because posting here is indistinguishable from the operator's own
#: hand and therefore lands wherever their cursor now is.
_HID_EVENT_TAP = 0

#: ``proc_listpids(PROC_ALL_PIDS, ...)``.
_PROC_ALL_PIDS = 1

#: A GUI application's executable lives inside its bundle. This substring is how a bundled app
#: is told from the several hundred daemons and helpers a Mac runs, without AppKit and without
#: any permission — which is why :func:`list_gui_apps` works before the operator has granted
#: accessibility, and why ``computer_list_apps`` can name what to ask for.
_BUNDLE_MARKER = ".app/Contents/MacOS/"

#: Attributes read for every element. ``AXValue`` is included because the dispatch's
#: secure-field screen refuses a destination already holding credential-shaped text, and it
#: cannot do that over a value the driver declined to report.
_TEXT_ATTRIBUTES = {
    "title": "AXTitle",
    "value": "AXValue",
    "placeholder": "AXPlaceholderValue",
    "description": "AXDescription",
    "help": "AXHelp",
    "role": "AXRole",
    "subrole": "AXSubrole",
}


class FFIUnavailable(Exception):
    """This build cannot reach the macOS accessibility API at all (wrong OS, or no framework)."""


class AXPermissionDenied(Exception):
    """The API is present but this process is not trusted for accessibility.

    Distinct from :class:`FFIUnavailable` because the fixes are completely different: one is
    "you are not on macOS", the other is "click this checkbox". Collapsing them would produce a
    refusal that could not tell an operator which.
    """


class AppNotFound(Exception):
    """No running GUI application matches the requested name."""


class AXCallFailed(Exception):
    """An accessibility call returned an error that is not a permission problem."""


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


@dataclass
class _Frameworks:
    """The loaded libraries, with every signature already bound."""

    ax: Any
    cf: Any
    cg: Any
    libc: Any


_LOADED: _Frameworks | None = None


def _bind(frameworks: _Frameworks) -> None:
    """Declare every signature once.

    ctypes defaults every argument and return to ``int``, which on arm64 silently truncates a
    64-bit pointer to 32 bits. So an unbound signature here is not a style problem — it is a
    corrupted ``AXUIElementRef`` and an undebuggable crash.
    """
    ax, cf, cg, libc = frameworks.ax, frameworks.cf, frameworks.cg, frameworks.libc
    void, ptr = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)

    cf.CFStringCreateWithCString.restype = void
    cf.CFStringCreateWithCString.argtypes = [void, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [void, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFGetTypeID.argtypes = [void]
    for getter in ("CFStringGetTypeID", "CFArrayGetTypeID", "CFBooleanGetTypeID"):
        getattr(cf, getter).restype = ctypes.c_ulong
        getattr(cf, getter).argtypes = []
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [void]
    cf.CFArrayGetValueAtIndex.restype = void
    cf.CFArrayGetValueAtIndex.argtypes = [void, ctypes.c_long]
    cf.CFBooleanGetValue.restype = ctypes.c_bool
    cf.CFBooleanGetValue.argtypes = [void]

    ax.AXUIElementCreateApplication.restype = void
    ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
    ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int
    ax.AXUIElementCopyAttributeValue.argtypes = [void, void, ptr]
    ax.AXUIElementSetAttributeValue.restype = ctypes.c_int
    ax.AXUIElementSetAttributeValue.argtypes = [void, void, void]
    ax.AXUIElementPerformAction.restype = ctypes.c_int
    ax.AXUIElementPerformAction.argtypes = [void, void]
    ax.AXUIElementCopyActionNames.restype = ctypes.c_int
    ax.AXUIElementCopyActionNames.argtypes = [void, ptr]
    ax.AXIsProcessTrusted.restype = ctypes.c_bool
    ax.AXIsProcessTrusted.argtypes = []
    ax.AXValueGetValue.restype = ctypes.c_bool
    ax.AXValueGetValue.argtypes = [void, ctypes.c_uint32, void]

    cg.CGEventCreate.restype = void
    cg.CGEventCreate.argtypes = [void]
    cg.CGEventGetLocation.restype = _CGPoint
    cg.CGEventGetLocation.argtypes = [void]
    cg.CGEventCreateMouseEvent.restype = void
    cg.CGEventCreateMouseEvent.argtypes = [void, ctypes.c_uint32, _CGPoint, ctypes.c_uint32]
    cg.CGEventCreateKeyboardEvent.restype = void
    cg.CGEventCreateKeyboardEvent.argtypes = [void, ctypes.c_uint16, ctypes.c_bool]
    cg.CGEventKeyboardSetUnicodeString.restype = None
    cg.CGEventKeyboardSetUnicodeString.argtypes = [
        void,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_uint16),
    ]
    # The non-variadic sibling of CGEventCreateScrollWheelEvent. The variadic form cannot be
    # called correctly through ctypes on arm64, where variadic arguments follow a different
    # register discipline from fixed ones.
    cg.CGEventCreateScrollWheelEvent2.restype = void
    cg.CGEventCreateScrollWheelEvent2.argtypes = [
        void,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_int32,
    ]
    cg.CGEventPostToPid.restype = None
    cg.CGEventPostToPid.argtypes = [ctypes.c_int32, void]
    cg.CGEventPost.restype = None
    cg.CGEventPost.argtypes = [ctypes.c_uint32, void]
    cg.CGWarpMouseCursorPosition.restype = ctypes.c_int
    cg.CGWarpMouseCursorPosition.argtypes = [_CGPoint]

    libc.proc_listpids.restype = ctypes.c_int
    libc.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, void, ctypes.c_int]
    libc.proc_pidpath.restype = ctypes.c_int
    libc.proc_pidpath.argtypes = [ctypes.c_int32, ctypes.c_char_p, ctypes.c_uint32]


def _load() -> _Frameworks:
    """The loaded frameworks, or :class:`FFIUnavailable`. Cached; never raises at import."""
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    if platform.system() != "Darwin":
        raise FFIUnavailable(f"the macOS accessibility API does not exist on {platform.system()}")
    try:
        frameworks = _Frameworks(
            ax=ctypes.cdll.LoadLibrary(_APPLICATION_SERVICES),
            cf=ctypes.cdll.LoadLibrary(_CORE_FOUNDATION),
            cg=ctypes.cdll.LoadLibrary(_CORE_GRAPHICS),
            libc=ctypes.CDLL(_LIBSYSTEM),
        )
        _bind(frameworks)
    except (OSError, AttributeError) as exc:
        raise FFIUnavailable(f"a required macOS framework could not be loaded: {exc}") from exc
    _LOADED = frameworks
    return frameworks


def is_process_trusted() -> bool:
    """``AXIsProcessTrusted()`` — has the operator granted this binary accessibility access.

    Read, never *requested*: the API also offers ``AXIsProcessTrustedWithOptions`` with a prompt
    option, and using it would make a tool call pop a system dialog on the operator's screen.
    An agent-triggered permission prompt is a consent surface the agent chose the timing of, so
    this build only ever reports the answer and names the manual step in its FIX.
    """
    return bool(_load().ax.AXIsProcessTrusted())


def pointer_position() -> tuple[float, float]:
    """Where the operator's real cursor is right now. Needs no permission.

    Its only caller is the rail that asserts the pointer did not move across an element
    activation. That is deliberate rather than dead code: the alternative is ctypes inside the
    test file, which would break the property this module exists to hold — that every OS call in
    this feature is in one auditable place.
    """
    frameworks = _load()
    event = frameworks.cg.CGEventCreate(None)
    point = frameworks.cg.CGEventGetLocation(ctypes.c_void_p(event))
    return (float(point.x), float(point.y))


def _cfstr(frameworks: _Frameworks, text: str) -> Any:
    return frameworks.cf.CFStringCreateWithCString(None, text.encode("utf-8"), _UTF8)


def _from_cfstring(frameworks: _Frameworks, ref: Any) -> str:
    buffer = ctypes.create_string_buffer(4096)
    if not frameworks.cf.CFStringGetCString(ctypes.c_void_p(ref), buffer, 4096, _UTF8):
        return ""
    return buffer.value.decode("utf-8", "replace")


def _check(code: int, what: str) -> None:
    """Turn an ``AXError`` into the right exception, so permission never reads as a defect."""
    if code == _AX_SUCCESS:
        return
    if code in _AX_NOT_TRUSTED_ERRORS:
        raise AXPermissionDenied(f"{what} was refused by the OS (AXError {code})")
    raise AXCallFailed(f"{what} failed with AXError {code}")


def _attribute(frameworks: _Frameworks, element: Any, name: str) -> Any:
    """One attribute, or ``None`` when the element simply does not expose it.

    An unsupported attribute is ordinary — a static text has no ``AXPlaceholderValue`` — so it
    returns ``None`` rather than raising. A *permission* error is not ordinary and still raises.
    """
    out = ctypes.c_void_p()
    attribute = _cfstr(frameworks, name)
    code = frameworks.ax.AXUIElementCopyAttributeValue(
        ctypes.c_void_p(element), ctypes.c_void_p(attribute), ctypes.byref(out)
    )
    if code in _AX_NOT_TRUSTED_ERRORS:
        raise AXPermissionDenied(f"reading {name} was refused by the OS (AXError {code})")
    if code != _AX_SUCCESS:
        return None
    return out.value


def _text_attribute(frameworks: _Frameworks, element: Any, name: str) -> str:
    """An attribute as text, always a ``str``.

    Never ``None``: ``policy.check_input_target`` refuses a screened key whose value is not a
    string, so a ``None`` title here would turn every element into a malformed target.
    """
    value = _attribute(frameworks, element, name)
    if value is None:
        return ""
    if frameworks.cf.CFGetTypeID(ctypes.c_void_p(value)) == frameworks.cf.CFStringGetTypeID():
        return _from_cfstring(frameworks, value)
    return ""


def _bool_attribute(frameworks: _Frameworks, element: Any, name: str, default: bool) -> bool:
    value = _attribute(frameworks, element, name)
    if value is None:
        return default
    if frameworks.cf.CFGetTypeID(ctypes.c_void_p(value)) == frameworks.cf.CFBooleanGetTypeID():
        return bool(frameworks.cf.CFBooleanGetValue(ctypes.c_void_p(value)))
    return default


def _frame(frameworks: _Frameworks, element: Any) -> tuple[float, float, float, float]:
    """The element's screen rectangle, or zeroes when it exposes no geometry."""
    x = y = width = height = 0.0
    position = _attribute(frameworks, element, "AXPosition")
    if position is not None:
        point = _CGPoint()
        if frameworks.ax.AXValueGetValue(
            ctypes.c_void_p(position), _AX_VALUE_CGPOINT, ctypes.byref(point)
        ):
            x, y = float(point.x), float(point.y)
    size = _attribute(frameworks, element, "AXSize")
    if size is not None:
        extent = _CGSize()
        if frameworks.ax.AXValueGetValue(
            ctypes.c_void_p(size), _AX_VALUE_CGSIZE, ctypes.byref(extent)
        ):
            width, height = float(extent.width), float(extent.height)
    return (x, y, width, height)


def _array_items(frameworks: _Frameworks, ref: Any) -> list[Any]:
    if ref is None:
        return []
    if frameworks.cf.CFGetTypeID(ctypes.c_void_p(ref)) != frameworks.cf.CFArrayGetTypeID():
        return []
    count = frameworks.cf.CFArrayGetCount(ctypes.c_void_p(ref))
    return [
        frameworks.cf.CFArrayGetValueAtIndex(ctypes.c_void_p(ref), position)
        for position in range(count)
    ]


def _action_names(frameworks: _Frameworks, element: Any) -> tuple[str, ...]:
    out = ctypes.c_void_p()
    code = frameworks.ax.AXUIElementCopyActionNames(ctypes.c_void_p(element), ctypes.byref(out))
    if code in _AX_NOT_TRUSTED_ERRORS:
        raise AXPermissionDenied(f"reading actions was refused by the OS (AXError {code})")
    if code != _AX_SUCCESS:
        return ()
    return tuple(
        _from_cfstring(frameworks, item) for item in _array_items(frameworks, out.value) if item
    )


def list_gui_apps() -> list[str]:
    """Every running bundled application's name, from ``libproc``. Needs no permission.

    Deliberately not AppKit's ``NSWorkspace``: that needs pyobjc, and this is a list of process
    executable paths the kernel already hands any process. The dispatch narrows this to the
    operator's allowlist in step 7, so breadth here costs nothing and lets
    ``computer_list_apps`` report honestly how many were withheld.
    """
    frameworks = _load()
    needed = frameworks.libc.proc_listpids(_PROC_ALL_PIDS, 0, None, 0)
    if needed <= 0:
        return []
    slots = needed // ctypes.sizeof(ctypes.c_int32)
    buffer = (ctypes.c_int32 * slots)()
    frameworks.libc.proc_listpids(_PROC_ALL_PIDS, 0, ctypes.byref(buffer), needed)
    path = ctypes.create_string_buffer(4096)
    names: set[str] = set()
    for pid in buffer:
        if pid <= 0:
            continue
        if frameworks.libc.proc_pidpath(pid, path, 4096) <= 0:
            continue
        executable = path.value.decode("utf-8", "replace")
        if _BUNDLE_MARKER in executable:
            names.add(executable.rsplit("/", 1)[-1])
    return sorted(names)


def resolve_app_pid(app: str) -> int:
    """The pid of the running application named *app*, or :class:`AppNotFound`.

    Exact match on the bundle executable name, the same string :func:`list_gui_apps` reports.
    No fuzzy matching: "the app whose name is closest to what you asked for" is how an agent
    ends up driving the wrong window.
    """
    frameworks = _load()
    wanted = app.strip()
    if not wanted:
        raise AppNotFound("no application name was given")
    needed = frameworks.libc.proc_listpids(_PROC_ALL_PIDS, 0, None, 0)
    slots = max(needed, 0) // ctypes.sizeof(ctypes.c_int32)
    buffer = (ctypes.c_int32 * slots)()
    frameworks.libc.proc_listpids(_PROC_ALL_PIDS, 0, ctypes.byref(buffer), needed)
    path = ctypes.create_string_buffer(4096)
    for pid in buffer:
        if pid <= 0:
            continue
        if frameworks.libc.proc_pidpath(pid, path, 4096) <= 0:
            continue
        executable = path.value.decode("utf-8", "replace")
        if _BUNDLE_MARKER in executable and executable.rsplit("/", 1)[-1] == wanted:
            return int(pid)
    raise AppNotFound(f"no running application is named {wanted!r}")


def _front_window(frameworks: _Frameworks, application: Any) -> Any:
    """The window a click would land in: the focused one, else the first in ``AXWindows``."""
    focused = _attribute(frameworks, application, "AXFocusedWindow")
    if focused is not None:
        return focused
    windows = _array_items(frameworks, _attribute(frameworks, application, "AXWindows"))
    if not windows:
        raise AXCallFailed("the application exposes no windows")
    return windows[0]


def walk_window(app: str) -> WindowWalk:
    """Walk *app*'s front window into a flat, indexed list plus parallel opaque handles.

    Depth-first pre-order, window first. The order must be deterministic and it must be the
    *same* order on a re-walk, because that is what makes an index from one snapshot mean the
    same element on the next — the whole premise of the fingerprint check.

    Both caps (:data:`~personalclaw.computer_use.types.MAX_ELEMENTS`,
    :data:`~personalclaw.computer_use.types.MAX_DEPTH`) are enforced here rather than left to
    the ceilinged spawn, because a truncated answer that says so is more useful than a killed
    process.
    """
    frameworks = _load()
    if not is_process_trusted():
        raise AXPermissionDenied("this process is not trusted for accessibility")
    pid = resolve_app_pid(app)
    application = frameworks.ax.AXUIElementCreateApplication(pid)
    window = _front_window(frameworks, application)

    walk = WindowWalk()
    stack: list[tuple[Any, int]] = [(window, 0)]
    while stack:
        if len(walk.elements) >= MAX_ELEMENTS:
            walk.truncated = True
            break
        handle, depth = stack.pop(0)
        text = {
            field: _text_attribute(frameworks, handle, attribute)
            for field, attribute in _TEXT_ATTRIBUTES.items()
        }
        walk.elements.append(
            Element(
                index=len(walk.elements),
                enabled=_bool_attribute(frameworks, handle, "AXEnabled", True),
                frame=_frame(frameworks, handle),
                actions=_action_names(frameworks, handle),
                **text,
            )
        )
        walk.handles.append(handle)
        if depth < MAX_DEPTH:
            children = _array_items(frameworks, _attribute(frameworks, handle, "AXChildren"))
            stack = [(child, depth + 1) for child in children if child] + stack
    return walk


def press(handle: Any) -> None:
    """Activate an element through the accessibility API. Posts no mouse event.

    This is what ``click_method="auto"`` resolves to, and it is the reason the default click
    moves nothing: ``AXPress`` is delivered to the element by the OS, so the operator's cursor
    stays exactly where they left it and the click cannot land somewhere else because a window
    moved between the decision and the act.
    """
    perform_action(handle, "AXPress")


def perform_action(handle: Any, action: str) -> None:
    """Perform a named accessibility action. Posts no mouse event."""
    frameworks = _load()
    name = _cfstr(frameworks, action)
    code = frameworks.ax.AXUIElementPerformAction(ctypes.c_void_p(handle), ctypes.c_void_p(name))
    _check(code, f"performing {action}")


def set_value(handle: Any, value: str) -> None:
    """Set the element's ``AXValue`` directly. Posts no mouse or key event."""
    frameworks = _load()
    attribute = _cfstr(frameworks, "AXValue")
    text = _cfstr(frameworks, value)
    code = frameworks.ax.AXUIElementSetAttributeValue(
        ctypes.c_void_p(handle), ctypes.c_void_p(attribute), ctypes.c_void_p(text)
    )
    _check(code, "setting the element value")


def focus(handle: Any) -> None:
    """Make the element the keyboard focus, so typed keys reach it. Posts no mouse event."""
    frameworks = _load()
    attribute = _cfstr(frameworks, "AXFocused")
    true_ref = ctypes.c_void_p.in_dll(frameworks.cf, "kCFBooleanTrue")
    code = frameworks.ax.AXUIElementSetAttributeValue(
        ctypes.c_void_p(handle), ctypes.c_void_p(attribute), true_ref
    )
    _check(code, "focusing the element")


def type_text(pid: int, text: str) -> None:
    """Type *text* into whatever is focused in process *pid*. Posts key events, never a mouse one.

    Delivered with ``CGEventPostToPid`` rather than ``CGEventPost``, so the keys go to the
    target process instead of the global event stream — a keystroke posted globally would land
    in whatever the operator happened to switch to.
    """
    frameworks = _load()
    for down in (True, False):
        event = frameworks.cg.CGEventCreateKeyboardEvent(None, 0, down)
        if not event:
            raise AXCallFailed("a keyboard event could not be created")
        units = text.encode("utf-16-le")
        count = len(units) // 2
        buffer = (ctypes.c_uint16 * max(count, 1)).from_buffer_copy(units or b"\x00\x00")
        frameworks.cg.CGEventKeyboardSetUnicodeString(ctypes.c_void_p(event), count, buffer)
        frameworks.cg.CGEventPostToPid(pid, ctypes.c_void_p(event))


def scroll(pid: int, vertical: int, horizontal: int) -> None:
    """Scroll by whole lines inside process *pid*. Posts a wheel event, never a mouse move."""
    frameworks = _load()
    event = frameworks.cg.CGEventCreateScrollWheelEvent2(
        None, _SCROLL_UNIT_LINE, 2, int(vertical), int(horizontal), 0
    )
    if not event:
        raise AXCallFailed("a scroll event could not be created")
    frameworks.cg.CGEventPostToPid(pid, ctypes.c_void_p(event))


def click_located(pid: int, x: float, y: float) -> None:
    """Post a click at *x*,*y* to process *pid* WITHOUT moving the real cursor.

    §2 reserves this for canvas and custom-drawn UI that exposes no addressable element. The
    event carries a location, but ``CGEventPostToPid`` delivers it to one process's event queue
    and the window server never moves the physical pointer — which is why this path, unlike
    :func:`click_global`, does not disturb whatever the operator is doing.
    """
    frameworks = _load()
    point = _CGPoint(x=float(x), y=float(y))
    for kind in (_LEFT_MOUSE_DOWN, _LEFT_MOUSE_UP):
        event = frameworks.cg.CGEventCreateMouseEvent(None, kind, point, 0)
        if not event:
            raise AXCallFailed("a mouse event could not be created")
        frameworks.cg.CGEventPostToPid(pid, ctypes.c_void_p(event))


def click_global(x: float, y: float) -> None:
    """Warp the operator's REAL cursor to *x*,*y* and click there.

    **The only function in this codebase that moves the physical pointer.** It is reachable only
    when a model names ``click_method="global"`` — ``auto`` never resolves onto it (§3 floor 2,
    enforced in ``service._click_method``) — and the dispatch audits it under its own SEL
    operation so a real-cursor warp is one filter away from every ordinary click.
    ``test_only_one_function_warps_the_real_cursor`` pins that this remains the sole caller.
    """
    frameworks = _load()
    point = _CGPoint(x=float(x), y=float(y))
    frameworks.cg.CGWarpMouseCursorPosition(point)
    for kind in (_LEFT_MOUSE_DOWN, _LEFT_MOUSE_UP):
        event = frameworks.cg.CGEventCreateMouseEvent(None, kind, point, 0)
        if not event:
            raise AXCallFailed("a mouse event could not be created")
        # The HID tap, not CGEventPostToPid: this method's whole point is that the click lands
        # wherever the real cursor now is, exactly as if the operator had done it.
        frameworks.cg.CGEventPost(_HID_EVENT_TAP, ctypes.c_void_p(event))
