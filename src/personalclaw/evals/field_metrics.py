"""Loop-3 live field metrics beside lab results + ``lab_field_divergence`` (E3 / ES-9).

Amendment E3's contract, verbatim: *"Loop 3 — live quality (field): derived metrics,
computed by query, stored nowhere new (the §4.4 discipline): per-template/per-action-type
👍/👎 rate + edit-before-approve rate from plan 58's FEEDBACK-SIGNAL store, plus
approval/rejection/undo rates from the earned-autonomy ledger. Rendered BESIDE lab results
on the §10 Learning-page tab — one row per subject: lab score (Loop 1, pinned) | gate
status (Loop 2) | field trend (Loop 3). A subject whose lab score rose while its field
trend fell is flagged ``lab_field_divergence`` — the honest "should-be vs is" check, and a
§4.2 trust-record demotion signal."*

**Everything here is a query.** No file is written by any function in this module except
through the demotion machinery that already exists (``autonomy.demote`` via
``ladder.revoke_scope``/``revoke_granted_scopes``). The sources are all shipped records:

* 👍/👎 — FEEDBACK-SIGNAL's ``feedback.jsonl`` current verdicts, attributed by
  ``producer_id == subject`` — the SAME attribution :func:`personalclaw.guardrails.
  autonomy._feedback_rejections` already uses, so the field row and the ladder's own
  evidence can never disagree about whose thumbs these are.
* edit-before-approve — the run journal's ``user_edited_mid_flight`` events against
  human-answered gates (plan 58 defers explicit edit records as an open question; the
  journal event is the flywheel's own "gold" edit signal, and it exists today).
  Template-scoped only: no record anywhere captures an edit on an action type's output,
  so that cell is ``None`` — *not measured*, never ``0.0``.
* approvals/rejections/undos — the SEL approval verdicts through
  :func:`personalclaw.guardrails.autonomy.approval_outcome_series` (one attribution
  dialect, again) plus the reversal records ``guardrails.ladder`` keeps for undo.
* lab score (Loop 1) — the newest ``results.tsv`` study row for the subject, pin and all.
* gate status (Loop 2) — the newest gate report attached to a proposal targeting the
  subject, projected through the SAME :func:`personalclaw.evals.gate.summary` the inbox
  row renders.

**The divergence is the point.** ``lab_field_divergence`` flags a subject whose newest lab
measurement ROSE (``score_new > score_old``) while its live field trend is ``falling`` and
the field evidence postdates the lab row — lab said "better", the user's own verdicts say
"worse", which voids the "the system behaves well" premise behind any standing autonomy
grant. :func:`sweep_lab_field_divergence` files the §4.2 demotion mechanically: a
divergent action type loses its OWN standing grant (:func:`personalclaw.guardrails.ladder.
revoke_scope`), and a divergent template revokes standing grants wholesale
(:func:`~personalclaw.guardrails.ladder.revoke_granted_scopes`) — the same consequence a
failed §2 study and a nodding gate already carry, because all three are template-scoped
proof that the evidence autonomy rests on is wrong. Both paths are gated on a standing
grant, which is what makes a STANDING divergence naturally idempotent, and re-granting
always takes a click.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── constants (module constants, NOT config: a bar an operator can lower is not a bar) ──

#: The one window every field source is read over, in days. One number on purpose: a row
#: whose thumbs, edits and approvals covered three different spans would be comparing
#: nothing to nothing.
FIELD_WINDOW_DAYS = 30

#: Field signals before a trend verdict counts as evidence — ``attention_trend``'s own
#: reasoning: "falling over 2 thumbs" is a sample-size artifact, not a signal.
FIELD_TREND_MIN_SIGNALS = 6

#: How far the newer half's good-rate must move before the verdict leaves ``flat``. Rates
#: live in [0, 1], so the bar is absolute rather than multiplicative.
FIELD_TREND_MIN_DELTA = 0.15

#: The ``source`` every divergence demotion carries — also the evidence-id prefix, so the
#: revocation notice's dedup key is per subject and a standing divergence files ONE row.
DIVERGENCE_SOURCE = "lab_field_divergence"

#: The two subject families E3 names. Closed on purpose: a third family must decide where
#: its lab rows, its field signals and its demotion consequence come from before it gets
#: a row, or the table would render columns nothing can ever fill.
SUBJECT_TEMPLATE = "template"
SUBJECT_ACTION_TYPE = "action_type"

#: The ``results.tsv`` kinds that count as a subject's LAB (Loop-1) measurement. Study
#: rows key ``scenario_id`` to the template id (``studies._pin_for``); gate rows key it to
#: a subset hash and are Loop-2's column, not Loop-1's.
LAB_KINDS = ("template_ab",)

# Bounded display reads, same posture and numbers as `workflows.handlers._attention_scopes`.
_RUN_SCAN_LIMIT = 120
_PER_SCOPE_RUNS = 15


def _epoch(raw: Any) -> float | None:
    """Epoch seconds from a journal/ledger timestamp — float, numeric string, or ISO.

    ``None`` when nothing parses: a fabricated timestamp would silently decide the
    post-ship check, which is the one comparison the divergence flag hangs on.
    """
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = float(text)
        return value if value > 0 else None
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _score(raw: object) -> float | None:
    """A ledger score cell as a float, or ``None`` — the TSV stores ``None`` as ``""``,
    and reading that back as ``0.0`` would turn "not measured" into "scored zero"."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ── the field trend ───────────────────────────────────────────────────────────


