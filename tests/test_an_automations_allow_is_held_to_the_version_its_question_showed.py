"""An automation's Allow is a yes to the version of its workflow its question showed.

The question an Allow answers names the version of the workflow the automation runs ("It runs
“weekly-report” as it is when you allow it (version 1 now)"), and the yes is recorded as a yes to
that version. Recorded as whatever was there when the yes arrived, a save that landed between the
question and the owner's answer — an agent's tool, an import, another machine's sync — was the
version the yes allowed, though nobody had shown it to her.

So the question carries what it showed (``shown``: the workflow's version and digest, and each
workflow it runs as a step at theirs), the yes sends it back, and the Allow records exactly that. A
yes whose workflow, or a workflow it runs as a step, has moved since is refused with the answer
"Use vN" gives a version saved after its owner looked (``409 stale_write``, "Nothing was
changed: …"), and asks again: the refusal carries the question as it is now, which names who
saved each version since the one she was shown. A yes that does not say what it was shown allows
nothing.

Driven through every door an Allow takes: the Triggers page's switch and Allow, the create dialog,
the editor, a lifecycle trigger's create, and the CLI, which has no way to say which version it was
shown and so sends the owner to the Triggers page.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider
from personalclaw.config import loader as config_loader
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.hooks import ScriptHookStore
from personalclaw.stale_write import revision_of
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import handlers as H
from personalclaw.workflows import service
from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

pytestmark = pytest.mark.anyio

NAME = "weekly-report"
PART = "report-part"
OTHER = "monthly-report"
TRIGGER_ID = "manual:weekly-report"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Engine:
    """The workflow supervisor, recording what each launch was handed instead of running it."""

    def __init__(self) -> None:
        self.launched: list[tuple[Any, dict[str, Any]]] = []

    async def launch(self, run: Any, spec: dict[str, Any]) -> None:
        self.launched.append((run, copy.deepcopy(spec)))


class _State:
    def push_refresh(self, *_args: Any) -> None:
        return None


@pytest.fixture
def engine(monkeypatch) -> _Engine:
    engine = _Engine()
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=engine),
    )
    return engine


@pytest.fixture(autouse=True)
def _one_home(monkeypatch):
    """The Triggers page's stores read the home every other door here reads."""
    monkeypatch.setattr(T, "config_dir", config_loader.config_dir)
    hooks = ScriptHookStore(config_dir=config_loader.config_dir())
    monkeypatch.setattr(T, "_hook_store", lambda _state: hooks)
    monkeypatch.setattr(T, "_used_by_index", lambda: {})


@pytest.fixture(autouse=True)
def _native_store():
    before = defs_mod.get_provider("native")
    defs_mod.register_provider(NativeWorkflowDefProvider())
    yield
    defs_mod.unregister_provider("native")
    if before is not None:
        defs_mod.register_provider(before)


def _root(said: str) -> dict[str, Any]:
    """A workflow whose one step says *said*: what tells one version from another."""
    return {
        "kind": "sequence",
        "id": "main",
        "children": [{"kind": "transform", "id": "tail", "config": {"expr": said}}],
    }


def _runs_the_part(said: str) -> dict[str, Any]:
    """A workflow that runs the part as a step, after a step that says *said*."""
    return {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "head", "config": {"expr": said}},
            {"kind": "subworkflow", "id": "part", "config": {"ref": PART, "inputs": {}}},
        ],
    }


