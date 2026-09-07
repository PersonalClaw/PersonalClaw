"""`DCU-3`'s stale-index clause: the half a re-snapshot clears, and the harness that measures it.

`DCU-3`'s ``done_when`` ends *"a stale index (past TTL or changed fingerprint) refuses and
**forces a re-snapshot**"*. ``test_computer_use_dispatch.py`` already proves the **refuses**
half from both sides of both bounds. Nothing proved the **forces a re-snapshot** half: the
closest test moves the frozen clock *backwards*, which is not a re-snapshot, and the rest assert
only that the word ``computer_snapshot`` appears in the FIX line. A refusal whose remedy has
never been executed is a remedy nobody has checked — so the first section here runs it, for both
triggers, through :func:`~personalclaw.computer_use.service.computer_dispatch`.

The sharp part is not that the new id acts. It is that the **old id keeps refusing** while the
new one acts. "Forces a re-snapshot" is a claim about the store not silently healing an index
the operator was told to abandon, and a test that only checked the new id would pass against a
dispatch that had quietly started accepting the old one again.

The second section is about ``scripts/dcu3_stale_index_validate.py``, the live harness. That
script cannot run in CI — it needs a real desktop and the macOS Accessibility (TCC) grant — so
the two things about it that can rot silently are pinned here instead:

* **Its discriminators are the production strings.** ``service._stale`` raises ONE code,
  ``ERR_COMPUTER_USE_STALE_INDEX``, for three different causes (unknown/evicted id, past TTL,
  changed fingerprint). Two of those three are vacuous for a staleness measurement — a typo'd
  snapshot id refuses with the same code as the thing under test. The harness therefore matches
  on the message detail, and :func:`test_the_three_stale_index_details_stay_distinguishable`
  drives all three refusals for real and asserts each names its own detail and neither of the
  others. Reword one of them in ``service`` and this reds, instead of the harness quietly losing
  the ability to say which staleness it measured.
* **Its guards actually guard.** The harness's screens are asserted to REJECT the wrong cause
  and the wrong driver-spawn count, and its preflight is asserted to report ``unproven`` rather
  than skip when the grant is absent. A harness whose failure paths have never fired is a
  harness that reports green for the wrong reasons.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import pathlib
import sys

import pytest

from personalclaw.computer_use import enable_state, service

ARMED_APP = "TextEdit"
TEXT_AREA = {"index": 0, "role": "AXTextArea", "value": "", "title": "scratch"}


def _harness():
    """Load the live harness by path. It is a script, not a package module, by design.

    Loaded in isolation (``spec_from_file_location``) rather than added to ``sys.path``: the
    script is an operator entry point and importing it must not depend on repo layout.
    """
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "dcu3_stale_index_validate.py"
    spec = importlib.util.spec_from_file_location("_dcu3_stale_index_harness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Real keystone document at ``tmp_path``, empty snapshot store, no developer home read."""
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "enable.json"))
    (tmp_path / "enable.json").write_text(
        json.dumps({"version": 1, "enabled": True, "apps": [ARMED_APP]}), encoding="utf-8"
    )
    enable_state.reset_enable_state()
    service.reset_snapshots()
    yield
    enable_state.reset_enable_state()
    service.reset_snapshots()


class _Desktop:
    """A driver double whose window can CHANGE between calls — the point of these tests.

    ``test_computer_use_dispatch.py``'s fake is fixed at construction, which is right for its
    bounds tests and useless here: both triggers are about a tree that differs between the
    snapshot and the act, so the fingerprint has to be mutable while the dispatch runs.
    """

    def __init__(self) -> None:
        self.fingerprint = "fp-window-as-walked"
        self.ops: list[str] = []
        self.values: list[str] = []

    def install(self, monkeypatch) -> None:
        async def run(op, payload, *, tool):
            self.ops.append(op)
            if op == "snapshot":
                return {"fingerprint": self.fingerprint, "elements": [dict(TEXT_AREA)]}
            if op == "set_value":
                self.values.append(str(payload.get("value", "")))
            return {"ok": True, "op": op}

        monkeypatch.setattr(service, "_run_driver", run)


def _run(coro):
    return asyncio.run(coro)


def _snapshot_through_dispatch() -> dict:
    return _run(service.computer_dispatch("computer_snapshot", {"app": ARMED_APP}))


def _act(snapshot_id: str, value: str):
    return _run(
        service.computer_dispatch(
            "computer_set_value",
            {"snapshot_id": snapshot_id, "element_index": 0, "value": value},
        )
    )


def _refusal(snapshot_id: str) -> service.ComputerUseRefusal:
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _act(snapshot_id, "must not reach the window")
    return excinfo.value


# ── 1. the clause's second half: a re-snapshot clears it, the old id does not ──


