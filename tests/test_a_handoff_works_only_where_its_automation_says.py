"""Where an automation's handoff works is its owner's to say, never what fired it.

A second opinion hands stalled work to a different agent, which may edit the folder it is given.
An automation names that folder in its own settings (``workspace``), as its owner set it up and
allowed it. What fires the automation is event data: a webhook's body, an app's event, a
lifecycle hook's moment. It chooses nothing about where the work happens.

🔴 Red before: with no folder in its settings, the handoff took one from the fire's payload, its
``workspace`` or its ``cwd``. A lifecycle hook's payload always carries ``cwd``, PersonalClaw's own
process folder, so a hook's handoff with no folder of its own worked in PersonalClaw's own folder,
the one place a handoff must never fall back to. Now it does not start, and says what it needs.

`check-work` takes the same rule: an automation's check reads the folder it names (``root``), and
only a workflow step's reads its run's folder, which the engine gives it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from signed_in_gateway import Gateway, signed_in_gateway

import personalclaw.proposer.service as proposer
from personalclaw.action_providers.base import WORKFLOW_STEP_EVENT, ActionContext
from personalclaw.action_providers.check_work_provider import CheckWorkActionProvider
from personalclaw.action_providers.second_opinion_provider import SecondOpinionActionProvider
from personalclaw.cancellation import settle
from personalclaw.hooks import ScriptHook, run_script_hook
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

#: What every handoff needs besides where it works.
HANDOFF = {"goal": "green suite", "stuck_at": "one failing test", "origin_runner": "runner-a"}
#: A folder something other than the automation's owner names.
ELSEWHERE = "/srv/elsewhere"
AUTOMATION = "store:webhook:stuck-build"


@pytest.fixture
def asked(monkeypatch) -> list[dict[str, Any]]:
    """The handoffs asked for, by what each handed the proposer. None runs an agent."""
    asks: list[dict[str, Any]] = []

    async def _handoff(**kw: Any) -> Any:
        asks.append(kw)
        return SimpleNamespace(accepted=True, rejection="", to_dict=lambda: {"accepted": True})

    monkeypatch.setattr(proposer, "run_second_opinion", _handoff)
    return asks


def _fire(**payload: Any) -> ActionContext:
    """An automation's fire whose payload names a folder."""
    return ActionContext(
        event="webhook.fire", trigger_id="webhook:stuck-build", payload=dict(payload)
    )


# ── the provider ──


@pytest.mark.asyncio
async def test_an_automations_handoff_never_takes_its_folder_from_the_fires_payload(asked):
    """🔴 Red before: the handoff was asked to work in the folder the payload named."""
    result = await SecondOpinionActionProvider().execute(
        dict(HANDOFF), _fire(trigger_id="webhook:stuck-build", workspace=ELSEWHERE, cwd=ELSEWHERE)
    )

    assert asked == []
    assert result.success is False and result.failure_class == "user"
    assert "did not start" in result.error and "'workspace'" in result.error


@pytest.mark.asyncio
async def test_a_lifecycle_hooks_handoff_does_not_start_in_personalclaws_own_folder(asked):
    """🔴 Red before: a hook's payload carries ``cwd``, PersonalClaw's own process folder, and a
    hook's handoff with no folder of its own was asked to work there."""
    hook = ScriptHook(
        id="hand-off-when-stuck",
        name="Hand off when stuck",
        event="Stop",
        provider="second-opinion",
        provider_config=dict(HANDOFF),
        capabilities={"providers": ["second-opinion"]},
    )

    result = await run_script_hook(hook, "the agent stopped")

    assert asked == []
    assert "did not start" in result.error and "'workspace'" in result.error


@pytest.mark.asyncio
async def test_an_automation_with_its_own_folder_hands_off_there_whatever_fired_it(asked, tmp_path):
    """Control, the same before and after: the folder in its own settings is where it works."""
    result = await SecondOpinionActionProvider().execute(
        {**HANDOFF, "workspace": str(tmp_path)}, _fire(workspace=ELSEWHERE, cwd=ELSEWHERE)
    )

    assert result.success is True, result.error
    assert [a["workspace"] for a in asked] == [str(tmp_path)]


