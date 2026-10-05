"""Every Knowledge write, upload and artifact save waits for a content scan's child to start, so the
child starts the scanner and nothing else of the package.

The scan runs in a child process (``uploads.content_scan``): the gateway writes the window to its
stdin and reads one verdict back. The child was the CLI's hidden ``content-scan`` command, and the
CLI imports its whole command tree before it reads its arguments: about 1,100 modules and a second
of start-up for every scan, around a verdict that takes milliseconds (measured). A library of 110
notes the agent wrote took over 100 s to build that way.

So the child is a module of its own, started as ``<interpreter> -m`` as the package's other child
modules are, and the package modules it imports are the scanner's. The test reads them from the
child's own ``-X importtime`` report while it scans an ordinary note, started with the argv and the
environment the gateway gives it.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from personalclaw.sandbox import build_child_env
from personalclaw.uploads import content_scan

#: Every module of the package the child imports, and why: each one is paid for on every scan.
#: The child's own module (``uploads.scan_child``) is not among them: ``-m`` runs it as
#: ``__main__``.
_THE_SCANNER = {
    "personalclaw": "the package, which every module of it is imported under",
    "personalclaw.uploads": "the folder the child runs from, whose __init__ imports nothing",
    "personalclaw.supply_chain": "the scanner",
    "personalclaw.signing": "the signature state every scan report carries",
    "personalclaw.credential_locations": "the locations the scanner's credential rule looks for",
}

_IMPORTED = re.compile(r"^import time:\s+\d+ \|\s+\d+ \|\s*(\S+)\s*$")

#: The clean and refused content of the upload scan tests: a list with and without a right-to-left
#: override, an invisible character the scanner refuses.
_CLEAN = "Shopping list for Saturday: eggs, flour, apples.\n".encode()
_REFUSED = "Shopping list for Saturday: \u202eeggs\u202c, flour, apples.\n".encode()


def _scan(tmp_path, window: bytes, *flags: str) -> subprocess.CompletedProcess[bytes]:
    """Run the child as ``_ask_child`` starts it (its argv, its environment), with *flags* given
    to the interpreter, and hand it *window*."""
    argv = content_scan.scan_argv()
    env = build_child_env(site="upload content scan", extra={"PERSONALCLAW_HOME": str(tmp_path)})
    return subprocess.run(
        [argv[0], *flags, *argv[1:]],
        input=window,
        capture_output=True,
        timeout=120,
        env=env,
        start_new_session=True,
    )


def test_a_scan_imports_the_scanner_and_nothing_else_of_the_package(tmp_path):
    done = _scan(tmp_path, _CLEAN, "-X", "importtime")
    assert json.loads(done.stdout) == {"dangerous": False}, done.stderr[-2000:]

    imported = {
        found.group(1)
        for line in done.stderr.decode("utf-8", "replace").splitlines()
        if (found := _IMPORTED.match(line))
    }
    ours = {name for name in imported if name.split(".")[0] == "personalclaw"}
    assert "personalclaw.supply_chain" in ours, "the floor: the report names the scanner it ran"
    beyond, missing = sorted(ours - set(_THE_SCANNER)), sorted(set(_THE_SCANNER) - ours)
    assert not beyond and not missing, (
        "every Knowledge write, upload and artifact save waits for each module the scan's child "
        "imports; add one to _THE_SCANNER only when the scan needs it, and say why. "
        f"Imported beyond the scanner ({len(beyond)}): {beyond[:20]}; "
        f"listed and not imported: {missing}"
    )


@pytest.mark.parametrize(("window", "dangerous"), [(_CLEAN, False), (_REFUSED, True)])
def test_the_child_answers_what_the_scanner_decides(tmp_path, window, dangerous):
    done = _scan(tmp_path, window)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.decode().splitlines() == [json.dumps({"dangerous": dangerous})]
