"""An automation's agent only reads in a working folder you have not trusted, and its Allow says so.

A working folder an automation's agent runs in is a project folder: until its owner trusts it
(``guardrails.project_trust``), the folder is in Preview, and a run there is held to reading
whatever write access its step asks for. Three things were wrong with that:

* A Run Prompt automation given write access was cut to reading in such a folder when it fired,
  while its Allow said "Its agent may change files, run commands and send messages without asking
  you": the owner agreed to a run that never happened.
* An Invoke Agent automation was not held to the folder's trust at all: given write access, its
  agent changed files and ran commands in a folder its owner never trusted.
* Every Run Prompt automation with a working folder asked the owner to trust it on its first fire,
  "An automation wants to run project scripts", even one that only reads and runs no script.

One shared check now decides it for both actions, and the Allow is said from the same answer
(``automation_posture.agent_run_policy``): it names the folder that is in Preview, and what the
agent may do once the owner trusts it. When the trust changes after the Allow, the run follows
the folder as it is now: one put back in Preview holds the run to reading, and its history and its
trigger say why.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.action_providers import invoke_agent_provider, run_prompt_provider
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider
from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider
from personalclaw.guardrails import project_trust
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger

TRIGGER = "file:site-rebuild"
FOLDER = "~/Projects/site"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A PersonalClaw home and the owner's own folders: a project she lets agents work in, and a
    note an automation may be given to change."""
    root = Path(os.path.realpath(tmp_path))
    pc_home = root / "pc-home"
    (pc_home / "workspace").mkdir(parents=True)
    user = root / "user"
    project = user / "Projects" / "site"
    project.mkdir(parents=True)
    (project / "README.md").write_text("# Site\n", encoding="utf-8")
    notes = user / "Notes"
    notes.mkdir()
    (notes / "site.md").write_text("# Site notes\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    (pc_home / "config.json").write_text(
        json.dumps({"agent": {"subagent_cwd_allowed_roots": ["~/Projects", "~/Notes"]}}),
        encoding="utf-8",
    )
    return SimpleNamespace(pc=pc_home, project=project, notes=notes)


@pytest.fixture()
def asked(monkeypatch) -> list[str]:
    """The folders the owner was asked to trust, in order (the Inbox request itself is
    ``project_trust._prompt_trust_vs_preview``'s)."""
    folders: list[str] = []
    monkeypatch.setattr(
        project_trust, "_prompt_trust_vs_preview", lambda folder, _state: folders.append(folder)
    )
    return folders


def _trigger(config: dict, *, provider: str = "run-prompt") -> Trigger:
    return Trigger(
        id=TRIGGER,
        name="Rebuild the site",
        kind="file",
        enabled=True,
        workflow={"provider": provider, "config": config},
    )


def _spawned_with(provider: str, config: dict, monkeypatch) -> dict:
    """What a real fire of *provider*'s action hands the subagent manager."""
    handed: dict = {}

    def spawn(**kw):
        handed.update(kw)
        return SimpleNamespace(id="run-1", done=False, error="")

    services = SimpleNamespace(subagents=SimpleNamespace(spawn=spawn))
    module, action = (
        (run_prompt_provider, RunPromptActionProvider())
        if provider == "run-prompt"
        else (invoke_agent_provider, InvokeAgentActionProvider())
    )
    monkeypatch.setattr(module, "get_action_services", lambda: services)
    result = asyncio.run(action.execute(config, ActionContext(event="x", trigger_id=TRIGGER)))
    assert result.success, result.error
    return handed


# ── the Allow ───────────────────────────────────────────────────────────────────────────────────


def test_the_allow_of_an_automation_in_a_folder_you_have_not_trusted_says_it_only_reads(
    home, monkeypatch, asked
):
    """🔴 Before: "Its agent may change files, run commands and send messages without asking you",
    for a run its folder's Preview held to reading."""
    config = {"message": "Rebuild the site.", "cwd": FOLDER, "capability": "mutating"}
    said = grants.consent(_trigger(config), ["run-prompt"])

    assert "Its agent only reads" in said
    assert f"Its working folder {FOLDER} is in Preview" in said
    # What trusting the folder gives it is said as that, never as what it may do now.
    assert said.endswith(
        "once you trust that folder, it may change files, run commands and send messages "
        "without asking you."
    )
    assert _spawned_with("run-prompt", config, monkeypatch)["capability_class"] == "research"


