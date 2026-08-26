"""Locate an installed Chromium for BA-2's behavioural proofs — by shape, not by literal.

Two of ``BROWSE-AUTOMATION`` `BA-2`'s ``done_when`` clauses cannot be reached by any assertion
about source text: *"the injected safety script makes a test page's ``fetch()`` /
``media.play()`` / ``navigator.bluetooth`` throw or return blocked"* and *"client-side redirects
are re-evaluated per ``Page.frameNavigated``"*. Both are proven only by
``tests/test_browse_safety_script.py`` (layer 2) and ``tests/test_browse_cdp_live.py``, which
launch a real browser over raw CDP. When no browser is found those files ``pytest.skip`` — and a
skip is counted as a pass, not as a gap. **So the reach of this module is the reach of BA-2's
behavioural proof**, and anything it fails to find is a clause that quietly stopped being tested.

Both files used to carry their own copy of the search, spelled as two absolute literals::

    ~/Library/Caches/ms-playwright/chromium_headless_shell-1234
        /chrome-headless-shell-mac-arm64/chrome-headless-shell

That produced two silent-skip failure modes, neither of which could ever go red:

* **The revision was baked in.** ``package.json`` allows ``playwright: "^1.62.1"``, so the first
  bump moves the directory off ``-1234``; the literal then matches nothing, ``_chrome_path()``
  returns ``None``, and all 22 behavioural tests skip. A rail that matches nothing looks clean.
* **The OS and CPU were baked in.** Only macOS arm64 was spelled out. CI runs the pytest job on
  Linux, so on the one machine that gates every merge the behavioural layer had never executed
  even once — its green was 22 skips.

The fix is to search by *shape*: the per-OS Playwright roots, a glob over ``chromium*-<any
revision>``, and a glob for the executable's own name inside it (rather than an enumeration of
per-arch directory names, which is the same brittleness one level down).

``tests/test_browse_behavioural_proof_is_reachable.py`` is the rail over this module. Its
load-bearing case asks the question the literals answered wrongly — *"a Chromium is installed
here; can we see it?"* — using Playwright's own ``INSTALLATION_COMPLETE`` marker as the evidence
that one exists, so the floor is not computed from the value it is meant to pin.

**Absence is still allowed to skip**, because a machine with no browser genuinely cannot run the
proof and failing there would take the whole suite down over an environment. What must not be
allowed is for that skip to be *unnoticed* — so :data:`REQUIRE_ENV` turns it into a failure, which
is how a gate (or a future CI job that does install a browser) demands the proof actually ran.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

#: Point this at a browser binary to use it verbatim. The escape hatch for a Chrome that came
#: from somewhere other than Playwright (a distro package, a Nix store path, a CI cache).
CHROME_ENV = "PERSONALCLAW_TEST_CHROME"

#: Set this (to anything but ``0``/``false``/``no``/empty) and a missing browser or missing
#: ``websockets`` FAILS instead of skipping. Nothing in the repo sets it by default: a developer
#: without a browser should still be able to run the suite. It exists so that a gate which
#: *intends* to prove the behavioural clauses cannot pass by skipping them.
REQUIRE_ENV = "PERSONALCLAW_REQUIRE_BROWSE_PROOF"

#: Directory-name prefixes Playwright uses for a Chromium install, best first. The headless
#: shell is preferred because it is what these proofs want: smaller, no window server needed.
#: The trailing ``-`` is deliberate — it is followed by the revision, which is what we refuse
#: to hard-code. ``chromium_headless_shell-`` is listed first AND matched first per root.
_REVISION_DIR_PREFIXES = ("chromium_headless_shell-", "chromium-")

#: Executable names/paths to look for *inside* a revision directory, relative to it. One glob
#: segment stands in for the per-arch folder (``chrome-headless-shell-mac-arm64``,
#: ``chrome-linux``, ``chrome-win64``, …) precisely so a new arch name needs no edit here.
#: NOT gated on ``sys.platform``: the rail tests synthesise foreign layouts to prove the reach,
#: and a cross-compiled or copied browser tree is not worth refusing.
_BINARY_GLOBS = (
    "*/chrome-headless-shell",
    "*/chrome-headless-shell.exe",
    "*/chrome",
    "*/chrome.exe",
    "*/headless_shell",
    "*/*.app/Contents/MacOS/Chromium",
    "*/*.app/Contents/MacOS/Google Chrome for Testing",
)

#: Playwright writes this marker file into a revision directory once the download unpacked
#: cleanly. The rail uses it as INDEPENDENT evidence that a browser is installed — independent
#: because Playwright writes it and :func:`find_chrome` never reads it.
INSTALL_MARKER = "INSTALLATION_COMPLETE"


def _falsey(value: str | None) -> bool:
    return (value or "").strip().lower() in ("", "0", "false", "no", "off")


def browsers_roots() -> tuple[pathlib.Path, ...]:
    """Directories that may hold Playwright browser downloads, in search order.

    ``PLAYWRIGHT_BROWSERS_PATH`` is Playwright's own override and wins when set to a real path
    (it also accepts ``0``, meaning "next to the package", which is not a root — hence the
    falsey check). The per-OS defaults follow. Roots that do not exist are kept rather than
    filtered: the skip message names what was searched, and "we looked here and it was absent"
    is the useful half of that message.
    """
    roots: list[pathlib.Path] = []
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not _falsey(configured):
        roots.append(pathlib.Path(configured).expanduser())

    home = pathlib.Path.home()
    if sys.platform == "darwin":
        roots.append(home / "Library" / "Caches" / "ms-playwright")
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(pathlib.Path(local) / "ms-playwright")
    else:
        cache = os.environ.get("XDG_CACHE_HOME")
        base = pathlib.Path(cache).expanduser() if cache else home / ".cache"
        roots.append(base / "ms-playwright")

    seen: set[pathlib.Path] = set()
    ordered: list[pathlib.Path] = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            ordered.append(root)
    return tuple(ordered)


def installed_revision_dirs(
    roots: tuple[pathlib.Path, ...] | None = None,
) -> tuple[pathlib.Path, ...]:
    """Revision directories that Playwright marked as fully installed.

    Deliberately answered from :data:`INSTALL_MARKER` and nothing else, so the rail can ask
    "is a browser installed?" without going through the matcher whose blindness it is testing.
    """
    found: list[pathlib.Path] = []
    for root in roots if roots is not None else browsers_roots():
        if not root.is_dir():
            continue
        for prefix in _REVISION_DIR_PREFIXES:
            for candidate in sorted(root.glob(f"{prefix}*")):
                if (candidate / INSTALL_MARKER).exists():
                    found.append(candidate)
    return tuple(found)


def find_chrome(roots: tuple[pathlib.Path, ...] | None = None) -> str | None:
    """Path to a usable Chromium binary, or ``None``.

    Order: the explicit :data:`CHROME_ENV` override, then each root, then within a root the
    headless shell before the full browser. ``os.access(X_OK)`` rather than ``exists()``: an
    unpacked-but-not-executable tree, or a same-named plain file, is not something we can launch,
    and treating it as a hit would trade a skip for a confusing subprocess failure.
    """
    override = os.environ.get(CHROME_ENV)
    if override and os.access(override, os.X_OK):
        return override

    for root in roots if roots is not None else browsers_roots():
        if not root.is_dir():
            continue
        for prefix in _REVISION_DIR_PREFIXES:
            for revision in sorted(root.glob(f"{prefix}*"), reverse=True):
                for pattern in _BINARY_GLOBS:
                    for binary in sorted(revision.glob(pattern)):
                        if binary.is_file() and os.access(binary, os.X_OK):
                            return str(binary)
    return None


def _missing(proof: str, detail: str) -> None:
    """Skip — or fail, if the caller declared the proof mandatory."""
    if _falsey(os.environ.get(REQUIRE_ENV)):
        pytest.skip(f"{proof} NOT RUN: {detail}")
    pytest.fail(f"{REQUIRE_ENV} is set, so {proof} not running is a FAILURE, not a skip: {detail}")


def chrome_or_skip(proof: str, roots: tuple[pathlib.Path, ...] | None = None) -> str:
    """The browser these proofs need, or a skip that names where we looked.

    Naming the searched roots is the point of the message: the old literals failed by matching
    nothing, and a skip that says only "no browser found" gives a reader no way to tell "none
    installed" (fine) from "installed somewhere we do not look" (a hole in the proof).
    """
    chrome = find_chrome(roots)
    if chrome is not None:
        return chrome
    searched = ", ".join(str(root) for root in (roots or browsers_roots())) or "(no root)"
    _missing(
        proof,
        f"no Chromium found under {searched}. Install it with `npx playwright install chromium`, "
        f"or point {CHROME_ENV} at a binary. This is an environment gate, not a pass — the "
        "clause it proves is behavioural and no content assertion substitutes for it.",
    )
    raise AssertionError("unreachable: _missing always raises")  # pragma: no cover


def websockets_or_skip(proof: str) -> None:
    """The CDP transport these proofs need. Same require-env rail as the browser.

    Covered by the same switch on purpose: a require-env that only guarded the browser would be
    half a rail, since a venv without ``websockets`` skips the proof just as silently.
    """
    try:
        import websockets  # noqa: F401
    except ImportError:
        _missing(
            proof,
            "`websockets` is not importable, and `playwright` is not a dependency here, so "
            "there is no way to speak CDP.",
        )
