"""The report lease has a WRITER, so its refusal can actually fire (WF2KNO-12).

The manual-run route refuses with a 409 while ``research-report:<id>`` is held. That check was
written first and, until this seam, nothing ever wrote the claim — a route reading a key no
code sets is a refusal that can never happen, which is the shape this codebase keeps finding
(a live reader of an unwritten key). So three things are asserted here, and each has a way of
looking done while being absent:

* **THE KEY IS ONE STRING.** The runner writes it and the route reads it. Two spellings would
  make the lease silently unmatchable and every assertion about the 409 would still pass on
  each side alone, so the two spellings are compared directly.
* **THE CLAIM IS HELD DURING THE RUN, NOT AROUND IT.** A claim taken and released before the
  work begins satisfies "write_claim was called" while protecting nothing. The assertion runs
  INSIDE the locked body.
* **IT IS RELEASED ON EVERY EXIT.** A run that fails must not wedge the report until the
  claim's self-expiry, so the failing path is asserted too — not just the happy one.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.action_providers import ActionContext, ActionResult
from personalclaw.action_providers.knowledge_report_provider import KnowledgeReportActionProvider
from personalclaw.dashboard.handlers import research_reports as handlers
from personalclaw.knowledge import research_reports as rr
from personalclaw.triggers import claims


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated PERSONALCLAW_HOME. The claim store writes under it, never the real home."""
    h = tmp_path / "home"
    h.mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    import personalclaw.config as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: h, raising=False)
    return h


def _ctx() -> ActionContext:
    return ActionContext(event="test.run", context="", payload={})


def test_the_claim_id_is_one_string_across_both_layers():
    """The route reads what the runner writes — asserted, not assumed."""
    assert handlers.report_claim_id("rep-1") == rr.report_claim_id("rep-1")
    assert rr.report_claim_id("rep-1") == "research-report:rep-1"


@pytest.mark.asyncio
async def test_the_claim_is_held_INSIDE_the_run_and_released_after(home, monkeypatch):
    provider = KnowledgeReportActionProvider()
    claim_id = rr.report_claim_id("rep-1")
    seen: list[bool] = []

    async def fake_locked(action_config, ctx, timeout=30):
        # The whole point: while the body runs, the route's own check must say "running".
        seen.append(claims.is_running(claim_id))
        return ActionResult(success=True)

    monkeypatch.setattr(provider, "_execute_locked", fake_locked)
    assert claims.is_running(claim_id) is False
    result = await provider.execute({"report_id": "rep-1"}, _ctx())

    assert result.success is True
    assert seen == [True], "the claim was not held while the run was in flight"
    assert claims.is_running(claim_id) is False, "the claim outlived the run"


@pytest.mark.asyncio
async def test_a_failed_run_still_releases_the_claim(home, monkeypatch):
    """Otherwise one crash wedges the report until the claim self-expires."""
    provider = KnowledgeReportActionProvider()
    claim_id = rr.report_claim_id("rep-2")

    async def boom(action_config, ctx, timeout=30):
        raise RuntimeError("the model died")

    monkeypatch.setattr(provider, "_execute_locked", boom)
    with pytest.raises(RuntimeError):
        await provider.execute({"report_id": "rep-2"}, _ctx())
    assert claims.is_running(claim_id) is False


@pytest.mark.asyncio
async def test_a_second_run_is_skipped_rather_than_doubled(home, monkeypatch):
    """Two fires for one report collide, and the loser is a SUCCESS with a named skip.

    Success, not an error: a duplicate manual fire while a scheduled one is working is a no-op,
    and reporting it as a failure would teach an owner to retry the thing that is already
    happening.
    """
    provider = KnowledgeReportActionProvider()
    started = asyncio.Event()
    finish = asyncio.Event()
    ran = 0

    async def slow(action_config, ctx, timeout=30):
        nonlocal ran
        ran += 1
        started.set()
        await finish.wait()
        return ActionResult(success=True)

    monkeypatch.setattr(provider, "_execute_locked", slow)
    first = asyncio.create_task(provider.execute({"report_id": "rep-3"}, _ctx()))
    await asyncio.wait_for(started.wait(), timeout=5)

    second = await provider.execute({"report_id": "rep-3"}, _ctx())
    assert second.success is True
    assert "already_running" in (second.stdout or "")
    assert ran == 1, "the second fire ran the report a second time"

    finish.set()
    assert (await asyncio.wait_for(first, timeout=5)).success is True
    assert claims.is_running(rr.report_claim_id("rep-3")) is False


@pytest.mark.asyncio
async def test_the_manual_route_refuses_while_the_runner_holds_it(home):
    """The two halves meet: a claim written the way the RUNNER writes it blocks the route."""
    from personalclaw.triggers.scheduling import Claim

    claim_id = rr.report_claim_id("rep-4")
    claims.write_claim(Claim(trigger_id=claim_id, holder="knowledge-report", claimed_at=1.0e9))
    try:
        assert claims.is_running(handlers.report_claim_id("rep-4"), now=1.0e9 + 1) is True
    finally:
        claims.release_claim(claim_id)
    assert claims.is_running(claim_id) is False
