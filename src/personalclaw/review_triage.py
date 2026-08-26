"""Reviewer-comment triage — line-anchored findings, human acceptance, accepted-subset dispatch.

EXECUTION-ISOLATION §7 asks for Air's productized review loop "built once as a shared primitive
(not per-surface)": review-producing stages (workflow review/gate nodes, loop judges, the §4
second-opinion, inbox draft reviewers) emit line-anchored diff comments, a human accepts or rejects
each one, the accepted subset is dispatched back to the worker that produced the diff, and the
rejections feed calibration. This module is that primitive. It owns four steps and no surface:

1. **Parse** a review stage's output into canonical Finding records
   (`{severity, location, problem, why, recommended_fix, status}` — WORKFLOWS-V2 §Canonical-Finding,
   already the prompt contract in `workflows/bundled/shared/finding-record.md`) plus agentsystem's
   `auto_fixable` flag. One contract; this module adds no second schema.
2. **Anchor** each finding against the ACTUAL unified diff before anything renders it. This is the
   step the feature lives or dies on. A finding anchored to a line the diff does not contain is
   reported UNANCHORED with a typed reason — never silently re-pointed at a nearby line, and never
   shown as truth. Applying a real critique at the wrong line is the worst outcome available here:
   it corrupts working code while looking like review.
3. **Triage** — accept/reject per finding. An unanchored finding cannot be accepted: acceptance
   means "apply this at this line", and there is no line. Accepting it is REFUSED, loudly.
4. **Dispatch + calibrate** — the accepted subset becomes one follow-up brief delivered to the
   ORIGINATING worker; the rejections become `judge_divergence` calibration records (a reviewer
   whose findings are always rejected is a fake gate, and that is exactly what
   `workflows/judge_calibration.py` was built to detect).

**Propose, never write.** There is exactly one path from a finding to a delivered instruction —
:func:`dispatch_accepted` — and it reads `TriageResult.accepted`, which only :func:`triage` can
populate and only for a finding a human accepted. `auto_fixable` mechanical batching
(:func:`auto_apply_candidates`) filters the SAME accepted list rather than reading findings
directly, so "opt-in mechanical apply" cannot become a second door around consent. A rejected or
untriaged finding has no reachable route to a delivery call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

#: The canonical severity ladder, most severe first. Kept as a tuple (not a set) because the
#: ORDER is the contract: gate predicates like "no open Critical|Major" and the `auto_fixable`
#: severity ceiling below are both expressed as a position in this ladder.
SEVERITIES: tuple[str, ...] = ("Critical", "Major", "Minor", "Nit")

#: Rank by position, so `rank(a) <= rank(b)` reads "at least as severe as".
_SEVERITY_RANK = {name.lower(): i for i, name in enumerate(SEVERITIES)}

#: Severity a finding must be no worse than to be eligible for mechanical batching. `Minor` is
#: deliberately conservative: a Critical fix worth applying is worth a human watching it land.
AUTO_APPLY_MAX_SEVERITY = "Minor"

#: `path:line` / `path:line:col` — the shape `finding-record.md` asks for when a location can be a
#: line at all. Anything else parses as a location WITHOUT a line, which is unanchorable by design
#: (the record explicitly also allows "a file and symbol, a section heading").
_LOCATION_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?$")

#: `@@ -old,count +new,count @@` — only the NEW-side start matters here; the old side is what the
#: reviewer did not read.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")


def severity_rank(severity: str) -> int:
    """Ladder position; an unknown severity sorts last (least severe), never first.

    A typo'd severity ranking as Critical would let a malformed reviewer output jump the
    `auto_fixable` ceiling, so the failure direction is chosen deliberately.
    """
    return _SEVERITY_RANK.get(str(severity).strip().lower(), len(SEVERITIES))


def normalize_severity(severity: Any) -> str:
    """Canonical capitalization, or the raw text when it is not on the ladder.

    Unrecognized severities are PRESERVED rather than coerced to a default. Coercing "Blocker" to
    "Nit" hides a reviewer that is not speaking the contract; keeping it makes the drift visible in
    the panel and keeps it off the mechanical-apply path (see :func:`severity_rank`).
    """
    text = str(severity or "").strip()
    for name in SEVERITIES:
        if text.lower() == name.lower():
            return name
    return text


@dataclass
class Finding:
    """One canonical Finding record, plus where it came from.

    The origin triple is what makes dispatch possible at all: "send the accepted subset to the
    worker that produced the diff" needs the worker's identity carried ON the finding, because by
    triage time the run has moved on and the panel is the only thing holding both halves.

    `line_text` is optional and load-bearing when present: it is the line the reviewer claims to
    have read. A finding whose line number still exists but whose CONTENT has changed is stale, and
    that is the case a line number alone cannot detect (see :func:`validate_anchors`).
    """

    severity: str = ""
    location: str = ""
    problem: str = ""
    why: str = ""
    recommended_fix: str = ""
    status: str = "Open"
    auto_fixable: bool = False
    line_text: str = ""
    origin_run_id: str = ""
    origin_node_id: str = ""
    origin_session_key: str = ""

    def __post_init__(self) -> None:
        self.severity = normalize_severity(self.severity)

    @property
    def path(self) -> str:
        """The file half of `location`, or "" when the location names no file:line."""
        m = _LOCATION_RE.match(self.location.strip())
        return m.group("path").strip() if m else ""

    @property
    def line(self) -> int:
        """The line half of `location`, or 0 when the location is not line-anchored."""
        m = _LOCATION_RE.match(self.location.strip())
        return int(m.group("line")) if m else 0

    @property
    def key(self) -> str:
        """Stable identity for one finding, used as the triage decision's handle.

        Derived from the content a reviewer would have to CHANGE to mean something else — never
        from list position. A panel that keyed decisions on index would apply the user's "accept"
        to a different finding as soon as one above it was filtered out by anchoring, which is the
        same wrong-line failure this module exists to prevent, one layer up.
        """
        digest = hashlib.sha256(
            "\x00".join(
                (
                    self.origin_run_id,
                    self.origin_node_id,
                    self.severity,
                    self.location,
                    self.problem,
                )
            ).encode("utf-8")
        ).hexdigest()
        return digest[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "severity": self.severity,
            "location": self.location,
            "problem": self.problem,
            "why": self.why,
            "recommended_fix": self.recommended_fix,
            "status": self.status,
            "auto_fixable": bool(self.auto_fixable),
            "line_text": self.line_text,
            "origin_run_id": self.origin_run_id,
            "origin_node_id": self.origin_node_id,
            "origin_session_key": self.origin_session_key,
        }


def _as_bool(value: Any) -> bool:
    """`auto_fixable` from a model is a bool, "true", or absent. Absent/unknown → False.

    agentsystem's own wording for the flag is "when in doubt, false", so an unparseable value
    resolves to the conservative side rather than to whatever `bool()` happens to say about a
    non-empty string like "no".
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value) if isinstance(value, (int, float)) else False


