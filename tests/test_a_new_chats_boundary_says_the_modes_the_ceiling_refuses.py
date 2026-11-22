"""A new chat's boundary reading says which permission modes the operator ceiling refuses.

A project's start box (Home's launcher) holds its picks until the chat is made, and the chat sets
them as it is made. Under an operator ceiling of ``approval: ask`` the mode route refuses every mode
that approves on its own (Trust reads, Trust, YOLO), so a box that showed such a pick showed a mode
the chat never ran in. The box reads the gateway's own rule from the new chat's boundary reading
(``GET /api/chat/boundary``): ``permission.refused`` names each mode the ceiling refuses, and
``permission.said`` is the sentence the mode route refuses it with. Reading it is no request for a
grant, so it writes no refusal row.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import approval_grants
from tests.chat_test_helpers import _api_app, _make_state

pytestmark = [pytest.mark.asyncio, pytest.mark.security]


def _app(state) -> web.Application:
    from personalclaw.dashboard.chat import api_chat_boundary

    app = _api_app(state)
    app.router.add_get("/api/chat/boundary", api_chat_boundary)
    return app


async def _read(state, query: str = "") -> dict:
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.get(f"/api/chat/boundary{query}")
        assert resp.status == 200
        return await resp.json()


@pytest.fixture
def refusals(monkeypatch) -> list[str]:
    """Each refusal row the ceiling would write."""
    rows: list[str] = []
    monkeypatch.setattr(approval_grants, "refused", lambda grant, **_kw: rows.append(grant))
    return rows


async def test_under_an_ask_ceiling_it_names_every_mode_that_approves_on_its_own(
    tmp_path, monkeypatch, refusals
):
    """🔴 Red before: the reading said nothing of the permission modes."""
    monkeypatch.setattr(
        "personalclaw.guardrails.policy.ceiling_permits_approval", lambda _level: False
    )

    body = await _read(_make_state(tmp_path))

    assert sorted(body["permission"]["refused"]) == ["trust", "trust_reads", "yolo"]
    assert body["permission"]["said"] == approval_grants.refusal_sentence()
    assert refusals == [], "a reading asks for no grant, so it writes no refusal row"


async def test_with_no_ceiling_it_refuses_nothing(tmp_path, monkeypatch, refusals):
    """🔴 Red before: the reading said nothing of the permission modes."""
    monkeypatch.setattr(
        "personalclaw.guardrails.policy.ceiling_permits_approval", lambda _level: True
    )

    body = await _read(_make_state(tmp_path))

    assert body["permission"] == {"refused": [], "said": ""}


async def test_an_open_chat_and_a_new_loop_are_read_as_before(tmp_path, monkeypatch, refusals):
    """An open chat's mode is set through the mode route, which says its own refusal; a loop has no
    permission mode to pick. Neither reading carries it."""
    monkeypatch.setattr(
        "personalclaw.guardrails.policy.ceiling_permits_approval", lambda _level: False
    )
    state = _make_state(tmp_path)
    chat = state.get_or_create_session()

    for query in (f"?session={chat.key}", "?loop=attended"):
        assert set(await _read(state, query)) == {"sandbox", "reach"}, query
