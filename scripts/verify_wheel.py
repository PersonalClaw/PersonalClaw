#!/usr/bin/env python3
"""Wheel contract verifier (plan 34 T1.5, contract C4).

Proves a built PersonalClaw wheel is a self-contained, installable, servable
artifact — the guarantee every install channel (pip/uv/pipx/container) rides on.
It asserts, against a real wheel and a scratch venv with NO Node present:

  1. the wheel carries the built SPA (``personalclaw/static/dist/index.html``);
  2. it installs into a fresh venv from the wheel alone (no source tree, no npm);
  3. ``personalclaw gateway --test-mode`` boots and emits its READY line;
  4. ``GET /api/healthz`` → 200 JSON (auth-exempt liveness), and
  5. ``GET /`` → 200 HTML (the SPA shell, served from the packaged assets).

Exit 0 = contract met. Run locally after ``npm run build && python -m build``,
and in ``release.yml`` (replacing the shallow namelist check).

Usage:
    python scripts/verify_wheel.py [--wheel dist/personalclaw-*.whl] [--build] [--keep]

    --wheel PATH  verify this wheel (default: newest dist/*.whl).
    --build       run ``python -m build --wheel`` first (assumes the SPA is
                  already built into web/dist or src/personalclaw/static/dist).
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
import time
import urllib.error
import urllib.request
import venv
import zipfile
from pathlib import Path
from typing import NoReturn

_SPA_MARKER = "personalclaw/static/dist/index.html"
_READY_PREFIX = "PERSONALCLAW_READY:"
_BOOT_TIMEOUT_S = 90.0


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
    _log("building wheel (python -m build --wheel)…")
    subprocess.run([sys.executable, "-m", "build", "--wheel"], check=True)


def _assert_spa_in_wheel(wheel: Path) -> None:
    names = zipfile.ZipFile(wheel).namelist()
    if not any(n.endswith(_SPA_MARKER) for n in names):
        _fail(
            f"wheel {wheel.name} does not carry the SPA ({_SPA_MARKER}). "
            "Run `npm run build` before `python -m build` so setup.py's "
            "BuildWithWeb stages web/dist into the package."
        )
    _log(f"OK: wheel carries the SPA — {wheel.name}")


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


def _read_ready_line(proc: "subprocess.Popen[str]", deadline: float) -> dict:
    """Block until the gateway prints its PERSONALCLAW_READY line (or timeout)."""
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
        # Surface startup chatter for debugging without failing on it.
        _log(f"gateway> {line}")
    _fail("timed out waiting for the gateway READY line")


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


def _boot_and_probe(py: Path, home: Path) -> None:
    env = dict(os.environ)
    env["PERSONALCLAW_HOME"] = str(home)
    # Loopback-only, no-auth so `/` (the SPA shell) is served without a token —
    # a localhost smoke test; effective_bind() pins NONE mode to 127.0.0.1.
    env["PERSONALCLAW_AUTH_MODE"] = "none"
    env.pop("PYTHONWARNINGS", None)

    _log("booting `personalclaw gateway --test-mode`…")
    proc = subprocess.Popen(
        [str(py), "-m", "personalclaw", "gateway", "--test-mode"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        ready = _read_ready_line(proc, time.time() + _BOOT_TIMEOUT_S)
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
    _assert_no_node()

    scratch = Path(tempfile.mkdtemp(prefix="pc_verify_wheel_"))
    venv_dir = scratch / "venv"
    home_dir = scratch / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    try:
        py = _make_venv(venv_dir)
        _pip_install_wheel(py, wheel)
        _boot_and_probe(py, home_dir)
    finally:
        if args.keep:
            _log(f"kept scratch dir: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    _log(
        "PASS: wheel contract met (SPA packaged, installs Node-free, "
        "gateway serves / + /api/healthz)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