def _finding_from_mapping(
    raw: Mapping[str, Any],
    *,
    run_id: str,
    node_id: str,
    session_key: str,
) -> Finding | None:
    """One record, or None when it carries no problem statement.

    A row with no `problem` is not a finding — it is noise that would occupy a triage slot and
    train the user to bulk-reject. Dropping it here keeps the count honest.
    """
    problem = str(raw.get("problem", "") or "").strip()
    if not problem:
        return None
    return Finding(
        severity=raw.get("severity", ""),
        location=str(raw.get("location", "") or "").strip(),
        problem=problem,
        why=str(raw.get("why", "") or "").strip(),
        recommended_fix=str(raw.get("recommended_fix", "") or "").strip(),
        status=str(raw.get("status", "Open") or "Open").strip(),
        auto_fixable=_as_bool(raw.get("auto_fixable")),
        line_text=str(raw.get("line_text", "") or ""),
        origin_run_id=run_id,
        origin_node_id=node_id,
        origin_session_key=session_key,
    )


def parse_findings(
    output: Any,
    *,
    run_id: str = "",
    node_id: str = "",
    session_key: str = "",
) -> list[Finding]:
    """Findings out of a review stage's output, in the order the reviewer emitted them.

    Accepts what the engine actually holds at a node settle: the node's output dict (with a
    `findings` key), a bare list of records, or a JSON string of either. Anything else yields `[]`
    — a stage that produced prose is not a review stage, and inventing findings from prose is how a
    triage panel starts showing the user things no reviewer said.
    """
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except (TypeError, ValueError):
            return []
    rows: Any = output
    if isinstance(output, Mapping):
        rows = output.get("findings")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    out: list[Finding] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        parsed = _finding_from_mapping(row, run_id=run_id, node_id=node_id, session_key=session_key)
        if parsed is not None:
            out.append(parsed)
    return out


