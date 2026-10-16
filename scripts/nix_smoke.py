#!/usr/bin/env python3
"""What the Nix channel's install has to do on a scratch home, asserted: the flake's `serves` check.

``nix flake check`` runs this against the package ``flake.nix`` builds, so the gate the release
runbook and the ``nix`` workflow read proves the install works, not that it compiled:

1. ``personalclaw doctor`` finds the dependencies and pip through the interpreter PersonalClaw
   starts its Python children with (``sys.executable``). nixpkgs' application wrapper left that
   the bare interpreter, which read both as missing on 0.1.3 and failed every child on 0.2.1.
2. ``personalclaw --verbose gateway --test-mode`` boots and prints its READY line.
3. ``GET /api/healthz`` answers ``ok`` for the packaged version.
4. ``GET /`` with the READY line's token is the dashboard: the SPA shell's root mount and the
   bundle it loads, served as JavaScript. A made-up ``/assets/*.js`` must NOT come back as
   JavaScript, or the assertion would hold for anything.
5. ``POST /api/mcp/probe`` finds the agent's own MCP server (``personalclaw-core``, a child
   started as ``python -m personalclaw...``) up, with tools.
6. Nothing in the gateway's log says a child could not import a module.

Exit 0 = all of it holds. Exit 1 = it does not; the log is printed with the token taken out.

Usage: python3 scripts/nix_smoke.py <bin/personalclaw> <version>
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BOOT_TIMEOUT_S = 180
READY = "PERSONALCLAW_READY:"
CORE_SERVER = "personalclaw-core"
#: What a Python child that cannot find its packages writes, in its traceback or our log of it.
IMPORT_FAILURE = re.compile(r"ModuleNotFoundError|No module named")


class Failed(Exception):
    """An assertion about the install did not hold."""


def redact(text: str) -> str:
    """The log with the READY line's token, and any token in a URL, taken out."""
    text = re.sub(r'("token"\s*:\s*")[^"]+', r"\1[redacted]", text)
    return re.sub(r"(token=)[A-Za-z0-9_.~-]+", r"\1[redacted]", text)


def import_failures(lines: list[str]) -> list[str]:
    return [line.rstrip() for line in lines if IMPORT_FAILURE.search(line)]


def _get(url: str, *, token: str = "", data: bytes | None = None) -> tuple[int, str, str]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 (loopback)
            body = response.read().decode("utf-8", "replace")
            return response.status, response.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read().decode("utf-8", "replace")


def check_doctor(exe: str, env: dict[str, str]) -> None:
    # A fresh home has no agent config, so the doctor exits 1 on that row whatever else holds:
    # these two rows are the claim, and they are read as the doctor prints them.
    report = subprocess.run(
        [exe, "doctor"], capture_output=True, text=True, env=env, timeout=300
    ).stdout
    for row in ("deps:", "pip:"):
        line = next((line for line in report.splitlines() if line.strip().startswith(row)), "")
        if "✅" not in line:
            raise Failed(f"`personalclaw doctor` says: {line.strip() or f'no {row} row'}\n{report}")
        print(f"OK: doctor {line.strip()}")


def check_gateway(exe: str, version: str, env: dict[str, str]) -> list[str]:
    """Boot, probe and stop the gateway; return its log."""
    gateway = subprocess.Popen(
        [exe, "--verbose", "gateway", "--test-mode"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    log: list[str] = []
    try:
        assert gateway.stdout is not None
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        ready = None
        while ready is None and time.monotonic() < deadline:
            line = gateway.stdout.readline()
            if not line:
                raise Failed(f"the gateway exited ({gateway.wait()}) before its READY line")
            log.append(line)
            if line.startswith(READY):
                ready = json.loads(line[len(READY) :])
        if ready is None:
            raise Failed(f"no READY line within {BOOT_TIMEOUT_S}s")
        base, token = f"http://127.0.0.1:{ready['port']}", ready["token"]
        print(f"OK: gateway READY on {base}")

        status, _, body = _get(f"{base}/api/healthz")
        health = json.loads(body) if status == 200 else {}
        if health.get("status") != "ok" or health.get("version") != version:
            raise Failed(f"/api/healthz -> {status} {body[:200]!r}, want ok for {version}")
        print(f"OK: /api/healthz -> ok, {version}")

        status, kind, body = _get(f"{base}/", token=token)
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', body)
        if status != 200 or "text/html" not in kind or '<div id="root"' not in body or not scripts:
            raise Failed(f"/ -> {status} {kind!r} is not the dashboard: {body[:300]!r}")
        status, kind, bundle = _get(f"{base}/{scripts[0].lstrip('/')}")
        if status != 200 or "javascript" not in kind:
            raise Failed(f"the dashboard's bundle {scripts[0]} -> {status} {kind!r}")
        made_up = f"/assets/nix-smoke-{uuid.uuid4().hex}.js"
        control, control_kind, _ = _get(f"{base}{made_up}")
        if control == 200 and "javascript" in control_kind:
            raise Failed(
                f"{made_up} came back as JavaScript too, so the check above proves nothing"
            )
        print(f"OK: / -> the dashboard, its bundle {scripts[0]} ({len(bundle)} bytes) as {kind}")

        status, _, body = _get(f"{base}/api/mcp/probe", token=token, data=b"{}")
        servers = (
            {server.get("name"): server for server in json.loads(body)} if status == 200 else {}
        )
        core = servers.get(CORE_SERVER, {})
        if core.get("status") != "ok" or not core.get("tools"):
            raise Failed(f"MCP server {CORE_SERVER} -> {status} {json.dumps(core)[:300]}")
        print(f"OK: MCP server {CORE_SERVER} is up with {len(core['tools'])} tools")
    finally:
        gateway.terminate()
        try:
            rest, _ = gateway.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            gateway.kill()
            rest, _ = gateway.communicate()
        log.extend(rest.splitlines(keepends=True))
    return log


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    exe, version = argv
    scratch = Path(tempfile.mkdtemp(prefix="nix-smoke-"))
    env = {**os.environ, "HOME": str(scratch / "user"), "PERSONALCLAW_HOME": str(scratch / "home")}
    log: list[str] = []
    try:
        check_doctor(exe, env)
        log = check_gateway(exe, version, env)
        failures = import_failures(log)
        if failures:
            raise Failed("a child could not import its packages:\n" + "\n".join(failures))
        print(f"OK: the gateway's log ({len(log)} lines) names no module a child could not find")
    except Failed as exc:
        print(redact("".join(log)), file=sys.stderr)
        print(f"nix_smoke: {redact(str(exc))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
