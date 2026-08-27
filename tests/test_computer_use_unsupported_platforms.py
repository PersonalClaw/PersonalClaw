"""Rails for `DCU-6` — the honest Windows/Linux platform story.

The atom's clause is *"on non-macOS, every computer-use tool returns a typed refusal naming the
platform; no silent no-op"*, and the thing that makes it hard to prove is that it is a statement
about platforms this suite does not run on. So the shape of this file is deliberate:

* **What is REAL here (macOS, no simulation).** That ``DRIVER_MODULES``'s Windows and Linux
  entries now resolve to importable modules — before this atom they named nothing and
  ``resolve_driver`` answered ``None`` — and that importing either on macOS touches no OS
  library. Both are real properties of a real macOS run and both are the reason the rest can be
  measured at all: a driver that raised at import would take the *gateway* down, not the tool
  (``resolve_driver`` runs inside the gateway's own process).
* **What is SIMULATED, and how narrowly.** Only ``platform.system()``. Every other participant is
  the shipped one: the real ``computer_dispatch``, the real keystone document, the real
  ``policy``/``gate`` screens, the real SEL, the real ``create_subprocess_limited`` ceilinged
  spawn, a real separate child process running the real ``driver_host.main`` over the real stdio
  JSON protocol, and the real ``_run_driver`` translation that decides whether a child's code
  survives. The end-to-end leg fakes ``platform.system`` *inside the child* (via the argv the
  dispatch spawns) rather than in this process, because a parent-side monkeypatch cannot reach
  across ``fork``/``exec`` — patching it here and asserting a subprocess result would be a test
  that measures nothing.
* **Every guard has a vacuity assertion.** A refusal that fires everywhere is not a platform
  refusal, it is a broken driver: each Windows/Linux assertion below has a Darwin twin through
  the same code path confirming the refusal does *not* fire there. Those twins **simulate**
  Darwin rather than requiring it, because this suite runs on a macOS developer machine *and* on
  a Linux CI runner — and CI is the only place the Windows/Linux rows are enforced without
  someone watching, so a floor that skipped there would read as a pass while proving nothing.
  The one leg that genuinely cannot be faked — a real macOS ``run_op`` — is host-gated and says
  so.

**The child is pinned to THIS tree.** ``sys.path`` is seeded with this repository's ``src`` in
the spawned child rather than trusting the ambient install. A venv whose editable install points
at a different checkout would otherwise silently run *that* tree's ``driver_host`` — which has no
Windows driver — and the test would fail (or pass) for a reason that has nothing to do with the
code under test. It is the same hazard
``test_the_driver_argv_names_the_child_module_and_this_interpreter`` exists to name.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import platform
import sys

import pytest

from personalclaw.computer_use import driver_host, enable_state, linux_driver, macos_driver, service
from personalclaw.computer_use import tools as ct
from personalclaw.computer_use import unsupported_platform, windows_driver
from personalclaw.errors import ERROR_CODES
from personalclaw.manifest_meta import TOOL_META

ARMED_APP = "TextEdit"
CODE = unsupported_platform.ERR_PLATFORM_UNSUPPORTED

#: The repository's ``src``, seeded into the spawned child so it runs this tree.
SRC = str(pathlib.Path(__file__).resolve().parents[1] / "src")

#: ``(module, platform.system() value, the API its refusal must name)``.
PENDING = (
    (windows_driver, "Windows", "UI Automation"),
    (linux_driver, "Linux", "AT-SPI"),
)

#: The seven driver operations, derived from the tool surface rather than tabulated — a second
#: list is a second place to forget one, and a forgotten op would read here as "covered".
OPS = tuple(service._driver_op(spec) for spec in ct.TOOL_SURFACE)

#: For the one assertion that cannot be simulated: what a *real* macOS ``run_op`` answers. Every
#: other Darwin twin in this file fakes ``platform.system()`` and therefore runs everywhere.
_NEEDS_DARWIN = pytest.mark.skipif(
    platform.system() != "Darwin", reason="an unsimulated macOS answer needs a macOS host"
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Redirect the keystone to ``tmp_path`` and clear the snapshot store, every test.

    Unconditional, like the dispatch suite's: a test that resolved the real keystone would read
    the developer's own arming state, and the refusal messages quote the resolved path.
    """
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "enable.json"))
    enable_state.reset_enable_state()
    service.reset_snapshots()
    yield
    enable_state.reset_enable_state()
    service.reset_snapshots()


