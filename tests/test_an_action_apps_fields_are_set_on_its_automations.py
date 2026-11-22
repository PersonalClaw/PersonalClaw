"""An automation action's fields are set on each automation that runs it, never as app settings.

🔴 The defect (Backlog: "An action app's Configure page saves its per-automation fields as app
settings that nothing reads"). An app whose provider is an automation action, and which declares
no settings of the app's own (A2A Agent Call, HTTP Webhook, and every action that ships with
PersonalClaw), served its action's ``settingsSchema`` as the app's settings. That schema is the
action's form in each automation that runs it (`hooks.api_action_providers`). Apps › A2A Agent
Call › Configure offered Agent URL, Skill, Message, Inputs and Headers, saved them in the app's
``data/config.json``, and nothing read them: its ``create_provider`` drops them, and each fire
hands the action its automation's own values, so every automation set up that way failed "A2A
action is missing 'url' field".

Now such an app has no app-level settings, as an app whose settings live on its instances has
none: its Configure goes to the Automations page, its settings routes refuse with a sentence that
says where its fields are set, and its row says ``configuredPerAutomation``. An action app that
does declare settings of its own keeps them.

Driven through the real manifest model, the real Apps handlers and the real listing builders,
with the manifests the bundled actions ship and one shaped as A2A Agent Call's.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.apps.manifest import AppManifest
from personalclaw.dashboard.handlers import apps as A

#: A2A Agent Call's manifest as the apps repository ships it: an action provider whose
#: settingsSchema is each automation's form, and no settings of the app's own.
A2A: dict[str, Any] = {
    "name": "a2a-action",
    "version": "1.0.0",
    "displayName": "A2A Agent Call",
    "description": "Call another agent.",
    "provider": {
        "type": "action",
        "implementation": "provider:create_provider",
        "settingsSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "title": "Agent URL"},
                "skill": {"type": "string"},
                "text": {"type": "string", "title": "Message"},
            },
        },
    },
}

NATIVE = Path(A.__file__).resolve().parents[2] / "apps" / "native"


def _native_actions() -> list[Path]:
    """The manifests of the actions that ship with PersonalClaw, declared singular or listed."""
    found = []
    for path in sorted(NATIVE.glob("*/app.json")):
        first = AppManifest.from_dict(json.loads(path.read_text())).first_provider()
        if first is not None and first.type == "action":
            found.append(path)
    assert len(found) >= 10, f"the shipped actions were not found under {NATIVE}"
    return found


def _request(method: str, name: str) -> web.Request:
    req = make_mocked_request(method, f"/api/apps/{name}/config", match_info={"name": name})

    async def _json():
        return {"url": "https://agent.example/a2a"}

    req.json = _json  # type: ignore[assignment]
    return req


@pytest.fixture
def a2a(monkeypatch) -> AppManifest:
    manifest = AppManifest.from_dict(A2A)
    monkeypatch.setattr("personalclaw.apps.app_manager._manifest_of", lambda _n: manifest)
    return manifest


def test_an_action_apps_fields_are_not_its_settings(a2a):
    """🔴 Before: the action's per-automation form was served as the app's settings."""
    assert A._effective_config_schema(a2a) == {}
    said = A._configured_elsewhere(a2a) or ""
    assert said.startswith("A2A Agent Call is set up on each automation that runs its action"), said
    assert "Automations page" in said


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_its_settings_routes_say_where_its_fields_are_set(a2a, method):
    """🔴 Before: GET served the form and PUT saved it where nothing reads it."""
    handler = A.api_app_config_get if method == "GET" else A.api_app_config_put
    resp = asyncio.run(handler(_request(method, "a2a-action")))
    assert resp.status == 409, resp.body
    assert "set up on each automation" in json.loads(resp.body)["error"]


def test_its_row_says_so_and_offers_no_settings():
    (row,) = A._installed_rows([{"name": "a2a-action", "manifest": A2A}], {}, {})
    assert (row["configuredPerAutomation"], row["hasConfig"], row["configuredPerInstance"]) == (
        True,
        False,
        False,
    ), row


@pytest.mark.parametrize("path", _native_actions(), ids=lambda p: p.parent.name)
def test_every_action_that_ships_is_set_up_on_its_automations(path):
    """Each bundled action's form is each automation's too: Bash's command, Notify's title."""
    manifest = AppManifest.from_dict(json.loads(path.read_text()))
    assert A._configured_per_automation(manifest), path.parent.name
    assert A._effective_config_schema(manifest) == {}


def test_the_bundled_actions_are_listed_as_set_up_on_their_automations():
    """Their rows as the listing builds a bundled extension's (`_bundled_rows`)."""
    extensions = []
    for path in _native_actions():
        manifest = AppManifest.from_dict(json.loads(path.read_text()))
        extensions.append(
            SimpleNamespace(
                name=manifest.name,
                manifest=manifest,
                provider_config=manifest.first_provider(),
                enabled=True,
            )
        )
    assert extensions, "no bundled action was found"
    # Core's own tools are listed beside them unless the caller already has them: they have.
    rows = A._bundled_rows(extensions, {"personalclaw-filesystem"})
    assert [r["name"] for r in rows] == [e.name for e in extensions]
    for row in rows:
        assert (row["configuredPerAutomation"], row["hasConfig"]) == (True, False), row["name"]


def test_an_action_app_with_settings_of_its_own_keeps_them():
    """The precedent holds: an app's own settings, or a setup schema, are its settings."""
    own = {"type": "object", "properties": {"api_key": {"type": "string"}}}
    manifest = AppManifest.from_dict({**A2A, "settings": own})
    assert A._configured_elsewhere(manifest) is None
    assert A._effective_config_schema(manifest) == own
    raw = {**A2A, "settings": own}
    (row,) = A._installed_rows([{"name": "a2a-action", "manifest": raw}], {}, {})
    assert row["configuredPerAutomation"] is False


def test_a_provider_that_is_not_an_action_keeps_its_form():
    channel = {
        **A2A,
        "provider": {**A2A["provider"], "type": "channel"},
    }
    manifest = AppManifest.from_dict(channel)
    assert A._configured_elsewhere(manifest) is None
    assert A._effective_config_schema(manifest) == A2A["provider"]["settingsSchema"]
