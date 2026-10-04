"""An edit of an automation changes the settings it sends, and nothing else.

Measured before the rule (`triggers.action_edit`): the Triggers page's schedule editor builds an
Invoke Agent action from the fields it draws (its prompt, agent, model, approval and working
folder), and every door that edits an automation replaced the stored action with what it was sent.
Moving such an automation's time therefore dropped the files its agent may change, its capability
and its turn cap, and it ran read-only from then on; the save even asked the owner to allow "the
changed action", for a change she never made. The chat's `automation_update` dropped the same
settings when it changed a prompt, and a lifecycle trigger's editor dropped every setting its form
did not send.

Now one rule holds at every door: a setting an edit sends replaces the saved one, a setting sent as
null is removed, a setting it does not send stays as saved, and naming another provider replaces
the action.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import types
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.config.loader as loader
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.hooks import ScriptHookStore
from personalclaw.triggers import grants
from personalclaw.triggers import tools as Tools
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

#: An Invoke Agent automation's settings, the three the schedule editor never drew among them.
_SETTINGS = {
    "task_template": "Tidy the kitchen note from the new receipts.",
    "agent": "researcher",
    "model": "fixture-model",
    "writes": ["~/Notes/kitchen.md"],
    "capability": "mutating",
    "max_turns": 7,
}
#: The settings the schedule editor drew before it drew those three.
_DRAWN = ("task_template", "agent", "model", "approval_mode", "cwd")


def _agent(**overrides) -> dict:
    return {"provider": "invoke-agent", "config": {**_SETTINGS, **overrides}}


# ── harness ──


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    hooks = ScriptHookStore(config_dir=tmp_path)
    monkeypatch.setattr(T, "_hook_store", lambda _state: hooks)
    monkeypatch.setattr(T, "_used_by_index", lambda: {})
    return tmp_path


def _store(home: Path) -> TriggerStore:
    return TriggerStore(base_dir=home)


def _granted(workflow: dict) -> dict:
    probe = Trigger(id="", name="", kind="clock", workflow=copy.deepcopy(workflow))
    grants.give(probe)
    return probe.capabilities


def _schedule(home: Path, *, workflow: dict, tid: str = "kitchen") -> None:
    """An Invoke Agent automation the owner made and allowed, every weekday at nine."""
    _store(home).upsert(
        Trigger(
            id=tid,
            name="Kitchen note",
            kind="clock",
            enabled=True,
            created_by="user",
            spec={"kind": "cron", "expr": "0 9 * * 1-5"},
            workflow=copy.deepcopy(workflow),
            capabilities=_granted(workflow),
            next_fire_at="2030-01-01T09:00:00+00:00",
        )
    )


def _row(home: Path, tid: str = "kitchen") -> Trigger:
    loaded = _store(home).get(tid)
    assert loaded is not None
    return loaded.trigger


def _config(home: Path, tid: str = "kitchen") -> dict:
    workflow = _row(home, tid).workflow
    inline = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else workflow
    return dict(inline.get("config") or {})


def _req(method: str, path: str, *, body: dict, match_info: dict, headers: dict | None = None):
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None, _background_tasks=set())
    req = make_mocked_request(method, path, match_info=match_info, app=app, headers=headers)
    req["user"] = "owner"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    return req


def _body(resp: web.Response) -> dict:
    return json.loads(resp.body.decode())


def _edit(trigger_id: str, body: dict) -> web.Response:
    """A save from the Triggers page's editor, naming the revision it read, as the page does."""
    kind, raw = T._split_id(trigger_id)
    state = types.SimpleNamespace()
    if kind == "lifecycle":
        listed = T._serialize_lifecycle(T._hook_store(state).get(raw), [])
    else:
        listed = T._schedule_row_for(state, T._trigger_store().get(raw))
    return asyncio.run(
        T.api_trigger_detail(
            _req(
                "PUT",
                f"/api/triggers/{trigger_id}",
                body=body,
                match_info={"id": trigger_id},
                headers={"If-Match": f'"{listed["revision"]}"'},
            )
        )
    )


def _form_save(**changes) -> dict:
    """A save from the schedule editor that draws only `_DRAWN`: each as it was read, an empty one
    sent as cleared (null)."""
    config = {key: _SETTINGS.get(key) for key in _DRAWN}
    body = {
        "name": "Kitchen note",
        "timezone": "",
        "silent": False,
        "strict_schedule": False,
        "channel": "",
        "skip_dates": [],
        "failure_delivery": "inbox",
        "failure_dedupe": False,
        "catch_up": False,
        "cron": "0 9 * * 1-5",
        "action": {"provider": "invoke-agent", "config": config},
    }
    body.update(changes)
    return body


