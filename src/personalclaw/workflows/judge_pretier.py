"""The free rule tier — everything decidable without a model, decided first.

Loop judges run every cycle. So the ordering here is not an optimization detail: a
rule-solvable failure that reaches the probabilistic model costs tokens on every
iteration of every run, forever. This is the single largest token saving available in
the loop design, and it also makes the cheap failures *deterministic* — a missing
artifact should never be a judgement call.

Four rule families, cheapest first:

1. **Mechanical validation** — schema, length, forbidden content. Microseconds.
2. **Failure-pattern regexes** — tool errors and verbal give-ups the worker already
   admitted to in its own output. If the worker said "I couldn't get this working",
   no model needs to adjudicate that.
3. **Structural pre-checks** — referenced files exist, links resolve.
4. **Existence/non-emptiness** — did this produce anything at all? Zero artifacts and
   zero commits is not a borderline case.

Anything that survives all four is genuinely a judgement call, and only then is the
model worth its cost.

**A pre-tier verdict is always a REJECT or a pass-through, never a PASS.** These rules
can prove work is *unfinished*; they cannot prove it is *good*. Letting the cheap tier
issue a PASS would recreate self-approval with extra steps.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Verbal give-ups and tool errors. Matched against the WORKER's output: these are
#: admissions, not inferences, so a regex is the right instrument.
_GIVEUP_PATTERNS = (
    r"\bi (?:could ?n[o']?t|was unable to|failed to)\b",
    r"\b(?:unable|failed) to (?:complete|finish|fix|resolve|implement)\b",
    r"\bgiv(?:e|ing) up\b",
    r"\bnot (?:possible|feasible) (?:to|for me)\b",
    r"\bneeds? (?:human|manual) (?:intervention|help)\b",
    r"\bi'?ll leave (?:this|that) (?:to you|for now)\b",
)

#: Tool/infrastructure failures. These say the environment broke, which is a
#: different thing from the work being wrong — routed as REJECT with a distinct
#: reason so the escalation ladder can tell them apart.
_TOOL_ERROR_PATTERNS = (
    r"\bcommand not found\b",
    r"\bpermission denied\b",
    r"\bno such file or directory\b",
    r"\bmodulenotfounderror\b",
    r"\bconnection (?:refused|reset|timed out)\b",
    r"\btraceback \(most recent call last\)",
)

#: Stub markers — work that was replaced by a placeholder rather than done.
_STUB_PATTERNS = (
    r"\braise NotImplementedError\b",
    r"\bTODO:? implement\b",
    r"\bFIXME\b",
    r"\bpass\s*#\s*stub\b",
    r"\breturn None\s*#\s*(?:stub|placeholder)\b",
)

_GIVEUP_RE = re.compile("|".join(_GIVEUP_PATTERNS), re.IGNORECASE)
_TOOL_ERROR_RE = re.compile("|".join(_TOOL_ERROR_PATTERNS), re.IGNORECASE)
_STUB_RE = re.compile("|".join(_STUB_PATTERNS), re.IGNORECASE)

#: Below this, output is too short to be a real deliverable.
MIN_SUBSTANCE_CHARS = 20


#: Failure classes the rules can prove. Plain strings rather than an enum: these are
#: routing keys consumed by the escalation ladder's `failure_mutations` map, which is
#: template-authored YAML — an enum here would force every template to import it.
FAILURE_CLASSES = (
    "empty_output",
    "worker_gave_up",
    "tool_error",
    "stubbed_output",
    "missing_artifact",
    "no_output",
)


@dataclass
class PreTierResult:
    """What the free rules concluded."""

    #: True when the cheap tier PROVED the work unfinished. The expensive judge is
    #: then skipped entirely.
    rejected: bool = False
    #: Which rule family fired — the escalation ladder routes on this.
    failure_class: str = ""
    reason: str = ""
    checks_run: list[str] = field(default_factory=list)
    #: Deterministic cross-check result, when one was declared: True/False/None
    #: (None = the check could not run, which is NOT a failure).
    fallback_result: bool | None = None

    @property
    def should_invoke_judge(self) -> bool:
        """Only spend a model call on what the rules could not settle."""
        return not self.rejected


def check_mechanical(text: str, *, min_chars: int = MIN_SUBSTANCE_CHARS) -> PreTierResult:
    """Length and forbidden-content checks. The cheapest tier."""
    result = PreTierResult(checks_run=["mechanical"])
    if not text or len(text.strip()) < min_chars:
        result.rejected = True
        result.failure_class = "empty_output"
        result.reason = f"output under {min_chars} chars — nothing to judge"
    return result


def check_failure_patterns(text: str) -> PreTierResult:
    """Verbal give-ups and tool errors the worker already admitted.

    Ordered: a give-up is about the work, a tool error is about the environment, and
    the escalation ladder treats them differently (retry vs fix-the-environment).
    """
    result = PreTierResult(checks_run=["failure_patterns"])
    match = _GIVEUP_RE.search(text or "")
    if match:
        result.rejected = True
        result.failure_class = "worker_gave_up"
        result.reason = f"worker admitted failure: {match.group(0)!r}"
        return result
    match = _TOOL_ERROR_RE.search(text or "")
    if match:
        result.rejected = True
        result.failure_class = "tool_error"
        result.reason = f"tool/infrastructure error: {match.group(0)!r}"
    return result


def check_stubs(text: str) -> PreTierResult:
    """Placeholder markers — work replaced by a promise to do the work."""
    result = PreTierResult(checks_run=["stubs"])
    match = _STUB_RE.search(text or "")
    if match:
        result.rejected = True
        result.failure_class = "stubbed_output"
        result.reason = f"stub marker present: {match.group(0)!r}"
    return result


def check_structural(referenced_paths: list[str], *, root: Path | None = None) -> PreTierResult:
    """Do the files the output references actually exist?

    A deliverable citing a file it did not create is unfinished, and that is a fact
    about the filesystem rather than an opinion about quality.
    """
    result = PreTierResult(checks_run=["structural"])
    missing = []
    for raw in referenced_paths:
        if not raw:
            continue
        path = Path(raw)
        if root is not None and not path.is_absolute():
            path = root / path
        if not path.exists():
            missing.append(raw)
    if missing:
        result.rejected = True
        result.failure_class = "missing_artifact"
        result.reason = f"referenced path(s) do not exist: {', '.join(missing[:3])}"
    return result


def check_existence(
    *, artifacts: int = 0, commits: int = 0, changed_files: int = 0
) -> PreTierResult:
    """Did anything get produced? Zero of everything is not a borderline case."""
    result = PreTierResult(checks_run=["existence"])
    if artifacts <= 0 and commits <= 0 and changed_files <= 0:
        result.rejected = True
        result.failure_class = "no_output"
        result.reason = "no artifacts, commits, or changed files"
    return result


def run_pretier(
    *,
    worker_output: str = "",
    referenced_paths: list[str] | None = None,
    root: Path | None = None,
    artifacts: int = 0,
    commits: int = 0,
    changed_files: int = 0,
    min_chars: int = MIN_SUBSTANCE_CHARS,
    check_existence_gate: bool = True,
) -> PreTierResult:
    """Run every free rule, cheapest first, and stop at the first rejection.

    Short-circuiting is the point: once the work is provably unfinished, further
    checks cost time and change nothing. The accumulated `checks_run` records how far
    it got, so a rejection is auditable rather than mysterious.
    """
    accumulated: list[str] = []
    for check in (
        lambda: check_mechanical(worker_output, min_chars=min_chars),
        lambda: check_failure_patterns(worker_output),
        lambda: check_stubs(worker_output),
        lambda: check_structural(referenced_paths or [], root=root),
    ):
        result = check()
        accumulated.extend(result.checks_run)
        if result.rejected:
            result.checks_run = accumulated
            return result

    if check_existence_gate:
        result = check_existence(artifacts=artifacts, commits=commits, changed_files=changed_files)
        accumulated.extend(result.checks_run)
        if result.rejected:
            result.checks_run = accumulated
            return result

    return PreTierResult(checks_run=accumulated)


# ── The deterministic fallback / cross-check ──


async def run_fallback_check(
    check: Any,
    *,
    command: str = "",
    artifact_path: str = "",
    diff_lines: int = 0,
    cwd: str | None = None,
) -> bool | None:
    """Evaluate a declared `fallback_check`. TRISTATE.

    None means "the check could not run" — a missing tool must never be read as a
    real failure, which is the same tristate discipline `loop/gates.run_verify_command`
    already established (exit 127 → None). Collapsing None into False would turn an
    uninstalled linter into a failing deliverable.
    """
    from personalclaw.workflows.judge_contract import FallbackCheck

    try:
        kind = FallbackCheck(str(getattr(check, "value", check)))
    except ValueError:
        return None

    if kind is FallbackCheck.ARTIFACT_EXISTS:
        if not artifact_path:
            return None
        return Path(artifact_path).exists()

    if kind is FallbackCheck.DIFF_NONEMPTY:
        return diff_lines > 0

    if kind is FallbackCheck.COMMAND_EXIT_CODE:
        if not command:
            return None
        # Reuse the loop's verify runner: it already carries the audit screen and the
        # exit-127→None handling, and a second implementation would drift from it.
        from personalclaw.loop.gates import run_verify_command

        return await run_verify_command(command, cwd, label="judge fallback")

    return None
