#!/usr/bin/env python3
"""Wheel contract verifier (plan 34 T1.5, contract C4).

Proves a built PersonalClaw wheel is a self-contained, installable, servable
artifact — the guarantee every install channel (pip/uv/pipx/container) rides on.
It asserts, against a real wheel and a scratch venv with NO Node present:

  1. the wheel carries the built SPA (``personalclaw/static/dist/index.html``);
  2. it installs into a fresh venv from the wheel alone (no source tree, no npm);
  3. ``personalclaw gateway --test-mode`` boots and emits its READY line;
  4. ``GET /api/healthz`` → 200 JSON (auth-exempt liveness);
  5. ``GET /`` → 200 HTML (the SPA shell, served from the packaged assets), and
  6. every bundled app/extension the wheel ships actually ENABLED — see
     :func:`extension_failures` for why assertion 6 exists (#2758), and
  7. the bundled default chat model, if one is signed off, carries a permitted licence and
     fits its declared size budget — see :func:`_assert_bundled_model_admitted` (OU-14).

Exit 0 = contract met. Run locally after ``npm run build && python -m build``,
and in ``release.yml`` (replacing the shallow namelist check).

Usage:
    python scripts/verify_wheel.py [--wheel dist/personalclaw-*.whl] [--build] [--keep]

    --wheel PATH  verify this wheel (default: newest dist/*.whl).
    --build       clear the stale staging tree, then run ``python -m build --wheel``
                  (assumes the SPA is already built into web/dist or
                  src/personalclaw/static/dist). See :func:`_build_wheel`.
    --keep        keep the scratch venv/home for debugging.

The script deliberately uses only the stdlib (+ the wheel it installs) so it can
run on a bare CI runner without extra deps.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import venv
import zipfile
from pathlib import Path
from typing import Iterable, NoReturn

_SPA_MARKER = "personalclaw/static/dist/index.html"
_READY_PREFIX = "PERSONALCLAW_READY:"
_BOOT_TIMEOUT_S = 90.0

#: Log lines that mean "the wheel shipped an app it cannot load". Each one is emitted by
#: ``personalclaw/providers/registry.py``; ``tests/test_verify_wheel_contract.py`` pins every
#: marker to the source line that emits it, so rewording the log reds that rail instead of
#: silently disarming assertion 6 here.
_EXTENSION_FAILURE_MARKERS: tuple[str, ...] = (
    "Failed to enable extension",  # registry._enable_one — handler.create() raised
    "No type handler for provider type",  # registry._enable_one — unknown provider type
    "Cannot enable unknown extension",  # registry.enable — manifest names a missing entry
)

#: The POSITIVE line, and the reason the gateway is booted verbose. Without it assertion 6 can
#: only say "no bad news", which is what an extension loader that never ran also says.
_EXTENSION_SUCCESS_MARKER = "Enabled extension"

#: Where a bundled app lives inside the wheel — used only to report the two counts side by side.
_BUNDLED_APP_PREFIX = "personalclaw/apps/native/"


def _log(msg: str) -> None:
    print(f"[verify_wheel] {msg}", flush=True)


def _fail(msg: str) -> NoReturn:
    print(f"[verify_wheel] FAIL: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def _find_wheel(explicit: str | None) -> Path:
    if explicit:
        matches = sorted(glob.glob(explicit))
        if not matches:
            _fail(f"no wheel matched {explicit!r}")
        return Path(matches[-1])
    matches = sorted(glob.glob("dist/*.whl"))
    if not matches:
        _fail("no wheel in dist/ — run `python -m build --wheel` (or pass --wheel)")
    return Path(matches[-1])


def _build_wheel() -> None:
    """Build the wheel from a CLEAN staging tree.

    🪤 ``python -m build`` DOES NOT CLEAR ``build/``, and setuptools re-uses whatever it finds
    there. MEASURED 2026-09-07 (#2758) in a tree that ``git status`` reported clean: the wheel
    carried three ``personalclaw/apps/native/`` app directories that existed neither in git nor
    on disk — ``run-workflow-action``, ``personalclaw-schedule-tools``, ``native-workflows`` —
    left behind in ``build/lib/`` by an earlier build. Two of them named factory functions that
    no longer exist anywhere in the package, so the gateway logged two ERROR tracebacks while
    this very script printed PASS.

    So anything ever DELETED from ``src/personalclaw/**`` could reappear in a locally built
    wheel — and a container image, a ``pip install ./dist/*.whl`` or a hand-cut release all
    inherit it. Here the payload was inert; a deleted module that still *imports* would run.
    ``DIST-3`` calls the bare ``python -m build`` "the release command", so the safety belongs
    in the command rather than in a reader's memory of ``rm -rf build``.

    ``dist/`` goes too: :func:`_find_wheel` picks ``sorted(glob(...))[-1]``, which is
    LEXICOGRAPHIC and not newest-by-mtime, so a leftover wheel with a higher version string
    would be verified in place of the one just built. Only under ``--build`` — a bare
    ``--wheel`` invocation (what ``release.yml`` runs) touches nothing.
    """
    for stale in (Path("build"), Path("dist")):
        if stale.exists():
            _log(f"removing the stale {stale}/ tree so the build cannot re-use it")
            shutil.rmtree(stale, ignore_errors=True)
    _log("building wheel (python -m build --wheel)…")
    subprocess.run([sys.executable, "-m", "build", "--wheel"], check=True)


def _load_bundled_model_rail():
    """Import ``personalclaw.bundled_model`` from the SOURCE tree, by path.

    By path and not by ``import personalclaw.bundled_model`` because this script runs on a bare
    runner before anything is installed — that is the whole reason it is stdlib-only. The module
    it loads is stdlib-only too, and it is the SAME module the tests exercise, so the release
    gate and ``tests/test_bundled_model_gate.py`` cannot drift into two dialects of the rule.
    """
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    import importlib

    return root, importlib.import_module("personalclaw.bundled_model")


def _assert_bundled_model_admitted(wheel: Path) -> None:
    """Assertion 7 (OU-14): the bundled default chat model's licence and size budget.

    Three things can red here — an over-budget weight, a weight with a licence that is not on
    the permitted allowlist, and a weight nothing signed off — and one thing is deliberately
    NOT a red: no bundle at all, which is the state today. That state prints its own loud line
    rather than an ``OK``, because a gate whose green means "nobody measured" is not a gate, and
    OU-14's own escalation found three of its four clauses could go green with no model bundled.
    """
    root, rail = _load_bundled_model_rail()
    try:
        declaration = rail.repo_declaration(root)
    except rail.BundleDeclarationError as exc:
        _fail(f"the bundled-model sign-off record is unreadable: {exc}")
    result = rail.gate_wheel(wheel, declaration)
    _log(f"bundled model: {result.summary}")
    for refusal in result.refusals:
        _log(f"bundled model REFUSAL: {refusal}")
    if not result.ok:
        _fail(f"bundled-model gate refused this wheel ({len(result.refusals)} refusal(s))")


def _assert_spa_in_wheel(wheel: Path) -> None:
    names = zipfile.ZipFile(wheel).namelist()
    if not any(n.endswith(_SPA_MARKER) for n in names):
        _fail(
            f"wheel {wheel.name} does not carry the SPA ({_SPA_MARKER}). "
            "Run `npm run build` before `python -m build` so setup.py's "
            "BuildWithWeb stages web/dist into the package."
        )
    _log(f"OK: wheel carries the SPA — {wheel.name}")


def bundled_app_count(wheel: Path) -> int:
    """How many app directories the wheel carries under ``personalclaw/apps/native/``.

    Reported beside the enabled count purely so the release log says what was on offer as well
    as what loaded — assertion 6 does not require the two to be EQUAL, because a bundled app
    that declares no ``provider`` legitimately never becomes an extension.
    """
    names = zipfile.ZipFile(wheel).namelist()
    dirs = {
        n[len(_BUNDLED_APP_PREFIX) :].split("/", 1)[0]
        for n in names
        if n.startswith(_BUNDLED_APP_PREFIX) and "/" in n[len(_BUNDLED_APP_PREFIX) :]
    }
    return len(dirs)


def _make_venv(root: Path) -> Path:
    """Create a venv with pip; return the python executable path."""
    venv.EnvBuilder(with_pip=True, clear=True).create(str(root))
    py = (
        root
        / ("Scripts" if os.name == "nt" else "bin")
        / ("python.exe" if os.name == "nt" else "python")
    )
    if not py.exists():
        _fail(f"venv python not found at {py}")
    return py


def _pip_install_wheel(py: Path, wheel: Path) -> None:
    _log("installing the wheel into the scratch venv (from the wheel alone)…")
    subprocess.run(
        [str(py), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        check=True,
    )
    subprocess.run(
        [str(py), "-m", "pip", "install", str(wheel), "--quiet"],
        check=True,
    )


def _assert_no_node() -> None:
    """The wheel must serve its own SPA with NO Node toolchain present."""
    if shutil.which("npm") or shutil.which("node"):
        _log(
            "WARNING: node/npm present on PATH — the contract is that assets ship "
            "in the wheel; the test still holds but does not *prove* Node-absence."
        )
    else:
        _log("OK: no node/npm on PATH — asset-serving proves the wheel is self-contained")


def extension_failures(lines: Iterable[str]) -> list[str]:
    """The gateway lines that say a bundled app FAILED TO LOAD, in order.

    Assertion 6, and the reason it exists: this script already booted a real gateway and
    already read its output — that is how #2758's two ERROR tracebacks were visible in a run
    that printed ``PASS``. The evidence was in hand and nothing asserted on it, which is the
    same shape as a check that passes while the thing it exists to prove never happened.

    A user installing such a wheel gets silently missing capabilities plus error rows in the
    app Store, and nothing in the release path says so. Extension loading is best-effort BY
    DESIGN in the gateway (one broken app must not take the process down), so a failure is
    logged and the boot succeeds — which means the log is the only place the failure exists.
    """
    return [line for line in lines if any(m in line for m in _EXTENSION_FAILURE_MARKERS)]


def extensions_enabled(lines: Iterable[str]) -> list[str]:
    """The gateway lines that say a bundled app DID load — the anti-vacuity half."""
    return [line for line in lines if _EXTENSION_SUCCESS_MARKER in line]


def _assert_every_bundled_app_enabled(transcript: list[str], bundled_apps: int) -> None:
    """Assertion 6: the registry ran, and it reported no failure.

    BOTH halves, because either alone is satisfiable by nothing happening. Measured on the
    0.1.3 wheel: 182 gateway lines, 30 ``Enabled extension`` lines against 30 bundled app
    directories, one WARNING (the expected ``AUTH_MODE=none`` notice) and zero failures. At the
    default log level only 2 lines are emitted and NONE of them mention an extension, which is
    why the gateway is booted ``--verbose`` — a check whose evidence window is empty passes for
    the same reason a broken one does.
    """
    failures = extension_failures(transcript)
    if failures:
        detail = "\n".join(f"  {line}" for line in failures[:20])
        _fail(
            f"the wheel boots but {len(failures)} bundled extension line(s) report a FAILURE "
            f"to load:\n{detail}\n"
            "The wheel ships an app the package cannot enable. If the app directory is not in "
            "git, a stale build/ tree was packaged — rebuild with --build (which clears it). "
            "If it IS in git, the app's manifest names a factory the package no longer defines."
        )
    enabled = extensions_enabled(transcript)
    if not enabled:
        _fail(
            f"the gateway never reported enabling a single extension, so this assertion "
            f"measured NOTHING — a wheel with broken apps would look identical. The wheel "
            f"carries {bundled_apps} bundled app director(ies) under {_BUNDLED_APP_PREFIX}. "
            f"Read {len(transcript)} line(s); check that the gateway is still booted with the "
            f"top-level --verbose flag (the registry logs enables at INFO)."
        )
    _log(
        f"OK: {len(enabled)} extension(s) enabled, 0 failed "
        f"({bundled_apps} bundled app dir(s) in the wheel, {len(transcript)} gateway line(s))"
    )


def _read_ready_line(proc: "subprocess.Popen[str]", deadline: float, transcript: list[str]) -> dict:
    """Block until the gateway prints its PERSONALCLAW_READY line (or timeout).

    Every line read is appended to *transcript*, including the pre-READY startup chatter —
    which is exactly where the extension failures of #2758 appear, since the registry enables
    the bundled apps during boot.
    """
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                _fail(f"gateway exited early (rc={proc.returncode}) before READY")
            continue
        line = line.rstrip("\n")
        if line.startswith(_READY_PREFIX):
            return json.loads(line[len(_READY_PREFIX) :])
        # Surface startup chatter for debugging without failing on it — but KEEP it, so
        # assertion 6 can judge it (see `extension_failures`).
        transcript.append(line)
        _log(f"gateway> {line}")
    _fail("timed out waiting for the gateway READY line")


def _drain(proc: "subprocess.Popen[str]", transcript: list[str]) -> None:
    """Keep reading the gateway's output after READY, into *transcript*.

    Without this the pipe would fill (blocking the gateway) and, more importantly, any
    extension that is enabled lazily — after the READY line rather than during boot — would
    report its failure into a stream nobody read. Assertion 6 must not depend on WHEN the
    registry happens to enable an app.
    """
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            transcript.append(line)
            _log(f"gateway> {line}")
    except (ValueError, OSError):  # pipe closed under us by terminate()
        return


def _http_get(url: str, timeout: float = 10.0) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read(4096).decode("utf-8", "replace")
            return resp.status, resp.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as exc:  # non-2xx
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", ""
    except Exception as exc:  # noqa: BLE001
        _fail(f"GET {url} raised {type(exc).__name__}: {exc}")


def _boot_and_probe(py: Path, home: Path, bundled_apps: int = 0) -> None:
    env = dict(os.environ)
    env["PERSONALCLAW_HOME"] = str(home)
    # Loopback-only, no-auth so `/` (the SPA shell) is served without a token —
    # a localhost smoke test; effective_bind() pins NONE mode to 127.0.0.1.
    env["PERSONALCLAW_AUTH_MODE"] = "none"
    env.pop("PYTHONWARNINGS", None)

    # 🪤 `--verbose` is a TOP-LEVEL flag and belongs BEFORE the subcommand — `gateway
    # --test-mode --verbose` exits 2 with "unrecognized arguments". It is here because the
    # registry logs each enable at INFO and the default level is WARNING: measured on the 0.1.3
    # wheel, the default boot emits 2 lines and mentions no extension at all, so assertion 6
    # would have had an EMPTY evidence window. Verbose gives it 182 lines and 30 enables.
    _log("booting `personalclaw --verbose gateway --test-mode`…")
    proc = subprocess.Popen(
        [str(py), "-m", "personalclaw", "--verbose", "gateway", "--test-mode"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    transcript: list[str] = []
    drain: threading.Thread | None = None
    try:
        ready = _read_ready_line(proc, time.time() + _BOOT_TIMEOUT_S, transcript)
        drain = threading.Thread(target=_drain, args=(proc, transcript), daemon=True)
        drain.start()
        port = int(ready["port"])
        base = f"http://127.0.0.1:{port}"
        _log(f"gateway READY on {base} (pid={ready.get('pid')})")

        # 4. /api/healthz — auth-exempt liveness, 200 JSON with the version.
        status, ctype, body = _http_get(f"{base}/api/healthz")
        if status != 200:
            _fail(f"/api/healthz returned {status} (want 200)")
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001
            payload = {}
        if payload.get("status") != "ok":
            _fail(f"/api/healthz body not ok: {body!r}")
        _log(f"OK: /api/healthz → 200 {payload}")

        # 5. / — the SPA shell, 200 HTML served from the packaged static/dist.
        status, ctype, body = _http_get(f"{base}/")
        if status != 200:
            _fail(f"/ returned {status} (want 200 HTML)")
        if "text/html" not in ctype.lower() and "<!doctype html" not in body.lower():
            _fail(f"/ did not return HTML (content-type={ctype!r})")
        _log("OK: / → 200 HTML (SPA shell served from the wheel's static/dist)")
    finally:
        _log("stopping gateway…")
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if drain is not None:
            drain.join(timeout=5)

    # 6. Assertion SIX, deliberately after the shutdown so the transcript is complete: the
    #    gateway served / and /api/healthz, so the two assertions above are satisfied — and a
    #    wheel whose bundled apps failed to load satisfies them too. #2758.
    _assert_every_bundled_app_enabled(transcript, bundled_apps)


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the PersonalClaw wheel contract (C4).")
    ap.add_argument("--wheel", help="wheel path or glob (default: newest dist/*.whl)")
    ap.add_argument("--build", action="store_true", help="build the wheel first")
    ap.add_argument("--keep", action="store_true", help="keep scratch venv/home")
    args = ap.parse_args()

    if args.build:
        _build_wheel()

    wheel = _find_wheel(args.wheel)
    _log(f"verifying {wheel}")
    _assert_spa_in_wheel(wheel)
    _assert_bundled_model_admitted(wheel)
    _assert_no_node()

    scratch = Path(tempfile.mkdtemp(prefix="pc_verify_wheel_"))
    venv_dir = scratch / "venv"
    home_dir = scratch / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    try:
        py = _make_venv(venv_dir)
        _pip_install_wheel(py, wheel)
        _boot_and_probe(py, home_dir, bundled_app_count(wheel))
    finally:
        if args.keep:
            _log(f"kept scratch dir: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    _log(
        "PASS: wheel contract met (SPA packaged, installs Node-free, "
        "gateway serves / + /api/healthz, every bundled app enabled, bundled-model gate "
        "not refused — read its own line above for whether it measured anything)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