def test_the_allow_of_an_automation_in_a_folder_you_trust_says_it_may_change_files(
    home, monkeypatch, asked
):
    project_trust.record_project_trust(str(home.project), trusted=True)
    config = {"message": "Rebuild the site.", "cwd": FOLDER, "capability": "mutating"}
    said = grants.consent(_trigger(config), ["run-prompt"])

    assert said.endswith(
        "Its agent may change files, run commands and send messages without asking you."
    )
    assert "Preview" not in said
    assert _spawned_with("run-prompt", config, monkeypatch)["capability_class"] == "mutating"
    assert asked == []


def test_a_folder_is_one_folder_however_its_path_is_written(home, monkeypatch, asked):
    """🔴 Before: a working folder written from ``~`` (as every form invites) was looked up as a
    folder named ``~`` inside wherever the gateway was started, so trusting the folder itself never
    reached it, and the owner was asked to trust that nonexistent path."""
    project_trust.record_project_trust(str(home.project), trusted=True)
    assert project_trust.project_decision(FOLDER) == project_trust.DECISION_TRUSTED

    project_trust.record_project_trust(FOLDER, trusted=False)
    stored = json.loads((home.pc / "project_trust.json").read_text(encoding="utf-8"))
    assert list(stored) == [str(home.project)]


#: What giving an automation's agent write access gives it, where nothing holds it back.
WRITE_ACCESS = (
    "This automation's agent gets write access: it may change files, run commands and send "
    "messages, not only read."
)
WRITE_ACCESS_ONCE_TRUSTED = (
    f"This automation's agent gets write access once you trust its working folder {FOLDER}: "
    "then it may change files, run commands and send messages, not only read."
)


def test_the_consent_to_write_access_in_a_folder_you_have_not_trusted_waits_for_the_folder(
    home, asked
):
    """The dialog that creates one also asks to loosen its posture, in one sentence after the
    Allow. 🔴 Before: "This automation's agent gets write access: it may change files, run commands
    and send messages, not only read", right after an Allow that said it only reads."""
    from personalclaw.automation_posture import unconsented_step_loosening
    from personalclaw.triggers.tools import posture_refusal

    config = {"message": "Rebuild the site.", "cwd": FOLDER, "capability": "mutating"}
    loosened = unconsented_step_loosening(
        "action", current={}, new=config, body={}, provider="run-prompt"
    )
    assert loosened == ("action.capability", WRITE_ACCESS_ONCE_TRUSTED)
    # The chat's door says it in the same words.
    action = {"provider": "run-prompt", "config": config}
    refused = posture_refusal(action, stored={}, creating=True)
    assert refused is not None and WRITE_ACCESS_ONCE_TRUSTED in refused.text

    project_trust.record_project_trust(str(home.project), trusted=True)
    loosened = unconsented_step_loosening(
        "action", current={}, new=config, body={}, provider="run-prompt"
    )
    assert loosened == ("action.capability", WRITE_ACCESS)


def test_a_workflows_agent_step_waits_for_its_folder_and_a_stage_does_not(home, asked):
    """An Invoke Agent step of a workflow is held to its folder's trust like a trigger's; a stage
    spawns its agent another way, which no folder holds back, so its consent stays as it was."""
    from personalclaw.automation_posture import unconsented_workflow_loosening

    def spec(node: dict) -> dict:
        """The workflow's root, as its save hands it to the posture check."""
        return {"kind": "sequence", "id": "main", "children": [node]}

    step = {"task_template": "Rebuild the site.", "cwd": FOLDER, "capability": "mutating"}
    action = {
        "kind": "action",
        "id": "rebuild",
        "config": {"provider": "invoke-agent", "with": step},
    }
    stage = {"kind": "stage", "id": "rebuild", "config": {"prompt": "x", **step}}

    [_field, consent] = unconsented_workflow_loosening(
        "site", current_root=None, new_root=spec(action), body={}
    )
    assert consent == WRITE_ACCESS_ONCE_TRUSTED
    [_field, consent] = unconsented_workflow_loosening(
        "site", current_root=None, new_root=spec(stage), body={}
    )
    assert consent == WRITE_ACCESS


