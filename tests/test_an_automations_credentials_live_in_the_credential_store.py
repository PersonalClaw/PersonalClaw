"""An automation's credential settings live in the credential store; ``triggers.json`` holds a
reference.

An action provider's manifest declares which of its settings are credentials (``x-meta.sensitive``
on its ``settingsSchema``), and the automation form is built from that schema. App settings kept
such a field in the credential store; an automation did not: a webhook URL or an Authorization
header typed into an automation was written to ``triggers.json`` as typed, and every snapshot and
export of the home carried it. Now every write of the store keeps such a field in the credential
store under a key the automation owns, a read shows the mask, a fire fills it, deleting the
automation deletes its keys, and a boot moves what an earlier release left in the file.

Driven through ``TriggerStore`` and the real fire path, with an action provider registered as an
installed app's whose manifest declares the fields, as webhook-action's does.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

APP = "fixture-hook-app"
ACTION = "fixture-hook"
URL = "https://example.com/fixture-hook-7c1d9e"
TOKEN = "fixture-token-5e4d3c2b"
HEADERS = json.dumps({"Authorization": f"Bearer {TOKEN}"})
SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "x-meta": {"label": "Webhook URL", "sensitive": True}},
        "headers": {"type": "string", "x-meta": {"label": "Headers (JSON)", "sensitive": True}},
        "method": {"type": "string", "x-meta": {"label": "Method"}},
    },
}


class _Recorder:
    """An app's action provider: records what each call was handed."""

    name = ACTION
    display_name = "Fixture Hook"

    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def execute(self, config, ctx, timeout=30):
        self.seen.append(dict(config))
        from personalclaw.action_providers.base import ActionResult

        return ActionResult(success=True, stdout="ok", stderr="", exit_code=0, error="")


@pytest.fixture
def home():
    from personalclaw.config.loader import config_dir

    return config_dir()


