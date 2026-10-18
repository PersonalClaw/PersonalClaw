"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_website_installer.py``

Its checks:
``tests/rails/test_website_installer_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


_INSTALLER = _ROOT / "deploy" / "website" / "install.sh"


#: The URL the one-liner is served from. The live legs in ``breadth.yml`` must name it.
SERVED_URL = "https://personalclaw.dev/install"


#: Turns "the tool this case needs is absent" from a skip into a failure. Set by the
#: ``lint`` job, which installs shellcheck and dash first. NOT set on a contributor's
#: laptop, where reding the suite over a missing linter would be hostile.
REQUIRE_ENV = "PERSONALCLAW_REQUIRE_INSTALL_PROOF"


def jobs(text: str) -> dict[str, str]:
    """Job id → the block of ``text`` belonging to it.

    A job id is the only key at two-space indent in a workflow: a job's own keys
    (``runs-on``, ``env``, ``steps``) sit at four and steps deeper still, so the scan needs
    no YAML.
    """
    found: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line.strip() and not line.startswith(" ") and not line.startswith("#"):
            break  # back to a top-level key: the jobs mapping ended
        header = re.match(r"^ {2}([A-Za-z][\w-]*):\s*(#.*)?$", line)
        if header:
            current = header.group(1)
            found[current] = []
            continue
        if current is not None:
            found[current].append(line)
    return {k: "\n".join(v) for k, v in found.items()}


def header_block(text: str) -> str:
    """The leading comment block of a shell script (the shebang and every ``#`` line after).

    Stops at the first line that is neither a comment nor blank, so only the header — the
    part that makes claims about CI — is returned.
    """
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            out.append(line)
            continue
        break
    return "\n".join(out)


def header_claims(text: str) -> set[tuple[str, str]]:
    """``(workflow filename, job id)`` pairs claimed by a script's header.

    A claim is a ``*.yml`` filename and a backtick-quoted identifier on the SAME header
    line — the shape the header uses to tabulate its coverage. Derived rather than pinned:
    rewording the prose is free, naming a job that does not exist is not.
    """
    claims: set[tuple[str, str]] = set()
    for line in header_block(text).splitlines():
        workflows = re.findall(r"\b([A-Za-z][\w.-]*\.yml)\b", line)
        ids = re.findall(r"`([A-Za-z][\w-]*)`", line)
        for wf in workflows:
            for job in ids:
                claims.add((wf, job))
    return claims


@pytest.fixture(scope="module")
def installer_text() -> str:
    return _INSTALLER.read_text(encoding="utf-8")


def code_only(body: str) -> str:
    """``body`` with whole-line ``#`` comments removed.

    Every "the leg does X" assertion below must read the leg's actual YAML and shell, not its
    prose. This job is heavily commented and those comments NAME the things being asserted —
    ``UV_TOOL_DIR``, ``install.sh.sha256``, even ``continue-on-error`` (in a comment
    explaining why it must not be used). Matching against the raw body let the commentary
    satisfy the assertions: measured, ``test_it_redirects_uv_tool_dirs`` passed on a comment,
    and the ``continue-on-error`` guard failed on one. Documenting a rail is not having it.
    """
    return "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))


def fetch_guards(body: str) -> list[str]:
    """Every ``if ! curl …; then … fi`` block in a job body, as text.

    The unit that matters for "an inconclusive run must not be green" is not the job and not
    a word — it is each individual guard around a network fetch. A whole-body substring check
    is too loose to notice a regression *inside* one guard: renaming the message while an
    ``::error title=… unproven::`` annotation survived elsewhere in the same job left the
    assertion passing, which is how this helper came to exist.

    Nesting is not handled because these guards do not nest; the first ``fi`` at the guard's
    own indent closes it.
    """
    lines = body.splitlines()
    guards: list[list[str]] = []
    current: list[str] | None = None
    indent = 0
    for line in lines:
        if current is None:
            if re.search(r"if\s+!\s+curl\b", line):
                current = [line]
                indent = len(line) - len(line.lstrip())
            continue
        current.append(line)
        if line.strip() == "fi" and (len(line) - len(line.lstrip())) == indent:
            guards.append(current)
            current = None
    if current is not None:  # unterminated — hand it back so the caller can fail on it
        guards.append(current)
    return ["\n".join(g) for g in guards]


def _live_leg(full_text: str) -> tuple[str, str]:
    """The ``breadth.yml`` job that exercises the served one-liner — located by BEHAVIOUR.

    Found by "the job that mentions the served URL", not by its id, so renaming it stays
    green while deleting it does not.
    """
    matches = {jid: body for jid, body in jobs(full_text).items() if SERVED_URL in body}
    assert matches, (
        f"no job in breadth.yml mentions {SERVED_URL}. The installer's live rail is gone, "
        "and the header's smoke claim is false again."
    )
    assert len(matches) == 1, f"expected one live installer leg, found {sorted(matches)}"
    return next(iter(matches.items()))