def test_a_past_ttl_refusal_is_cleared_by_a_re_snapshot(monkeypatch):
    """Past TTL refuses; a NEW snapshot acts; the expired id still refuses.

    The third assertion is the one that matters. Without it this test passes against a store
    that started honouring the expired id again, which is the opposite of "forces a re-snapshot".
    """
    desktop = _Desktop()
    desktop.install(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(service, "_now", lambda: clock[0])

    stale = _snapshot_through_dispatch()
    clock[0] += service.SNAPSHOT_TTL_SECS + 0.001
    first = _refusal(str(stale["snapshot_id"]))
    assert first.error.code == service.ERR_STALE_INDEX
    assert "computer_snapshot" in first.error.fix

    fresh = _snapshot_through_dispatch()
    assert fresh["snapshot_id"] != stale["snapshot_id"]
    assert _act(str(fresh["snapshot_id"]), "written after the re-snapshot").get("ok") is True
    assert desktop.values == ["written after the re-snapshot"]

    again = _refusal(str(stale["snapshot_id"]))
    assert again.error.code == service.ERR_STALE_INDEX
    assert desktop.values == ["written after the re-snapshot"], "the expired id reached the driver"


def test_a_changed_fingerprint_refusal_is_cleared_by_a_re_snapshot(monkeypatch):
    """A window that changed under the index refuses INSIDE the TTL, and re-snapshotting clears it.

    No clock movement anywhere: this trigger must fire on its own, or the TTL is doing the work
    and the fingerprint check is untested.
    """
    desktop = _Desktop()
    desktop.install(monkeypatch)

    stale = _snapshot_through_dispatch()
    desktop.fingerprint = "fp-window-after-the-user-moved-it"
    refusal = _refusal(str(stale["snapshot_id"]))
    assert refusal.error.code == service.ERR_STALE_INDEX
    assert "changed" in refusal.error.what
    assert desktop.values == []

    fresh = _snapshot_through_dispatch()
    assert fresh["fingerprint"] == "fp-window-after-the-user-moved-it"
    assert _act(str(fresh["snapshot_id"]), "written against the new tree").get("ok") is True
    assert desktop.values == ["written against the new tree"]

    assert _refusal(str(stale["snapshot_id"])).error.code == service.ERR_STALE_INDEX
    assert desktop.values == ["written against the new tree"], "the stale id reached the driver"


def test_a_re_snapshot_does_not_resurrect_an_evicted_id(monkeypatch):
    """The third cause, for completeness: taking more snapshots is what EVICTED the old id, so a
    re-snapshot cannot be the remedy for it. Recorded so the two remedies are not conflated."""
    desktop = _Desktop()
    desktop.install(monkeypatch)
    first = _snapshot_through_dispatch()
    for _ in range(service.MAX_LIVE_SNAPSHOTS):
        _snapshot_through_dispatch()
    refusal = _refusal(str(first["snapshot_id"]))
    assert refusal.error.code == service.ERR_STALE_INDEX
    assert "no such snapshot is live" in refusal.error.what


# ── 2. the live harness: its discriminators and its guards ────────────────────


def test_the_three_stale_index_details_stay_distinguishable(monkeypatch):
    """One code, three causes — each refusal must name its own and neither of the other two.

    This is the assertion the live harness leans on. ``ERR_COMPUTER_USE_STALE_INDEX`` alone
    cannot tell "the window changed" from "you passed a snapshot id that was never live", and
    only one of those is what `DCU-3`'s clause is about.
    """
    harness = _harness()
    details = (harness.DETAIL_UNKNOWN_ID, harness.DETAIL_TTL, harness.DETAIL_FINGERPRINT)
    assert len(set(details)) == 3, details

    desktop = _Desktop()
    desktop.install(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(service, "_now", lambda: clock[0])

    observed: dict[str, str] = {}

    observed[harness.DETAIL_UNKNOWN_ID] = _refusal("an-id-that-was-never-live").error.render()

    past_ttl = _snapshot_through_dispatch()
    clock[0] += service.SNAPSHOT_TTL_SECS + 0.001
    observed[harness.DETAIL_TTL] = _refusal(str(past_ttl["snapshot_id"])).error.render()

    clock[0] += 1.0
    changed = _snapshot_through_dispatch()
    desktop.fingerprint = "fp-changed"
    observed[harness.DETAIL_FINGERPRINT] = _refusal(str(changed["snapshot_id"])).error.render()

    for expected, rendered in observed.items():
        assert expected in rendered, f"{expected!r} missing from {rendered!r}"
        for other in details:
            if other != expected:
                assert other not in rendered, f"{rendered!r} also names {other!r}"


def test_the_harness_rejects_a_stale_refusal_with_the_wrong_cause(monkeypatch):
    """The harness's vacuous-cause screen must REJECT, or it is decoration.

    A snapshot id that was never live refuses with the code the harness is looking for. If the
    harness accepted that while claiming to have measured a past-TTL refusal, it would report
    green for a typo.
    """
    harness = _harness()
    desktop = _Desktop()
    desktop.install(monkeypatch)
    harness._ATTEMPTS.clear()
    harness._STALE_OBSERVED.clear()

    with pytest.raises(harness.Failure) as excinfo:
        harness._expect_stale(
            {"snapshot_id": "an-id-that-was-never-live", "elements": [dict(TEXT_AREA)]},
            marker="must not reach the window",
            expect_detail=harness.DETAIL_TTL,
            forbid_details=(harness.DETAIL_FINGERPRINT, harness.DETAIL_UNKNOWN_ID),
            expect_driver_ops=[],
            clause="past-ttl",
        )
    assert "expected cause" in excinfo.value.detail
    assert harness._STALE_OBSERVED == [], "a rejected leg must not be counted as observed"


def test_the_harness_rejects_a_refusal_that_reached_the_driver_when_it_should_not(monkeypatch):
    """The driver-spawn count is the harness's second discriminator, so it must red on mismatch.

    A past-TTL refusal is decided before any window is walked. One that spawned a driver was
    decided by something else wearing the same code, and the harness has to notice.
    """
    harness = _harness()
    desktop = _Desktop()
    desktop.install(monkeypatch)
    harness._install_driver_counter()
    harness._ATTEMPTS.clear()
    harness._STALE_OBSERVED.clear()
    harness._DRIVER_OPS.clear()

    stale = _snapshot_through_dispatch()
    desktop.fingerprint = "fp-changed"  # a FINGERPRINT refusal: it does re-walk
    with pytest.raises(harness.Failure) as excinfo:
        harness._expect_stale(
            {"snapshot_id": str(stale["snapshot_id"]), "elements": [dict(TEXT_AREA)]},
            marker="must not reach the window",
            expect_detail=harness.DETAIL_FINGERPRINT,
            forbid_details=(harness.DETAIL_TTL, harness.DETAIL_UNKNOWN_ID),
            expect_driver_ops=[],  # wrong on purpose: this cause DOES re-walk
            clause="changed-fingerprint",
        )
    assert "the driver" in excinfo.value.detail


def test_the_harness_reports_unproven_rather_than_skipping_without_the_grant(monkeypatch):
    """No grant means ``unproven`` with the fix, never a pass and never a silent skip.

    The failure mode this atom already suffered once: its recorded reason declared the grant
    ungrantable when nobody had asked the OS. The symmetric error is to assume a grant that once
    answered True still does — macOS resolves the request against the RESPONSIBLE process, so it
    is a property of the session, not of the workstation. Either way the answer must never be
    green by omission.

    ``platform.system`` is forced to Darwin so this reaches the grant branch on a Linux runner.
    Without that the test passed on CI for the wrong reason: preflight refused on the PLATFORM
    check first, and the assertion about the Accessibility fix never applied. Which is the same
    shape as everything else in this file — an assertion that never ran reads exactly like one
    that held.
    """
    harness = _harness()
    from personalclaw.computer_use import macos_ffi

    monkeypatch.setattr(harness.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos_ffi, "is_process_trusted", lambda: False)
    with pytest.raises(harness.Failure) as excinfo:
        harness._preflight()
    detail = excinfo.value.detail
    assert excinfo.value.clause == "preflight"
    assert "Privacy & Security > Accessibility" in detail
    assert "RESPONSIBLE" in detail, "the fix must name the identity macOS actually evaluates"
    assert "AXIsProcessTrusted" in detail


def test_the_harness_refuses_on_a_non_macos_host_too(monkeypatch):
    """The other preflight leg, and the one every CI runner actually takes.

    A harness that measured a macOS-only capability and returned quietly on Linux would report
    the absence of a driver as the absence of a problem.
    """
    harness = _harness()

    monkeypatch.setattr(harness.platform, "system", lambda: "Linux")
    with pytest.raises(harness.Failure) as excinfo:
        harness._preflight()
    assert excinfo.value.clause == "preflight"
    assert "Linux" in excinfo.value.detail


def _dotted(node: ast.AST) -> str:
    """``a.b.c`` for a call target, or ``""`` for anything not made of plain attribute access."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def test_the_harness_drives_only_the_dispatch():
    """The harness must not reach around ``computer_dispatch`` onto the driver or the FFI.

    Asserted over the SOURCE's call graph rather than from a run: a bypass added on a branch no
    run happens to take is still a bypass, and this script's whole standing rests on measuring
    the chain rather than the driver. ``list_gui_apps`` is the one allowed FFI call — it is the
    pre-launch app census that teardown needs in order to quit only what the run started, and it
    drives nothing. Matched on calls, not on text, so the module's prose may name a function it
    must not call.
    """
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "dcu3_stale_index_validate.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {_dotted(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    called.discard("")

    # Exactly two FFI calls are allowed, and both are READS that drive nothing: the grant probe
    # (which must run before anything is launched) and the pre-launch app census (which is what
    # lets teardown quit only what the run started). An equality assertion, so a THIRD FFI call
    # reds even if it looks harmless — the moment the harness walks a window itself it is
    # measuring the driver instead of the chain.
    assert {name for name in called if name.startswith("macos_ffi.")} == {
        "macos_ffi.is_process_trusted",
        "macos_ffi.list_gui_apps",
    }, sorted(name for name in called if name.startswith("macos_ffi."))
    for banned in ("macos_driver", "driver_host", "ffi"):
        offenders = sorted(name for name in called if name.split(".")[0] == banned)
        assert offenders == [], f"the harness reaches around the dispatch: {offenders}"
    assert "service.computer_dispatch" in called

    literals = {
        node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value
    }
    assert "osascript" not in literals, "osascript needs the Apple Events grant; it blocks"
