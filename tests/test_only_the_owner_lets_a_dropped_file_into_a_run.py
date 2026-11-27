"""Only the owner lets a dropped file into a workflow run; an app's file waits for her.

🔴 The finding (CORE, 2026-11-22, on 471bd65f6). ``POST /api/workflows/runs/{run_id}/drop`` is a
route an app may call (``apps/permissions.py``), and it took ``confirm`` from whoever sent the
request: an app posting a file the workflow gates, with ``?confirm=true``, landed it in the drop as
if the owner had approved it. Since 471bd65f6 a step reads what was dropped (``{{run.dropped}}``),
so that file reached every step that binds it. And a template's ``auto_accept_mimes`` skipped the
gate for anyone, so an app's own workflow could name ``text/*`` and need no confirmation at all.

Now ``confirm`` approves a file only from her own request (``approval_answer.of_request`` reads
the owner): an app's or an agent's is no approval. An app's file always waits for her, whatever
the template auto-accepts, and comes back refused with what it would have handed over, as an
unconfirmed drop does. The route's permission text says both.

Driven through the real route with each principal set as the auth middleware sets it: her session
(``request["user"]``), an app's token (``request["app"]``) and the gateway's own processes
(``X-Internal-Secret``).
"""

from __future__ import annotations

import types

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps.permissions import ROUTE_AUTHZ
from personalclaw.workflows import filedrop
from personalclaw.workflows import handlers as H
from personalclaw.workflows import store
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

pytestmark = pytest.mark.security

ROUTE = "POST /api/workflows/runs/{run_id}/drop"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


#: The chat the agent of these tests works for (its `X-Session-Key`).
AGENTS_CHAT = "chat:abc"


def _run(file_drop, *, started_for: str = "") -> str:
    """A running run of a workflow with *file_drop*, started for the chat *started_for* names."""
    origin = (
        RunOrigin(kind=OriginKind.CHAT, session_key=started_for) if started_for else RunOrigin()
    )
    run = WorkflowRun(
        id=store.new_run_id(), workflow_name="drop-probe", status=RunStatus.RUNNING, origin=origin
    )
    store.create(run)
    store.write_spec(run.id, {"root": {"kind": "sequence", "id": "main"}, "file_drop": file_drop})
    return run.id


def _app(principal: str) -> web.Application:
    @web.middleware
    async def proved(request: web.Request, handler):
        if principal == "owner":
            request["user"] = "owner"
        elif principal == "app":
            request["app"] = "demo-app"
        return await handler(request)

    app = web.Application(middlewares=[proved])
    H.register_workflow_routes(app)
    return app


def _form(*, confirm_part: bool = False) -> FormData:
    fd = FormData()
    if confirm_part:
        fd.add_field("confirm", "true")
    fd.add_field("file", b"Ship it on Friday.", filename="notes.txt", content_type="text/plain")
    return fd


async def _drop(principal: str, rid: str, *, query: str = "", confirm_part: bool = False):
    headers = (
        {"X-Internal-Secret": "x", "X-Session-Key": AGENTS_CHAT} if principal == "agent" else {}
    )
    async with TestClient(TestServer(_app(principal))) as client:
        r = await client.post(
            f"/api/workflows/runs/{rid}/drop{query}",
            data=_form(confirm_part=confirm_part),
            headers=headers,
        )
        return r.status, await r.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["query", "part"])
async def test_an_apps_drop_with_confirm_lands_nothing(how):
    """🔴 Before: the app's own `confirm=true` landed the file as if she had approved it."""
    rid = _run(True)
    status, body = await _drop(
        "app", rid, query="?confirm=true" if how == "query" else "", confirm_part=how == "part"
    )
    assert status == 428 and body["error"]["code"] == "approval_required", body
    assert "waits for you" in body["error"]["message"]
    assert body["error"]["detail"]["pending"]["filename"] == "notes.txt"
    assert filedrop.read_manifest(rid) == []
    assert not (filedrop.drop_dir(rid) / "notes.txt").exists()


