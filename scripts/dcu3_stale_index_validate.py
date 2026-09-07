#!/usr/bin/env python3
"""DESKTOP-COMPUTER-USE: measure the STALE-INDEX half of `DCU-3`'s ``done_when``, live.

`DCU-3`'s clause has two halves::

    With the enable on, snapshotting a TextEdit window then AXPress-ing a button by index and
    typing into a field succeeds without the pointer moving; a stale index (past TTL or
    changed fingerprint) refuses and forces a re-snapshot.

PR #2552 measured the first half on a real desktop and said so explicitly: it *"exercised the
fresh path only"*. This script measures the second half — **both triggers, separately** — and
carries the fresh-path positive control beside them, because a harness that only ever sees
refusals has not distinguished *"staleness is caught"* from *"everything is refused"*.

**Every step goes through** :func:`personalclaw.computer_use.service.computer_dispatch`, never
the driver and never the FFI. Dispatch is the only entry point this plan shipped; reaching
around it would prove the driver works and say nothing about the chain meant to restrain it.
The one FFI call this script makes is :func:`macos_ffi.list_gui_apps`, and it is not a drive —
it is the teardown census, taken *before* anything is launched so cleanup can quit only what
this run started.

**The grant is probed FIRST and an ungranted machine reports ``unproven``, never green.** A
skip that reads as a pass is the failure mode this repo keeps rediscovering, and it has already
bitten this atom once: `DCU-3`'s recorded reason said the grant could not be had, when nobody
had asked the OS. The symmetric error is to assume it *can* be had because it once was — so
this script asks again, every run, and reports what the OS answered.

**Why the grant is not a property of the workstation.** macOS resolves an Accessibility request
against the *responsible* process, not the binary that called ``AXIsProcessTrusted()``. For an
interpreter started from a terminal or a host application, the responsible process is that
terminal or application — so the same machine, the same python and the same repo answer True in
one session and False in another. :func:`_preflight` therefore reports the mechanism and the
command that names the identity the operator must actually grant, instead of naming a binary
that may not be the one being asked about.

**Three refusals share one code, so the message is the discriminator.** ``_stale()`` in
``service`` raises ``ERR_COMPUTER_USE_STALE_INDEX`` for an unknown/evicted snapshot id, for a
past-TTL id, and for a changed fingerprint. A harness that asserted only the code would report
"stale index refused" for a typo'd snapshot id, which measures nothing. Each leg below asserts
the detail it expects and asserts the other two details are ABSENT, and
``test_the_three_stale_index_details_stay_distinguishable`` pins those strings so a refactor
that collapsed them reds the suite instead of quietly hollowing out this script.

**Driver spawns are counted, and the count is the second discriminator.** The past-TTL check
(step 3a) runs before any driver call; the fingerprint check (step 3b) runs *because* of one.
So a past-TTL refusal that reached the driver, or a fingerprint refusal that did not, is a
different bug wearing the same code. The counter wraps ``service._run_driver`` and calls
through — remove it and every leg behaves identically.

Usage::

    PYTHONPATH=src python scripts/dcu3_stale_index_validate.py

Exit status 0 only when every clause holds. Anything unproven is reported as ``unproven`` with
the reason, never silently dropped. The run sleeps once for the snapshot TTL (read from
``service.SNAPSHOT_TTL_SECS``, not hardcoded), so it takes about a minute.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

#: The three ``service._stale`` details, as the substrings that tell them apart. Asserted
#: present for the leg that means them and ABSENT for the other two — one shared code with
#: three causes is otherwise indistinguishable, and two of the three causes are vacuous here.
DETAIL_TTL = "old and indices expire after"
DETAIL_FINGERPRINT = "the window has changed since it was walked"
DETAIL_UNKNOWN_ID = "no such snapshot is live in this gateway"

#: Prose markers, deliberately not credential- or secret-shaped: step 7 passes every driver
#: string through ``redact_credentials``, and a marker that got redacted would fail the
#: read-back for a reason that has nothing to do with staleness.
MARKER_CONTROL = "DCU-3 stale-index run wrote this on the fresh path"
MARKER_TTL_RECOVERY = "DCU-3 stale-index run wrote this after the past-TTL re-snapshot"
MARKER_FP_RECOVERY = "DCU-3 stale-index run wrote this after the fingerprint re-snapshot"
MARKER_NEVER = "DCU-3 expects this string never to reach a window"

APP = "TextEdit"


class Failure(Exception):
    """A clause did not hold. Carries the clause name so the report names it."""

    def __init__(self, clause: str, detail: str) -> None:
        super().__init__(f"{clause}: {detail}")
        self.clause = clause
        self.detail = detail


#: Every dispatch this run made. "One SEL row per attempt" is checked against this, not against
#: a number written down once, so a mismatch in EITHER direction reds.
_ATTEMPTS: list[str] = []

#: Every stale-index refusal this run actually observed, in order. The SEL stale-row count is
#: checked against this for the same reason: a hardcoded 2 would go on passing if a leg silently
#: stopped refusing, or if a third refusal appeared that nobody meant.
_STALE_OBSERVED: list[str] = []

#: Driver operations spawned since the last :func:`_driver_ops_since` call.
_DRIVER_OPS: list[str] = []


def _install_driver_counter() -> None:
    """Count driver spawns, observationally. Wraps ``_run_driver``; never replaces it.

    The wrapper awaits the real function and returns its result unchanged, so every refusal,
    every timeout and every ceiling still comes from the production path. It exists because the
    two triggers differ in whether the driver is reached at all, and that difference is the
    sharpest available proof of WHICH check fired.
    """
    from personalclaw.computer_use import service

    real = service._run_driver

    async def counting(op: str, payload: dict[str, Any], *, tool: str) -> dict[str, Any]:
        _DRIVER_OPS.append(op)
        return await real(op, payload, tool=tool)

    service._run_driver = counting  # type: ignore[assignment]


def _driver_ops_since() -> list[str]:
    """The driver operations spawned since the previous call, and reset the window."""
    ops = list(_DRIVER_OPS)
    _DRIVER_OPS.clear()
    return ops


def _dispatch(tool: str, params: dict[str, Any]) -> tuple[str, Any]:
    """Run one dispatch. Returns ``("ok", result)`` or ``("refused", AgentError)``."""
    from personalclaw.computer_use import enable_state
    from personalclaw.computer_use import policy as cu_policy
    from personalclaw.computer_use import service

    _ATTEMPTS.append(tool)
    try:
        result = asyncio.run(
            service.computer_dispatch(
                tool, params, source="dcu3_stale_index_validate", caller_identity=""
            )
        )
    except (
        enable_state.ComputerUseDisabled,
        cu_policy.ComputerUsePolicyRefusal,
        service.ComputerUseRefusal,
    ) as exc:
        return "refused", exc.error
    return "ok", result


def _rendered(error: Any) -> str:
    """WHAT + WHY + FIX as one string, which is what a model actually reads."""
    return " ".join(str(getattr(error, field, "")) for field in ("what", "why", "fix"))


def _write_enable(home: Path, apps: list[str]) -> None:
    from personalclaw.computer_use.enable_state import ENABLE_FILENAME, GOVERNANCE_DIRNAME

    governance = home / GOVERNANCE_DIRNAME
    governance.mkdir(parents=True, exist_ok=True)
    (governance / ENABLE_FILENAME).write_text(
        json.dumps({"version": 1, "enabled": True, "apps": sorted(apps)}), encoding="utf-8"
    )


def _sel_rows(home: Path) -> list[dict[str, Any]]:
    path = home / "security_events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _snapshot(clause: str) -> dict[str, Any]:
    """One ``computer_snapshot`` through the dispatch, or a :class:`Failure` naming the clause."""
    outcome, payload = _dispatch("computer_snapshot", {"app": APP})
    if outcome != "ok":
        raise Failure(clause, f"computer_snapshot refused: {getattr(payload, 'code', payload)!r}")
    return payload


def _element_index(snapshot: dict[str, Any], role: str, clause: str) -> int:
    for element in list(snapshot.get("elements") or []):
        if isinstance(element, dict) and str(element.get("role", "")) == role:
            return int(element["index"])
    raise Failure(clause, f"no {role} in the {len(snapshot.get('elements') or [])} walked elements")


def _element_value(snapshot: dict[str, Any], role: str) -> str:
    for element in list(snapshot.get("elements") or []):
        if isinstance(element, dict) and str(element.get("role", "")) == role:
            return str(element.get("value", ""))
    return ""


def _window_title(snapshot: dict[str, Any]) -> str:
    """The walked window's own title. ``walk_window`` is window-first, so it is element 0."""
    elements = list(snapshot.get("elements") or [])
    return str(elements[0].get("title", "")) if elements and isinstance(elements[0], dict) else ""