# ── invoke-agent: the same check ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "posture",
    [
        pytest.param({"approval_mode": "auto", "capability": "mutating"}, id="approves-itself"),
        pytest.param({}, id="asks-you"),
    ],
)
def test_invoke_agent_in_a_folder_you_have_not_trusted_only_reads(
    home, monkeypatch, asked, posture
):
    """🔴 Before: invoke-agent never asked about the folder, and its agent got write access in
    it, an agent that approves its own calls included."""
    config = {"task_template": "Review the change.", "cwd": FOLDER, **posture}
    handed = _spawned_with("invoke-agent", config, monkeypatch)

    assert handed["capability_class"] == "research"
    # Its first fire held back asked the owner to trust the folder, once, and recorded Preview.
    assert asked == [str(home.project)]
    assert project_trust.project_decision(str(home.project)) == project_trust.DECISION_PREVIEW
    said = grants.consent(_trigger(config, provider="invoke-agent"), ["invoke-agent"])
    assert f"Its working folder {FOLDER} is in Preview" in said

    _spawned_with("invoke-agent", config, monkeypatch)
    assert asked == [str(home.project)], "the owner is asked once, not at every fire"


def test_invoke_agent_in_a_folder_you_trust_gets_the_access_its_step_asks_for(
    home, monkeypatch, asked
):
    project_trust.record_project_trust(str(home.project), trusted=True)
    config = {
        "task_template": "Review the change.",
        "cwd": FOLDER,
        "approval_mode": "auto",
        "capability": "mutating",
    }
    assert _spawned_with("invoke-agent", config, monkeypatch)["capability_class"] == "mutating"


# ── a reading automation is not asked about ─────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", ["run-prompt", "invoke-agent"])
def test_a_reading_automation_in_a_folder_you_have_not_trusted_asks_nothing(
    home, monkeypatch, asked, provider
):
    """🔴 Before: a reading Run Prompt automation's first fire asked the owner to trust its folder
    because "an automation wants to run project scripts", and recorded the folder as Preview."""
    key = "message" if provider == "run-prompt" else "task_template"
    config = {key: "Summarise the README.", "cwd": FOLDER, "approval_mode": "auto"}
    config["writes"] = ["~/Notes/site.md"]
    handed = _spawned_with(provider, config, monkeypatch)

    assert asked == []
    assert not (home.pc / "project_trust.json").exists()
    assert handed["capability_class"] == "research"
    # The files its Allow names one by one stay its to change: Preview holds back write access.
    assert handed["may_change"] == (str(home.notes / "site.md"),)
    assert handed["held_back"] == ""


# ── the trust changes after the Allow ───────────────────────────────────────────────────────────


def test_a_folder_put_back_in_preview_holds_its_run_back_and_its_history_says_so(
    home, monkeypatch, asked
):
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
    from personalclaw.subagent import SubagentInfo, agent_work_id
    from personalclaw.triggers.settle import settle_agent_run

    project_trust.record_project_trust(str(home.project), trusted=True)
    config = {"message": "Rebuild the site.", "cwd": FOLDER, "capability": "mutating"}
    allowed = grants.consent(_trigger(config), ["run-prompt"])
    assert "Preview" not in allowed

    project_trust.record_project_trust(str(home.project), trusted=False)
    handed = _spawned_with("run-prompt", config, monkeypatch)

    assert handed["capability_class"] == "research"
    assert handed["held_back"] == (
        f"Its agent only reads: its working folder {FOLDER} is in Preview until you trust it."
    )
    info = SubagentInfo(
        id="run-1",
        task="t",
        trigger_id=TRIGGER,
        result="The site needs a rebuild.",
        held_back=handed["held_back"],
    )
    store = ScheduleRunStore(home.pc)
    store.append_sync(
        ScheduleRun(job_id=TRIGGER, status="launched", work_id=agent_work_id("run-1"))
    )
    assert settle_agent_run(info, base_dir=home.pc)
    [row], _total = store._list_for_job_sync(TRIGGER, 0, 10)
    assert row["status"] == "success"
    assert row["summary"] == f"{handed['held_back']} The site needs a rebuild."


class _Dashboard:
    """What a gateway's ending of an agent reaches on the dashboard: its notes."""

    def __init__(self) -> None:
        self.notes: list[dict] = []

    def notify(self, kind, title, body, *, meta=None, raised_by_app=""):
        self.notes.append({"kind": kind, "title": title, "body": body, "meta": dict(meta or {})})

    def broadcast_ws(self, *_a, **_k) -> None:
        return None


