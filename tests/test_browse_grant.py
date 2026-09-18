"""BA-9 — the per-task browser grant flow, close-to-kill, its SEL audit, and the human's answer.

Covers the acceptance the atom names: a ``user_browser`` task cannot start without a fresh grant
through the FAIL-CLOSED ``ApprovalGate`` (300s -> REJECT), the grant/revoke emit ``browser_grant`` /
``browser_revoked``, closing the tab ends a run within one step, and any gate failure is a REJECT
(never an open door).

Plus the half that was missing, and which these tests exist to keep reachable:

* **The request is audited when it is REQUESTED.** The only row used to be written after the gate
  resolved, so an authorization request against the operator's own logged-in browser that was
  cancelled left no trace at all. ``TestTheRequestIsAuditedWhenItIsRaised`` proves the row survives
  a cancellation — the case with no second row to fall back on.
* **A human can actually answer it.** ``approve_grant`` / ``reject_grant`` / ``pending_grants`` had
  ZERO non-test callers, so every ``user_browser`` task could only ever end in the 300s refusal.
  ``TestAnsweringAPendingGrant`` drives the PROCESS-GLOBAL gate (``gate`` omitted, the way the
  provider calls it) rather than an injected one, because an injected gate would prove the helpers
  work while leaving the shipped channel unreachable — which is exactly the state that shipped.
  ``TestTheGrantRoutes`` drives the same thing through the HTTP handlers the panel posts to.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.agents.native.approval import ApprovalGate
from personalclaw.browse import grant
from personalclaw.browse.loop import PARK_TAB_CLOSED, run_browse_loop
from personalclaw.dashboard.handlers import browse_mirror as bm


def _capture_sel(monkeypatch) -> list[dict]:
    """Capture every SEL row the grant module writes, without touching disk."""
    rows: list[dict] = []

    def _fake(self, **kw):  # noqa: ANN001 — bound method signature
        rows.append(kw)

    monkeypatch.setattr("personalclaw.sel.SecurityEventLog.log_api_access", _fake)
    return rows


# ── fail-closed grant ─────────────────────────────────────────────────────────


def test_gate_timeout_rejects_with_none_sentinel(monkeypatch) -> None:
    """No human answer within the window -> REJECT, and ``granted_at`` is None (never a 0.0
    sentinel that a ``> 0`` test would misread as 'granted at the epoch')."""
    rows = _capture_sel(monkeypatch)

    async def _run() -> grant.BrowserGrant:
        gate = ApprovalGate()  # nothing ever resolves it
        return await grant.request_grant(
            task="Read my dashboard",
            scope=("example.com",),
            gate=gate,
            request_id="t",
            timeout=0.05,
        )

    g = asyncio.run(_run())
    assert g.granted is False
    assert g.granted_at is None  # the None-sentinel discipline
    assert any(r["operation"] == "browser_grant" and r["outcome"] == "rejected" for r in rows)


def test_missing_channel_rejects(monkeypatch) -> None:
    """An explicit ``gate=None`` means no approval channel -> REJECT, fail-closed."""
    rows = _capture_sel(monkeypatch)
    g = asyncio.run(
        grant.request_grant(task="t", scope=(), gate=None, request_id="n", timeout=0.05)
    )
    assert g.granted is False
    assert g.granted_at is None
    assert any(r["operation"] == "browser_grant" and r["outcome"] == "rejected" for r in rows)


def test_gate_exception_is_fail_closed(monkeypatch) -> None:
    """A gate that RAISES rejects — the loop never falls open on a bookkeeping error."""
    rows = _capture_sel(monkeypatch)

    class _BoomGate(ApprovalGate):
        async def request(self, request_id, *, timeout=300.0):  # noqa: ANN001
            raise RuntimeError("gate exploded")

    g = asyncio.run(grant.request_grant(task="t", scope=(), gate=_BoomGate(), request_id="b"))
    assert g.granted is False
    assert any(r["operation"] == "browser_grant" and r["outcome"] == "rejected" for r in rows)


def test_grant_emits_browser_grant(monkeypatch) -> None:
    """An approved grant returns granted + a monotonic ``granted_at`` and emits browser_grant."""
    rows = _capture_sel(monkeypatch)

    async def _run() -> grant.BrowserGrant:
        gate = ApprovalGate()
        task = asyncio.create_task(
            grant.request_grant(
                task="Check prices", scope=("shop.example",), gate=gate, request_id="ok1", timeout=5
            )
        )
        while not gate.approve("ok1"):  # False until request() has registered the future
            await asyncio.sleep(0.005)
        return await task

    g = asyncio.run(_run())
    assert g.granted is True
    assert g.granted_at is not None
    assert g.group_name == "Check prices"
    grants = [r for r in rows if r["operation"] == "browser_grant" and r["outcome"] == "granted"]
    assert grants, "expected a granted browser_grant row"
    # The audit row names the task + scope, never a secret.
    assert "shop.example" in grants[0]["resources"]


def test_revoke_emits_browser_revoked(monkeypatch) -> None:
    """Revoking a granted task emits browser_revoked; a never-granted grant revokes to nothing."""
    rows = _capture_sel(monkeypatch)
    granted = grant.BrowserGrant(
        task="t",
        scope=("example.com",),
        group_name="t",
        request_id="r",
        granted=True,
        granted_at=1.0,
    )
    grant.revoke_grant(granted, reason="tab_closed")
    revokes = [r for r in rows if r["operation"] == "browser_revoked"]
    assert len(revokes) == 1
    assert revokes[0]["outcome"] == "ok"
    assert "tab_closed" in revokes[0]["resources"]

    rows.clear()
    ungranted = grant.BrowserGrant(
        task="t", scope=(), group_name="t", request_id="r2", granted=False
    )
    grant.revoke_grant(ungranted, reason="run_ended")
    assert not rows, "a grant that was never granted must not emit a revoked row"


# ── close-to-kill ───────────────────────────────────────────────────────────────


def test_close_check_observes_disconnect() -> None:
    """make_close_check closes when the bound connector is gone / re-attached; fails toward stop."""
    g = grant.BrowserGrant(
        task="t",
        scope=(),
        group_name="t",
        request_id="r",
        granted=True,
        granted_at=1.0,
        bound_device_id="dev1",
        bound_cdp_url="ws://127.0.0.1:9222/devtools/page/A",
    )

    class _St:
        def __init__(
            self, connected, device_id="dev1", cdp_url="ws://127.0.0.1:9222/devtools/page/A"
        ):
            self.connected = connected
            self.device_id = device_id
            self.cdp_url = cdp_url

    assert grant.make_close_check(g, status_reader=lambda: _St(True))() == (False, "")
    assert grant.make_close_check(g, status_reader=lambda: _St(False))()[0] is True
    assert (
        grant.make_close_check(
            g, status_reader=lambda: _St(True, cdp_url="ws://127.0.0.1:9222/devtools/page/B")
        )()[0]
        is True
    )

    def _boom():
        raise RuntimeError("cannot read connector")

    assert grant.make_close_check(g, status_reader=_boom)()[0] is True  # fail toward STOP


def test_close_to_kill_ends_run_in_one_step() -> None:
    """A close_check reporting closed parks the loop as PARK_TAB_CLOSED before any model call."""

    class _Nav:
        ok = True
        reason = ""
        error = ""

    class _FakeSession:
        async def start(self):
            return None

        async def navigate(self, url):
            return _Nav()

    async def _decide(_prompt):  # must never be reached
        raise AssertionError("close-to-kill must stop the run before the model is called")

    result = asyncio.run(
        run_browse_loop(
            goal="do a thing",
            start_url="https://example.com",
            session=_FakeSession(),
            page=object(),
            decide=_decide,
            max_steps=20,
            close_check=lambda: (True, "the task tab group was closed"),
        )
    )
    assert result.parked is True
    assert result.park_reason == PARK_TAB_CLOSED


# ══════════════════════════════════════════════════════════════════════════════
# The revoke closes the trail on EVERY exit
# ══════════════════════════════════════════════════════════════════════════════


class TestAnApprovedGrantIsAlwaysRevoked:
    """Measured on a live gateway: an approved grant whose CDP open FAILED left a
    ``browser_grant granted`` row with no ``browser_revoked`` beside it, because the revoke sat in
    the browse loop's own ``finally`` and three return paths never reached it (the login park and
    both ``_open`` failures). The audit trail said the operator's browser was authorized and never
    said the authorization ended."""

    @pytest.fixture
    def _attached_and_enabled(self, monkeypatch):
        from personalclaw.browse import target as bt
        from personalclaw.config.loader import AppConfig, BrowseConfig

        cfg = AppConfig(browse=BrowseConfig(user_browser_enabled=True))
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        bt.clear_connector()
        bt.register_connector(device_id="dev-ba9", cdp_url="ws://127.0.0.1:9222/devtools/page/X")
        yield
        bt.clear_connector()

    @pytest.fixture
    def _instant_yes(self, monkeypatch):
        from personalclaw.agents.native.approval import APPROVE, ApprovalGate

        class _Yes(ApprovalGate):
            async def request(self, request_id: str, *, timeout: float = 300.0) -> str:
                return APPROVE

        monkeypatch.setattr(grant, "_gate", _Yes())

    def test_a_failed_cdp_open_still_records_browser_revoked(
        self, monkeypatch, tmp_path, _attached_and_enabled, _instant_yes
    ) -> None:
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
        rows = _capture_sel(monkeypatch)

        import personalclaw.action_providers.browse_provider as bp
        from personalclaw.action_providers.base import ActionContext

        async def _boom(_self, _cfg, _ctx, *, cdp_url: str):
            raise RuntimeError("server rejected WebSocket connection: HTTP 403")

        monkeypatch.setattr(bp.BrowseActionProvider, "_open", _boom)

        result = asyncio.run(
            bp.BrowseActionProvider().execute(
                {
                    "goal": "Download my October invoices",
                    "start_url": "https://billing.test/invoices",
                    "target": "user_browser",
                },
                ActionContext(event="manual"),
            )
        )
        assert result.success is False
        assert result.agent_error is not None
        assert result.agent_error.code == "ERR_BROWSE_CONNECT_FAILED"

        granted = [
            r for r in rows if r["operation"] == "browser_grant" and r["outcome"] == "granted"
        ]
        revoked = [r for r in rows if r["operation"] == "browser_revoked"]
        assert granted, "the grant was not recorded as granted, so this leg proves nothing"
        assert revoked, "a granted grant whose open failed left the authorization open in the audit"
        assert "run_ended" in revoked[0]["resources"]


# ══════════════════════════════════════════════════════════════════════════════
# The request-time audit row
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _no_pending_leak():
    """The pending-grant store is a process global, so a test that abandons a wait would leak its
    row into the next test's ``pending_grants()`` read. Cleared at both ends."""
    grant._pending.clear()
    yield
    grant._pending.clear()


