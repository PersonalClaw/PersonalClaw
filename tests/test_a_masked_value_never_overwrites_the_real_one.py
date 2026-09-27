"""A masked value is never saved back over the real one.

Every read that hands text to an editor masks what looks like a credential, and every editor
seeded from such a read sends the mask back on save. Measured on ``main`` at 6e327611a, each save
below wrote ``[REDACTED: credential]`` over the real value, and only prompts and snippets had the
inverse (``security.restore_masked_spans``, issue 440):

    Files: edit any line of a file holding a key, save       -> the key in the file is the marker
    Files → Save as artifact (no edit at all)                -> the source file is rewritten masked
    Artifact editor, and the agent's artifact_update          -> the body is stored masked
    Loop plan review → Launch (no edit)                      -> task, plan and verify_command masked
    Schedule: rename an agent schedule                       -> its prompt is stored masked
    Inbox: save or send a draft                              -> the draft is stored masked
    Agent knowledge_update after knowledge_get               -> the item's body is stored masked
    PUT /api/memory/semantic with a value it listed          -> the fact is stored masked
    MCP server PUT with the display mask as a value          -> the credential store holds the mask

The contract: a save that carries an unchanged mask keeps the stored value, and a save never
persists a mask as a value. A marker that is already part of the stored text (a redacted
CLAUDE.md, say) is text, and stays.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

# A credential-shaped literal the redactor recognises. Not a real key.
SECRET = "sk-ant-api03-" + ("A" * 20) + ("B" * 20) + ("C" * 15)
MASK = "[REDACTED: credential]"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace: a folder the file explorer (and a file-backed artifact) may touch."""
    folder = tmp_path / "ws"
    folder.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(folder))
    return folder


@pytest.fixture(autouse=True)
def _quiet_sel():
    with patch("personalclaw.sel.sel", return_value=MagicMock()):
        yield


# ── Files: the editor and Save as artifact ──────────────────────────────────────────────────


def _files_app() -> web.Application:
    from personalclaw.artifacts.handlers import register_artifact_routes
    from personalclaw.dashboard.handlers import api_file_read, api_file_write

    app = web.Application()
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    app["state"] = state
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    register_artifact_routes(app)
    return app


async def _shown(client: TestClient, path: Path) -> tuple[str, dict[str, str]]:
    """The file as the editor is seeded with it, and the ``If-Match`` its save names."""
    resp = await client.get("/api/file-read", params={"path": str(path)})
    assert resp.status == 200, await resp.text()
    return await resp.text(), {"If-Match": resp.headers["ETag"]}


@pytest.mark.asyncio
async def test_saving_a_file_after_editing_another_line_keeps_its_key(ws):
    doc = ws / "deploy.md"
    doc.write_text(f"key: {SECRET}\nregion: us-east-1\n")
    async with TestClient(TestServer(_files_app())) as client:
        shown, base = await _shown(client, doc)
        assert MASK in shown and SECRET not in shown, "precondition: the editor sees the mask"
        resp = await client.post(
            "/api/file-write",
            json={"path": str(doc), "content": shown.replace("us-east-1", "eu-west-1")},
            headers=base,
        )
        assert resp.status == 200, await resp.text()
    assert doc.read_text() == f"key: {SECRET}\nregion: eu-west-1\n"


@pytest.mark.asyncio
async def test_saving_a_file_unchanged_leaves_it_byte_identical(ws):
    doc = ws / "deploy.md"
    original = f"key: {SECRET}\nregion: us-east-1\n"
    doc.write_text(original)
    async with TestClient(TestServer(_files_app())) as client:
        shown, base = await _shown(client, doc)
        resp = await client.post(
            "/api/file-write", json={"path": str(doc), "content": shown}, headers=base
        )
        assert resp.status == 200, await resp.text()
    assert doc.read_text() == original


