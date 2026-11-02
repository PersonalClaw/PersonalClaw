"""A lifecycle trigger's credential settings live in the credential store, and its secret references
are filled when it runs.

A lifecycle trigger (``hooks.json``, run on an agent event) hands an action its config as a stored
automation does, from the same schema-driven form. Its config was written as typed, so a webhook
URL or an Authorization header was kept in ``hooks.json``; and the hook handed the action its
config as stored, so a ``{{secret:NAME}}`` reached the action as the placeholder itself, the defect
a stored automation's fire had before ``triggers.secrets``. Now a save keeps such a field in the
credential store under a key the hook alone holds, a read shows the mask, a run fills its own
references and the user's, what the action answers is masked of them, deleting the hook deletes its
keys, and a boot moves what an earlier release left in the file.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

APP = "fixture-lifecycle-app"
ACTION = "fixture-lifecycle-hook"
URL = "https://example.com/fixture-lifecycle-7a6b5c"
TOKEN = "fixture-lifecycle-token-4d3e2f"
SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "x-meta": {"label": "Webhook URL", "sensitive": True}},
        "headers": {"type": "string", "x-meta": {"label": "Headers (JSON)", "sensitive": True}},
    },
}


class _Echo:
    name = ACTION
    display_name = "Fixture Lifecycle Hook"

    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def execute(self, config, ctx, timeout=30):
        self.seen.append(dict(config))
        from personalclaw.action_providers.base import ActionResult

        return ActionResult(
            success=True,
            stdout=f"posted to {config.get('url')}",
            stderr="",
            exit_code=0,
            error="",
            blocked=False,
            duration_ms=1,
        )


@pytest.fixture
def home():
    from personalclaw.config.loader import config_dir

    return Path(config_dir())


@pytest.fixture
def action(home, monkeypatch):
    from personalclaw.action_providers import registry

    bundle = home / "apps" / APP
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "app.json").write_text(
        json.dumps(
            {
                "name": APP,
                "version": "1.0.0",
                "displayName": "Fixture Lifecycle App",
                "description": "fixture",
                "provider": {
                    "type": "action",
                    "implementation": "provider:create_provider",
                    "settingsSchema": SCHEMA,
                },
            }
        ),
        encoding="utf-8",
    )
    echo = _Echo()
    monkeypatch.setattr(registry, "_providers", {**registry._providers})
    monkeypatch.setattr(registry, "_origins", {**registry._origins})
    registry.register_action_provider(echo, app=APP, app_label="Fixture Lifecycle App")
    return echo


def _hooks(home):
    from personalclaw.hooks import ScriptHookStore

    return ScriptHookStore(home)


def _make(store, **config):
    return store.create(
        {
            "id": "fixture-hook",
            "name": "Fixture lifecycle trigger",
            "event": "Stop",
            "provider": ACTION,
            "provider_config": config,
            "capabilities": {"providers": [ACTION]},
        }
    )


def _stored_config(home) -> dict:
    (row,) = json.loads((home / "hooks.json").read_text())["hooks"]
    return row["provider_config"]


def _anywhere(home, needle: str) -> list[str]:
    return [
        str(p.relative_to(home))
        for p in sorted(home.rglob("*"))
        if p.is_file() and not p.is_symlink() and needle.encode() in p.read_bytes()
    ]


def test_a_saved_lifecycle_trigger_keeps_its_credentials_in_the_store(home, action):
    """🔴 Red before: the URL and the token were written to hooks.json as typed."""
    from personalclaw.config import secret_refs

    _make(_hooks(home), url=URL, headers=json.dumps({"Authorization": TOKEN}))
    stored = _stored_config(home)
    assert all(
        secret_refs.is_trigger_key(secret_refs.ref_key(stored[f])) for f in SCHEMA["properties"]
    )
    assert _anywhere(home, URL) == [".env"] and _anywhere(home, TOKEN) == [".env"]


def test_a_run_fills_its_own_and_the_users_references_and_masks_them_from_the_answer(home, action):
    """🔴 Red before: the hook handed the action ``{{secret:NAME}}`` as written."""
    from personalclaw.config.credentials import save_credential
    from personalclaw.hooks import run_script_hook

    save_credential("FIXTURE_LIFECYCLE_TOKEN", TOKEN)
    store = _hooks(home)
    _make(
        store,
        url=URL,
        headers=json.dumps({"Authorization": "Bearer {{secret:FIXTURE_LIFECYCLE_TOKEN}}"}),
    )
    result = asyncio.run(
        run_script_hook(store.get("fixture-hook"), "", {"hook_event_name": "Stop"})
    )

    (handed,) = action.seen
    assert handed["url"] == URL
    assert json.loads(handed["headers"])["Authorization"] == f"Bearer {TOKEN}"
    assert URL not in json.dumps(vars(result), default=str), "the answer is masked of the value"


def test_a_secret_that_does_not_resolve_refuses_the_run(home, action):
    from personalclaw.hooks import run_script_hook

    store = _hooks(home)
    _make(store, url="{{secret:FIXTURE_NOT_STORED}}")
    result = asyncio.run(
        run_script_hook(store.get("fixture-hook"), "", {"hook_event_name": "Stop"})
    )
    assert action.seen == [], "the action never ran with a placeholder"
    assert "FIXTURE_NOT_STORED" in (result.error or "")


def test_a_read_shows_the_mask_and_deleting_the_hook_deletes_its_keys(home, action):
    from personalclaw.apps.secret_fields import SECRET_MASK
    from personalclaw.dashboard.handlers.triggers import _serialize_lifecycle

    store = _hooks(home)
    _make(store, url=URL)
    row = _serialize_lifecycle(store.get("fixture-hook"), [])
    assert row["action"]["config"]["url"] == f"https://example.com/{SECRET_MASK}", "where it goes"

    shown = row["action"]["config"]["url"]
    store.update("fixture-hook", {"provider_config": {"url": shown}, "name": "Renamed"})
    assert _anywhere(home, URL) == [".env"], "a save of the form as read keeps the value"
    assert store.delete("fixture-hook") is True
    assert _anywhere(home, URL) == []


def test_headers_given_as_an_object_are_held_and_the_run_sends_them(home, action):
    """🔴 Red before: ``headers`` saved as an object stayed in hooks.json as typed."""
    from personalclaw.hooks import run_script_hook

    given = {"Authorization": f"Bearer {TOKEN}"}
    store = _hooks(home)
    _make(store, url=URL, headers=given)
    assert _anywhere(home, TOKEN) == [".env"]

    asyncio.run(run_script_hook(store.get("fixture-hook"), "", {"hook_event_name": "Stop"}))
    (handed,) = action.seen
    assert json.loads(handed["headers"]) == given


def test_a_boot_moves_what_an_earlier_release_left_in_hooks_json(home, action):
    """🔴 Red before: nothing moved a lifecycle trigger's plain value."""
    from personalclaw.dashboard.server import _move_stored_settings
    from personalclaw.hooks import ScriptHook

    plain = ScriptHook(
        id="fixture-hook",
        name="Old",
        event="Stop",
        provider=ACTION,
        provider_config={"url": URL, "headers": {"Authorization": TOKEN}},
        capabilities={"providers": [ACTION]},
    )
    (home / "hooks.json").write_text(json.dumps({"hooks": [plain.to_dict()]}))

    _move_stored_settings()  # the gateway's start

    text = (home / "hooks.json").read_text()
    assert URL not in text and TOKEN not in text, "headers saved as an object move too"
    assert _anywhere(home, URL) == [".env"] and _anywhere(home, TOKEN) == [".env"]