@pytest.fixture(autouse=True)
def _mute_the_ui_signal(monkeypatch):
    """The grant signal resolves the live dashboard state, which no unit test stands up. Muted so a
    test is measuring the grant flow and not the relay's best-effort no-op path (the relay itself is
    covered in ``test_browse_mirror.py``)."""
    monkeypatch.setattr(grant, "_signal_grants", lambda: None)


async def _wait_for_pending(request_id: str, *, tries: int = 400) -> dict:
    """The pending row for ``request_id``, once ``request_grant`` has registered it."""
    for _ in range(tries):
        for meta in grant.pending_grants():
            if meta["request_id"] == request_id:
                return meta
        await asyncio.sleep(0.005)
    raise AssertionError(f"{request_id} never appeared in pending_grants()")


class TestTheRequestIsAuditedWhenItIsRaised:
    """``browser_grant``/``needs_confirm`` at REQUEST time, not only at resolution."""

    def test_the_row_exists_before_anyone_answers(self, monkeypatch) -> None:
        rows = _capture_sel(monkeypatch)

        async def _run() -> None:
            gate = ApprovalGate()
            task = asyncio.create_task(
                grant.request_grant(
                    task="Reconcile my invoices",
                    scope=("billing.test",),
                    gate=gate,
                    request_id="req1",
                    timeout=30,
                )
            )
            await _wait_for_pending("req1")
            # Mid-flight: nobody has answered, so there is no decision yet — and the request must
            # already be on the record.
            asked = [
                r
                for r in rows
                if r["operation"] == "browser_grant" and r["outcome"] == "needs_confirm"
            ]
            assert asked, "the grant REQUEST left no audit row"
            assert "billing.test" in asked[0]["resources"]
            assert "Reconcile my invoices" in asked[0]["resources"]
            assert not [
                r for r in rows if r["outcome"] in ("granted", "rejected")
            ], "a decision row appeared before anyone decided"
            gate.reject("req1")
            await task

        asyncio.run(_run())

    def test_a_cancelled_request_is_still_auditable(self, monkeypatch) -> None:
        """THE finding this row exists for. A cancellation unwinds through ``finally`` without ever
        reaching the decision audit, so before the request row a grant request against the
        operator's own browser could be raised and abandoned with NO trace whatsoever."""
        rows = _capture_sel(monkeypatch)

        async def _run() -> None:
            gate = ApprovalGate()
            task = asyncio.create_task(
                grant.request_grant(
                    task="Silently abandoned",
                    scope=("ghost.test",),
                    gate=gate,
                    request_id="cancelled",
                    timeout=30,
                )
            )
            await _wait_for_pending("cancelled")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_run())

        # No decision row — the cancellation never reached one. That is the point.
        assert not [r for r in rows if r["outcome"] in ("granted", "rejected")]
        asked = [
            r for r in rows if r["operation"] == "browser_grant" and r["outcome"] == "needs_confirm"
        ]
        assert asked, "a cancelled grant request left NO audit trace"
        assert "ghost.test" in asked[0]["resources"]

    def test_the_request_outcome_word_is_one_the_audit_surface_classifies(self) -> None:
        """``needs_confirm`` must be a DECLARED family word, or the row lands in the unclassified
        remainder and no filter pill on the audit surface can select it — the silent-zero defect
        ``AUDIT_OUTCOME_FAMILIES`` exists to end. A new word here would also move
        ``test_audit_outcome_families``'s ceiling."""
        from personalclaw.sel import AUDIT_OUTCOME_FAMILIES, audit_outcome_tone

        family = next(f for f in AUDIT_OUTCOME_FAMILIES if f["key"] == "needs_confirm")
        assert grant._OUTCOME_REQUESTED in family["values"]
        assert audit_outcome_tone(grant._OUTCOME_REQUESTED) == "warning"

    def test_the_pending_row_carries_what_a_human_needs_to_decide(self, monkeypatch) -> None:
        """The scope and the deadline, so a surface can name the sites and say when silence refuses.
        No credential-shaped field exists on the row at all."""
        _capture_sel(monkeypatch)

        async def _run() -> None:
            gate = ApprovalGate()
            task = asyncio.create_task(
                grant.request_grant(
                    task="Check my order",
                    scope=("shop.test", "cdn.shop.test"),
                    gate=gate,
                    request_id="meta1",
                    timeout=300,
                )
            )
            meta = await _wait_for_pending("meta1")
            assert meta["task"] == "Check my order"
            assert meta["scope"] == ["shop.test", "cdn.shop.test"]
            assert meta["timeout"] == 300.0
            assert meta["requested_at"] > 0
            assert set(meta) == {
                "request_id",
                "task",
                "scope",
                "group",
                "requested_at",
                "timeout",
            }, "the pending row grew a field — check it carries no credential"
            gate.reject("meta1")
            await task

        asyncio.run(_run())

    def test_the_pending_row_is_gone_once_it_resolves(self, monkeypatch) -> None:
        """An answered grant must leave the read model, or the panel keeps offering a card whose
        buttons 404."""
        _capture_sel(monkeypatch)

        async def _run() -> None:
            gate = ApprovalGate()
            task = asyncio.create_task(
                grant.request_grant(task="t", gate=gate, request_id="gone", timeout=30)
            )
            await _wait_for_pending("gone")
            gate.approve("gone")
            await task

        asyncio.run(_run())
        assert grant.pending_grants() == []