@pytest.fixture
def action(home, monkeypatch):
    """The fixture app installed in the isolated home, and its action registered as the app's."""
    from personalclaw.action_providers import registry

    bundle = Path(home) / "apps" / APP
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "app.json").write_text(
        json.dumps(
            {
                "name": APP,
                "version": "1.0.0",
                "displayName": "Fixture Hook App",
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
    provider = _Recorder()
    monkeypatch.setattr(registry, "_providers", {**registry._providers})
    monkeypatch.setattr(registry, "_origins", {**registry._origins})
    registry.register_action_provider(provider, app=APP, app_label="Fixture Hook App")
    return provider


def _trigger(tid: str = "fixture-auto", **config):
    from personalclaw.triggers.models import parse_trigger

    trigger, issues = parse_trigger(
        {
            "id": tid,
            "kind": "clock",
            "name": "Fixture automation",
            "spec": {"clock": "interval", "every": "1h"},
            "workflow": {"inline": {"provider": ACTION, "config": config}},
        }
    )
    return trigger


def _store(home):
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(home)


def _config_on_disk(home, tid: str = "fixture-auto") -> dict:
    doc = json.loads((Path(home) / "triggers.json").read_text())
    (row,) = [r for r in doc["triggers"] if r["id"] == tid]
    return row["workflow"]["inline"]["config"]


def _anywhere(home, needle: str) -> list[str]:
    return [
        str(p.relative_to(home))
        for p in sorted(Path(home).rglob("*"))
        if p.is_file() and not p.is_symlink() and needle.encode() in p.read_bytes()
    ]


def test_a_saved_automation_keeps_its_credentials_in_the_store(home, action):
    """🔴 Red before: the URL and the token were written to triggers.json as typed."""
    from personalclaw.config import secret_refs

    saved = _store(home).upsert(_trigger(url=URL, headers=HEADERS, method="POST"))

    on_disk = _config_on_disk(home)
    assert on_disk["method"] == "POST", "a setting that is no credential is kept as it is"
    for field in ("url", "headers"):
        assert secret_refs.is_trigger_key(secret_refs.ref_key(on_disk[field])), on_disk
    text = (Path(home) / "triggers.json").read_text()
    assert URL not in text and TOKEN not in text
    assert _anywhere(home, URL) == [".env"] and _anywhere(home, TOKEN) == [".env"]
    assert URL not in json.dumps(saved.to_dict()), "the store hands back what it stored"


def test_a_fire_hands_the_action_its_values_and_masks_them_from_what_it_answers(home, action):
    from personalclaw.triggers.secrets import resolve_for

    stored = _store(home).upsert(_trigger(url=URL, headers=HEADERS))
    config = stored.workflow["inline"]["config"]
    handed, filled = resolve_for(action, config, trigger_id=stored.id)

    assert handed["url"] == URL and handed["headers"] == HEADERS
    assert URL in filled and HEADERS in filled, "each value filled is masked from the answer"


def test_the_real_fire_path_hands_the_provider_the_value(home, action):
    """Through `_fire_store_trigger`, as a clock fire runs it."""
    from personalclaw.gateway import GatewayOrchestrator

    stored = _store(home).upsert(_trigger(url=URL))
    stored.capabilities = {"providers": [ACTION]}
    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(stored, {"trigger_id": stored.id})
    )
    assert action.seen and action.seen[-1]["url"] == URL


def test_a_read_shows_the_mask_and_a_save_of_the_form_as_read_keeps_the_value(home, action):
    from personalclaw.apps.secret_fields import SECRET_MASK
    from personalclaw.config import secret_refs
    from personalclaw.triggers.held_secrets import shown

    store = _store(home)
    store.upsert(_trigger(url=URL, headers=HEADERS))
    before = _config_on_disk(home)
    assert shown(before)["url"] == f"https://example.com/{SECRET_MASK}"
    assert shown(before)["headers"] == SECRET_MASK

    # The form sends back what it was shown, with one field changed.
    store.upsert(_trigger(url=shown(before)["url"], headers=SECRET_MASK, method="PUT"))
    after = _config_on_disk(home)
    assert after["url"] == before["url"] and after["headers"] == before["headers"]
    assert after["method"] == "PUT"
    from personalclaw.triggers.held_secrets import fill

    assert fill("fixture-auto", after)[0]["url"] == URL

    # The whole mask, from a client that shows no host, keeps it too.
    store.upsert(_trigger(url=SECRET_MASK, headers=SECRET_MASK, method="PUT"))
    assert _config_on_disk(home)["url"] == before["url"]

    rotated = "https://example.com/fixture-hook-rotated"
    store.upsert(_trigger(url=rotated, headers=SECRET_MASK))
    assert fill("fixture-auto", _config_on_disk(home))[0]["url"] == rotated
    assert _anywhere(home, URL) == [], "the rotated-away value did not linger"
    assert secret_refs.ref_key(_config_on_disk(home)["headers"]) == secret_refs.ref_key(
        before["headers"]
    )


def test_a_reference_to_the_users_own_secret_is_left_as_it_is(home, action):
    from personalclaw.config.credentials import save_credential
    from personalclaw.triggers.secrets import resolve_for

    save_credential("FIXTURE_HOOK", URL)
    stored = _store(home).upsert(
        _trigger(url="{{secret:FIXTURE_HOOK}}", headers='{"X-Key": "{{secret:FIXTURE_HOOK}}"}')
    )
    on_disk = _config_on_disk(home)
    assert on_disk["url"] == "{{secret:FIXTURE_HOOK}}"
    assert on_disk["headers"] == '{"X-Key": "{{secret:FIXTURE_HOOK}}"}'
    handed, _ = resolve_for(action, stored.workflow["inline"]["config"], trigger_id=stored.id)
    assert handed["url"] == URL


def test_a_key_that_is_gone_refuses_the_fire_naming_the_field(home, action):
    from personalclaw.config import secret_refs
    from personalclaw.config.credentials import delete_credential
    from personalclaw.triggers.secrets import UnresolvedSecret, resolve_for

    stored = _store(home).upsert(_trigger(url=URL))
    config = stored.workflow["inline"]["config"]
    delete_credential(secret_refs.ref_key(config["url"]))
    with pytest.raises(UnresolvedSecret) as refused:
        resolve_for(action, config, trigger_id=stored.id)
    assert refused.value.key == "url"


def test_deleting_the_automation_deletes_its_keys(home, action):
    store = _store(home)
    store.upsert(_trigger(url=URL, headers=HEADERS))
    assert store.delete("fixture-auto") is True
    assert _anywhere(home, URL) == [] and _anywhere(home, TOKEN) == []


def test_another_automations_key_is_refused_and_held_by_no_other_record(home, action):
    from personalclaw.config import secret_refs

    store = _store(home)
    store.upsert(_trigger(url=URL))
    theirs = _config_on_disk(home)["url"]
    with pytest.raises(secret_refs.ForeignSecretReference):
        store.upsert(_trigger("another-auto", url=theirs))
    key = secret_refs.ref_key(theirs)
    assert not secret_refs.trigger_owner("another-auto").holds(key)
    assert not secret_refs.app_owner(APP).holds(key)
    assert not secret_refs.provider_owner("openai").holds(key)


def test_a_boot_moves_what_an_earlier_release_left_in_the_file(home, action):
    """🔴 Red before: nothing moved an automation's plain value, at boot or ever."""
    from personalclaw.dashboard.server import _move_stored_settings
    from personalclaw.triggers.secrets import resolve_for

    gone = {"provider": "an-uninstalled-action", "config": {"url": URL + "/other"}}
    (Path(home) / "triggers.json").write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": [
                    {**_trigger(url=URL, headers=HEADERS).to_dict()},
                    {**_trigger("orphan").to_dict(), "workflow": {"inline": gone}},
                ],
            }
        )
    )

    _move_stored_settings()  # the gateway's start

    text = (Path(home) / "triggers.json").read_text()
    assert f'"{URL}"' not in text and TOKEN not in text
    assert URL + "/other" in text, "an automation no provider here answers to is left as it was"
    config = _config_on_disk(home)
    assert resolve_for(action, config, trigger_id="fixture-auto")[0]["url"] == URL


