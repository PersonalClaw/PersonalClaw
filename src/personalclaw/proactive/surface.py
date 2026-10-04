"""The read model the triage surfaces bind.

One function assembles the whole digest card, and the reason it is a pure function over
already-persisted data — the run row, the triage node's output, that run's ledger slice — is
that everything the card shows was recorded by PA-2/PA-3 as it happened. Nothing here
re-derives a verdict, re-runs a gate, or asks a model anything; the "strictly read-only on
view" is a property of this module, not a rule someone has to remember.

**Three failures this read model refuses to render as "nothing happened yet".** Each is a
separate, named state, because a card that draws an empty list for all three tells the user the
opposite of the truth in two of the three cases:

``uninstalled``  the "Morning triage" template pack was never installed. There is no schedule,
                 so nothing was ever going to run. The card's job here is to offer §5.4's
                 install, not to report an empty digest.
``off``          installed, but ``proactive.triage_enabled`` is false. The schedule is dormant
                 and criterion 10 requires it to READ as dormant-but-kept.
``never_run``    installed and enabled, but no digest has completed yet — the first one has not
                 come round. An empty digest and an unrun digest are different facts.
``ready``        a digest exists. Only now are the section lists meaningful.
``error``        the read itself failed. The caller passes the exception's text through and the
                 card says so. A swallowed read error is the single most confident way to say
                 "your machine did nothing", which is the opposite of what is known.

**An unmeasured count is never a zero.** Two absences are reported as absences rather than as
zeroes, because both are reachable in normal operation:

* ``auto_stage_ran`` is False when the digest ran with no auto-execution stage at all (the
  default: ``auto_execute_enabled`` is off). "Auto-execution is off" and "auto-execution ran and
  did nothing" are different sentences and the card must not print the second for the first.
* ``ledger_complete`` is False when the provider reported ``ledger_rows: 0`` while the summary
  says items WERE dropped or refused. The ``_record`` skips rows it cannot stamp with a run
  key, and reports the absence rather than faking them; a card that showed "0 filtered" there
  would be reporting the gap as a result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from personalclaw.proactive.autoexec import not_done_note, stopped_note
from personalclaw.proactive.proposals import bind_arguments

#: The bundled WorkflowDef the pack card installs. One name, shared with the provider.
TRIAGE_WORKFLOW = "morning-triage"
#: The action node inside it whose output IS the digest. `TriageDigestActionProvider._NODE_ID`.
TRIAGE_NODE_ID = "triage"

STATE_UNINSTALLED = "uninstalled"
STATE_OFF = "off"
STATE_NEVER_RUN = "never_run"
STATE_READY = "ready"
STATE_ERROR = "error"

#: The ledger kinds the card's "In the run journal" section renders, in the order it renders them.
#: Ordered so the section reads as a narrative: what landed, what failed, what the spend floor
#: stopped, what the user answered, what the filter dropped, what the parser refused. It is the
#: run's record, not "what your machine did": a failure and a refusal are in it, so nothing may
#: render it under that heading.
JOURNAL_KINDS: tuple[str, ...] = (
    "auto_executed",
    "auto_failed",
    "skipped_budget",
    "triage_reply",
    "skipped_triage",
    "proposal_refused",
)

#: Reply verbs the card's one-tap controls emit. The same vocabulary `approval.parse_reply`
#: accepts, so a tap and a typed channel reply travel one grammar rather than two.
REPLY_YES = "yes"
REPLY_NO = "no"
REPLY_ALWAYS_YES = "always yes"
REPLY_ALWAYS_NO = "always no"
REPLY_VERBS: tuple[str, ...] = (REPLY_YES, REPLY_NO, REPLY_ALWAYS_YES, REPLY_ALWAYS_NO)

#: What became of an answer, as its ``triage_reply`` row records it. A "yes" whose action did not
#: happen is ``failed`` (it was tried) or ``refused`` (a guard or the item stopped it first), never
#: ``declined``: that is the user's "no", and recording her yes as one says she turned it down.
OUTCOME_ANSWER_EXECUTED = "executed"
OUTCOME_ANSWER_DECLINED = "declined"
OUTCOME_ANSWER_FAILED = "failed"
OUTCOME_ANSWER_REFUSED = "refused"
ANSWERS_NOT_DONE: tuple[str, ...] = (OUTCOME_ANSWER_FAILED, OUTCOME_ANSWER_REFUSED)


def run_permalink(run_id: str) -> str:
    """The digest's own run-journal deep link, via the substrate's one URL builder.

    Imported rather than formatted here so the card's permalinks and the delivered
    notification's ``statusUrl`` are the same string by construction — two builders would
    eventually disagree about which surface a run opens on.
    """
    from personalclaw.triggers.delivery import status_url

    return status_url(run_id=run_id)


def _rows(value: Any) -> list[dict[str, Any]]:
    """A list-of-dicts view of an output field, tolerating anything else as empty.

    The output is JSON a previous process wrote; a field that is not the expected shape is a
    corrupt row, not a reason to 500 the whole card.
    """
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _by_ordinal(rows: Sequence[Mapping[str, Any]], key: str = "item_id") -> dict[str, dict]:
    return {str(row.get(key, "") or ""): dict(row) for row in rows if row.get(key)}


def _item_index(output: Mapping[str, Any]) -> dict[str, dict]:
    """Ordinal → provenance, from the run's own persisted manifest projection."""
    return {
        str(row.get("ordinal", "") or ""): row
        for row in _rows(output.get("items"))
        if row.get("ordinal")
    }


