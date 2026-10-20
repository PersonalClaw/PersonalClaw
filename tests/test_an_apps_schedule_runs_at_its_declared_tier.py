"""An app's scheduled job runs at the agent tier its manifest declares, as the app's work.

A manifest's ``crons`` are agent jobs on a clock. Each was registered as an ``invoke-agent`` action
that approved its own calls (``approval_mode: "auto"``), and its agent started without the app's
name, so no tier held it and the ``cron`` permission alone turned it on: an app that declared the
``text`` tier, or no agent at all, got a read-capable agent on its schedule that asked nobody, and
its result was handed to a turn of the owner's own agent. Now:

* a job's agent runs at the app's tier, the one it holds when the job fires: ``text`` hands its
  model the job's message alone, with no tools; ``read`` gives it read-only tools and no message to
  send; ``tools`` gives it the owner's tools;
* it carries the app's name, so it approves none of its calls: each one that needs approval asks
  the owner, whatever her own switches say, and its result is the app's;
* an app that names no tier schedules no job, and a manifest that declares jobs, or the ``cron``
  permission, without one is refused at install;
* install consent says each job's tier, the job's own Allow says it, and the job is named by its
  app wherever it is named.

Driven as the clock drives it: the real reconciliation into the trigger store, the invoke-agent
action a job's fire runs, and a real subagent manager over PersonalClaw's own runtime on a
scripted model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_an_apps_agent_is_held_to_its_tier import _HER_CONTEXT, _Model, _Notes, _Recorder
from test_apps_cannot_run_code_or_bypass_approvals import _home

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.apps.manifest import AppManifest
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition

APP = "probe-garden"
NAMED = "Probe Garden"
JOB = f"app:{APP}:water-rota"
MESSAGE = "Draft this week's watering rota from the plot notes."
CRON = {"name": "water-rota", "cron_expr": "0 7 * * 1", "message": MESSAGE}


def _install(home: Path, permissions: dict, *, crons: list | None = None, enabled=True) -> None:
    """The app as an install leaves it on disk, its manifest NOT validated: the shape an install
    made before a rule leaves behind, which is what reconciliation and a fire meet."""
    appdir = home / "apps" / APP
    appdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": NAMED,
        "description": "Keeps the allotment's watering rota.",
        "permissions": permissions,
        "crons": [CRON] if crons is None else crons,
    }
    (appdir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (appdir / "installed.json").write_text(
        json.dumps({"name": APP, "version": "1.0.0", "enabled": enabled}), encoding="utf-8"
    )


def _manifest(permissions: dict, crons: list | None = None) -> AppManifest:
    return AppManifest.from_dict(
        {
            "name": APP,
            "version": "1.0.0",
            "displayName": NAMED,
            "description": "Keeps the allotment's watering rota.",
            "permissions": permissions,
            "crons": [CRON] if crons is None else crons,
        }
    )


def _store(home: Path) -> Any:
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=home)


def _config(row: Any) -> dict[str, Any]:
    inline = (row.trigger.workflow or {}).get("inline") or {}
    return dict(inline.get("config") or {})


# ── 1. what a manifest may declare ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("permissions", "crons"),
    [({"cron": True}, None), ({"cron": True}, []), ({}, None)],
    ids=["jobs and the cron permission", "the cron permission alone", "jobs alone"],
)
def test_jobs_or_the_cron_permission_with_no_agent_tier_are_refused_at_install(permissions, crons):
    refused = [e for e in _manifest(permissions, crons).validate() if "permissions.agent" in e]
    assert len(refused) == 1, refused
    assert all(f'"{tier}"' in refused[0] for tier in ("text", "read", "tools")), refused
    for tier in ("text", "read", "tools"):
        assert _manifest({**permissions, "agent": tier}, crons).validate() == [], tier


def test_a_text_tier_job_names_no_agent():
    """A text job's model is handed its message alone on the worker with no tools, so a job that
    names an agent asks for an agent and its tools."""
    named = [{**CRON, "agent": "researcher"}]
    refused = _manifest({"cron": True, "agent": "text"}, named).validate()
    assert len(refused) == 1 and "'water-rota'" in refused[0] and "researcher" in refused[0]
    assert _manifest({"cron": True, "agent": "read"}, named).validate() == []
    assert _manifest({"cron": True, "agent": "text"}).validate() == []


# ── 2. what reconciliation registers ───────────────────────────────────────────────────────


def test_an_app_that_names_no_tier_schedules_none_of_its_jobs(tmp_path):
    """An install from before the rule declares jobs and no tier: nothing it runs could be held to
    one, so none is registered, one registered before is removed, and consent says so."""
    from personalclaw.apps.app_crons import reconcile_app_crons, schedules
    from personalclaw.apps.disclosure import describe

    with _home(tmp_path):
        _install(tmp_path, {"cron": True})
        store = _store(tmp_path)
        reconcile_app_crons(store)
        assert store.get(JOB) is None, "a job of an app with no tier was registered"
        _install(tmp_path, {"cron": True, "agent": "read"})
        reconcile_app_crons(store)
        assert store.get(JOB) is not None
        _install(tmp_path, {"cron": True})
        reconcile_app_crons(store)
        assert store.get(JOB) is None, "the job outlived its app's tier"

    untiered = _manifest({"cron": True})
    assert schedules(untiered, untiered.crons[0]) is False
    (said,) = describe(untiered)["crons"]
    assert said["scheduled"] is False and said["tier"] == ""
    (said,) = describe(_manifest({"cron": True, "agent": "tools"}))["crons"]
    assert said["scheduled"] is True and said["tier"] == "tools"


def test_a_jobs_row_carries_no_posture_of_its_own_and_one_made_before_loses_it(tmp_path):
    """The row says what the job runs and nothing of how its agent asks: that is the app's tier.
    A row registered before kept ``approval_mode: "auto"``, and showed an agent that approves its
    own calls; reconciliation takes it off and leaves the owner's switch and cadence alone."""
    from personalclaw.apps.app_crons import reconcile_app_crons

    with _home(tmp_path):
        _install(tmp_path, {"cron": True, "agent": "read"})
        store = _store(tmp_path)
        reconcile_app_crons(store)
        config = _config(store.get(JOB))
        assert config == {"task_template": MESSAGE, "agent": "", "model": ""}, config

        row = store.get(JOB)
        assert row is not None
        row.trigger.enabled = False
        row.trigger.spec = {"kind": "cron", "expr": "30 6 * * *", "timezone": "Europe/Lisbon"}
        row.trigger.workflow["inline"]["config"].update(approval_mode="auto", capability="mutating")
        store.upsert(row.trigger)
        reconcile_app_crons(store)
        again = store.get(JOB)

    assert again is not None
    assert _config(again) == {"task_template": MESSAGE, "agent": "", "model": ""}
    assert again.trigger.enabled is False, "her switch is hers"
    assert again.trigger.spec["expr"] == "30 6 * * *", "and so is the cadence she set"