@pytest.mark.asyncio
async def test_a_marker_that_is_part_of_the_file_stays_text(ws):
    """A CLAUDE.md imported already redacted holds the marker as its own text."""
    doc = ws / "CLAUDE.md"
    doc.write_text(f"Use the token {MASK} from the vault.\nBe brief.\n")
    async with TestClient(TestServer(_files_app())) as client:
        shown, base = await _shown(client, doc)
        resp = await client.post(
            "/api/file-write",
            json={"path": str(doc), "content": shown.replace("Be brief.", "Be very brief.")},
            headers=base,
        )
        assert resp.status == 200, await resp.text()
    assert doc.read_text() == f"Use the token {MASK} from the vault.\nBe very brief.\n"


@pytest.mark.asyncio
async def test_saving_a_file_as_an_artifact_leaves_its_key_in_the_file(ws, tmp_path):
    """Files → Save as a versioned artifact sends the editor's draft, which is the masked read,
    as the artifact's content, and the store writes that content into the file itself."""
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.native import NativeArtifactProvider

    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    doc = ws / "brief.md"
    original = f"# Brief\n\nkey: {SECRET}\n"
    doc.write_text(original)
    with patch.object(registry, "get_provider", return_value=provider):
        async with TestClient(TestServer(_files_app())) as client:
            shown, base = await _shown(client, doc)
            resp = await client.post(
                "/api/artifacts",
                json={
                    "name": "brief.md",
                    "content": shown,
                    "source": "manual",
                    "source_path": str(doc),
                    "kind": "markdown",
                },
                headers=base,
            )
            assert resp.status == 201, await resp.text()
            slug = (await resp.json())["slug"]
    assert doc.read_text() == original
    assert SECRET in (provider.get(slug).content or "")


# ── Artifacts: the editor and the agent's tools ─────────────────────────────────────────────


@pytest.fixture
def artifacts(tmp_path):
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.native import NativeArtifactProvider

    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


@pytest.mark.asyncio
async def test_the_artifact_editor_keeps_the_hidden_key_through_an_edit(artifacts):
    art = artifacts.create(
        name="Notes", content=f"key: {SECRET}\nline two\n", kind="markdown", description="d"
    )
    async with TestClient(TestServer(_files_app())) as client:
        read = await (await client.get(f"/api/artifacts/{art.slug}")).json()
        shown = read["content"]
        assert MASK in shown and SECRET not in shown
        resp = await client.patch(
            f"/api/artifacts/{art.slug}",
            json={"content": shown.replace("line two", "line 2")},
            headers={"If-Match": read["content_revision"]},
        )
        assert resp.status == 200, await resp.text()
    assert artifacts.get(art.slug).content == f"key: {SECRET}\nline 2\n"


@pytest.mark.asyncio
async def test_the_artifact_editor_keeps_a_hidden_key_in_the_description(artifacts):
    art = artifacts.create(
        name="Notes", content="body", kind="markdown", description=f"uses {SECRET}"
    )
    async with TestClient(TestServer(_files_app())) as client:
        shown = (await (await client.get(f"/api/artifacts/{art.slug}")).json())["description"]
        assert MASK in shown
        resp = await client.patch(
            f"/api/artifacts/{art.slug}", json={"description": shown + " (checked)"}
        )
        assert resp.status == 200, await resp.text()
    assert artifacts.get(art.slug).description == f"uses {SECRET} (checked)"


def test_the_agent_iterating_on_an_artifact_keeps_the_hidden_key(artifacts, monkeypatch):
    from personalclaw import mcp_artifacts

    monkeypatch.setattr(mcp_artifacts, "_resolve_session_key", lambda: "dashboard:chat-1")
    art = artifacts.create(name="Notes", content=f"key: {SECRET}\nline two\n", kind="markdown")
    shown = mcp_artifacts._call_tool_inner("artifact_get", {"slug": art.slug})
    assert MASK in shown and SECRET not in shown
    out = mcp_artifacts._call_tool_inner(
        "artifact_update", {"slug": art.slug, "content": shown.replace("line two", "line 2")}
    )
    assert "Error" not in out, out
    assert artifacts.get(art.slug).content == f"key: {SECRET}\nline 2\n"


# ── Loops: plan review → Launch ─────────────────────────────────────────────────────────────


