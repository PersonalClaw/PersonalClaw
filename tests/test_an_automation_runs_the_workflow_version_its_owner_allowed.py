"""An automation that runs a workflow runs the version of it its owner allowed.

The owner's Allow of a "Run workflow" automation is her yes to the workflow as it was then. A newer
version she saves herself, in the workflow's editor, is followed, since that save is her yes too;
a newer version anything else saves — an agent's tool, an accepted refiner proposal, an import —
is not: the automation keeps running the version she allowed, its row says a newer one exists, and
"Use vN" moves it there, asking her first. Every version records who saved it, set by the door the
save came through, never by what the door was handed.

Driven through the doors themselves: the editor's route as the owner's session, the agent's
``workflow_author`` tool and its gateway route, the publish switch, an accepted refiner diff, a
prompt card; the automation's Allow (``grants.give``) and its fire (the ``run-workflow`` action,
with the engine's launch recorded rather than run); the Triggers page's "Use vN" route and the
workflow page's list of the automations that run it.
"""

from __future__ import annotations

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
from personalclaw.stale_write import revision_of
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.screen import capabilities_for_action
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import handlers as H
from personalclaw.workflows import service, store, versions
from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

pytestmark = pytest.mark.anyio

NAME = "weekly-report"
TRIGGER_ID = "manual:weekly-report"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _root(said: str) -> dict[str, Any]:
    """A two-step workflow whose last step says *said*: what tells one version from another."""
    return {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": {"n": 1}}},
            {"kind": "transform", "id": "tail", "config": {"expr": said}},
        ],
    }


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
    """The Triggers page's store reads the home every other door here reads."""
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.triggers.config_dir", config_loader.config_dir
    )


@pytest.fixture(autouse=True)
def _native_store():
    before = defs_mod.get_provider("native")
    defs_mod.register_provider(NativeWorkflowDefProvider())
    yield
    defs_mod.unregister_provider("native")
    if before is not None:
        defs_mod.register_provider(before)


def _request(
    method: str,
    path: str,
    body: dict[str, Any],
    *,
    match: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    state: Any = None,
) -> web.Request:
    """A request the owner's signed-in session makes, unless *headers* say otherwise."""
    app = web.Application()
    if state is not None:
        app["state"] = state
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