def test_headers_given_as_an_object_are_held_as_their_json_text(home, action):
    """🔴 Red before: the store holds text, so ``headers`` saved as an object (the API or an
    agent's tool, which an action takes as well as JSON text) stayed in triggers.json as typed."""
    from personalclaw.config import secret_refs
    from personalclaw.triggers.secrets import resolve_for

    given = {"Authorization": f"Bearer {TOKEN}"}
    stored = _store(home).upsert(_trigger(url=URL, headers=given))

    assert secret_refs.is_trigger_key(secret_refs.ref_key(_config_on_disk(home)["headers"]))
    assert _anywhere(home, TOKEN) == [".env"]
    handed, filled = resolve_for(action, stored.workflow["inline"]["config"], trigger_id=stored.id)
    assert json.loads(handed["headers"]) == given, "the action is handed the same headers"
    assert handed["headers"] in filled


def test_a_boot_moves_headers_an_earlier_release_saved_as_an_object(home, action):
    """🔴 Red before: the boot move skipped a value that was not text."""
    from personalclaw.dashboard.server import _move_stored_settings
    from personalclaw.triggers.secrets import resolve_for

    given = {"Authorization": f"Bearer {TOKEN}"}
    (Path(home) / "triggers.json").write_text(
        json.dumps({"version": 1, "triggers": [_trigger(url=URL, headers=given).to_dict()]})
    )

    _move_stored_settings()  # the gateway's start

    assert TOKEN not in (Path(home) / "triggers.json").read_text()
    handed, _ = resolve_for(action, _config_on_disk(home), trigger_id="fixture-auto")
    assert json.loads(handed["headers"]) == given


def _granted(home, **config):
    """An automation saved with *config*, its action allowed by the owner (`grants.give`)."""
    from personalclaw.triggers import grants

    store = _store(home)
    trigger = store.upsert(_trigger(**config))
    grants.give(trigger)
    return store, store.upsert(trigger)


def _row_config(store, tid: str = "fixture-auto") -> dict:
    from personalclaw.dashboard.handlers.triggers import _serialize_store

    return _serialize_store(store.get(tid))["action"]["config"]


def test_a_held_web_address_reads_as_where_it_goes_and_nothing_else(home, action):
    """🔴 Red before: a held URL read as the mask alone, so an owner asked to allow an edit could
    not see where it now posts. Its scheme, host and port show; its login, path and query never
    do, and a header stays masked whole."""
    from personalclaw.apps.secret_fields import SECRET_MASK

    store = _store(home)
    store.upsert(
        _trigger(url="https://ada:pw-9c8b@hooks.example.com:8443/T0/B0/x?k=v", headers=HEADERS)
    )
    shown = _row_config(store)
    assert shown["url"] == f"https://hooks.example.com:8443/{SECRET_MASK}"
    assert shown["headers"] == SECRET_MASK
    assert "ada" not in json.dumps(shown) and "T0" not in json.dumps(shown)


