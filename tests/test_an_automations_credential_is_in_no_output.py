"""After an automation is saved and fires, its credential is in no output and no file but the store.

The sweep the projection list cannot vouch for on its own: a scheduled webhook automation and a
lifecycle trigger are each saved with a known URL and token, fired through the real fire paths by
an action that echoes what it was handed (the lifecycle trigger's headers given as an object, as
the API and an agent's tool may send them), and then every surface that serializes them is read for
the plain values: the automation rows, the dry run, the agent's automation list, the run records
and history, the audit and security-log rows, the waiting list, the Inbox and the bell (each is a
file in the home, which is read whole), and an export of the home. The credential store (``.env``
in the test home) is the one place they may be.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

APP = "fixture-sweep-app"
ACTION = "fixture-sweep-hook"
URL = "https://example.com/fixture-sweep-hook-3b2a19"
TOKEN = "fixture-sweep-token-8f7e6d"
HOOK_URL = "https://example.com/fixture-lifecycle-hook-6c5b4a"
HOOK_TOKEN = "fixture-lifecycle-token-2d1c0b"
SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "x-meta": {"label": "Webhook URL", "sensitive": True}},
        "headers": {"type": "string", "x-meta": {"label": "Headers (JSON)", "sensitive": True}},
    },
}


class _Echo:
    """An app's webhook action that says, in its answer, everything it was handed."""

    name = ACTION
    display_name = "Fixture Sweep Hook"

    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def execute(self, config, ctx, timeout=30):
        self.seen.append(dict(config))
        said = f"posted to {config.get('url')} with {config.get('headers')}"
        from personalclaw.action_providers.base import ActionResult

        return ActionResult(
            success=True, stdout=said, stderr=said, exit_code=200, error="", duration_ms=1
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
                "displayName": "Fixture Sweep App",
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
    registry.register_action_provider(echo, app=APP, app_label="Fixture Sweep App")
    return echo


def _hits(text: str) -> list[str]:
    return [v for v in (URL, TOKEN, HOOK_URL, HOOK_TOKEN) if v in text]


def test_a_saved_and_fired_automation_leaves_its_credential_in_no_output(home, action):
    from personalclaw.dashboard.handlers.triggers import _serialize_lifecycle, _serialize_store
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.hooks import ScriptHookStore, run_script_hook
    from personalclaw.portability import create_export_zip
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.models import parse_trigger
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(home)
    trigger, _issues = parse_trigger(
        {
            "id": "sweep-auto",
            "kind": "clock",
            "name": "Sweep automation",
            "spec": {"kind": "cron", "expr": "0 9 * * *"},
            "workflow": {
                "inline": {
                    "provider": ACTION,
                    "config": {"url": URL, "headers": json.dumps({"Authorization": TOKEN})},
                }
            },
        }
    )
    saved = store.upsert(trigger)
    saved.capabilities = {"providers": [ACTION]}
    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(saved, {"trigger_id": saved.id})
    )
    assert action.seen and action.seen[-1]["url"] == URL, "the fire handed the action its value"

    hooks = ScriptHookStore(home)
    hook = hooks.create(
        {
            "id": "sweep-hook",
            "name": "Sweep lifecycle trigger",
            "event": "Stop",
            "provider": ACTION,
            "provider_config": {"url": HOOK_URL, "headers": {"Authorization": HOOK_TOKEN}},
            "capabilities": {"providers": [ACTION]},
        }
    )
    hook_result = asyncio.run(run_script_hook(hooks.get(hook.id), "", {"hook_event_name": "Stop"}))
    assert action.seen[-1]["url"] == HOOK_URL, "the hook handed the action its value"

    outputs: dict[str, str] = {
        "row": json.dumps(_serialize_store(store.get("sweep-auto")), default=str),
        "dry run": json.dumps(
            vars(T.run(store, trigger_id="sweep-auto", dry_run=True, yours=True)), default=str
        ),
        "agent list": json.dumps(vars(T.list_automations(store)), default=str),
        "lifecycle row": json.dumps(_serialize_lifecycle(hooks.get("sweep-hook"), []), default=str),
        "hook result": json.dumps(vars(hook_result), default=str),
    }
    for path in sorted(home.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.name != ".env":
            outputs[str(path.relative_to(home))] = path.read_bytes().decode("utf-8", "replace")
    archive, _manifest = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for member in zf.namelist():
            outputs[f"export:{member}"] = zf.read(member).decode("utf-8", "replace")

    origin = json.dumps("https://example.com/••••••••")[1:-1]  # as the JSON text holds it
    assert origin in outputs["dry run"] and origin in outputs["row"], "where it goes is shown"
    reads = ("row", "dry run", "agent list", "lifecycle row", "hook result")
    keys = [where for where in reads if "PCSECRET_TRIGGER_" in outputs[where]]
    assert keys == [], f"a read handed out the key a value is kept under: {keys}"
    leaks = {where: _hits(text) for where, text in outputs.items() if _hits(text)}
    assert leaks == {}, f"the credential reached: {sorted(leaks)}"
    assert (home / ".env").read_text().count(URL) == 1, "the store holds it, once"