# ── 3. what a fire starts ──────────────────────────────────────────────────────────────────


async def _fire(home: Path, config: dict | None = None, subagents: Any = None) -> tuple[Any, Any]:
    """The job's fire, as the clock dispatches it: the invoke-agent action of its row, with the
    job's id on the context."""
    from personalclaw.action_providers import invoke_agent_provider
    from personalclaw.action_providers.base import ActionContext

    subagents = subagents or _Recorder()
    with patch.object(
        invoke_agent_provider,
        "get_action_services",
        lambda: SimpleNamespace(subagents=subagents),
    ):
        fired = await invoke_agent_provider.InvokeAgentActionProvider().execute(
            config or {"task_template": MESSAGE},
            ActionContext(event="trigger.fired", trigger_id=JOB),
        )
    return fired, subagents


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "capability"), [("text", "text"), ("read", "research"), ("tools", "mutating")]
)
async def test_a_fire_starts_the_jobs_agent_at_its_apps_tier_as_the_apps_work(
    tmp_path, tier, capability
):
    with _home(tmp_path):
        _install(tmp_path, {"cron": True, "agent": tier})
        # The row as it was registered before: its own posture said it approves its own calls.
        fired, subagents = await _fire(
            tmp_path, {"task_template": MESSAGE, "agent": "", "approval_mode": "auto"}
        )
    assert fired.success, fired.error
    (started,) = subagents.started
    assert started["task"] == MESSAGE
    assert started["capability_class"] == capability
    assert started["app"] == APP, "the agent carries the app's name"
    assert not started.get("approval_mode"), "an app's agent approves none of its own calls"
    assert started["parent_session_key"] == f"app:{APP}", "its result is the app's"
    assert started["trigger_id"] == JOB, "its run is the job's, in its history and its asks"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permissions", "enabled", "why"),
    [
        ({"cron": True}, True, "does not declare the 'agent' permission"),
        ({"cron": True, "agent": True}, True, "names no tier"),
        ({"cron": True, "agent": "tools"}, False, "is disabled"),
    ],
    ids=["no tier", "the boolean from before tiers", "switched off"],
)
async def test_a_fire_of_an_app_that_may_run_no_agent_work_starts_nothing(
    tmp_path, permissions, enabled, why
):
    with _home(tmp_path):
        _install(tmp_path, permissions, enabled=enabled)
        fired, subagents = await _fire(tmp_path)
    assert not fired.success
    assert subagents.started == [], "an agent started for an app that may run none"
    assert "starts no agent" in fired.error and why in fired.error, fired.error


