"""A chat's plan and its approval belong to that chat alone.

Measured before this was written: a chat's plan walkthrough, with each step's review state, was
kept in a file named by the chat key's slug (``chat_plan._path``): every character but a letter, a
digit, ``-``, ``_`` and ``.`` turned into ``_``. Two chats whose keys differ only in those
characters read and wrote one file. Each showed the other's plan as its own, an approval given in
one was given to the other's steps, and a cancel in one deleted the other's plan.

Now a plan file is named as a transcript is: the slug while the file there is this chat's or there
is none, else the slug and a short hash of the whole key, which the chat keeps while it is there.
A plan file records its chat (its session's ``project_id``), and a chat reads only a plan that
records it.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_plan_mode_shared import _app, _no_dispatch, _seed, _shown
from chat_test_helpers import _make_state

from personalclaw.dashboard import chat_plan
from personalclaw.planning import session as PS
from personalclaw.planning.session import StepStatus

pytestmark = [pytest.mark.asyncio, pytest.mark.security]

AWAITING = StepStatus.AWAITING_REVIEW.value
APPROVED = StepStatus.APPROVED.value
MINE = "# Plan\n- rename the module\n- update its imports"
THEIRS = "# Plan\n- push the branch to main"


def _url(name: str, what: str) -> str:
    return f"/api/chat/sessions/{quote(name, safe='')}/{what}"


async def _plan(client, state, chat, words: str) -> None:
    """*chat* opens plan mode, and its planning turn drafts *words* for review."""
    r = await client.post(_url(chat.key, "plan/activate"), json={})
    assert r.status == 200, await r.text()
    chat.append("assistant", words, "msg msg-a")
    chat.drain()
    assert chat_plan.maybe_submit_plan_draft(state, chat) is True


async def _shown_plan(client, name: str) -> list[tuple[str, str]]:
    """The plan *name*'s chat shows: each step's words and review state."""
    r = await client.get(_url(name, "plan-session"))
    assert r.status == 200, await r.text()
    session = (await r.json())["session"]
    return [(s["artifact"]["markdown"], s["status"]) for s in (session or {}).get("steps", [])]


async def _approve(client, name: str) -> None:
    r = await client.post(
        _url(name, "plan/approve"),
        json={"step_id": "chat-plan-1"},
        headers=_shown(name, "chat-plan-1"),
    )
    assert r.status == 200, await r.text()


async def test_a_plan_written_in_one_chat_is_not_shown_in_its_alike(tmp_path, monkeypatch):
    """🔴 Red before: the plan ``a?b`` wrote was shown in ``a:b`` as ``a:b``'s, waiting for the
    owner's review there."""
    _no_dispatch(monkeypatch)
    state = _make_state(tmp_path)
    mine, theirs = _seed(state, "a:b"), _seed(state, "a?b")
    async with TestClient(TestServer(_app(state))) as client:
        await _plan(client, state, theirs, THEIRS)

        assert await _shown_plan(client, mine.key) == []
        assert await _shown_plan(client, theirs.key) == [(THEIRS, AWAITING)]
        assert chat_plan.awaiting_review(mine.key) == ""


async def test_an_approval_given_in_one_chat_is_not_given_to_the_others_plan(tmp_path, monkeypatch):
    """🔴 Red before: both chats' plans were one file, so the owner's yes in ``a:b`` was a yes to
    the steps ``a?b`` wrote last."""
    _no_dispatch(monkeypatch)
    state = _make_state(tmp_path)
    mine, theirs = _seed(state, "a:b"), _seed(state, "a?b")
    async with TestClient(TestServer(_app(state))) as client:
        await _plan(client, state, mine, MINE)
        await _plan(client, state, theirs, THEIRS)

        await _approve(client, mine.key)

        assert await _shown_plan(client, mine.key) == [(MINE, APPROVED)]
        assert await _shown_plan(client, theirs.key) == [(THEIRS, AWAITING)]
        assert chat_plan.awaiting_review(theirs.key) == "chat-plan-1"


async def test_a_cancel_in_one_chat_leaves_the_others_plan(tmp_path, monkeypatch):
    """🔴 Red before: cancelling ``a?b``'s plan deleted the file both chats' plans were in."""
    _no_dispatch(monkeypatch)
    state = _make_state(tmp_path)
    mine, theirs = _seed(state, "a:b"), _seed(state, "a?b")
    async with TestClient(TestServer(_app(state))) as client:
        await _plan(client, state, mine, MINE)
        await _plan(client, state, theirs, THEIRS)

        r = await client.post(_url(theirs.key, "plan/cancel"), json={})
        assert r.status == 200, await r.text()

        assert await _shown_plan(client, mine.key) == [(MINE, AWAITING)]
        assert await _shown_plan(client, theirs.key) == []


