"""The routing-proposal review surface — list / accept / reject (MRT-5 §6.3).

Before this, ``accept``/``reject``/``pending`` were **library functions reachable from
``tests/test_routing_proposals.py`` alone**: ``model_telemetry.py`` registered only the policy GET
and PUT, and no handler in ``dashboard/`` imported ``routing.proposals`` at all. A queue a user
cannot see or decide is not a propose-don't-write mechanism — it is a mechanism with no surface.

The shape mirrors the tree's other propose-only queue (``/api/learning/proposals``): GET the list,
POST ``{id}/accept``, DELETE ``{id}`` to dismiss. What these rails assert is the *outcome on disk*
— the table, the queue, the suppression — never merely the response body, because a handler that
answered 200 while calling nothing would pass a body-only assertion.

Isolated home via ``PERSONALCLAW_HOME`` (read per call, cached nowhere), with the redirect asserted.
Nothing here may touch the real ``~/.personalclaw``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.model_telemetry import register_model_telemetry_routes
from personalclaw.routing import policy, proposals

UC = "chat"
QC = "general"
CURRENT = ["cloudy:big", "ollama:small"]
PROPOSED = ["ollama:small", "cloudy:big"]

EVIDENCE = {
    "n": {"cloudy:big": 20, "ollama:small": 20},
    "scores": {"cloudy:big": 0.4, "ollama:small": 0.95},
    "min_samples": 5,
    "hysteresis": 0.05,
    "p50_delta_ms": -780.0,
    "cost_delta_usd": -0.004,
    "sample_audit_ids": ["aud-l", "aud-c"],
}


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home the HANDLERS resolve for themselves.

    The handlers take no ``home``: they reach ``config_dir()`` through ``proposals._default_home``,
    which is exactly the binding a real request uses — so the redirect is the env var, and it is
    asserted rather than assumed.
    """
    from personalclaw.config.loader import config_dir

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert Path(config_dir()).resolve() == tmp_path.resolve(), "PERSONALCLAW_HOME did not bind"
    assert (
        Path(str(proposals._default_home())).resolve() == tmp_path.resolve()
    ), "the queue's own home accessor did not follow the redirect"
    policy.save_policy(tmp_path, policy._empty_policy())
    return tmp_path


async def _client() -> TestClient:
    app = web.Application()
    register_model_telemetry_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _queue(home: Path, **over):
    kwargs = {
        "use_case": UC,
        "query_class": QC,
        "current": CURRENT,
        "proposed": PROPOSED,
        "evidence": dict(EVIDENCE),
        "home": home,
    }
    kwargs.update(over)
    prop = proposals.propose(**kwargs)
    assert prop is not None, "the fixture failed to enqueue anything to decide"
    return prop


def _policy_bytes(home: Path) -> bytes:
    return (home / "routing_policy.json").read_bytes()


