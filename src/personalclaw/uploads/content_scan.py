"""The content scan a resumable upload gets when it completes, run in a child process.

A completed upload of a text-like file (a document, an archive, or a file of no known kind) is
scanned before it is handed on: a window of it, its first 256 KB and, for a file over 512 KB, its
last 256 KB, by both of the scanner's surfaces (the destructive-script rules and the
prose-injection rules). A file whose content is refused is not used. Media is not scanned, nor is
a binary file (a NUL byte in the window): its bytes cannot reveal a payload, and random runs of
them read as false alarms.

Why a child process. The scan holds the interpreter lock for long stretches: one of its rules
parses the window as Python, and one parse of a window that reads as Python (source code, JSON
lines) holds the lock for about 0.1 s. In any thread of the gateway that stops the event loop, and
every request with it, for as long (measured beside a 10 ms tick: a 512 KB window of code stopped
it for 0.12 s from a worker thread, and for the whole 0.45 s scan on the loop itself; in a child,
for nothing). So the gateway reads the window in a worker thread, starts its own CLI as
``personalclaw content-scan``, hands it the window on stdin, and reads one JSON line back.

What it answers. A refused window raises :class:`UploadError` 422, the scanner's refusal. A child
that cannot start, does not answer within :data:`SCAN_DEADLINE_SECS`, or ends without an answer
raises 503: the content was not checked, so the upload is not used (failing closed: a check that
did not run is never read as a pass). Content the scanner itself cannot process is let through, as
it was when the scan ran in the gateway, and the child says so in the gateway's log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from personalclaw.uploads.store import UploadError

logger = logging.getLogger(__name__)

#: The hidden CLI subcommand the child runs (``cli.HIDDEN_COMMANDS``).
CHILD_COMMAND = "content-scan"

#: How much of the file the scan reads from its start, and from its end.
SCAN_WINDOW = 256 * 1024

#: The upload categories the scan reads: the text-like ones, where a script or an injected
#: instruction can sit. Media is never scanned.
SCANNABLE_CATEGORIES = frozenset({"document", "archive", "other"})

#: How long the child may take, its start included (measured: about 1 s at most on a loaded host).
SCAN_DEADLINE_SECS = 60.0

REFUSED = "upload rejected: content failed the safety scan"
NOT_CHECKED = "upload rejected: its content could not be checked, so nothing was made from it"


def read_window(path: Path) -> bytes | None:
    """The bytes the scan reads from *path*: its head and, past twice the window, its tail.

    ``None`` for a binary file (a NUL byte in the window)."""
    with open(path, "rb") as fh:
        head = fh.read(SCAN_WINDOW)
        size = path.stat().st_size
        if size > 2 * SCAN_WINDOW:
            fh.seek(size - SCAN_WINDOW)
            tail = fh.read(SCAN_WINDOW)
        else:
            tail = b""
    window = head + b"\n" + tail
    return None if b"\x00" in window else window


def is_dangerous(window: bytes) -> bool:
    """Whether either scanner surface refuses *window*. Runs in the child.

    An uploaded file is untrusted content, so it gets both surfaces: ``script`` (the
    destructive-script rules: a download piped to a shell, a decoded payload run, a recursive
    delete) and ``manifest`` (prose injection and invisible characters). Scanning ``manifest``
    alone let classic shell payloads through as clean."""
    from personalclaw.supply_chain import SkillScanner, Verdict

    text = window.decode("utf-8", errors="replace")
    scanner = SkillScanner()
    return any(
        scanner.scan_text(text, surface=surface).verdict is Verdict.DANGEROUS
        for surface in ("script", "manifest")
    )


def scan_argv() -> list[str]:
    """The child's argv: this install's own CLI running :data:`CHILD_COMMAND`.

    ``sys.executable -m personalclaw`` rather than a console script on PATH, so the child is this
    install; a frozen (PyInstaller) bundle has no ``-m``: its executable IS the CLI."""
    from personalclaw.self_update import is_frozen

    head = [sys.executable] if is_frozen() else [sys.executable, "-m", "personalclaw"]
    return [*head, CHILD_COMMAND]


async def scan_upload(path: Path, category: str) -> None:
    """Scan the completed upload at *path*, of upload category *category*.

    Returns when it may be used; raises :class:`UploadError` when it may not (see the module
    docstring for which status says what)."""
    if category not in SCANNABLE_CATEGORIES:
        return
    try:
        window = await asyncio.to_thread(read_window, path)
    except OSError:
        # Unchanged from the scan in the gateway: a window that cannot be read is let through.
        logger.debug("the content scan could not read %s", path.name, exc_info=True)
        return
    if window is None:
        return
    answer = await _ask_child(window)
    if answer is None:
        raise UploadError(NOT_CHECKED, 503)
    if answer.get("dangerous") is True:
        raise UploadError(REFUSED, 422)


async def _ask_child(window: bytes) -> dict[str, Any] | None:
    """The child's answer for *window*, or ``None`` when it gave none."""
    from personalclaw.cancellation import kill_timed_out
    from personalclaw.config.loader import config_dir
    from personalclaw.sandbox import build_child_env
    from personalclaw.security import mask_child_output

    # The child allowlist: it reads what someone uploaded, and needs none of the gateway's own.
    env = build_child_env(
        site="upload content scan", extra={"PERSONALCLAW_HOME": str(config_dir())}
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *scan_argv(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            # Its own process group, so a kill reaches anything it started.
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning("The upload content scan could not start: %s", exc)
        return None
    try:
        async with asyncio.timeout(SCAN_DEADLINE_SECS):
            out, err = await proc.communicate(window)
    except TimeoutError:
        logger.warning("The upload content scan did not answer within %.0f s", SCAN_DEADLINE_SECS)
        await kill_timed_out(proc)
        return None
    except BaseException:
        # Cancelled (the gateway stopping): nothing waits for the child any more, so it goes.
        await kill_timed_out(proc)
        raise
    for line in reversed(out.decode("utf-8", "replace").splitlines()):
        try:
            answer = json.loads(line)
        except ValueError:
            continue
        if isinstance(answer, dict) and isinstance(answer.get("dangerous"), bool):
            if answer.get("unscannable"):
                logger.info(
                    "The upload content scan could not process a window (%s); it was let through",
                    answer["unscannable"],
                )
            return answer
    logger.warning(
        "The upload content scan ended without an answer (exit %s): %s",
        proc.returncode,
        mask_child_output(err.decode("utf-8", "replace"), limit=600, tail=True),
    )
    return None


def main() -> int:
    """``personalclaw content-scan``: read a window on stdin, answer one JSON line on stdout."""
    window = sys.stdin.buffer.read(2 * SCAN_WINDOW + 2)
    answer: dict[str, Any]
    try:
        answer = {"dangerous": is_dangerous(window)}
    except Exception as exc:  # noqa: BLE001 — content the scanner cannot process is let through
        answer = {"dangerous": False, "unscannable": type(exc).__name__}
    sys.stdout.write(json.dumps(answer) + "\n")
    sys.stdout.flush()
    return 0