@pytest.fixture
def loops(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    return tmp_path


def test_launching_from_plan_review_keeps_every_hidden_key(loops):
    from personalclaw.dashboard.handlers import loop_routes as H
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    command = f"python3 check.py --token {SECRET}"
    loop = store.create(
        Loop(
            id="",
            name="Audit",
            kind="goal",
            task=f"Audit the billing API with key {SECRET}.",
            plan=[{"title": f"rotate {SECRET}"}],
            kind_config={
                "goal_type": "verifiable",
                "sub_goals": [f"rotate {SECRET}", "write the report"],
                "verify_command": command,
            },
        )
    )
    view = store.get_redacted(loop.id)
    assert view is not None and MASK in view["task"] and SECRET not in json.dumps(view)
    # Exactly the body LoopPlanReview.tsx sends on Launch: the redacted fields, plus the
    # clarifications folded into the task.
    body = {
        "name": view["name"],
        "task": view["task"] + "\n\nClarifications:\n- scope → all regions",
        "plan": [{"title": p["title"]} for p in view["plan"]],
        "kind_config": {
            "goal_type": "verifiable",
            "sub_goals": view["kind_config"]["sub_goals"],
            "verify_command": view["kind_config"]["verify_command"],
        },
    }
    app = web.Application()
    app["state"] = MagicMock()
    req = make_mocked_request(
        "PUT",
        f"/api/loops/{loop.id}",
        headers={"If-Match": view["revision"]},
        match_info={"id": loop.id},
        app=app,
    )

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    resp = _run(H.api_loop_update(req))
    assert resp.status == 200, resp.text
    saved = store.get(loop.id)
    assert saved.task == (
        f"Audit the billing API with key {SECRET}.\n\nClarifications:\n- scope → all regions"
    )
    assert saved.plan[0]["title"] == f"rotate {SECRET}"
    assert saved.kind_config["sub_goals"] == [f"rotate {SECRET}", "write the report"]
    assert saved.kind_config["verify_command"] == command


# ── Schedules: rename an agent schedule ─────────────────────────────────────────────────────


@pytest.fixture
def schedules(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.handlers import triggers as T
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))

    class _Empty:
        def list_all(self):
            return []

        def load(self):
            return []

    monkeypatch.setattr(T, "_hook_store", lambda s: _Empty())
    monkeypatch.setattr(T, "_used_by_index", lambda: {})
    state = MagicMock()
    state.crons.list_jobs.return_value = []
    state._hook_store = None
    return T, state, tmp_path


def _treq(
    method: str, path: str, state: Any, *, body=None, match_info=None, query=None, headers=None
):
    app = web.Application()
    app["state"] = state
    full = path + ("?" + query if query else "")
    req = make_mocked_request(
        method, full, headers=headers or {}, match_info=match_info or {}, app=app
    )
    req["user"] = "tester"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[method-assign]
    return req


def _agent_action(prompt: str) -> dict:
    # What `api.ts` builds for an agent-mode schedule from the form's `message`.
    return {
        "provider": "invoke-agent",
        "config": {"task_template": prompt, "agent": "", "model": "", "approval_mode": ""},
    }


def test_renaming_an_agent_schedule_keeps_the_key_in_its_prompt(schedules):
    from personalclaw.triggers.store import TriggerStore

    T, state, home = schedules
    prompt = f"Summarize the day. The API key is {SECRET}."
    created = _run(
        T.api_trigger_create(
            _treq(
                "POST",
                "/api/triggers",
                state,
                body={
                    "trigger_type": "schedule",
                    "name": "Nightly digest",
                    "cron": "0 9 * * *",
                    "action": _agent_action(prompt),
                },
            )
        )
    )
    assert created.status == 200, created.text
    listed = json.loads(
        _run(T.api_triggers(_treq("GET", "/api/triggers", state, query="type=schedule"))).text
    )
    rows = listed if isinstance(listed, list) else listed.get("triggers") or listed.get("items")
    row = next(r for r in rows if r.get("name") == "Nightly digest")
    # The form's prompt field is seeded from `message`, the masked copy.
    assert MASK in row["message"] and SECRET not in row["message"]
    resp = _run(
        T.api_trigger_detail(
            _treq(
                "PUT",
                f"/api/triggers/{row['id']}",
                state,
                body={"name": "Evening digest", "action": _agent_action(row["message"])},
                match_info={"id": row["id"]},
                headers={"If-Match": row["revision"]},
            )
        )
    )
    assert resp.status == 200, resp.text
    stored = TriggerStore(base_dir=home).list_triggers()
    renamed = next(t for t in stored if t.name == "Evening digest")
    assert renamed.workflow["inline"]["config"]["task_template"] == prompt