def _write_by_index(snapshot: dict[str, Any], marker: str, clause: str) -> dict[str, Any]:
    """Set the text area's value BY ELEMENT INDEX, then read it back through the dispatch.

    The read-back is a second ``computer_snapshot`` rather than a direct FFI walk, so the value
    this script compares is the one that came through step 7's redaction — the same string a
    model would have been handed.
    """
    index = _element_index(snapshot, "AXTextArea", clause)
    outcome, payload = _dispatch(
        "computer_set_value",
        {"snapshot_id": str(snapshot.get("snapshot_id")), "element_index": index, "value": marker},
    )
    if outcome != "ok":
        raise Failure(clause, f"set_value by index refused: {getattr(payload, 'code', payload)!r}")
    ops = _driver_ops_since()
    if ops != ["snapshot", "set_value"]:
        raise Failure(
            clause,
            f"an acting dispatch should re-walk then act; the driver ran {ops!r}",
        )
    readback = _snapshot(clause)
    observed = _element_value(readback, "AXTextArea").strip()
    if observed != marker:
        raise Failure(clause, f"the text area holds {observed!r}, not the marker this run wrote")
    return {
        "element_index": index,
        "window": _window_title(snapshot),
        "driver_ops": ops,
        "value_read_back": observed,
    }


def _expect_stale(
    snapshot: dict[str, Any],
    *,
    marker: str,
    expect_detail: str,
    forbid_details: tuple[str, ...],
    expect_driver_ops: list[str],
    clause: str,
) -> dict[str, Any]:
    """Act on *snapshot* by index and require a stale-index refusal with the named cause."""
    index = _element_index(snapshot, "AXTextArea", clause)
    _driver_ops_since()
    outcome, payload = _dispatch(
        "computer_set_value",
        {"snapshot_id": str(snapshot.get("snapshot_id")), "element_index": index, "value": marker},
    )
    ops = _driver_ops_since()
    if outcome != "refused":
        raise Failure(clause, f"acting on a stale index returned a result: {payload!r}")
    code = str(getattr(payload, "code", ""))
    if code != "ERR_COMPUTER_USE_STALE_INDEX":
        raise Failure(clause, f"refused with {code!r}, not the stale-index code")
    rendered = _rendered(payload)
    if expect_detail not in rendered:
        raise Failure(clause, f"the refusal does not name the expected cause: {rendered!r}")
    for forbidden in forbid_details:
        if forbidden in rendered:
            raise Failure(
                clause,
                f"the refusal names {forbidden!r} too, so the cause is not the one under test",
            )
    if ops != expect_driver_ops:
        raise Failure(
            clause,
            f"expected the driver to run {expect_driver_ops!r} for this cause, it ran {ops!r}",
        )
    _STALE_OBSERVED.append(clause)
    return {
        "code": code,
        "what": str(getattr(payload, "what", "")),
        "fix": str(getattr(payload, "fix", "")),
        "driver_ops": ops,
    }


