"""Lifecycle-hook honest status (T7): a fire-and-forget action that only LAUNCHED
a background turn records last_status='launched', not 'ok' — matching the
schedule path, so the lifecycle-trigger badge doesn't overstate it as success.
"""

from __future__ import annotations

import asyncio

import personalclaw.action_providers as action_providers_mod
from personalclaw.action_providers.base import ActionResult
from personalclaw.hooks import HOOK_EVENT_STOP, ScriptHook, run_script_hook


class _FakeProvider:
    def __init__(self, result: ActionResult) -> None:
        self._result = result

    async def execute(self, config, ctx, timeout=30):
        return self._result


def _hook() -> ScriptHook:
    return ScriptHook(
        id="h1", name="n", event=HOOK_EVENT_STOP, matcher="",
        provider="run-prompt", provider_config={"prompt_id": "x"}, enabled=True,
    )


def _run(result: ActionResult, monkeypatch) -> ScriptHook:
    # run_script_hook does `from personalclaw.action_providers import get_action_provider`
    # at call time, so patch it on that source module.
    monkeypatch.setattr(action_providers_mod, "get_action_provider", lambda name: _FakeProvider(result))
    hook = _hook()
    asyncio.run(run_script_hook(hook, "", {"hook_event_name": "Stop"}))
    return hook


def test_launched_outcome_records_launched(monkeypatch):
    hook = _run(ActionResult(success=True, outcome="launched"), monkeypatch)
    assert hook.last_status == "launched"  # NOT "ok"
    assert hook.run_count == 1


def test_plain_success_still_records_ok(monkeypatch):
    hook = _run(ActionResult(success=True, outcome=""), monkeypatch)
    assert hook.last_status == "ok"


def test_failure_records_error(monkeypatch):
    hook = _run(ActionResult(success=False, error="boom"), monkeypatch)
    assert hook.last_status == "error"


def test_blocked_records_blocked(monkeypatch):
    hook = _run(ActionResult(success=False, blocked=True), monkeypatch)
    assert hook.last_status == "blocked"