# ── Inbox: save or send a draft ─────────────────────────────────────────────────────────────

ITEM_ID = "c1_1700000000.1"


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    from personalclaw.inbox import InboxItem, InboxState, InboxStore

    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    store = InboxStore(path=tmp_path / "inbox.json")
    store.add(
        InboxItem(
            id=ITEM_ID,
            channel="c1",
            channel_name="general",
            thread_ts=None,
            message="What's the staging key?",
            sender_id="u1",
            sender_name="Ann",
            draft=f"Here it is: {SECRET}",
            source="native",
            can_reply=True,
        )
    )
    store.save()
    state_obj = InboxState()
    monkeypatch.setattr(state_obj, "save", lambda: None, raising=False)
    monkeypatch.setattr(store, "load", lambda: None, raising=False)

    class _State:
        _inbox_svc = None

        def __init__(self) -> None:
            self._inbox_store = store
            self._inbox_state = state_obj
            self.broadcasts: list = []

        def broadcast_ws(self, event: str, payload: Any) -> None:
            self.broadcasts.append((event, payload))

        def get_session(self, key: str):
            return None

    return _State(), store


def _inbox_call(handler, st, method: str, path: str, body=None, match_info=None):
    req = make_mocked_request(method, path, match_info=match_info or {})
    req.app["state"] = st

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return _run(handler(req))


def _shown_draft(st) -> str:
    from personalclaw.dashboard import handlers_inbox as h

    resp = _inbox_call(h.api_inbox_list, st, "GET", "/api/inbox")
    items = json.loads(resp.text)
    items = items if isinstance(items, list) else items.get("items", [])
    return next(i for i in items if i["id"] == ITEM_ID)["draft"]


def test_saving_an_inbox_draft_keeps_the_hidden_key(inbox):
    from personalclaw.dashboard import handlers_inbox as h

    st, store = inbox
    shown = _shown_draft(st)
    assert MASK in shown and SECRET not in shown
    resp = _inbox_call(
        h.api_inbox_update,
        st,
        "PUT",
        f"/api/inbox/{ITEM_ID}",
        body={"draft": shown + " Rotate it after."},
        match_info={"id": ITEM_ID},
    )
    assert resp.status == 200, resp.text
    assert store.items[ITEM_ID].draft == f"Here it is: {SECRET} Rotate it after."


def test_sending_an_inbox_reply_records_the_draft_with_its_hidden_key(inbox):
    from personalclaw.dashboard import handlers_inbox as h

    st, store = inbox
    shown = _shown_draft(st)
    resp = _inbox_call(
        h.api_inbox_send, st, "POST", "/api/inbox/send", body={"id": ITEM_ID, "text": shown}
    )
    assert resp.status == 200, resp.text
    assert store.items[ITEM_ID].draft == f"Here it is: {SECRET}"


# ── The agent's knowledge tools ─────────────────────────────────────────────────────────────


def test_the_agent_editing_a_knowledge_item_keeps_its_hidden_key(tmp_path, monkeypatch):
    import personalclaw.knowledge as knowledge_pkg
    from personalclaw.agents.native import builtin_tools
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.knowledge.store import KnowledgeStore
    from personalclaw.security import redact_for_display

    store = KnowledgeStore(str(tmp_path / "k.db"))
    monkeypatch.setattr(knowledge_pkg, "get_knowledge_store", lambda *a, **k: store)
    monkeypatch.setattr(builtin_tools, "_enrich_in_background", lambda *a, **k: None)
    body = f"key: {SECRET}\nregion: us-east-1"
    item_id = store.create_typed_item(item_type="note", title="Deploy notes", content=body)
    tools = NativeBuiltinToolProvider()
    got = _run(tools.invoke("knowledge_get", {"id": item_id}))
    assert got.success and MASK in got.output and SECRET not in got.output
    shown_body = redact_for_display(body)
    assert shown_body in got.output, "precondition: the agent sees exactly this body"
    upd = _run(
        tools.invoke(
            "knowledge_update",
            {"id": item_id, "content": shown_body.replace("us-east-1", "eu-west-1")},
        )
    )
    assert upd.success, upd.error
    assert store.get_item(item_id)["content"] == f"key: {SECRET}\nregion: eu-west-1"