@pytest.mark.asyncio
async def test_the_tier_is_the_one_the_app_holds_when_the_job_fires(tmp_path):
    """An update that changes the tier holds the job's next fire to it, with no new row."""
    with _home(tmp_path):
        _install(tmp_path, {"cron": True, "agent": "text"})
        _, first = await _fire(tmp_path)
        _install(tmp_path, {"cron": True, "agent": "read"})
        _, second = await _fire(tmp_path)
    assert first.started[0]["capability_class"] == "text"
    assert second.started[0]["capability_class"] == "research"


@pytest.mark.asyncio
async def test_no_posture_on_the_jobs_step_widens_or_loosens_its_agent(tmp_path):
    """A step's write access and its self-approval are the owner's grants for her own automations;
    an app's job has the app's tier, whatever its row says."""
    with _home(tmp_path):
        _install(tmp_path, {"cron": True, "agent": "read"})
        fired, subagents = await _fire(
            tmp_path,
            {"task_template": MESSAGE, "approval_mode": "auto", "capability": "mutating"},
        )
    assert fired.success, fired.error
    (started,) = subagents.started
    assert started["capability_class"] == "research"
    assert not started.get("approval_mode")
    assert not started.get("may_change") and not started.get("cwd")


# ── 4. what the job's agent is shown, handed and may use ───────────────────────────────────


class _Garden(_Notes):
    """Her notes, and a tool whose only effect is telling her something."""

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            *await super().list_tools(),
            ToolDefinition(
                name="tell_owner",
                description="Send the owner a message.",
                parameters={"type": "object", "properties": {"message": {"type": "string"}}},
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                tells_owner=("message",),
            ),
        ]


