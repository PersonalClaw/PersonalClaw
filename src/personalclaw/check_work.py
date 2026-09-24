"""Check-work — claim reconstruction → derived executable checks → evidence report.

HARNESS-CRAFT §3.1/§3.2 (HC-4). ONE module, two entry points, so the skill and the
unattended hook can never grow two behaviors for one name:

* the bundled ``check-work`` skill (``skills/bundled/check-work/SKILL.md``) — the
  LIGHT, in-session half. The agent reconstructs what it claimed this session,
  calls :func:`derive_checks`, and executes what came back with real tool calls.
* the SDLC post-gate hook (``loops.check_work_stages``, default off) — the
  unattended half. After a stage's gate passes it re-derives checks from the
  stage's own claims, catching the "gate command passed but the claim is broader
  than the command" class (a claimed file that was never written).

**Doctrine — ground truth over self-report** (inherited from the loop
judge-independence work): a derived check either EXECUTES and yields ``pass`` or
``fail`` with quoted evidence, or it is reported ``unverifiable`` with the reason
it could not run. There is no third path. Nothing in this module returns ``pass``
without having observed something, and a claim that yields no derivable check is
reported as underivable rather than quietly dropped.

Command checks are never executed by this module on its own authority: the caller
injects a ``command_runner``. Without one, a command check is ``unverifiable``
("re-run it yourself"), which is the honest answer — not a pass.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

#: A useful report is 2-4 checks: one is not a cross-check, five is a test suite.
MIN_CHECKS = 2
MAX_CHECKS = 4

#: Bytes read when confirming a claim's content — enough to quote evidence, bounded
#: so a check never pulls a whole build artifact into the report.
_READ_LIMIT = 200_000

#: Commands we recognise as verification commands when a claim names one. Deliberately
#: a small, repo-real set: the point of a derived command check is that the reader can
#: re-run the exact thing the session claimed to have run.
_COMMAND_HEADS = (
    "make",
    "pytest",
    "python",
    "python3",
    "npm",
    "npx",
    "node",
    "ruff",
    "flake8",
    "mypy",
    "git",
    "pip",
    "personalclaw",
)

#: Language that marks a sentence as a COMPLETION claim ("I did X"), as opposed to a
#: plan ("I will do X"). Claim reconstruction only mines the former.
_COMPLETION_WORDS = re.compile(
    r"\b(added|created|wrote|written|updated|edited|fixed|implemented|ran|run|passes|"
    r"passed|green|landed|shipped|removed|deleted|renamed|verified|now|done)\b",
    re.IGNORECASE,
)

_INTENT_WORDS = re.compile(r"\b(will|going to|plan to|next|should|would|todo|let me)\b", re.I)

_BACKTICKED = re.compile(r"`([^`\n]{1,200})`")
_PATHLIKE = re.compile(r"^[\w.\-/]+\.[A-Za-z][\w]{0,7}$")
_IDENTLIKE = re.compile(r"^[A-Za-z_][\w.]{2,60}(\(\))?$")


@dataclass(frozen=True)
class Claim:
    """One completion claim reconstructed from session text."""

    text: str
    files: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    idents: tuple[str, ...] = ()


@dataclass(frozen=True)
class DerivedCheck:
    """An executable check derived from a claim.

    ``kind`` is one of ``file_exists``, ``file_contains``, ``command``. Each kind is
    something a reader can run and watch pass or fail — never prose advice.
    """

    kind: str
    target: str
    claim: str
    needle: str | None = None

    @property
    def label(self) -> str:
        if self.kind == "file_exists":
            return f"`{self.target}` exists"
        if self.kind == "file_contains":
            return f"`{self.target}` contains `{self.needle}`"
        return f"`{self.target}` re-runs clean"

    @property
    def how(self) -> str:
        """The literal command a reader can run to reproduce this check."""
        if self.kind == "file_exists":
            return f"test -e {self.target}"
        if self.kind == "file_contains":
            return f"grep -n -- {self.needle!r} {self.target}"
        return self.target


@dataclass(frozen=True)
class CheckResult:
    check: DerivedCheck
    status: str  # "pass" | "fail" | "unverifiable"
    evidence: str


@dataclass
class CheckWorkReport:
    """The result of one check-work pass.

    ``note`` carries the honest-failure message when fewer than :data:`MIN_CHECKS`
    checks could be derived — the caller must say so rather than invent a check.
    """

    results: list[CheckResult] = field(default_factory=list)
    note: str = ""

    @property
    def passed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "pass"]

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "fail"]

    @property
    def unverifiable(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "unverifiable"]

    @property
    def verdict(self) -> str:
        """``fail`` if anything failed; ``pass`` only if something actually executed
        and passed with nothing failing; ``unverifiable`` otherwise. An empty report
        is NEVER a pass."""
        if self.failed:
            return "fail"
        if self.passed:
            return "pass"
        return "unverifiable"


def reconstruct_claims(text: str) -> list[Claim]:
    """Mine completion claims out of session text (recent turns + tool-call summaries).

    Intent sentences ("I will add X") are skipped: they claim nothing yet. Each claim
    keeps the backticked spans it named, classified into paths, commands and bare
    identifiers — those are what :func:`derive_checks` turns into checks.
    """
    claims: list[Claim] = []
    for raw in re.split(r"(?<=[.!?\n])\s+", text or ""):
        sentence = raw.strip().strip("-*# ")
        if not sentence or len(sentence) > 600:
            continue
        if not _COMPLETION_WORDS.search(sentence):
            continue
        if _INTENT_WORDS.search(sentence) and not re.search(r"\b(ran|passes|passed)\b", sentence):
            continue
        files: list[str] = []
        commands: list[str] = []
        idents: list[str] = []
        for span in _BACKTICKED.findall(sentence):
            span = span.strip()
            if not span:
                continue
            head = span.split()[0].split("/")[-1]
            if " " in span and head in _COMMAND_HEADS:
                commands.append(span)
            elif _PATHLIKE.match(span):
                files.append(span)
            elif span in _COMMAND_HEADS:
                commands.append(span)
            elif _IDENTLIKE.match(span):
                idents.append(span.removesuffix("()"))
        # Unbackticked paths still count — models narrate `src/x/y.py` both ways. Scan
        # the sentence with its backticked spans REMOVED: measured, a hyphenated segment
        # inside an already-captured path (`…/bundled/check-work/SKILL.md`) got re-matched
        # as a second, bogus path `work/SKILL.md`, which then failed as a phantom claim —
        # the worst kind of check, a fail nobody claimed.
        plain = _BACKTICKED.sub(" ", sentence)
        for bare in re.findall(r"(?<![\w`/.\-])((?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z]\w{0,7})", plain):
            if bare not in files:
                files.append(bare)
        if files or commands:
            claims.append(
                Claim(
                    text=sentence,
                    files=tuple(dict.fromkeys(files)),
                    commands=tuple(dict.fromkeys(commands)),
                    idents=tuple(dict.fromkeys(idents)),
                )
            )
    return claims


def derive_checks(claims: list[Claim], *, max_checks: int = MAX_CHECKS) -> list[DerivedCheck]:
    """Turn reconstructed claims into 2-4 executable checks, most specific first.

    A claim naming both a file and an identifier becomes a CONTENT check (the strong
    form: the file exists AND says what the claim said it says). A file alone becomes
    an existence check. A named command becomes a re-run check. Deduplicated, and
    capped so one chatty claim cannot crowd out the others.
    """
    strong: list[DerivedCheck] = []
    weak: list[DerivedCheck] = []
    for claim in claims:
        for path in claim.files:
            if claim.idents:
                strong.append(
                    DerivedCheck(
                        kind="file_contains",
                        target=path,
                        claim=claim.text,
                        needle=claim.idents[0],
                    )
                )
            else:
                weak.append(DerivedCheck(kind="file_exists", target=path, claim=claim.text))
        for cmd in claim.commands:
            strong.append(DerivedCheck(kind="command", target=cmd, claim=claim.text))
    out: list[DerivedCheck] = []
    seen: set[tuple[str, str, str | None]] = set()
    for check in [*strong, *weak]:
        key = (check.kind, check.target, check.needle)
        if key in seen:
            continue
        seen.add(key)
        out.append(check)
        if len(out) >= max(1, max_checks):
            break
    return out


def _resolve(root: Path, target: str) -> Path | None:
    """Resolve ``target`` under ``root``, refusing an escape. ``None`` = out of bounds
    (reported unverifiable, never guessed)."""
    try:
        base = root.resolve()
        candidate = (base / target).resolve() if not Path(target).is_absolute() else Path(target)
        candidate = candidate.resolve()
        if candidate == base or base in candidate.parents:
            return candidate
    except OSError:
        return None
    return None


def _run_file_exists(check: DerivedCheck, root: Path) -> CheckResult:
    path = _resolve(root, check.target)
    if path is None:
        return CheckResult(check, "unverifiable", f"`{check.target}` resolves outside {root}")
    if not path.exists():
        return CheckResult(check, "fail", f"no such path: {path} (claim says it was written)")
    if path.is_dir():
        n = len(list(path.iterdir()))
        return CheckResult(
            check, "pass", f"{path} is a directory with {n} entr{'y' if n == 1 else 'ies'}"
        )
    size = path.stat().st_size
    if size == 0:
        return CheckResult(check, "fail", f"{path} exists but is empty (0 bytes)")
    return CheckResult(check, "pass", f"{path} exists, {size} bytes")


def _run_file_contains(check: DerivedCheck, root: Path) -> CheckResult:
    path = _resolve(root, check.target)
    if path is None:
        return CheckResult(check, "unverifiable", f"`{check.target}` resolves outside {root}")
    if not path.is_file():
        return CheckResult(check, "fail", f"no such file: {path} (claim says it was written)")
    try:
        text = path.read_text(encoding="utf-8", errors="strict")[:_READ_LIMIT]
    except (UnicodeDecodeError, OSError) as exc:
        return CheckResult(
            check, "unverifiable", f"{path} is not readable as text ({exc.__class__.__name__})"
        )
    needle = check.needle or ""
    for lineno, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return CheckResult(check, "pass", f"{path}:{lineno}: {line.strip()[:160]}")
    return CheckResult(
        check,
        "fail",
        f"{path} has {len(text.splitlines())} lines, none containing `{needle}`",
    )


def run_checks(
    checks: list[DerivedCheck],
    *,
    root: Path | str,
    command_runner: Callable[[str], bool | None] | None = None,
) -> list[CheckResult]:
    """Execute derived checks against ground truth.

    File checks run here (a filesystem read is safe and side-effect free). Command
    checks run only through an injected ``command_runner`` returning
    ``True``/``False``/``None`` (ran+passed / ran+failed / could not run). With no
    runner, a command check is ``unverifiable`` — this module never shells out on its
    own authority, and never marks an unrun command as passing.
    """
    root_path = Path(root)
    out: list[CheckResult] = []
    for check in checks:
        try:
            if check.kind == "file_exists":
                out.append(_run_file_exists(check, root_path))
            elif check.kind == "file_contains":
                out.append(_run_file_contains(check, root_path))
            elif check.kind == "command":
                if command_runner is None:
                    out.append(
                        CheckResult(
                            check,
                            "unverifiable",
                            "no command runner in this context — re-run "
                            f"`{check.target}` yourself and paste the output",
                        )
                    )
                else:
                    ok = command_runner(check.target)
                    if ok is True:
                        out.append(CheckResult(check, "pass", f"`{check.target}` exited 0"))
                    elif ok is False:
                        out.append(CheckResult(check, "fail", f"`{check.target}` exited non-zero"))
                    else:
                        out.append(
                            CheckResult(
                                check, "unverifiable", f"`{check.target}` could not run here"
                            )
                        )
            else:  # pragma: no cover - closed kind set, kept explicit
                out.append(CheckResult(check, "unverifiable", f"unknown check kind {check.kind!r}"))
        except Exception as exc:  # never let one check abort the report
            logger.debug("check-work check failed: %s", check, exc_info=True)
            out.append(CheckResult(check, "unverifiable", f"check raised {exc.__class__.__name__}"))
    return out


def derive_and_run(
    text: str,
    *,
    root: Path | str,
    command_runner: Callable[[str], bool | None] | None = None,
    max_checks: int = MAX_CHECKS,
) -> CheckWorkReport:
    """Reconstruct → derive → execute, in one call. The shared core both entry points
    use, so the skill and the SDLC hook are behaviorally identical."""
    claims = reconstruct_claims(text)
    checks = derive_checks(claims, max_checks=max_checks)
    results = run_checks(checks, root=root, command_runner=command_runner)
    note = ""
    if not checks:
        note = (
            "No executable check could be derived from these claims — say so. Do not "
            "substitute a generic checklist or report an unrun check as passing."
        )
    elif len(checks) < MIN_CHECKS:
        note = (
            f"Only {len(checks)} check was derivable (a useful report is {MIN_CHECKS}-"
            f"{MAX_CHECKS}). Report the shortfall instead of padding it."
        )
    return CheckWorkReport(results=results, note=note)


def render_report(report: CheckWorkReport) -> str:
    """Render a report as the markdown the skill presents (and the hook logs)."""
    icon = {"pass": "PASS", "fail": "FAIL", "unverifiable": "UNVERIFIABLE"}
    lines = [f"**Check-work: {report.verdict.upper()}**", ""]
    for res in report.results:
        lines.append(f"- [{icon[res.status]}] {res.check.label} — `{res.check.how}`")
        lines.append(f"  - evidence: {res.evidence}")
    if report.note:
        lines += ["", report.note]
    return "\n".join(lines)
