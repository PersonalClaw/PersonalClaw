"""An image or a video a chat's agent makes belongs to the chat's Project, in either runtime.

``image_generate`` and ``video_generate`` saved what they made with ``create_binary`` and no
``project_id``, so the picture or the clip was filed under no Project and never showed on the
Project's page, whichever runtime made it:

* **PersonalClaw's own agent** runs the tool in the gateway with the turn's Project bound
  (``builtin_tools.bind_tool_context``), and the save did not read it.
* **An agent CLI's** call of either tool is made by the gateway (``MODEL_WORK``, through
  ``POST /api/tools/invoke``), which runs it as the work of the chat the call names
  (``_as_its_work``) and binds no Project. So these two tools, in the gateway with no Project
  bound, read the Project of the chat the call names from the gateway's open chats, as
  ``GET /api/chat/sessions/bound-project`` reads a ``dashboard:`` chat. That door is not
  widened: the project contextvar, and with it ``command_secrets.call_project()``, stay unbound
  for such a call.

A chat in no Project stamps '' quietly; work that is no chat's (an app, a scheduled job, your
own pages) stamps ''; and a lookup that fails stamps '' and says so, as an artifact's does.
"""

from __future__ import annotations

import base64
import logging
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.mcp_artifacts as ma
from personalclaw import artifact_project, mcp_core
from personalclaw.dashboard import token_auth
from personalclaw.inbox_providers import native_source

SECRET = "the-internal-credential-for-these-tests"
PORT = 10000
P = "proj-harbour"
IN_P, LOOSE = "chat-in-p", "chat-loose"
NOTE = artifact_project.NO_PROJECT_NOTE
_PNG = b"\x89PNG\r\n\x1a\n" + b"a harbour at dusk" * 4
_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"a tide coming in" * 4

#: What the model was called under, for each call: (the turn's Project, the secrets' Project).
SEEN: list[tuple[str, str]] = []


def _bound_projects() -> tuple[str, str]:
    from personalclaw.agents.native.builtin_tools import current_project_id
    from personalclaw.agents.native.command_secrets import call_project

    return current_project_id(), call_project()


class _Painter:
    name = "painter"

    async def list_models(self):
        from personalclaw.image_gen.provider import ImageGenModel

        return [ImageGenModel(name="p1")]

    async def generate(self, prompt: str, **_kw: Any):
        from personalclaw.image_gen.provider import ImageResult

        SEEN.append(_bound_projects())
        return [ImageResult(b64=base64.b64encode(_PNG).decode(), mime="image/png")]


class _Filmer:
    name = "filmer"

    def __init__(self, clip) -> None:
        self.clip = clip

    async def generate(self, prompt: str, **_kw: Any):
        from personalclaw.video_gen.provider import VideoResult

        SEEN.append(_bound_projects())
        return [VideoResult(local_path=str(self.clip), mime="video/mp4")]


ARGS = {
    "image": ("image_generate", {"prompt": "A harbour at dusk", "name": "Harbour"}),
    "video": ("video_generate", {"prompt": "A tide coming in", "name": "Tide"}),
}


@pytest.fixture
def library(tmp_path, monkeypatch):
    """The artifact library, the stubbed image and video models, and the gateway's open chats:
    one in Project P and one in no Project."""
    from personalclaw.artifacts import native
    from personalclaw.artifacts import registry as art_reg
    from personalclaw.image_gen import registry as ig_reg
    from personalclaw.video_gen import registry as vg_reg

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
    monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", False)
    store = native.NativeArtifactProvider(root=tmp_path / "artifacts")
    monkeypatch.setattr(art_reg, "get_provider", lambda name="native": store)
    monkeypatch.setattr(ig_reg, "active_image_gen", lambda: (_Painter(), "p1"))
    clip = tmp_path / "tide.mp4"
    clip.write_bytes(_MP4)
    monkeypatch.setattr(vg_reg, "active_video_gen", lambda: (_Filmer(clip), "f1"))
    state = SimpleNamespace(
        _sessions={
            IN_P: SimpleNamespace(project_id=P),
            LOOSE: SimpleNamespace(project_id=""),
            # Named as a scheduled job and an app are, so a lookup that took the name after the
            # colon from work that is no chat's would find a Project here.
            "nightly": SimpleNamespace(project_id=P),
            "field-notes": SimpleNamespace(project_id=P),
            "ui": SimpleNamespace(project_id=P),
        }
    )
    native_source.set_dashboard_state(state)
    SEEN.clear()
    try:
        yield SimpleNamespace(store=store, state=state)
    finally:
        native_source.set_dashboard_state(None)


