"""Engine-owned completion — the ladder, the verdict enum, and artifact gates.

One rule generates this whole module: **no agent certifies its own work.**

That is not a stylistic preference. Agent-declared completion is systematically
overconfident, and the studied intervention is dramatic — gating on an independent pass
state moved one case from 37.5% to 87.5% real completion. So a stage may only *request* a
transition; the engine executes the declared verification and flips the state.

Three mechanisms enforce it:

**A no-skip ladder.** Checks run static → runtime → system, and a hard failure at any rung
fails the gate outright. Deliberately NOT averaged: averaging lets a confident model pass a
gate it structurally failed, which is precisely the failure being prevented.

**A closed verdict enum.** A judge returns `PASS|RETRY|ESCALATE|REJECT` and nothing else,
so the scheduler routes on DATA rather than parsing prose. Parsing "I think this looks
mostly fine?" into a control-flow decision is how an engine becomes unpredictable.

**The fresh-judge invariant.** A gate judging output runs in a session distinct from the
node that produced it; `self_judge: true` is explicit opt-in. Without the separation the
judge inherits the producer's reasoning and rubber-stamps it.

`required_artifacts` closes the matching hole for non-LLM work: a node claiming to have
written files does not complete until the files exist. The verifier must read where the
worker writes, which is why the run workspace is passed in rather than assumed.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Verdict(str, Enum):
    """The CLOSED set a judge may return. Closed so the scheduler branches on data.

    `RETRY` and `ESCALATE` are separate because they mean different things to a human:
    retry says "try again, this is recoverable", escalate says "stop, a person must
    decide". Collapsing them loses the only signal that distinguishes a hiccup from a
    dead end.
    """

    PASS = "PASS"
    RETRY = "RETRY"
    ESCALATE = "ESCALATE"
    REJECT = "REJECT"


#: Ladder rungs, in mandatory order. Static checks are cheapest and catch the most, so
#: they run first — reaching an expensive system check on code that does not parse wastes
#: the expensive check.
LADDER_ORDER = ("static", "runtime", "system")


def parse_verdict(value: Any) -> Verdict | None:
    """Extract a verdict from a judge's response.

    Tolerant of shape (a bare string, or a dict with a `verdict` key) but STRICT about
    vocabulary: an unrecognized word returns None rather than being guessed at. A guessed
    verdict is a routing decision made on noise.
    """
    raw = value
    if isinstance(value, dict):
        raw = value.get("verdict") or value.get("result") or value.get("status")
    if raw is None:
        return None
    text = str(raw).strip().upper()
    for verdict in Verdict:
        if text == verdict.value:
            return verdict
    # A judge that wrapped the verdict in a sentence: accept it only if exactly ONE
    # vocabulary word appears, so "PASS or REJECT?" stays ambiguous rather than resolving
    # to whichever happened to be first.
    hits = [v for v in Verdict if v.value in text]
    return hits[0] if len(hits) == 1 else None


@dataclass
class CriterionResult:
    """One ladder criterion's outcome. `hard_fail` is what makes the no-averaging rule
    real — a hard failure ends the gate regardless of every other score."""

    criterion: str
    rung: str = "static"
    passed: bool = False
    score: float = 0.0
    hard_fail: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "rung": self.rung,
            "passed": self.passed,
            "score": round(self.score, 4),
            "hard_fail": self.hard_fail,
            "detail": self.detail,
        }


@dataclass
class LadderResult:
    results: list[CriterionResult] = field(default_factory=list)
    verdict: Verdict = Verdict.PASS
    #: The rung that ended it, when something failed hard.
    stopped_at: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == Verdict.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "passed": self.passed,
            "stopped_at": self.stopped_at,
            "criteria": [r.to_dict() for r in self.results],
        }


def run_ladder(criteria: list[dict[str, Any]], evaluated: dict[str, Any]) -> LadderResult:
    """Evaluate an ordered ladder with the no-skip, no-averaging rules.

    `criteria` declares what to check; `evaluated` supplies already-computed outcomes
    (keyed by criterion name). The split keeps this function pure — actually running a
    command or a judge belongs to the engine, and mixing the two would make the rules
    untestable without a subprocess.
    """
    out = LadderResult()
    by_rung: dict[str, list[dict[str, Any]]] = {r: [] for r in LADDER_ORDER}
    for c in criteria:
        rung = str(c.get("rung", "static") or "static")
        by_rung.setdefault(rung if rung in by_rung else "static", []).append(c)

    for rung in LADDER_ORDER:
        for c in by_rung.get(rung, []):
            name = str(c.get("name", "") or "criterion")
            raw = evaluated.get(name)
            threshold = c.get("threshold")
            hard = bool(c.get("hard", True))
            passed, score = _score(raw, threshold)
            result = CriterionResult(
                criterion=name,
                rung=rung,
                passed=passed,
                score=score,
                hard_fail=(hard and not passed),
                detail="" if passed else f"{name} did not meet its threshold",
            )
            out.results.append(result)
            if result.hard_fail:
                # No averaging, no continuing: a hard failure IS the gate's answer.
                out.verdict = Verdict.REJECT
                out.stopped_at = rung
                return out
    return out


def _score(raw: Any, threshold: Any) -> tuple[bool, float]:
    """Normalize a criterion outcome to (passed, score).

    A missing outcome is a FAILURE, not a pass. An unevaluated criterion silently passing
    is how a no-skip ladder quietly becomes skippable.
    """
    if raw is None:
        return False, 0.0
    if isinstance(raw, bool):
        return raw, 1.0 if raw else 0.0
    if isinstance(raw, (int, float)):
        score = float(raw)
        if isinstance(threshold, (int, float)):
            return score >= float(threshold), score
        return score > 0, score
    verdict = parse_verdict(raw)
    if verdict is not None:
        return verdict == Verdict.PASS, 1.0 if verdict == Verdict.PASS else 0.0
    return False, 0.0


# ── required artifacts ───────────────────────────────────────────────────────


@dataclass
class ArtifactCheck:
    satisfied: bool = True
    missing: list[str] = field(default_factory=list)
    digests: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "missing": list(self.missing),
            "digests": list(self.digests),
        }


def check_required_artifacts(patterns: list[str], workspace: Path) -> ArtifactCheck:
    """Refuse completion until declared files exist, recording a digest for each.

    The digest (path/size/sha256) goes in the ledger so a later reader can tell whether
    the artifact that satisfied the gate is the same one on disk now — "the file existed
    once" is a weaker claim than it looks across a rewind.

    Patterns are matched INSIDE the workspace and anything resolving outside it is
    ignored: a glob is spec-authored text, and `../../etc/passwd` must not be able to
    satisfy a gate.
    """
    check = ArtifactCheck()
    if not patterns:
        return check
    try:
        root = workspace.resolve()
    except OSError:
        check.satisfied = False
        check.missing = list(patterns)
        return check

    for pattern in patterns:
        matches = _safe_glob(root, str(pattern))
        if not matches:
            check.missing.append(str(pattern))
            continue
        for path in matches:
            check.digests.append(_digest(path, root))
    check.satisfied = not check.missing
    return check


def _safe_glob(root: Path, pattern: str) -> list[Path]:
    """Glob within `root`, dropping anything that escapes it."""
    if pattern.startswith("/") or ".." in pattern:
        # An absolute or traversing pattern is not a workspace artifact. Refusing beats
        # silently reinterpreting what the author asked for.
        logger.warning("ignoring artifact pattern outside the workspace: %s", pattern)
        return []
    out: list[Path] = []
    try:
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            rel = candidate.relative_to(root).as_posix()
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(candidate.name, pattern):
                resolved = candidate.resolve()
                if root in resolved.parents:
                    out.append(resolved)
    except OSError:
        logger.debug("artifact glob failed for %s", pattern, exc_info=True)
    return out


def _digest(path: Path, root: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
        return {
            "path": path.relative_to(root).as_posix(),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()[:16],
        }
    except OSError:
        return {"path": str(path), "size": 0, "sha256": ""}


# ── fresh-judge invariant ────────────────────────────────────────────────────


def judge_session_key(run_id: str, node_path: str, *, epoch: int = 0) -> str:
    """A session key distinct from any producing node's.

    The `judge:` prefix is the mechanism: a producer's session is `subagent:<id>`, so a
    judge can never accidentally reuse one. Without a distinct session the judge inherits
    the producer's reasoning and rubber-stamps its output.
    """
    return f"judge:{run_id}:{node_path}:{epoch}"


def requires_fresh_judge(node_config: dict[str, Any]) -> bool:
    """True unless the author explicitly opted into self-judging.

    Defaults to the safe reading: a gate is independent unless someone deliberately said
    otherwise.
    """
    return not bool((node_config or {}).get("self_judge", False))


# ── the injected verifier (WF2LOO-10) ────────────────────────────────────────


async def run_verify_block(block: dict[str, Any], *, default_cwd: str = "") -> bool | None:
    """Run ONE verification block's command and report the tristate.

    🔴 This is the callable `dispatch_gate` needs for every `verify_command` /
    `verify_script` gate — and until WF2LOO-10 the gateway wired NOTHING into
    `EngineServices.verify`, so in production every such gate returned INTERNAL "no
    verifier wired for this gate". Two shipped templates ended on a verification gate that
    could not run. The engine-side contract was complete; the last mile was missing.

    The execution itself is delegated to `loop.gates.run_verify_command`, deliberately
    rather than spawning here: that runner already carries the security screen
    (`audit_bash_command` refuses a destructive command), the resource ceiling
    (`PROFILE_TOOL` via the post-exec shim), a bounded timeout, and — most importantly —
    the same TRISTATE contract, where "the tool is missing" (exit 127) is `None` rather
    than a failure. A second implementation would be a second set of those decisions to
    keep in agreement.

    `cwd` comes from the block (a template binds `{{inputs.cwd}}` there) and falls back to
    the run's own workspace, so a gate never silently verifies a different directory than
    the one the work happened in.
    """
    from personalclaw.loop.gates import run_verify_command

    command = str(block.get("command") or block.get("script") or "").strip()
    if not command:
        # No command is NOT a pass: a gate whose verification is blank has verified
        # nothing, and the caller turns this into an explicit "could not determine".
        return None
    cwd = str(block.get("cwd") or default_cwd or "").strip()
    label = str(block.get("label") or "verify").strip() or "verify"
    return await run_verify_command(command, cwd or None, label=label)
