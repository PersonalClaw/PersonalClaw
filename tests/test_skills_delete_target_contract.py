"""One delete-a-missing-thing contract across the skills module (#636).

Three sibling deletes lived in handlers/skills.py; two of them returned
``200 {"ok": false}`` for a nonexistent target while ``api_skills_delete`` 404'd.
No frontend caller read the flag — the FE api wrapper only rejects on a non-2xx —
so a double-submit or a stale surface (the Skills page, the Inbox, and the
dashboard ActionCenter all offer the same proposal id) reported "Rejected" /
"Forgotten" for a request that changed nothing.

These rails pin the sibling contract: a missing target is a 404 naming it, an
existing target still deletes with ``ok: true``, and the ``slug='*'`` bulk clear
stays an idempotent 200 (it reports an honest ``cleared`` count, never a lie).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers.skills import (
    api_ephemeral_skill_discard,
    api_skill_proposal_reject,
)
from personalclaw.skills import ephemeral
from personalclaw.skills import loader as loader_mod
from personalclaw.skills import proposals


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _mock_sel(monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.handlers.skills.sel", MagicMock(), raising=False)


def _req(match_info: dict) -> MagicMock:
    r = MagicMock()
    r.match_info = match_info
    r.get = lambda key, default=None: default
    return r


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


def _enqueue_proposal():
    return proposals.enqueue(
        slug="release-flow",
        description="How to cut a release",
        triggers="release, ship",
        procedure_md="1. tag\n2. build",
        session_key="sess:1",
        created_at="2026-07-03T00:00:00+00:00",
        kind="new",
        refine_target="",
        source_excerpt="",
    )


# ── proposal reject ───────────────────────────────────────────────────────────


def test_rejecting_a_real_proposal_returns_ok_true(home):
    p = _enqueue_proposal()
    resp = _run(api_skill_proposal_reject(_req({"id": p.id})))
    assert resp.status == 200
    assert _body(resp)["ok"] is True
    assert all(x.id != p.id for x in proposals.list_pending())


def test_rejecting_a_missing_proposal_is_404_not_a_silent_ok_false(home):
    resp = _run(api_skill_proposal_reject(_req({"id": "nonexistent-id"})))
    assert resp.status == 404
    assert "nonexistent-id" in _body(resp)["error"]


# ── ephemeral discard ─────────────────────────────────────────────────────────


def test_discarding_a_real_draft_returns_ok_true(home):
    draft = ephemeral.remember("sess:e1", "Deploy dance", "1. build\n2. ship")
    resp = _run(api_ephemeral_skill_discard(_req({"session": "sess:e1", "slug": draft.slug})))
    assert resp.status == 200
    assert _body(resp)["ok"] is True
    assert ephemeral.list_drafts("sess:e1") == []


def test_discarding_a_missing_draft_is_404(home):
    resp = _run(api_ephemeral_skill_discard(_req({"session": "sess:e1", "slug": "zzz"})))
    assert resp.status == 404
    assert "zzz" in _body(resp)["error"]


def test_bulk_clear_stays_an_idempotent_200_with_an_honest_count(home):
    # '*' is a bulk op, not a targeted delete: clearing an empty session is a
    # legitimate no-op and its count says so — no lie to pin a 404 onto.
    resp = _run(api_ephemeral_skill_discard(_req({"session": "sess:empty", "slug": "*"})))
    assert resp.status == 200
    body = _body(resp)
    assert body["ok"] is True
    assert body["cleared"] == 0
