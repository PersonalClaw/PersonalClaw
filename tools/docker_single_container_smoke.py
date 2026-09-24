#!/usr/bin/env python3
"""Assert the single-container `docker run` in README.md reaches a USABLE dashboard — DIST-15.

`DIST-15`'s `done_when` is deliberately built to reject the cheap answer. A healthz-only
assertion is **explicitly insufficient**, because until this atom landed the gateway image
would have passed one: `Dockerfile.backend` copied `src/` + `pyproject.toml` + `setup.py`
and nothing else, and `setup.py`'s `BuildWithWeb` grafts `web/dist` into the installed
package only if `web/dist` is already in the build tree — which `.dockerignore` guarantees
it is not. So the image answered `/api/healthz` 200 and served **no dashboard at all**.
That is the exact failure this file exists to catch, and why it asserts an asset:

  1. the container starts from the command **read out of README.md**, in a scratch
     directory with no PersonalClaw files in it;
  2. `GET /api/healthz` → 200 with `status: ok`;
  3. `GET /` → 200 HTML carrying the SPA shell's root mount point;
  4. the FIRST script the shell references → 200 **with a JavaScript content-type**.

**Step 4 is the whole point and the content-type is not decoration.** The SPA serves an
`index.html` fallback for unknown paths, so a bundle that never made it into the image
answers the asset request with 200 + `text/html` — a status-only assertion calls that
green. Hence the negative control in :func:`_assert_asset`: a fabricated `/assets/*.js`
must NOT come back as JavaScript. Without it "200 and a JS content-type" cannot be
distinguished from "200 for literally anything", and the rail is vacuous.

**Why `/` is fetched WITH a token and the asset WITHOUT one.** Both are the product's
real behaviour, not a workaround: under the default `local_token` auth mode a tokenless
page GET gets the paste-token gate (`dashboard/token_auth.py:_deny`), while `/assets/`,
`/fonts/`, `/sprites/` and `/vendor/` are in `_BYPASS_PREFIXES` and are served without a
session. So the token comes from `docker exec … personalclaw token` — the same step
`docs/guides/containers.md` documents — and the asset is fetched cold. Arming
`PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1` or `PERSONALCLAW_AUTH_MODE=none` to make the bare
`curl` in the `done_when` literal would have been a weaker rail against a weaker product:
`AUTH_MODE=none` pins the bind to loopback *inside* the container (so `-p` reaches
nothing) unless `PERSONALCLAW_BIND_HOST` also overrides it, and that combination — no
auth on `0.0.0.0` — is precisely what the loopback invariant exists to prevent.

**The command is READ, never re-spelled.** Re-typing it here would let README.md drift into
being wrong while this file stayed green, which is the failure mode DIST-17 is about. The
only things rewritten are the image ref (`--image`, so CI can test the image built from the
commit under review rather than whatever `:latest` resolves to), the container name and the
volume name — the last two for safety: a run must never `docker rm -f personalclaw` or
remove a `personalclaw_home` volume a reader of this repo is actually using.

Exit 0 = the clause holds. Exit 1 = it does not. Exit 2 = the run could not measure
(no docker, port in use, README command not found) — never a pass, and a different
finding, so it says so separately.

Usage:
    python3 tools/docker_single_container_smoke.py [--image REF] [--readme README.md]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import NoReturn

_BOOT_TIMEOUT_S = 180.0
_HTTP_TIMEOUT_S = 15.0
# The SPA's mount point. `web/index.html` carries it and every build preserves it, so it
# distinguishes the real shell from the 403 paste-token gate and from an nginx error page.
_SHELL_MARKER = '<div id="root"'
_SCRIPT_RE = re.compile(r"""<script[^>]*\bsrc=["']([^"']+)["']""", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[?&]token=([^\s&]+)")
_JS_CONTENT_TYPES = ("javascript", "ecmascript")
#: What replaces a token span in captured output. A marker, never a deletion: the dump
#: exists to be read, so a reader must still see THAT a token was printed and where.
_REDACTED = "<redacted>"


class Unmeasurable(Exception):
    """The run could not be performed, so it proves nothing either way (exit 2)."""


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fail(msg: str) -> NoReturn:
    """Exit 1: the clause does not hold. `NoReturn` so callers may treat it as terminal."""
    print(f"FAIL: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def redact_secrets(text: str) -> str:
    """Scrub query-string session tokens out of text this tool is about to print.

    **Why this exists.** The gateway's startup banner prints the *authenticated* dashboard
    URL — ``gateway.py:4519-4531`` mints a session token, ``dashboard/origin.py:375``
    builds ``{base}?token={token}`` and ``format_dashboard_urls`` emits it as the line
    ``   http://localhost:10000?token=…`` — so the container's own stdout carries a live
    session token, and :func:`_dump_container_logs` then prints 40 lines of that stdout into
    a GitHub Actions log on a **public** repo, on every push to `main` touching `README.md`
    or `web/**`. Low impact (loopback-bound container, destroyed seconds later), but a
    standing scheduled producer of credential-shaped strings in a world-readable log.

    **Why in Python and not ``::add-mask::``.** A mask directive does not retroactively
    scrub text already written, and the dump sits inside a ``::group::`` fold — so a mask
    emitted around the fold cannot help. The scrub has to happen on the string, before
    ``print``.

    Reuses :data:`_TOKEN_RE` — the pattern this file already needs in order to EXTRACT the
    token in :func:`_mint_token` — so there is one spelling of "a token in a URL", not two
    that can drift. Only the matched span is rewritten: text carrying no token comes back
    byte-identical, because a redactor that mangles ordinary log lines has destroyed the
    diagnostic the dump exists for.
    """
    return _TOKEN_RE.sub(lambda m: f"{m.group(0)[0]}token={_REDACTED}", text)


# ---------------------------------------------------------------------------
# Reading the command out of README.md
# ---------------------------------------------------------------------------


def readme_docker_run(readme: Path) -> str:
    """The single-container `docker run` command, as README.md spells it.

    Exactly one is expected. Zero means the README half of the clause regressed (the
    state DIST-15 started from: ``grep -c 'docker run' README.md`` was 0). More than one
    means there is now a choice to make and this tool must not guess which command a
    reader would copy.
    """
    try:
        lines = readme.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise Unmeasurable(f"cannot read {readme}: {exc}") from exc

    commands: list[str] = []
    in_fence = False
    buffer: list[str] = []
    for line in lines:
        if line.startswith("```"):
            if in_fence:
                block = "\n".join(buffer).strip()
                # Join a backslash-continued command into the one line a reader pastes.
                # The leading `\s*` collapses the space before the backslash too, so the
                # joined form is what a shell sees rather than one with doubled gaps.
                block = re.sub(r"\s*\\\n\s*", " ", block)
                if block.startswith("docker run "):
                    commands.append(block)
                buffer = []
            in_fence = not in_fence
            continue
        if in_fence:
            buffer.append(line)

    if not commands:
        raise Unmeasurable(
            f"no `docker run` command found in {readme} — the clause requires ONE command "
            "copied verbatim from README.md, so there is nothing to test"
        )
    if len(commands) > 1:
        raise Unmeasurable(
            f"{len(commands)} `docker run` commands in {readme}; this rail tests the single "
            "documented one and will not guess. Narrow it, or teach this tool the anchor."
        )
    return commands[0]


#: `docker run` flags that consume the NEXT token. Only what the documented command may
#: plausibly use — an unknown value-taking flag would make the scan below mistake its value
#: for the image ref, so this raises rather than guessing (see :func:`retarget`).
_VALUE_FLAGS = frozenset(
    {
        "--name",
        "-p",
        "--publish",
        "-v",
        "--volume",
        "--mount",
        "-e",
        "--env",
        "--env-file",
        "--network",
        "--restart",
        "-u",
        "--user",
        "-w",
        "--workdir",
        "--label",
        "--add-host",
        "--entrypoint",
        "--platform",
        "--pull",
        "--memory",
        "--cpus",
        "--health-cmd",
    }
)


def retarget(command: str, image: str | None, name: str, volume: str) -> tuple[list[str], str]:
    """Rewrite only the image ref, `--name` and the named volume.

    The image is found POSITIONALLY — the first bare token that is neither a flag nor a
    flag's value — not by pattern-matching for ``ghcr.io`` or for ``a/b:c``. A pattern
    would also match ``personalclaw_home:/data``, and retargeting the volume spec as if it
    were the image is the kind of bug that makes a rail test the wrong thing quietly.
    """
    argv = shlex.split(command)
    if argv[:2] != ["docker", "run"]:
        raise Unmeasurable(f"not a `docker run` command: {command}")

    used_image = ""
    i = 2
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("-"):
            flag = arg.split("=", 1)[0]
            if "=" in arg:  # --flag=value is self-contained
                i += 1
                continue
            if flag in _VALUE_FLAGS:
                value = argv[i + 1]
                if flag == "--name":
                    argv[i + 1] = name
                elif (
                    flag in ("-v", "--volume")
                    and ":" in value
                    and not value.startswith((".", "/", "~"))
                ):
                    argv[i + 1] = f"{volume}:{value.split(':', 1)[1]}"
                i += 2
                continue
            i += 1  # a boolean flag (-d, --rm, -it, …)
            continue
        used_image = arg
        if image:
            argv[i] = image
            used_image = image
        break
    if not used_image:
        raise Unmeasurable(
            f"could not find the image reference in: {command} — if a new value-taking flag "
            "was added, teach _VALUE_FLAGS about it rather than letting this scan guess"
        )
    return argv, used_image


def published_port(argv: list[str]) -> int:
    """The HOST port the command publishes (`-p [host-ip:]host:container`)."""
    for i, arg in enumerate(argv):
        if arg in ("-p", "--publish"):
            parts = argv[i + 1].split(":")
            if len(parts) >= 2:
                return int(parts[-2])
    raise Unmeasurable("the README command publishes no host port")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _get(url: str) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "dist15-smoke"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:  # noqa: S310
            return (
                resp.status,
                resp.headers.get("Content-Type", ""),
                resp.read().decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as exc:
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
        return exc.code, ctype, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, "", f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=_BOOT_TIMEOUT_S
    )
    if check and proc.returncode != 0:
        raise Unmeasurable(f"`docker {' '.join(args)}` failed: {proc.stderr.strip()}")
    return proc


def _require_docker() -> None:
    try:
        _docker("version", "--format", "{{.Server.Version}}")
    except FileNotFoundError as exc:
        raise Unmeasurable("docker is not on PATH — this clause is about a Docker host") from exc


def _require_free_port(port: int) -> None:
    with socket.socket() as sock:
        sock.settimeout(2)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise Unmeasurable(
                f"host port {port} is already serving; the README command would fail to "
                "bind and the result would say nothing about the image"
            )


# ---------------------------------------------------------------------------
# The assertions
# ---------------------------------------------------------------------------


def _await_healthz(base: str, name: str) -> None:
    deadline = time.time() + _BOOT_TIMEOUT_S
    last = ""
    while time.time() < deadline:
        status, _ctype, body = _get(f"{base}/api/healthz")
        if status == 200:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            if payload.get("status") != "ok":
                _fail(f"/api/healthz → 200 but body is not ok: {body!r}")
            _log(f"OK: /api/healthz → 200 {payload}")
            return
        last = f"status={status} body={body[:200]!r}"
        if (
            _docker("inspect", "-f", "{{.State.Running}}", name, check=False).stdout.strip()
            != "true"
        ):
            _fail(f"the container exited before serving /api/healthz (last: {last})")
        time.sleep(2)
    _fail(f"/api/healthz never returned 200 within {_BOOT_TIMEOUT_S:.0f}s (last: {last})")


def _mint_token(name: str) -> str:
    proc = _docker("exec", name, "personalclaw", "token", check=False)
    match = _TOKEN_RE.search(proc.stdout)
    if not match:
        # stdout provably carries no token URL (the search above just failed on it), but
        # stderr was never searched and is echoed verbatim into the same public log, so it
        # goes through the same scrub.
        _fail(
            "`docker exec … personalclaw token` printed no token URL, so the documented way "
            f"into the dashboard does not work: rc={proc.returncode} "
            f"stdout={proc.stdout.strip()!r} "
            f"stderr={redact_secrets(proc.stderr).strip()!r}"
        )
    return match.group(1)


def _assert_shell(base: str, token: str) -> str:
    """`GET /` is the SPA shell. Returns the first script src it references."""
    status, ctype, body = _get(f"{base}/?token={token}")
    if status != 200:
        _fail(f"/ returned {status} (want 200 HTML) — body: {body[:300]!r}")
    if "text/html" not in ctype.lower():
        _fail(f"/ returned content-type {ctype!r} (want text/html)")
    if _SHELL_MARKER not in body:
        _fail(
            f"/ returned 200 HTML without {_SHELL_MARKER!r} — this is the image's failure "
            "mode, not a missing marker: an SPA-less gateway still answers / with HTML "
            f"(the paste-token gate). Body: {body[:300]!r}"
        )
    scripts = _SCRIPT_RE.findall(body)
    if not scripts:
        _fail("/ returned the SPA shell but it references no script — there is no bundle to load")
    _log(f"OK: / → 200 HTML SPA shell referencing {len(scripts)} script(s)")
    return scripts[0]


def _assert_asset(base: str, src: str) -> None:
    """The first referenced script is really served, as JavaScript. With a negative control."""
    url = src if src.startswith("http") else f"{base}/{src.lstrip('/')}"
    status, ctype, body = _get(url)
    if status != 200:
        _fail(f"the shell's first script {src} returned {status} (want 200)")
    if not any(kind in ctype.lower() for kind in _JS_CONTENT_TYPES):
        _fail(
            f"{src} returned 200 but content-type {ctype!r} is not JavaScript — the SPA "
            "fallback served the shell in place of a bundle that is not in the image. "
            f"Body starts: {body[:200]!r}"
        )
    _log(f"OK: {src} → 200 {ctype} ({len(body)} bytes)")

    # Negative control on the discriminator itself: if a fabricated asset ALSO comes back
    # as JavaScript, the assertion above passes for any input and proves nothing.
    bogus = f"/assets/dist15-control-{uuid.uuid4().hex}.js"
    status, ctype, _body = _get(f"{base}{bogus}")
    if status == 200 and any(kind in ctype.lower() for kind in _JS_CONTENT_TYPES):
        raise Unmeasurable(
            f"control failed: a fabricated asset ({bogus}) also answered 200 {ctype!r}, so "
            "the assertion above cannot tell a real bundle from a catch-all"
        )
    _log(f"OK (control): {bogus} → {status} {ctype!r} — not served as JavaScript")


def _dump_container_logs(name: str) -> None:
    """Print the container's tail for diagnosis, with session tokens redacted.

    A function rather than four inline lines in ``main``'s ``finally`` so the redaction is
    reachable in a test without a Docker daemon: the rail in
    ``tests/test_docker_single_container.py`` drives THIS, with ``_docker`` faked, and
    asserts on what actually reaches stdout. Grepping the source for a call would pass on a
    helper that was defined and never wired in, which is the exact gap this change closes.
    """
    logs = _docker("logs", "--tail", "40", name, check=False)
    print("::group::container logs (last 40 lines, session tokens redacted)", flush=True)
    print(redact_secrets(logs.stdout + logs.stderr), flush=True)
    print("::endgroup::", flush=True)


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", default="README.md", type=Path)
    parser.add_argument(
        "--image",
        default=None,
        help="image ref to substitute for the README's (e.g. a tag built from this commit)",
    )
    args = parser.parse_args()

    suffix = f"dist15-{os.getpid()}"
    name = f"personalclaw-smoke-{suffix}"
    volume = f"personalclaw_home_smoke_{os.getpid()}"

    try:
        command = readme_docker_run(args.readme)
        argv, image = retarget(command, args.image, name, volume)
        port = published_port(argv)
        _require_docker()
        _require_free_port(port)
    except Unmeasurable as exc:
        print(f"UNMEASURABLE: {exc}", file=sys.stderr)
        return 2

    _log(f"README command: {command}")
    _log(f"running as:     {shlex.join(argv)}")

    # A scratch directory with no PersonalClaw files, which is the clause's subject: the
    # command must not be depending on a checkout it happens to be standing in.
    scratch = tempfile.mkdtemp(prefix="dist15-scratch-")
    base = f"http://127.0.0.1:{port}"
    started = False
    try:
        proc = subprocess.run(
            argv, cwd=scratch, capture_output=True, text=True, timeout=_BOOT_TIMEOUT_S
        )
        if proc.returncode != 0:
            _fail(
                f"the README command failed from a scratch directory (rc={proc.returncode}): "
                f"{proc.stderr.strip()!r}"
            )
        started = True
        _log(f"container {name} started from {scratch} (image {image})")

        _await_healthz(base, name)
        token = _mint_token(name)
        first_script = _assert_shell(base, token)
        _assert_asset(base, first_script)
    except Unmeasurable as exc:
        print(f"UNMEASURABLE: {exc}", file=sys.stderr)
        return 2
    finally:
        if started:
            _dump_container_logs(name)
            _docker("rm", "-f", "-v", name, check=False)
            _docker("volume", "rm", "-f", volume, check=False)

    _log("")
    _log("PASS: one `docker run` from README.md reaches a usable dashboard — healthz 200, the")
    _log("      SPA shell, and its first bundle served as JavaScript.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