# ── Memory: a fact written back as it was listed ────────────────────────────────────────────


def test_writing_back_a_listed_fact_keeps_its_hidden_key(tmp_path, monkeypatch):
    from personalclaw.dashboard.handlers import memory as M

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    row = {"key": "staging.api_key", "value_json": json.dumps(f"the key is {SECRET}")}
    vs = MagicMock()
    vs.get_all_semantic.return_value = [row]
    vs.get_semantic.return_value = row
    vs.set_semantic.return_value = None
    state = MagicMock()
    state.context_builder.memory.vector_store = vs
    state._restricted_keys = set()

    listed = MagicMock()
    listed.app = {"state": state}
    entries = json.loads(_run(M.api_memory_semantic(listed)).body)["entries"]
    shown = json.loads(entries[0]["value_json"])
    assert MASK in shown and SECRET not in shown

    app = web.Application()
    app["state"] = state
    req = make_mocked_request("PUT", "/api/memory/semantic", app=app)

    async def _json():
        return {"key": "staging.api_key", "value": shown + " (staging only)"}

    req.json = _json  # type: ignore[method-assign]
    with patch.object(M, "_is_restricted_session", return_value=False):
        resp = _run(M.api_memory_semantic_write(req))
    assert resp.status == 200, resp.text
    written = vs.set_semantic.call_args
    assert written is not None and written.args[1] == f"the key is {SECRET} (staging only)"


# ── The credential store: the display mask is never a value ────────────────────────────────

TOKEN = "ghp_fixtureMaskedEditToken00112233445566"


@pytest.fixture
def mcp_home(monkeypatch):
    from personalclaw.config import loader as config_loader

    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    home = config_loader.config_dir()
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    return home


def _mcp(method: str, name: str, body: dict | None = None, headers: dict | None = None):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request(
        method, f"/api/mcp/servers/{name}", headers=headers or {}, match_info={"name": name}
    )

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return _run(mcp_mod.api_mcp_server_detail(req))


def test_an_mcp_edit_that_sends_the_mask_as_a_value_keeps_the_token(mcp_home):
    from personalclaw.apps.secret_fields import SECRET_MASK
    from personalclaw.config.credentials import get_credential
    from personalclaw.config.secret_refs import ref_key

    added = _mcp("PUT", "gh", {"command": "echo", "env": {"GITHUB_TOKEN": TOKEN}})
    assert added.status == 200, added.text
    spec = json.loads((mcp_home / "mcp.json").read_text())["mcpServers"]["gh"]
    key = ref_key(spec["env"]["GITHUB_TOKEN"])
    assert key and get_credential(key) == TOKEN
    # A client that echoes what a masked form showed, instead of listing it in keepEnv.
    read = json.loads(_mcp("GET", "gh").text)
    resp = _mcp(
        "PUT",
        "gh",
        {"command": "echo", "args": ["-v"], "env": {"GITHUB_TOKEN": SECRET_MASK}},
        headers={"If-Match": read["revision"]},
    )
    assert resp.status == 200, resp.text
    spec = json.loads((mcp_home / "mcp.json").read_text())["mcpServers"]["gh"]
    assert spec["args"] == ["-v"]
    assert get_credential(ref_key(spec["env"]["GITHUB_TOKEN"])) == TOKEN


