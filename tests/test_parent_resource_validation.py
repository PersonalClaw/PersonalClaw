"""A request scoped to a parent resolves that parent first (#2940 reads, #2995 the write).

Two issues, one rule. A sub-resource READ under a nonexistent parent answered ``200`` with a
well-formed empty body (#2940), and a WRITE under one answered ``{"ok": true}`` and left a file
behind (#2995). Same defect: the door never asked whether the thing it was scoped to exists.

🔴 WHY SOME TESTS ASSERT A PAIR, not just a status. #2940's own proof is a diff of two
*responses*, not a status: ``/api/workflows/audit-sweep/ledger`` (a real template with no runs)
and ``/api/workflows/qa23-does-not-exist/ledger`` returned byte-identical bodies apart from the
echoed name, so an agent could not tell a real empty parent from a typo. Where a real parent can
be built cheaply the test drives BOTH ids and asserts they now differ — a bare ``== 404`` rail
would still pass if the fix had broken the real parent's read into a 404 as well.

Every handler is driven DIRECTLY, with no ``request_boundary`` middleware in the stack. That is
deliberate: a refusal that only materialises because middleware is installed is not a property of
the door, and it would become a bare 500 in an embedded mount that never installed it.

The refusal ENVELOPE is per family, on purpose — this repo has two wire shapes and #2940's own
"Suggested fix" says to 404 "with the family's existing code". So the workflows family answers its
nested ``service_code`` shape, the tasks/apps/artifacts/triggers/knowledge doors answer the flat
``{"error": ...}`` their siblings already answer, and the session doors answer the registered
``session_not_found``. No new wire code is minted here; every code asserted below is already in
:data:`personalclaw.http_errors.HTTP_ERROR_CODES`.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import streams, web
from aiohttp.test_utils import make_mocked_request

from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.knowledge.store import KnowledgeStore

GHOST = "qa-parent-does-not-exist-2940"


def _request(method: str, path: str, *, match_info: dict[str, str], state=None, **kw):
    app = web.Application()
    if state is not None:
        app["state"] = state
    return make_mocked_request(method, path, match_info=match_info, app=app, **kw)


def _body(response) -> dict:
    return json.loads(response.body.decode())


def _json_payload(obj: dict[str, Any]):
    """A mocked request payload carrying *obj* as a JSON body.

    ``make_mocked_request`` needs a real stream for ``await request.json()``; the shared body
    reader reads the bytes (never ``can_read_body``, which is a transport flag that disagrees with
    its own payload under mocks), so a plain byte stream is enough.
    """
    stream = streams.StreamReader(MagicMock(), 2**16, loop=asyncio.get_event_loop())
    stream.feed_data(json.dumps(obj).encode())
    stream.feed_eof()
    return stream


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "path"),
    [
        ("api_def_ledger", "/api/workflows/qa-parent-does-not-exist-2940/ledger"),
        ("api_template_trajectory", "/api/workflows/qa-parent-does-not-exist-2940/trajectory"),
        (
            "api_def_version_diff",
            "/api/workflows/qa-parent-does-not-exist-2940/versions/diff?a=1&b=2",
        ),
    ],
)
async def test_workflow_subresource_reads_404_for_unknown_definition(handler_name, path):
    from personalclaw.workflows import handlers

    response = await getattr(handlers, handler_name)(
        _request(
            "GET",
            path,
            match_info={"name": "qa-parent-does-not-exist-2940"},
        )
    )

    assert response.status == 404
    assert _body(response)["error"]["service_code"] == "WF_DEF_NOT_FOUND"


@pytest.mark.asyncio
async def test_task_comments_read_404s_before_loading_comments_for_unknown_task(monkeypatch):
    from personalclaw.tasks import handlers

    monkeypatch.setattr(handlers.registry, "get_task", AsyncMock(return_value=None))
    get_comments = AsyncMock(side_effect=AssertionError("comments read must not run"))
    monkeypatch.setattr(handlers.registry, "get_comments", get_comments)

    response = await handlers.api_tasks_comments_get(
        _request(
            "GET",
            "/api/tasks/qa-parent-does-not-exist-2940/comments",
            match_info={"task_id": "qa-parent-does-not-exist-2940"},
        )
    )

    assert response.status == 404
    assert _body(response) == {"error": "task not found"}
    get_comments.assert_not_awaited()


@pytest.mark.asyncio
async def test_uninstall_preview_404s_before_describing_unknown_app(monkeypatch):
    from personalclaw.apps import app_manager
    from personalclaw.dashboard.handlers import apps as handlers

    monkeypatch.setattr(app_manager, "_manifest_of", lambda _name: None)
    preview = MagicMock(side_effect=AssertionError("preview must not run"))
    monkeypatch.setattr(app_manager, "preview_uninstall", preview)

    response = await handlers.api_app_uninstall_preview(
        _request(
            "GET",
            "/api/apps/qa-parent-does-not-exist-2940/uninstall-preview",
            match_info={"name": "qa-parent-does-not-exist-2940"},
        )
    )

    assert response.status == 404
    preview.assert_not_called()


@pytest.mark.asyncio
async def test_artifact_versions_read_distinguishes_unknown_from_unversioned_parent(
    tmp_path, monkeypatch
):
    from personalclaw.artifacts import handlers

    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    real = provider.create(name="No snapshots yet", content="body")
    monkeypatch.setattr(handlers, "_provider", lambda _request: provider)

    missing = await handlers.api_artifact_versions(
        _request(
            "GET",
            "/api/artifacts/qa-parent-does-not-exist-2940/versions",
            match_info={"slug": "qa-parent-does-not-exist-2940"},
            state=SimpleNamespace(),
        )
    )
    existing = await handlers.api_artifact_versions(
        _request(
            "GET",
            f"/api/artifacts/{real.slug}/versions",
            match_info={"slug": real.slug},
            state=SimpleNamespace(),
        )
    )

    assert missing.status == 404
    assert existing.status == 200
    assert _body(existing)["versions"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger_id",
    [
        "schedule:qa-parent-does-not-exist-2940",
        "lifecycle:qa-parent-does-not-exist-2940",
        "event:qa-parent-does-not-exist-2940",
        "store:file:qa-parent-does-not-exist-2940",
    ],
)
async def test_trigger_history_404s_for_unknown_trigger_kind(trigger_id, monkeypatch):
    from personalclaw.dashboard.handlers import triggers as handlers

    empty_store = SimpleNamespace(get=lambda _key: None, load=lambda: [])
    monkeypatch.setattr(handlers, "_trigger_store", lambda: empty_store)
    monkeypatch.setattr(handlers, "_event_store", lambda: empty_store)
    monkeypatch.setattr(handlers, "_hook_store", lambda _state: empty_store)

    response = await handlers.api_trigger_history(
        _request(
            "GET",
            f"/api/triggers/{trigger_id}/history",
            match_info={"id": trigger_id},
            state=SimpleNamespace(),
        )
    )

    assert response.status == 404
    assert _body(response) == {"error": "not found"}


@pytest.mark.asyncio
async def test_item_relations_read_distinguishes_unknown_from_unrelated_parent(
    tmp_path, monkeypatch
):
    from personalclaw.dashboard.handlers import knowledge as handlers

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    store = KnowledgeStore(tmp_path / "knowledge.db")
    item_id = store.create_typed_item(item_type="note", title="Real", content="body")
    state = SimpleNamespace(knowledge_store=store)

    missing = await handlers.list_item_relations(
        _request(
            "GET",
            "/api/knowledge/items/qa-parent-does-not-exist-2940/relations",
            match_info={"id": "qa-parent-does-not-exist-2940"},
            state=state,
        )
    )
    existing = await handlers.list_item_relations(
        _request(
            "GET",
            f"/api/knowledge/items/{item_id}/relations",
            match_info={"id": item_id},
            state=state,
        )
    )

    assert missing.status == 404
    assert existing.status == 200
    assert _body(existing) == {"outbound": [], "inbound": []}


def _empty_session_state():
    conversation_log = MagicMock()
    conversation_log.has_session.return_value = False
    return SimpleNamespace(conversation_log=conversation_log, _sessions={})


@pytest.mark.asyncio
async def test_session_agents_read_404s_for_unknown_session():
    from personalclaw.dashboard.handlers import core as handlers

    response = await handlers.api_session_agents_list(
        _request(
            "GET",
            "/api/sessions/qa-parent-does-not-exist-2940/agents",
            match_info={"id": "qa-parent-does-not-exist-2940"},
            state=_empty_session_state(),
        )
    )

    assert response.status == 404
    assert _body(response)["error"]["code"] == "session_not_found"


@pytest.mark.asyncio
async def test_autonudge_read_404s_for_unknown_session(monkeypatch):
    from personalclaw.dashboard.handlers import autonudge as handlers

    service = MagicMock()
    monkeypatch.setattr(handlers, "_autonudge_get", lambda: service)
    response = await handlers.api_autonudge_get(
        _request(
            "GET",
            "/api/autonudge/session/qa-parent-does-not-exist-2940",
            match_info={"session_name": "qa-parent-does-not-exist-2940"},
            state=_empty_session_state(),
        )
    )

    assert response.status == 404
    assert _body(response)["error"]["code"] == "session_not_found"
    service.get_by_session.assert_not_called()


# ── #2995 — the WRITE side of the same hole ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_metadata_put_refuses_a_ghost_agent_and_writes_nothing():
    """#2995's headline. ``PUT /api/agent-metadata/{name}`` checked auth and the body shape, then
    saved routing metadata for an agent that does not exist — answering ``{"ok": true}`` and
    leaving an ``.md`` file that is in the durability inventory (so it rides into snapshots and
    sync) and that nothing lists or reaps.

    The status is the cheap half. The load-bearing assertion is that the WRITE did not happen: a
    404 that still wrote would be the same bug with a politer response.
    """
    from personalclaw import agent_metadata
    from personalclaw.dashboard.handlers import agents as H

    cfg = MagicMock()
    cfg.agents = {"reviewer": MagicMock()}

    request = _request(
        "PUT",
        f"/api/agent-metadata/{GHOST}",
        match_info={"name": GHOST},
        payload=_json_payload({"content": "ghost routing rules"}),
    )
    # The authenticated caller the auth middleware would have stamped. Without it the door
    # answers 401 and never reaches the check under test — which would make this rail pass for
    # the wrong reason.
    request["user"] = "tester"

    with (
        patch.object(H.AppConfig, "load", return_value=cfg),
        patch.object(H, "_sel", return_value=MagicMock()),
    ):
        response = await H.api_agent_metadata_put(request)

    assert response.status == 404, f"a ghost agent was told its metadata saved: {_body(response)}"
    assert _body(response)["error"]["code"] == "not_found"
    assert agent_metadata.load(GHOST) == ""
    assert not (agent_metadata.metadata_dir() / f"{GHOST}.md").exists()


@pytest.mark.asyncio
async def test_agent_metadata_put_still_saves_for_a_real_agent():
    """The falsification for the rail above: the gate must admit the door's real traffic.

    The FE editor only ever PUTs a name it read from the agent list, and ``orchestrator_skill``
    reads these notes back by the literal ``cfg.agents`` key — so exact config membership is the
    predicate the READER already uses, not a new one invented here. A case-insensitive resolve
    would save a file under a key no reader looks up, which is the lost write this refusal exists
    to stop.
    """
    from personalclaw import agent_metadata
    from personalclaw.dashboard.handlers import agents as H

    cfg = MagicMock()
    cfg.agents = {"reviewer": MagicMock()}

    request = _request(
        "PUT",
        "/api/agent-metadata/reviewer",
        match_info={"name": "reviewer"},
        payload=_json_payload({"content": "prefers small diffs"}),
    )
    request["user"] = "tester"

    with (
        patch.object(H.AppConfig, "load", return_value=cfg),
        patch.object(H, "_sel", return_value=MagicMock()),
    ):
        response = await H.api_agent_metadata_put(request)

    assert response.status == 200
    assert agent_metadata.load("reviewer") == "prefers small diffs"


# ── #2940 residuals discovered by the all-src GET census ────────────────────────────────


@pytest.mark.asyncio
async def test_memory_backlinks_distinguish_unknown_from_unlinked_entity(monkeypatch):
    from personalclaw.dashboard.handlers import memory as handlers

    service = MagicMock()
    service.graph_entities.return_value = [{"id": "real-entity"}]
    service.graph_backlinks.return_value = []
    monkeypatch.setattr(handlers, "_get_service", lambda _state: service)

    missing = await handlers.api_memory_entity_backlinks(
        _request(
            "GET",
            f"/api/memory/entities/{GHOST}/backlinks",
            match_info={"entity_id": GHOST},
            state=SimpleNamespace(),
        )
    )
    existing = await handlers.api_memory_entity_backlinks(
        _request(
            "GET",
            "/api/memory/entities/real-entity/backlinks",
            match_info={"entity_id": "real-entity"},
            state=SimpleNamespace(),
        )
    )

    assert missing.status == 404
    assert _body(missing) == {"error": "no such entity"}
    assert existing.status == 200
    assert _body(existing) == {"links": []}
    service.graph_backlinks.assert_called_once_with("real-entity")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "suffix", "empty_body"),
    [
        ("get_entity_related", "related", {"related": []}),
        ("get_entity_items", "items", []),
    ],
)
async def test_knowledge_entity_reads_distinguish_unknown_from_unlinked_entity(
    tmp_path, monkeypatch, handler_name, suffix, empty_body
):
    from personalclaw.dashboard.handlers import knowledge as handlers

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.add_entity("Real Empty Entity", "concept")
    state = SimpleNamespace(knowledge_store=store)
    handler = getattr(handlers, handler_name)

    missing = await handler(
        _request(
            "GET",
            f"/api/knowledge/entities/by-name/{GHOST}/{suffix}",
            match_info={"name": GHOST},
            state=state,
        )
    )
    existing = await handler(
        _request(
            "GET",
            f"/api/knowledge/entities/by-name/Real%20Empty%20Entity/{suffix}",
            match_info={"name": "Real Empty Entity"},
            state=state,
        )
    )

    assert missing.status == 404
    assert _body(missing) == {"error": "entity not found"}
    assert existing.status == 200
    assert _body(existing) == empty_body


@pytest.mark.asyncio
async def test_ephemeral_skills_distinguish_unknown_from_session_without_drafts(monkeypatch):
    from personalclaw.dashboard.handlers import skills as handlers
    from personalclaw.skills import ephemeral

    state = _empty_session_state()
    list_drafts = MagicMock(return_value=[])
    monkeypatch.setattr(ephemeral, "list_drafts", list_drafts)

    missing = await handlers.api_ephemeral_skills_list(
        _request(
            "GET",
            f"/api/skills/ephemeral/{GHOST}",
            match_info={"session": GHOST},
            state=state,
        )
    )
    state.conversation_log.has_session.return_value = True
    existing = await handlers.api_ephemeral_skills_list(
        _request(
            "GET",
            "/api/skills/ephemeral/real-empty-session",
            match_info={"session": "real-empty-session"},
            state=state,
        )
    )

    assert missing.status == 404
    assert _body(missing)["error"]["code"] == "session_not_found"
    assert existing.status == 200
    assert _body(existing) == {"drafts": []}
    list_drafts.assert_called_once_with("real-empty-session")


@pytest.mark.asyncio
async def test_feedback_target_rejects_unknown_kind_but_keeps_unknown_id_hydration(monkeypatch):
    from personalclaw import feedback as fb
    from personalclaw.dashboard.handlers import feedback as handlers

    monkeypatch.setattr(handlers, "_enabled", lambda: True)
    current_verdict = MagicMock(return_value=None)
    monkeypatch.setattr(fb, "current_verdict", current_verdict)

    bad_kind = await handlers.api_feedback_target(
        _request(
            "GET",
            f"/api/feedback/target/not-a-kind/{GHOST}",
            match_info={"kind": "not-a-kind", "id": GHOST},
        )
    )
    valid_kind = fb.TARGET_KINDS[0]
    unknown_id = await handlers.api_feedback_target(
        _request(
            "GET",
            f"/api/feedback/target/{valid_kind}/{GHOST}",
            match_info={"kind": valid_kind, "id": GHOST},
        )
    )

    assert bad_kind.status == 400
    assert _body(bad_kind)["error"]["code"] == "bad_request"
    assert unknown_id.status == 200
    assert _body(unknown_id) == {"verdict": None}
    current_verdict.assert_called_once_with(valid_kind, GHOST)