@pytest.mark.asyncio
async def test_an_apps_drop_of_a_type_the_workflow_auto_accepts_lands_nothing():
    """🔴 Before: a template's `auto_accept_mimes` let an app's file in with no one asked."""
    rid = _run({"auto_accept_mimes": ["text/*"]})
    status, body = await _drop("app", rid)
    assert status == 428 and "waits for you" in body["error"]["message"], body
    assert filedrop.read_manifest(rid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["query", "part"])
async def test_an_agents_confirm_is_no_approval(how):
    rid = _run(True)
    status, body = await _drop(
        "agent", rid, query="?confirm=true" if how == "query" else "", confirm_part=how == "part"
    )
    assert status == 428 and body["error"]["code"] == "approval_required", body
    assert filedrop.read_manifest(rid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["query", "part"])
async def test_her_own_confirm_lets_the_file_in(how):
    """The control: her confirmation is the approval, by the query or by a form part."""
    rid = _run(True)
    status, body = await _drop(
        "owner", rid, query="?confirm=true" if how == "query" else "", confirm_part=how == "part"
    )
    assert status == 200, body
    assert [e["filename"] for e in filedrop.read_manifest(rid)] == ["notes.txt"]


@pytest.mark.asyncio
async def test_a_type_the_workflow_auto_accepts_still_needs_no_confirm_from_her():
    rid = _run({"auto_accept_mimes": ["text/*"]})
    status, body = await _drop("owner", rid)
    assert status == 200, body
    assert len(filedrop.read_manifest(rid)) == 1


def test_the_routes_permission_says_an_app_never_lets_a_file_in():
    said = ROUTE_AUTHZ[ROUTE].reason
    assert "never lands on the app's word" in said and "auto-accepts" in said, said
    assert "only a file you drop yourself" in said, said


AUTO_TEXT = {"auto_accept_mimes": ["text/*"]}


@pytest.mark.asyncio
async def test_an_agents_drop_into_another_chats_run_waits_for_her():
    """🔴 Before: another chat's agent dropped a text file into a run whose workflow auto-accepts
    `text/*`, and that run's steps read it (CORE, 2026-11-22)."""
    rid = _run(AUTO_TEXT, started_for="chat:other")
    status, body = await _drop("agent", rid)
    assert status == 428 and "an agent of another chat" in body["error"]["message"], body
    assert filedrop.read_manifest(rid) == []


@pytest.mark.asyncio
async def test_an_agents_drop_into_her_own_run_waits_for_her():
    """A run you started is no chat's own: an agent's file waits for you there too."""
    rid = _run(AUTO_TEXT)
    status, body = await _drop("agent", rid)
    assert status == 428 and "waits for you" in body["error"]["message"], body
    assert filedrop.read_manifest(rid) == []


@pytest.mark.asyncio
async def test_an_agents_drop_into_its_own_chats_run_follows_the_auto_accept_list():
    rid = _run(AUTO_TEXT, started_for=AGENTS_CHAT)
    status, body = await _drop("agent", rid)
    assert status == 200, body
    assert [e["filename"] for e in filedrop.read_manifest(rid)] == ["notes.txt"]


@pytest.mark.asyncio
async def test_an_agents_drop_into_its_own_chats_run_of_a_gated_type_still_waits():
    """Its own chat's run follows the list, and no more: its confirm is no approval there."""
    rid = _run(True, started_for=AGENTS_CHAT)
    status, body = await _drop("agent", rid, query="?confirm=true")
    assert status == 428, body
    assert filedrop.read_manifest(rid) == []


@pytest.mark.asyncio
async def test_a_run_whose_origin_cannot_be_read_holds_an_agents_drop(monkeypatch):
    """Closed: a run that cannot be read is no chat's own."""
    rid = _run(AUTO_TEXT, started_for=AGENTS_CHAT)
    real = store.get

    def unreadable(run_id: str):
        if run_id == rid:
            raise OSError("the run's record is unreadable")
        return real(run_id)

    # The drop's own read of the run, and only that: the route's guard reads the run as before.
    monkeypatch.setattr(H, "store", types.SimpleNamespace(get=unreadable))
    status, body = await _drop("agent", rid)
    assert status == 428 and "an agent of another chat" in body["error"]["message"], body
    assert filedrop.read_manifest(rid) == []
