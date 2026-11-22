"""A run paused because its token spend went unrecorded can go on, and says why it paused.

Measured before this was written: a resumed run with a token budget, whose earlier steps never
recorded their token counts (steps run before a restart, on a runtime that reports no usage),
paused before its first new step with "token spend unrecorded; cannot pre-charge token budget",
in red. Resume paused it again at once, and nothing cleared the pause: a resume's new caps were
taken by the run's first step, after the check that paused it, so even the owner's resume with no
token budget (``max_tokens: 0``) paused again. The only way on was to cancel and start again.

Now the run takes the caps its owner resumed with before that check, so a resume with no token
budget goes on; a plain resume is refused with why, since it would only pause again; and the
page is told the pause is this one (``tokens_unrecorded``), in words, to offer that resume.
"""

from __future__ import annotations

import time

import pytest
from test_a_run_pauses_at_its_dollar_cap import (  # noqa: F401 (fixtures)
    _controller,
    _isolated_home,
    _paused,
    _Priced,
    _spec,
    _stopped_runs,
    _Supervisor,
    anyio_backend,
)

from personalclaw.approval_answer import YOU
from personalclaw.workflows import journal as J
from personalclaw.workflows import run_budget, service, store
from personalclaw.workflows.models import RunBudget, RunStatus

pytestmark = pytest.mark.anyio


async def _paused_for_unrecorded_tokens():
    """A run resumed after a restart whose earlier step recorded no token count."""
    provider = _Priced(0.0)
    c = _controller(
        _spec(2),
        provider,
        budget=RunBudget(max_tokens=10_000),
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        total_tokens=0,
        status=RunStatus.RUNNING,
    )
    store.append_jsonl(
        c.run.id,
        J.EVENTS_FILE,
        {"kind": J.STEP_COMPLETED, "instance_path": "old", "node_id": "old", "cost_usd": 0.0},
    )
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    assert provider.steps == []
    return c, provider


async def test_the_pause_says_why_in_words_and_names_itself_to_the_page():
    """🔴 Red before: the engine's own words, and nothing the page could tell it apart by."""
    c, _ = await _paused_for_unrecorded_tokens()
    saved = store.get(c.run.id)

    assert saved.error_message == run_budget.TOKENS_UNRECORDED
    assert "didn't record the tokens they used" in saved.error_message
    shown = run_budget.shown(saved)
    assert shown["tokens_unrecorded"] is True
    assert shown["at_budget"] is False


async def test_a_resume_with_no_token_budget_goes_on():
    """🔴 Red before: it paused again at once."""
    c, provider = await _paused_for_unrecorded_tokens()

    resumed = service.resume_run(
        c.run.id, supervisor=_Supervisor(c), by=YOU, budget={"max_tokens": 0}
    )

    assert resumed.get("ok") is True, resumed
    assert await _paused(c) == RunStatus.COMPLETE
    assert provider.steps == ["a0", "a1"]
    assert store.get(c.run.id).budget.max_tokens == 0


async def test_a_plain_resume_is_refused_with_why_and_the_run_stays_paused():
    """🔴 Red before: it was taken, and the run paused again with the same line."""
    c, provider = await _paused_for_unrecorded_tokens()

    bare = service.resume_run(c.run.id, supervisor=_Supervisor(c), by=YOU)

    assert bare["ok"] is False and bare["code"] == "WF_RUN_AT_BUDGET", bare
    assert "no token budget" in bare["message"]
    assert provider.steps == []
    assert store.get(c.run.id).status == RunStatus.PAUSED