# ── anchoring against the real diff ──────────────────────────────────────────


class AnchorState(str, Enum):
    """Whether a finding's location survives contact with the diff."""

    ANCHORED = "anchored"
    UNANCHORED = "unanchored"


@dataclass
class AnchoredFinding:
    """A finding plus the verdict on its anchor. `reason` is empty only when anchored.

    `resolved_path` is the diff's own spelling of the file. A reviewer that wrote a repo-relative
    path and a diff that spells it `b/src/x.py` are the same file, and the panel should show the
    diff's spelling so a user can find the line.
    """

    finding: Finding
    state: AnchorState
    reason: str = ""
    resolved_path: str = ""
    resolved_line: int = 0
    diff_line_text: str = ""

    @property
    def anchored(self) -> bool:
        return self.state is AnchorState.ANCHORED

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.finding.to_dict(),
            "anchor_state": self.state.value,
            "anchor_reason": self.reason,
            "resolved_path": self.resolved_path,
            "resolved_line": self.resolved_line,
            "diff_line_text": self.diff_line_text,
        }


def parse_diff_lines(diff_text: str) -> dict[str, dict[int, str]]:
    """`{new_path: {new_line_number: line_text}}` for every line the diff SHOWS on the new side.

    Added and context lines both count: a review comment legitimately lands on an unchanged line
    inside a hunk ("this existing call is now wrong"), and excluding context would report those as
    unanchored. Removed lines do not count — there is no new-side line to anchor to, and pointing a
    follow-up instruction at a line that no longer exists is the defect, not the feature.

    Deliberately tolerant of `git show --stat --patch` preamble, `\\ No newline at end of file`,
    rename headers and binary stubs: the input is whatever the git endpoint returned, and a parser
    that raised on a stat header would report a whole diff as unanchorable.
    """
    files: dict[str, dict[int, str]] = {}
    current: dict[int, str] | None = None
    new_line = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current, in_hunk = None, False
            continue
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            if path == "/dev/null":
                current, in_hunk = None, False
                continue
            if path.startswith("b/"):
                path = path[2:]
            # A tab separates the path from an optional timestamp in POSIX diff output.
            path = path.split("\t", 1)[0]
            current = files.setdefault(path, {})
            in_hunk = False
            continue
        m = _HUNK_RE.match(raw)
        if m and current is not None:
            new_line = int(m.group("start"))
            in_hunk = True
            continue
        if not in_hunk or current is None:
            continue
        if raw.startswith("\\"):
            continue  # "\ No newline at end of file" — metadata, not a line
        if raw.startswith("+"):
            current[new_line] = raw[1:]
            new_line += 1
        elif raw.startswith("-"):
            continue  # old side only; no new-side line number exists
        elif raw.startswith(" ") or raw == "":
            current[new_line] = raw[1:] if raw else ""
            new_line += 1
        else:
            in_hunk = False  # left the patch body (trailing stat, commit trailer, …)
    return files