def _arm(tmp_path, *apps: str) -> None:
    """Write a real enable document and force a re-read — the real parser, not a patched
    accessor, so the chain is proven to read the operator's actual document."""
    (tmp_path / "enable.json").write_text(
        json.dumps({"version": 1, "enabled": True, "apps": list(apps)}), encoding="utf-8"
    )
    enable_state.reset_enable_state()


def _child_argv(system: str) -> list[str]:
    """An argv that runs the REAL ``driver_host.main`` in a real child, with only
    ``platform.system()`` faked — the one thing a macOS host cannot supply honestly."""
    return [
        sys.executable,
        "-c",
        f"import sys; sys.path.insert(0, {SRC!r});"
        f"import platform; platform.system = lambda: {system!r};"
        "from personalclaw.computer_use.driver_host import main;"
        "raise SystemExit(main())",
    ]


def _run(coro):
    return asyncio.run(coro)


def _dispatch_error(tool: str, args: dict, *, system: str, tmp_path):
    """Drive the whole chain for *tool* against a child that believes it is on *system*.

    Returns the ``AgentError`` the dispatch produced, or ``None`` when the call succeeded.
    """
    return _dispatch(tool, args, system=system, tmp_path=tmp_path)[0]


def _dispatch(tool: str, args: dict, *, system: str, tmp_path):
    """``(error | None, result | None)`` from one real end-to-end dispatch."""
    _arm(tmp_path, ARMED_APP)
    try:
        return None, _run(service.computer_dispatch(tool, args))
    except (service.ComputerUseRefusal, enable_state.ComputerUseDisabled) as exc:
        return exc.error, None


def _args_for(spec, snapshot_id: str) -> dict:
    """The minimum a tool needs to reach the driver, so the refusal under test is the driver's
    and not an argument complaint standing in for it."""
    if spec.name == "computer_list_apps":
        return {}
    if spec.name == "computer_snapshot":
        return {"app": ARMED_APP}
    extra = {
        "computer_type": {"text": "Lunch on Tuesday"},
        "computer_set_value": {"value": "Lunch on Tuesday"},
        "computer_scroll": {"direction": "down"},
        "computer_perform_action": {"action": "AXPress"},
    }
    return {"snapshot_id": snapshot_id, "element_index": 0, **extra.get(spec.name, {})}


# ── 1. resolution: the mapping is now a fact (REAL — no simulation) ───────────


@pytest.mark.parametrize(("module", "system", "_api"), PENDING)
def test_the_platform_driver_now_resolves(module, system, _api):
    """The gap this atom closes, stated as the assertion that would have failed before it.

    ``DRIVER_MODULES`` has named ``windows_driver``/``linux_driver`` since `DCU-4` while neither
    module existed, so ``resolve_driver`` returned ``None`` on both platforms and their answer
    came from the *no driver at all* fallback. This is a REAL macOS assertion — resolution is by
    import and these modules import anywhere.
    """
    assert driver_host.resolve_driver(system) is module
    assert driver_host.DRIVER_MODULES[system] == module.__name__


def test_darwin_still_resolves_its_own_driver():
    """Vacuity floor for the row above: a change that made every platform resolve to the same
    module would satisfy it. macOS must still get the macOS driver."""
    assert driver_host.resolve_driver("Darwin") is macos_driver


