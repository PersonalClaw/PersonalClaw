"""An agent a lifecycle hook starts carries the hook, like one a stored trigger starts.

#3716 threaded the trigger through the two store-trigger dispatches, so an `invoke-agent` or
`run-prompt` agent's approval names the trigger and its note links to it. A lifecycle hook (a
trigger that fires on the agent's own events: a prompt, a tool call, a session starting) runs the
same actions through `hooks.run_script_hook`, which handed them no trigger. Its agent's approval
read "A subagent is waiting…", and a call nobody answered left a note pointing nowhere.

A hook is addressed as `lifecycle:<id>` on the Triggers page (`_serialize_lifecycle`), so that is
the id it carries: the page opens it by that id, and it cannot be mistaken for a stored trigger's.
"""

from __future__ import annotations

import pytest

import personalclaw.action_providers as AP
from personalclaw.action_providers.base import ActionContext, ActionResult
from personalclaw.hooks import ScriptHook, ScriptHookStore, run_script_hook
from personalclaw.triggers import grants
from personalclaw.triggers.store import trigger_name


class _Seen:
    def __init__(self) -> None:
        self.contexts: list[ActionContext] = []

    async def execute(self, config, ctx, timeout=30):
        self.contexts.append(ctx)
        return ActionResult(success=True, stdout="ran")


def _hook(**over) -> ScriptHook:
    hook = ScriptHook(
        id="h-prompt",
        name="Plan on every prompt",
        event="UserPromptSubmit",
        provider="invoke-agent",
        provider_config={"task_template": "write the plan"},
        **over,
    )
    grants.give(hook)
    return hook


@pytest.fixture
def seen(monkeypatch):
    recorder = _Seen()
    real = AP.get_action_provider
    monkeypatch.setattr(
        AP, "get_action_provider", lambda name: recorder if name == "invoke-agent" else real(name)
    )
    return recorder


@pytest.mark.asyncio
async def test_a_hook_hands_its_action_the_trigger_that_ran_it(seen):
    result = await run_script_hook(_hook(), "a prompt")
    assert not result.error, result.error
    assert [c.trigger_id for c in seen.contexts] == ["lifecycle:h-prompt"]


@pytest.mark.asyncio
async def test_so_does_its_test_from_the_trigger_page(seen):
    await run_script_hook(_hook(), "a prompt", test=True)
    assert [c.trigger_id for c in seen.contexts] == ["lifecycle:h-prompt"]


def test_the_hook_is_named_by_its_own_name():
    """The asker a note names: "The trigger “Plan on every prompt”", not "A trigger"."""
    ScriptHookStore().create(_hook().to_dict())
    assert trigger_name("lifecycle:h-prompt") == "Plan on every prompt"


def test_a_hook_that_is_gone_has_no_name():
    assert trigger_name("lifecycle:nope") == ""
