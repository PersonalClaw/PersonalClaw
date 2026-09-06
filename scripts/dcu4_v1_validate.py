#!/usr/bin/env python3
"""DESKTOP-COMPUTER-USE V1: drive a real app by element index, live, and record the result.

`DCU-4`'s last open clause is the plan's V1 row (``DESKTOP-COMPUTER-USE.md`` line 120):

    Validation (macOS, enable ON): drive a real app by element index; confirm the pointer stays
    put; confirm a secure-field refusal; confirm SEL records; confirm the enable file being
    absent blocks everything.

Every prior audit recorded V1 as ungrantable-by-code because the macOS Accessibility (TCC)
grant is SIP-protected. That is true of *granting* it and false of *using* it: on a machine
where the operator has already trusted the interpreter, ``AXIsProcessTrusted()`` answers True
and the whole chain is drivable. So this script probes the grant FIRST and refuses to report a
pass without it, rather than skipping and reporting green — a skip that reads as a pass is the
failure mode this repo keeps rediscovering.

**Every phase drives ``service.computer_dispatch``**, never the driver or the FFI directly.
Dispatching is the only entry point (``DCU-4``), so a validation that reached around it would
prove the driver works and say nothing about the chain that is supposed to restrain it.

**The absent-enable phase runs in its own interpreter.** ``enable_state`` reads the keystone
once and caches it ("no mid-run flip"), so a single process cannot honestly observe both the
absent and the armed state; ``reset_enable_state()`` exists but using it here would validate
the test hook instead of the property. ``--phase absent`` therefore re-execs.

Usage::

    PYTHONPATH=src python scripts/dcu4_v1_validate.py            # both phases, JSON to stdout
    PYTHONPATH=src python scripts/dcu4_v1_validate.py --phase armed --home /tmp/x

Exit status 0 only when every clause holds. Anything unproven is reported as ``unproven`` with
the reason, never silently dropped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# The seven-tool surface must all refuse when the enable file is absent; "blocks everything"
# is a claim about the population, not about whichever tool the validator happened to try.
_ABSENT_PROBES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("computer_list_apps", {}),
    ("computer_snapshot", {"app": "TextEdit"}),
    ("computer_click", {"snapshot_id": "x", "element_index": 0}),
    ("computer_type", {"snapshot_id": "x", "element_index": 0, "text": "x"}),
    ("computer_set_value", {"snapshot_id": "x", "element_index": 0, "value": "x"}),
    ("computer_scroll", {"snapshot_id": "x", "element_index": 0, "direction": "down"}),
    ("computer_perform_action", {"snapshot_id": "x", "element_index": 0, "action": "AXPress"}),
)

_MARKER = "DCU-4 V1 drove this text area by element index"


class Failure(Exception):
    """A clause did not hold. Carries the clause name so the report names it."""

    def __init__(self, clause: str, detail: str) -> None:
        super().__init__(f"{clause}: {detail}")
        self.clause = clause
        self.detail = detail


#: Every dispatch this run made, so "one SEL row per attempt" is checked against the real
#: number of attempts rather than a number written down once and left to drift.
_ATTEMPTS: list[str] = []


def _dispatch(tool: str, params: dict[str, Any]) -> tuple[str, Any]:
    """Run one dispatch. Returns ``("ok", result)`` or ``("refused", code)``."""
    from personalclaw.computer_use import enable_state
    from personalclaw.computer_use import policy as cu_policy
    from personalclaw.computer_use import service

    _ATTEMPTS.append(tool)
    try:
        result = asyncio.run(
            service.computer_dispatch(tool, params, source="dcu4_v1_validate", caller_identity="")
        )
    except (
        enable_state.ComputerUseDisabled,
        cu_policy.ComputerUsePolicyRefusal,
        service.ComputerUseRefusal,
    ) as exc:
        return "refused", exc.error
    return "ok", result


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


def phase_absent(home: Path) -> dict[str, Any]:
    """Clause: an absent enable file blocks everything, and the refusal is still audited."""
    from personalclaw.computer_use.enable_state import ENABLE_FILENAME, GOVERNANCE_DIRNAME

    enable_file = home / GOVERNANCE_DIRNAME / ENABLE_FILENAME
    if enable_file.exists():  # absent means absent; a stale file would make this phase a lie
        enable_file.unlink()
    observed = []
    for tool, params in _ABSENT_PROBES:
        outcome, payload = _dispatch(tool, params)
        if outcome != "refused":
            raise Failure(
                "absent-enable-blocks-everything",
                f"{tool} returned a result with no enable file: {payload!r}",
            )
        observed.append({"tool": tool, "code": getattr(payload, "code", "")})
    codes = {row["code"] for row in observed}
    if codes != {"ERR_COMPUTER_USE_DISABLED"}:
        raise Failure(
            "absent-enable-blocks-everything",
            f"expected every refusal to be ERR_COMPUTER_USE_DISABLED, saw {sorted(codes)}",
        )
    rows = _sel_rows(home)
    denied = [r for r in rows if r.get("outcome") == "denied"]
    if len(denied) != len(_ATTEMPTS):
        raise Failure(
            "sel-records-present",
            f"{len(_ATTEMPTS)} refused attempts produced {len(denied)} SEL denied rows",
        )
    return {
        "enable_file": str(enable_file),
        "enable_file_exists": enable_file.exists(),
        "probes": observed,
        "sel_denied_rows": len(denied),
    }


def _front_window_present(app: str) -> bool:
    from personalclaw.computer_use import macos_ffi

    try:
        macos_ffi.walk_window(app)
    except Exception:
        return False
    return True


def _element(elements: list[dict[str, Any]], **match: str) -> dict[str, Any]:
    for el in elements:
        if all(str(el.get(key, "")) == value for key, value in match.items()):
            return el
    raise Failure("element-lookup", f"no element matching {match!r} in {len(elements)} elements")


def _fresh_act(
    app: str, match: dict[str, str], tool: str, extra: dict[str, Any], *, tries: int = 4
) -> tuple[str, Any, dict[str, Any], tuple[tuple[float, float], tuple[float, float]]]:
    """Snapshot *app*, find the element matching *match*, and act on it BY INDEX.

    Retries ``ERR_COMPUTER_USE_STALE_INDEX``. That is not a weakened screen: a live desktop can
    legitimately change between the snapshot and the act (a banner appears, a window resizes),
    the fingerprint check is *supposed* to refuse the stale index, and re-snapshotting is the
    operator's own next move. What is NOT retried is any other refusal — a policy refusal is the
    answer, not a transient.

    The real cursor is sampled immediately before and immediately after the ACTING dispatch, not
    once around the whole run: the clause is "the pointer did not move *as a result of* this
    action", and a wider window would fail on a human nudging the mouse — a false defect, which
    is as bad as a missed one.
    """
    from personalclaw.computer_use import macos_ffi

    Result = tuple[str, Any, dict[str, Any], tuple[tuple[float, float], tuple[float, float]]]
    last: Result | None = None
    for _ in range(tries):
        outcome, snap = _dispatch("computer_snapshot", {"app": app})
        if outcome != "ok":
            here = macos_ffi.pointer_position()
            return outcome, snap, {}, (here, here)
        element = _element(list(snap.get("elements") or []), **match)
        pointer_before = macos_ffi.pointer_position()
        outcome, payload = _dispatch(
            tool,
            {
                "snapshot_id": str(snap.get("snapshot_id")),
                "element_index": element["index"],
                **extra,
            },
        )
        pointer_after = macos_ffi.pointer_position()
        if outcome == "refused" and getattr(payload, "code", "") == "ERR_COMPUTER_USE_STALE_INDEX":
            last = (outcome, payload, element, (pointer_before, pointer_after))
            time.sleep(0.5)
            continue
        return outcome, payload, element, (pointer_before, pointer_after)
    assert last is not None
    return last


def phase_armed(home: Path) -> dict[str, Any]:
    """Clauses: real app driven by element index, pointer stays put, secure field refused."""
    from personalclaw.computer_use import macos_ffi

    scratch = Path(tempfile.gettempdir()) / "dcu4-v1-scratch.txt"
    scratch.write_text("scratch\n", encoding="utf-8")
    secure_page = Path(tempfile.gettempdir()) / "dcu4-v1-secure.html"
    secure_page.write_text(
        '<!doctype html><meta charset="utf-8"><title>DCU-4 V1</title>'
        "<body><h1>DCU-4 V1 secure-field target</h1>"
        '<form><label for="p">Password</label>'
        '<input id="p" type="password" aria-label="Password"></form></body>',
        encoding="utf-8",
    )
    _write_enable(home, ["TextEdit", "Safari"])

    # Recorded BEFORE launching, because teardown may only kill what this run started. A
    # validation that quits an app the operator already had open destroys their unsaved work.
    already = set(macos_ffi.list_gui_apps())
    launched = {app for app in ("TextEdit", "Safari") if app not in already}

    # ``-F`` (fresh) matters: without it macOS restores the app's previously open windows,
    # the front window is some earlier document, and "the window closed" becomes unobservable
    # because a SECOND window is still there. That cost this script two runs to find.
    subprocess.run(["open", "-F", "-a", "TextEdit", str(scratch)], check=True)
    subprocess.run(["open", "-F", "-a", "Safari", secure_page.as_uri()], check=True)
    for _ in range(30):
        if _front_window_present("TextEdit") and _front_window_present("Safari"):
            break
        time.sleep(0.5)
    else:
        raise Failure("live-targets", "TextEdit and Safari did not both expose a front window")

    report: dict[str, Any] = {"launched_by_this_run": sorted(launched)}

    # --- the allowlist narrows the listing (step 7), and it is not vacuous --------------------
    outcome, listing = _dispatch("computer_list_apps", {})
    if outcome != "ok":
        raise Failure("list-apps", f"refused: {getattr(listing, 'code', listing)!r}")
    listed = list(listing.get("apps") or [])
    if "TextEdit" not in listed:
        raise Failure("list-apps", f"TextEdit absent from the narrowed listing {listed!r}")
    running = macos_ffi.list_gui_apps()
    if len(running) <= len(listed):
        raise Failure(
            "list-apps",
            f"the listing was not narrowed: {len(listed)} listed of {len(running)} running",
        )
    report["list_apps"] = {"listed": listed, "running": len(running)}

    # --- a non-allowlisted app is refused BY NAME, before any window is walked ---------------
    outcome, refusal = _dispatch("computer_snapshot", {"app": "Finder"})
    if outcome != "refused" or getattr(refusal, "code", "") != "ERR_COMPUTER_USE_APP_NOT_ALLOWED":
        raise Failure("app-allowlist", f"Finder was not refused: {outcome} {refusal!r}")
    report["non_allowlisted_app"] = {"app": "Finder", "code": refusal.code}

    # --- CLICK by element index, on a CLEAN document -----------------------------------------
    # Ordered before the write deliberately. A press on the close button of an EDITED document
    # raises the save sheet instead of closing the window, so the clause would read "the press
    # had no effect" when the press in fact worked perfectly — a false negative that cost this
    # script one run to find. The clean document makes the effect unambiguous.
    outcome, clicked, close_button, click_pointer = _fresh_act(
        "TextEdit", {"role": "AXButton", "subrole": "AXCloseButton"}, "computer_click", {}
    )
    if outcome != "ok":
        raise Failure("click-by-index", f"click refused: {getattr(clicked, 'code', clicked)!r}")
    if clicked.get("method") != "auto":
        raise Failure("click-by-index", f"the default method resolved to {clicked.get('method')!r}")
    closed = False
    for _ in range(20):
        if not _front_window_present("TextEdit"):
            closed = True
            break
        time.sleep(0.25)
    if not closed:
        raise Failure(
            "click-by-index",
            "pressing the close button by index left the window open, so the press had no effect",
        )
    report["click_by_index"] = {
        "element_index": close_button["index"],
        "method": clicked.get("method"),
        "observable_effect": "front window closed",
        "pointer_before": click_pointer[0],
        "pointer_after": click_pointer[1],
    }

    # --- WRITE by element index, and prove the operator's pointer did not move ----------------
    subprocess.run(["open", "-F", "-a", "TextEdit", str(scratch)], check=True)
    for _ in range(30):
        if _front_window_present("TextEdit"):
            break
        time.sleep(0.5)
    else:
        raise Failure("live-targets", "TextEdit did not re-open a front window after the click")
    outcome, written, text_area, write_pointer = _fresh_act(
        "TextEdit", {"role": "AXTextArea"}, "computer_set_value", {"value": _MARKER}
    )
    if outcome != "ok":
        raise Failure("drive-by-index", f"set_value refused: {getattr(written, 'code', written)!r}")
    live_area = _element(
        [el.to_dict() for el in macos_ffi.walk_window("TextEdit").elements], role="AXTextArea"
    )
    if str(live_area.get("value", "")).strip() != _MARKER:
        raise Failure(
            "drive-by-index",
            f"the text area holds {live_area.get('value')!r}, not the marker this run wrote",
        )
    report["drive_by_index"] = {
        "tool": "computer_set_value",
        "element_index": text_area["index"],
        "observed_value": live_area.get("value"),
        "pointer_before": write_pointer[0],
        "pointer_after": write_pointer[1],
    }

    # --- the pointer clause, stated per action ------------------------------------------------
    moved = [
        {"action": name, "before": pair[0], "after": pair[1]}
        for name, pair in (("computer_set_value", write_pointer), ("computer_click", click_pointer))
        if pair[0] != pair[1]
    ]
    if moved:
        raise Failure(
            "pointer-stays-put", f"the cursor moved across an index-driven action: {moved!r}"
        )
    report["pointer_stays_put"] = {
        "set_value": {"before": write_pointer[0], "after": write_pointer[1]},
        "click": {"before": click_pointer[0], "after": click_pointer[1]},
    }

    # --- a LIVE secure field, refused --------------------------------------------------------
    outcome, refusal, secure, _ = _fresh_act(
        "Safari",
        {"role": "AXTextField", "subrole": "AXSecureTextField"},
        "computer_type",
        {"text": "hunter2"},
    )
    if outcome != "refused":
        raise Failure("secure-field", f"typing into a live secure field returned {refusal!r}")
    code = getattr(refusal, "code", "")
    if code != "ERR_COMPUTER_USE_SECURE_FIELD":
        raise Failure("secure-field", f"refused with {code!r}, not the secure-field code")
    rendered = " ".join(str(getattr(refusal, field, "")) for field in ("what", "why", "fix"))
    if "AXSecureTextField" not in rendered:
        raise Failure("secure-field", f"the refusal does not name AXSecureTextField: {rendered!r}")
    live_secure = _element(
        [el.to_dict() for el in macos_ffi.walk_window("Safari").elements],
        role="AXTextField",
        subrole="AXSecureTextField",
    )
    if str(live_secure.get("value", "")):
        raise Failure("secure-field", "the secure field holds a value, so something was typed")
    report["secure_field"] = {
        "app": "Safari",
        "element_index": secure["index"],
        "subrole": secure.get("subrole"),
        "code": code,
        "field_value_after": live_secure.get("value", ""),
    }

    # --- SEL: one row per attempt, both verdicts present -------------------------------------
    rows = _sel_rows(home)
    approved = [r for r in rows if r.get("outcome") == "approved"]
    denied = [r for r in rows if r.get("outcome") == "denied"]
    if not approved or not denied:
        raise Failure(
            "sel-records-present",
            f"expected both verdicts, saw approved={len(approved)} denied={len(denied)}",
        )
    if len(rows) != len(_ATTEMPTS):
        raise Failure(
            "sel-records-present",
            f"{len(_ATTEMPTS)} attempts produced {len(rows)} SEL rows; the clause is one row per "
            "attempt, so a mismatch in either direction is a hole in the security record",
        )
    report["sel"] = {
        "attempts": len(_ATTEMPTS),
        "total_rows": len(rows),
        "approved": len(approved),
        "denied": len(denied),
        "operations": [r.get("operation") for r in rows],
        "codes": sorted({r.get("error") for r in rows if r.get("error")}),
    }

    # --- leave the machine as we found it -----------------------------------------------------
    # Deliberately NOT osascript: `tell application` needs the Apple Events (Automation) TCC
    # grant, a DIFFERENT grant from Accessibility, and on a machine without it osascript blocks
    # on a prompt nobody answers. Teardown is also strictly limited to apps this run launched —
    # see `launched`. The scratch document is left EDITED and unsaved on purpose: quitting the
    # app is what discards it, and saving it would write a file nobody asked for.
    cleanup: dict[str, Any] = {"scratch_document": "left unsaved; discarded with the app"}
    for app in sorted(launched):
        subprocess.run(["pkill", "-x", app], check=False)
        cleanup[app] = "quit (this run launched it)"
    for app in sorted({"TextEdit", "Safari"} - launched):
        cleanup[app] = "left running (the operator already had it open)"
    report["cleanup"] = cleanup
    return report


def _preflight() -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise Failure("preflight", f"V1 is a macOS validation; this is {platform.system()}")
    from personalclaw.computer_use import macos_ffi

    trusted = macos_ffi.is_process_trusted()
    if not trusted:
        raise Failure(
            "preflight",
            "AXIsProcessTrusted() is False: this interpreter has no macOS Accessibility (TCC) "
            "grant. Grant it in System Settings > Privacy & Security > Accessibility for the "
            "process that runs this script. It cannot be granted by code (SIP-protected).",
        )
    return {"platform": platform.platform(), "ax_process_trusted": trusted}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("absent", "armed", "both"), default="both")
    parser.add_argument("--home", default="")
    args = parser.parse_args(argv)

    if args.phase == "both":
        root = Path(tempfile.mkdtemp(prefix="dcu4-v1-"))
        try:
            out: dict[str, Any] = {"phases": {}}
            for phase in ("absent", "armed"):
                home = root / phase
                home.mkdir(parents=True, exist_ok=True)
                env = {**os.environ, "PERSONALCLAW_HOME": str(home)}
                proc = subprocess.run(
                    [sys.executable, __file__, "--phase", phase, "--home", str(home)],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                try:
                    out["phases"][phase] = json.loads(proc.stdout or "{}")
                except json.JSONDecodeError:
                    out["phases"][phase] = {"status": "error", "stdout": proc.stdout}
                if proc.stderr.strip():
                    out["phases"][phase]["stderr_tail"] = proc.stderr.strip().splitlines()[-8:]
                if proc.returncode != 0:
                    out["status"] = "fail"
            out.setdefault("status", "pass")
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0 if out["status"] == "pass" else 1
        finally:
            shutil.rmtree(root, ignore_errors=True)

    home = Path(args.home or os.environ.get("PERSONALCLAW_HOME") or tempfile.mkdtemp())
    home.mkdir(parents=True, exist_ok=True)
    os.environ["PERSONALCLAW_HOME"] = str(home)
    result: dict[str, Any] = {"phase": args.phase, "home": str(home)}
    try:
        result["preflight"] = _preflight()
        result["detail"] = phase_absent(home) if args.phase == "absent" else phase_armed(home)
    except Failure as exc:
        result["status"] = "unproven"
        result["clause"] = exc.clause
        result["reason"] = exc.detail
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    result["status"] = "pass"
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