def _resolve_path(claimed: str, files: Mapping[str, dict[int, str]]) -> tuple[str, str]:
    """Map a finding's claimed path onto a path in the diff. Returns `(path, reason)`.

    Three cases, in order: an exact match; exactly one path in the diff that ENDS with the claimed
    path (a reviewer given a repo-relative path against a diff spelled from a different root); more
    than one such path, which is AMBIGUOUS and refused. Refusing beats guessing — two files named
    `handlers.py` in one diff is common, and picking either one would anchor the comment to a file
    the reviewer never read.
    """
    if claimed in files:
        return claimed, ""
    needle = claimed.lstrip("./")
    matches = [p for p in files if p == needle or p.endswith("/" + needle)]
    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        return "", "ambiguous_path"
    return "", "file_not_in_diff"


def validate_anchors(
    findings: Iterable[Finding],
    diff_text: str,
    *,
    files: Mapping[str, dict[int, str]] | None = None,
) -> list[AnchoredFinding]:
    """Every finding, with its anchor checked against the real diff. Nothing is dropped.

    Unanchored findings are RETAINED and labelled rather than filtered out, because "the reviewer
    said five things and two of them do not match your code" is information the user needs — a
    panel that quietly showed three would read as a reviewer that found three.

    The typed reasons, each a distinct failure a user can act on:

    * `empty_diff` — there is no diff to anchor against at all.
    * `no_line_anchor` — the location names a file/symbol/section, not a line. Valid per the Finding
      record, and honestly not dispatchable as a line edit.
    * `file_not_in_diff` / `ambiguous_path` — see :func:`_resolve_path`.
    * `line_not_in_diff` — the file is in the diff but that line is not on its new side.
    * `content_moved` — the line exists and holds DIFFERENT text than the reviewer quoted. This is
      the stale-anchor case a line number cannot catch on its own, and the one where silently
      applying the fix would edit unrelated code.
    """
    table = dict(files) if files is not None else parse_diff_lines(diff_text)
    out: list[AnchoredFinding] = []
    for finding in findings:
        if not table:
            out.append(AnchoredFinding(finding, AnchorState.UNANCHORED, "empty_diff"))
            continue
        claimed, line = finding.path, finding.line
        if not claimed or line <= 0:
            out.append(AnchoredFinding(finding, AnchorState.UNANCHORED, "no_line_anchor"))
            continue
        path, reason = _resolve_path(claimed, table)
        if not path:
            out.append(AnchoredFinding(finding, AnchorState.UNANCHORED, reason))
            continue
        lines = table[path]
        if line not in lines:
            out.append(
                AnchoredFinding(
                    finding, AnchorState.UNANCHORED, "line_not_in_diff", resolved_path=path
                )
            )
            continue
        actual = lines[line]
        quoted = finding.line_text.strip()
        if quoted and quoted != actual.strip():
            out.append(
                AnchoredFinding(
                    finding,
                    AnchorState.UNANCHORED,
                    "content_moved",
                    resolved_path=path,
                    resolved_line=line,
                    diff_line_text=actual,
                )
            )
            continue
        out.append(
            AnchoredFinding(
                finding,
                AnchorState.ANCHORED,
                resolved_path=path,
                resolved_line=line,
                diff_line_text=actual,
            )
        )
    return out


# ── triage ───────────────────────────────────────────────────────────────────