async def _editor_save(
    said: str, *, headers: dict[str, str] | None = None, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Save the workflow from its editor: the owner's door, naming the revision it edited."""
    claim = dict(headers or {})
    stored = await service.get_def(NAME)
    if stored.get("ok"):
        claim["If-Match"] = revision_of(stored["definition"])
    body = {"name": NAME, "root": _root(said), **(extra or {})}
    saved = _reply(await H.api_def_save(_request("POST", "/api/workflows", body, headers=claim)))
    assert saved.get("saved"), saved
    return saved


def _agents_tool_save(said: str) -> str:
    """Save the workflow with the agent's own tool, ``workflow_author``."""
    from personalclaw.mcp_workflows import _call_tool

    answer = _call_tool("workflow_author", {"name": NAME, "root": json.dumps(_root(said))})
    assert '"saved": true' in str(answer), answer
    return str(answer)


async def _agents_gateway_save(said: str) -> dict[str, Any]:
    """Save the workflow the way an agent CLI's tool server does: the gateway's own route, with
    the internal credential the gateway's processes present."""
    body = {"name": NAME, "root": _root(said)}
    headers = {"X-Internal-Secret": "the-gateway-s-own", "X-Session-Key": "dashboard:chat-1"}
    saved = _reply(
        await H.api_agent_save(
            _request("POST", "/api/workflows/agent-saves", body, headers=headers)
        )
    )
    assert saved.get("saved"), saved
    return saved


def _allowed_automation(*, created_by: str = "user", capabilities: dict | None = None) -> Trigger:
    """A "Run workflow" automation the owner allowed (`grants.give`, her yes), stored."""
    trigger = Trigger(
        id=TRIGGER_ID,
        name="Weekly report",
        kind="manual",
        created_by=created_by,
        workflow={"inline": {"provider": "run-workflow", "config": {"workflow": NAME}}},
    )
    if capabilities is None:
        assert grants.give(trigger) == ["run-workflow"]
    else:
        trigger.capabilities = capabilities
    TriggerStore(base_dir=config_loader.config_dir()).upsert(trigger)
    return trigger


def _stored() -> Trigger:
    row = TriggerStore(base_dir=config_loader.config_dir()).get(TRIGGER_ID)
    assert row is not None
    return row.trigger


async def _fire(engine: _Engine) -> tuple[int, str]:
    """Fire the automation's action; the version its run executes and what its last step says."""
    result = await RunWorkflowActionProvider().execute(
        {"workflow": NAME}, ActionContext(event="manual", trigger_id=TRIGGER_ID)
    )
    assert result.success, result.error
    run, spec = engine.launched[-1]
    assert store.get(run.id).spec_version == run.spec_version
    return run.spec_version, spec["root"]["children"][-1]["config"]["expr"]


def _saved_by(version: int) -> str:
    record = versions.get_version(NAME, version)
    assert record is not None, f"v{version} was not recorded"
    return record.saved_by


# ── what a fire runs ─────────────────────────────────────────────────────────────────────────


async def test_a_newer_version_an_agents_tool_saved_is_not_what_the_automation_runs(engine):
    """🔴 Before: the owner allowed the automation for the workflow as it was, an agent's
    ``workflow_author`` then saved a new version, and the next fire ran the agent's version."""
    await _editor_save("the owner's report")
    _allowed_automation()
    _agents_tool_save("the agent's rewrite")

    assert await _fire(engine) == (1, "the owner's report")
    row = grants.workflow_version(_stored())
    assert row is not None
    assert (row["runs"], row["allowed"], row["newer"]) == (1, 1, 2), row
    assert row["since"] == [{"version": 2, "saved_by": "agent"}], row


async def test_a_newer_version_an_agent_cli_saved_through_the_gateway_is_not_followed(engine):
    await _editor_save("the owner's report")
    _allowed_automation()
    await _agents_gateway_save("the agent's rewrite")

    assert await _fire(engine) == (1, "the owner's report")


async def test_a_newer_version_the_owner_saved_in_the_editor_is_followed(engine):
    """The control: her own save is her yes, so the automation runs it without being asked."""
    await _editor_save("the owner's report")
    _allowed_automation()
    await _editor_save("the owner's second draft")

    assert await _fire(engine) == (2, "the owner's second draft")

    await _editor_save("the owner's third draft")
    assert await _fire(engine) == (3, "the owner's third draft")


async def test_the_owners_editor_save_over_an_agents_version_is_followed(engine):
    """An agent's save in between is not followed; her editor save after it is — her editor
    showed her what the agent saved, and she saved it as she wanted it."""
    await _editor_save("the owner's report")
    _allowed_automation()
    await _editor_save("the owner's second draft")
    _agents_tool_save("the agent's rewrite")

    assert await _fire(engine) == (2, "the owner's second draft")

    await _editor_save("the owner's third draft, over the agent's")
    assert await _fire(engine) == (4, "the owner's third draft, over the agent's")
    row = grants.workflow_version(_stored())
    assert row is not None and (row["runs"], row["newer"]) == (4, 0), row


async def test_publishing_an_agents_version_does_not_make_it_the_owners(engine):
    """The publish switch shows no step, so it is no yes to the steps it saves again."""
    await _editor_save("the owner's report")
    _allowed_automation()
    published = await H.api_def_a2a_publish(
        _request(
            "POST", f"/api/workflows/{NAME}/a2a-publish", {"published": True}, match={"name": NAME}
        )
    )
    assert _reply(published)["a2a_published"] is True
    # A switch that changes no step leaves the automation running what it ran.
    assert await _fire(engine) == (2, "the owner's report")

    _agents_tool_save("the agent's rewrite")
    unpublished = await H.api_def_a2a_publish(
        _request(
            "POST", f"/api/workflows/{NAME}/a2a-publish", {"published": False}, match={"name": NAME}
        )
    )
    assert _reply(unpublished)["a2a_published"] is False

    assert await _fire(engine) == (1, "the owner's report")


async def test_a_version_the_automation_may_run_that_is_gone_is_refused_in_words(engine):
    await _editor_save("the owner's report")
    _allowed_automation()
    _agents_tool_save("the agent's rewrite")
    (store.workflows_dir() / "versions" / NAME / "v001.json").unlink()

    result = await RunWorkflowActionProvider().execute(
        {"workflow": NAME}, ActionContext(event="manual", trigger_id=TRIGGER_ID)
    )

    assert not result.success and engine.launched == []
    assert "the version it was allowed (v1) is no longer kept here" in result.error, result.error
    assert "Use v2" in result.error, result.error


async def test_the_create_dialogs_allow_records_the_version_it_allowed(engine):
    """🔴 Before: the create dialog's yes granted the action through a door of its own, which
    recorded no version, so an agent's save after it was what the automation ran."""
    from unittest.mock import MagicMock

    from personalclaw.dashboard.handlers.triggers import api_trigger_create

    await _editor_save("the owner's report")
    body = {
        "trigger_type": "schedule",
        "name": "Friday report",
        "cron": "0 17 * * 5",
        "confirm": True,
        "action": {"provider": "run-workflow", "config": {"workflow": NAME}},
    }
    created = await api_trigger_create(_request("POST", "/api/triggers", body, state=MagicMock()))
    assert created.status == 200, _reply(created)
    _agents_tool_save("the agent's rewrite")

    row = TriggerStore(base_dir=config_loader.config_dir()).get("clock:friday-report")
    assert row is not None
    allowed = grants.allowed_workflow(row.trigger)
    assert allowed is not None and allowed.version == 1, row.trigger.capabilities
    result = await RunWorkflowActionProvider().execute(
        {"workflow": NAME}, ActionContext(event="schedule", trigger_id="clock:friday-report")
    )
    assert result.success, result.error
    assert engine.launched[-1][0].spec_version == 1


async def test_an_allow_from_before_versions_were_recorded_is_bound_at_its_first_fire(engine):
    """An Allow given before the grant said which version it was for covered every version. Its
    first fire binds it to the version it runs then, and a later agent's save is not followed."""
    await _editor_save("the owner's report")
    _allowed_automation(capabilities={"providers": ["run-workflow"]})

    assert await _fire(engine) == (1, "the owner's report")
    assert grants.allowed_workflow(_stored()) is not None

    _agents_tool_save("the agent's rewrite")
    assert await _fire(engine) == (1, "the owner's report")


async def test_an_automation_personalclaws_own_code_made_runs_the_workflow_as_it_is(engine):
    """PersonalClaw's own automations are granted by the code that makes them and run the
    template it ships, as it is: they record no version, and none is bound to them."""
    await _editor_save("the first")
    automation = Trigger(
        id=TRIGGER_ID,
        name="A system digest",
        kind="manual",
        created_by="system",
        workflow={"inline": {"provider": "run-workflow", "config": {"workflow": NAME}}},
    )
    automation.capabilities = capabilities_for_action(automation)
    TriggerStore(base_dir=config_loader.config_dir()).upsert(automation)
    await _editor_save("the second")

    assert await _fire(engine) == (2, "the second")
    assert grants.allowed_workflow(_stored()) is None


async def test_a_run_no_automation_started_runs_the_workflow_as_it_is(engine):
    await _editor_save("the owner's report")
    _agents_tool_save("the agent's rewrite")

    result = await RunWorkflowActionProvider().execute(
        {"workflow": NAME}, ActionContext(event="workflow_step")
    )

    assert result.success, result.error
    assert engine.launched[-1][0].spec_version == 2


# ── "Use vN": pinning changes what runs, and asks first ─────────────────────────────────────


async def _use(version: int, *, confirm: bool) -> web.Response:
    """Use vN on the automation's row: the Triggers page's route, as the owner's session."""
    body: dict[str, Any] = {"version": version, **({"confirm": True} if confirm else {})}
    from personalclaw.dashboard.handlers.triggers import api_trigger_workflow_version

    return await api_trigger_workflow_version(
        _request(
            "POST",
            f"/api/triggers/store:{TRIGGER_ID}/workflow-version",
            body,
            match={"id": f"store:{TRIGGER_ID}"},
            state=_State(),
        )
    )


async def test_use_vn_moves_what_the_automation_runs(engine):
    """🔴 Before: nothing an owner could do changed which version an automation ran — the
    version pointer `repin` moved was read by no run."""
    await _editor_save("the owner's report")
    _allowed_automation()
    _agents_tool_save("the agent's rewrite")
    assert await _fire(engine) == (1, "the owner's report")

    used = await _use(2, confirm=True)

    assert used.status == 200, _reply(used)
    assert _reply(used)["trigger"]["workflow_version"]["runs"] == 2
    assert await _fire(engine) == (2, "the agent's rewrite")


async def test_use_vn_asks_first_and_changes_nothing_until_answered(engine):
    await _editor_save("the owner's report")
    _allowed_automation()
    _agents_tool_save("the agent's rewrite")

    asked = await _use(2, confirm=False)

    assert asked.status == 400
    detail = _reply(asked)["error"]["detail"]
    assert detail["title"] == f"Run version 2 of “{NAME}”?", detail
    assert "runs version 1" in detail["consent"] and "version 2 instead" in detail["consent"]
    assert "v2 by an agent" in detail["consent"], detail
    assert detail["change"] == "v1 → v2"
    assert await _fire(engine) == (1, "the owner's report")


async def test_use_vn_offers_the_version_there_is_when_the_allowed_one_is_gone(engine):
    await _editor_save("the owner's report")
    _allowed_automation()
    _agents_tool_save("the agent's rewrite")
    (store.workflows_dir() / "versions" / NAME / "v001.json").unlink()

    asked = await _use(2, confirm=False)

    detail = _reply(asked)["error"]["detail"]
    assert "was allowed to run version 1" in detail["consent"], detail
    assert "no longer kept here, so it runs nothing" in detail["consent"], detail
    assert detail["change"] == "v1 → v2"
    assert (await _use(2, confirm=True)).status == 200
    assert await _fire(engine) == (2, "the agent's rewrite")


def test_every_saver_has_words_for_the_owner():
    assert set(grants._SAVED_BY_WORDS) == versions.SAVERS


async def test_use_vn_for_a_version_that_is_no_longer_current_changes_nothing(engine):
    await _editor_save("the owner's report")
    _allowed_automation()
    _agents_tool_save("the agent's rewrite")
    _agents_tool_save("the agent's second rewrite")

    stale = await _use(2, confirm=True)

    assert stale.status == 409 and _reply(stale)["error"]["code"] == "stale_write"
    assert "is version 3 now, not 2" in _reply(stale)["error"]["message"]
    assert await _fire(engine) == (1, "the owner's report")


async def test_the_workflow_wide_version_pointer_is_gone():
    """The pointer `versions/repin` moved was read by no run: a run of an automation reads the
    version its grant allows, and every other run the workflow as it is."""
    app = web.Application()
    H.register_workflow_routes(app)
    paths = {r.resource.canonical for r in app.router.routes()}
    assert "/api/workflows/{name}/versions/repin" not in paths
    assert not hasattr(versions, "repin") and not hasattr(versions, "pinned_version")


# ── who saved each version: the door says ───────────────────────────────────────────────────


async def test_each_door_records_who_saved_the_version_it_wrote():
    await _editor_save("by the owner")
    _agents_tool_save("by the agent's tool")
    await _agents_gateway_save("by an agent CLI")
    # The editor's own route, reached with the internal credential: an agent's, whatever its
    # body says it is.
    await _editor_save(
        "by an agent at the owner's route",
        headers={"X-Internal-Secret": "the-gateway-s-own"},
        extra={"provenance": "owner", "saved_by": "owner", "_saved_by": "owner"},
    )
    await H.api_def_a2a_publish(
        _request(
            "POST", f"/api/workflows/{NAME}/a2a-publish", {"published": True}, match={"name": NAME}
        )
    )
    # The refiner's candidate is the stored definition with its ops applied, read raw.
    stored = await NativeWorkflowDefProvider().get_def(NAME)
    candidate = stored.to_dict()
    candidate.update(root=_root("by the refiner"), provenance="owner", _saved_by="owner")
    assert (await service.save_accepted_diff(candidate, ops=[]))["ok"]

    assert [_saved_by(n) for n in range(1, 7)] == [
        "owner",
        "agent",
        "agent",
        "agent",
        "publish",
        "refiner",
    ]
    listed = _reply(
        await H.api_def_versions(
            _request("GET", f"/api/workflows/{NAME}/versions", {}, match={"name": NAME})
        )
    )
    assert [r["saved_by"] for r in listed["versions"]] == [
        "owner",
        "agent",
        "agent",
        "agent",
        "publish",
        "refiner",
    ]
    assert "pinned" not in listed


def test_a_prompt_card_is_saved_as_an_import():
    from personalclaw.packs.prompt_cards import _save_def_sync

    assert _save_def_sync({"name": "from-a-card", "root": _root("from a pasted card")})
    assert versions.get_version("from-a-card", 1).saved_by == "import"


async def test_a_version_number_never_names_two_definitions():
    """A workflow deleted and made again continues its numbers: its old history keeps its own."""
    await _editor_save("the first workflow")
    assert (await service.delete_def(NAME))["ok"]
    _agents_tool_save("made again, by an agent")

    assert (await service.get_def(NAME))["definition"]["version"] == 2
    assert versions.get_version(NAME, 1).saved_by == "owner"
    assert versions.get_version(NAME, 2).saved_by == "agent"


def test_a_versions_history_stays_on_its_machine():
    """What an automation here may run, saved by whom here: another machine's would run as
    written, its owner's saves read as this one's."""
    from personalclaw.durability.inventory import INVENTORY, stays_here

    (entry,) = [e for e in INVENTORY if e.id == "workflows"]
    assert stays_here(entry, f"versions/{NAME}/v001")
    assert not stays_here(entry, f"defs/{NAME}/workflow")


# ── the workflow's page lists the automations that run it ───────────────────────────────────


async def test_the_workflow_page_lists_its_automations_with_the_version_each_runs(engine):
    await _editor_save("the owner's report")
    _allowed_automation()
    _agents_tool_save("the agent's rewrite")

    listed = _reply(
        await H.api_def_automations(
            _request("GET", f"/api/workflows/{NAME}/automations", {}, match={"name": NAME})
        )
    )

    (row,) = listed["automations"]
    assert row["id"] == TRIGGER_ID and row["name"] == "Weekly report"
    assert (row["workflow_version"]["runs"], row["workflow_version"]["newer"]) == (1, 2)
    assert row["status_url"].endswith(TRIGGER_ID)


async def test_the_allow_says_which_version_it_lets_the_automation_run():
    await _editor_save("the owner's report")
    automation = Trigger(
        id=TRIGGER_ID,
        name="Weekly report",
        kind="manual",
        workflow={"inline": {"provider": "run-workflow", "config": {"workflow": NAME}}},
    )

    asked = grants.question(automation)

    assert asked is not None
    assert f"It runs “{NAME}” as it is when you allow it (version 1 now)" in asked.sentence
    assert "a newer version only once you save one in the workflow's editor" in asked.sentence

    _agents_tool_save("the agent's rewrite")
    asked = grants.question(automation)
    assert asked is not None
    assert "(version 2 now, saved by an agent)" in asked.sentence, asked.sentence