def answered_ordinals(events: Sequence[Mapping[str, Any]]) -> dict[str, dict]:
    """Ordinal → the ``triage_reply`` row that already answered it, for THIS run.

    The idempotency index (criterion 9). Derived from the run's ledger rather than kept in
    memory, so a gateway restart between delivery and reply loses nothing: the second reply
    finds the first one's row and acks instead of acting twice.
    """
    out: dict[str, dict] = {}
    for event in events:
        if str(event.get("kind", "") or "") != "triage_reply":
            continue
        ordinal = str(event.get("item_ordinal", "") or "")
        if ordinal:
            out[ordinal] = dict(event)
    return out


def build_digest_view(
    *,
    enabled: bool,
    installed: bool,
    run: Mapping[str, Any] | None = None,
    output: Mapping[str, Any] | None = None,
    events: Sequence[Mapping[str, Any]] = (),
    error: str = "",
) -> dict[str, Any]:
    """Assemble §5.1's digest card from what the last digest run persisted.

    ``run`` is the run summary row, ``output`` the triage node's ``summary()`` JSON, ``events``
    that run's ledger slice. The state precedence is deliberate: an error outranks everything
    (a read that failed cannot report installedness honestly), then uninstalled, then off, then
    never-run. Reversing any two of those would let the card answer a question it did not ask —
    "no digest yet" for a machine whose triage switch is off, for instance.
    """
    if error:
        return {"state": STATE_ERROR, "error": error, "enabled": enabled, "installed": installed}
    base: dict[str, Any] = {
        "enabled": enabled,
        "installed": installed,
        "workflow": TRIAGE_WORKFLOW,
        "node_id": TRIAGE_NODE_ID,
        "error": "",
    }
    if not installed:
        return {**base, "state": STATE_UNINSTALLED}
    if not enabled:
        return {**base, "state": STATE_OFF}
    if not run or not output:
        return {**base, "state": STATE_NEVER_RUN}

    run_id = str(run.get("run_id", "") or run.get("id", "") or "")
    permalink = run_permalink(run_id)
    items = _item_index(output)
    proposals = _by_ordinal(_rows(output.get("proposals")))
    answered = answered_ordinals(events)

    # The stage reports itself: `auto_ledger_rows` is written by `AutoExecResult.summary()`
    # and by nothing else, so its PRESENCE is the honest signal that the stage ran at all.
    # `auto_executed == []` cannot carry that, because an off stage and a stage that executed
    # nothing produce the same empty list.
    auto_stage_ran = "auto_ledger_rows" in dict(output)
    auto_rows = _rows(output.get("auto_executed"))
    # Only actions that LANDED are here (`autoexec.AutoAction`); a failed dispatch is a deferral,
    # and it reaches the card as a pending row that says it was not done.
    auto_done: list[dict[str, Any]] = []
    for row in auto_rows:
        ordinal = str(row.get("item_id", "") or "")
        known = _provenance(items, ordinal)
        auto_done.append(
            {
                **known,
                "ordinal": ordinal,
                "source_id": str(row.get("source_id", "") or "") or known["source_id"],
                "action_type": str(row.get("action_type", "") or ""),
                "provider": str(row.get("provider", "") or ""),
                "rule": str(row.get("rule", "") or ""),
                "reversal": str(row.get("reversal", "") or ""),
                "undoable": bool(row.get("undoable")),
                "permalink": permalink,
            }
        )

    # The pending set is the deferred set when the stage ran, and the raw proposals when it did
    # not. Joined back to `proposals` for `pattern_key`: the "always" tap writes a rule against
    # the pattern, and `auto_deferred` rows do not carry one. Without the join the button would
    # have to invent a pattern from the action type, which is how a narrow taught rule quietly
    # becomes a broad one.
    deferred = _rows(output.get("auto_deferred"))
    source = deferred if auto_stage_ran else _rows(output.get("proposals"))
    pending: list[dict[str, Any]] = []
    for row in source:
        ordinal = str(row.get("item_id", "") or "")
        joined = proposals.get(ordinal, {})
        reply = answered.get(ordinal) or {}
        reason = str(row.get("reason", "") or "")
        action_type = str(row.get("action_type", "") or "")
        pending.append(
            {
                "ordinal": ordinal,
                "action_type": action_type,
                "tier": str(row.get("tier", "") or joined.get("tier", "") or ""),
                "pattern_key": str(joined.get("pattern_key", "") or ""),
                "clamped": bool(joined.get("clamped")),
                # What the proposal bound when its run recorded it (a task's title): what its Yes
                # carries out, and what the card shows before she answers. Re-bound here, so a
                # record from an older process or edited on disk shows and runs no more than the
                # kind declares.
                "action_config": bind_arguments(action_type, joined.get("action_config"))[0],
                "reason": reason,
                "rule": str(row.get("rule", "") or ""),
                # The digest's own text says the same sentence (`rank.render_digest`): an action it
                # tried and could not do, or one a guard held, is said as not done, never as done
                # and never as a proposal nobody tried.
                "not_done": (
                    not_done_note(reason, str(row.get("detail", "") or ""), on_card=True)
                    if auto_stage_ran
                    else ""
                ),
                "answered": bool(reply),
                "answer": str(reply.get("verb", "") or ""),
                # A "yes" that did not happen, in the words its reply recorded. Empty for an answer
                # that happened, a "no" among them.
                "answer_not_done": (
                    str(reply.get("detail", "") or "")
                    if str(reply.get("outcome", "") or "") in ANSWERS_NOT_DONE
                    else ""
                ),
                "permalink": permalink,
                **_provenance(items, ordinal),
            }
        )
    stopped = (
        stopped_note(
            (str(row.get("reason", "") or "") for row in deferred),
            budget_reason=str(output.get("budget_reason", "") or ""),
        )
        if auto_stage_ran
        else ""
    )

    ran, waiting = _the_rest(output, items, placed={row["ordinal"] for row in auto_done + pending})

    dropped = _int(output.get("dropped"))
    refused = len(_rows(output.get("refused")))
    recorded = _int(output.get("ledger_rows"))
    return {
        **base,
        "state": STATE_READY,
        "run_id": run_id,
        "status": str(run.get("status", "") or ""),
        "finished_at": str(run.get("finished_at", "") or run.get("started_at", "") or ""),
        "permalink": permalink,
        "window_start": str(output.get("window_start", "") or ""),
        "title": str(output.get("digest_title", "") or ""),
        "body": str(output.get("digest_body", "") or ""),
        # 🔴 NOT `delivered`. `DashboardState.notify` returns None, so the pipeline's own
        # `delivered` flag can only ever mean "handed to the delivery gate" — measured by driving a
        # digest inside quiet hours: the run reported `delivered: True` while the notification list
        # did not grow by one. A card that printed "delivered" there would be telling the user they
        # were notified when the gate had deliberately held it back. Renamed at the boundary so no
        # consumer can inherit the wrong claim.
        "handed_to_notify": bool(output.get("delivered")),
        "collected": _int(output.get("collected")),
        "lanes": (
            dict(output.get("lanes") or {}) if isinstance(output.get("lanes"), Mapping) else {}
        ),
        "dropped": dropped,
        "auto_stage_ran": auto_stage_ran,
        "auto_done": auto_done,
        "pending": pending,
        "ran": ran,
        "waiting": waiting,
        # Why nothing (or nothing more) ran on its own when the stage stopped as a whole: incident
        # mode, the approval ceiling, or the spend floor. The digest's text says the same sentence.
        "auto_stopped": stopped,
        "degraded": bool(output.get("degraded")),
        "journal": journal_rows(events, permalink=permalink),
        # False means "rows that should exist were not written", never "there were none".
        "ledger_complete": recorded > 0 or (dropped == 0 and refused == 0),
        "ledger_rows": recorded,
    }