def field_trend(goods: Sequence[bool], *, min_signals: int = FIELD_TREND_MIN_SIGNALS) -> str:
    """``rising`` / ``falling`` / ``flat`` / ``""`` over a time-ordered outcome series.

    ``goods`` is oldest first; each entry is one field verdict (👍 or a clean approval is
    ``True``, 👎 / an edit-before-approve / a rejection / an undo is ``False``). The
    verdict compares the older half's good-rate to the newer half's —
    :func:`personalclaw.workflows.introspection.attention_trend`'s shape, because the
    consumer is the same kind of one-word chip. ``""`` is *unmeasured*, a different fact
    from ``flat``, and the caller must keep them apart.
    """
    if len(goods) < min_signals:
        return ""
    half = len(goods) // 2
    older = goods[:half]
    newer = goods[-half:]
    older_rate = sum(older) / len(older)
    newer_rate = sum(newer) / len(newer)
    if newer_rate <= older_rate - FIELD_TREND_MIN_DELTA:
        return "falling"
    if newer_rate >= older_rate + FIELD_TREND_MIN_DELTA:
        return "rising"
    return "flat"


# ── the per-subject projections ───────────────────────────────────────────────


@dataclass(frozen=True)
class FieldSummary:
    """One subject's Loop-3 field metrics — derived per query, stored nowhere.

    Every rate is ``None`` when its denominator is zero. A subject nobody has thumbed is
    not a subject with 0% approval; rendering the absence as a number is the exact
    dishonesty the eval panels' "not measured" string exists to avoid.
    """

    ups: int = 0
    downs: int = 0
    thumb_rate: float | None = None
    edited_runs: int = 0
    clean_approved_runs: int = 0
    edit_before_approve_rate: float | None = None
    approvals: int = 0
    rejections: int = 0
    undos: int = 0
    approval_rate: float | None = None
    signals: int = 0
    trend: str = ""
    last_signal_ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ups": self.ups,
            "downs": self.downs,
            "thumb_rate": self.thumb_rate,
            "edited_runs": self.edited_runs,
            "clean_approved_runs": self.clean_approved_runs,
            "edit_before_approve_rate": self.edit_before_approve_rate,
            "approvals": self.approvals,
            "rejections": self.rejections,
            "undos": self.undos,
            "approval_rate": self.approval_rate,
            "signals": self.signals,
            "trend": self.trend,
        }


@dataclass(frozen=True)
class SubjectRow:
    """One Learning-tab row: lab score (Loop 1, pinned) | gate status (Loop 2) | field
    trend (Loop 3), plus the divergence verdict the sweep acts on."""

    subject_kind: str
    subject: str
    lab: dict[str, Any] | None = None
    gate: dict[str, Any] | None = None
    field: FieldSummary = field(default_factory=FieldSummary)
    lab_field_divergence: bool = False
    divergence_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_kind": self.subject_kind,
            "subject": self.subject,
            "lab": dict(self.lab) if self.lab else None,
            "gate": dict(self.gate) if self.gate else None,
            "field": self.field.to_dict(),
            "lab_field_divergence": self.lab_field_divergence,
            "divergence_reason": self.divergence_reason,
        }


