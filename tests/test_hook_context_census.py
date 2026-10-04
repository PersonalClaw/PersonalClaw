"""Every hook kind's context passes through one function on its way to the hook's action.

A lifecycle hook hands its action what its event carried, and every event reaches the action by
``hooks.run_script_hook``, whichever seam fired it: the agent-scoped one a chat turn and the native
runtime use (``ScriptHookStore.fire_for_ids``) and the informational one the other events take
(``ScriptHookStore.fire``). ``hooks.hand_on`` is where that context leaves the gateway: the words
it carries go through the injection screen and are fenced as data, with the hook as their source.
This census fires every declared event through both seams and checks that each fire's context
passed through ``hand_on``, and that what the action was handed is what ``hand_on`` made of it. A
new event, or a new way to run a hook's action, has to pass through it too.

What each event's context carries is pinned here, with why: words someone wrote are screened and
fenced; names (a session's key, ids, counts, the tool an approval is about) reach the action as
they are, so an action can use them as names.
"""

from __future__ import annotations

import ast
import inspect

import pytest

import personalclaw.action_providers as AP
from personalclaw import hooks
from personalclaw.action_providers.base import ActionResult
from personalclaw.hooks import HOOK_EVENTS, ScriptHook, ScriptHookStore
from personalclaw.security import is_fenced
from personalclaw.triggers import grants

#: What each event's context carries, as its fire site builds it.
CONTEXT_CARRIES: dict[str, str] = {
    "AgentSpawn": "names: the session's key (chat_runner)",
    "SessionStart": "names: the session's key (chat_runner)",
    "UserPromptSubmit": "words: the message the turn answers (chat_runner)",
    "PreToolUse": "names: nothing; the call rides the payload as it was made",
    "PostToolUse": "names: nothing; the call and its result ride the payload",
    "PreResponse": "names: the session and the agent (lifecycle_fire)",
    "PostResponse": "names: the session, the agent and two counts (lifecycle_fire)",
    "MemoryWrite": "names: the lesson's kind, key and scope (lifecycle_fire)",
    "ContextCompact": "names: the session and two sizes (lifecycle_fire)",
    "SubagentSpawn": "names: the subagent's id, its parent session and its agent (lifecycle_fire)",
    "TaskComplete": "words: the task's title, beside its id and status (workflows.pool)",
    "ApprovalRequest": "names: the tool asked about, where it came from (lifecycle_fire)",
    "Error": "words: the error's message the person was shown (chat_runner)",
    "SessionEnd": "names: the session, why it ended and a count (lifecycle_fire)",
    "Stop": "words: the agent's reply (chat_runner)",
}

#: The context each kind's fire hands in: words for the events that carry words.
_WORDS = "Plan the week around the dentist on Thursday."
_NAMES = "session=dashboard:chat-7 agent=planner"


def test_every_hook_kind_says_what_its_context_carries():
    assert set(CONTEXT_CARRIES) == set(HOOK_EVENTS), "an event nobody declared the context of"
    names = {event for event, carries in CONTEXT_CARRIES.items() if carries.startswith("names")}
    assert hooks.NAMES_ONLY_EVENTS == frozenset(names)


class _Action:
    def __init__(self) -> None:
        self.handed: list = []

    async def execute(self, config, ctx, timeout=30):
        self.handed.append(ctx)
        return ActionResult(success=True, stdout="Done.")


@pytest.fixture
def action(monkeypatch):
    recorder = _Action()
    real = AP.get_action_provider
    monkeypatch.setattr(
        AP, "get_action_provider", lambda name: recorder if name == "invoke-agent" else real(name)
    )
    return recorder


@pytest.fixture
def passed(monkeypatch):
    """Every ``hand_on`` call, with what it handed back."""
    calls: list[tuple[str, object]] = []
    real = hooks.hand_on

    def _spy(hook, context, hook_event):
        handed = real(hook, context, hook_event)
        calls.append((hook.id, handed))
        return handed

    monkeypatch.setattr(hooks, "hand_on", _spy)
    return calls


def _hook(store: ScriptHookStore, event: str) -> ScriptHook:
    hook = ScriptHook(
        id=f"census-{event}",
        name=f"{event} census",
        event=event,
        provider="invoke-agent",
        provider_config={"task_template": "note $CONTEXT"},
    )
    grants.give(hook)
    return store.create(hook.to_dict())


@pytest.mark.parametrize("seam", ["fire", "fire_for_ids"])
@pytest.mark.parametrize("event", HOOK_EVENTS)
@pytest.mark.asyncio
async def test_every_hook_kind_s_context_passes_through_the_one_function(
    tmp_path, action, passed, event, seam
):
    store = ScriptHookStore(config_dir=tmp_path)
    hook = _hook(store, event)
    context = _NAMES if event in hooks.NAMES_ONLY_EVENTS else _WORDS
    kwargs = {"context": context, "tool_response": {"output": "Monday: groceries."}}
    if seam == "fire":
        await store.fire(event, **kwargs)
    else:
        await store.fire_for_ids(event, [hook.id], **kwargs)

    assert [hook_id for hook_id, _ in passed] == [hook.id], f"{event} bypassed hooks.hand_on"
    (handed,) = action.handed
    _, made = passed[0]
    # What the action was handed is what `hand_on` made of it: its context and its event.
    assert handed.context == made.context and handed.payload == made.payload
    label = f"source=trigger:lifecycle:{hook.id} "
    if event in hooks.NAMES_ONLY_EVENTS:
        assert handed.context == context, f"{event}'s names were changed on the way"
    else:
        assert is_fenced(handed.context) and label in handed.context, handed.context
    output = handed.payload["tool_response"]["output"]
    assert is_fenced(output) and label in output, f"{event}'s tool result arrived unfenced"


def _calls_in(func, name: str) -> list[int]:
    """The lines in *func* that call something named *name* (a function or a method)."""
    tree = ast.parse(inspect.getsource(hooks))
    (node,) = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == func]
    return [
        call.lineno
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and (
            (isinstance(call.func, ast.Name) and call.func.id == name)
            or (isinstance(call.func, ast.Attribute) and call.func.attr == name)
        )
    ]


def test_one_function_runs_a_hook_s_action_and_it_hands_on_first_and_takes_in_after():
    """Structural, beside the sweep above: the sweep proves the events that exist, and this fails
    a second place in ``hooks.py`` that runs a hook's action, which the sweep would not see."""
    tree = ast.parse(inspect.getsource(hooks))
    executors = sorted(
        {
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            for call in ast.walk(fn)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "execute"
        }
    )
    assert executors == ["run_script_hook"], executors
    (execute,) = _calls_in("run_script_hook", "execute")
    handed_on = _calls_in("run_script_hook", "hand_on")
    taken_in = _calls_in("run_script_hook", "take_in")
    assert handed_on and min(handed_on) < execute, "the action can run before hand_on"
    assert taken_in and min(taken_in) > execute, "what the action printed is not taken in"
