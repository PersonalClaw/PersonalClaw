"""A skill proposal whose install is refused says why and stays waiting; one answered elsewhere
is gone.

The accept route answered ``409`` for both: a proposal no longer in the queue, and an install the
gateway refused (the new skill's files could not be written, or a refinement could not be laid
over its skill). The Inbox panel read every 409 as "This proposal was already answered", so a
refused install hid its reason and claimed a decision that was never made, while the proposal
stayed pending on the Skills page. Now a proposal that is gone answers ``404 skill_proposal_gone``
(the reject route too), and a refusal ``409 skill_proposal_not_installed`` with its reason.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.skills import (
    api_skill_proposal_accept,
    api_skill_proposal_reject,
    api_skill_proposals_list,
)
from personalclaw.skills import proposals as P
from personalclaw.skills.loader import SkillsLoader


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home for the loader, the proposal store and the inbox."""
    import personalclaw.config.loader as cfg
    import personalclaw.skills.loader as sl

    monkeypatch.setattr(sl, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.handlers.skills.sel", MagicMock(), raising=False)
    return tmp_path


def _client() -> TestClient:
    app = web.Application()
    app.router.add_get("/api/skills/proposals", api_skill_proposals_list)
    app.router.add_post("/api/skills/proposals/{id}/accept", api_skill_proposal_accept)
    app.router.add_delete("/api/skills/proposals/{id}", api_skill_proposal_reject)
    return TestClient(TestServer(app))


def _propose(slug: str = "release-notes"):
    prop = P.enqueue(
        slug=slug,
        description="Write the release notes from the merged pull requests",
        triggers="release notes",
        procedure_md="1. list the merged pull requests\n2. group them",
        session_key="s1",
        created_at="2026-10-01T00:00:00Z",
    )
    assert prop is not None
    return prop


@pytest.mark.asyncio
async def test_a_proposal_answered_elsewhere_is_404_gone(home):
    """🔴 Red before: accept of a proposal no longer waiting answered 409, as a refusal does."""
    async with _client() as client:
        gone = "/api/skills/proposals/answered-on-skills-page"
        accepted = await client.post(f"{gone}/accept", json={})
        rejected = await client.delete(gone)

        for resp in (accepted, rejected):
            assert resp.status == 404
            error = (await resp.json())["error"]
            assert error["code"] == "skill_proposal_gone"
            assert error["id"] == "answered-on-skills-page"
            assert "already answered" in error["message"]


@pytest.mark.asyncio
async def test_a_refused_install_says_why_and_the_proposal_still_waits(home, monkeypatch):
    """🔴 Red before: the refusal was the same bare 409 as a proposal that is gone."""
    prop = _propose()
    monkeypatch.setattr(SkillsLoader, "create_auto_skill", lambda self, *a, **kw: None)

    async with _client() as client:
        resp = await client.post(f"/api/skills/proposals/{prop.id}/accept", json={})
        assert resp.status == 409
        error = (await resp.json())["error"]
        assert error["code"] == "skill_proposal_not_installed"
        assert "could not write skill 'release-notes'" in error["message"]
        assert error["message"].endswith("so the proposal is still waiting.")

        listed = await (await client.get("/api/skills/proposals")).json()
    assert prop.id in {p["id"] for p in listed["proposals"]}


@pytest.mark.asyncio
async def test_an_install_that_goes_through_still_answers_ok(home):
    """The control: a proposal that installs is accepted as before."""
    prop = _propose("weekly-digest")
    async with _client() as client:
        resp = await client.post(f"/api/skills/proposals/{prop.id}/accept", json={})
        assert resp.status == 200, await resp.text()