class TriageOutcome(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass
class TriageDecision:
    """One human decision, addressed by :attr:`Finding.key`."""

    key: str
    outcome: TriageOutcome
    reason: str = ""


@dataclass
class TriageResult:
    """What the human decided, split by what may now happen.

    Four buckets, not two, because the difference between them is exactly what "nothing was
    auto-written without acceptance" means operationally:

    * `accepted` — anchored AND accepted. The ONLY list :func:`dispatch_accepted` reads.
    * `rejected` — explicitly rejected; feeds calibration.
    * `refused` — accepted but NOT anchored. Acceptance means "apply this here" and there is no
      here, so the accept does not take effect and the user is told why.
    * `untriaged` — no decision arrived. Not accepted, therefore not dispatched. Silence is not
      consent.
    """

    accepted: list[AnchoredFinding] = field(default_factory=list)
    rejected: list[tuple[AnchoredFinding, str]] = field(default_factory=list)
    refused: list[tuple[AnchoredFinding, str]] = field(default_factory=list)
    untriaged: list[AnchoredFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": [a.to_dict() for a in self.accepted],
            "rejected": [{**a.to_dict(), "rejection_reason": r} for a, r in self.rejected],
            "refused": [{**a.to_dict(), "refused_reason": r} for a, r in self.refused],
            "untriaged": [a.to_dict() for a in self.untriaged],
        }


def triage(
    anchored: Sequence[AnchoredFinding], decisions: Iterable[TriageDecision]
) -> TriageResult:
    """Apply the human's decisions to the anchored findings.

    A decision naming a key that is not on the list is IGNORED rather than errored: the panel and
    the ledger can legitimately drift by a re-run, and a stale accept must not become an accept of
    whatever now occupies that slot.
    """
    by_key = {a.finding.key: a for a in anchored}
    chosen: dict[str, TriageDecision] = {}
    for incoming in decisions:
        if incoming.key in by_key:
            chosen[incoming.key] = incoming
    result = TriageResult()
    for item in anchored:
        decision = chosen.get(item.finding.key)
        if decision is None:
            result.untriaged.append(item)
        elif decision.outcome is TriageOutcome.REJECT:
            result.rejected.append((item, decision.reason))
        elif item.anchored:
            result.accepted.append(item)
        else:
            result.refused.append((item, item.reason or "unanchored"))
    return result


def auto_apply_candidates(
    result: TriageResult, *, max_severity: str = AUTO_APPLY_MAX_SEVERITY
) -> list[AnchoredFinding]:
    """The accepted subset a surface MAY apply mechanically, if it opted in.

    Reads `result.accepted` — never the findings — so the `auto_fixable` batching path §7 allows
    cannot become a second door around acceptance. `auto_fixable: true` on a rejected or untriaged
    finding buys it nothing; it is not in `accepted`, so it is not here.

    A severity that is not ON the ladder is excluded rather than treated as least-severe. It ranks
    last by :func:`severity_rank` — which is the right answer for a gate predicate and the wrong one
    here, because "unknown" would then clear every ceiling and a malformed reviewer output would be
    the most mechanically-appliable kind there is.
    """
    ceiling = severity_rank(max_severity)
    ladder = len(SEVERITIES)
    return [
        a
        for a in result.accepted
        if a.finding.auto_fixable and ceiling <= severity_rank(a.finding.severity) < ladder
    ]


# ── dispatch to the originating worker ───────────────────────────────────────


def dispatch_brief(accepted: Sequence[AnchoredFinding]) -> str:
    """The follow-up instruction for the originating worker, most severe first.

    Ordered by severity so a worker that runs out of budget mid-brief has done the Critical work.
    Each item carries the diff's OWN path:line — not the reviewer's claimed location — because the
    resolved anchor is the one that was verified.
    """
    ordered = sorted(accepted, key=lambda a: severity_rank(a.finding.severity))
    lines = [
        "A human reviewed your diff and ACCEPTED the findings below. Apply each one at the",
        "line given. Findings the human rejected are not listed and must not be acted on.",
        "",
    ]
    for i, item in enumerate(ordered, start=1):
        f = item.finding
        where = f"{item.resolved_path}:{item.resolved_line}"
        lines.append(f"{i}. [{f.severity}] {where} — {f.problem}")
        if f.why:
            lines.append(f"   why: {f.why}")
        if f.recommended_fix:
            lines.append(f"   fix: {f.recommended_fix}")
    return "\n".join(lines)


@dataclass
class DispatchReceipt:
    """What actually left the building. `delivered` is False when nothing was sent.

    `reason` says why not, and the two no-send cases are deliberately distinguishable:
    `nothing_accepted` (there was nothing to send — the correct outcome of a full rejection) and
    whatever the delivery seam reported (a send that was attempted and failed).
    """

    delivered: bool = False
    reason: str = ""
    target: str = ""
    brief: str = ""
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "reason": self.reason,
            "target": self.target,
            "brief": self.brief,
            "count": self.count,
        }


