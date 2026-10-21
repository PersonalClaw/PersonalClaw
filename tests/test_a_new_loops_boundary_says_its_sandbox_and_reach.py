"""A new loop's Execution Boundary reads its sandbox and its reach from the gateway (#3808).

The loop composer's footer shows, before a loop is created, its Mode (the loop's permission mode:
Attended or Unattended), where it works, the sandbox its commands run in and the network its work
reaches. A loop not created yet has no run to read, so ``GET /api/chat/boundary?loop=<mode>`` reads
its reach for the posture its Mode gives every session of the loop: an Attended loop's are watched
(``INTERACTIVE``), an Unattended loop's are not (``HEADLESS``), each layered and bounded by the
operator's ceiling exactly as ``profile_for_session`` resolves them once the loop runs
(``policy.profile_for_posture``). The sandbox is its runtime's, as a new chat's on it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import sandbox
from personalclaw.guardrails import policy
from tests.chat_test_helpers import _api_app, _make_state

pytestmark = pytest.mark.asyncio


def _app(state) -> web.Application:
    from personalclaw.dashboard.chat import api_chat_boundary

    app = _api_app(state)
    app.router.add_get("/api/chat/boundary", api_chat_boundary)
    return app


async def _read(state, query: str = "") -> tuple[int, dict]:
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.get(f"/api/chat/boundary{query}")
        return resp.status, await resp.json()


@pytest.fixture
def backend(monkeypatch) -> Iterator[None]:
    """This computer's sandbox backend, as the probe would answer."""
    monkeypatch.setattr(
        sandbox,
        "detect_backend",
        lambda config_mode="auto": "none" if config_mode == "off" else "namespace",
    )
    yield


@pytest.fixture
def postures_apart(monkeypatch):
    """The operator's layering gives each posture its own tier, so a reading shows which it was
    read for."""
    real = policy.safety_profile_for

    def layered(base):
        tier = "registry" if base is policy.HEADLESS else "listed"
        return real(base).with_overrides(egress_tier=tier)

    monkeypatch.setattr(policy, "safety_profile_for", layered)


@pytest.mark.parametrize("unattended", [True, False])
def test_a_posture_resolves_as_a_session_of_that_posture_does(
    monkeypatch, postures_apart, unattended
):
    """``profile_for_session`` is ``profile_for_posture`` of the session's posture: one
    composition, so the reading of a loop not yet created is what its sessions get."""
    monkeypatch.setattr(policy, "is_unattended_session", lambda key: unattended)
    assert policy.profile_for_session("any-session") == policy.profile_for_posture(unattended)
    assert policy.profile_for_posture(unattended).egress_tier == (
        "registry" if unattended else "listed"
    )


@pytest.mark.parametrize(("mode", "tier"), [("unattended", "registry"), ("attended", "listed")])
async def test_a_new_loops_reach_is_its_modes(tmp_path, backend, postures_apart, mode, tier):
    status, body = await _read(_make_state(tmp_path), f"?loop={mode}")
    assert status == 200
    assert body["reach"]["tier"] == tier
    assert body["sandbox"]["confined"] is True


async def test_a_loop_that_runs_as_a_workflow_run_is_read_unattended_whatever_its_mode(
    tmp_path, backend, postures_apart
):
    """A general loop's stages are a workflow run's, which nobody watches step by step."""
    status, body = await _read(_make_state(tmp_path), "?loop=attended&kind=general")
    assert status == 200
    assert body["reach"]["tier"] == "registry"
    _, body = await _read(_make_state(tmp_path), "?loop=attended&kind=goal")
    assert body["reach"]["tier"] == "listed"


async def test_a_loop_mode_it_does_not_know_or_a_chat_beside_it_is_refused(tmp_path, backend):
    state = _make_state(tmp_path)
    chat = state.get_or_create_session()
    assert (await _read(state, "?loop=sometimes"))[0] == 400
    assert (await _read(state, f"?session={chat.key}&loop=attended"))[0] == 400


async def test_a_new_chats_reading_is_as_it_was(tmp_path, backend, postures_apart):
    """Without ``loop`` the reading is a new chat's: an attended run, unnamed."""
    status, body = await _read(_make_state(tmp_path))
    assert status == 200
    assert body["reach"]["tier"] == "listed"
