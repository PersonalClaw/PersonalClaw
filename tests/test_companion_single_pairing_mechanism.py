"""One device-pairing mechanism, and no platform-specific code in the shared surfaces (`CA-9`).

COMPANION-APPS Success Criterion 1 and its S4 T4.2/T4.3 rows assert two properties of the
*repository*, not of the prose that claims them:

* **no parallel device-token code** — MOBILE-COMPANION's own device-token/QR design was folded
  into COMPANION-APPS §C1/§C2 by `CA-3`, and the fold cost nothing because the rival design was
  never built. A doc saying "there is no second mechanism" is not evidence; the moment someone
  adds a `device_token` route, store or claim, every one of those sentences becomes a lie and
  nothing goes red. These censuses are what makes it go red.
* **no speculative per-platform code shipped** — the future-platform recipe in
  ``docs/guides/companion-apps.md`` says a new platform wraps the served UI and implements the
  client contract, gated on PLATFORM-REACH. The failure mode it guards against is a well-meant
  stub: a native SDK import or a reserved branch landing in the surfaces every platform shares,
  ahead of any shell that uses it. That is dead code that reads as a promise.

**What is NOT a violation here.** A sanctioned native shell landing as its own tree with its own
build once PLATFORM-REACH clears the platform is the recipe working, not a regression — which is
why the platform census is scoped to ``src/personalclaw`` and ``web/src`` (the shared halves) and
deliberately says nothing about a shell directory. ``desktop/`` is likewise out of scope: an
Electron shell is DESKTOP-CAPABILITIES' to branch on host OS as it needs.
``personalclaw.auth.enrollment`` is out of scope too — it is a documented deliberate sibling of
``auth.pairing`` with its own store file and its own surface, not a second device-pairing path.

Every census here carries a **vacuity floor**. A pattern that matches nothing looks clean while
proving nothing, so each test asserts its scanner found a subject before it asserts what the
subject is.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The shared surfaces: the backend every platform talks to, and the one SPA every platform
#: renders. A native symbol here means a shell has leaked into the half it does not own.
_SHARED_ROOTS = ("src/personalclaw", "web/src")

#: Shared surfaces plus the shells, for censuses about the pairing mechanism itself.
_CODE_ROOTS = (*_SHARED_ROOTS, "desktop")

_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".mjs", ".cjs")

_SKIP_DIRS = {"__pycache__", "node_modules", "dist", "build", ".venv"}

#: The rival design's name in every IDENTIFIER spelling — ``device_token``, ``deviceToken``,
#: ``device-token``, ``devicetoken``. The space-separated prose form is deliberately excluded:
#: the mentions that survive in code are the mechanism denying itself ("**There is no device
#: token.**", ``handlers/devices.py``), and a census that flags a negation flags the wrong
#: thing. Prose in a code file should therefore say "device token" with a space; every spelling
#: a symbol could actually take is caught.
_DEVICE_TOKEN_RE = re.compile(r"device[_-]?token", re.I)

#: Native platform SDKs. Each one is unambiguous: it cannot appear in a shared surface by
#: coincidence, only because a shell was written there.
_PLATFORM_SDK_MARKERS = (
    "@capacitor",
    "capacitor.config",
    "cordova",
    "FirebaseMessaging",
    "UIApplication",
    "NSUserDefaults",
    "android.permission",
    "APNs",
    "UNUserNotificationCenter",
)


@lru_cache(maxsize=None)
def _files(roots: tuple[str, ...]) -> tuple[Path, ...]:
    """Every text source file under ``roots``, skipping generated and vendored trees."""
    out: list[Path] = []
    for rel in roots:
        base = _ROOT / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in _EXTENSIONS:
                continue
            if _SKIP_DIRS & set(path.parts):
                continue
            out.append(path)
    return tuple(out)


@lru_cache(maxsize=None)
def _corpus(roots: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Read every file under ``roots`` ONCE, as ``((relative path, lines), …)``.

    `_census` is called once per pattern, and `test_no_speculative_per_platform_code` alone
    walks nine markers — so re-walking and re-reading `src/personalclaw` plus `web/src` inside
    every call made the cost O(files x patterns) for no benefit. Measured on an idle machine,
    that one test took **45.0s** of the module's 71.7s; under the load this repo is routinely
    developed at (several agents, load 40-70 on 18 cores) it ran past the suite's own 120s
    per-test timeout and failed as a timeout rather than an assertion, which reads like a broken
    test rather than a slow one. Reading once makes the tree a constant, not a multiplier.

    The tuple-of-tuples return is deliberate: `lru_cache` hands the same object to every caller,
    so an immutable one cannot be mutated by one test and observed by the next.
    """
    return tuple(
        (str(p.relative_to(_ROOT)), tuple(p.read_text(encoding="utf-8").splitlines()))
        for p in _files(roots)
    )


