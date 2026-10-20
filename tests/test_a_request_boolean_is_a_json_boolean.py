"""A boolean in a request body is the JSON ``true`` or ``false``, or the request is refused.

``bool("false")`` is True. A door that read its switch or its consent by truthiness turned on
what its caller turned off: the one-link pack import read ``"consent": "false"`` as consent and
installed a pack the scanner had flagged for review, a trigger sent ``"enabled": "false"`` was
switched on, and a loop created with ``"autopilot": "false"`` drove itself. Every door now reads
its booleans through ``request_validation`` (``bool_field`` / ``require_bool`` /
``optional_bool``): a real boolean is itself, an omitted field takes the door's own safe default,
and anything else is a 400 ``field_not_a_boolean`` that names the field and changes nothing.

``tests/test_request_boolean_census.py`` holds every door to it; this file drives the doors the
defect reached. What each reader makes of every spelling is a row of the one table in
``test_safety_flags_reject_truthy_strings``; the readers' own cases of a field left out or sent as
a null stay here. Each door is driven end to end, each time a row of one of the tests below: a
``true``, a ``false``, a field left out where the door has a default, and the text ``"false"``,
which it refuses, changing nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from table_rows import row
from test_safety_flags_reject_truthy_strings import JSON_NO, JSON_REFUSED, JSON_YES

from personalclaw.dashboard.request_boundary import request_boundary_middleware
from personalclaw.request_validation import (
    MISSING,
    RequestValidationError,
    bool_field,
    optional_bool,
    require_bool,
)

pytestmark = pytest.mark.security


# ── the readers: a field left out, or sent as a null ─────────────────────────


def test_an_omitted_field_takes_the_door_default():
    for default in (True, False):
        assert bool_field({}, "flag", default=default) is default


def test_a_null_is_refused_where_the_door_has_a_default():
    """``bool(None)`` read a null as no where an omitted field read as yes: not one request, so a
    switch with a default of its own refuses a null rather than guess which was meant."""
    for default in (True, False):
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


# ── the doors ────────────────────────────────────────────────────────────────

#: What a door does with a value it refuses, in place of reading it.
REFUSED = "refused"
#: A field the request leaves out.
OMITTED = object()


def _flagged_one_link(scratch: Path, mp: pytest.MonkeyPatch) -> tuple[str, Path]:
    """A one-link document for a pack whose one skill the scanner flags for review.

    The skill is ordinary; the scanner is stubbed to answer WARNING for it, the verdict that
    makes the import ask for consent. Returns ``(document, importer_home)``.
    """
    from personalclaw import supply_chain
    from personalclaw.packs import onelink
    from personalclaw.packs.build import build_pack

    author = scratch / "author"
    skill = author / "skills" / "weekly-notes"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: weekly-notes\ndescription: Summarise the week's notes\n---\n# Notes\n",
        encoding="utf-8",
    )
    mp.setenv("PERSONALCLAW_HOME", str(author))
    archive = build_pack(
        ["skill:weekly-notes"], name="notes", version="1.0.0", out_path=scratch / "notes.pclaw"
    )
    document = onelink.to_onelink(Path(archive))

    def _flagged(self, staged_dir, tier=supply_chain.TrustTier.COMMUNITY):
        return supply_chain.ScanReport(verdict=supply_chain.Verdict.WARNING, tier=tier)

    mp.setattr(supply_chain.SkillScanner, "scan", _flagged)
    importer = scratch / "importer"
    importer.mkdir()
    mp.setenv("PERSONALCLAW_HOME", str(importer))
    return document, importer


async def _one_link(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> str:
    """Whether the flagged pack ``"installed"``, was ``"asked"`` for consent, or was refused."""
    from personalclaw.dashboard.handlers.packs import api_pack_one_link

    document, home = _flagged_one_link(scratch, mp)
    body = {"link": document} if sent is OMITTED else {"link": document, "consent": sent}
    status, payload = await _send(api_pack_one_link, "/api/packs/one-link", body)
    installed = (home / "skills" / "weekly-notes").exists()
    if _refused(status, payload, "consent"):
        assert not installed
        return REFUSED
    if status == 409:
        assert payload["error"]["code"] == "pack_refused_needs_consent" and not installed
        return "asked"
    assert status == 200 and installed, (status, payload)
    return "installed"


def _item_flag(field: str):
    async def drive(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> object:
        """The flag a real store holds after the edit, or refused with nothing of it written."""
        from personalclaw.dashboard.handlers import knowledge as H
        from personalclaw.knowledge.store import KnowledgeStore

        store = KnowledgeStore(str(scratch / "knowledge.db"))
        iid = store.create_typed_item(item_type="note", title="Plan", content="alpha")
        start = (not sent) if isinstance(sent, bool) else False
        store.update_item(iid, **{field: int(start)})
        status, payload = await _send(
            H.update_item,
            f"/api/knowledge/items/{iid}",
            {"reingest": False, field: sent, "title": "Renamed"},
            route="/api/knowledge/items/{id}",
            method="PATCH",
            state=SimpleNamespace(knowledge_store=store),
        )
        item = store.get_item(iid)
        if _refused(status, payload, field):
            assert (item[field], item["title"]) == (start, "Plan"), "a refused edit wrote"
            return REFUSED
        assert status == 200, payload
        return item[field]

    return drive


async def _trigger_toggle(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> object:
    """A store trigger's switch after the toggle, or refused and still as it was."""
    from personalclaw.config import loader
    from personalclaw.dashboard.handlers import triggers as T
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    mp.setattr(loader, "config_dir", lambda: scratch)
    mp.setattr(T, "config_dir", lambda: scratch)
    mp.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=scratch))
    start = (not sent) if isinstance(sent, bool) else False
    TriggerStore(base_dir=scratch).upsert(
        Trigger(
            id="event:deploy",
            name="Deploy notes",
            kind="event",
            enabled=start,
            created_by="user",
            spec={"source": "memory", "pattern": "MemoryKeyPattern", "key_glob": "project.*"},
            workflow={"inline": {"provider": "notify", "config": {"title_template": "Deployed"}}},
        )
    )
    status, payload = await _send(
        T.api_trigger_toggle,
        "/api/triggers/store:event:deploy/toggle",
        {"enabled": sent},
        route="/api/triggers/{id}/toggle",
        state=SimpleNamespace(push_refresh=lambda *_: None),
    )
    stored = TriggerStore(base_dir=scratch).get("event:deploy")
    assert stored is not None
    if _refused(status, payload, "enabled"):
        assert stored.trigger.enabled is start
        return REFUSED
    assert status == 200, payload
    return stored.trigger.enabled