# ══════════════════════════════════════════════════════════════════════════════
# Answering a pending grant — the human half
# ══════════════════════════════════════════════════════════════════════════════


class TestAnsweringAPendingGrant:
    """Against the PROCESS-GLOBAL gate the provider actually uses (``gate`` omitted). An injected
    gate would pass while the shipped channel stayed unreachable — the state that shipped."""

    def test_approve_grant_lets_the_run_proceed(self, monkeypatch) -> None:
        _capture_sel(monkeypatch)

        async def _run() -> grant.BrowserGrant:
            task = asyncio.create_task(
                grant.request_grant(
                    task="Pay my bill", scope=("billing.test",), request_id="a1", timeout=30
                )
            )
            await _wait_for_pending("a1")
            assert grant.approve_grant("a1") is True
            return await task

        g = asyncio.run(_run())
        assert g.granted is True
        assert g.granted_at is not None
        assert g.reason == ""

    def test_reject_grant_refuses_without_waiting_out_the_ceiling(self, monkeypatch) -> None:
        """A human's Deny must refuse NOW. The timeout is 30s here, so a test that finishes proves
        the answer resolved the gate rather than the clock running out."""
        _capture_sel(monkeypatch)

        async def _run() -> grant.BrowserGrant:
            task = asyncio.create_task(
                grant.request_grant(task="Nope", request_id="r1", timeout=30)
            )
            await _wait_for_pending("r1")
            assert grant.reject_grant("r1") is True
            return await task

        g = asyncio.run(_run())
        assert g.granted is False
        assert g.granted_at is None
        assert g.bound_device_id == "" and g.bound_cdp_url == ""

    def test_a_human_deny_does_not_claim_a_timeout_that_never_happened(self, monkeypatch) -> None:
        """``ApprovalGate`` reports a deny and a timeout as the same ``REJECT``. Until
        ``reject_grant`` had a caller every refusal really WAS the clock, so one sentence covered
        both; now that a person can decline in a second, reusing it would make the audit row and
        the typed refusal assert a 300s wait that did not occur."""
        rows = _capture_sel(monkeypatch)

        async def _run() -> grant.BrowserGrant:
            task = asyncio.create_task(
                grant.request_grant(task="No thanks", request_id="why1", timeout=300)
            )
            await _wait_for_pending("why1")
            grant.reject_grant("why1")
            return await task

        g = asyncio.run(_run())
        assert g.granted is False
        assert g.reason == "a person declined this task"
        assert "300s" not in g.reason
        rejected = [r for r in rows if r["outcome"] == "rejected"]
        assert rejected and "a person declined this task" in rejected[0]["resources"]
        # And it reaches the user through the typed error the provider returns.
        assert "a person declined this task" in grant.grant_denied_error(g).why

    def test_an_unanswered_grant_still_says_the_clock_refused_it(self, monkeypatch) -> None:
        """CONTROL for the leg above — the timeout sentence must not have been replaced, only
        narrowed to the case it is true for."""
        rows = _capture_sel(monkeypatch)
        g = asyncio.run(
            grant.request_grant(task="Ignored", gate=ApprovalGate(), request_id="w2", timeout=0.05)
        )
        assert g.granted is False
        assert g.reason == "the grant was not approved within 0s"
        assert any("not approved within" in r.get("resources", "") for r in rows)

    def test_the_answered_marker_never_reaches_the_wire(self, monkeypatch) -> None:
        """It is bookkeeping on the shared pending row, and ``pending_grants`` feeds an HTTP
        response — so the projection, not the row, is what a surface sees."""
        _capture_sel(monkeypatch)

        async def _run() -> list[dict]:
            task = asyncio.create_task(
                grant.request_grant(task="t", request_id="proj1", timeout=30)
            )
            await _wait_for_pending("proj1")
            grant._pending["proj1"][grant._ANSWERED] = "reject"
            published = grant.pending_grants()
            grant.reject_grant("proj1")
            await task
            return published

        published = asyncio.run(_run())
        assert grant._ANSWERED not in published[0]
        assert set(published[0]) == set(grant._PUBLIC_FIELDS)

    def test_answering_an_id_nobody_is_waiting_on_is_false(self) -> None:
        """What the route turns into a 404 — an already-answered or expired grant, not an error."""
        assert grant.approve_grant("never-existed") is False
        assert grant.reject_grant("never-existed") is False

    def test_two_grants_are_answered_independently(self, monkeypatch) -> None:
        """One gate serves many request ids, so answering one must not resolve the other."""
        _capture_sel(monkeypatch)

        async def _run() -> tuple[grant.BrowserGrant, grant.BrowserGrant]:
            first = asyncio.create_task(
                grant.request_grant(task="one", request_id="m1", timeout=30)
            )
            second = asyncio.create_task(
                grant.request_grant(task="two", request_id="m2", timeout=30)
            )
            await _wait_for_pending("m1")
            await _wait_for_pending("m2")
            assert grant.approve_grant("m1") is True
            assert grant.reject_grant("m2") is True
            return await first, await second

        one, two = asyncio.run(_run())
        assert one.granted is True
        assert two.granted is False