def _census(
    pattern: re.Pattern[str] | str, roots: tuple[str, ...] = _CODE_ROOTS
) -> dict[str, list[int]]:
    """``{path relative to the repo root: [1-based line numbers]}``."""
    matches = (
        pattern.search
        if isinstance(pattern, re.Pattern)
        else (lambda line, needle=pattern: needle in line)
    )
    hits: dict[str, list[int]] = {}
    for rel, lines in _corpus(roots):
        for lineno, line in enumerate(lines, 1):
            if matches(line):
                hits.setdefault(rel, []).append(lineno)
    return hits


def test_scanner_is_not_vacuous() -> None:
    """The floor under every census below: the roots resolve and the matcher matches."""
    assert len(_files(_CODE_ROOTS)) > 100, "the code roots resolved to almost nothing"
    for root in _SHARED_ROOTS:
        assert (_ROOT / root).is_dir(), f"{root} does not exist — every census below is vacuous"
    # A control pattern that IS present, proving a zero elsewhere means absence, not a broken
    # scanner. `attach_device` is the device-provenance writer; `endpointSocketUrl` is the S3
    # socket-URL helper the wrapper contract names.
    assert _census("attach_device"), "control pattern missing — the Python census is broken"
    assert _census("endpointSocketUrl"), "control pattern missing — the web census is broken"
    # The device-token pattern itself can match, and matches the shapes a symbol takes.
    for spelling in ("device_token", "deviceToken", "device-token", "DEVICE_TOKEN"):
        assert _DEVICE_TOKEN_RE.search(spelling), f"the census cannot see {spelling}"
    assert (
        _DEVICE_TOKEN_RE.search("no device token exists") is None
    ), "the prose form is excluded on purpose — see the pattern's comment"


def test_no_parallel_device_token_code() -> None:
    """Success Criterion 1: no second device-token design survives in code.

    A device session is a ``sessions.json`` row with ``device``/``issuer`` set. There is no
    separate token type, so the words should not appear in code at all.
    """
    hits = _census(_DEVICE_TOKEN_RE)
    assert hits == {}, (
        "a device-token code path appeared; COMPANION-APPS §C1 says a device session is a "
        f"sessions.json row and nothing else: {hits}"
    )


def test_pairing_code_store_has_one_production_importer() -> None:
    """``auth.pairing`` is the only pairing-code store, reached from exactly one route module."""
    module = _ROOT / "src" / "personalclaw" / "auth" / "pairing.py"
    source = module.read_text(encoding="utf-8")
    for symbol in ("def issue_code(", "def redeem_code(", "PAIR_CODE_TTL_SECS"):
        assert symbol in source, f"{symbol} left auth/pairing.py — this census now measures nothing"

    importers = set(_census("auth import pairing", ("src/personalclaw",))) | set(
        _census("auth.pairing", ("src/personalclaw",))
    )
    assert importers == {"src/personalclaw/dashboard/handlers/devices.py"}, (
        "pairing codes are redeemed from more than one place; COMPANION-APPS §C2 owns that "
        f"redemption once: {sorted(importers)}"
    )


def test_device_provenance_has_one_writer() -> None:
    """Only the C2 route module names a paired device or writes its provenance."""
    writers = {
        path
        for path in _census("attach_device(", ("src/personalclaw",))
        if not path.endswith("session_store.py")  # its own definition site
    }
    assert writers == {
        "src/personalclaw/dashboard/handlers/devices.py"
    }, f"device provenance is written from more than one module: {sorted(writers)}"

    users = set(_census("PAIRED_DEVICE_USER", ("src/personalclaw",)))
    assert users == {
        "src/personalclaw/dashboard/handlers/devices.py"
    }, f"a second module mints paired-device sessions: {sorted(users)}"


def test_no_speculative_per_platform_code() -> None:
    """T4.3: the shared surfaces carry no native SDK ahead of a PLATFORM-REACH-cleared shell."""
    hits: dict[str, dict[str, list[int]]] = {}
    for marker in _PLATFORM_SDK_MARKERS:
        found = _census(marker, _SHARED_ROOTS)
        if found:
            hits[marker] = found
    assert hits == {}, (
        "a platform SDK reached a shared surface; a new platform wraps the served UI and "
        f"implements the client contract instead (docs/guides/companion-apps.md): {hits}"
    )