def _thumb_signals(subject: str) -> list[tuple[float, bool]]:
    """(ts, 👍?) for every current feedback verdict whose ``producer_id`` is the subject.

    Any producer KIND matches — the attribution is ``producer_id == subject``, exactly
    :func:`personalclaw.guardrails.autonomy._feedback_rejections`'s rule, so the same 👎
    that blocks a promotion also moves this row.
    """
    from personalclaw.feedback import producer_verdict_series

    out: list[tuple[float, bool]] = []
    for (_kind, pid), series in producer_verdict_series(window_days=FIELD_WINDOW_DAYS).items():
        if pid != subject:
            continue
        out.extend((ts, verdict == "up") for ts, verdict in series)
    return out


def _template_run_signals() -> dict[str, list[tuple[float, bool]]]:
    """Per-template (run start, approved-without-edit?) pairs from recent run journals.

    A run yields ONE signal, and only when a human actually decided something in it: a
    ``user_edited_mid_flight`` event is an edit-before-approve (bad), a human-answered
    gate with no edit is a clean approval (good), and a run whose gates were all
    auto-approved yields NOTHING — nobody looked, so it is evidence of nothing. Bounded
    exactly like ``_attention_scopes`` (the §4.4 discipline this module inherits).
    """
    from personalclaw.workflows import journal
    from personalclaw.workflows import store as run_store

    runs, _total = run_store.list_runs(limit=_RUN_SCAN_LIMIT)
    out: dict[str, list[tuple[float, bool]]] = {}
    for run in runs:
        scope = run.workflow_name or ""
        if not scope:
            continue
        bucket = out.setdefault(scope, [])
        if len(bucket) >= _PER_SCOPE_RUNS:
            continue
        try:
            events = journal.ledger(run.id)
        except Exception:  # noqa: BLE001 — one unreadable journal must not stop the table
            continue
        edited = False
        human_gate = False
        for event in events:
            kind = str(event.get("kind") or "")
            if kind == "user_edited_mid_flight":
                edited = True
            elif kind == "gate_resolved":
                answer = event.get("answer")
                if not (isinstance(answer, dict) and answer.get("auto")):
                    human_gate = True
        if not edited and not human_gate:
            continue
        started = _epoch(run.started_at or run.created_at) or 0.0
        bucket.append((started, not edited))
    return {scope: pairs for scope, pairs in out.items() if pairs}


def _action_type_signals(key: str) -> tuple[list[tuple[float, bool]], int, int, int]:
    """(signals, approvals, rejections, undos) for one action type from the SEL tail and
    the reversal store — the "earned-autonomy ledger" half of E3's field metrics."""
    from personalclaw.guardrails.autonomy import approval_outcome_series
    from personalclaw.guardrails.ladder import reversal_records

    signals = list(approval_outcome_series(key, window_days=FIELD_WINDOW_DAYS))
    approvals = sum(1 for _ts, good in signals if good)
    rejections = sum(1 for _ts, good in signals if not good)
    undos = 0
    cutoff = datetime.now(timezone.utc).timestamp() - FIELD_WINDOW_DAYS * 86_400
    for record in reversal_records():
        if record.action_type != key or not record.reversed_at:
            continue
        ts = _epoch(record.reversed_at)
        if ts is None or ts < cutoff:
            continue
        undos += 1
        signals.append((ts, False))
    return signals, approvals, rejections, undos


def _summarize(
    thumb: list[tuple[float, bool]],
    run_signals: list[tuple[float, bool]] | None,
    ladder_signals: list[tuple[float, bool]] | None,
    counts: tuple[int, int, int],
) -> FieldSummary:
    """Fold one subject's signal streams into the row's :class:`FieldSummary`."""
    ups = sum(1 for _ts, good in thumb if good)
    downs = len(thumb) - ups
    edited = sum(1 for _ts, good in (run_signals or []) if not good)
    clean = sum(1 for _ts, good in (run_signals or []) if good)
    approvals, rejections, undos = counts
    decided = approvals + rejections + undos
    merged = sorted(thumb + list(run_signals or []) + list(ladder_signals or []))
    goods = [good for _ts, good in merged]
    return FieldSummary(
        ups=ups,
        downs=downs,
        thumb_rate=round(ups / len(thumb), 4) if thumb else None,
        edited_runs=edited,
        clean_approved_runs=clean,
        edit_before_approve_rate=(
            round(edited / (edited + clean), 4)
            if run_signals is not None and (edited + clean)
            else None
        ),
        approvals=approvals,
        rejections=rejections,
        undos=undos,
        approval_rate=(
            round(approvals / decided, 4) if ladder_signals is not None and decided else None
        ),
        signals=len(merged),
        trend=field_trend(goods),
        last_signal_ts=merged[-1][0] if merged else 0.0,
    )