# ══════════════════════════════════════════════════════════════════════════════
# The HTTP surface the panel posts to
# ══════════════════════════════════════════════════════════════════════════════


class _FakeReq:
    """Enough of an aiohttp request for these handlers: a ``match_info`` and an empty app."""

    def __init__(self, **match_info) -> None:
        self.match_info = dict(match_info)
        self.app: dict = {}

    async def json(self):
        raise ValueError("no body")


def _body_of(resp) -> dict:
    return json.loads(resp.body)


class TestTheGrantRoutes:
    def test_the_status_read_model_carries_the_pending_grants(self, monkeypatch, tmp_path) -> None:
        """``GET /api/browse/status`` is where the panel learns a grant is waiting. Before this the
        grant store had no HTTP reader at all, so a pending grant was invisible to every surface."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
        _capture_sel(monkeypatch)

        async def _run() -> dict:
            task = asyncio.create_task(
                grant.request_grant(
                    task="Read my statements", scope=("bank.test",), request_id="s1", timeout=30
                )
            )
            await _wait_for_pending("s1")
            body = _body_of(await bm.api_browse_status(_FakeReq()))
            grant.reject_grant("s1")
            await task
            return body

        body = asyncio.run(_run())
        assert [g["request_id"] for g in body["grants"]] == ["s1"]
        assert body["grants"][0]["scope"] == ["bank.test"]
        assert body["grants"][0]["task"] == "Read my statements"

    def test_the_approve_route_resolves_the_real_gate(self, monkeypatch) -> None:
        _capture_sel(monkeypatch)

        async def _run() -> grant.BrowserGrant:
            task = asyncio.create_task(
                grant.request_grant(task="Allow me", request_id="h1", timeout=30)
            )
            await _wait_for_pending("h1")
            resp = await bm.api_browse_grant_resolve(_FakeReq(request_id="h1", action="approve"))
            assert resp.status == 200
            assert _body_of(resp) == {"ok": True, "request_id": "h1", "action": "approve"}
            return await task

        assert asyncio.run(_run()).granted is True

    def test_the_reject_route_resolves_the_real_gate(self, monkeypatch) -> None:
        _capture_sel(monkeypatch)

        async def _run() -> grant.BrowserGrant:
            task = asyncio.create_task(
                grant.request_grant(task="Deny me", request_id="h2", timeout=30)
            )
            await _wait_for_pending("h2")
            resp = await bm.api_browse_grant_resolve(_FakeReq(request_id="h2", action="reject"))
            assert resp.status == 200
            return await task

        assert asyncio.run(_run()).granted is False

    def test_an_unanswerable_id_is_a_404(self) -> None:
        resp = asyncio.run(
            bm.api_browse_grant_resolve(_FakeReq(request_id="stale", action="approve"))
        )
        assert resp.status == 404

    def test_an_unknown_verb_resolves_nothing(self, monkeypatch) -> None:
        """A verb outside the closed set must be a 400 that leaves the grant PENDING — not something
        that quietly picks a direction on a security gate."""
        _capture_sel(monkeypatch)

        async def _run() -> None:
            task = asyncio.create_task(
                grant.request_grant(task="Untouched", request_id="h3", timeout=30)
            )
            await _wait_for_pending("h3")
            resp = await bm.api_browse_grant_resolve(_FakeReq(request_id="h3", action="maybe"))
            assert resp.status == 400
            assert [m["request_id"] for m in grant.pending_grants()] == ["h3"]
            grant.reject_grant("h3")
            await task

        asyncio.run(_run())

    def test_the_route_is_registered_so_a_request_can_reach_it(self) -> None:
        """The handlers above are called directly, which proves they work and proves nothing about
        whether a POST can reach them — the shape that let the grant helpers ship unreachable."""
        from aiohttp import web

        app = web.Application()
        bm.register_browse_mirror_routes(app)
        registered = {
            (r.method, str(getattr(r.resource, "canonical", ""))) for r in app.router.routes()
        }
        assert ("POST", "/api/browse/grants/{request_id}/{action}") in registered
        assert ("GET", "/api/browse/status") in registered
