"""BA-9 — the per-task browser grant flow, close-to-kill, and its SEL audit.

Covers the acceptance the atom names: a ``user_browser`` task cannot start without a fresh grant
through the FAIL-CLOSED ``ApprovalGate`` (300s -> REJECT), the grant/revoke emit ``browser_grant`` /
``browser_revoked``, closing the tab ends a run within one step, and any gate failure is a REJECT
(never an open door).
"""

from __future__ import annotations

import asyncio

from personalclaw.agents.native.approval import ApprovalGate
from personalclaw.browse import grant
from personalclaw.browse.loop import PARK_TAB_CLOSED, run_browse_loop


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