def _lab_rows_by_subject() -> dict[str, list[dict]]:
    """Loop-1 ledger rows grouped by subject, in append (= chronological) order."""
    from personalclaw.evals import store

    grouped: dict[str, list[dict]] = {}
    for row in store.read_results():
        if str(row.get("kind") or "") not in LAB_KINDS:
            continue
        subject = str(row.get("scenario_id") or "")
        if subject:
            grouped.setdefault(subject, []).append(row)
    return grouped


def _lab_view(rows: list[dict]) -> dict[str, Any] | None:
    """The newest lab row as the tab's Loop-1 cell — score, pin, and whether it ROSE.

    ``rose`` is ``None`` when either arm went unmeasured: "the lab did not measure a
    rise" and "the lab measured no rise" are different facts, and only the second may
    feed a demotion.
    """
    if not rows:
        return None
    newest = rows[-1]
    score_new = _score(newest.get("score_new"))
    score_old = _score(newest.get("score_old"))
    return {
        "score": score_new,
        "previous": score_old,
        "rose": None if score_new is None or score_old is None else score_new > score_old,
        "verdict": str(newest.get("verdict") or ""),
        "study_id": str(newest.get("study_id") or ""),
        "model_fp": str(newest.get("model_fp") or ""),
        "ts": _epoch(newest.get("ts")),
    }


def _gate_view(subject: str) -> dict[str, Any] | None:
    """The newest Loop-2 gate report for the subject, through the SAME projection the
    inbox row renders — a second projection here would eventually disagree with it."""
    from personalclaw.evals import gate
    from personalclaw.learning import proposals

    report = proposals.newest_gate_for_target(subject)
    if report is None:
        return None
    return gate.summary(report)


def _diverged(lab: dict[str, Any] | None, summary: FieldSummary) -> tuple[bool, str]:
    """The E3 flag: lab rose, field trend fell, and the field evidence postdates the lab.

    All three clauses are conjunctive and each has a vacuous partner in the tests: no lab
    row / an unmeasured rise / a rising-flat-unmeasured field trend / field signals that
    all predate the lab row each keep the flag OFF, because the flag files a demotion and
    a demotion must rest on a measured contradiction, never on an absence. An
    unparseable lab timestamp does NOT veto the flag — the contradiction is real in that
    case and only its ordering is unknown, and the fail-safe direction is the plan's
    explicit ruling (the cost of over-demoting is a re-grant click).
    """
    if lab is None or lab.get("rose") is not True:
        return False, ""
    if summary.trend != "falling":
        return False, ""
    lab_ts = lab.get("ts")
    if isinstance(lab_ts, (int, float)) and summary.last_signal_ts <= lab_ts:
        return False, ""
    return True, (
        f"lab score rose to {lab.get('score')} (from {lab.get('previous')}, "
        f"{lab.get('study_id') or 'unpinned run'}) while the live field trend is falling "
        f"over {summary.signals} field signal(s)"
    )


def subject_rows() -> list[SubjectRow]:
    """One row per subject — the whole Learning-tab table, computed on request.

    Subjects are the union E3 names: every workflow template with recent runs or a lab
    row, and every registered action type. Divergent rows sort first (they are the rows
    the tab exists to surface), then templates before action types, then by name.
    """
    from personalclaw.guardrails.autonomy import registered_action_types
    from personalclaw.guardrails.rungs import ensure_core_action_types

    ensure_core_action_types()
    specs = registered_action_types()
    action_type_keys = {spec.key for spec in specs}

    lab_by_subject = _lab_rows_by_subject()
    run_signals = _template_run_signals()

    rows: list[SubjectRow] = []
    # The registry decides the family, not the evidence: a lab row ABOUT an action type
    # must not reclassify it as a template, because the family decides which demotion
    # the divergence files (its own grant vs the wholesale revocation).
    template_names = sorted((set(lab_by_subject) | set(run_signals)) - action_type_keys)
    for name in template_names:
        summary = _summarize(_thumb_signals(name), run_signals.get(name, []), None, (0, 0, 0))
        lab = _lab_view(lab_by_subject.get(name, []))
        flagged, reason = _diverged(lab, summary)
        rows.append(
            SubjectRow(
                subject_kind=SUBJECT_TEMPLATE,
                subject=name,
                lab=lab,
                gate=_gate_view(name),
                field=summary,
                lab_field_divergence=flagged,
                divergence_reason=reason,
            )
        )

    for spec in specs:
        signals, approvals, rejections, undos = _action_type_signals(spec.key)
        summary = _summarize(
            _thumb_signals(spec.key), None, signals, (approvals, rejections, undos)
        )
        lab = _lab_view(lab_by_subject.get(spec.key, []))
        flagged, reason = _diverged(lab, summary)
        rows.append(
            SubjectRow(
                subject_kind=SUBJECT_ACTION_TYPE,
                subject=spec.key,
                lab=lab,
                gate=_gate_view(spec.key),
                field=summary,
                lab_field_divergence=flagged,
                divergence_reason=reason,
            )
        )

    rows.sort(key=lambda r: (not r.lab_field_divergence, r.subject_kind, r.subject))
    return rows