def test_a_lookalike_host_reads_in_ascii(home, action):
    """🔴 Red before: masked whole, there was no host to read. Shown, a name drawn in another script
    to look like a known one reads as the ASCII name it is."""
    store = _store(home)
    lookalike = "ex\u0430mple.com"  # Cyrillic a
    store.upsert(_trigger(url=f"https://{lookalike}/hook"))
    shown = _row_config(store)["url"]
    assert shown.startswith("https://" + lookalike.encode("idna").decode("ascii") + "/")
    assert lookalike not in shown and shown.startswith("https://xn--")


def test_the_mask_as_shown_saves_back_with_no_question_and_keeps_the_value(home, action):
    """🔴 Red before: the editor sent the mask back, its consent check read that as a new value, and
    a save that changed nothing asked the owner to allow "the changed action" again."""
    from personalclaw.dashboard.handlers import trigger_consent
    from personalclaw.dashboard.handlers import triggers as H
    from personalclaw.triggers.held_secrets import fill

    store, _ = _granted(home, url=URL, headers=HEADERS)
    body = H._keep_masked_trigger(
        None,
        H._SCHEDULE,
        "fixture-auto",
        {"action": {"provider": ACTION, "config": _row_config(store)}},
    )
    saving = H._saving_action(None, H._SCHEDULE, "fixture-auto", body)
    asked, _candidate = trigger_consent._grant_for_save(
        None, body, kind=H._SCHEDULE, raw="fixture-auto", saving=saving
    )
    assert asked is None, asked.sentence
    store.upsert(_trigger(**saving["config"]))
    assert fill("fixture-auto", _config_on_disk(home))[0]["url"] == URL


def test_a_host_edited_inside_the_mask_is_refused(home, action):
    """🔴 Red before: the mask was only ever whole. Now that a read shows the host, an edit of the
    host inside the mask must not keep the hidden path and send it elsewhere."""
    from personalclaw.apps.secret_fields import SECRET_MASK

    store = _store(home)
    store.upsert(_trigger(url=URL))
    before = (Path(home) / "triggers.json").read_text()
    with pytest.raises(ValueError, match="part of a value kept hidden was changed"):
        store.upsert(_trigger(url=f"https://elsewhere.example/{SECRET_MASK}"))
    assert (Path(home) / "triggers.json").read_text() == before


def test_an_agents_repoint_is_asked_about_naming_both_hosts(home, action):
    """🔴 Red before: the agent's edit was switched off and waited for the owner, whose question
    named the action alone while the editor showed the address masked: she allowed it blind."""
    from personalclaw.triggers import grants
    from personalclaw.triggers import tools as T

    store, _ = _granted(home, url=URL, headers=HEADERS)
    moved = T.update(
        store,
        trigger_id="fixture-auto",
        patch={
            "workflow": {
                "inline": {"provider": ACTION, "config": {"url": "https://other.example/in"}}
            }
        },
    )
    assert "switched off" in moved.text, moved.text
    stored = store.get("fixture-auto").trigger
    asked = grants.asking(stored, grants.missing(stored))
    assert (
        "now goes to https://other.example, not https://example.com" in asked.sentence
    ), asked.sentence


def test_an_action_that_starts_a_run_or_a_model_turn_is_left_alone(home, action):
    """Its config is the run's or the turn's, which keeps references by name and fills them where
    a step or a tool uses them: filling or moving it here would put the value in their record."""
    action.hands_config_to_a_run = True
    _store(home).upsert(_trigger(url=URL))
    assert _config_on_disk(home)["url"] == URL


def test_the_automation_form_gets_an_actions_schema_from_the_app_that_registered_it(action):
    """🔴 Red before: the schema was looked up as ``<action>-action``, so an action named apart
    from its app (A2A Agent Call's ``a2a-call``) had a form with no fields."""
    from unittest.mock import MagicMock, patch

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers.hooks import api_action_providers

    ext = MagicMock()
    ext.name = APP
    ext.provider_config.type = "action"
    ext.provider_config.settingsSchema = SCHEMA
    registry = MagicMock()
    registry.list_extensions.return_value = [ext]
    with patch("personalclaw.providers.registry.get_provider_registry", return_value=registry):
        resp = asyncio.run(api_action_providers(make_mocked_request("GET", "/")))
    rows = {p["name"]: p for p in json.loads(resp.body)["providers"]}
    assert rows[ACTION]["settingsSchema"] == SCHEMA