def dispatch_accepted(
    result: TriageResult,
    *,
    deliver: Callable[[str, str], bool],
    target: str = "",
) -> DispatchReceipt:
    """Deliver the accepted subset to the originating worker. The ONLY write path.

    `deliver(target, brief)` is the seam, not a hard-wired sink, because §7's dispatch target
    depends on the surface: a live workflow run takes `service.steer_run` (drained at the loop
    boundary by `RunController._consume_steering`, so the next iteration acts on it); a finished
    run has no session to resume and takes a fresh session with the same brief as its handoff.

    `deliver` is NOT CALLED AT ALL when nothing was accepted. That is the load-bearing property:
    the negative case must be an absent call, not a call with an empty payload that some future
    sink decides to treat as "apply everything".
    """
    if not result.accepted:
        return DispatchReceipt(delivered=False, reason="nothing_accepted", target=target)
    brief = dispatch_brief(result.accepted)
    resolved = target or next(
        (a.finding.origin_session_key for a in result.accepted if a.finding.origin_session_key), ""
    )
    if not resolved:
        return DispatchReceipt(
            delivered=False, reason="no_origin_worker", brief=brief, count=len(result.accepted)
        )
    try:
        ok = bool(deliver(resolved, brief))
    except Exception as exc:  # pragma: no cover - seam failures are the caller's to surface
        logger.warning("review-triage dispatch failed for %s: %s", resolved, exc)
        return DispatchReceipt(
            delivered=False,
            reason="delivery_failed",
            target=resolved,
            brief=brief,
            count=len(result.accepted),
        )
    return DispatchReceipt(
        delivered=ok,
        reason="" if ok else "delivery_refused",
        target=resolved,
        brief=brief,
        count=len(result.accepted),
    )


# ── rejections → the flywheel's calibration record ───────────────────────────


def calibration_records(result: TriageResult, *, template: str = "") -> list[dict[str, Any]]:
    """One `judge_divergence` payload per REJECTED finding, ready for `Journal.write`.

    Reuses `judge_calibration.DivergenceRecord` rather than minting a parallel record, because the
    nodding-loop detector and `divergence_exemplars` already read that shape — a second dialect
    would leave the reviewer's rejection rate invisible to the one instrument built to look at it.

    The mapping: the reviewer asserted a problem (`judge_verdict="REJECT"`), the human said there
    was none (`human_verdict="PASS"`), so `DivergenceRecord.direction` computes `false_reject` —
    the judge cried wolf. An ACCEPTED finding is agreement and writes nothing, matching
    `RunController._emit_judge_divergence`'s rule that only disagreement is a divergence.
    """
    from personalclaw.workflows import judge_calibration

    out: list[dict[str, Any]] = []
    for item, reason in result.rejected:
        f = item.finding
        record = judge_calibration.DivergenceRecord(
            run_id=f.origin_run_id,
            node_id=f.origin_node_id,
            template=template,
            judge_verdict="REJECT",
            human_verdict="PASS",
            reason=reason,
        )
        out.append(
            {
                **record.to_dict(),
                "source": "review_triage",
                "finding_key": f.key,
                "severity": f.severity,
                "location": f.location,
                "anchor_state": item.state.value,
            }
        )
    return out