# ── the mechanical §4.2 demotion signal ───────────────────────────────────────


def _evals_enabled() -> bool:
    """The ``evals.enabled`` kill switch, failing CLOSED — this sweep demotes autonomy
    from evals-derived evidence, so "could not read the switch" must not resolve to
    "demote things"."""
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().evals.enabled)
    except Exception:  # noqa: BLE001
        logger.debug("evals enabled check failed — divergence sweep skipped", exc_info=True)
        return False


def _any_standing_grant() -> bool:
    from personalclaw.guardrails.autonomy import registered_action_types, rung_state
    from personalclaw.guardrails.rungs import ensure_core_action_types

    ensure_core_action_types()
    for spec in registered_action_types():
        state = rung_state(spec.key)
        if state is not None and state.granted_at:
            return True
    return False


def sweep_lab_field_divergence(rows: list[SubjectRow] | None = None) -> list[str]:
    """File the §4.2 trust-record demotion for every divergent subject. Returns them.

    The mechanical half of the atom's second clause, driven by the gateway's autonomy
    sweep — never by a GET, because a read surface must not demote things. Priced only
    when something is at stake: with no standing grant anywhere there is nothing a
    divergence could void, so the journal/SEL walk is skipped entirely — which, combined
    with both demotion paths' own standing-grant gating, is what makes a STANDING
    divergence idempotent: after the first sweep nothing is granted, and the next sweep
    files nothing.

    Two consequences, one per subject family, both through the shipped §4.2 machinery:

    * an ``action_type`` subject loses its OWN grant
      (:func:`personalclaw.guardrails.ladder.revoke_scope` — floor, cooldown,
      trust-record ``revoked`` flag, SEL audit, one notice);
    * a ``template`` subject revokes standing grants wholesale
      (:func:`~personalclaw.guardrails.ladder.revoke_granted_scopes`) — the SAME
      consequence a failed §2 study and a nodding gate carry, because all three are
      template-scoped proof that "the system behaves well" evidence is void.

    One subject's failure never stops the rest, and a failure files nothing for it.
    """
    if not _evals_enabled():
        return []
    if rows is None:
        if not _any_standing_grant():
            return []
        try:
            rows = subject_rows()
        except Exception:  # noqa: BLE001 — a failed read must demote nothing
            logger.warning(
                "lab_field_divergence: subject rows unreadable — filed nothing", exc_info=True
            )
            return []
    from personalclaw.guardrails import ladder

    filed: list[str] = []
    for row in rows:
        if not row.lab_field_divergence:
            continue
        cause = f"Lab-field divergence on {row.subject}: {row.divergence_reason}."
        evidence_id = f"{DIVERGENCE_SOURCE}:{row.subject}"
        try:
            if row.subject_kind == SUBJECT_ACTION_TYPE:
                if ladder.revoke_scope(
                    row.subject, cause=cause, evidence_id=evidence_id, source=DIVERGENCE_SOURCE
                ):
                    filed.append(row.subject)
            else:
                if ladder.revoke_granted_scopes(
                    cause=cause, evidence_id=evidence_id, source=DIVERGENCE_SOURCE
                ):
                    filed.append(row.subject)
        except Exception:  # noqa: BLE001 — one subject's failure must not stop the rest
            logger.warning(
                "lab_field_divergence: demotion signal failed for %s", row.subject, exc_info=True
            )
    return filed


__all__ = [
    "DIVERGENCE_SOURCE",
    "FIELD_TREND_MIN_DELTA",
    "FIELD_TREND_MIN_SIGNALS",
    "FIELD_WINDOW_DAYS",
    "LAB_KINDS",
    "SUBJECT_ACTION_TYPE",
    "SUBJECT_TEMPLATE",
    "FieldSummary",
    "SubjectRow",
    "field_trend",
    "subject_rows",
    "sweep_lab_field_divergence",
]