# ── through the webhook's door ──


def _webhook(home: Path, config: dict[str, Any]) -> None:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id="webhook:stuck-build",
            name="Stuck build",
            kind="webhook",
            created_by="user",
            workflow={"inline": {"provider": "second-opinion", "config": dict(config)}},
            capabilities={"providers": ["second-opinion"]},
        )
    )


async def _post_naming_a_folder(gw: Gateway) -> tuple[int, Any]:
    """A program fires the automation with a body that names a folder, and waits for the fire."""
    status, made = await gw.as_owner(
        "POST",
        "/api/external-access/clients",
        json={"label": "Build server", "surfaces": ["webhook"], "scope": {"trigger": AUTOMATION}},
    )
    assert status == 200, made
    async with aiohttp.ClientSession() as http:
        resp = await http.post(
            gw.url(f"/api/triggers/{AUTOMATION}/fire"),
            data=json.dumps({"workspace": ELSEWHERE, "cwd": ELSEWHERE}).encode(),
            headers={"Authorization": f"Bearer {made['token']}"},
        )
        answer = resp.status, await resp.json(content_type=None)
    await settle(gw.state._background_tasks)
    return answer


async def _history(gw: Gateway) -> list[dict]:
    status, body = await gw.as_owner("GET", f"/api/triggers/{AUTOMATION}/history")
    assert status == 200, body
    return list(body["runs"])


@pytest.mark.asyncio
async def test_a_webhook_body_naming_a_folder_does_not_move_the_handoff(
    tmp_path, monkeypatch, asked
):
    """The body is data the fire is about: it reaches the run fenced, under ``body``, and names
    no folder the handoff works in. With none in its own settings, the handoff does not start, and
    the automation's history says what it needs."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _webhook(gw.home, HANDOFF)

        status, body = await _post_naming_a_folder(gw)

        assert status == 202, body
        assert asked == []
        (run,) = await _history(gw)
        assert run["status"] == "failure" and "'workspace'" in run["error"], run


@pytest.mark.asyncio
async def test_a_webhook_automation_with_its_own_folder_hands_off_there(
    tmp_path, monkeypatch, asked
):
    """Control: the folder its owner named is where its handoff works, whatever the body says."""
    work = tmp_path / "work"
    work.mkdir()
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _webhook(gw.home, {**HANDOFF, "workspace": str(work)})

        status, body = await _post_naming_a_folder(gw)

        assert status == 202, body
        assert [a["workspace"] for a in asked] == [str(work)]


# ── check-work ──


@pytest.fixture
def checked(monkeypatch) -> list[str]:
    """The folders check-work checked against."""
    roots: list[str] = []

    def _derive_and_run(text: str, *, root: str, max_checks: int) -> Any:
        roots.append(root)
        return SimpleNamespace(verdict="pass", note="", results=[], failed=[])

    monkeypatch.setattr("personalclaw.check_work.derive_and_run", _derive_and_run)
    monkeypatch.setattr("personalclaw.check_work.render_report", lambda report: "report")
    return roots


@pytest.mark.asyncio
async def test_an_automations_check_reads_only_the_folder_it_names(checked, tmp_path):
    """🔴 Red before: with no ``root`` of its own, the check read the folder the payload named."""
    provider = CheckWorkActionProvider()

    refused = await provider.execute({"text": "wrote notes.md"}, _fire(workspace=ELSEWHERE))
    named = await provider.execute(
        {"text": "wrote notes.md", "root": str(tmp_path)}, _fire(workspace=ELSEWHERE)
    )

    assert refused.success is False and "'root'" in refused.error
    assert named.success is True
    assert checked == [str(tmp_path)]


@pytest.mark.asyncio
async def test_a_steps_check_reads_its_runs_folder(checked, tmp_path):
    """Control: a workflow step's check reads the folder its run works in, which the engine puts
    on the step's dispatch."""
    step = ActionContext(event=WORKFLOW_STEP_EVENT, payload={"workspace": str(tmp_path)})

    result = await CheckWorkActionProvider().execute({"text": "wrote notes.md"}, step)

    assert result.success is True
    assert checked == [str(tmp_path)]