def _rewind(flag: str):
    async def drive(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> object:
        """What the run was asked to do, or refused with the run not rewound: its cached outputs
        stay, and no request to fire its effects again reaches its history."""
        from personalclaw.workflows import handlers

        seen: dict[str, Any] = {}

        def _record(run_id, node_id, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "preview": {}}

        mp.setattr(handlers.service, "rewind_run", _record)
        body = {"node_id": "publish", **({} if sent is OMITTED else {flag: sent})}
        status, payload = await _send(
            handlers.api_run_rewind,
            "/api/workflows/runs/a1b2c3d4/rewind",
            body,
            route="/api/workflows/runs/{run_id}/rewind",
            state=SimpleNamespace(workflows=None, _restricted=False),
        )
        if _refused(status, payload, flag):
            assert seen == {}
            return REFUSED
        assert status == 200, payload
        return seen[flag]

    return drive


class Door(NamedTuple):
    """A door driven end to end, and what it does with ``true``, ``false`` and a field left out."""

    name: str
    drive: Callable[[Path, pytest.MonkeyPatch, object], Awaitable[object]]
    true: object
    false: object
    #: ``None`` where a field left out is no default, and is not driven here.
    omitted: object


DOORS = [
    Door("POST /api/packs/one-link consent", _one_link, "installed", "asked", "asked"),
    Door(
        "PATCH /api/knowledge/items/{id} is_archived", _item_flag("is_archived"), True, False, None
    ),
    Door("PATCH /api/knowledge/items/{id} is_pinned", _item_flag("is_pinned"), True, False, None),
    Door("POST /api/triggers/{id}/toggle enabled", _trigger_toggle, True, False, None),
    Door(
        "POST /api/workflows/runs/{run_id}/rewind redo_effects",
        _rewind("redo_effects"),
        True,
        False,
        False,
    ),
    Door("POST /api/workflows/runs/{run_id}/rewind force", _rewind("force"), True, False, False),
]


async def _drive(subtests, tmp_path: Path, answer: str, sent: object) -> None:
    assert len(DOORS) == 6, "a door was dropped from the table"
    assert len(DOORS) == len({door.name for door in DOORS}), "two doors share a name"
    for at, door in enumerate(DOORS):
        expected = REFUSED if answer == "refused" else getattr(door, answer)
        if expected is None:
            # No default to take: a knowledge item's flag is read only when it is sent, and a
            # trigger's toggle left without enabled switches the trigger over.
            continue
        with row(subtests, door=door.name):
            scratch = tmp_path / f"door-{at}"
            scratch.mkdir()
            with pytest.MonkeyPatch.context() as mp:
                got = await door.drive(scratch, mp, sent)
            assert got == expected, f"{door.name} sent {sent!r} did {got!r}, not {expected!r}"


@pytest.mark.asyncio
async def test_every_request_door_takes_a_true(subtests, tmp_path):
    await _drive(subtests, tmp_path, "true", JSON_YES)


@pytest.mark.asyncio
async def test_every_request_door_takes_a_false(subtests, tmp_path):
    await _drive(subtests, tmp_path, "false", JSON_NO)


@pytest.mark.asyncio
async def test_a_request_door_left_without_its_boolean_takes_its_safe_default(subtests, tmp_path):
    await _drive(subtests, tmp_path, "omitted", OMITTED)


#: ``(handler, route, path, body, field, method)``: doors that refuse before anything is read or
#: written, each sent its boolean as text: ``"false"``, or ``"true"`` where a yes is the danger.
_REFUSING = [
    (
        "personalclaw.dashboard.handlers.mcp:api_mcp_toggle",
        "/api/mcp/toggle",
        "/api/mcp/toggle",
        {"name": "notes", "enabled": JSON_REFUSED},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.mcp:api_mcp_toggle_tool",
        "/api/mcp/toggle-tool",
        "/api/mcp/toggle-tool",
        {"server": "notes", "tool": "search", "enabled": JSON_REFUSED},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.mcp:api_mcp_toggle_all",
        "/api/mcp/toggle-all",
        "/api/mcp/toggle-all",
        {"enabled": JSON_REFUSED},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.tools:api_tools_toggle",
        "/api/tools/toggle",
        "/api/tools/toggle",
        {"provider": "personalclaw-core", "name": "artifact_list", "enabled": JSON_REFUSED},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.tools:api_providers_toggle",
        "/api/tools/provider-toggle",
        "/api/tools/provider-toggle",
        {"provider": "personalclaw-artifacts", "enabled": JSON_REFUSED},
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
        {"disabled": JSON_REFUSED},
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
        {"persistent_sessions": JSON_REFUSED},
        "persistent_sessions",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.views:api_dashboard_view_tile_resolve",
        "/api/dashboard/views/{view_id}/tiles/resolve",
        "/api/dashboard/views/overview/tiles/resolve",
        {"ref": "artifact:weekly-notes", "keep": JSON_REFUSED},
        "keep",
        "POST",
    ),
    (
        "personalclaw.dashboard.handlers.core:api_sel_rotate",
        "/api/sel/rotate",
        "/api/sel/rotate",
        {"archive": JSON_REFUSED},
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
        {"enabled": JSON_REFUSED},
        "enabled",
        "POST",
    ),
    (
        "personalclaw.providers.instance_routes:handle_set_use_case_settings",
        "/api/models/use-cases/{use_case}/settings",
        "/api/models/use-cases/tts/settings",
        {"enabled": JSON_REFUSED, "auto_speak": True},
        "enabled",
        "PUT",
    ),
]


def _handler(spec: str):
    import importlib

    module, name = spec.split(":")
    return getattr(importlib.import_module(module), name)


@pytest.mark.asyncio
async def test_every_request_door_refuses_a_boolean_sent_as_text(subtests, tmp_path):
    """Refused naming the field, before anything is read or written: the doors driven above with
    nothing of the request applied, and the doors that refuse before they read anything else."""
    from request_boolean_census_shared import BODY_BOOLEANS

    from personalclaw.providers.use_cases import _settings_dir

    await _drive(subtests, tmp_path, "refused", JSON_REFUSED)
    assert len(_REFUSING) == 15, "a door was dropped from the table"
    pinned = {(module, fn) for module, fn, _ in BODY_BOOLEANS}
    for spec, route, path, body, field, method in _REFUSING:
        with row(subtests, door=f"{method} {route} {field}"):
            module, fn = spec.split(":")
            where = module.removeprefix("personalclaw.").replace(".", "/") + ".py"
            assert (where, fn) in pinned, f"{spec} is not a door the census pins"
            status, payload = await _send(_handler(spec), path, body, route=route, method=method)
            assert _refused(status, payload, field), (status, payload)
    assert not (_settings_dir() / "tts.json").exists(), "a refused speech switch wrote its settings"
