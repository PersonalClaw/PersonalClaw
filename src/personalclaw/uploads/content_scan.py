"""The content scan every stored upload gets before it is used, run in a child process.

Every route that stores an uploaded file the agent or the library will later read hands it to
:func:`scan_upload` before anything is made from it: a chat attachment, a file uploaded to a
folder, a Knowledge file, a resumable upload when it completes, a file dropped into a workflow
run, a binary artifact's new bytes, a pinned screen frame, a project archive and a backup import,
whether the file arrived in one request or in parts. ``tests/test_stored_upload_scan_census.py``
reads the package for every route that takes a file's bytes from a request, and fails on one that
neither scans them nor says why not. A file dropped in the memory vault's ``raw/`` folder, which no
request brings, is handed to it when a sync takes it into Knowledge
(``knowledge.file_items.take_file``).

What it reads. An upload is read by its bytes, whatever its name or its type says it is, by both
of the scanner's surfaces (the destructive-script rules and the prose-injection rules), as two
windows at most: its first :data:`SCAN_WINDOW` (256 KB) and its last. A file of up to
:data:`WHOLE_FILE_BYTES` (512 KB) is read whole, so no byte of it goes unscanned: its last
window is the rest of it, read on from the first. A larger file gets the large-file policy: what
lies between its two windows is not read. A window that holds a NUL byte is binary and is not
read, because random runs of binary bytes read as false alarms. So an ordinary picture, recording,
video or archive, whose bytes are binary, is never read, and an SVG drawing, which is text, is
read like any text file (measured over the files ordinary software installs: of 6,660 pictures,
recordings, videos and archives none had a window the scan would read; all 3,886 SVGs did, and
none was refused).

What a reader makes of an upload. The text inside a binary window (beside a stray NUL byte, or in
a file a zip stores uncompressed) is not checked here, and neither is the text of a PDF or an
Office document, which sits in compressed parts of the file. So the text a reader makes of an
upload is scanned too, before a model is handed it or it is stored for one (:func:`scan_text`): a
chat or an Inbox attachment's text, a Knowledge document's and a code file's, and a document the
agent opens with ``read_file``. It is read the same way and by the same surfaces, except that no
window of it is skipped for holding a NUL byte: a reader made it, so it is text, and it is what
the model reads.

Why a child process. The scan holds the interpreter lock for long stretches: one of its rules
parses the window as Python, and one parse of a window that reads as Python (source code, JSON
lines) holds the lock for about 0.1 s. In any thread of the gateway that stops the event loop, and
every request with it, for as long (measured beside a 10 ms tick: a 512 KB window of code stopped
it for 0.12 s from a worker thread, and for the whole 0.45 s scan on the loop itself; in a child,
for nothing). So the gateway reads the window in a worker thread, starts its own CLI as
``personalclaw content-scan``, hands it the window on stdin, and reads one JSON line back.

What it answers. Refused content raises :class:`ContentRefused` 422 ``upload_content_refused``,
the scanner's refusal. A window that cannot be read, a child that cannot start, does not answer
within :data:`SCAN_DEADLINE_SECS`, or ends without an answer (the scanner raised on the content
included) raises 503 ``upload_content_unchecked``: the content was not checked, so the upload is
not used (failing closed: a check that did not run is never read as a pass). Either one is a row
in the security event log, naming the route.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.uploads.store import UploadError

if TYPE_CHECKING:
    from aiohttp import web

logger = logging.getLogger(__name__)

#: The hidden CLI subcommand the child runs (``cli.HIDDEN_COMMANDS``).
CHILD_COMMAND = "content-scan"

#: The size of the scan's two windows, a file's first and its last: each is read unless it holds a
#: NUL byte.
SCAN_WINDOW = 256 * 1024

#: The largest file the scan reads whole: twice the window, so a file is read whole or as its
#: two windows, and no file has a range the scan skips while promising to read it.
WHOLE_FILE_BYTES = 2 * SCAN_WINDOW

#: The most the child reads on stdin: a whole file, or two windows and the line between them.
MAX_WINDOW_BYTES = WHOLE_FILE_BYTES + 1

#: How long the child may take, its start included (measured: about 1 s at most on a loaded host).
SCAN_DEADLINE_SECS = 60.0

REFUSED_CODE = "upload_content_refused"
UNCHECKED_CODE = "upload_content_unchecked"
REFUSED = "upload rejected: content failed the safety scan"
NOT_CHECKED = "upload rejected: its content could not be checked, so nothing was made from it"

#: Why the text a reader made of a file is not handed on, by the refusal's code, as the end of a
#: sentence about that file: the same words wherever the text was going.
WITHHELD = {
    REFUSED_CODE: "its text failed the content safety scan",
    UNCHECKED_CODE: "its text could not be checked",
}


def nothing_made(why: str) -> str:
    """The status line of a library item made of nothing from its file, for the reason *why*: the
    scan's (:data:`WITHHELD`), or why a file nobody uploaded was not taken
    (``knowledge.file_items.take_file``)."""
    return f"{why[:1].upper()}{why[1:]}, so nothing was made from it."


class ContentRefused(UploadError):
    """The scan's answer that an upload may not be used, with its wire code.

    A route answers it with :meth:`response`, so every route says the same thing for the same
    refusal."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message, status)
        self.code = code

    @property
    def withheld(self) -> str:
        """Why the text a reader made of a file is not handed on (:data:`WITHHELD`)."""
        return WITHHELD[self.code]

    @property
    def nothing_made(self) -> str:
        """The status line of a library item whose file this withheld (:func:`nothing_made`)."""
        return nothing_made(self.withheld)

    def response(self) -> web.Response:
        """The wire answer: ``{"error": {"code", "message"}}`` with this refusal's status."""
        from personalclaw.http_errors import json_error

        # Each code a literal, so the wire-code registry's check sees it.
        if self.code == REFUSED_CODE:
            return json_error("upload_content_refused", message=self.message, status=self.status)
        return json_error("upload_content_unchecked", message=self.message, status=self.status)