class TestList:
    @pytest.mark.asyncio
    async def test_the_queue_is_listed_with_its_evidence_and_a_badge_count(self, home: Path):
        """§6.3's badge, and the reason evidence rides along: a proposal a user cannot inspect is
        not reviewable, and the queue is capped at 50 so there is no second round-trip to save."""
        prop = _queue(home)
        c = await _client()
        try:
            got = await (await c.get("/api/models/routing-proposals")).json()
        finally:
            await c.close()
        assert got["count"] == 1
        row = got["proposals"][0]
        assert row["id"] == prop.id
        assert (row["current"], row["proposed"]) == (CURRENT, PROPOSED)
        assert row["evidence"]["n"] == EVIDENCE["n"]
        assert row["evidence"]["sample_audit_ids"] == EVIDENCE["sample_audit_ids"]

    @pytest.mark.asyncio
    async def test_an_empty_queue_is_an_empty_list_not_a_404(self, home: Path):
        c = await _client()
        try:
            got = await (await c.get("/api/models/routing-proposals")).json()
        finally:
            await c.close()
        assert got == {"count": 0, "proposals": []}

    @pytest.mark.asyncio
    async def test_an_unreadable_queue_fails_open_rather_than_500ing_the_tab(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """An unreadable proposal store means "nothing to review", not a blanked Routing tab."""

        def boom(**kw):
            raise OSError("queue unreadable")

        monkeypatch.setattr(proposals, "pending", boom)
        c = await _client()
        try:
            resp = await c.get("/api/models/routing-proposals")
            assert resp.status == 200
            assert await resp.json() == {"count": 0, "proposals": []}
        finally:
            await c.close()


class TestAccept:
    @pytest.mark.asyncio
    async def test_accepting_writes_the_table_with_the_proposal_as_its_basis(self, home: Path):
        """The outcome is read off DISK: a handler that answered 200 and called nothing would pass
        a body-only assertion."""
        prop = _queue(home)
        c = await _client()
        try:
            resp = await c.post(f"/api/models/routing-proposals/{prop.id}/accept")
            body = await resp.json()
        finally:
            await c.close()
        assert resp.status == 200 and body["applied"] is True
        assert policy.table_order(UC, QC, home=home) == PROPOSED
        assert policy.order_basis(UC, QC, home=home)["proposal_id"] == prop.id
        assert proposals.pending(home=home) == []

    @pytest.mark.asyncio
    async def test_a_hand_set_order_answers_200_with_the_refusal_reason(self, home: Path):
        """A refusal is a correct answer to a legitimate request, not a client error — and the
        surface has to be able to say WHY rather than appearing to do nothing."""
        policy.set_order(UC, QC, CURRENT, home=home, basis={"source": "user"})
        prop = _queue(home)
        before = _policy_bytes(home)
        c = await _client()
        try:
            resp = await c.post(f"/api/models/routing-proposals/{prop.id}/accept")
            body = await resp.json()
        finally:
            await c.close()
        assert resp.status == 200
        assert body["applied"] is False and "hand-set" in body["reason"]
        assert _policy_bytes(home) == before, "a refusal must write no table"

    @pytest.mark.asyncio
    async def test_the_byte_comparison_above_can_see_an_accepted_write(self, home: Path):
        """Vacuity floor for the refusal test: the same bytes DO move when the cell is not
        hand-set, so ``==`` there is a measurement rather than a tautology."""
        prop = _queue(home)
        before = _policy_bytes(home)
        c = await _client()
        try:
            await c.post(f"/api/models/routing-proposals/{prop.id}/accept")
        finally:
            await c.close()
        assert _policy_bytes(home) != before

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_a_404_in_the_shared_envelope(self, home: Path):
        c = await _client()
        try:
            resp = await c.post("/api/models/routing-proposals/rp-nope/accept")
            body = await resp.json()
        finally:
            await c.close()
        assert resp.status == 404
        assert body["error"]["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_an_already_decided_id_is_a_404_not_a_second_apply(self, home: Path):
        """Accepting twice must not re-write the table off a spent decision."""
        prop = _queue(home)
        assert proposals.accept(prop.id, home=home) is True
        after_first = _policy_bytes(home)
        c = await _client()
        try:
            resp = await c.post(f"/api/models/routing-proposals/{prop.id}/accept")
        finally:
            await c.close()
        assert resp.status == 404
        assert _policy_bytes(home) == after_first


class TestReject:
    @pytest.mark.asyncio
    async def test_rejecting_writes_no_table_and_suppresses_the_finding(self, home: Path):
        """Reject means the table was right: no write, and ``propose`` refuses the same finding
        for ``reproposal_cooldown_days`` — asserted by re-proposing it."""
        prop = _queue(home)
        before = _policy_bytes(home)
        c = await _client()
        try:
            resp = await c.delete(f"/api/models/routing-proposals/{prop.id}")
            body = await resp.json()
        finally:
            await c.close()
        assert resp.status == 200 and body["ok"] is True
        assert _policy_bytes(home) == before
        assert proposals.pending(home=home) == []
        assert (
            proposals.propose(
                use_case=UC,
                query_class=QC,
                current=CURRENT,
                proposed=PROPOSED,
                evidence=dict(EVIDENCE),
                home=home,
            )
            is None
        ), "the rejection recorded no cooldown"

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_a_404(self, home: Path):
        c = await _client()
        try:
            resp = await c.delete("/api/models/routing-proposals/rp-nope")
        finally:
            await c.close()
        assert resp.status == 404
