"""A proposal's review says what it is and what Accept does, before either is pressed.

``GET /api/learning/proposals/{id}/review`` is what a proposal's panel reads. It serves:
- the card's lines;
- why it was proposed (only what its producer wrote);
- the thing itself, by type;
- where it came from;
- what Accept and Reject do, worked out at the read.
It is read-only, and only you read it: an app's token is refused.

The detail, accept and reject routes answer their 404s with the same codes:
- ``learning_disabled`` while learning is off;
- ``learning_proposal_gone`` for a proposal that is no longer waiting.
So a card says what each means.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import learning as L
from personalclaw.learning import proposals

STEPS = ["Fetch the changes merged in {{week}}", "Group the result by project", "Draft the email"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(proposals, "_dir", lambda: tmp_path / "proposals")
    monkeypatch.setattr(proposals, "_decisions_path", lambda: tmp_path / "decisions.json")
    monkeypatch.setattr(L, "_enabled", lambda: True)
    (tmp_path / "proposals").mkdir()
    return tmp_path


def _call(handler, method: str, pid: str, *, user="me", app=None, query: str = ""):
    path = f"/api/learning/proposals/{pid}/review{query}"
    request = make_mocked_request(method, path, match_info={"id": pid}, app=web.Application())
    if user:
        request["user"] = user
    if app:
        request["app"] = app
    resp = asyncio.run(handler(request))
    return resp.status, json.loads(resp.body.decode())


def _template() -> proposals.Proposal:
    _verdict, prop = proposals.enqueue(
        kind="template",
        title="Draft the Friday status email from the week's merged changes",
        body="\n".join(f"- {s}" for s in STEPS) + "\n\nWhy: You send it every Friday by hand",
        provenance="inferred",
        session_key="dashboard:c1",
        run_id="weekly-status-report",
        tags=["ad_hoc_to_template", "auto_file", "name:weekly-status-report"],
    )
    return prop


def test_the_review_serves_the_card_the_steps_the_why_and_what_accept_does(home):
    prop = _template()
    status, body = _call(L.api_learning_proposal_review, "GET", prop.id)
    assert status == 200, body
    assert body["headline"] == "Draft the Friday status email from the week's merged changes"
    assert body["name"] == "weekly-status-report"
    assert body["content"] == {"type": "steps", "steps": STEPS}
    assert body["why"] == "You send it every Friday by hand"
    assert body["accept"] == {
        "effect": "installs",
        "verb": "Save template",
        "sentence": "Saves the template weekly-status-report to Workflows",
        "confirm": False,
    }
    assert body["reject"]["verb"] == "Reject"
    assert body["session_key"] == "dashboard:c1" and body["runs"] == []


def test_the_review_reads_and_writes_nothing(home):
    prop = _template()
    before = {p.name: p.read_bytes() for p in (home / "proposals").iterdir()}
    _call(L.api_learning_proposal_review, "GET", prop.id)
    assert {p.name: p.read_bytes() for p in (home / "proposals").iterdir()} == before
    assert proposals.get(prop.id).status == "pending"


def test_an_apps_token_is_refused(home):
    prop = _template()
    status, body = _call(L.api_learning_proposal_review, "GET", prop.id, user=None, app="acme")
    assert status == 403
    assert body["error"]["code"] == "learning_review_owner_only"


def test_a_proposal_no_longer_waiting_is_gone_on_every_route(home):
    for handler, method in (
        (L.api_learning_proposal_review, "GET"),
        (L.api_learning_proposal, "GET"),
        (L.api_learning_proposal_accept, "POST"),
        (L.api_learning_proposal_reject, "DELETE"),
    ):
        status, body = _call(handler, method, "lp-never")
        assert status == 404, handler.__name__
        assert body["error"]["code"] == "learning_proposal_gone", handler.__name__


def test_learning_off_says_so_on_every_route(home, monkeypatch):
    prop = _template()
    monkeypatch.setattr(L, "_enabled", lambda: False)
    for handler, method in (
        (L.api_learning_proposal_review, "GET"),
        (L.api_learning_proposal, "GET"),
        (L.api_learning_proposal_accept, "POST"),
        (L.api_learning_proposal_reject, "DELETE"),
    ):
        status, body = _call(handler, method, prop.id)
        assert status == 404, handler.__name__
        assert body["error"]["code"] == "learning_disabled"
        assert body["error"]["message"] == "Learning is off, so this can't be decided here."


def test_a_review_that_cannot_be_built_is_unreadable_not_empty(home, monkeypatch):
    from personalclaw.learning import review

    prop = _template()

    def _boom(*_a, **_k):
        raise ValueError("bad record")

    monkeypatch.setattr(review, "review_for", _boom)
    status, body = _call(L.api_learning_proposal_review, "GET", prop.id)
    assert status == 500
    assert body["error"]["code"] == "learning_proposal_unreadable"


def test_a_captures_review_cuts_each_capture_and_opens_one_whole(home, monkeypatch):
    from personalclaw.learning import staging

    store = staging.StagingStore(home)
    monkeypatch.setattr(staging, "get_store", lambda *a, **k: store)
    long = "Prefers squash merges " + "and keeps the history linear " * 40
    store.stage(cadence="per_turn", kind="preference", content=long)
    store.stage(cadence="per_turn", kind="preference", content="Keeps PR titles short")
    entry, other = store.pending(limit=2)
    uids = [entry.uid, other.uid]
    _verdict, prop = proposals.enqueue(
        kind="lesson_batch",
        title="2 capture(s) staged while no model was bound",
        body="\n".join(f"- {t}" for t in (long, "Keeps PR titles short")),
        provenance="inferred",
        staging_refs=uids,
        occurrences=1,
        min_evidence=1,
    )
    _status, body = _call(L.api_learning_proposal_review, "GET", prop.id)
    first = body["content"]["captures"][0]
    assert len(first["text"]) == 600 and first["cut"] is True
    _status, body = _call(
        L.api_learning_proposal_review, "GET", prop.id, query=f"?capture={entry.uid}"
    )
    first = body["content"]["captures"][0]
    assert first["text"] == long and first["cut"] is False
    store.close()
