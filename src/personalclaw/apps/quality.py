"""Verify a declared quality bar against the evidence (APE-4).

``app.json`` may carry a ``quality`` block — ``{tested, designSystem, a11y}`` — that the
Store renders as a badge row (see :class:`~personalclaw.apps.manifest.QualityDeclaration`).
For a THIRD-PARTY app that block is a self-declaration and nothing more. For a
FIRST-PARTY app it is a promise this module holds to account: the apps-repo CI runs
``python -m personalclaw.apps.quality .`` over the tree and exits non-zero when a
declaration outruns what the bundle can actually show.

The point is narrow and worth stating plainly, because getting it wrong would make this
module an instance of the defect it exists to catch: **a badge is only worth anything if
a lie about it fails the build.** So every axis here is checked against evidence that
lives in the bundle, and a claim with *nothing to check* counts as a violation rather
than a free pass — declaring ``designSystem: "v2"`` with no frontend to lint, or
``a11y: true`` with no UI to scan, would badge a check that never ran.

What each claim must show:

``tested: true``
    The bundle ships at least one ``test_*.py`` (root or ``tests/``) AND those tests
    pass. Presence alone is not evidence: an empty ``tests/`` directory would otherwise
    buy the badge. The run is a real ``python -m pytest <bundle>`` — the same thing the
    apps-repo ``tests`` job does — injectable for unit tests via ``run_tests``.

``designSystem: "v2"``
    Every frontend source in the bundle passes token-lint — the SAME rule the host
    frontend is held to. The patterns are shared data
    (``apps/token_lint_rules.json``), not a second implementation; see that file.
    ``"legacy"`` and ``"n/a"`` claim nothing and are not verified.

``a11y: true``
    The bundle ships ``a11y/axe-report.json`` — an envelope
    ``{"appVersion": …, "tool": …, "violations": [...]}`` — whose ``appVersion`` matches
    the manifest's (so last release's clean scan cannot launder this one) and whose
    ``violations`` list is empty. axe needs a browser, which the apps-repo CI has none
    of, so the app produces the artifact and CI checks it. No artifact means the scan
    is unevidenced, which is exactly a lie's fingerprint.

An axis the manifest does not declare demands nothing: absent is not a claim. An axis
declared FALSE (or ``"legacy"``/``"n/a"``) demands nothing either — there is no claim to
falsify, and an honest miss must never be punished harder than silence.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NamedTuple

from personalclaw.apps.manifest import AppManifest

#: The canonical token-lint patterns, shared with ``web/src/design/tokenLintRule.ts``.
#: Packaged (see pyproject ``package-data``) because the apps-repo CI reads them out of
#: an installed wheel, with no core checkout on disk.
TOKEN_LINT_RULES_PATH = Path(__file__).with_name("token_lint_rules.json")

#: Where a bundle puts its axe evidence. A fixed path, not a manifest field: a
#: declarable path would let an app point the check at a file it likes.
AXE_REPORT_RELPATH = "a11y/axe-report.json"

#: Directories never linted inside a bundle — build output and installed deps are not
#: the app's authored frontend.
_SKIP_DIRS = frozenset({"node_modules", "dist", "build", ".git", "__pycache__", ".venv"})

#: Frontend source suffixes token-lint covers, mirroring the host walker's ``\.tsx?$``.
_FRONTEND_SUFFIXES = (".ts", ".tsx")


# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityViolation:
    """One dishonest (or unevidenced) claim. ``claim`` is what the manifest said,
    ``reason`` is what the bundle actually shows."""

    app: str
    axis: str
    claim: str
    reason: str

    def render(self) -> str:
        return f"{self.app}: quality.{self.axis}={self.claim} — {self.reason}"


# ---------------------------------------------------------------------------
# Token-lint (the designSystem axis)
# ---------------------------------------------------------------------------


def load_token_lint_rules() -> dict[str, str]:
    """The four canonical patterns. Raises if the packaged file is missing — a
    silently-empty rule set would make every ``designSystem: "v2"`` claim pass."""
    data = json.loads(TOKEN_LINT_RULES_PATH.read_text(encoding="utf-8"))
    rules = {k: str(v) for k, v in data.items() if not k.startswith("_")}
    missing = {"hex", "raw_px", "px_ok_context", "calc_with_token"} - set(rules)
    if missing:
        raise ValueError(f"{TOKEN_LINT_RULES_PATH} is missing pattern(s): {sorted(missing)}")
    return rules


def frontend_sources(app_dir: Path) -> list[Path]:
    """The bundle's authored frontend sources, in stable order. Excludes tests and
    ``*.config.ts`` (build config carries no chrome), plus ``_SKIP_DIRS``."""
    out: list[Path] = []
    for p in sorted(app_dir.rglob("*")):
        if not p.is_file() or p.suffix not in _FRONTEND_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(app_dir).parts):
            continue
        name = p.name
        if re.match(r".*\.test\.tsx?$", name) or re.match(r".*\.config\.tsx?$", name):
            continue
        out.append(p)
    return out


class StrippedSource(NamedTuple):
    """The Python twin of ``StrippedSource`` in ``web/src/design/tokenLintRule.ts``."""

    #: One entry per input line, in order: that line with comment text removed. Same
    #: length as ``text.split("\n")``, so an index is still a line number.
    code: list[str]
    #: The state the scanner ended in — ``"code"`` or ``"block"``. ``"block"`` means a
    #: block comment never closed; a stuck-open tracker reads as "the rest of the file is
    #: clean", which is exactly the weakening to catch, so callers must assert on this.
    #: :func:`token_lint_bundle` refuses the file by name when it is not ``"code"``
    #: (#3347) — a silent clean verdict on an unreadable file earned the badge.
    end_state: str


def strip_comments(text: str) -> StrippedSource:
    """Split ``text`` into lines and blank out comment text, carrying block-comment state
    across newlines (#3337).

    Both consumers used to decide "is this a comment?" from the line's OWN first
    characters, so an INTERIOR line of a multi-line ``{/* … */}`` block — which carries no
    marker — was linted as code. Every decimal digit is a hex digit, so the ``hex`` pattern
    matches any 3-to-8-digit issue reference (``#532``, ``#1783``); citing an issue number
    in a comment therefore failed the gate. Tightening the pattern does not help: ``#1783``
    is four digits.

    Three scoping decisions, each measured against the host corpus rather than assumed:

    1. STRING-AWARE, and ``//`` beats ``/*``. A ``/*`` inside a string or a line comment
       must not open a block, because a tracker that opens one there leaves state stuck
       open and silently stops catching real raw hexes for the rest of the file — the ONLY
       way this change could weaken the gate instead of fixing it. All three shapes ship in
       the host frontend today (``'/*EDITMODE-BEGIN*/'``; ``'… #/settings/* subpages'``;
       a ``/*`` quoted inside a ``//`` comment).
    2. Template literals are a LINE-LOCAL quote, and only when the line closes them
       (#3347). A backtick opens quote state only if another backtick follows on the SAME
       line, so ``` `${proto}//${host}` ``` is content while an UNPAIRED backtick — the
       three regex literals that ship today (``/^```/m`` and friends) and the opening line
       of a multi-line template — stays an ordinary character. That asymmetry is the whole
       point: pairing on the line needs no parser context, never survives a newline (so
       state cannot leak to EOF, which is how template tracking broke before), and leaves
       a trailing ``//`` comment on a regex-literal line still stripped — un-stripping one
       would re-create #3337's defect, since every decimal digit is a hex digit. Multi-line
       template CONTENT therefore stays linted, so a raw hex in a css-in-template is still
       caught. Measured over all 665 host files: 7 lines of stripped code change, and the
       violation set and every EOF state are unchanged.

    3. ``://`` is a scheme separator, not a comment (#3347). Only the IMMEDIATELY preceding
       character counts, so ``case 'x': // note`` is still prose while a URL in JSX text —
       unquoted, so rule 1 cannot help it — no longer truncates the line. Three host lines
       carry this shape today.

    A line whose first non-space characters are ``//`` is prose in any non-block state,
    which covers the ``//`` comments inside embedded-JS template literals.

    Not handled, deliberately — a ``/*`` in JSX text or in a regex character class. Both
    need a real parser and neither occurs in the corpus. Both fail STRICT (state opens,
    code is dropped), which :attr:`StrippedSource.end_state` reports and
    :func:`token_lint_bundle` refuses on.

    Behavioural parity with the TS twin is pinned by ``token_lint_comment_cases.json``,
    which both sides' tests read.
    """
    state = "code"
    code: list[str] = []
    for line in text.split("\n"):
        if state != "block" and line.strip().startswith("//"):
            code.append("")
            continue
        kept: list[str] = []
        quote = ""  # "" | "'" | '"' | "`" — line-local by construction
        i = 0
        n = len(line)
        while i < n:
            c = line[i]
            nxt = line[i + 1] if i + 1 < n else ""
            if state == "block":
                if c == "*" and nxt == "/":
                    state = "code"
                    i += 2
                else:
                    i += 1
                continue
            if quote:
                kept.append(c)
                if c == "\\":
                    kept.append(nxt)
                    i += 2
                    continue
                if c == quote:
                    quote = ""
                i += 1
                continue
            if c in ("'", '"') or (c == "`" and line.find("`", i + 1) != -1):
                quote = c
                kept.append(c)
                i += 1
                continue
            if c == "/" and nxt == "/":
                if i and line[i - 1] == ":":
                    kept.append(c)  # `https://` — a scheme, not a comment
                    i += 1
                    continue
                break  # the rest of the line is a comment
            if c == "/" and nxt == "*":
                state = "block"
                i += 2
                continue
            kept.append(c)
            i += 1
        code.append("".join(kept))
    return StrippedSource(code=code, end_state=state)


#: What :func:`token_lint_bundle` reports for a file the tracker could not read to the end
#: (#3347). It occupies the same ``{relpath: [...]}`` channel as a line violation, so the
#: badge refusal in :func:`verify_app` names the file with no extra wiring.
EOF_UNREADABLE = "EOF: unreadable — an unterminated /* comments out the rest of this file"


def _lint_source(text: str, r: dict[str, str]) -> tuple[list[str], str]:
    """The scanner both entry points share: per-line hits, plus the EOF state that says
    whether those hits saw the whole file."""
    hex_re = re.compile(r["hex"])
    px_re = re.compile(r["raw_px"])
    ok_re = re.compile(r["px_ok_context"])
    calc_re = re.compile(r["calc_with_token"])
    hits: list[str] = []
    lines = text.split("\n")
    stripped = strip_comments(text)
    for i, code in enumerate(stripped.code, start=1):
        trimmed = lines[i - 1].strip()
        if hex_re.search(code):
            hits.append(f"{i}: hex — {trimmed[:80]}")
        if px_re.search(code) and not calc_re.search(code) and not ok_re.search(code):
            hits.append(f"{i}: px — {trimmed[:80]}")
    return hits, stripped.end_state


def token_lint_file(path: Path, rules: dict[str, str] | None = None) -> list[str]:
    """Token-lint one file. Returns ``"<line>: <kind> — <text>"`` strings (empty = clean).
    Same line semantics as the host lint: comment text is stripped by
    :func:`strip_comments` before the patterns run, because design rationale legitimately
    cites hex/px in prose. The reported text is the ORIGINAL line, so a violation still
    reads the way the author wrote it.

    Per-line only, by construction: an empty list means "no line of CODE violated", which
    is not the same claim as "this file is clean" when the tracker never reached EOF.
    :func:`token_lint_bundle` owns that distinction — it is the one the badge reads."""
    r = rules or load_token_lint_rules()
    return _lint_source(path.read_text(encoding="utf-8", errors="replace"), r)[0]


def token_lint_bundle(app_dir: Path) -> dict[str, list[str]]:
    """Token-lint every frontend source in the bundle → ``{relpath: [violations]}``.

    A file whose scan ends outside ``code`` state is REFUSED by name rather than reported
    clean (#3347). An unterminated ``/*`` genuinely comments out the rest of the file, so
    zero line violations is arithmetically correct and substantively a lie: the linter
    stopped seeing code, and a silent clean verdict is what awarded ``designSystem: "v2"``
    to a bundle carrying a raw hex two lines below the opener. The refusal rides the
    existing channel — any non-empty entry is already a failed declaration to the caller —
    and is appended LAST so a real line violation still leads the message."""
    rules = load_token_lint_rules()
    out: dict[str, list[str]] = {}
    for f in frontend_sources(app_dir):
        hits, end_state = _lint_source(f.read_text(encoding="utf-8", errors="replace"), rules)
        if end_state != "code":
            hits.append(EOF_UNREADABLE)
        if hits:
            out[f.relative_to(app_dir).as_posix()] = hits
    return out


# ---------------------------------------------------------------------------
# Tests (the tested axis)
# ---------------------------------------------------------------------------


def bundle_test_files(app_dir: Path) -> list[Path]:
    """``test_*.py`` at the bundle root or under ``tests/`` — the two shapes the
    apps-repo CI's per-bundle pytest step discovers."""
    found = sorted(app_dir.glob("test_*.py")) + sorted(app_dir.glob("tests/test_*.py"))
    return [p for p in found if p.is_file()]


def run_bundle_tests(app_dir: Path, timeout: float = 900.0) -> tuple[bool, str]:
    """Run the bundle's own tests. ``(passed, output_tail)``.

    A real subprocess, deliberately: the tested claim is about the bundle's tests
    PASSING, and importing them in-process would let one app's conftest/stubs leak into
    the next. ``-p no:cacheprovider`` keeps the run from writing into the bundle.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(app_dir), "-q", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(app_dir),
        )
    except subprocess.TimeoutExpired:
        return False, f"pytest timed out after {timeout}s"
    tail = "\n".join((proc.stdout + proc.stderr).strip().split("\n")[-6:])
    return proc.returncode == 0, tail


# ---------------------------------------------------------------------------
# a11y evidence (the a11y axis)
# ---------------------------------------------------------------------------


def read_axe_report(app_dir: Path) -> dict[str, Any] | None:
    """The bundle's axe evidence envelope, or None when absent/unreadable."""
    path = app_dir / AXE_REPORT_RELPATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# The verifier
# ---------------------------------------------------------------------------

TestRunner = Callable[[Path], tuple[bool, str]]


def verify_app(
    app_dir: Path,
    manifest: AppManifest | None = None,
    *,
    run_tests: TestRunner | None = None,
) -> list[QualityViolation]:
    """Check one bundle's ``quality`` claims against its evidence.

    ``run_tests`` is injectable so a unit test can pin the CALL SITE without spawning
    pytest; the default really runs the bundle's tests.
    """
    m = manifest or AppManifest.from_json_file(app_dir / "app.json")
    name = m.name or app_dir.name
    if m.quality is None:
        # Declared nothing → claimed nothing → nothing to verify. Absent is not a
        # failure, and it is not upgraded to a `false` claim either.
        return []

    runner = run_tests or run_bundle_tests
    out: list[QualityViolation] = []
    claims = m.quality.claims()

    if "tested" in claims:
        tests = bundle_test_files(app_dir)
        if not tests:
            out.append(
                QualityViolation(
                    name,
                    "tested",
                    "true",
                    "no test_*.py in the bundle (root or tests/) — nothing to run",
                )
            )
        else:
            passed, tail = runner(app_dir)
            if not passed:
                out.append(
                    QualityViolation(
                        name, "tested", "true", f"the bundle's own tests fail:\n      {tail}"
                    )
                )

    if "designSystem" in claims:
        sources = frontend_sources(app_dir)
        if not sources:
            out.append(
                QualityViolation(
                    name,
                    "designSystem",
                    '"v2"',
                    "no frontend source (*.ts/*.tsx) in the bundle to lint — "
                    'declare "n/a" for a backend-only app',
                )
            )
        else:
            offenders = token_lint_bundle(app_dir)
            if offenders:
                detail = "; ".join(f"{f} ({len(v)})" for f, v in sorted(offenders.items()))
                first = sorted(offenders.items())[0]
                out.append(
                    QualityViolation(
                        name,
                        "designSystem",
                        '"v2"',
                        f"token-lint fails on {detail} — e.g. {first[0]} {first[1][0]}",
                    )
                )

    if "a11y" in claims:
        report = read_axe_report(app_dir)
        if report is None:
            out.append(
                QualityViolation(
                    name,
                    "a11y",
                    "true",
                    f"no readable axe evidence at {AXE_REPORT_RELPATH} — "
                    "the claim is unverified, which is indistinguishable from false",
                )
            )
        elif "violations" not in report or not isinstance(report["violations"], list):
            out.append(
                QualityViolation(
                    name,
                    "a11y",
                    "true",
                    f"{AXE_REPORT_RELPATH} has no `violations` list — "
                    "an envelope that cannot say 'zero' is not evidence",
                )
            )
        elif str(report.get("appVersion", "")) != m.version:
            out.append(
                QualityViolation(
                    name,
                    "a11y",
                    "true",
                    f"{AXE_REPORT_RELPATH} was produced against appVersion "
                    f"{report.get('appVersion')!r}, but this app is {m.version!r} — "
                    "a stale scan cannot vouch for the current release",
                )
            )
        elif report["violations"]:
            ids = ", ".join(str(v.get("id", "?")) for v in report["violations"][:4])
            out.append(
                QualityViolation(
                    name,
                    "a11y",
                    "true",
                    f"{AXE_REPORT_RELPATH} reports {len(report['violations'])} "
                    f"axe violation(s): {ids}",
                )
            )

    return out


def verify_tree(
    root: Path, *, run_tests: TestRunner | None = None
) -> tuple[list[QualityViolation], int]:
    """Verify every ``*/app.json`` under ``root``. Returns ``(violations, apps_seen)``.

    ``apps_seen`` is returned so the caller can refuse a vacuous run: a glob that
    matched nothing would otherwise report "no violations" and look like a pass.
    """
    violations: list[QualityViolation] = []
    seen = 0
    for mf in sorted(root.glob("*/app.json")):
        seen += 1
        try:
            m = AppManifest.from_json_file(mf)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            violations.append(
                QualityViolation(mf.parent.name, "manifest", "-", f"unreadable app.json: {exc}")
            )
            continue
        violations.extend(verify_app(mf.parent, m, run_tests=run_tests))
    return violations, seen


def main(argv: list[str] | None = None) -> int:
    """CLI for the apps-repo CI: ``python -m personalclaw.apps.quality <tree>``.

    Exit 1 on any violation, and exit 1 on a tree with no ``*/app.json`` at all —
    a checker that silently checked nothing is the failure mode this atom is about.
    """
    ap = argparse.ArgumentParser(
        prog="python -m personalclaw.apps.quality",
        description="Verify each app's declared quality block against its evidence.",
    )
    ap.add_argument("tree", nargs="?", default=".", help="dir holding <app>/app.json (default: .)")
    args = ap.parse_args(argv)

    root = Path(args.tree).resolve()
    violations, seen = verify_tree(root)

    if seen == 0:
        print(f"quality: no */app.json under {root} — nothing was verified", file=sys.stderr)
        return 1

    if violations:
        print(f"quality: {len(violations)} dishonest declaration(s) in {seen} app(s):\n")
        for v in violations:
            print(f"  ✗ {v.render()}")
        print(
            "\nEither meet the bar or stop declaring it. A quality badge the bundle "
            "cannot back is worse than no badge."
        )
        return 1

    print(f"quality: {seen} app(s) — every declared claim is backed by evidence.")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
