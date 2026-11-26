"""A notify or create-task automation with no title to make is refused where it is saved, as the
other actions no fire could run are, instead of being accepted and failing at every fire.

🔴 The defect (Backlog: "A Notify automation saved with no title_template is accepted, then fails at
every fire"). Found on the prototype: `POST /api/triggers` took an event automation whose action
was `{"provider": "notify", "config": {"title": "Memory changed", "message": "..."}}`, keys the
notify action does not read, and answered `ok`, health `ok`. Every fire then failed with "notify
hook is missing 'title_template'", each counting toward autopause, and nothing at save had said so.
`create-task` renders its task's title from `title_template` the same way.

Now each judges its own config where it is saved (`notify_provider.config_problem`,
`create_task_provider.config_problem`, both `template.title_template_problem`), asked by the
Triggers page's create and edit (`_action_problem`) and by the chat's and the CLI's
(`triggers.tools`): no `title_template`, or one with no words, is refused in words that name the
key, and a config that set `title` is told the key the action reads. Driven through the real
handlers and the real tools over a scratch store.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

#: What the prototype's save took: the keys a person would guess, none of them the one it reads.
GUESSED = {"title": "Memory changed", "message": "A memory was written"}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.handlers import triggers as T

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr("personalclaw.triggers.boot_migrate.config_dir", lambda: tmp_path)
    return tmp_path


def _request(method: str, path: str, body: dict, match_info: dict) -> web.Request:
    app = web.Application()
    state = MagicMock()
    state.crons.list_jobs.return_value = []
    state._hook_store = None
    state._background_tasks = set()
    app["state"] = state
    req = make_mocked_request(method, path, match_info=match_info, app=app)
    req["user"] = "owner"

    async def _json() -> dict:
        return body

    req.json = _json  # type: ignore[method-assign]
    return req


def _answer(resp: web.StreamResponse) -> dict[str, Any]:
    assert isinstance(resp, web.Response)
    return json.loads(resp.body.decode())  # type: ignore[union-attr]


def _create(provider: str, config: dict) -> web.Response:
    from personalclaw.dashboard.handlers import triggers as T

    body = {
        "trigger_type": "schedule",
        "name": "Morning note",
        "cron": "0 9 * * *",
        "action": {"provider": provider, "config": config},
    }
    resp = asyncio.run(T.api_trigger_create(_request("POST", "/api/triggers", body, {})))
    assert isinstance(resp, web.Response)
    return resp


def _refusal(resp: web.Response) -> str:
    assert resp.status == 400, _answer(resp)
    error = _answer(resp)["error"]
    assert error["code"] == "invalid_request", error
    return str(error["message"])


def _stored(home: Path) -> list[Trigger]:
    return [row.trigger for row in TriggerStore(base_dir=home).load()]


# ── the Triggers page's save ──


def test_a_notify_saved_with_keys_it_does_not_read_is_refused_naming_the_one_it_reads(
    home: Path,
) -> None:
    """🔴 Before: saved, health ok, and every fire failed."""
    said = _refusal(_create("notify", GUESSED))
    assert "title_template, not title" in said, said
    assert _stored(home) == [], "refused at the save means nothing was written"


@pytest.mark.parametrize(
    ("provider", "what"), [("notify", "A notification"), ("create-task", "A task")]
)
@pytest.mark.parametrize("config", [{}, {"title_template": "   "}, {"title_template": 7}])
def test_an_action_with_no_title_to_make_is_refused(
    home: Path, provider: str, what: str, config: dict
) -> None:
    """🔴 Before: no `title_template`, one of blanks, or one that is no text was saved."""
    said = _refusal(_create(provider, config))
    assert said.startswith(f"{what} needs a title: set title_template"), said
    assert _stored(home) == []


@pytest.mark.parametrize("provider", ["notify", "create-task"])
def test_a_titled_action_is_saved(home: Path, provider: str) -> None:
    """The control: a title the action can render is saved as before."""
    resp = _create(provider, {"title_template": "Standup in 15 minutes"})
    assert resp.status < 300, _answer(resp)
    assert len(_stored(home)) == 1


def test_an_edit_that_takes_the_title_away_is_refused(home: Path) -> None:
    """The edit is asked of the action as it will be saved, and the stored row stays as it was."""
    from personalclaw.dashboard.handlers import triggers as T

    TriggerStore(base_dir=home).upsert(
        Trigger(
            id="morning",
            name="Morning note",
            kind="clock",
            enabled=True,
            created_by="user",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow={"inline": {"provider": "notify", "config": {"title_template": "Morning"}}},
            capabilities={"providers": ["notify"]},
        )
    )
    body = {"action": {"provider": "notify", "config": {"title_template": ""}}}
    resp = asyncio.run(
        T.api_trigger_detail(
            _request("PUT", "/api/triggers/schedule:morning", body, {"id": "schedule:morning"})
        )
    )
    assert isinstance(resp, web.Response)
    assert _refusal(resp).startswith("A notification needs a title"), _answer(resp)
    (kept,) = _stored(home)
    assert kept.workflow["inline"]["config"]["title_template"] == "Morning"


# ── the chat's and the CLI's save ──


def _tools_create(store: TriggerStore, config: dict) -> Any:
    from personalclaw.triggers import tools

    return tools.create(
        store,
        name="Morning note",
        kind="clock",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        workflow={"inline": {"provider": "notify", "config": config}},
        created_by="user",
    )


def test_the_chat_and_the_cli_refuse_it_too(tmp_path: Path) -> None:
    """🔴 Before: `automation_create` and `automation_update` saved it as the page did."""
    from personalclaw.triggers import tools

    store = TriggerStore(base_dir=tmp_path)
    refused = _tools_create(store, GUESSED)
    assert refused.ok is False and "title_template, not title" in refused.text, refused.text
    assert store.list_triggers() == []

    created = _tools_create(store, {"title_template": "Morning"})
    assert created.ok is True, created.text
    trigger_id = created.data["trigger"]["id"]
    edited = tools.update(
        store,
        trigger_id=trigger_id,
        patch={"workflow": {"inline": {"provider": "notify", "config": {"title_template": ""}}}},
    )
    assert edited.ok is False and "A notification needs a title" in edited.text, edited.text
    row = store.get(trigger_id)
    assert row is not None
    assert row.trigger.workflow["inline"]["config"]["title_template"] == "Morning"


# ── what is refused is what every fire refuses ──


@pytest.mark.parametrize("provider", ["notify", "create-task"])
def test_what_the_save_refuses_every_fire_refuses(provider: str) -> None:
    """The save's question is the run's: a config it refuses is one the action cannot run, so
    nothing it refuses could ever have fired."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.registry import get_action_provider

    action = get_action_provider(provider)
    assert action is not None
    result = asyncio.run(action.execute(GUESSED, ActionContext(event="manual")))
    assert result.success is False and "title_template" in (result.error or ""), result
