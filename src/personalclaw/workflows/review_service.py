"""Run-scoped glue for the reviewer-comment triage primitive.

:mod:`personalclaw.review_triage` is deliberately surface-free and engine-free: it parses, anchors,
triages, briefs and calibrates, and it knows nothing about runs, worktrees or git. This module is
the workflow-run binding for it — the half that answers "which diff?" and "which worker?" — so the
primitive stays reusable by its other consumers (loop judge feedback, inbox drafts) without
inheriting a run id.

Three decisions worth reading before changing anything here:

**The diff is read live, at every request.** Not stamped on the run, not cached with the findings.
A worker keeps working after the review stage settles, so a stored anchor verdict is stale the
moment it is written — and a stale anchor that still says `anchored` is precisely how an accepted
fix lands on the wrong line. Re-reading costs one `git diff`.

**Anchors are re-validated on the POST, not trusted from the GET.** The user accepted a finding
against what the panel showed them; between render and submit the worker may have moved that line.
So the accept is re-checked against the diff as it is NOW, and a finding that went stale in between
is REFUSED rather than dispatched. Without that second check this endpoint would be a TOCTOU hole
in the one property the change is built around.

**Dispatch is the run's own steering queue.** `service.steer_run` parks the brief on the run's
queue (`store.queue_steering`), which `iteration_context.consume_steering` drains at the next
iteration boundary and injects into the worker's prompt. That IS "follow-up instructions to the
originating session" for a workflow run — no new delivery channel, no second dialect. A run that
has already reached a terminal status has no boundary left to drain at, so the brief is PARKED for
the user to start a follow-up run with, and the receipt says so. Auto-spawning a fresh run off a
review acceptance would be unattended execution the user never asked for.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from personalclaw import review_triage
from personalclaw.review_triage import (
    AnchoredFinding,
    Finding,
    TriageDecision,
    TriageOutcome,
)
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import provisioning, service, store
from personalclaw.workflows.models import SUCCESS_STATES

logger = logging.getLogger(__name__)

#: Untracked files a single diff read will synthesize a patch for. A worker that created two
#: hundred files is not a review the user is triaging line by line, and the cap keeps one request
#: from forking two hundred `git` processes.
MAX_UNTRACKED = 40

#: Same cap the git endpoints use, for the same reason: a half-megabyte patch is past the point
#: where a human is reading lines, and an uncapped read is a memory hazard on a generated-file diff.
DIFF_CAP = 512 * 1024


async def _git(args: list[str], cwd: str, timeout: float = 10.0) -> str:
    """Run a read-only ``git`` command (arg vector, never a shell). '' on error or timeout.

    Non-zero exit is NOT an error here: ``git diff`` exits 1 when there are differences under
    ``--no-index``, which is the case this is used for. stdout is what matters.
    """
    from personalclaw.net.git import git_argv, git_env

    try:
        proc = await asyncio.create_subprocess_exec(
            *git_argv(args),
            cwd=cwd,
            # A run's workspace is a directory its agents write, `.git` included (`net.git`).
            env=git_env(site="run-review-git"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await asyncio.gather(proc.wait(), return_exceptions=True)
        return ""
    return out.decode("utf-8", "replace")


async def workspace_diff(run: Any) -> tuple[str, str, bool]:
    """`(workspace_path, unified_diff, truncated)` for a run's workspace. Empty when it has none.

    Tracked changes come from ``git diff HEAD``. Untracked files are appended as synthesized
    ``--no-index`` patches, because a worker that CREATED a file is the common case and a review
    finding on a new file would otherwise report `file_not_in_diff` — an anchoring failure that
    reads as "the reviewer hallucinated" when the truth is "git had not been told about it yet".
    """
    state = provisioning.workspace_state(run)
    path = str(state.get("path", "") or "")
    if not path or not Path(path).is_dir():
        return "", "", False
    diff = await _git(["diff", "--no-color", "HEAD", "--"], path)
    untracked = await _git(["ls-files", "--others", "--exclude-standard"], path)
    for rel in [line for line in untracked.splitlines() if line.strip()][:MAX_UNTRACKED]:
        if not (Path(path) / rel).is_file():
            continue
        diff += await _git(["diff", "--no-color", "--no-index", "--", "/dev/null", rel], path)
    truncated = len(diff) > DIFF_CAP
    return path, diff[:DIFF_CAP], truncated


def findings_for(run_id: str) -> list[Finding]:
    """Every `review_finding` this run filed, oldest first, rebuilt as Finding records, each marked
    "Refuted", with its reason, when the latest verdict a step judging it gave was a refutation
    (a row whose ``judged`` is the finding's row stamp: ``review_triage.verdict_on``).

    Read from the LEDGER rather than from node outputs: a node output can be spilled, rewound or
    superseded by a re-run, and the ledger row is the one record that outlives all three. Rows the
    writer stamped are ignored on the way back in (`ts`, `seq`, …) — only the record's own fields
    reconstruct a Finding, so a ledger stamping change cannot silently alter a finding's key.

    A verdict names its row by the key the row was written with (``key``, taken before the ledger
    redacts the row's words) and the instance and epoch that filed it, so a finding that quotes a
    credential is still the row its verifier judged, and the same words another round filed are
    another row. A "Refuted" status a finder wrote itself is not a verifier's, and reads Open.
    """
    rows = journal_mod.ledger(run_id, kinds={journal_mod.REVIEW_FINDING})
    filed: list[tuple[str, Finding]] = []
    verdicts: dict[str, tuple[bool, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "judged" in row:
            if row.get("judged"):
                verdicts[str(row["judged"])] = (
                    row.get("refuted") is True,
                    str(row.get("refute_reason", "") or ""),
                )
            continue
        stamp = _filed_stamp(row)
        finding = Finding(
            severity=row.get("severity", ""),
            location=str(row.get("location", "") or ""),
            problem=str(row.get("problem", "") or ""),
            why=str(row.get("why", "") or ""),
            recommended_fix=str(row.get("recommended_fix", "") or ""),
            status=str(row.get("status", "Open") or "Open"),
            auto_fixable=bool(row.get("auto_fixable", False)),
            line_text=str(row.get("line_text", "") or ""),
            origin_run_id=str(row.get("origin_run_id", "") or run_id),
            origin_node_id=str(row.get("origin_node_id", "") or row.get("node_id", "") or ""),
            origin_session_key=str(row.get("origin_session_key", "") or ""),
        )
        filed.append((stamp, finding))
    for stamp, finding in filed:
        refuted, reason = verdicts.get(stamp, (False, ""))
        if refuted:
            finding.status, finding.refute_reason = review_triage.REFUTED, reason
        elif finding.status == review_triage.REFUTED:
            finding.status = "Open"
    return [finding for _stamp, finding in filed]


def _filed_stamp(row: dict[str, Any]) -> str:
    """The stamp of a finder's `review_finding` row: the key it was written with (taken before the
    ledger masks the row's words) and the instance and epoch that filed it."""
    return review_triage.row_stamp(
        str(row.get("key", "") or ""),
        str(row.get("instance_path", "") or ""),
        int(row.get("epoch", 0) or 0),
    )


def _filed_stamps(run_id: str) -> set[str]:
    """The stamps of every finding run *run_id* has filed."""
    return {
        _filed_stamp(row)
        for row in journal_mod.ledger(run_id, kinds={journal_mod.REVIEW_FINDING})
        if isinstance(row, dict) and "judged" not in row
    }


def record_review(ctl: Any, item: Any, inst: Any, result: Any) -> None:
    """What step *item*'s settled *result* says for the run's review list
    (``RunController._apply``).

    Review findings → Run Ledger. ANY stage whose output carries `findings` is a review-producing
    stage — the `{{block:finding-record}}` prompt contract is what makes it one, not the node kind,
    so `audit-sweep`'s parallel infer nodes and a judge gate both land here without either
    declaring anything new. Each finding is written RAW (the reviewer's own claimed `location`),
    one row per finding, and goes on in the step's output with its row's stamp, so a step that
    judges it names the row. Anchor validation deliberately does NOT happen here: findings must be
    validated against the ACTUAL diff "before render", and the diff at settle time is not the diff
    the human will be looking at — the worker keeps working. The panel re-anchors on every read.
    A `findings` key in a DICT output, or a JSON string that mentions one — an `infer` node
    returns its JSON as text. The cheap substring test comes first so a megabyte of prose is not
    JSON-parsed on every settle.

    Then, for a step that succeeded judging one finding (a verify panel's verifier): its verdict on
    the row its item names. A failed step's answer is never bound, so the finding goes on as the
    seed has it, and the list says nothing the run did not do.
    """
    raw_out = result.output
    if isinstance(raw_out, dict) or (isinstance(raw_out, str) and '"findings"' in raw_out):
        findings, keyed = review_triage.keyed_findings(
            raw_out,
            run_id=ctl.run.id,
            node_id=item.node.id or item.path,
            instance_path=item.path,
            epoch=inst.epoch,
            filed=_filed_stamps(ctl.run.id) if review_triage.carries_stamps(raw_out) else (),
        )
        if findings:
            result.output = raw_out = keyed
        for finding in findings:
            ctl.journal.write(
                journal_mod.REVIEW_FINDING,
                instance_path=item.path,
                node_id=item.node.id,
                epoch=inst.epoch,
                template=str(ctl.run.workflow_name or ""),
                **finding.to_dict(),
            )
    if result.state not in SUCCESS_STATES:
        return
    verdict = review_triage.verdict_on(raw_out, item.item if item.has_item else None)
    if verdict is None:
        return
    stamp, refuted, reason = verdict
    if not stamp and not refuted:
        return
    row: dict[str, Any] = {"judged": stamp, "refuted": refuted, "refute_reason": reason}
    if not stamp:
        row["note"] = "a refutation of an item that names no filed finding: no row was marked"
    ctl.journal.write(
        journal_mod.REVIEW_FINDING,
        instance_path=item.path,
        node_id=item.node.id,
        epoch=inst.epoch,
        **row,
    )


def _counts(anchored: list[AnchoredFinding]) -> dict[str, int]:
    return {
        "total": len(anchored),
        "anchored": sum(1 for a in anchored if a.anchored),
        "unanchored": sum(1 for a in anchored if not a.anchored),
    }


async def review_findings(run_id: str) -> dict[str, Any]:
    """The triage payload: every emitted finding, anchored against the run's diff right now."""
    run = store.get(run_id)
    if run is None:
        return {"ok": False, "code": "WF_RUN_NOT_FOUND", "message": f"no run {run_id!r}"}
    path, diff, truncated = await workspace_diff(run)
    anchored = review_triage.validate_anchors(findings_for(run_id), diff)
    return {
        "ok": True,
        "run_id": run_id,
        "workspace": path,
        "diff": diff,
        "diff_truncated": truncated,
        "findings": [a.to_dict() for a in anchored],
        "counts": _counts(anchored),
        "terminal": run.status in service.TERMINAL_RUN_STATUSES,
    }


def _decisions_from(raw: Any) -> tuple[list[TriageDecision], str]:
    """Parse the POST body's decision list. Returns `(decisions, error_message)`.

    An unknown outcome is an ERROR, not a default. Defaulting it either way decides for the user:
    to `accept` it would dispatch something nobody accepted, and to `reject` it would file a
    calibration record blaming the reviewer for a client-side typo.
    """
    if not isinstance(raw, list):
        return [], "decisions must be a list"
    out: list[TriageDecision] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return [], "each decision must be an object"
        key = str(entry.get("key", "") or "").strip()
        outcome = str(entry.get("outcome", "") or "").strip().lower()
        if not key:
            return [], "each decision needs a finding key"
        if outcome not in {o.value for o in TriageOutcome}:
            return [], f"unknown outcome {outcome!r} — use 'accept' or 'reject'"
        out.append(
            TriageDecision(
                key=key,
                outcome=TriageOutcome(outcome),
                reason=str(entry.get("reason", "") or "")[:1000],
            )
        )
    return out, ""


async def apply_triage(run_id: str, raw_decisions: Any, *, dispatch: bool = True) -> dict[str, Any]:
    """Accept/reject findings, dispatch the accepted subset, calibrate the rejections.

    `dispatch=False` is a dry run: the triage is computed and returned, the calibration records are
    NOT written and nothing is delivered. It exists so the panel can preview the brief before the
    user commits, which is the propose-don't-write posture applied to the dispatch itself.
    """
    run = store.get(run_id)
    if run is None:
        return {"ok": False, "code": "WF_RUN_NOT_FOUND", "message": f"no run {run_id!r}"}
    decisions, problem = _decisions_from(raw_decisions)
    if problem:
        return {"ok": False, "code": "WF_TRIAGE_BAD_DECISIONS", "message": problem}

    _, diff, _ = await workspace_diff(run)
    anchored = review_triage.validate_anchors(findings_for(run_id), diff)
    result = review_triage.triage(anchored, decisions)

    if not dispatch:
        return {
            "ok": True,
            "run_id": run_id,
            "dry_run": True,
            "brief": review_triage.dispatch_brief(result.accepted) if result.accepted else "",
            **result.to_dict(),
            "receipt": review_triage.DispatchReceipt(reason="dry_run").to_dict(),
        }

    def deliver(target: str, brief: str) -> bool:
        outcome = service.steer_run(target, brief)
        if outcome.get("ok"):
            return True
        # A terminal run has no iteration boundary left to drain the queue at. Park the brief so
        # the accepted work is not lost, and let the receipt say it needs a fresh run — rather
        # than starting one unasked.
        run.extra["review_handoff_brief"] = brief
        store.save(run)
        return False

    receipt = review_triage.dispatch_accepted(result, deliver=deliver, target=run_id)
    if not receipt.delivered and receipt.reason == "delivery_refused":
        receipt.reason = "handoff_parked"

    calibration = review_triage.calibration_records(result, template=str(run.workflow_name or ""))
    if calibration:
        journal = journal_mod.Journal(run_id)
        for row in calibration:
            journal.write(journal_mod.JUDGE_DIVERGENCE, **row)

    return {
        "ok": True,
        "run_id": run_id,
        "dry_run": False,
        **result.to_dict(),
        "receipt": receipt.to_dict(),
        "calibrated": len(calibration),
        "auto_apply_candidates": [
            a.finding.key for a in review_triage.auto_apply_candidates(result)
        ],
    }