def read_window(path: Path) -> bytes | None:
    """What the scan reads of the file at *path*, by :func:`window_of`'s rule.

    Decided on what is read, not on a size taken first: a file that reads longer than
    :data:`WHOLE_FILE_BYTES` gets its two windows."""
    with open(path, "rb") as fh:
        start = fh.read(WHOLE_FILE_BYTES + 1)
        if len(start) <= WHOLE_FILE_BYTES:
            return _text_of(start[:SCAN_WINDOW], start[SCAN_WINDOW:], contiguous=True)
        fh.seek(-SCAN_WINDOW, os.SEEK_END)
        return _text_of(start[:SCAN_WINDOW], fh.read(SCAN_WINDOW), contiguous=False)


def window_of(data: bytes) -> bytes | None:
    """What the scan reads of an upload held in memory.

    A file is read as two windows at most, its first :data:`SCAN_WINDOW` and its last. One of up
    to :data:`WHOLE_FILE_BYTES` is read whole: its last window is the rest of it, read on from the
    first, so nothing that straddles the two goes unread. A larger one has what lies between its
    windows unread. A window that holds a NUL byte is binary and is not read. ``None`` when
    neither window is read."""
    head, tail, contiguous = _windows(data)
    return _text_of(head, tail, contiguous=contiguous)


def scanned_head(text: str) -> str:
    """The longest beginning of *text* that :func:`scan_text` reads: all of it when the scan reads
    it whole, else its first window. Whatever hands on only the beginning of a text hands on no
    more than this, so nothing it hands on went unread."""
    data = text.encode("utf-8", errors="replace")
    if len(data) <= WHOLE_FILE_BYTES:
        return text
    return data[:SCAN_WINDOW].decode("utf-8", errors="ignore")


def _windows(data: bytes) -> tuple[bytes, bytes, bool]:
    """*data*'s first window and its last, and whether the two are contiguous: they are when the
    whole of it is read, the last being the rest of it."""
    if len(data) <= WHOLE_FILE_BYTES:
        return data[:SCAN_WINDOW], data[SCAN_WINDOW:], True
    return data[:SCAN_WINDOW], data[-SCAN_WINDOW:], False


def _joined(head: bytes, tail: bytes, *, contiguous: bool) -> bytes:
    """Two windows as the scan reads them: as the file has them when they are *contiguous*, else
    with a line between them."""
    return head + tail if contiguous else head + b"\n" + tail


