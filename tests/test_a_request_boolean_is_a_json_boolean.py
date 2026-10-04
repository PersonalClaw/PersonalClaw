"""A boolean in a request body is the JSON ``true`` or ``false``, or the request is refused.

``bool("false")`` is True. A door that read its switch or its consent by truthiness turned on
what its caller turned off: the one-link pack import read ``"consent": "false"`` as consent and
installed a pack the scanner had flagged for review, a trigger sent ``"enabled": "false"`` was
switched on, and a loop created with ``"autopilot": "false"`` drove itself. Every door now reads
its booleans through ``request_validation`` (``bool_field`` / ``require_bool`` /
``optional_bool``): a real boolean is itself, an omitted field takes the door's own safe default,
and anything else is a 400 ``field_not_a_boolean`` that names the field and changes nothing.

``tests/test_request_boolean_census.py`` holds every door to it; this file drives the readers
and the doors the defect reached.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.request_boundary import request_boundary_middleware
from personalclaw.request_validation import (
    MISSING,
    RequestValidationError,
    bool_field,
    optional_bool,
    require_bool,
)

#: Every value a client might send for a boolean that is not one. The text spellings are the
#: point (``"false"`` is truthy); a number, a list and an object are a confused caller too.
NOT_BOOLEANS: list[Any] = ["false", "true", "no", "0", "", "off", 0, 1, [], {}, ["false"]]


# ── the readers ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [True, False])
def test_a_json_boolean_is_itself_in_every_reader(value):
    body = {"flag": value}
    assert bool_field(body, "flag", default=not value) is value
    assert require_bool(body, "flag") is value
    assert optional_bool(body, "flag") is value


@pytest.mark.parametrize("value", NOT_BOOLEANS, ids=repr)
def test_anything_else_is_refused_naming_the_field(value):
    body = {"flag": value}
    for read in (
        lambda: bool_field(body, "flag", default=False),
        lambda: require_bool(body, "flag"),
        lambda: optional_bool(body, "flag"),
    ):
        with pytest.raises(RequestValidationError) as refused:
            read()
        assert refused.value.code == "field_not_a_boolean"
        assert refused.value.status == 400
        assert "flag" in refused.value.message


@pytest.mark.parametrize("default", [True, False])
def test_an_omitted_field_takes_the_door_default(default):
    assert bool_field({}, "flag", default=default) is default


@pytest.mark.parametrize("default", [True, False])
def test_a_null_is_refused_where_the_door_has_a_default(default):
    """``bool(None)`` read a null as no where an omitted field read as yes: not one request, so a
    switch with a default of its own refuses a null rather than guess which was meant."""
    with pytest.raises(RequestValidationError) as refused:
        bool_field({"flag": None}, "flag", default=default)
    assert refused.value.code == "field_not_a_boolean"
    assert "null" in refused.value.message


def test_a_null_is_omitted_where_the_door_decides_nothing():
    assert bool_field({}, "flag", default=None) is None
    assert bool_field({"flag": None}, "flag", default=None) is None


def test_a_required_boolean_must_be_sent():
    with pytest.raises(RequestValidationError) as refused:
        require_bool({}, "flag")
    assert refused.value.code == "field_required"
    assert "flag" in refused.value.message
    with pytest.raises(RequestValidationError) as refused:
        require_bool({"flag": None}, "flag")
    assert refused.value.code == "field_not_a_boolean"


def test_an_update_door_leaves_an_omitted_switch_alone_and_refuses_a_null():
    assert optional_bool({}, "flag") is MISSING
    with pytest.raises(RequestValidationError) as refused:
        optional_bool({"flag": None}, "flag")
    assert refused.value.code == "field_not_a_boolean"


# ── driving a door through the request boundary ──────────────────────────────


async def _send(
    handler,
    path: str,
    payload: dict[str, Any],
    *,
    route: str | None = None,
    method: str = "POST",
    state: Any = None,
) -> tuple[int, dict[str, Any]]:
    """Drive *handler* through the request boundary every ``/api`` route runs behind."""
    app = web.Application(middlewares=[request_boundary_middleware()])
    app["state"] = state if state is not None else SimpleNamespace()
    app.router.add_route(method, route or path, handler)
    async with TestClient(TestServer(app)) as client:
        resp = await client.request(method, path, json=payload)
        return resp.status, await resp.json()


def _refused(status: int, payload: dict[str, Any], field: str) -> bool:
    error = payload.get("error") or {}
    return (
        status == 400
        and isinstance(error, dict)
        and error.get("code") == "field_not_a_boolean"
        and field in str(error.get("message", ""))
    )


# ── the one-link pack import: consent ────────────────────────────────────────


@pytest.fixture
def flagged_one_link(tmp_path, monkeypatch):
    """A one-link document for a pack whose one skill the scanner flags for review.

    The skill is ordinary; the scanner is stubbed to answer WARNING for it, the verdict that
    makes the import ask for consent. Returns ``(document, importer_home)``.
    """
    from personalclaw import supply_chain
    from personalclaw.packs import onelink
    from personalclaw.packs.build import build_pack

    author = tmp_path / "author"
    skill = author / "skills" / "weekly-notes"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: weekly-notes\ndescription: Summarise the week's notes\n---\n# Notes\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(author))
    archive = build_pack(
        ["skill:weekly-notes"], name="notes", version="1.0.0", out_path=tmp_path / "notes.pclaw"
    )
    document = onelink.to_onelink(Path(archive))

    def _flagged(self, staged_dir, tier=supply_chain.TrustTier.COMMUNITY):
        return supply_chain.ScanReport(verdict=supply_chain.Verdict.WARNING, tier=tier)

    monkeypatch.setattr(supply_chain.SkillScanner, "scan", _flagged)
    importer = tmp_path / "importer"
    importer.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(importer))
    return document, importer


def _installed(home: Path) -> bool:
    return (home / "skills" / "weekly-notes").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", NOT_BOOLEANS, ids=repr)
async def test_one_link_consent_sent_as_anything_but_true_installs_nothing(
    flagged_one_link, consent
):
    from personalclaw.dashboard.handlers.packs import api_pack_one_link

    document, home = flagged_one_link
    status, payload = await _send(
        api_pack_one_link, "/api/packs/one-link", {"link": document, "consent": consent}
    )
    assert _refused(status, payload, "consent"), (status, payload)
    assert not _installed(home)


@pytest.mark.asyncio
async def test_one_link_consent_false_or_omitted_is_asked_for(flagged_one_link):
    from personalclaw.dashboard.handlers.packs import api_pack_one_link

    document, home = flagged_one_link
    for body in ({"link": document, "consent": False}, {"link": document}):
        status, payload = await _send(api_pack_one_link, "/api/packs/one-link", body)
        assert status == 409, payload
        assert payload["error"]["code"] == "pack_refused_needs_consent"
        assert not _installed(home)


@pytest.mark.asyncio
async def test_one_link_consent_true_installs_the_flagged_pack(flagged_one_link):
    from personalclaw.dashboard.handlers.packs import api_pack_one_link

    document, home = flagged_one_link
    status, payload = await _send(
        api_pack_one_link, "/api/packs/one-link", {"link": document, "consent": True}
    )
    assert status == 200, payload
    assert _installed(home)


# ── doors that refuse before anything is read or written ─────────────────────

#: ``(handler, route, path, body, field, method)``: each body sends its boolean as the text
#: ``"false"`` or ``"true"``, the spellings truthiness read as a yes.
_DOORS = [
    (
        "personalclaw.dashboard.handlers.mcp:api_mcp_toggle",
        "/api/mcp/toggle",
        "/api/mcp/toggle",
        {"name": "notes", "enabled": "false"},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.mcp:api_mcp_toggle_tool",
        "/api/mcp/toggle-tool",
        "/api/mcp/toggle-tool",
        {"server": "notes", "tool": "search", "enabled": "false"},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.mcp:api_mcp_toggle_all",
        "/api/mcp/toggle-all",
        "/api/mcp/toggle-all",
        {"enabled": "false"},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.tools:api_tools_toggle",
        "/api/tools/toggle",
        "/api/tools/toggle",
        {"provider": "personalclaw-core", "name": "artifact_list", "enabled": "false"},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.tools:api_providers_toggle",
        "/api/tools/provider-toggle",
        "/api/tools/provider-toggle",
        {"provider": "personalclaw-artifacts", "enabled": "false"},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.tools:api_tool_invoke",
        "/api/tools/invoke",
        "/api/tools/invoke",
        {"tool": "artifact_list", "arguments": {}, "dry_run": "true"},
        "dry_run",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.messaging:api_send_message",
        "/api/send-message",
        "/api/send-message",
        {"text": "The report is ready.", "dry_run": "true"},
        "dry_run",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.external_access:api_external_access_client_toggle",
        "/api/external-access/clients/{client_id}/disabled",
        "/api/external-access/clients/desk-app/disabled",
        {"disabled": "false"},
        "disabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.external_access:api_external_access_client",
        "/api/external-access/clients",
        "/api/external-access/clients",
        {"label": "notes app", "surfaces": ["openai"], "persistent_sessions": "true"},
        "persistent_sessions",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.external_access:"
        "api_external_access_client_persistent_sessions",
        "/api/external-access/clients/{client_id}/persistent-sessions",
        "/api/external-access/clients/desk-app/persistent-sessions",
        {"persistent_sessions": "false"},
        "persistent_sessions",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.views:api_dashboard_view_tile_resolve",
        "/api/dashboard/views/{view_id}/tiles/resolve",
        "/api/dashboard/views/overview/tiles/resolve",
        {"ref": "artifact:weekly-notes", "keep": "false"},
        "keep",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.core:api_sel_rotate",
        "/api/sel/rotate",
        "/api/sel/rotate",
        {"archive": "false"},
        "archive",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.core:api_project_trust",
        "/api/guardrails/project-trust",
        "/api/guardrails/project-trust",
        {"dir": "/home/user/projects/notes", "trusted": "true"},
        "trusted",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.triggers:api_trigger_toggle",
        "/api/triggers/{id}/toggle",
        "/api/triggers/schedule:nightly/toggle",
        {"enabled": "false"},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.providers.instance_routes:handle_set_use_case_settings",
        "/api/models/use-cases/{use_case}/settings",
        "/api/models/use-cases/tts/settings",
        {"enabled": "false"},
        "enabled",
        "PUT",
    ),
]


def _handler(spec: str):
    import importlib

    module, name = spec.split(":")
    return getattr(importlib.import_module(module), name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spec, route, path, body, field, method",
    _DOORS,
    ids=[f"{d[0].rsplit(':', 1)[1]}-{d[4]}" for d in _DOORS],
)
async def test_a_door_refuses_a_boolean_sent_as_text(spec, route, path, body, field, method):
    status, payload = await _send(_handler(spec), path, body, route=route, method=method)
    assert _refused(status, payload, field), (status, payload)


@pytest.mark.asyncio
async def test_a_refused_speech_switch_writes_no_settings():
    from personalclaw.providers.instance_routes import handle_set_use_case_settings
    from personalclaw.providers.use_cases import _settings_dir

    status, payload = await _send(
        handle_set_use_case_settings,
        "/api/models/use-cases/tts/settings",
        {"enabled": "false", "auto_speak": True},
        route="/api/models/use-cases/{use_case}/settings",
        method="PUT",
    )
    assert _refused(status, payload, "enabled"), (status, payload)
    assert not (_settings_dir() / "tts.json").exists()


# ── a knowledge item's pin and archive, on a real store ──────────────────────


@pytest.fixture
def kstore(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    return KnowledgeStore(str(tmp_path / "knowledge.db"))


async def _patch_item(store, iid: str, body: dict[str, Any]):
    from personalclaw.dashboard.handlers import knowledge as H

    return await _send(
        H.update_item,
        f"/api/knowledge/items/{iid}",
        {"reingest": False, **body},
        route="/api/knowledge/items/{id}",
        method="PATCH",
        state=SimpleNamespace(knowledge_store=store),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["is_archived", "is_pinned"])
@pytest.mark.parametrize("sent", ["false", "0", "no"], ids=repr)
async def test_an_item_flag_sent_as_text_changes_nothing(kstore, field, sent):
    iid = kstore.create_typed_item(item_type="note", title="Plan", content="alpha")
    status, payload = await _patch_item(kstore, iid, {field: sent, "title": "Renamed"})
    assert _refused(status, payload, field), (status, payload)
    item = kstore.get_item(iid)
    assert item[field] is False
    assert item["title"] == "Plan", "a refused edit wrote the rest of the body"


@pytest.mark.asyncio
async def test_an_item_flag_sent_as_a_boolean_is_written(kstore):
    iid = kstore.create_typed_item(item_type="note", title="Plan", content="alpha")
    status, payload = await _patch_item(kstore, iid, {"is_archived": True})
    assert status == 200, payload
    assert kstore.get_item(iid)["is_archived"] is True
    status, payload = await _patch_item(kstore, iid, {"is_archived": False})
    assert status == 200, payload
    assert kstore.get_item(iid)["is_archived"] is False


# ── a store trigger switched off stays off ───────────────────────────────────


@pytest.fixture
def trigger_home(tmp_path, monkeypatch):
    from personalclaw.config import loader
    from personalclaw.dashboard.handlers import triggers as T
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    return tmp_path


def _event_trigger(home: Path, *, enabled: bool) -> None:
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    TriggerStore(base_dir=home).upsert(
        Trigger(
            id="event:deploy",
            name="Deploy notes",
            kind="event",
            enabled=enabled,
            created_by="user",
            spec={"source": "memory", "pattern": "MemoryKeyPattern", "key_glob": "project.*"},
            workflow={"inline": {"provider": "notify", "config": {"title_template": "Deployed"}}},
        )
    )


def _enabled(home: Path) -> bool:
    from personalclaw.triggers.store import TriggerStore

    row = TriggerStore(base_dir=home).get("event:deploy")
    assert row is not None
    return row.trigger.enabled


async def _toggle(body: dict[str, Any]):
    from personalclaw.dashboard.handlers.triggers import api_trigger_toggle

    return await _send(
        api_trigger_toggle,
        "/api/triggers/store:event:deploy/toggle",
        body,
        route="/api/triggers/{id}/toggle",
        state=SimpleNamespace(push_refresh=lambda *_: None),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", ["false", "true", "off", 0, 1], ids=repr)
async def test_a_trigger_switched_off_stays_off_for_text(trigger_home, sent):
    _event_trigger(trigger_home, enabled=False)
    status, payload = await _toggle({"enabled": sent})
    assert _refused(status, payload, "enabled"), (status, payload)
    assert _enabled(trigger_home) is False


@pytest.mark.asyncio
async def test_a_trigger_switch_takes_a_boolean(trigger_home):
    _event_trigger(trigger_home, enabled=True)
    status, payload = await _toggle({"enabled": False})
    assert status == 200, payload
    assert _enabled(trigger_home) is False
