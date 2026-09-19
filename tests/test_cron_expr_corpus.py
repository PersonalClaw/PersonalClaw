"""The cron corpus the FRONTEND validator is measured against still matches croniter (#687).

`web/src/pages/schedule/cronExprCorpus.json` carries a `valid` column that is croniter's measured
verdict, and `web/src/pages/schedule/cronExpr.test.ts` asserts the frontend validator is SOUND
against it — every expression it refuses is one croniter refuses too.

That only holds while the column is TRUE. So this module is the other half of the rail: it re-asks
`schedule.validate_cron_expr` — the same function `triggers.tools.invalid_cron_refusal` calls, so
this is literally the server's own answer — for every case in the file. A croniter upgrade that
changes an answer, or a hand-edited verdict, reds HERE, where the fix is to regenerate the corpus,
rather than silently leaving the frontend check disagreeing with the refusal the server will issue.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.schedule import validate_cron_expr

CORPUS = Path(__file__).resolve().parents[1] / "web/src/pages/schedule/cronExprCorpus.json"


def _cases() -> list[dict]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]


def test_the_corpus_ships_with_the_frontend_validator_it_constrains():
    assert CORPUS.exists(), f"{CORPUS} is the shared fixture; cronExpr.test.ts reads it too"
    cases = _cases()
    # Both columns have to be populated or the soundness assertion on the other side is vacuous:
    # an all-invalid corpus proves nothing about false refusals, an all-valid one proves nothing
    # about the check catching anything.
    assert len(cases) >= 40
    assert sum(1 for c in cases if c["valid"]) >= 15
    assert sum(1 for c in cases if not c["valid"]) >= 15


@pytest.mark.parametrize("case", _cases(), ids=lambda c: repr(c["expr"]))
def test_croniter_still_agrees_with_the_recorded_verdict(case):
    assert validate_cron_expr(case["expr"]) is case["valid"], (
        f"{case['expr']!r} ({case['group']}) — croniter now says "
        f"{validate_cron_expr(case['expr'])}, the corpus records {case['valid']}. "
        "Regenerate cronExprCorpus.json rather than editing the verdict."
    )


def test_the_two_expressions_the_old_token_COUNT_got_wrong_are_both_covered():
    """🔴 THE defect, from both sides.

    The frontend counted five whitespace-separated tokens. `'99 99 * * *'` has five and croniter
    rejects it; `'@daily'` has one and croniter accepts it — so the check flagged a working
    expression and cleared a broken one, and neither answer reached the server, which validated
    the expression nowhere at all.
    """
    assert len("99 99 * * *".split()) == 5
    assert validate_cron_expr("99 99 * * *") is False
    assert len("@daily".split()) == 1
    assert validate_cron_expr("@daily") is True
    recorded = {c["expr"]: c["valid"] for c in _cases()}
    assert recorded["99 99 * * *"] is False
    assert recorded["@daily"] is True