async def _run_job(
    home: Path, tier: str, model: _Model, *, yolo: bool = False
) -> tuple[Any, _Garden, MagicMock, list[str]]:
    """Fire the job on a REAL subagent manager, whose session is PersonalClaw's own runtime with
    ``_Garden`` and *model*, and wait for its agent to end. Every ask is answered no, so a call
    that ran was never asked about. Returns ``(the run, the tools, the sessions, what was asked)``.
    """
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentManager

    garden = _Garden()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[garden],
    )
    await runtime.start()

    async def _session(*_args: Any, **kwargs: Any) -> tuple[Any, bool, bool]:
        from personalclaw.session import _push_approval_policy

        _push_approval_policy(
            runtime, kwargs.get("approval_policy", ""), kwargs.get("approval_source")
        )
        return runtime, True, False

    sessions = _mock_sessions()
    sessions.get_or_create = AsyncMock(side_effect=_session)
    sessions.has_session = MagicMock(return_value=False)
    sessions.get_approval_policy = MagicMock(return_value="")
    ctx = _mock_ctx_builder()
    ctx.build_message = MagicMock(
        side_effect=lambda msg, *_a, **_k: (f"{_HER_CONTEXT}\n{msg}", None)
    )
    asked: list[str] = []

    async def _no(event, parent_session_key=""):
        asked.append(event.title or "")
        return False

    manager = SubagentManager(
        sessions=sessions,
        ctx_builder=ctx,
        on_tool_approval=_no,
        is_yolo=(lambda: True) if yolo else None,
    )
    with (
        _home(home),
        patch("personalclaw.subagent_persistence._subagents_dir", lambda: home / "runs"),
        patch("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0)),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
    ):
        _install(home, {"cron": True, "agent": tier})
        fired, _ = await _fire(
            home, {"task_template": MESSAGE, "approval_mode": "auto"}, subagents=manager
        )
        assert fired.success, fired.error
        run_id = str(fired.work_id).split(":", 1)[1]
        for _ in range(500):
            info = manager.get(run_id)
            if info is not None and info.done:
                break
            await asyncio.sleep(0.01)
    info = manager.get(run_id)
    assert info is not None and info.done, "the job's agent did not end"
    return info, garden, sessions, asked


@pytest.mark.asyncio
async def test_a_text_jobs_model_is_handed_its_message_alone_and_shown_no_tools(tmp_path):
    from personalclaw.agents.defaults import LITE_AGENT_NAME

    model = _Model(calls=("read_notes",))
    info, garden, sessions, asked = await _run_job(tmp_path, "text", model)
    assert model.offered[0] == [], "a text job's model is shown no tools at all"
    sent = [m.get("content") for m in model.handed[0] if m.get("role") == "user"]
    assert len(sent) == 1 and MESSAGE in sent[0], sent
    assert not any(_HER_CONTEXT in str(m.get("content")) for m in model.handed[0])
    assert sessions.get_or_create.call_args.kwargs["agent"] == LITE_AGENT_NAME
    assert garden.ran == [] and asked == []


@pytest.mark.asyncio
async def test_a_read_jobs_agent_reads_and_neither_changes_nor_messages_you(tmp_path):
    """An automation's own agent may tell its owner what it found; an app's job's agent, at the
    read tier, may only read: its install consent says it can't change anything or send messages."""
    model = _Model(calls=("read_notes", "write_note", "tell_owner"))
    info, garden, _sessions, asked = await _run_job(tmp_path, "read", model)
    assert model.offered[0] == ["read_notes"], model.offered
    assert garden.ran == ["read_notes"], "the read ran; the change and the message did not"
    assert asked == [], "a call the tier refuses is never put to her as a question"


@pytest.mark.asyncio
async def test_a_tools_jobs_change_asks_you_whatever_your_switches_say(tmp_path):
    """YOLO is on, the row says it approves its own calls, and the change is still put to her."""
    model = _Model(calls=("read_notes", "write_note"))
    info, garden, _sessions, asked = await _run_job(tmp_path, "tools", model, yolo=True)
    assert "write_note" in model.offered[0], model.offered
    assert asked == ["write_note"], "the change was put to her"
    assert garden.ran == ["read_notes"], "and she said no, so it did not run"


# ── 5. what the job is called, and what its Allow and its edits say ────────────────────────


@pytest.mark.asyncio
async def test_an_apps_job_is_named_by_its_app(tmp_path):
    """Its row was named by its id, a key: the Triggers page, its runs, the asks and notes it
    leaves in the Inbox said ``app:probe-garden:water-rota``. It is named by its app now, by the
    name install consent showed, and a row named by its id before is renamed; one the owner
    renamed keeps her name."""
    from personalclaw.apps.app_crons import reconcile_app_crons
    from personalclaw.dashboard.approval_state import _who_asked
    from personalclaw.triggers.store import trigger_name

    named = f"{NAMED}: water-rota"
    with _home(tmp_path):
        _install(tmp_path, {"cron": True, "agent": "tools"})
        store = _store(tmp_path)
        reconcile_app_crons(store)
        assert trigger_name(JOB) == named
        _, subagents = await _fire(tmp_path)
        asked = {"source": "subagent", "session": f"app:{APP}", "trigger": JOB}
        who = _who_asked({**asked, "trigger_name": trigger_name(JOB)})

        row = store.get(JOB)
        assert row is not None
        row.trigger.name = JOB
        store.upsert(row.trigger)
        reconcile_app_crons(store)
        assert trigger_name(JOB) == named, "a row named by its id before is renamed"
        row = store.get(JOB)
        assert row is not None
        row.trigger.name = "Watering"
        store.upsert(row.trigger)
        reconcile_app_crons(store)
        kept = trigger_name(JOB)
    assert subagents.started[0]["title"] == named, "its run is called by its job's name"
    assert who == f"The trigger “{named}”"
    assert kept == "Watering", "her own name for it is hers"


def test_the_jobs_allow_says_the_tier_its_agent_runs_at(tmp_path):
    from personalclaw.apps.app_crons import reconcile_app_crons
    from personalclaw.triggers.grants import held_back, what_its_agent_may_do

    with _home(tmp_path):
        _install(tmp_path, {"cron": True, "agent": "read"})
        store = _store(tmp_path)
        reconcile_app_crons(store)
        row = store.get(JOB)
        assert row is not None
        said = what_its_agent_may_do(row.trigger)
        assert said.startswith(f"It is the scheduled job of the app “{NAMED}”"), said
        assert "read-only tools" in said and "can't change anything or send messages" in said
        assert held_back(row.trigger) is None
        _install(tmp_path, {"cron": True})
        said = what_its_agent_may_do(row.trigger)
    assert "starts no agent" in said, said


def test_no_edit_gives_an_apps_job_a_posture_of_its_own(tmp_path):
    """Whoever saves it, the Triggers page with her yes included: how the job's agent asks and what
    it may change are the app's tier. Its message stays hers to change."""
    from personalclaw.apps.app_crons import reconcile_app_crons
    from personalclaw.triggers import tools

    with _home(tmp_path):
        _install(tmp_path, {"cron": True, "agent": "read"})
        store = _store(tmp_path)
        reconcile_app_crons(store)
        loosened = {
            "inline": {
                "provider": "invoke-agent",
                "config": {"task_template": MESSAGE, "agent": "", "approval_mode": "auto"},
            }
        }
        refused = tools.update(
            store, trigger_id=JOB, patch={"workflow": loosened}, owner_consented=True
        )
        assert not refused.ok and "agent tier" in refused.text, refused.text
        assert "approval_mode" not in _config(store.get(JOB)), "nothing was saved"
        reworded = {
            "inline": {
                "provider": "invoke-agent",
                "config": {"task_template": "Water before nine.", "agent": "", "model": ""},
            }
        }
        saved = tools.update(
            store, trigger_id=JOB, patch={"workflow": reworded}, owner_consented=True
        )
        assert saved.ok, saved.text
        assert _config(store.get(JOB))["task_template"] == "Water before nine."