def _the_rest(
    output: Mapping[str, Any], items: Mapping[str, Mapping[str, Any]], *, placed: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The kept items no other section holds: the runs that ended, and what else is waiting.

    The digest's body lists both ("What your machine did", "Also waiting"), and the card counted
    them in "N items in this window" while showing neither. ``kept`` is the gate's own record of
    what it let through (`TriageResult.summary`), so an item the user's rules filtered is counted
    in ``dropped`` and never shown; an item already ``placed`` as a proposal or an action taken is
    not shown twice. In the manifest's ordinal order.
    """
    kept = [str(o) for o in output.get("kept") or [] if str(o) in items and str(o) not in placed]
    ran: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    for ordinal in kept:
        row = {"ordinal": ordinal, **_provenance(items, ordinal)}
        if row["source"] == "run":
            # A run that failed, was stopped or was handed to you (`collect._run_materiality`).
            ran.append({**row, "needs_you": row["materiality"] == "error"})
        else:
            waiting.append(row)
    return ran, waiting


def _provenance(items: Mapping[str, Mapping[str, Any]], ordinal: str) -> dict[str, str]:
    """Title/source/store id/link for one ordinal, or empty strings when the map has no such row.

    Empty strings rather than a placeholder title: a card that printed "Item 3" for an item
    whose provenance was not recorded would be inventing the one thing the user needs to
    recognise it by.
    """
    row = items.get(ordinal) or {}
    return {
        "title": str(row.get("title", "") or ""),
        "source": str(row.get("source", "") or ""),
        # The store id the ordinal resolved to, which a reply acts on. Without it a "yes" rebuilt a
        # manifest of rows it had to drop as unaddressable, and acted on nothing.
        "source_id": str(row.get("source_id", "") or ""),
        "item_permalink": str(row.get("permalink", "") or ""),
        "materiality": str(row.get("materiality", "") or ""),
    }


def journal_rows(
    events: Sequence[Mapping[str, Any]], *, permalink: str = ""
) -> list[dict[str, Any]]:
    """The card's "In the run journal" section: the run's own ledger rows, with permalinks.

    Filtered to :data:`JOURNAL_KINDS` and sorted by that tuple's order then by sequence, so
    the section groups by what happened rather than by write order — the ledger interleaves an
    execution and the filter decision that let it through, and a reader wants them apart.
    """
    order = {kind: i for i, kind in enumerate(JOURNAL_KINDS)}
    rows = [
        {
            "kind": str(event.get("kind", "") or ""),
            "seq": _int(event.get("seq")),
            "ordinal": str(event.get("item_ordinal", "") or ""),
            "action_type": str(event.get("action_type", "") or ""),
            "rule": str(event.get("rule", "") or ""),
            "outcome": str(event.get("outcome", "") or ""),
            "reason": str(event.get("reason", "") or event.get("rationale", "") or ""),
            "detail": str(event.get("detail", "") or ""),
            "verb": str(event.get("verb", "") or ""),
            "permalink": permalink,
        }
        for event in events
        if str(event.get("kind", "") or "") in order
    ]
    rows.sort(key=lambda row: (order.get(str(row["kind"]), len(order)), _int(row["seq"])))
    return rows


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