def _only(store, kind: str):
    (art,) = store.list(kind=kind)
    return art


# ── PersonalClaw's own agent, in the gateway ─────────────────────────────────────────────────


def _native(work: str, project: str, kind: str) -> str:
    """The tool as the native runtime calls it: the chat's session and its Project bound for the
    turn (``bind_tool_context``), through the in-process tool group's ``_call_tool``."""
    from personalclaw.agents.native import builtin_tools

    tool, args = ARGS[kind]
    bound = builtin_tools.bind_tool_context(cwd=None, project_id=project)
    token = mcp_core.set_current_session_key(work)
    try:
        return ma._call_tool(tool, dict(args))
    finally:
        mcp_core.reset_current_session_key(token)
        builtin_tools.reset_tool_context(bound)


@pytest.mark.parametrize("kind", ["image", "video"])
def test_native_a_save_in_a_project_chat_is_its_projects(library, kind):
    said = _native(f"dashboard:{IN_P}", P, kind)
    assert _only(library.store, kind).project_id == P, said
    assert not said.endswith(NOTE), said


@pytest.mark.parametrize("kind", ["image", "video"])
def test_native_a_save_in_a_chat_in_no_project_is_no_projects(library, kind):
    said = _native(f"dashboard:{LOOSE}", "", kind)
    assert _only(library.store, kind).project_id == "", said
    assert not said.endswith(NOTE), said


# ── An agent CLI's call, made by the gateway through POST /api/tools/invoke ──────────────────


@pytest_asyncio.fixture
async def gateway(library, tmp_path, monkeypatch):
    """The gateway's sign-in and its tool route on a live server, serving the artifacts tools."""
    from personalclaw.dashboard.handlers.tools import api_tool_invoke
    from personalclaw.dashboard.server import INTERNAL_ROUTES, MIXED_INTERNAL_ROUTES
    from personalclaw.tool_providers.registry import create_artifacts_provider

    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"an-image-a-project-chat-makes-is-its-projects")
    token_auth.revoke_all_sessions()
    artifacts = create_artifacts_provider()
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [artifacts])
    app = web.Application(
        middlewares=[
            token_auth.token_auth_middleware(
                port=PORT,
                internal_routes=INTERNAL_ROUTES,
                mixed_internal_routes=MIXED_INTERNAL_ROUTES,
                internal_secret=SECRET,
            )
        ]
    )
    app["state"] = library.state
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()


async def _from_the_cli(client, work: str, kind: str) -> str:
    """The call the agent CLI's tool server hands the gateway (``mcp_core.made_by_the_gateway``):
    the internal credential, naming the chat it serves."""
    tool, args = ARGS[kind]
    resp = await client.post(
        "/api/tools/invoke",
        json={"tool": tool, "arguments": dict(args)},
        headers={"X-Internal-Secret": SECRET, "X-Session-Key": work},
    )
    body = await resp.json()
    assert resp.status == 200 and body.get("ok"), body
    return str(body["output"])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "video"])
async def test_cli_a_save_in_a_project_chat_is_its_projects(gateway, library, kind):
    said = await _from_the_cli(gateway, f"dashboard:{IN_P}", kind)
    assert _only(library.store, kind).project_id == P, said
    assert not said.endswith(NOTE), said


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "video"])
async def test_cli_a_save_in_a_chat_in_no_project_is_no_projects(gateway, library, kind):
    said = await _from_the_cli(gateway, f"dashboard:{LOOSE}", kind)
    assert _only(library.store, kind).project_id == "", said
    assert not said.endswith(NOTE), said


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "video"])
async def test_cli_the_call_binds_no_project_for_its_secrets(gateway, library, kind):
    """The door is not widened: the model is called with no Project bound for the turn, so
    ``command_secrets.call_project()`` answers as before for a gateway-made call."""
    await _from_the_cli(gateway, f"dashboard:{IN_P}", kind)
    assert SEEN == [("", "")]


