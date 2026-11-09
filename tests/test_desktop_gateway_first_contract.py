"""What the desktop app reads of a gateway it updates, pinned at the gateway's side.

The desktop app's Update the Gateway (``desktop/gatewayFirst.js``) plans from the gateway's update
read and drives its update. It reads these answers, field by field, so each is pinned here as the
gateway gives it: a rename on this side would leave the app's plan reading nothing.

- ``GET /api/update/check``: ``kind``, ``current``, ``latest``, ``update_available``, ``channel``,
  ``pin`` and ``check_enabled``; ``POST /api/update/check`` adds ``checked_now``.
- ``POST /api/update``: a gateway built into a desktop app answers ``instructions`` and runs
  nothing; one already updating answers 409 with its sentence as a string.
- ``GET /api/status``: ``version`` and ``update_progress`` (``{step, detail}``).
- Who may ask: ``POST /api/update`` and ``POST /api/update/check`` are the owner's alone, never an
  installed app's.

Hermetic: a mocked request and stand-ins for the release read, as ``test_update_apply_kind.py``
does. Nothing reaches the network or runs an install.
"""

from __future__ import annotations

import inspect
import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly
from personalclaw.dashboard import handlers_system
from personalclaw.dashboard.handlers import updates as upd

pytestmark = pytest.mark.security


class _StateStub:
    def __init__(self) -> None:
        self._background_tasks: set = set()
        self.progress: list = []

    def push_refresh(self, *kinds: str) -> None:
        pass

    def push_update_progress(self, step: str, detail: str = "") -> None:
        self.progress.append((step, detail))


def _req(method: str, path: str) -> object:
    req = make_mocked_request(method, path)
    req.app["state"] = _StateStub()
    return req


@pytest.fixture(autouse=True)
def _no_flight():
    upd._apply_in_flight = False
    yield
    upd._apply_in_flight = False


@pytest.fixture
def _release_read(monkeypatch):
    """The release read and the checks stood in for: a pip install, 0.3.8 on offer."""

    async def _status(_current, *, fetch):
        return {"kind": "pip", "current": "0.3.7", "latest": "0.3.8", "update_available": True}

    async def _checked(*, asked):
        return True

    monkeypatch.setattr("personalclaw.self_update.build_update_status", _status)
    monkeypatch.setattr(upd, "_do_update_check", _checked)
    monkeypatch.setattr(upd, "_claim_update_check", lambda *, asked, at_start=False: asked)


@pytest.mark.asyncio
async def test_the_update_read_names_what_the_apps_plan_reads(_release_read) -> None:
    read = json.loads((await upd.api_update_check(_req("GET", "/api/update/check"))).body)
    for key in ("kind", "current", "latest", "update_available", "channel", "pin", "check_enabled"):
        assert key in read, f"GET /api/update/check no longer says {key}"
    assert read["kind"] == "pip" and read["latest"] == "0.3.8" and read["update_available"] is True
    assert isinstance(read["pin"], str) and isinstance(read["channel"], str)
    assert "checked_now" not in read, "a read that ran no check says it checked"
    looked = json.loads((await upd.api_update_check_now(_req("POST", "/api/update/check"))).body)
    assert looked["checked_now"] is True
    assert looked["latest"] == "0.3.8"


@pytest.mark.asyncio
async def test_a_gateway_built_into_an_app_answers_instructions_and_runs_nothing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")

    async def _status(_current, *, fetch):
        return {"kind": "desktop", "apply_method": "desktop_delegate", "instructions": []}

    monkeypatch.setattr("personalclaw.self_update.build_update_status", _status)
    req = _req("POST", "/api/update")
    resp = await upd.api_update_apply(req)
    body = json.loads(resp.body)
    assert resp.status == 200
    assert (body["status"], body["kind"]) == ("instructions", "desktop")
    assert upd._apply_in_flight is False
    assert req.app["state"].progress == [], "an update step ran"


@pytest.mark.asyncio
async def test_an_update_already_running_is_a_409_with_its_sentence_as_a_string(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "pip")
    upd._apply_in_flight = True
    resp = await upd.api_update_apply(_req("POST", "/api/update"))
    assert resp.status == 409
    body = json.loads(resp.body)
    assert isinstance(body["error"], str) and body["error"]


def test_a_missing_installer_is_a_409_with_its_code() -> None:
    # The second 409 the app maps: an error object whose code says the installer is missing.
    source = inspect.getsource(upd)
    assert source.count('json_error("update_installer_missing", message=') >= 1