def test_an_mcp_value_that_is_only_the_mask_is_refused_when_nothing_is_stored(mcp_home):
    from personalclaw.apps.secret_fields import SECRET_MASK
    from personalclaw.config.credentials import credential_names

    before = set(credential_names())
    resp = _mcp("PUT", "fresh", {"command": "echo", "env": {"API_TOKEN": SECRET_MASK}})
    assert resp.status == 400, resp.text
    assert "API_TOKEN" in resp.text
    assert set(credential_names()) == before


def test_the_credential_store_never_stores_the_display_mask(monkeypatch):
    from personalclaw.apps.secret_fields import SECRET_MASK
    from personalclaw.config.credentials import get_credential, save_credential
    from personalclaw.config.secret_refs import make_ref, provider_owner, store

    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    owner = provider_owner("fixture-provider")
    key = owner.key("api_key")
    save_credential(key, TOKEN)
    previous = {"api_key": make_ref(key)}
    kept = store({"api_key": SECRET_MASK}, owner=owner, declared=["api_key"], previous=previous)
    assert kept["api_key"] == make_ref(key)
    assert get_credential(key) == TOKEN
    with pytest.raises(ValueError, match="api_key"):
        store({"api_key": SECRET_MASK}, owner=provider_owner("other"), declared=["api_key"])


# ── the one inverse every save path above calls ─────────────────────────────────────────────

OTHER = "sk-ant-api03-" + ("D" * 20) + ("E" * 20) + ("F" * 15)


def test_a_structured_value_is_restored_key_by_key():
    from personalclaw.security import keep_masked_values, redact_for_display

    stored = {"config": {"task_template": f"use {SECRET}", "agent": ""}, "provider": "invoke-agent"}
    shown = {"config": {"task_template": redact_for_display(f"use {SECRET}"), "agent": ""}}
    assert keep_masked_values(shown, stored) == {
        "config": {"task_template": f"use {SECRET}", "agent": ""}
    }


def test_list_items_moved_without_an_edit_keep_their_own_values():
    from personalclaw.security import keep_masked_values, redact_for_display

    stored = [f"rotate {SECRET}", "write the report", f"revoke {OTHER}"]
    moved = [redact_for_display(s) for s in (stored[2], stored[1], stored[0])]
    assert keep_masked_values(moved, stored) == [
        f"revoke {OTHER}",
        "write the report",
        f"rotate {SECRET}",
    ]


def test_items_that_show_alike_each_keep_one_value_none_lost_or_doubled():
    """Two items that show the same are the same to the client, so which lands where cannot be
    known (the kind in the marker is what tells values apart). Neither is lost or written twice."""
    from personalclaw.security import keep_masked_values, redact_for_display

    stored = [f"rotate {SECRET}", "write the report", f"rotate {OTHER}"]
    shown = [redact_for_display(s) for s in stored]
    assert shown[0] == shown[2], "precondition: two different keys show alike"
    kept = keep_masked_values([shown[1], shown[2], shown[0]], stored)
    assert kept[0] == "write the report"
    assert sorted(kept[1:]) == sorted([f"rotate {SECRET}", f"rotate {OTHER}"])


def test_a_mask_that_cannot_be_placed_is_refused_not_stored(monkeypatch):
    import personalclaw.security as security

    monkeypatch.setattr(security, "restore_masked_spans", lambda submitted, stored: None)
    with pytest.raises(security.MaskConflict, match="cannot be recovered"):
        security.keep_masked_spans(f"rewritten {MASK}", f"key: {SECRET}")


@pytest.mark.asyncio
async def test_a_file_save_whose_mask_cannot_be_placed_is_refused_and_the_file_kept(
    ws, monkeypatch
):
    import personalclaw.security as security

    doc = ws / "deploy.md"
    original = f"key: {SECRET}\nregion: us-east-1\n"
    doc.write_text(original)
    async with TestClient(TestServer(_files_app())) as client:
        _, base = await _shown(client, doc)
        monkeypatch.setattr(security, "restore_masked_spans", lambda submitted, stored: None)
        resp = await client.post(
            "/api/file-write",
            json={"path": str(doc), "content": f"key: {MASK}\nregion: eu\n"},
            headers=base,
        )
        assert resp.status == 409
        assert "cannot be recovered" in (await resp.json())["error"]
    assert doc.read_text() == original