def test_an_unmapped_platform_still_answers_none():
    """The ``None`` branch is not dead code and must not be "tidied away": a platform outside the
    map genuinely has no driver, and that is a different sentence from "your platform's driver
    is not written yet" — which is exactly why the two carry different codes."""
    assert driver_host.resolve_driver("Plan9") is None
    assert "Plan9" not in driver_host.DRIVER_MODULES


# ── 2. import safety: a driver may never break the gateway (REAL) ─────────────


_ALLOWED_IMPORT_ROOTS = frozenset({"__future__", "typing", "personalclaw"})


def _offending_imports(source: str) -> list[str]:
    """Module-level imports outside the allowlist. AST, not text: a comment naming ``ctypes``
    is prose, and a text scan would count it."""
    offenders: list[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        offenders += [n for n in names if n.split(".")[0] not in _ALLOWED_IMPORT_ROOTS]
    return offenders


@pytest.mark.parametrize(("module", "_system", "_api"), PENDING)
def test_a_pending_driver_imports_no_os_library_at_module_level(module, _system, _api):
    """``resolve_driver`` imports this module inside the GATEWAY's process. A module-level
    ``import comtypes`` (or ``gi``) would therefore turn "this machine has no desktop
    capability" into "this machine has no gateway" — the failure mode `DCU-3` wrote
    ``test_importing_the_ffi_touches_no_framework`` about. This file having imported the module
    at the top on macOS is the other half of the proof."""
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert _offending_imports(source) == []
    assert "ctypes" not in source


def test_the_import_scanner_detects_an_os_library():
    """The vacuity proof for the rail above: a scanner that matched nothing would pass it
    silently. Run against the exact shape it exists to catch."""
    assert _offending_imports("import comtypes\nfrom gi.repository import Atspi\n") == [
        "comtypes",
        "gi.repository",
    ]


@pytest.mark.parametrize(("module", "_system", "_api"), PENDING)
def test_a_pending_driver_re_derives_no_wording(module, _system, _api):
    """One refusal, two modules. Neither platform module may build its own envelope or its own
    ``DriverError``: two copies of a WHAT/WHY/FIX drift the moment one is edited, which is the
    family the structural-duplication ratchet counts. The sentence lives in
    ``unsupported_platform`` and these modules supply only the two facts that differ."""
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    built = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "DriverError" not in built
    assert not [
        d
        for d in ast.walk(tree)
        if isinstance(d, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "error" for k in d.keys)
    ]


# ── 3. the child's answer for every operation (platform SIMULATED in-process) ─


@pytest.mark.parametrize(("module", "system", "api"), PENDING)
def test_every_operation_refuses_with_the_platform_code(module, system, api, monkeypatch):
    """The atom's clause at the driver boundary: all seven operations, the real
    ``driver_host.run_op``, one typed refusal each.

    SIMULATED: ``platform.system()`` only. Everything the child does with the answer — resolve
    the module, find ``op_<name>``, serialise the envelope — is the shipped path.
    """
    monkeypatch.setattr(platform, "system", lambda: system)
    for op in OPS:
        answer = driver_host.run_op({"op": op})
        error = answer.get("error")
        assert error, f"{system}/{op} answered without an error: {answer}"
        assert error["code"] == CODE, (system, op, error["code"])
        assert error["code"] in ERROR_CODES, error["code"]
        assert system in error["message"] and op in error["message"], error["message"]
        assert api in error["why"], error["why"]
        assert error["why"] and error["fix"]
    assert len(OPS) == 7, OPS


def _ops_answering(code: str) -> list[str]:
    """The operations whose real ``run_op`` answer carries *code*, named rather than counted so a
    failure says which one. Shared by the two Darwin twins below: one body, so the simulated and
    unsimulated legs cannot drift into asserting different things."""
    answers = {op: driver_host.run_op({"op": op}).get("error") or {} for op in OPS}
    return [op for op, error in answers.items() if error.get("code") == code]


def test_no_operation_refuses_this_way_on_darwin(monkeypatch):
    """**The vacuity assertion for the whole file.** Same ``run_op``, Darwin instead of the two
    pending platforms: not one operation may answer with the platform code. A refusal that fired
    here would mean the check is on the wrong thing entirely and the *working* platform had been
    broken by the change that made the broken ones honest.

    Darwin is simulated exactly as the Windows and Linux rows simulate theirs, so this floor runs
    on the Linux CI runner too — the host it must hold on, since that is where the row it
    falsifies is enforced. Nothing about the host can leak in: ``CODE`` is produced in one place,
    ``unsupported_platform.refusal``, which only ``windows_driver`` and ``linux_driver`` call, so
    resolving ``macos_driver`` at all is what makes the assertion true. On a Linux host that
    driver's OS call fails and ``run_op`` reports ``ERR_COMPUTER_USE_DRIVER_UNAVAILABLE`` — a
    different code, which is the point.
    """
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert _ops_answering(CODE) == []


@_NEEDS_DARWIN
def test_no_operation_refuses_this_way_on_an_unsimulated_macos_host():
    """The twin above with nothing faked at all, on the one host that can answer honestly.

    Host-gated rather than skipped-and-forgotten: what it adds over the simulated twin is that
    the *real* macOS driver, reaching the real AX API, still does not answer with the pending
    platform's code — and no amount of patching can supply that. The simulated twin is what holds
    the line on CI, so this skipping there costs the file nothing.
    """
    assert platform.system() == "Darwin"
    assert _ops_answering(CODE) == []


@pytest.mark.parametrize(("module", "system", "api"), PENDING)
def test_the_fix_is_actionable_rather_than_a_restatement(module, system, api, monkeypatch):
    """A FIX that repeats the error is the defect this atom exists to remove. The *old* answer
    on Windows was "Nothing to configure — the capability is armed but has no driver to run",
    which tells an operator neither what works nor what not to try. This one must name macOS,
    say plainly that no local configuration helps, and keep the internal module path out of a
    user-facing sentence."""
    monkeypatch.setattr(platform, "system", lambda: system)
    fix = driver_host.run_op({"op": "snapshot"})["error"]["fix"]
    assert "macOS" in fix
    assert "not a permission or a setting" in fix
    assert module.__name__ not in fix and "personalclaw." not in fix


def test_the_two_platforms_do_not_share_one_hardcoded_sentence(monkeypatch):
    """Parameterised, not copied: Windows must not report AT-SPI and Linux must not report UIA.
    A single hardcoded string would satisfy every per-platform assertion above individually."""
    answers = {}
    for module, system, _api in PENDING:
        monkeypatch.setattr(platform, "system", lambda system=system: system)
        answers[system] = driver_host.run_op({"op": "click"})["error"]
    assert answers["Windows"] != answers["Linux"]
    assert "AT-SPI" not in json.dumps(answers["Windows"])
    assert "UI Automation" not in json.dumps(answers["Linux"])


# ── 4. THE CALL SITE: computer_dispatch, end to end, real ceilinged spawn ─────


@pytest.mark.parametrize("spec", ct.TOOL_SURFACE, ids=lambda s: s.name)
def test_every_tool_refuses_through_the_real_dispatch_on_windows(spec, tmp_path, monkeypatch):
    """**The clause, at the call site a user reaches.** Not "a function exists that would return
    this code" — the real ``computer_dispatch``, the real keystone, the real screens, the real
    SEL row, the real ``create_subprocess_limited`` spawn, a real child process, and the real
    ``_run_driver`` translation.

    That last participant is why this test is the one that matters: ``_run_driver`` honours a
    child's code **only** if it is in ``_CHILD_CODES`` and otherwise rewrites it as
    ``ERR_COMPUTER_USE_DRIVER_FAILED``. `DCU-3` measured that flattening on
    ``ERR_COMPUTER_USE_AX_PERMISSION`` and it is the difference between an operator reading
    "run this on macOS" and reading "the driver failed".

    SIMULATED: ``platform.system()`` inside the child, and nothing else. Index-based tools
    refuse during step 3b's re-walk rather than at step 6 — the earliest point the chain needs a
    driver at all, which is the honest place for the refusal to arrive.
    """
    monkeypatch.setattr(service, "_driver_argv", lambda: _child_argv("Windows"))
    snapshot = service._remember(ARMED_APP, "fp-1", [{"role": "AXTextField", "label": "Subject"}])
    error = _dispatch_error(
        spec.name, _args_for(spec, snapshot.snapshot_id), system="Windows", tmp_path=tmp_path
    )
    assert error is not None, f"{spec.name} did not refuse on Windows"
    assert error.code == CODE, (spec.name, error.code, error.what)
    assert "Windows" in error.what
    assert "UI Automation" in error.why
    assert "macOS" in error.fix


def test_the_dispatch_refusal_is_not_flattened_to_a_generic_driver_failure(tmp_path, monkeypatch):
    """Stated as its own row so the reason the code is in ``_CHILD_CODES`` cannot be lost: drop
    it from that allowlist and the message an operator reads becomes "the driver failed"."""
    monkeypatch.setattr(service, "_driver_argv", lambda: _child_argv("Linux"))
    error = _dispatch_error(
        "computer_snapshot", {"app": ARMED_APP}, system="Linux", tmp_path=tmp_path
    )
    assert error is not None
    assert error.code == CODE
    assert error.code != service.ERR_DRIVER_FAILED
    assert CODE in service._CHILD_CODES


def test_the_same_spawn_on_darwin_does_not_produce_the_platform_refusal(tmp_path, monkeypatch):
    """**The end-to-end leg's vacuity assertion.** Identical machinery, identical argv shape,
    only the simulated platform changed — and the platform code must NOT appear. Without this,
    the test above would pass just as happily against a driver that refused everything
    everywhere, which is a broken macOS driver dressed as a Windows fix."""
    monkeypatch.setattr(service, "_driver_argv", lambda: _child_argv("Darwin"))
    error = _dispatch_error(
        "computer_snapshot", {"app": ARMED_APP}, system="Darwin", tmp_path=tmp_path
    )
    # macOS either succeeds or refuses for a macOS reason (an ungranted accessibility
    # permission, on a machine where TCC has not been ticked) — never for this one.
    assert error is None or error.code != CODE, error and (error.code, error.what)


def test_an_unarmed_machine_still_refuses_at_the_keystone_first(tmp_path, monkeypatch):
    """Order matters more than the new refusal does: the keystone is the FIRST check, so an
    unarmed Windows machine must hear "not armed", not "no driver". A platform refusal that
    front-ran the keystone would disclose that the capability is otherwise wired."""
    monkeypatch.setattr(service, "_driver_argv", lambda: _child_argv("Windows"))
    with pytest.raises(enable_state.ComputerUseDisabled) as caught:
        _run(service.computer_dispatch("computer_list_apps", {}))
    assert caught.value.error.code == enable_state.ERR_DISABLED


# ── 5. the code is a registered, declared, distinct surface ───────────────────


def test_the_code_is_registered_and_distinct():
    """A refusal carrying an unregistered code is an ad-hoc string with a colon in it. It must
    also stay distinct from the two refusals it would otherwise be confused with, because an
    operator acts on all three differently."""
    assert ERROR_CODES[CODE]
    assert CODE not in (service.ERR_DRIVER_UNAVAILABLE, service.ERR_DRIVER_FAILED)
    assert CODE.startswith("ERR_COMPUTER_USE_")


def test_every_tool_declares_the_platform_code():
    """``manifest_meta``'s comment promises the listed codes are "the ones a caller can actually
    receive, not a superset". On Windows and Linux this is the answer every one of the seven
    gives, so an undeclared code would make that comment false and leave a model to discover the
    platform story by calling a tool."""
    missing = [
        spec.name
        for spec in ct.TOOL_SURFACE
        if CODE not in TOOL_META.get(spec.name, {}).get("error_codes", [])
    ]
    assert not missing, missing
