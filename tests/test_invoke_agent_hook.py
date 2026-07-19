"""The ``invoke-agent`` hook action and its guards.

depth cap (no spawn at the limit), missing-field (error result),
services-unavailable (error), success (fire-and-forget spawn with rendered
args), and capacity (semaphore-locked → error). Also pins that
``fire_for_ids(depth=N)`` injects ``__hook_depth`` into the payload the provider
reads.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.invoke_agent_provider import (
    _HOOK_INVOKE_MAX_DEPTH,
    InvokeAgentActionProvider,
)


def _ctx(depth: int = 0, context: str = "diff") -> ActionContext:
    return ActionContext(event="Stop", context=context, payload={"__hook_depth": depth})


def test_depth_cap_does_not_spawn():
    res = asyncio.run(InvokeAgentActionProvider().execute({"task_template": "x"}, _ctx(_HOOK_INVOKE_MAX_DEPTH)))
    assert res.success is False and "depth cap" in res.error


def test_missing_task_is_error():
    res = asyncio.run(InvokeAgentActionProvider().execute({}, _ctx(0)))
    assert res.success is False and "task_template" in res.error


def test_services_unavailable_is_error(monkeypatch):
    import personalclaw.action_providers.invoke_agent_provider as mod

    monkeypatch.setattr(mod, "get_action_services", lambda: SimpleNamespace(subagents=None, spawn_background=lambda c: None))
    res = asyncio.run(InvokeAgentActionProvider().execute({"task_template": "x"}, _ctx(0)))
    assert res.success is False and "subagent manager unavailable" in res.error


def test_success_spawns_fire_and_forget(monkeypatch):
    import personalclaw.action_providers.invoke_agent_provider as mod

    spawned = {}
    fake_sub = SimpleNamespace(spawn=lambda **kw: spawned.update(kw))
    scheduled = []

    def _bg(coro):
        scheduled.append(coro)
        return asyncio.ensure_future(coro)

    monkeypatch.setattr(mod, "get_action_services", lambda: SimpleNamespace(subagents=fake_sub, spawn_background=_bg))

    async def go():
        res = await InvokeAgentActionProvider().execute(
            {"task_template": "Review $CONTEXT", "agent": "code-reviewer", "approval_mode": "auto"},
            _ctx(0),
        )
        await asyncio.sleep(0.05)  # let the fire-and-forget task run
        return res

    res = asyncio.run(go())
    assert res.success is True and "Review diff" in res.stdout
    assert scheduled, "spawn must be scheduled as a background task (never blocks lifecycle)"
    assert spawned.get("task") == "Review diff"
    assert spawned.get("agent") == "code-reviewer"
    assert spawned.get("approval_mode") == "auto"


def test_capacity_reached_is_error(monkeypatch):
    import personalclaw.action_providers.invoke_agent_provider as mod

    fake_sub = SimpleNamespace(spawn=lambda **kw: None)
    monkeypatch.setattr(
        mod, "get_action_services",
        lambda: SimpleNamespace(subagents=fake_sub, spawn_background=lambda c: None),
    )
    # A zero-permit semaphore is always locked → capacity guard trips before spawn.
    monkeypatch.setattr(mod, "_invoke_agent_sem", asyncio.Semaphore(0))
    res = asyncio.run(InvokeAgentActionProvider().execute({"task_template": "x"}, _ctx(0)))
    assert res.success is False and "capacity reached" in res.error


def test_fire_for_ids_injects_hook_depth(monkeypatch, tmp_path):
    """The depth the provider reads comes from fire_for_ids(depth=N)."""
    import asyncio as _asyncio

    from personalclaw.hooks import (
        HOOK_EVENT_STOP,
        ScriptHook,
        ScriptHookStore,
    )

    # Isolate to a tmp dir: fire_for_ids persists via _save_snapshot, so a default
    # ScriptHookStore() would leak this fixture hook (id="h") into the LIVE
    # ~/.personalclaw/hooks.json (observed reappearing as a phantom trigger).
    store = ScriptHookStore(config_dir=tmp_path)
    seen = {}

    async def _fake_run(hook, context, hook_event):
        seen["depth"] = hook_event.get("__hook_depth")
        return SimpleNamespace(
            hook_id=hook.id, hook_name=hook.name, event=hook.event, stdout="",
            stderr="", exit_code=0, error="", duration_ms=0,
        )

    import personalclaw.hooks as hooks_mod

    monkeypatch.setattr(hooks_mod, "run_script_hook", _fake_run)
    store._hooks["h"] = ScriptHook(
        id="h", name="n", event=HOOK_EVENT_STOP, matcher="",
        provider="notify", provider_config={}, enabled=True,
    )
    _asyncio.run(store.fire_for_ids(HOOK_EVENT_STOP, ["h"], depth=2))
    assert seen.get("depth") == 2