# ── the Triggers page ──


def test_moving_its_time_keeps_what_its_agent_may_do(home):
    """🔴 The finding: the files it may change, its capability and its turn cap were dropped by a
    save that moved only its time (the owner had said yes to "the changed action")."""
    _schedule(home, workflow={"inline": _agent()})

    resp = _edit("schedule:kitchen", _form_save(cron="30 7 * * 1-5", confirm=True))

    assert resp.status == 200, _body(resp)
    assert _row(home).spec["expr"] == "30 7 * * 1-5"
    assert _config(home) == _SETTINGS
    assert _row(home).enabled is True
    assert grants.missing(_row(home)) == []


def test_moving_its_time_asks_nothing(home):
    """Nothing it runs changed, so there is nothing to allow: the save asked the owner to allow
    "the changed action" because the action it built had lost three settings."""
    _schedule(home, workflow={"inline": _agent()})

    resp = _edit("schedule:kitchen", _form_save(cron="30 7 * * 1-5"))

    assert resp.status == 200, _body(resp)
    assert _config(home) == _SETTINGS


def test_a_setting_the_editor_cleared_is_removed(home):
    _schedule(home, workflow={"inline": _agent()})
    action = {"provider": "invoke-agent", "config": {"max_turns": None, "writes": None}}

    resp = _edit("schedule:kitchen", _form_save(action=action, confirm=True))

    assert resp.status == 200, _body(resp)
    config = _config(home)
    assert "max_turns" not in config and "writes" not in config
    assert config == {k: v for k, v in _SETTINGS.items() if k not in ("max_turns", "writes")}


def test_a_changed_setting_replaces_the_saved_one_whole(home):
    """A list is one setting: the files it may change are the ones the editor sent."""
    _schedule(home, workflow={"inline": _agent()})
    action = {"provider": "invoke-agent", "config": {"writes": ["~/Notes/pantry.md"]}}

    resp = _edit("schedule:kitchen", _form_save(action=action, confirm=True))

    assert resp.status == 200, _body(resp)
    assert _config(home) == {**_SETTINGS, "writes": ["~/Notes/pantry.md"]}


def test_naming_another_provider_replaces_the_action(home):
    _schedule(home, workflow={"inline": _agent()})
    action = {"provider": "notify", "config": {"title_template": "Receipts are in"}}

    resp = _edit("schedule:kitchen", _form_save(action=action, confirm=True))

    assert resp.status == 200, _body(resp)
    assert _row(home).workflow == {"inline": action}


def test_a_config_that_is_not_an_object_is_refused(home):
    _schedule(home, workflow={"inline": _agent()})

    resp = _edit(
        "schedule:kitchen",
        _form_save(action={"provider": "invoke-agent", "config": "max_turns=3"}, confirm=True),
    )

    assert resp.status == 400
    assert _body(resp)["error"]["code"] == "invalid_request"
    assert _config(home) == _SETTINGS


def test_an_edit_of_an_action_the_chat_saved_flat_is_judged_against_it(home):
    """The chat's tools store an action flat (`{provider, config}`); the editor read only the
    nested shape, so it judged an edit against no action at all, and asked the owner to allow an
    approval mode that had not changed."""
    _schedule(home, workflow=_agent(approval_mode="auto"))
    form = _form_save()
    form["action"]["config"]["approval_mode"] = "auto"

    resp = _edit("schedule:kitchen", form)

    assert resp.status == 200, _body(resp)
    assert _config(home) == {**_SETTINGS, "approval_mode": "auto"}
    assert "inline" not in _row(home).workflow


def test_a_new_automation_saves_as_it_did(home):
    """The positive control: creating one stores the action it was sent, every setting in it."""
    resp = asyncio.run(
        T.api_trigger_create(
            _req(
                "POST",
                "/api/triggers",
                body={
                    "trigger_type": "schedule",
                    "name": "Kitchen note",
                    "cron": "0 9 * * 1-5",
                    "action": _agent(),
                    "confirm": True,
                },
                match_info={},
            )
        )
    )

    assert resp.status == 200, _body(resp)
    raw = _body(resp)["trigger"]["raw_id"]
    assert _row(home, raw).workflow == {"inline": _agent()}