# ── The chat the call is bound to ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["image", "video"])
def test_native_an_inherited_session_key_names_no_chat(library, monkeypatch, kind):
    """A gateway started from an agent CLI's shell keeps that chat's PERSONALCLAW_SESSION_KEY.
    What a turn of another chat makes is that other chat's."""
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", f"dashboard:{IN_P}")
    said = _native(f"dashboard:{LOOSE}", "", kind)
    assert _only(library.store, kind).project_id == "", said


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "video"])
async def test_cli_an_inherited_session_key_names_no_chat(gateway, library, monkeypatch, kind):
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", f"dashboard:{IN_P}")
    said = await _from_the_cli(gateway, f"dashboard:{LOOSE}", kind)
    assert _only(library.store, kind).project_id == "", said


def test_a_deleted_image_regenerated_from_its_chat_is_its_projects(library):
    """Regenerate brings a deleted image back at its slug. The web client names the chat by its
    bare id, and ``api_artifact_regenerate`` passes that on."""
    said = _native(f"dashboard:{IN_P}", P, "image")
    made = _only(library.store, "image")
    assert made.project_id == P, said
    library.store.delete(made.slug)
    got = ma.regenerate_image_at_slug(
        library.store, made.slug, "A harbour at dusk", session_id=IN_P
    )
    assert got == (True, made.slug)
    assert library.store.get(made.slug).project_id == P


# ── Work that is no chat's, and a lookup that fails ──────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("work", ["cron:nightly", "dashboard:ui"])
async def test_cli_work_that_is_no_chats_stamps_nothing(gateway, library, work):
    said = await _from_the_cli(gateway, work, "image")
    assert _only(library.store, "image").project_id == "", said
    assert not said.endswith(NOTE), said


@pytest.mark.parametrize("kind", ["image", "video"])
def test_an_apps_work_stamps_nothing(library, kind):
    said = _native("app:field-notes", "", kind)
    assert _only(library.store, kind).project_id == "", said
    assert not said.endswith(NOTE), said


@pytest.mark.parametrize("kind", ["image", "video"])
def test_a_lookup_that_fails_stamps_nothing_and_says_so(library, kind, caplog):
    native_source.set_dashboard_state(None)
    caplog.set_level(logging.WARNING)
    said = _native(f"dashboard:{IN_P}", "", kind)
    assert _only(library.store, kind).project_id == "", said
    assert said.endswith(NOTE), said
    warned = [
        r.getMessage()
        for r in caplog.records
        if r.name == "personalclaw.mcp_artifacts"
        and r.levelno == logging.WARNING
        and f"dashboard:{IN_P}" in r.getMessage()
    ]
    assert len(warned) == 1, warned


@pytest.mark.parametrize("kind", ["image", "video"])
def test_a_failed_lookups_warning_names_the_turns_own_chat(library, monkeypatch, kind, caplog):
    """A gateway started from an agent CLI's shell keeps that chat's PERSONALCLAW_SESSION_KEY.
    When the open chats can't be read, the WARNING for the row filed under no project names the
    chat the turn is for, the one whose project was looked up, not the inherited one."""
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", f"dashboard:{LOOSE}")
    native_source.set_dashboard_state(None)
    caplog.set_level(logging.WARNING)
    said = _native(f"dashboard:{IN_P}", "", kind)
    assert said.endswith(NOTE), said
    filed = [
        r.getMessage()
        for r in caplog.records
        if r.name == "personalclaw.mcp_artifacts" and r.getMessage().startswith("Filed ")
    ]
    assert len(filed) == 1, filed
    assert f"dashboard:{IN_P}" in filed[0] and LOOSE not in filed[0], filed
