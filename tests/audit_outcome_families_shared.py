"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_audit_outcome_families.py``

Its checks:
``tests/rails/test_audit_outcome_families_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from personalclaw.sel import _outcome_token_match


def _values(family: dict) -> tuple[str, ...]:
    return tuple(str(v) for v in family["values"])


def _claims(family: dict, word: str) -> bool:
    """Whether ``family`` matches ``word`` under the SERVER's matcher — not a local re-derivation.
    A test that re-implements the matcher proves only that the test agrees with itself."""
    return any(_outcome_token_match(v, word) for v in _values(family))


#: THE LEDGER THAT REPLACED THE CEILING (#3443). Every emitted word that belongs in no
#: filter family, and WHY — keyed by the reason, because the reasons are shared and 62
#: near-identical sentences would be filler rather than a decision.
#:
#: 🔴 IT IS A NAMED SET, NOT A NUMBER, AND THAT IS THE WHOLE POINT. The old rail asserted
#: ``len(unclassified) <= 34``, which had two escapes a number always has: a new word could
#: be absorbed by editing one digit, and — because the scanner only saw inline literals —
#: could be made to disappear entirely by moving it into a constant. There is no digit to
#: edit here. A new word fails BY NAME, and the only ways to make it pass are to put it in a
#: family or to write down why it belongs in none. Both are decisions; neither is a bump.
#:
#: Asserted in both directions: a word here must NOT be claimed by a family (the two records
#: cannot disagree), and it must still be emitted (the ledger cannot rot into a list of words
#: nobody writes any more — the same silent-zero defect as an unoffered value, from the other
#: side).
_NO_FAMILY: dict[str, tuple[str, ...]] = {
    "a LIFECYCLE step rather than a verdict: the record says what happened next, not whether "
    "anything was allowed, refused or broke — so every pill would be wrong about it": (
        "accepted",
        "archived",
        "cancelled",
        "complete",
        "continue",
        "created",
        "delivered",
        "flush_produced",
        "grounded",
        "imported",
        "installed",
        "invoked",
        "launched",
        "outcome_resolved",
        "passed",
        "pending_outcome",
        "queued",
        "ran",
        "removed",
        "reused",
        "scanned",
        "spawned",
        "started",
        "suggested",
        "surfaced",
        "tie",
    ),
    # `cancelled` is an approval, or the tool call waiting on it, whose OWNER was stopped
    # before anyone answered: nobody allowed it, nobody refused it and nothing broke, so a
    # Denied pill returning it would report a refusal that never happened. `archived` and
    # `removed` are the audit trail's own rotation and retention steps (`sel.rotated`,
    # `sel.archive_expired`). `imported` is a legacy trigger file's rows brought over, switched off
    # and waiting for review (`trigger.import`, #3691): it records that an import happened, and
    # the rows' own grant decisions are audited separately (`trigger.grant`).
    # `complete` is how a chat turn ended (`turns.Outcome`), as an earlier answer Regenerate kept
    # reads (`dashboard.turn_rows`): its turn ran to its end, which says nothing was refused.
    # `absent` is an app setup step deleting a credential that was not stored (`app_cli`'s
    # `removed if removed else absent`): nothing to delete, nothing refused, nothing broke.
    "the work did not need doing, so nothing ran: a no-op is not a success, not a refusal of "
    "a caller, and not a fault": (
        "absent",
        "expired",
        "flush_skipped",
        "no_change",
        "noop",
        "not_triggered",
        "off_duty",
        "quiet",
        "skip",
        "skipped",
    ),
    "a PRECONDITION was absent, so nothing was asked for and denied and nothing broke — "
    "putting it in a family would report a refusal or a fault that never happened": (
        "no_model",
        "no_screenshot",
        "no_signal",
        "no_target",
        "verifier_absent",
    ),
    "the MODE a control resolved to, or the SHAPE of a result, rather than its verdict — a "
    "family here would make a pill accuse a working control every time it fires": (
        "bounded",
        "default",
        # An action that did its work at its no-model floor (`ActionResult.outcome`): the run's
        # shape, recorded `degraded` in its history, and neither a refusal nor a fault.
        "degraded",
        "downgraded",
        "halted_on_budget",
        "interrupted",
        "narrowed",
        "open",
        "partial",
        "permanent",
        "preview",
        "session_scope_only",
        "transient",
        "trusted",
    ),
    "ARGUABLE, and deliberately left narrow: classifying it is a judgement per word rather "
    "than a sweep, and getting it wrong on a security surface is worse than leaving a pill "
    "narrow — a Denied pill that returns a reaped subagent is a lie about a refusal": (
        "bypass",
        "fail_open",
        "fanout_breaker_tripped",
        "fanout_budget_exceeded",
        "killed",
        "reaped",
        "sigkill",
        "tampered",
        "too_large",
        "ungated",
        "ungated_declared",
    ),
    "a NEGATION, which must never read as its own affirmative: under the old substring match "
    "`approved` claimed it, so the Succeeded pill would have returned a refused auto-approval": (
        "not_auto_approved",
    ),
    # `truncated` is `bundled_model.DOWNLOAD_TRUNCATED`, and the tempting reading is
    # wrong in a way worth writing down: it is NOT context or prompt truncation, and it is not
    # the `failed` family either. It is the verdict of `verify_download()` when the fetched
    # weight is missing, empty or short of the signed-off `size_bytes` — a genuine fault, and
    # if it were an audit word `failed` would be exactly right. It is not one. Measured:
    # `bundled_model.py` and the `bundled-chat` app that drives it contain ZERO SEL writers and
    # ZERO SEL imports (`sel()` appears in 134 files, `log_api_access` in 113, neither in these
    # two), so no audit row can ever carry `outcome=truncated`. Its three sites are all
    # `DownloadResult(outcome=…)` — a dataclass field the SPA reads to pick which of four
    # sentences to print. Putting it in `failed` would add the third invented-in-effect value
    # to a pill, after `not_permitted` and `timeout`: a term that can only ever return zero
    # rows, passing `test_no_family_offers_a_term_nobody_writes` only because that rail measures
    # the census and the census is over-inclusive here BY DESIGN. The arithmetic makes it
    # plainer still — of the seven-word `DOWNLOAD_*` vocabulary the census sees just this one,
    # because `bad-status`/`digest-mismatch`/`over-budget` are hyphenated and `_OUTCOME_WORD`
    # rejects them while `unreachable`/`cancelled` are passed positionally. So a "Failed" pill
    # would offer one arbitrary seventh of one subsystem's return enum and match nothing.
    # `counted_complete` and `max_iterations` are a loop's `iteration` row in its run's journal:
    # the word a loop ends with, written where a cycle its owner ended with a Deny ends the loop
    # (`declines.end_cycle`). No audit row carries either.
    # `acted`, `help` and `unreadable` are `proactive.answer.Answered(outcome=…)`: what an answer to
    # the Morning triage digest came to, which the reply route turns into its wire answer and a
    # channel into the sentence it says back. The answer's audit row is `triage_reply`, `success`.
    # `scope_violation` and `not_scored` are an optimize-harness candidate's row in its search's
    # own ledger (`evals.optimize.LedgerRow`): a candidate dead for touching the frozen region, and
    # one its scorer could not measure. No audit row carries either.
    "not an audit record at all — an internal ledger row, or a function's return-value "
    "discriminator, whose field is also called `outcome` and which the deliberately tree-wide "
    "scan therefore also sees. Recorded rather than excluded, because narrowing the scan is "
    "the same make-a-word-invisible move #3443 is about": (
        "acted",
        "counted_complete",
        "help",
        "max_iterations",
        "not_scored",
        "scope_violation",
        "truncated",
        "unreadable",
    ),
}


def _no_family_words() -> dict[str, str]:
    """word → the reason it is in no family. Flattened once, so a word listed twice is a
    collected error rather than a silent overwrite."""
    out: dict[str, str] = {}
    duplicated = []
    for reason, words in _NO_FAMILY.items():
        for word in words:
            if word in out:
                duplicated.append(word)
            out[word] = reason
    assert not duplicated, f"listed under two reasons — one word, one decision: {duplicated}"
    return out