def test_a_lifecycle_edit_keeps_the_settings_it_does_not_send(home):
    hooks = T._hook_store(None)
    hook = hooks.create(
        {
            "name": "Review after a run",
            "event": "Stop",
            "provider": "invoke-agent",
            "provider_config": dict(_SETTINGS),
        }
    )
    grants.give(hook)
    hooks.update(hook.id, {"capabilities": hook.capabilities})
    sent = {"task_template": "Review what changed.", "max_turns": None}

    resp = _edit(
        f"lifecycle:{hook.id}",
        {"action": {"provider": "invoke-agent", "config": sent}, "confirm": True},
    )

    assert resp.status == 200, _body(resp)
    stored = hooks.get(hook.id).provider_config
    expected = {k: v for k, v in _SETTINGS.items() if k != "max_turns"}
    assert stored == {**expected, "task_template": "Review what changed."}


# ── the chat's automation_update and the CLI's cron update ──


def test_the_chat_changing_its_prompt_keeps_the_rest(home):
    _schedule(home, workflow={"inline": _agent()})
    edit = {"inline": {"provider": "invoke-agent", "config": {"task_template": "Do the pantry."}}}

    result = Tools.update(_store(home), trigger_id="kitchen", patch={"workflow": edit})

    assert result.ok, result.text
    assert _config(home) == {**_SETTINGS, "task_template": "Do the pantry."}


def test_the_chat_removes_a_setting_by_sending_null(home):
    _schedule(home, workflow={"inline": _agent()})
    edit = {"inline": {"config": {"max_turns": None}}}

    result = Tools.update(_store(home), trigger_id="kitchen", patch={"workflow": edit})

    assert result.ok, result.text
    assert _config(home) == {k: v for k, v in _SETTINGS.items() if k != "max_turns"}
    assert _row(home).workflow["inline"]["provider"] == "invoke-agent"


def test_the_chat_cannot_save_a_config_that_is_not_an_object(home):
    _schedule(home, workflow={"inline": _agent()})

    result = Tools.update(
        _store(home), trigger_id="kitchen", patch={"workflow": {"inline": {"config": ["x"]}}}
    )

    assert not result.ok
    assert "action.config must be an object" in result.text
    assert _config(home) == _SETTINGS


class TestTheCli:
    @pytest.fixture(autouse=True)
    def _cli(self, home, monkeypatch):
        from unittest.mock import MagicMock

        monkeypatch.setattr("personalclaw.cli_commands.config_dir", lambda: home)
        monkeypatch.setattr("personalclaw.cli_commands.sel", lambda: MagicMock())

    @staticmethod
    def _cron(**args):
        from personalclaw.cli_commands import _cron

        base = {
            "name": None,
            "message": None,
            "every": None,
            "every_secs": None,
            "cron_expr": None,
            "channel": None,
            "approval_mode": None,
            "yes": True,
        }
        _cron(argparse.Namespace(**{**base, **args}))

    def test_a_new_message_keeps_what_its_agent_may_do(self, home):
        """The chat's tools store an action flat; the CLI's own merge read only the nested shape,
        so the action it saved had the new message and none of the other settings."""
        _schedule(home, workflow=_agent())

        self._cron(cron_action="update", job_id="kitchen", message="Do the pantry.")

        assert _config(home) == {**_SETTINGS, "task_template": "Do the pantry."}

    def test_default_approval_clears_it(self, home):
        _schedule(home, workflow={"inline": _agent(approval_mode="auto")})

        self._cron(cron_action="update", job_id="kitchen", approval_mode="default")

        assert _config(home) == _SETTINGS


# ── the rule itself ──


def test_the_rule_reads_both_stored_shapes_and_keeps_each():
    from personalclaw.triggers.action_edit import edited_workflow

    flat = _agent()
    nested = {"inline": _agent()}
    edit = {"inline": {"config": {"model": "other-model"}}}

    assert edited_workflow(flat, edit) == _agent(model="other-model")
    assert edited_workflow(nested, edit) == {"inline": _agent(model="other-model")}


def test_a_resume_target_is_no_edit_of_an_action():
    from personalclaw.triggers.action_edit import edited_workflow

    target = {"resume": {"run_id": "run-1"}}
    assert edited_workflow({"inline": _agent()}, target) == target


def test_config_null_clears_every_setting_and_a_new_provider_starts_from_none():
    from personalclaw.triggers.action_edit import edited_action

    assert edited_action(_agent(), {"config": None}) == {"provider": "invoke-agent", "config": {}}
    assert edited_action(_agent(), {"provider": "bash", "config": {"command": "true"}}) == {
        "provider": "bash",
        "config": {"command": "true"},
    }