def _text_of(head: bytes, tail: bytes, *, contiguous: bool) -> bytes | None:
    """*head* and *tail* as the scan reads a file's bytes: each one unless it holds a NUL byte."""
    read = [window for window in (head, tail) if window and b"\x00" not in window]
    if len(read) == 2:
        return _joined(head, tail, contiguous=contiguous)
    return read[0] if read else None


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


async def scan_upload(upload: Path | bytes, category: str, *, surface: str) -> None:
    """Scan an upload before anything is made from it: the file stored at *upload*, or its bytes.

    *category* is its upload category (``uploads.policy.category_for``) and *surface* the route
    that took it, both for the security event log: what is read is decided by the bytes alone.
    Returns when it may be used; raises :class:`ContentRefused` when it may not (see the module
    docstring for which status says what).
    """
    read = f"category={category}"
    if isinstance(upload, bytes):
        window = window_of(upload)
    else:
        try:
            window = await asyncio.to_thread(read_window, upload)
        except OSError as exc:
            logger.warning("The upload content scan could not read %s: %s", upload.name, exc)
            raise _unchecked(surface, read, f"unreadable: {type(exc).__name__}") from exc
    if window is not None:
        await _judge(window, surface, read)


async def scan_text(text: str, *, surface: str) -> None:
    """Scan the text a reader made of an upload, before a model is handed it or it is kept for one.

    *surface* is where the text is going, for the security event log: ``attachment`` (a chat),
    ``inbox`` (an Inbox message's attachment), ``knowledge`` (the library) or ``read_file`` (the
    agent's file tool). Read as :func:`scan_upload` reads a file, whole up to
    :data:`WHOLE_FILE_BYTES` and else as its first and last window, except that no window is
    skipped for holding a NUL byte: a reader made this text, so it is text, and the model reads
    all of it. Returns when it may be handed on; raises :class:`ContentRefused` when it may not.
    """
    data = text.encode("utf-8", errors="replace")
    if not data.strip():
        return
    head, tail, contiguous = _windows(data)
    await _judge(_joined(head, tail, contiguous=contiguous), surface, "extracted text")


async def _judge(window: bytes, surface: str, read: str) -> None:
    """The child's verdict on *window*: returns when it may be used, else raises."""
    answer = await _ask_child(window)
    if answer is None:
        raise _unchecked(surface, read, "the scan gave no answer")
    if answer["dangerous"]:
        _audit(surface, read, "rejected", "")
        raise ContentRefused(REFUSED_CODE, REFUSED, 422)


def _unchecked(surface: str, read: str, why: str) -> ContentRefused:
    _audit(surface, read, "error", why)
    return ContentRefused(UNCHECKED_CODE, NOT_CHECKED, 503)


def _audit(surface: str, read: str, outcome: str, error: str) -> None:
    """One security event row per upload, or text a reader made of one, that the scan refused or
    could not check. *read* says which: the upload's category, or ``extracted text``.

    Never raises: the refusal is the control, and a log that could not be written must not turn
    it into a different answer."""
    from personalclaw.sel import sel

    try:
        sel().log_api_access(
            caller=f"uploads.content_scan:{surface}",
            operation="upload_scan",
            outcome=outcome,
            source="uploads",
            resources=read,
            error=error,
        )
    except Exception:  # noqa: BLE001 — the refusal stands whether or not its row was written
        logger.warning("The upload content scan could not record its %s", outcome, exc_info=True)


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
            return answer
    logger.warning(
        "The upload content scan ended without an answer (exit %s): %s",
        proc.returncode,
        mask_child_output(err.decode("utf-8", "replace"), limit=600, tail=True),
    )
    return None


def main() -> int:
    """``personalclaw content-scan``: read a window on stdin, answer one JSON line on stdout.

    Content the scanner raises on gets no answer: the exception ends the child, and the gateway
    reads a child that ended without an answer as content it could not check."""
    window = sys.stdin.buffer.read(MAX_WINDOW_BYTES)
    sys.stdout.write(json.dumps({"dangerous": is_dangerous(window)}) + "\n")
    sys.stdout.flush()
    return 0