def test_the_status_read_carries_the_version_and_the_update_progress() -> None:
    source = inspect.getsource(handlers_system)
    assert '"update_progress": state._update_progress,' in source
    assert '"version": personalclaw.__version__,' in source
    from personalclaw.dashboard.state import DashboardState

    # The progress the app reads is the `{step, detail}` each update step pushes.
    assert list(inspect.signature(DashboardState.push_update_progress).parameters) == [
        "self",
        "step",
        "detail",
    ]
    assert 'self._update_progress = {"step": step, "detail": detail}' in inspect.getsource(
        DashboardState
    )


def test_only_the_owner_may_ask_for_an_update_or_a_check_now() -> None:
    for route in ("POST /api/update", "POST /api/update/check"):
        assert isinstance(
            ROUTE_AUTHZ.get(route), OwnerOnly
        ), f"{route} is no longer the owner's alone"


@pytest.mark.asyncio
async def test_the_updates_restarting_answer_carries_its_detail(
    monkeypatch,
) -> None:
    # The app reads a 200 `restarting` as nothing to move, and its `detail` as the gateway's words.
    # The checkout's own decision is stood in for: this pins the answer, not when it is given.
    monkeypatch.setattr(upd.self_update, "detect_install_kind", lambda: "git")

    async def _nothing_to_advance(state, *, asked, auth_mode=""):
        return upd.CheckoutUpdate("restarting", detail="Already at v0.3.8; restarting.")

    monkeypatch.setattr(upd, "start_checkout_update", _nothing_to_advance)
    monkeypatch.setattr(upd, "_live_auth_mode", lambda _request: "")
    resp = await upd.api_update_apply(_req("POST", "/api/update"))
    assert resp.status == 200
    assert json.loads(resp.body) == {
        "ok": True,
        "status": "restarting",
        "detail": "Already at v0.3.8; restarting.",
    }


def test_a_wheel_with_nothing_to_install_says_done_then_clears_its_progress() -> None:
    # The app's wait reads `update_progress` as `{step: "done"}` or as null once it is cleared:
    # either is nothing to move.
    source = inspect.getsource(upd._upgrade_wheel)
    done = source.index('state.push_update_progress("done", f"{sentence}.")')
    assert source.index("state.clear_update_progress()", done) > done
    from personalclaw.dashboard.state import DashboardState

    assert "self._update_progress = None" in inspect.getsource(DashboardState.clear_update_progress)


def test_a_loopback_caller_without_an_origin_passes_the_origin_check_both_update_writes_meet() -> (
    None
):
    # The app's press sends no Origin (Node http to 127.0.0.1): the Origin check every write meets
    # lets a loopback caller without one through, so only the owner rule decides.
    from personalclaw.dashboard import server
    from personalclaw.dashboard.origin import check_origin

    assert 'check_origin(request, require=True, fallback_header="Referer")' in inspect.getsource(
        server
    )
    for route in ("/api/update", "/api/update/check"):
        loopback = make_mocked_request("POST", route).clone(remote="127.0.0.1")
        loopback.app["allowed_origins"] = set()
        assert check_origin(loopback, require=True, fallback_header="Referer"), route
        # The same request from another host is refused: the pass is loopback's alone.
        other = make_mocked_request("POST", route).clone(remote="192.0.2.7")
        other.app["allowed_origins"] = set()
        assert not check_origin(other, require=True, fallback_header="Referer"), route


@pytest.mark.asyncio
async def test_the_restart_probe_answers_its_counts_and_restarts_nothing() -> None:
    # The confirm says how many agents and sessions the restart interrupts, from these two fields.
    class _Counts(_StateStub):
        def active_work_snapshot(self) -> dict:
            return {"running_agents": 2, "sessions": 1}

    req = make_mocked_request("POST", "/api/system/restart?probe=1")
    req.app["state"] = _Counts()
    resp = await upd.api_restart(req)
    assert json.loads(resp.body) == {"ok": True, "running_agents": 2, "sessions": 1}
    assert req.app["state"].progress == [], "the probe restarted the gateway"
    # And the gateway's own count names the same two fields.
    from personalclaw.dashboard.state import DashboardState

    snapshot = inspect.getsource(DashboardState.active_work_snapshot)
    assert 'return {"running_agents": running_agents, "sessions": sessions}' in snapshot


def test_the_sign_out_answers_whether_it_revoked_the_session() -> None:
    # The app counts its press session ended only on `revoked: true` (or a 401).
    from personalclaw.dashboard.handlers import auth

    source = inspect.getsource(auth.api_auth_logout)
    assert 'web.json_response({"ok": True, "revoked": revoked})' in source
    assert 'request.get("session_nonce")' in source, "the sign-out no longer ends the caller's own"