def _ended(info) -> _Dashboard:
    """End the agent *info* describes the way a gateway does (`_subagent_done`), and return the
    dashboard its notes reached."""
    from unittest.mock import MagicMock, patch

    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        gateway = GatewayOrchestrator(cfg, no_dashboard=True, no_crons=True, no_open=True)
    dashboard = _Dashboard()
    gateway.dashboard_state = dashboard
    gateway.sessions = MagicMock()
    gateway.ctx_builder = MagicMock()
    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        with patch("personalclaw.gateway.SubagentManager") as manager:
            manager.return_value = MagicMock(running=[], get=MagicMock(return_value=None))
            manager.return_value.running_agents_for = MagicMock(return_value=[])
            gateway._init_subagents()
    asyncio.run(manager.call_args.kwargs["on_done"]([info]))
    return dashboard


def test_a_held_back_run_that_failed_says_why_in_the_inbox_too(home, monkeypatch, asked):
    """A failure its trigger files in the Inbox says what the run's history says: why it was held
    back, then how it failed. 🔴 Before: the Inbox item said only "Couldn't do its task: every
    tool call it made was refused", for a run its folder's Preview held to reading."""
    from personalclaw.inbox import InboxStore
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
    from personalclaw.subagent import SubagentInfo, agent_work_id
    from personalclaw.triggers.store import TriggerStore

    config = {"message": "Rebuild the site.", "cwd": FOLDER, "capability": "mutating"}
    trigger = _trigger(config)
    trigger.failure_delivery = "inbox"
    TriggerStore(base_dir=home.pc).upsert(trigger)
    held_back = _spawned_with("run-prompt", config, monkeypatch)["held_back"]
    ScheduleRunStore(home.pc).append_sync(
        ScheduleRun(job_id=TRIGGER, status="launched", work_id=agent_work_id("run-1"))
    )
    failed = "Couldn't do its task: every tool call it made was refused."
    _ended(
        SubagentInfo(
            id="run-1",
            task="Rebuild the site.",
            trigger_id=TRIGGER,
            title="Rebuild the site",
            error=failed,
            held_back=held_back,
        )
    )

    inbox = InboxStore()
    inbox.load()
    [item] = list(inbox.items.values())
    assert item.message == f"Rebuild the site failed\n\n{held_back} {failed}"
    [row], _total = ScheduleRunStore(home.pc)._list_for_job_sync(TRIGGER, 0, 10)
    assert row["summary"] == f"{held_back} {failed}"


def test_a_lifecycle_triggers_held_back_agent_says_why_when_it_ends(home, monkeypatch, asked):
    """A lifecycle trigger keeps no run rows, so the note its agent's ending sends is all that says
    how it went. 🔴 Before: it said what the agent replied, and nothing of the folder that held it
    to reading."""
    from personalclaw.subagent import SubagentInfo

    config = {"task_template": "Review the change.", "cwd": FOLDER, "capability": "mutating"}
    held_back = _spawned_with("invoke-agent", config, monkeypatch)["held_back"]
    assert held_back
    dashboard = _ended(
        SubagentInfo(
            id="run-2",
            task="Review the change.",
            trigger_id="lifecycle:review-on-stop",
            title="Review on stop",
            result="The change reads well; I could not run its tests.",
            held_back=held_back,
        )
    )

    [note] = dashboard.notes
    assert note["title"] == "Review on stop"
    assert note["body"] == f"{held_back} The change reads well; I could not run its tests."


def test_a_trigger_held_back_by_its_folder_says_so_and_names_the_folder_to_trust(home, asked):
    from personalclaw.dashboard.handlers.triggers import _serialize_store
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=home.pc)
    store.upsert(
        _trigger({"message": "Rebuild the site.", "cwd": FOLDER, "capability": "mutating"})
    )
    shown = _serialize_store(store.get(TRIGGER))
    assert shown["held_back"] == {
        "why": f"Its agent only reads: its working folder {FOLDER} is in Preview until you "
        "trust it.",
        "folder": str(home.project),
    }

    project_trust.record_project_trust(str(home.project), trusted=True)
    assert _serialize_store(store.get(TRIGGER))["held_back"] is None


def test_a_lifecycle_trigger_held_back_by_its_folder_says_so_too(home, asked):
    from personalclaw.dashboard.handlers.triggers import _serialize_lifecycle
    from personalclaw.hooks import ScriptHook

    hook = ScriptHook(
        id="review-on-stop",
        name="Review on stop",
        provider="invoke-agent",
        provider_config={"task_template": "Review the change.", "cwd": FOLDER},
    )
    assert _serialize_lifecycle(hook, [])["held_back"] == {
        "why": f"Its agent only reads: its working folder {FOLDER} is in Preview until you "
        "trust it.",
        "folder": str(home.project),
    }
