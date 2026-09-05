"""#621 — drafting is gated by ``can_reply``, like sending always was.

``api_inbox_draft`` called ``svc.draft_reply`` with no gate, so on an item whose
Send button is permanently disabled (``can_reply=False`` — all 44 on the measured
instance) the model ran, a full reply persisted, and the row badged 'draft'.
The gate mirrors the send handler's own refusal, and fires BEFORE the model
call — the point is the token that no longer burns.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from personalclaw.dashboard.handlers_inbox import api_inbox_draft


def _req(item) -> tuple[MagicMock, AsyncMock]:
    """A request whose inbox service holds exactly ``item`` (or nothing)."""
    draft_spy = AsyncMock(return_value=item)
    svc = MagicMock()
    svc.draft_reply = draft_spy
    # _get_inbox prefers the running service's (state, inbox) pair.
    svc.state = MagicMock()
    svc.inbox = SimpleNamespace(items={item.id: item} if item is not None else {})
    r = MagicMock()
    app = web.Application()
    app["state"] = MagicMock(_inbox_svc=svc)
    r.app = app
    r.match_info = {"id": item.id if item is not None else "missing"}
    return r, draft_spy


def _item(can_reply: bool):
    return SimpleNamespace(
        id="it-1",
        can_reply=can_reply,
        to_dict=lambda: {"id": "it-1", "can_reply": can_reply},
    )


async def _json(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_read_only_item_refuses_before_the_model_runs():
    req, draft_spy = _req(_item(can_reply=False))
    resp = await api_inbox_draft(req)
    assert resp.status == 400
    assert "does not support replies" in (await _json(resp))["error"]
    # The whole point: no model call happened.
    draft_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_replyable_item_still_drafts():
    req, draft_spy = _req(_item(can_reply=True))
    resp = await api_inbox_draft(req)
    assert resp.status == 200
    draft_spy.assert_awaited_once_with("it-1")


@pytest.mark.asyncio
async def test_missing_item_is_404_and_never_drafts():
    req, draft_spy = _req(_item(can_reply=True))
    req.match_info = {"id": "nope"}
    resp = await api_inbox_draft(req)
    assert resp.status == 404
    draft_spy.assert_not_awaited()
