"""The content scan's child: the process that reads one window and judges it.

``uploads.content_scan`` starts this module for every scan, as ``<interpreter> -m
personalclaw.uploads.scan_child`` (a module the desktop app's bundle runs the same way,
``_frozen_child.CHILD_MODULES``), writes the window to its stdin, and reads one JSON line back.
Content the scanner raises on gets no answer: the exception ends the child, and the gateway reads a
child that ended without an answer as content it could not check.

Every Knowledge write, upload and artifact save waits for this process to start, so it imports the
scanner (``supply_chain``) and nothing else of the package: the folder it sits in imports nothing,
and nothing of the gateway's side of the scan is imported here. The child used to be a command of
the CLI, which imports its whole command tree before it reads its arguments: about a second of
start-up for every scan, around a verdict that takes milliseconds (measured).
``tests/test_a_content_scan_starts_only_the_scanner.py`` holds the child to the modules it needs.

What the scan reads of a file is decided on the gateway's side; the sizes are here, because they
are also the most the child reads.
"""

from __future__ import annotations

import json
import sys

#: The size of the scan's two windows, a file's first and its last: each is read unless it holds a
#: NUL byte.
SCAN_WINDOW = 256 * 1024

#: The largest file the scan reads whole: twice the window, so a file is read whole or as its
#: two windows, and no file has a range the scan skips while promising to read it.
WHOLE_FILE_BYTES = 2 * SCAN_WINDOW

#: The most the child reads on stdin: a whole file, or two windows and the line between them.
MAX_WINDOW_BYTES = WHOLE_FILE_BYTES + 1


def is_dangerous(window: bytes) -> bool:
    """Whether either scanner surface refuses *window*.

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


def main() -> int:
    """Read a window on stdin, answer one JSON line on stdout."""
    window = sys.stdin.buffer.read(MAX_WINDOW_BYTES)
    sys.stdout.write(json.dumps({"dangerous": is_dangerous(window)}) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
