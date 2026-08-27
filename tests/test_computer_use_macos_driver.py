"""Rails for the macOS accessibility driver (`DCU-3`).

`DCU-4` shipped the dispatch, the ceilinged spawn and the snapshot store, and
``driver_host.resolve_driver`` has been importing ``personalclaw.computer_use.macos_driver`` and
finding **nothing** ever since. So the question this file answers is not "does a TTL work" —
``test_computer_use_dispatch.py`` already proves the dispatch-side TTL and fingerprint from both
sides — but the four things only the driver can get wrong:

1. **Does the capability now exist at its call site.** ``resolve_driver("Darwin")`` returning the
   module, and a handler present for every op the dispatch derives from the tool surface.
   :func:`test_every_dispatch_op_has_a_handler` derives the op names from ``TOOL_SURFACE`` rather
   than listing them, so a tool added later cannot quietly have no driver handler.
2. **Does the pointer stay still.** Asserted as the SET OF OS CALLS a given op makes, against a
   recording double for the FFI module — not as prose. An ``auto`` click that posted a mouse
   event would be visible as an extra recorded call, and
   :func:`test_the_pointer_rail_detects_a_mouse_event` proves the recording can fail.
3. **Does staleness fail closed at the moment of acting**, which is the one check the dispatch
   cannot make for itself: its own re-walk happens before the secure-field screen and the SEL
   row, so the window can move in between. Every refusal here is paired with a VACUITY case
   proving the same code path accepts a fresh index — a refusal test whose accept case is
   untested proves only that the function refuses everything.
4. **Does an absent framework refuse instead of exploding.** The driver is imported inside the
   gateway's own process, so a module-level framework load would turn "no desktop capability"
   into "no gateway".

**What ran for real on the authoring machine, and what did not.** ``op_list_apps``, the FFI
symbol binding, the real pointer read and the end-to-end spawn through ``computer_dispatch`` all
executed against the real OS. The AX-*driving* legs did not and cannot without a human grant:
``AXIsProcessTrusted()`` is False and macOS answers ``kAXErrorAPIDisabled`` (-25211), which is
itself asserted here as the real OS answer rather than mocked. Rather than mark those legs
skipped — a skipped surface reads like a pass —
:func:`test_the_accessibility_permission_refusal_is_the_real_os_answer` and
:func:`test_the_real_spawn_reaches_the_real_driver_and_its_code_survives` run UNCONDITIONALLY on
macOS and assert a different, meaningful end state on each side of the grant. Every AX-driving
behaviour is additionally proven against a recording double, so the logic is covered on a
machine where the OS will not cooperate.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import importlib
import inspect
import json
import pathlib
import platform

import pytest

from personalclaw.computer_use import (
    driver_host,
    enable_state,
    macos_driver,
    macos_ffi,
    policy,
    service,
)
from personalclaw.computer_use import tools as ct
from personalclaw.computer_use.types import Element, WindowWalk, fingerprint_of

IS_DARWIN = platform.system() == "Darwin"

_NEEDS_DARWIN = pytest.mark.skipif(not IS_DARWIN, reason="the macOS driver only runs on Darwin")

#: The OS calls that move or post a pointer. An ``auto`` click, a type, a set-value and a scroll
#: must make NONE of them; only the two explicitly-named coordinate methods may.
_POINTER_CALLS = frozenset({"click_located", "click_global"})


def _elements() -> list[Element]:
    """A small, realistic window: the window itself, a button, and a text area."""
    return [
        Element(index=0, role="AXWindow", title="Untitled", frame=(0.0, 0.0, 800.0, 600.0)),
        Element(
            index=1,
            role="AXButton",
            title="Save",
            actions=("AXPress", "AXShowMenu"),
            frame=(10.0, 10.0, 60.0, 20.0),
        ),
        Element(
            index=2,
            role="AXTextArea",
            title="Body",
            value="Lunch on Tuesday",
            actions=("AXPress",),
            frame=(0.0, 40.0, 800.0, 560.0),
        ),
    ]


class _RecordingFFI:
    """A double for :mod:`~personalclaw.computer_use.macos_ffi` that records every call.

    Substituted onto the real module's attributes, so the driver's own ``ffi.press(...)`` call
    sites are the ones exercised. The exception classes are deliberately NOT replaced: the
    driver's error mapping must be proven against the real ones.
    """

    def __init__(self, elements: list[Element] | None = None, pid: int = 4321) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._elements = list(elements if elements is not None else _elements())
        self.pid = pid

    @property
    def names(self) -> list[str]:
        return [name for name, _args in self.calls]

    def install(self, monkeypatch) -> _RecordingFFI:
        for name in (
            "walk_window",
            "list_gui_apps",
            "resolve_app_pid",
            "press",
            "perform_action",
            "set_value",
            "focus",
            "type_text",
            "scroll",
            "click_located",
            "click_global",
        ):
            monkeypatch.setattr(macos_ffi, name, getattr(self, f"_{name}"))
        return self

    def _walk_window(self, app):
        self.calls.append(("walk_window", (app,)))
        return WindowWalk(
            elements=list(self._elements),
            handles=[f"handle-{element.index}" for element in self._elements],
        )

    def _list_gui_apps(self):
        self.calls.append(("list_gui_apps", ()))
        return ["TextEdit", "Finder"]

    def _resolve_app_pid(self, app):
        self.calls.append(("resolve_app_pid", (app,)))
        return self.pid

    def _press(self, handle):
        self.calls.append(("press", (handle,)))

    def _perform_action(self, handle, action):
        self.calls.append(("perform_action", (handle, action)))

    def _set_value(self, handle, value):
        self.calls.append(("set_value", (handle, value)))

    def _focus(self, handle):
        self.calls.append(("focus", (handle,)))

    def _type_text(self, pid, text):
        self.calls.append(("type_text", (pid, text)))

    def _scroll(self, pid, vertical, horizontal):
        self.calls.append(("scroll", (pid, vertical, horizontal)))

    def _click_located(self, pid, x, y):
        self.calls.append(("click_located", (pid, x, y)))

    def _click_global(self, x, y):
        self.calls.append(("click_global", (x, y)))


@pytest.fixture
def ffi(monkeypatch) -> _RecordingFFI:
    return _RecordingFFI().install(monkeypatch)


def _act(request: dict) -> dict:
    """An element-indexed click request whose fingerprint matches the default tree."""
    return {
        "app": "TextEdit",
        "fingerprint": fingerprint_of(_elements()),
        "element_index": 1,
        **request,
    }


def _code(answer: dict) -> str:
    return str(answer.get("error", {}).get("code", ""))


# ---------------------------------------------------------------------------
# 1. The call site this atom lands
# ---------------------------------------------------------------------------


@_NEEDS_DARWIN
def test_driver_host_resolves_the_macos_driver():
    """The whole point of the atom: this returned ``None`` on every commit before it.

    ``resolve_driver`` resolves by import rather than by a capability flag, so this assertion
    is the real one — it is the same call the ceilinged child makes.
    """
    assert driver_host.resolve_driver("Darwin") is macos_driver


def test_every_dispatch_op_has_a_handler():
    """Derived from the tool surface, not tabulated, so a new tool cannot skip the driver."""
    expected = {spec.name[len("computer_") :] for spec in ct.TOOL_SURFACE}
    missing = [
        op for op in sorted(expected) if not callable(getattr(macos_driver, f"op_{op}", None))
    ]
    assert not missing, f"the macOS driver has no handler for {missing}"


def test_the_handler_set_is_not_vacuous():
    """A floor under the test above: it would pass over an empty tool surface."""
    assert len(ct.TOOL_SURFACE) == 7, "the tool surface changed; the derived op set moved with it"


def test_the_new_error_code_is_registered():
    """``errors.ERROR_CODES`` is append-only and a new failure path must add a code."""
    from personalclaw.errors import ERROR_CODES

    assert macos_driver.ERR_AX_PERMISSION in ERROR_CODES


def test_the_dispatch_honours_the_drivers_own_refusal_codes():
    """Without this, the AX-permission FIX is flattened into "the driver failed".

    ``_run_driver`` allowlists the codes a child may name for itself. The driver is the only
    party that can ask the OS about input access or re-walk the tree at the moment of acting, so
    both of its refusals have to be in that set — and it must remain an ALLOWLIST.
    """
    assert macos_driver.ERR_AX_PERMISSION in service._CHILD_CODES
    assert macos_driver.ERR_STALE_INDEX in service._CHILD_CODES
    assert "ERR_COMPUTER_USE_APP_NOT_ALLOWED" not in service._CHILD_CODES, (
        "a child able to claim a policy verdict could dress a failure up as one the policy "
        "never reached"
    )


# ---------------------------------------------------------------------------
# 2. Import safety and the absent-framework path
# ---------------------------------------------------------------------------


def _module_source(module) -> ast.Module:
    return ast.parse(pathlib.Path(inspect.getsourcefile(module)).read_text(encoding="utf-8"))


def test_importing_the_ffi_touches_no_framework():
    """No framework load at module level — importing this must never raise.

    The driver is imported inside the gateway's process. A module-level ``LoadLibrary`` would
    turn a machine without the capability into a machine without a gateway.
    """
    tree = _module_source(macos_ffi)
    loaders = {"LoadLibrary", "CDLL", "in_dll"}
    offenders = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                name = getattr(inner.func, "attr", getattr(inner.func, "id", ""))
                if name in loaders:
                    offenders.append(name)
    assert not offenders, f"macos_ffi loads a framework at import time via {offenders}"


def test_the_macos_driver_holds_no_ctypes():
    """Every OS type stays inside the FFI module, so the OS surface is one auditable file."""
    tree = _module_source(macos_driver)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "ctypes" not in imported


def test_the_driver_imports_and_refuses_where_the_frameworks_are_absent(monkeypatch):
    """The absent-dependency path, simulated: a non-Darwin build imports and refuses cleanly.

    This is the leg that would otherwise only be exercised on a Linux runner. ``_load`` is the
    single seam every entry point goes through, so forcing it to report the platform as absent
    reproduces exactly what a Linux install does.
    """
    monkeypatch.setattr(macos_ffi, "_LOADED", None)
    monkeypatch.setattr(macos_ffi.platform, "system", lambda: "Linux")

    reloaded = importlib.reload(macos_driver)  # must not raise
    try:
        with pytest.raises(macos_ffi.FFIUnavailable):
            macos_ffi._load()
        answer = reloaded.op_snapshot({"app": "TextEdit"})
        assert _code(answer) == macos_driver.ERR_DRIVER_UNAVAILABLE
        assert "Darwin" not in answer["error"]["message"]
    finally:
        monkeypatch.undo()
        macos_ffi._LOADED = None
        importlib.reload(macos_driver)


def test_every_op_refuses_rather_than_raising_when_the_frameworks_are_unavailable(monkeypatch):
    """Discovered by introspection, so a future op cannot escape the guard.

    A raise here would reach ``driver_host`` as a generic driver fault, losing the reason. Each
    op must answer with a typed envelope instead.
    """

    def unavailable(*_args, **_kwargs):
        raise macos_ffi.FFIUnavailable("simulated: no frameworks on this platform")

    for name in ("walk_window", "list_gui_apps", "resolve_app_pid"):
        monkeypatch.setattr(macos_ffi, name, unavailable)

    ops = [name for name in dir(macos_driver) if name.startswith("op_")]
    assert len(ops) == 7, f"expected seven ops, found {ops}"
    for name in ops:
        answer = getattr(macos_driver, name)(
            _act({"text": "x", "value": "x", "action": "AXPress", "direction": "up"})
        )
        assert _code(answer) == macos_driver.ERR_DRIVER_UNAVAILABLE, f"{name} did not refuse"


# ---------------------------------------------------------------------------
# 3. The real OS legs (no accessibility permission required)
# ---------------------------------------------------------------------------


@_NEEDS_DARWIN
def test_the_frameworks_load_and_every_symbol_binds():
    """Real: the absolute framework paths resolve and every signature binds on this machine."""
    frameworks = macos_ffi._load()
    assert frameworks.ax.AXUIElementCreateApplication.restype is not None
    assert frameworks.cg.CGWarpMouseCursorPosition.argtypes


@_NEEDS_DARWIN
def test_list_apps_reports_real_running_applications():
    """Real, and permissionless by design: an operator must be able to discover the app name
    to allowlist BEFORE granting accessibility."""
    answer = macos_driver.op_list_apps({})
    assert "error" not in answer, answer
    assert answer["apps"], "no bundled applications were found on a running desktop"
    assert all(isinstance(name, str) and name for name in answer["apps"])


@_NEEDS_DARWIN
def test_the_accessibility_permission_refusal_is_the_real_os_answer():
    """Real, and unconditional on macOS so it cannot read as a skip.

    Both branches are meaningful: without the grant the OS answers ``kAXErrorAPIDisabled`` and
    the driver must turn that into the operator-fixable code (never a generic driver failure);
    with the grant it must produce an actual indexed tree.
    """
    trusted = macos_ffi.is_process_trusted()
    answer = macos_driver.op_snapshot({"app": "Finder"})
    if not trusted:
        assert _code(answer) == macos_driver.ERR_AX_PERMISSION, answer
        assert "System Settings" in answer["error"]["fix"]
        assert "Accessibility" in answer["error"]["fix"]
    else:
        assert "error" not in answer, answer
        assert answer["elements"] and answer["fingerprint"]


@_NEEDS_DARWIN
def test_the_real_pointer_can_be_read_without_any_permission():
    """The precondition for asserting the pointer did not move, rather than claiming it."""
    x, y = macos_ffi.pointer_position()
    assert isinstance(x, float) and isinstance(y, float)


@_NEEDS_DARWIN
def test_an_absent_application_refuses_by_name():
    """Real: exact-match resolution, with no fuzzy fallback onto a similarly-named app."""
    with pytest.raises(macos_ffi.AppNotFound):
        macos_ffi.resolve_app_pid("NoSuchApplicationExists-DCU3")


# ---------------------------------------------------------------------------
# 4. The pointer never moves
# ---------------------------------------------------------------------------


def test_an_auto_click_presses_the_element_and_posts_no_pointer_event(ffi):
    answer = macos_driver.op_click(_act({}))
    assert "error" not in answer, answer
    assert answer["method"] == "auto"
    assert ("press", ("handle-1",)) in ffi.calls
    assert not _POINTER_CALLS & set(ffi.names), f"an auto click posted {ffi.names}"


@_NEEDS_DARWIN
def test_the_real_pointer_does_not_move_across_an_auto_click(ffi):
    """The recorded-call assertion above is the substantive one; this adds the real reading.

    Weak on its own — a mocked press could hardly move a cursor — but together they close both
    halves: no pointer call was made, AND the operator's physical pointer is where it was.
    """
    before = macos_ffi.pointer_position()
    macos_driver.op_click(_act({}))
    assert macos_ffi.pointer_position() == before


def test_typing_focuses_and_posts_keys_but_no_pointer_event(ffi):
    answer = macos_driver.op_type(_act({"element_index": 2, "text": "hello"}))
    assert "error" not in answer, answer
    assert ffi.names.count("focus") == 1
    assert ("type_text", (ffi.pid, "hello")) in ffi.calls
    assert not _POINTER_CALLS & set(ffi.names)


def test_set_value_posts_no_event_at_all(ffi):
    answer = macos_driver.op_set_value(_act({"element_index": 2, "value": "replaced"}))
    assert "error" not in answer, answer
    assert ("set_value", ("handle-2", "replaced")) in ffi.calls
    assert not _POINTER_CALLS & set(ffi.names)
    assert "type_text" not in ffi.names


def test_scrolling_posts_a_wheel_event_and_no_pointer_event(ffi):
    answer = macos_driver.op_scroll(_act({"element_index": 2, "direction": "down", "amount": 5}))
    assert "error" not in answer, answer
    assert ("scroll", (ffi.pid, -5, 0)) in ffi.calls
    assert not _POINTER_CALLS & set(ffi.names)


def test_only_the_explicitly_named_methods_touch_a_pointer(ffi):
    located = macos_driver.op_click({"app": "TextEdit", "click_method": "located", "x": 5, "y": 6})
    assert "error" not in located, located
    assert ("click_located", (ffi.pid, 5.0, 6.0)) in ffi.calls
    assert "click_global" not in ffi.names, "the located method must not warp the real cursor"
    assert located["pointer_moved"] is False

    warped = macos_driver.op_click({"click_method": "global", "x": 7, "y": 8})
    assert "error" not in warped, warped
    assert ("click_global", (7.0, 8.0)) in ffi.calls
    assert warped["pointer_moved"] is True


def test_the_pointer_rail_detects_a_mouse_event(monkeypatch, ffi):
    """Proof the rail above can fail: an ``auto`` click routed onto the pointer path trips it.

    Without this, ``test_an_auto_click_presses_the_element_and_posts_no_pointer_event`` would
    pass just as happily against a double that recorded nothing at all.
    """
    monkeypatch.setattr(
        macos_ffi, "press", lambda handle: ffi.calls.append(("click_global", (0.0, 0.0)))
    )
    macos_driver.op_click(_act({}))
    assert _POINTER_CALLS & set(ffi.names), "the rail would not have noticed a mouse event"


def test_only_one_function_warps_the_real_cursor():
    """A census, not a reading: ``CGWarpMouseCursorPosition`` has exactly one caller."""
    tree = _module_source(macos_ffi)
    warpers = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "CGWarpMouseCursorPosition" in ast.dump(node)
        and node.name != "_bind"
    ]
    assert warpers == ["click_global"], warpers


def test_no_other_module_in_the_package_warps_the_cursor():
    """The pointer boundary is drawn in one file, so it can be audited in one file."""
    package = pathlib.Path(inspect.getsourcefile(macos_driver)).parent
    offenders = [
        path.name
        for path in sorted(package.glob("*.py"))
        if "CGWarpMouseCursorPosition" in path.read_text(encoding="utf-8")
        and path.name != "macos_ffi.py"
    ]
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# 5. Staleness fails closed at the moment of acting — each with its vacuity case
# ---------------------------------------------------------------------------


def test_a_fresh_fingerprint_is_ACCEPTED_by_the_same_path_that_refuses_a_stale_one(ffi):
    """The vacuity half. Without it, a driver that refused everything would look correct."""
    answer = macos_driver.op_click(_act({}))
    assert "error" not in answer, answer
    assert ffi.names.count("walk_window") == 1


def test_a_stale_fingerprint_refuses_and_names_the_re_snapshot(ffi):
    answer = macos_driver.op_click(_act({"fingerprint": "fp-from-an-older-tree"}))
    assert _code(answer) == macos_driver.ERR_STALE_INDEX
    assert "computer_snapshot" in answer["error"]["fix"]
    assert "press" not in ffi.names, "a stale index must not reach the OS"


def test_a_window_that_changes_between_screening_and_acting_refuses(monkeypatch):
    """The reason the driver re-checks at all: the dispatch's own re-walk happens BEFORE the
    secure-field screen and the SEL row, so the window can move in the gap.

    The request carries the fingerprint of the tree as the dispatch screened it; the driver's own
    walk — the only one it makes — finds the button somewhere else. That is a window dragged
    mid-turn, and it must refuse rather than press whatever now sits at index 1.
    """
    moved = [
        (
            element
            if element.index != 1
            else dataclasses.replace(element, frame=(500.0, 400.0, 60.0, 20.0))
        )
        for element in _elements()
    ]
    ffi = _RecordingFFI(elements=moved).install(monkeypatch)
    answer = macos_driver.op_click(_act({}))
    assert _code(answer) == macos_driver.ERR_STALE_INDEX
    assert "press" not in ffi.names


def test_an_absent_fingerprint_fails_CLOSED(ffi):
    """A missing field must not read as "no check requested" — that makes the guard optional."""
    request = _act({})
    request.pop("fingerprint")
    answer = macos_driver.op_click(request)
    assert answer.get("error"), "an unverifiable tree was acted on"
    assert "press" not in ffi.names


def test_an_index_past_the_end_refuses_as_stale_and_the_last_index_acts(ffi):
    """Both sides of the bound, in one test, so neither can be vacuous."""
    last = macos_driver.op_click(_act({"element_index": 2}))
    assert "error" not in last, last

    past = macos_driver.op_click(_act({"element_index": 3}))
    assert _code(past) == macos_driver.ERR_STALE_INDEX
    assert "3" in past["error"]["message"]


def test_a_negative_index_refuses(ffi):
    assert _code(macos_driver.op_click(_act({"element_index": -1}))) == macos_driver.ERR_STALE_INDEX


def test_a_boolean_index_refuses_rather_than_reading_as_one(ffi):
    """``True`` is an ``int`` in Python and would silently press element 1."""
    answer = macos_driver.op_click(_act({"element_index": True}))
    assert answer.get("error")
    assert "press" not in ffi.names


# ---------------------------------------------------------------------------
# 6. The fingerprint's own contract
# ---------------------------------------------------------------------------


def test_the_fingerprint_ignores_the_value_a_user_is_typing():
    """Otherwise every second ``computer_type`` into the same field would refuse."""
    typed = [
        element if element.index != 2 else dataclasses.replace(element, value="Lunch on Wednesday")
        for element in _elements()
    ]
    assert fingerprint_of(typed) == fingerprint_of(_elements())


@pytest.mark.parametrize(
    "field,changed",
    [
        ("frame", (99.0, 99.0, 60.0, 20.0)),
        ("title", "Saved"),
        ("role", "AXMenuItem"),
        ("enabled", False),
        ("actions", ("AXPress", "AXShowMenu", "AXRaise")),
    ],
)
def test_the_fingerprint_notices_a_structural_change(field, changed):
    moved = [
        element if element.index != 1 else dataclasses.replace(element, **{field: changed})
        for element in _elements()
    ]
    assert fingerprint_of(moved) != fingerprint_of(_elements())


def test_the_fingerprint_notices_an_element_appearing():
    grown = _elements() + [Element(index=3, role="AXButton", title="Cancel")]
    assert fingerprint_of(grown) != fingerprint_of(_elements())


# ---------------------------------------------------------------------------
# 7. The element shape actually satisfies the dispatch's screens
# ---------------------------------------------------------------------------


def test_every_screened_key_is_a_string_never_none():
    """``check_input_target`` refuses a screened key whose value is not a string, so a ``None``
    title would turn every element into a malformed target."""
    shape = Element(index=0).to_dict()
    for key in ("role", "subrole", "title", "value", "placeholder", "description", "help"):
        assert isinstance(shape[key], str), key


def test_an_ordinary_text_element_passes_the_secure_field_screen():
    """The vacuity half of the pair below."""
    policy.check_input_target(_elements()[2].to_dict(), tool="computer_type")


@pytest.mark.parametrize(
    "element",
    [
        Element(index=0, role="AXTextField", subrole="AXSecureTextField", title="Password"),
        Element(index=0, role="AXTextField", title="Password"),
        Element(index=0, role="AXButton", title="Save"),
        Element(index=0),
    ],
    ids=["secure-subrole", "labelled-password", "not-a-text-role", "no-role-at-all"],
)
def test_the_drivers_own_element_shape_is_refused_where_it_should_be(element):
    """The driver's serialisation is screened by the REAL policy, not by a hand-written dict —
    a driver that spelled its keys differently would be screened against nothing."""
    with pytest.raises(policy.ComputerUsePolicyRefusal):
        policy.check_input_target(element.to_dict(), tool="computer_type")


# ---------------------------------------------------------------------------
# 8. Named actions and directions
# ---------------------------------------------------------------------------


def test_perform_action_refuses_an_action_the_element_does_not_advertise(ffi):
    answer = macos_driver.op_perform_action(_act({"action": "AXDelete"}))
    assert answer.get("error")
    assert "AXPress" in answer["error"]["fix"]
    assert "perform_action" not in ffi.names


def test_perform_action_runs_an_advertised_one(ffi):
    """The vacuity half: the refusal above would pass over a handler that refused everything."""
    answer = macos_driver.op_perform_action(_act({"action": "AXShowMenu"}))
    assert "error" not in answer, answer
    assert ("perform_action", ("handle-1", "AXShowMenu")) in ffi.calls


def test_scroll_refuses_an_unknown_direction(ffi):
    answer = macos_driver.op_scroll(_act({"element_index": 2, "direction": "sideways"}))
    assert answer.get("error")
    assert "scroll" not in ffi.names


def test_an_unknown_click_method_refuses_rather_than_falling_back(ffi):
    """No widening: a method this driver cannot run must never become ``auto`` or a coordinate."""
    answer = macos_driver.op_click({"app": "TextEdit", "click_method": "teleport"})
    assert answer.get("error")
    assert not set(ffi.names) & {"press", "click_located", "click_global"}


# ---------------------------------------------------------------------------
# 9. End to end through the real dispatch and the real ceilinged spawn
# ---------------------------------------------------------------------------


@_NEEDS_DARWIN
def test_the_real_spawn_reaches_the_real_driver_and_its_code_survives(tmp_path, monkeypatch):
    """The full chain with NOTHING faked: keystone, policy, SEL, ceilinged spawn, real driver.

    On a machine without the accessibility grant this asserts the honest end state — the code
    that reaches the model is the operator-fixable one, which is exactly what regresses if the
    driver's code is dropped from ``_run_driver``'s allowlist. With the grant it asserts a real
    indexed tree came back over the process boundary.
    """
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "enable.json"))
    (tmp_path / "enable.json").write_text(
        json.dumps({"version": 1, "enabled": True, "apps": ["Finder"]}), encoding="utf-8"
    )
    enable_state.reset_enable_state()
    service.reset_snapshots()
    try:
        if macos_ffi.is_process_trusted():
            answer = asyncio.run(service.computer_dispatch("computer_snapshot", {"app": "Finder"}))
            assert answer["elements"] and answer["snapshot_id"]
        else:
            with pytest.raises(service.ComputerUseRefusal) as caught:
                asyncio.run(service.computer_dispatch("computer_snapshot", {"app": "Finder"}))
            assert caught.value.error.code == macos_driver.ERR_AX_PERMISSION
            assert "System Settings" in caught.value.error.fix
    finally:
        enable_state.reset_enable_state()
        service.reset_snapshots()


@_NEEDS_DARWIN
def test_list_apps_end_to_end_is_narrowed_to_the_allowlist(tmp_path, monkeypatch):
    """Real spawn, real driver, real narrowing: the driver reports every running app and the
    dispatch's step 7 hands the model only the allowlisted one, with an honest withheld count."""
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "enable.json"))
    (tmp_path / "enable.json").write_text(
        json.dumps({"version": 1, "enabled": True, "apps": ["Finder"]}), encoding="utf-8"
    )
    enable_state.reset_enable_state()
    service.reset_snapshots()
    try:
        answer = asyncio.run(service.computer_dispatch("computer_list_apps", {}))
        assert answer["apps"] == ["Finder"], answer
        assert answer["withheld"] > 0, "a real desktop runs more than one bundled application"
    finally:
        enable_state.reset_enable_state()
        service.reset_snapshots()