async def test_a_chat_named_as_its_file_keeps_its_own_plan_beside_an_alike(tmp_path, monkeypatch):
    """A chat whose key is its own file name (``a_b``) is no exception: once ``a:b``'s plan holds
    that name, ``a_b``'s plan is a file of its own."""
    _no_dispatch(monkeypatch)
    state = _make_state(tmp_path)
    first, second = _seed(state, "a:b"), _seed(state, "a_b")
    async with TestClient(TestServer(_app(state))) as client:
        await _plan(client, state, first, MINE)
        await _plan(client, state, second, THEIRS)

        assert await _shown_plan(client, first.key) == [(MINE, AWAITING)]
        assert await _shown_plan(client, second.key) == [(THEIRS, AWAITING)]
    assert chat_plan._path(first.key).name == "a_b.json"
    assert chat_plan._path(second.key).name.startswith("a_b-")
    assert len(list((chat_plan.config_dir() / PS.CHAT_PLANS_DIR).glob("*.json"))) == 2


async def test_a_plan_kept_by_an_earlier_version_is_read_by_its_chat_alone(tmp_path, monkeypatch):
    """🔴 Red before: ``a?b`` opened ``a:b``'s kept plan as its own. A plan file an earlier
    version wrote stays where it is and records its chat, so that chat reads it, and an alike chat
    starts a plan of its own."""
    _no_dispatch(monkeypatch)
    state = _make_state(tmp_path)
    mine, theirs = _seed(state, "a:b"), _seed(state, "a?b")
    async with TestClient(TestServer(_app(state))) as client:
        await _plan(client, state, mine, MINE)
    kept = chat_plan.config_dir() / PS.CHAT_PLANS_DIR / "a_b.json"
    assert json.loads(kept.read_text())["session"]["project_id"] == "a:b"

    async with TestClient(TestServer(_app(state))) as client:
        assert await _shown_plan(client, theirs.key) == []
        await _plan(client, state, theirs, THEIRS)
        assert await _shown_plan(client, mine.key) == [(MINE, AWAITING)]
        assert await _shown_plan(client, theirs.key) == [(THEIRS, AWAITING)]
    assert json.loads(kept.read_text())["session"]["project_id"] == "a:b"


async def test_a_plan_file_that_records_another_chat_is_never_shown(tmp_path, monkeypatch):
    """Whatever file a chat's plan name finds, a plan that records another chat is not this chat's
    plan: it reads as no plan, and waits for no review there."""
    _no_dispatch(monkeypatch)
    state = _make_state(tmp_path)
    mine, theirs = _seed(state, "a:b"), _seed(state, "a?b")
    async with TestClient(TestServer(_app(state))) as client:
        await _plan(client, state, mine, MINE)
        await _plan(client, state, theirs, THEIRS)
    own = chat_plan._path(theirs.key)
    assert own.name.startswith("a_b-")
    raw = json.loads(own.read_text())
    raw["session"]["project_id"] = "a:b"
    own.write_text(json.dumps(raw))

    assert chat_plan.read(theirs.key) == (None, {})
    assert chat_plan.awaiting_review(theirs.key) == ""


async def test_a_chat_keeps_its_own_plan_once_its_alikes_plan_is_gone(tmp_path, monkeypatch):
    """Once the chat that holds the safe name cancels its plan, an alike keeps its plan file of its
    own. Falling back to the freed safe name would leave its plan, waiting for review, behind."""
    _no_dispatch(monkeypatch)
    state = _make_state(tmp_path)
    mine, theirs = _seed(state, "a:b"), _seed(state, "a?b")
    async with TestClient(TestServer(_app(state))) as client:
        await _plan(client, state, mine, MINE)
        await _plan(client, state, theirs, THEIRS)
        r = await client.post(_url(mine.key, "plan/cancel"), json={})
        assert r.status == 200, await r.text()
        assert not (chat_plan.config_dir() / PS.CHAT_PLANS_DIR / "a_b.json").exists()

        assert await _shown_plan(client, theirs.key) == [(THEIRS, AWAITING)]
        assert chat_plan.awaiting_review(theirs.key) == "chat-plan-1"
    assert chat_plan._path(theirs.key).name.startswith("a_b-")


async def test_a_plan_file_nobody_can_read_is_left_as_it_is(tmp_path, monkeypatch):
    """A plan file at a chat's safe name that can't be read is no chat's: no chat can show it is
    its own. A chat plans in a file of its own, and that file is left as it is."""
    _no_dispatch(monkeypatch)
    state = _make_state(tmp_path)
    folder = chat_plan.config_dir() / PS.CHAT_PLANS_DIR
    folder.mkdir(parents=True)
    (folder / "a_b.json").write_text("{not a plan", encoding="utf-8")
    mine = _seed(state, "a:b")
    async with TestClient(TestServer(_app(state))) as client:
        await _plan(client, state, mine, MINE)

        assert await _shown_plan(client, mine.key) == [(MINE, AWAITING)]
    assert chat_plan._path(mine.key).name.startswith("a_b-")
    assert (folder / "a_b.json").read_text(encoding="utf-8") == "{not a plan"
