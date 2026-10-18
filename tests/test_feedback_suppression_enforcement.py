"""``suppressed`` is a claim about EFFECT, and only a kind with a surfacing gate has one.

Feedback-Signal computes a withholding set over every kind in :data:`feedback.PRODUCER_KINDS`, but
a membership result can only *do* something where a surfacing path consults it. Exactly one does:
``skills.surfacing.surface_skills`` withholds a matched skill whose identity is
``("skill_synthesis", <key>)``. The other five kinds — ``prompt``, ``loop_judge``,
``workflow_surfacing``, ``routing_pair``, ``app`` — keep surfacing and get the retire PROPOSAL only.

That distinction was invisible at the API boundary. ``GET /api/feedback/producers`` set
``suppressed: true`` for any below-threshold producer of ANY kind, and ``FeedbackPanel`` renders
that as a red pill titled *"Stopped surfacing"*. For five of six kinds it was untrue — and the
panel's own test fixture used a ``prompt``-kind row with ``suppressed: true`` as its worked example,
so the impossible shape was written into the tests too.

These tests pin the two halves that can drift apart:

1. :data:`feedback.ENFORCED_SUPPRESSION_KINDS` must match which kinds the CODE actually gates. If
   someone wires a second surfacing gate, or removes the one there is, without updating the
   constant, the API resumes lying in one direction or the other.
2. The route must not report an effect for an unenforced kind.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from feedback_suppression_enforcement_shared import SRC

from personalclaw import feedback as fb
from personalclaw.dashboard.handlers.feedback import api_feedback_producers


class TestTheConstantMatchesTheCode:
    def test_every_enforced_kind_is_a_real_producer_kind(self):
        """A typo here would silently enforce nothing: the handler's membership test would never
        match, so every below-threshold producer would fall through to ``proposal_only`` and the one
        kind that IS withheld would stop being reported as withheld."""
        assert fb.ENFORCED_SUPPRESSION_KINDS, "the enforced set is empty — nothing claims an effect"
        unknown = [k for k in fb.ENFORCED_SUPPRESSION_KINDS if k not in fb.PRODUCER_KINDS]
        assert unknown == [], (
            f"ENFORCED_SUPPRESSION_KINDS names {unknown}, which is not in PRODUCER_KINDS "
            f"{list(fb.PRODUCER_KINDS)}. A kind that cannot be recorded cannot be suppressed."
        )

    def test_the_skills_gate_still_keys_on_the_enforced_kind(self):
        """``skills/surfacing.py`` withholds on the literal ``("skill_synthesis", key)``. If that
        literal changes, the constant is stale even though every other assertion still passes."""
        text = (SRC / "skills" / "surfacing.py").read_text()
        for kind in fb.ENFORCED_SUPPRESSION_KINDS:
            assert kind in text, (
                f"ENFORCED_SUPPRESSION_KINDS claims {kind!r} is withheld, but "
                f"skills/surfacing.py never mentions it. Either the gate keys on a different "
                f"identity now or the constant is stale."
            )


def _app() -> web.Application:
    """The REAL route, so a handler change is what these tests measure."""
    app = web.Application()
    app.router.add_get("/api/feedback/producers", api_feedback_producers)
    return app


async def _rows(stats, suppressed, monkeypatch):
    monkeypatch.setattr(fb, "producer_stats", lambda **kw: stats)
    monkeypatch.setattr(fb, "suppressed_producers", lambda **kw: suppressed)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/feedback/producers")
        assert resp.status == 200, await resp.text()
        return (await resp.json())["producers"]


class TestTheRouteDoesNotClaimAnUnenforcedEffect:
    """Drives the REAL route over a stubbed stats/withholding pair.

    An earlier draft re-implemented the handler's branch locally and asserted on the copy. It passed
    with the handler reverted to the old one-flag-for-every-kind behaviour — a test of the shape
    rather than of the code, which is the same failure mode as the defect itself. These call
    ``api_feedback_producers`` through a test client, so reverting the handler reds them.
    """

    @pytest.mark.asyncio
    async def test_an_unenforced_kind_below_threshold_is_proposal_only(self, monkeypatch):
        pid = "task-inbox-classify"
        stats = {("prompt", pid): {"n": 6, "accuracy": 0.33, "ups": 2, "downs": 4}}
        (row,) = await _rows(stats, {("prompt", pid)}, monkeypatch)
        assert row.get("suppressed") is not True, (
            "a `prompt` producer was reported as suppressed. Nothing withholds prompts — the panel "
            "renders that as a pill titled 'Stopped surfacing', which is false."
        )
        assert row.get("proposal_only") is True, "the honest state (retire proposed) is missing"

    @pytest.mark.asyncio
    async def test_the_enforced_kind_below_threshold_is_reported_suppressed(self, monkeypatch):
        """The other direction, and the more dangerous one: under-reporting a real withholding
        leaves the user unable to explain why a skill stopped appearing."""
        stats = {("skill_synthesis", "s"): {"n": 6, "accuracy": 0.2, "ups": 1, "downs": 5}}
        (row,) = await _rows(stats, {("skill_synthesis", "s")}, monkeypatch)
        assert row.get("suppressed") is True
        assert "proposal_only" not in row, "a withheld producer must not ALSO read proposal-only"

    @pytest.mark.asyncio
    async def test_a_producer_above_threshold_claims_neither(self, monkeypatch):
        stats = {("prompt", "good"): {"n": 9, "accuracy": 0.9, "ups": 8, "downs": 1}}
        (row,) = await _rows(stats, set(), monkeypatch)
        assert row.get("suppressed") is False
        assert "proposal_only" not in row

    @pytest.mark.asyncio
    async def test_below_min_n_reads_collecting_and_claims_nothing(self, monkeypatch):
        stats = {("prompt", "new"): {"n": 1, "accuracy": 0.0, "ups": 0, "downs": 1}}
        (row,) = await _rows(stats, {("prompt", "new")}, monkeypatch)
        assert row.get("collecting") is True
        # A threshold verdict over n=1 is noise, and "collecting" is the word for that.
        assert "suppressed" not in row
        assert "proposal_only" not in row

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", list(fb.PRODUCER_KINDS))
    async def test_every_kind_reports_exactly_one_state(self, kind, monkeypatch):
        """No kind may claim both flags, or neither, when below the threshold — the panel renders
        each as its own pill and would show two contradictory ones."""
        stats = {(kind, "x"): {"n": 6, "accuracy": 0.1, "ups": 1, "downs": 5}}
        (row,) = await _rows(stats, {(kind, "x")}, monkeypatch)
        claimed = [k for k in ("suppressed", "proposal_only") if row.get(k) is True]
        assert len(claimed) == 1, f"{kind} claimed {claimed} — expected exactly one state"