def _request(
    method: str,
    path: str,
    body: dict[str, Any],
    *,
    match: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> web.Request:
    """A request the owner's signed-in session makes."""
    app = web.Application()
    app["state"] = _State()
    request = make_mocked_request(
        method, path, headers=headers or {}, app=app, match_info=match or {}
    )
    request["user"] = "owner"

    async def _json() -> dict[str, Any]:
        return body

    request.json = _json  # type: ignore[method-assign]
    return request


def _reply(response: web.StreamResponse) -> dict[str, Any]:
    return json.loads(response.body.decode())  # type: ignore[attr-defined]


async def _owner_saves(name: str, root: dict[str, Any]) -> None:
    """Save *name* from its editor: the owner's door, naming the revision it edited."""
    claim: dict[str, str] = {}
    stored = await service.get_def(name)
    if stored.get("ok"):
        claim["If-Match"] = revision_of(stored["definition"])
    body = {"name": name, "root": root}
    saved = _reply(await H.api_def_save(_request("POST", "/api/workflows", body, headers=claim)))
    assert saved.get("saved"), saved


def _agent_saves(name: str, root: dict[str, Any]) -> None:
    """Save *name* with the agent's own tool, ``workflow_author``."""
    from personalclaw.mcp_workflows import _call_tool

    answer = _call_tool("workflow_author", {"name": name, "root": json.dumps(root)})
    assert '"saved": true' in str(answer), answer


def _store() -> TriggerStore:
    return TriggerStore(base_dir=config_loader.config_dir())


def _automation(workflow: str = NAME) -> Trigger:
    """A "Run workflow" automation its owner made and has not allowed yet: switched off."""
    trigger = Trigger(
        id=TRIGGER_ID,
        name="Weekly report",
        kind="manual",
        created_by="user",
        enabled=False,
        workflow={"inline": {"provider": "run-workflow", "config": {"workflow": workflow}}},
    )
    _store().upsert(trigger)
    return trigger


def _stored(trigger_id: str = TRIGGER_ID) -> Trigger:
    row = _store().get(trigger_id)
    assert row is not None, f"{trigger_id} is not stored"
    return row.trigger


async def _switch_on(**extra: Any) -> web.StreamResponse:
    """The Triggers page's switch, sent on: Allow, as the page sends it."""
    body = {"enabled": True, **extra}
    return await T.api_trigger_toggle(
        _request(
            "POST",
            f"/api/triggers/store:{TRIGGER_ID}/toggle",
            body,
            match={"id": f"store:{TRIGGER_ID}"},
        )
    )


def _question(response: web.StreamResponse) -> dict[str, Any]:
    """The question a write that needs the owner's yes answers with."""
    assert response.status == 400, _reply(response)
    error = _reply(response)["error"]
    assert error["code"] == "confirmation_required", error
    return error["detail"]


def _asked_again(response: web.StreamResponse) -> tuple[str, dict[str, Any]]:
    """The refusal of a yes whose question showed what is not there now, and the question it asks
    again: ``(message, question)``."""
    assert response.status == 409, _reply(response)
    error = _reply(response)["error"]
    assert error["code"] == "stale_write", error
    return error["message"], error["detail"]


async def _fire(engine: _Engine, trigger_id: str = TRIGGER_ID) -> tuple[int, str]:
    """Fire the automation's action; the version its run executes and what its first step says."""
    result = await RunWorkflowActionProvider().execute(
        {"workflow": NAME}, ActionContext(event="manual", trigger_id=trigger_id)
    )
    assert result.success, result.error
    run, spec = engine.launched[-1]
    return run.spec_version, spec["root"]["children"][0]["config"]["expr"]


# ── the Triggers page's switch and Allow ─────────────────────────────────────────────────────


async def test_an_unchanged_workflows_allow_pins_the_version_its_question_showed(engine):
    await _owner_saves(NAME, _root("the owner's report"))
    _automation()

    asked = _question(await _switch_on())

    assert asked["shown"]["name"] == NAME and asked["shown"]["version"] == 1, asked
    assert "(version 1 now)" in asked["consent"], asked
    allowed = await _switch_on(confirm=True, shown=asked["shown"])
    assert allowed.status == 200, _reply(allowed)
    pinned = grants.allowed_workflow(_stored())
    assert pinned is not None
    assert (pinned.version, pinned.digest) == (1, asked["shown"]["digest"])
    assert _stored().enabled is True

    # What it pinned is what runs, an agent's later save included.
    _agent_saves(NAME, _root("the agent's rewrite"))
    assert await _fire(engine) == (1, "the owner's report")


async def test_an_agents_save_between_the_question_and_the_allow_is_refused(engine):
    """🔴 Before: the yes pinned the version there was when it arrived, the agent's, which the
    question had never shown."""
    await _owner_saves(NAME, _root("the owner's report"))
    _automation()
    asked = _question(await _switch_on())
    _agent_saves(NAME, _root("the agent's rewrite"))

    message, again = _asked_again(await _switch_on(confirm=True, shown=asked["shown"]))

    said = f"“{NAME}” changed after you were asked about version 1: v2 by an agent."
    assert message.startswith(f"Nothing was changed: {said}"), message
    # Asked again, naming who saved since, about the version there is now.
    assert again["consent"].startswith(said), again
    assert "(version 2 now, saved by an agent)" in again["consent"], again
    assert again["shown"]["version"] == 2 and again["title"], again
    # Nothing was pinned, granted or switched on.
    assert grants.allowed_workflow(_stored()) is None
    assert grants.missing(_stored()) == ["run-workflow"]
    assert _stored().enabled is False

    # Her yes to the question she is asked now is held to what it showed.
    allowed = await _switch_on(confirm=True, shown=again["shown"])
    assert allowed.status == 200, _reply(allowed)
    pinned = grants.allowed_workflow(_stored())
    assert pinned is not None and pinned.version == 2
    assert await _fire(engine) == (2, "the agent's rewrite")


async def test_a_step_workflow_saved_between_the_question_and_the_allow_is_refused(engine):
    await _owner_saves(PART, _root("the owner's part"))
    await _owner_saves(NAME, _runs_the_part("the owner's report"))
    _automation()
    asked = _question(await _switch_on())
    assert [(c["name"], c["version"]) for c in asked["shown"]["calls"]] == [(PART, 1)], asked
    _agent_saves(PART, _root("the agent's part"))

    message, again = _asked_again(await _switch_on(confirm=True, shown=asked["shown"]))

    said = f"“{PART}”, which it runs as a step, changed after you were asked about version 1"
    assert said in message and "v2 by an agent" in message, message
    assert again["consent"].startswith(said), again
    assert [(c["name"], c["version"]) for c in again["shown"]["calls"]] == [(PART, 2)], again
    assert grants.allowed_workflow(_stored()) is None

    allowed = await _switch_on(confirm=True, shown=again["shown"])
    assert allowed.status == 200, _reply(allowed)
    pinned = grants.allowed_workflow(_stored())
    assert pinned is not None
    assert [(c.workflow, c.version) for c in pinned.calls] == [(PART, 2)]


async def test_a_yes_that_does_not_say_what_it_was_shown_allows_nothing(engine):
    """A yes sent with no question's ``shown`` names no version, so it pins none: it is asked."""
    await _owner_saves(NAME, _root("the owner's report"))
    _automation()

    message, again = _asked_again(await _switch_on(confirm=True))

    assert message.startswith("Nothing was changed:"), message
    assert again["shown"]["version"] == 1, again
    assert grants.allowed_workflow(_stored()) is None
    assert _stored().enabled is False

    forged = {**again["shown"], "digest": "0" * 64}
    _asked_again(await _switch_on(confirm=True, shown=forged))
    assert grants.allowed_workflow(_stored()) is None


async def test_an_allow_of_an_action_that_runs_no_workflow_asks_nothing_new():
    """The control: an Allow that is not about a workflow carries no ``shown`` and needs none."""
    trigger = Trigger(
        id=TRIGGER_ID,
        name="Weekly report",
        kind="manual",
        created_by="user",
        enabled=False,
        workflow={"inline": {"provider": "bash", "config": {"command": "echo hello"}}},
    )
    _store().upsert(trigger)

    asked = _question(await _switch_on())
    assert "shown" not in asked, asked
    allowed = await _switch_on(confirm=True)

    assert allowed.status == 200, _reply(allowed)
    assert grants.missing(_stored()) == []


# ── the create dialog ────────────────────────────────────────────────────────────────────────


def _create_body(**extra: Any) -> dict[str, Any]:
    return {
        "trigger_type": "schedule",
        "name": "Friday report",
        "cron": "0 17 * * 5",
        "action": {"provider": "run-workflow", "config": {"workflow": NAME}},
        **extra,
    }


async def _create(body: dict[str, Any]) -> web.StreamResponse:
    return await T.api_trigger_create(_request("POST", "/api/triggers", body))


async def test_the_create_dialogs_allow_is_held_to_what_it_asked(engine):
    await _owner_saves(NAME, _root("the owner's report"))
    asked = _question(await _create(_create_body()))
    assert asked["shown"]["version"] == 1, asked
    _agent_saves(NAME, _root("the agent's rewrite"))

    message, again = _asked_again(await _create(_create_body(confirm=True, shown=asked["shown"])))

    assert "v2 by an agent" in message, message
    assert _store().load() == [], "nothing is created on a refused yes"

    created = await _create(_create_body(confirm=True, shown=again["shown"]))
    assert created.status == 200, _reply(created)
    pinned = grants.allowed_workflow(_stored("clock:friday-report"))
    assert pinned is not None and pinned.version == 2


async def test_the_create_dialogs_unchanged_allow_pins_what_it_showed(engine):
    await _owner_saves(NAME, _root("the owner's report"))
    asked = _question(await _create(_create_body()))

    created = await _create(_create_body(confirm=True, shown=asked["shown"]))

    assert created.status == 200, _reply(created)
    pinned = grants.allowed_workflow(_stored("clock:friday-report"))
    assert pinned is not None
    assert (pinned.version, pinned.digest) == (1, asked["shown"]["digest"])


# ── the editor ───────────────────────────────────────────────────────────────────────────────


async def _edit(trigger_id: str, body: dict[str, Any]) -> web.StreamResponse:
    """A save from the editor, naming the revision it read, as the page does."""
    listed = T._schedule_row_for(SimpleNamespace(), _store().get(trigger_id.split(":", 1)[1]))
    return await T.api_trigger_detail(
        _request(
            "PUT",
            f"/api/triggers/{trigger_id}",
            body,
            match={"id": trigger_id},
            headers={"If-Match": f'"{listed["revision"]}"'},
        )
    )


async def test_the_editors_allow_is_held_to_what_it_asked(engine):
    """An edit that points the automation at another workflow asks about that one, and its yes
    is held to the version the question showed."""
    await _owner_saves(NAME, _root("the owner's report"))
    await _owner_saves(OTHER, _root("the owner's monthly report"))
    asked = _question(await _create(_create_body()))
    assert (await _create(_create_body(confirm=True, shown=asked["shown"]))).status == 200
    tid = "schedule:clock:friday-report"
    edit = {"action": {"provider": "run-workflow", "config": {"workflow": OTHER}}}

    asked = _question(await _edit(tid, edit))
    assert asked["shown"]["name"] == OTHER and asked["shown"]["version"] == 1, asked
    _agent_saves(OTHER, _root("the agent's monthly rewrite"))
    message, again = _asked_again(
        await _edit(tid, {**edit, "confirm": True, "shown": asked["shown"]})
    )

    assert f"“{OTHER}” changed after you were asked about version 1: v2 by an agent." in message
    unchanged = _stored("clock:friday-report")
    assert unchanged.workflow["inline"]["config"]["workflow"] == NAME
    pinned = grants.allowed_workflow(unchanged)
    assert pinned is not None and pinned.workflow == NAME

    saved = await _edit(tid, {**edit, "confirm": True, "shown": again["shown"]})
    assert saved.status == 200, _reply(saved)
    pinned = grants.allowed_workflow(_stored("clock:friday-report"))
    assert pinned is not None and (pinned.workflow, pinned.version) == (OTHER, 2)


# ── a lifecycle trigger ──────────────────────────────────────────────────────────────────────


async def test_a_lifecycle_triggers_create_is_held_to_what_it_asked(engine):
    await _owner_saves(NAME, _root("the owner's report"))
    body = {
        "trigger_type": "lifecycle",
        "name": "After a session starts",
        "event": "SessionStart",
        "action": {"provider": "run-workflow", "config": {"workflow": NAME}},
    }
    asked = _question(await _create(body))
    _agent_saves(NAME, _root("the agent's rewrite"))

    _message, again = _asked_again(
        await _create({**body, "confirm": True, "shown": asked["shown"]})
    )

    hooks = T._hook_store(None)
    assert hooks.list_all() == [], "nothing is created on a refused yes"
    created = await _create({**body, "confirm": True, "shown": again["shown"]})
    assert created.status == 200, _reply(created)
    (hook,) = hooks.list_all()
    pinned = grants.allowed_workflow(hook)
    assert pinned is not None and pinned.version == 2


# ── the CLI, which cannot say which version it was shown ─────────────────────────────────────


def test_the_cli_sends_a_workflows_allow_to_the_triggers_page(monkeypatch, capsys):
    """A yes typed at the terminal (``--yes``) answers a question printed by an earlier command,
    and cannot name the version that question showed, so it allows none: the CLI says where the
    Allow is given and changes nothing."""
    from unittest.mock import MagicMock

    from personalclaw import cli_commands

    asyncio.run(_owner_saves(NAME, _root("the owner's report")))
    home = config_loader.config_dir()
    monkeypatch.setattr(cli_commands, "config_dir", lambda: home)
    monkeypatch.setattr(cli_commands, "sel", lambda: MagicMock())
    trigger = Trigger(
        id="clock:ops",
        name="Ops report",
        kind="clock",
        created_by="user",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        workflow={"inline": {"provider": "run-workflow", "config": {"workflow": NAME}}},
    )
    _store().upsert(trigger)
    before = copy.deepcopy(_stored("clock:ops"))
    args = {
        "name": None,
        "message": "a new instruction",
        "every": None,
        "every_secs": None,
        "cron_expr": None,
        "channel": None,
        "approval_mode": None,
        "yes": True,
        "cron_action": "update",
        "job_id": "clock:ops",
    }

    with pytest.raises(SystemExit) as exited:
        cli_commands._cron(argparse.Namespace(**args))

    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert "Triggers page" in err and "Nothing was changed" in err, err
    after = _stored("clock:ops")
    assert after.workflow == before.workflow and after.capabilities == before.capabilities
