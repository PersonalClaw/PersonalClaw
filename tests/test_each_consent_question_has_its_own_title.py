"""Each question the owner is asked is headed with what it asks.

Measured on `main` (ddc21e05f) before any of this was written: the gateway's consent question
carried a sentence and no heading, and the SPA's one dialog headed every question "Loosen a
security setting?" — a plain grant for what a trigger runs included, where nothing is loosened.
The heading is product copy the owner reads before the sentence, so it is the gateway's, like the
sentence: `http_errors.consent_required` takes it from each caller, and a question with two
halves (a grant and a loosened posture) says both.
"""

from __future__ import annotations

import asyncio
import copy
import json
import types

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.config.loader as loader
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.hooks import ScriptHookStore
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

_BASH = {"inline": {"provider": "bash", "config": {"command": "touch /tmp/ran"}}}
_AGENT = {"inline": {"provider": "invoke-agent", "config": {"task_template": "check the build"}}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    hooks = ScriptHookStore(config_dir=tmp_path)
    monkeypatch.setattr(T, "_hook_store", lambda _state: hooks)
    monkeypatch.setattr(T, "_used_by_index", lambda: {})
    return tmp_path


def _req(method: str, path: str, *, body: dict, match_info: dict, headers: dict | None = None):
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None, _background_tasks=set())
    req = make_mocked_request(method, path, match_info=match_info, app=app, headers=headers)
    req["user"] = "owner"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    return req


def _schedule(home, tid="nightly", *, workflow=_BASH, granted=True, enabled=True):
    trigger = Trigger(
        id=tid,
        name=f"Job {tid}",
        kind="clock",
        enabled=enabled,
        created_by="user",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        workflow=copy.deepcopy(workflow),
        next_fire_at="2030-01-01T09:00:00+00:00" if enabled else "",
    )
    if granted:
        grants.give(trigger)
    TriggerStore(base_dir=home).upsert(trigger)


def _asked(resp: web.Response) -> dict:
    """The question a ``400 confirmation_required`` carries."""
    body = json.loads(resp.body.decode())
    assert resp.status == 400, body
    assert body["error"]["code"] == "confirmation_required", body
    return body["error"]["detail"]


def _create(**body) -> web.Response:
    return asyncio.run(
        T.api_trigger_create(_req("POST", "/api/triggers", body=body, match_info={}))
    )


def _edit(trigger_id: str, **body) -> web.Response:
    kind, raw = T._split_id(trigger_id)
    listed = T._schedule_row_for(types.SimpleNamespace(), T._trigger_store().get(raw))
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


def _toggle(trigger_id: str, **body) -> web.Response:
    return asyncio.run(
        T.api_trigger_toggle(
            _req(
                "POST",
                f"/api/triggers/{trigger_id}/toggle",
                body=body,
                match_info={"id": trigger_id},
            )
        )
    )


def _new(action: dict) -> dict:
    return {"trigger_type": "schedule", "name": "Cleanup", "cron": "0 3 * * *", "action": action}


def test_creating_a_trigger_that_needs_a_grant_is_headed_as_that(home):
    """🔴 Red on main: the question had no heading, so the dialog said "Loosen a security
    setting?" over "Creating “Cleanup” allows it to use the “Bash Command” action"."""
    asked = _asked(_create(**_new({"provider": "bash", "config": {"command": "date"}})))

    assert asked["title"] == "Allow what this trigger runs?"
    assert asked["consent"].startswith("Creating “Cleanup” allows it")


def test_an_edit_is_headed_by_what_it_changes(home):
    """🔴 Red on main. A new command for an allowed provider and a provider the trigger was never
    allowed are two questions, and each says which."""
    _schedule(home, "nightly")
    _schedule(home, "digest", workflow={"inline": {"provider": "notify", "config": {}}})

    changed = _asked(
        _edit("schedule:nightly", action={"provider": "bash", "config": {"command": "rm x"}})
    )
    new = _asked(_edit("schedule:digest", action={"provider": "bash", "config": {"command": "d"}}))

    assert changed["title"] == "Allow the changed action?"
    assert new["title"] == "Allow the new action?"


def test_switching_a_trigger_on_is_headed_as_that(home):
    """🔴 Red on main."""
    _schedule(home, "nightly", granted=False, enabled=False)

    assert (
        _asked(_toggle("schedule:nightly", enabled=True))["title"] == "Allow this trigger to run?"
    )


def test_a_loosened_posture_alone_keeps_its_own_heading(home):
    """The floor, and the one question the old heading was true of: letting an allowed agent
    approve its own tool calls asks nothing about the grant, which the posture keys are no part
    of (`grants._runs`)."""
    _schedule(home, "brief", workflow=_AGENT)
    auto = {"provider": "invoke-agent", "config": {"task_template": "check the build"}}
    auto["config"]["approval_mode"] = "auto"

    asked = _asked(_edit("schedule:brief", action=auto))

    assert asked["title"] == "Loosen a security setting?"
    assert "approve its own tool calls" in asked["consent"]
    assert asked["change"] == "Asks you → Approves its own calls"


def test_a_question_with_two_halves_is_headed_with_both(home):
    """🔴 Red on main: one Allow agrees to the grant AND the loosened posture, so the heading
    says both rather than naming one and hiding the other."""
    auto = {"provider": "invoke-agent", "config": {"task_template": "x", "approval_mode": "auto"}}

    asked = _asked(_create(**_new(auto)))

    assert asked["title"] == "Allow what it runs, and loosen a security setting?"
    assert "“Invoke Agent”" in asked["consent"] and "approve its own tool calls" in asked["consent"]
    # The loosened half says what it changes, after the sentences — its own is the last of them.
    assert asked["change"] == "Asks you → Approves its own calls"


def test_every_caller_names_its_question():
    """The heading is required of every caller, so a new question cannot fall back to a heading
    written for another one."""
    import inspect

    from personalclaw.http_errors import consent_required

    parameter = inspect.signature(consent_required).parameters["title"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    detail = json.loads(consent_required("f", "a sentence", title="A heading?").body)["error"]
    assert detail["detail"] == {"field": "f", "consent": "a sentence", "title": "A heading?"}