def _front_window_walks() -> bool:
    """Can the dispatch walk a front window for ``APP`` yet? Used only to wait out a launch."""
    outcome, _payload = _dispatch("computer_snapshot", {"app": APP})
    return outcome == "ok"


def phase_stale(home: Path) -> dict[str, Any]:  # noqa: C901 - one linear transcript, read top down
    """Both stale-index triggers, separately, plus the fresh-path positive control."""
    from personalclaw.computer_use import macos_ffi, service

    _install_driver_counter()
    _write_enable(home, [APP])

    doc_a = Path(tempfile.gettempdir()) / "dcu3-stale-index-A.txt"
    doc_b = Path(tempfile.gettempdir()) / "dcu3-stale-index-B.txt"
    doc_a.write_text("dcu3 stale-index scratch document A\n", encoding="utf-8")
    doc_b.write_text("dcu3 stale-index scratch document B\n", encoding="utf-8")

    # Captured BEFORE the launch, because teardown may only quit what this run started —
    # quitting an app the operator already had open would destroy their unsaved work.
    already = set(macos_ffi.list_gui_apps())
    launched = {APP} - already

    # ``-F`` (fresh) matters: without it macOS restores the app's previously open windows and
    # the front window is some earlier document, so "which window was walked" stops being
    # observable. #2552 lost a run to exactly this.
    subprocess.run(["open", "-F", "-a", APP, str(doc_a)], check=True)
    for _ in range(30):
        if _front_window_walks():
            break
        time.sleep(0.5)
    else:
        raise Failure("live-target", f"{APP} never exposed a walkable front window for document A")

    report: dict[str, Any] = {
        "launched_by_this_run": sorted(launched),
        "ttl_secs": service.SNAPSHOT_TTL_SECS,
    }

    # --- POSITIVE CONTROL: a fresh, unchanged snapshot still acts ----------------------------
    # First, and load-bearing. It rules out every vacuous reason the later refusals could fire:
    # the keystone is on, TextEdit is allowlisted, the AXTextArea exists at the index used, the
    # index is in range, and the OS grants this process accessibility. A run that only produced
    # refusals could not tell any of that apart from staleness.
    control_snap = _snapshot("fresh-path-positive-control")
    report["fresh_path_positive_control"] = _write_by_index(
        control_snap, MARKER_CONTROL, "fresh-path-positive-control"
    )
    report["fresh_path_positive_control"]["fingerprint"] = str(control_snap.get("fingerprint", ""))

    # --- TRIGGER 1: past TTL -----------------------------------------------------------------
    # Document B is opened for this leg and never written to. The positive control edited
    # document A, and an edited TextEdit document can change its own window title ("— Edited")
    # while the run sleeps — which would make "nothing changed but the clock" false for a reason
    # that has nothing to do with the TTL.
    subprocess.run(["open", "-F", "-a", APP, str(doc_b)], check=True)
    for _ in range(30):
        if _front_window_walks():
            break
        time.sleep(0.5)
    else:
        raise Failure("past-ttl", f"{APP} never exposed a walkable front window for document B")

    ttl_snap = _snapshot("past-ttl")
    ttl_fingerprint = str(ttl_snap.get("fingerprint", ""))
    ttl_window = _window_title(ttl_snap)
    slept = service.SNAPSHOT_TTL_SECS + 1.5
    time.sleep(slept)  # real time, not a patched clock: the clause is about a live desktop
    ttl_refusal = _expect_stale(
        ttl_snap,
        marker=MARKER_NEVER,
        expect_detail=DETAIL_TTL,
        forbid_details=(DETAIL_FINGERPRINT, DETAIL_UNKNOWN_ID),
        expect_driver_ops=[],  # step 3a refuses before any window is walked
        clause="past-ttl",
    )

    # The window did not change; the only thing that changed was age. Without this the leg
    # cannot tell a TTL refusal from a fingerprint refusal that happened to be worded oddly —
    # and the zero driver ops above only prove no re-walk happened, not that none was warranted.
    ttl_after = _snapshot("past-ttl")
    if str(ttl_after.get("fingerprint", "")) != ttl_fingerprint:
        raise Failure(
            "past-ttl",
            "the window changed during the sleep, so age was not the only difference "
            f"({ttl_fingerprint!r} -> {ttl_after.get('fingerprint')!r})",
        )
    if _window_title(ttl_after) != ttl_window:
        raise Failure("past-ttl", "the front window changed during the sleep")

    # ... and the re-snapshot is what clears it. This is the "forces a re-snapshot" half.
    report["past_ttl"] = {
        "window": ttl_window,
        "fingerprint_before": ttl_fingerprint,
        "fingerprint_after_sleep": str(ttl_after.get("fingerprint", "")),
        "slept_secs": round(slept, 1),
        "refusal": ttl_refusal,
        "recovered_by_re_snapshot": _write_by_index(
            ttl_after, MARKER_TTL_RECOVERY, "past-ttl-recovery"
        ),
    }

    # --- TRIGGER 2: changed fingerprint, INSIDE the TTL --------------------------------------
    # Bringing document A forward makes the app's focused window a different document, so the
    # walked tree has a different window title and different geometry. ``open`` goes through
    # LaunchServices, NOT osascript: ``tell application`` needs the Apple Events grant, a
    # DIFFERENT TCC grant from Accessibility, and on a host without it osascript blocks on a
    # prompt nobody answers.
    fp_snap = _snapshot("changed-fingerprint")
    fp_fingerprint = str(fp_snap.get("fingerprint", ""))
    fp_window = _window_title(fp_snap)
    taken_at = time.monotonic()
    subprocess.run(["open", "-a", APP, str(doc_a)], check=True)

    changed: dict[str, Any] | None = None
    for _ in range(20):
        time.sleep(0.4)
        candidate = _snapshot("changed-fingerprint")
        if str(candidate.get("fingerprint", "")) != fp_fingerprint:
            changed = candidate
            break
    if changed is None:
        raise Failure(
            "changed-fingerprint",
            "bringing the other document forward did not change the walked tree, so there is no "
            "staleness to measure (the leg would have been vacuous)",
        )
    age = time.monotonic() - taken_at
    if age >= service.SNAPSHOT_TTL_SECS:
        raise Failure(
            "changed-fingerprint",
            f"the snapshot was {age:.1f}s old, past the {service.SNAPSHOT_TTL_SECS:.0f}s TTL, so "
            "the TTL could have caused this refusal instead of the fingerprint",
        )
    fp_refusal = _expect_stale(
        fp_snap,
        marker=MARKER_NEVER,
        expect_detail=DETAIL_FINGERPRINT,
        forbid_details=(DETAIL_TTL, DETAIL_UNKNOWN_ID),
        expect_driver_ops=["snapshot"],  # step 3b DID re-walk; the comparison is why it refused
        clause="changed-fingerprint",
    )
    report["changed_fingerprint"] = {
        "window_snapshotted": fp_window,
        "window_at_act": _window_title(changed),
        "fingerprint_at_snapshot": fp_fingerprint,
        "fingerprint_at_act": str(changed.get("fingerprint", "")),
        "age_at_act_secs": round(age, 1),
        "refusal": fp_refusal,
        "recovered_by_re_snapshot": _write_by_index(
            changed, MARKER_FP_RECOVERY, "changed-fingerprint-recovery"
        ),
    }

    # --- the refusal is not the tool being off, the app being off, or the element missing ----
    # Stated as data rather than prose: all three would have refused the positive control and
    # both recoveries too, and each carries its own distinct code.
    report["vacuous_causes_ruled_out"] = {
        "keystone_enabled": "the positive control and both recoveries acted successfully",
        "app_allowlisted": f"{APP} passed check_app on every one of those acting dispatches",
        "element_present": "the same AXTextArea index acted before and after each refusal",
        "ax_permission": "granted; every walk succeeded (preflight probed it first)",
        "distinct_stale_cause": "each refusal names its own detail and NOT the other two",
    }

    # --- SEL: one row per attempt, and one stale row per stale refusal -----------------------
    rows = _sel_rows(home)
    if len(rows) != len(_ATTEMPTS):
        raise Failure(
            "sel-records-present",
            f"{len(_ATTEMPTS)} attempts produced {len(rows)} SEL rows; one row per attempt means "
            "a mismatch in either direction is a hole in the security record",
        )
    stale_rows = [r for r in rows if r.get("error") == "ERR_COMPUTER_USE_STALE_INDEX"]
    if len(stale_rows) != len(_STALE_OBSERVED):
        raise Failure(
            "sel-records-present",
            f"{len(_STALE_OBSERVED)} stale refusals were observed but {len(stale_rows)} SEL rows "
            "carry the stale code",
        )
    # Counting the rows was never enough (#2570). Both legs here refuse an index the gateway had
    # just resolved a snapshot for, so both rows have an app to name; the past-TTL one used to
    # record `resources=''` because step 3a raises before the dispatch assigns it, and a
    # count-based check stayed green over a record that had lost its target.
    anonymous = [r for r in stale_rows if r.get("resources") != f"app={APP}"]
    if anonymous:
        raise Failure(
            "sel-records-present",
            f"{len(anonymous)} of {len(stale_rows)} stale SEL rows do not name {APP} as the "
            f"target: {[r.get('resources') for r in anonymous]}. Both triggers refuse an index "
            "taken from a snapshot this run resolved, so a row without the app has dropped the "
            "one fact the audit is about",
        )
    report["sel"] = {
        "attempts": len(_ATTEMPTS),
        "total_rows": len(rows),
        "approved": len([r for r in rows if r.get("outcome") == "approved"]),
        "denied": len([r for r in rows if r.get("outcome") == "denied"]),
        "stale_rows": [
            {"operation": r.get("operation"), "resources": r.get("resources")} for r in stale_rows
        ],
        "stale_refusals_observed": list(_STALE_OBSERVED),
    }

    # --- leave the machine as we found it ----------------------------------------------------
    cleanup: dict[str, Any] = {"scratch_documents": "left unsaved; discarded when the app quits"}
    for app in sorted(launched):
        subprocess.run(["pkill", "-x", app], check=False)
        cleanup[app] = "quit (this run launched it)"
    if not launched:
        cleanup[APP] = "left running (the operator already had it open); its windows are untouched"
    report["cleanup"] = cleanup
    return report


