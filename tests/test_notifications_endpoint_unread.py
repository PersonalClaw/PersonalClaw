"""`GET /api/notifications` — what `unread` counts (issue #422).

The field used to ship `state.unread_count()`, which counts PENDING **inbox items**. That pivot is
deliberate and documented on that method — the inbox tracks "is this dealt with" and the log tracks
"did a delivery get acknowledged" — but under the notifications endpoint's `unread` key it answered
a different question from the one the name asks. The two track unrelated state and drift
independently: measured live, the field read 33 while every badge in the app rendered 41 over the
same 74 rows.

Nothing in `web/` read it (`NotificationBell`, `NotificationsPage` and `HeroPulse` each compute
`items.filter(n => !n.acked).length` themselves), so the defect was invisible in the UI and aimed
squarely at an API consumer — a mobile client, the MCP surface — with no reason to suspect a count
in a notifications payload was not about notifications.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers.messaging import api_notifications


def _req(state):
    r = MagicMock()
    r.app = {"state": state}
    return r


async def _json(resp):
    return json.loads(resp.body.decode())


@pytest.fixture()
def state(tmp_path, monkeypatch):
    from tests.chat_test_helpers import _make_state

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return _make_state(tmp_path)


@pytest.mark.asyncio
async def test_unread_counts_unacked_notification_rows(state):
    state._notification_log = [
        {"ts": "1", "kind": "info", "title": "a", "acked": True},
        {"ts": "2", "kind": "info", "title": "b", "acked": False},
        {"ts": "3", "kind": "info", "title": "c"},  # never acked ⇒ unread
    ]
    data = await _json(await api_notifications(_req(state)))
    assert data["unread"] == 2
    assert len(data["notifications"]) == 3


@pytest.mark.asyncio
async def test_unread_is_not_the_inbox_pending_count(state, monkeypatch):
    """🪤 THE VACUITY LEG, and the one that actually fails on the old code.

    Without it, any test whose inbox happens to be empty passes against `unread_count()` too — the
    two numbers only disagree when both stores are non-empty, which is exactly why this shipped.
    So the inbox count is forced to a value the log cannot produce.
    """
    monkeypatch.setattr(type(state), "unread_count", lambda self: 99, raising=False)
    state._notification_log = [{"ts": "1", "kind": "info", "title": "a", "acked": False}]
    data = await _json(await api_notifications(_req(state)))
    assert data["unread"] == 1, "the endpoint is still reporting the inbox's pending count"


@pytest.mark.asyncio
async def test_unread_agrees_with_the_expression_every_frontend_surface_uses(state):
    """The three badges compute `!n.acked` themselves; the payload must not disagree with them."""
    state._notification_log = [
        {"ts": str(i), "kind": "info", "title": "t", "acked": i % 3 == 0} for i in range(10)
    ]
    data = await _json(await api_notifications(_req(state)))
    assert data["unread"] == sum(1 for n in state._notification_log if not n.get("acked"))


@pytest.mark.asyncio
async def test_unread_is_zero_on_an_empty_log(state):
    state._notification_log = []
    assert (await _json(await api_notifications(_req(state))))["unread"] == 0
