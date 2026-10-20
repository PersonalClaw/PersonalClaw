"""The four PATCH twins apply the create door's shared ``name`` rule (#3168)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.artifacts.folders import ArtifactFolderStore
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.knowledge.store import KnowledgeStore


def _request(body, *, match_info, state):
    request = MagicMock()
    request.app = {"state": state}
    request.match_info = match_info
    request.json = AsyncMock(return_value=body)
    request.get = lambda key, default=None: "dashboard" if key == "user" else default
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", [{"a": "b"}, ["nested"], 42, True], ids=repr)
async def test_chat_folder_update_rejects_non_string_name_without_mutating(bad_name, monkeypatch):
    from personalclaw.dashboard import chat_folders as handlers

    folder = {"id": "f1", "name": "Keep", "order": 0, "collapsed": False}
    state = SimpleNamespace(
        _folders=[folder],
        save_folders=MagicMock(),
        push_sessions_update=MagicMock(),
    )
    monkeypatch.setattr(handlers, "sel", lambda: MagicMock())

    response = await handlers.api_chat_folder_update(
        _request({"name": bad_name}, match_info={"id": "f1"}, state=state)
    )

    assert response.status == 400
    assert folder["name"] == "Keep"
    state.save_folders.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", [{"a": "b"}, ["nested"], 42, True], ids=repr)
async def test_artifact_folder_update_rejects_non_string_name_without_mutating(
    bad_name, tmp_path, monkeypatch
):
    from personalclaw.artifacts import handlers

    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    folders = ArtifactFolderStore(provider.root)
    folder = folders.create("Keep")
    monkeypatch.setattr(handlers, "_provider", lambda _request: provider)
    monkeypatch.setattr(handlers, "_is_restricted_session", lambda _state, _request: False)
    monkeypatch.setattr(handlers, "_audit", lambda *_args: None)

    response = await handlers.api_artifact_folder_update(
        _request(
            {"name": bad_name},
            match_info={"id": folder.id},
            state=SimpleNamespace(),
        )
    )

    assert response.status == 400
    assert folders.get(folder.id).name == "Keep"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", [{"a": "b"}, ["nested"], 42, True], ids=repr)
async def test_chat_tag_update_rejects_non_string_name_without_mutating(bad_name, monkeypatch):
    from personalclaw.dashboard import chat_tags as handlers

    tag = {
        "id": "t1",
        "name": "Keep",
        "color": "#6b7280",
        "order": 0,
        "status": False,
    }
    state = SimpleNamespace(
        _tags=[tag],
        save_tags=MagicMock(),
        push_sessions_update=MagicMock(),
    )
    monkeypatch.setattr(handlers, "sel", lambda: MagicMock())

    response = await handlers.api_chat_tag_update(
        _request({"name": bad_name}, match_info={"id": "t1"}, state=state)
    )

    assert response.status == 400
    assert tag["name"] == "Keep"
    state.save_tags.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", [{"a": "b"}, ["nested"], 42, True], ids=repr)
async def test_knowledge_collection_update_rejects_non_string_name_without_mutating(
    bad_name, tmp_path
):
    from personalclaw.dashboard.handlers import knowledge as handlers

    store = KnowledgeStore(tmp_path / "knowledge.db")
    collection_id = store.create_collection(name="Keep")
    state = SimpleNamespace(knowledge_store=store)

    response = await handlers.update_collection(
        _request(
            {"name": bad_name},
            match_info={"id": collection_id},
            state=state,
        )
    )

    assert response.status == 400
    assert store.get_collection(collection_id)["name"] == "Keep"