def _preflight() -> dict[str, Any]:
    """Platform, the Accessibility grant, and the principal it was measured against.

    The responsible process is *recorded*, on both legs, not merely described in prose (#2569).
    A run that wrote down ``ax_process_trusted`` alone left its own reader unable to tell which
    identity answered — and on this host that identity is the difference between True and False.
    """
    if platform.system() != "Darwin":
        raise Failure("preflight", f"this is a macOS validation; this host is {platform.system()}")
    from personalclaw.computer_use import macos_ffi, macos_tcc

    trusted = macos_ffi.is_process_trusted()
    # AFTER the grant probe: tccd writes the attribution row when it answers, so a probe run
    # before the question has nothing of this process's to read. The PATIENT timeout, because
    # nothing is waiting on this: `log show` costs whatever the host's log archive costs, and a
    # validator that gave up early would record the very gap it exists to close.
    responsible = macos_tcc.responsible_process(timeout=macos_tcc.PATIENT_PROBE_TIMEOUT_SECS)
    if not trusted:
        raise Failure(
            "preflight",
            "AXIsProcessTrusted() answered False, so this session has no macOS Accessibility "
            "(TCC) grant and NOTHING below was measured. This is the OS's own answer, not a "
            "load failure: macos_ffi raises FFIUnavailable when the frameworks do not load. "
            "Fix: System Settings > Privacy & Security > Accessibility, then add and enable the "
            "RESPONSIBLE process for this session. macOS attributes the request to the terminal "
            "or host application that started the interpreter, not to the python binary, so "
            "adding python alone does not work. This session's responsible process, read from "
            f"tccd: {responsible.describe()}. To see it by hand: "
            f"{macos_tcc.RESPONSIBLE_PROBE_COMMAND}. "
            "The grant cannot be made by code (SIP-protected) and it is per-responsible-process, "
            "so a session that once answered True does not make the next one answer True.",
        )
    return {
        "platform": platform.platform(),
        "ax_process_trusted": trusted,
        "tcc_responsible_process": responsible.describe(),
        "tcc_responsible_identifier": responsible.identifier,
        "tcc_responsible_path": responsible.path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default="")
    args = parser.parse_args(argv)

    home = Path(args.home or os.environ.get("PERSONALCLAW_HOME") or tempfile.mkdtemp())
    home.mkdir(parents=True, exist_ok=True)
    os.environ["PERSONALCLAW_HOME"] = str(home)

    result: dict[str, Any] = {"atom": "DCU-3", "clause": "stale index", "home": str(home)}
    try:
        result["preflight"] = _preflight()
        result["detail"] = phase_stale(home)
    except Failure as exc:
        result["status"] = "unproven"
        result["failed_clause"] = exc.clause
        result["reason"] = exc.detail
        result["attempts"] = len(_ATTEMPTS)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    result["status"] = "pass"
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
