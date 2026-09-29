"""The ``invoke-agent`` hook action and its guards.

depth cap (no spawn at the limit), missing-field (error result),
services-unavailable (error), success (a spawn with rendered args, which schedules
the child and returns), and a spawn refused on the spot (the fire's failure). Also
pins that ``fire_for_ids(depth=N)`` injects ``__hook_depth`` into the payload the
provider reads.
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
    res = asyncio.run(
        InvokeAgentActionProvider().execute({"task_template": "x"}, _ctx(_HOOK_INVOKE_MAX_DEPTH))
    )
    assert res.success is False and "depth cap" in res.error


def test_missing_task_is_error():
    res = asyncio.run(InvokeAgentActionProvider().execute({}, _ctx(0)))
    assert res.success is False and "task_template" in res.error


def test_services_unavailable_is_error(monkeypatch):
    import personalclaw.action_providers.invoke_agent_provider as mod

    monkeypatch.setattr(
        mod,
        "get_action_services",
        lambda: SimpleNamespace(subagents=None),
    )
    res = asyncio.run(InvokeAgentActionProvider().execute({"task_template": "x"}, _ctx(0)))
    assert res.success is False and "subagent manager unavailable" in res.error


def _services(spawned: dict, *, refused: str = "") -> SimpleNamespace:
    """A manager whose spawn records what it was asked and returns the child it started, or,
    given `refused`, one it refused on the spot, as `SubagentManager.spawn` does."""

    def _spawn(**kw):
        spawned.update(kw)
        return SimpleNamespace(id="c0ffee01", done=bool(refused), error=refused)

    return SimpleNamespace(subagents=SimpleNamespace(spawn=_spawn))


def test_success_spawns_fire_and_forget(monkeypatch):
    import personalclaw.action_providers.invoke_agent_provider as mod

    spawned: dict = {}
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawned))

    res = asyncio.run(
        InvokeAgentActionProvider().execute(
            {"task_template": "Review $CONTEXT", "agent": "code-reviewer", "approval_mode": "auto"},
            _ctx(0),
        )
    )
    assert res.success is True and "Review diff" in res.stdout
    assert res.outcome == "launched", "the child has only started; the lifecycle does not wait"
    assert spawned.get("task") == "Review diff"
    assert spawned.get("agent") == "code-reviewer"
    assert spawned.get("approval_mode") == "auto"


def test_a_spawn_refused_on_the_spot_is_the_fires_failure(monkeypatch):
    """🔴 Before: the spawn ran in a background task that dropped what it returned, so a refused
    spawn fired as "launched" and its trigger heard nothing, since a launch says nothing until the
    agent ends."""
    import personalclaw.action_providers.invoke_agent_provider as mod

    refused = "spawn refused: only 1.2 GB memory available (need 4 GB)"
    monkeypatch.setattr(mod, "get_action_services", lambda: _services({}, refused=refused))
    res = asyncio.run(InvokeAgentActionProvider().execute({"task_template": "x"}, _ctx(0)))
    assert res.success is False and res.error == f"invoke-agent: {refused}"


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

    # `enforced` rides down from `_fire`; swallowed because this double is about the
    # `__hook_depth` payload, and a double that rejects a real keyword fails on the wrong axis.
    async def _fake_run(hook, context, hook_event, *, enforced=False):
        seen["depth"] = hook_event.get("__hook_depth")
        return SimpleNamespace(
            hook_id=hook.id,
            hook_name=hook.name,
            event=hook.event,
            stdout="",
            stderr="",
            exit_code=0,
            error="",
            duration_ms=0,
        )

    import personalclaw.hooks as hooks_mod

    monkeypatch.setattr(hooks_mod, "run_script_hook", _fake_run)
    store._hooks["h"] = ScriptHook(
        id="h",
        name="n",
        event=HOOK_EVENT_STOP,
        matcher="",
        provider="notify",
        provider_config={},
        enabled=True,
    )
    _asyncio.run(store.fire_for_ids(HOOK_EVENT_STOP, ["h"], depth=2))
    assert seen.get("depth") == 2
