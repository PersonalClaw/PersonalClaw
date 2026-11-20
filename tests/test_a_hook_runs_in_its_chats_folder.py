"""A lifecycle hook runs in, and is told, the working folder of the chat or helper it fires for.

🔴 The defect (Backlog: "Lifecycle hooks run in the gateway's folder, not the chat's working
folder"). Every hook was told ``cwd: os.getcwd()`` and its command ran there: wherever the gateway
was started from, or a service manager's folder. A formatter, a linter or a ``git status`` written
to act on the project a chat works in acted on another folder, or found no project at all, and
its safety checks (a delete of the folder it runs in, a sensitive path) judged that same wrong
folder.

Now each fire site hands the hook store the folder of the chat or helper it fires for
(``memory_locality.chat_folder``, ``SubagentInfo.cwd``, the native runtime's own). The hook runs
there and is told so (``hooks.hook_folder``), and its action's checks judge that folder
(``ActionContext.cwd``). A fire for no chat (a Test from the Triggers page, an event) runs in the
workspace. The folder is never the event's to name, and a folder that is not one refuses the run
rather than running it somewhere else.

Each test runs the real hook store over a hook in its own home, in a gateway started from a folder
of its own (``gateway``), beside the workspace and the chat's project.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from personalclaw.hooks import (
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_EVENT_USER_PROMPT_SUBMIT,
    ScriptHook,
    ScriptHookStore,
    run_script_hook,
)

#: The owner's yes to what the hook runs (`triggers.grants`).
_GRANTED = {"providers": ["bash", "run-script"]}
#: The command every hook below runs: where it runs, then the event it was handed.
_SAYS_WHERE = "pwd; cat"


class _Places:
    def __init__(self, root: Path) -> None:
        self.gateway = root / "gateway"
        self.workspace = root / "workspace"
        self.project = root / "project"
        for folder in (self.gateway, self.workspace, self.project):
            folder.mkdir()

    @staticmethod
    def real(folder: Path) -> str:
        return os.path.realpath(folder)


@pytest.fixture
def places(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Places:
    """The gateway started from `gateway`, with `workspace` as its workspace and `project` as the
    folder a chat works in."""
    found = _Places(tmp_path)
    monkeypatch.chdir(found.gateway)
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(found.workspace))
    return found


def _hook(command: str = _SAYS_WHERE, *, event: str = HOOK_EVENT_PRE_TOOL_USE, **kw: Any):
    return ScriptHook(
        id=kw.pop("id", "where"),
        name=kw.pop("name", "where"),
        event=event,
        provider=kw.pop("provider", "bash"),
        provider_config=kw.pop("provider_config", {"command": command}),
        timeout=30,
        enabled=True,
        capabilities=_GRANTED,
        **kw,
    )


def _store(home: Path, hook: ScriptHook) -> ScriptHookStore:
    store = ScriptHookStore(home)
    store.create(hook.to_dict())
    return store


def _where(stdout: str) -> tuple[str, dict]:
    """The folder the hook printed it ran in, and the event it was handed (its output is fenced as
    untrusted content on the way back)."""
    said = [
        line
        for line in stdout.splitlines()
        if not line.startswith(("<untrusted_content", "</untrusted_content>"))
    ]
    return os.path.realpath(said[0].strip()), json.loads("\n".join(said[1:]) or "{}")


@pytest.mark.asyncio
async def test_a_hook_fired_for_a_chat_runs_in_its_folder_and_is_told_so(places, tmp_path):
    """🔴 Before: `fire_for_ids` took no folder; every hook ran, and was told it ran, in the
    gateway's."""
    store = _store(tmp_path / "home", _hook())
    (result,) = await store.fire_for_ids(
        HOOK_EVENT_PRE_TOOL_USE, ["where"], tool_name="Bash", cwd=str(places.project)
    )
    ran, event = _where(result.stdout)
    assert (ran, event["cwd"]) == (places.real(places.project),) * 2


@pytest.mark.asyncio
async def test_a_hook_fired_for_no_chat_runs_in_the_workspace(places, tmp_path):
    """🔴 Before: in the folder the gateway was started from, and told so."""
    store = _store(tmp_path / "home", _hook(event=HOOK_EVENT_USER_PROMPT_SUBMIT))
    (result,) = await store.fire_for_ids(HOOK_EVENT_USER_PROMPT_SUBMIT, ["where"], "hello")
    ran, event = _where(result.stdout)
    assert (ran, event["cwd"]) == (places.real(places.workspace),) * 2


@pytest.mark.asyncio
async def test_a_test_from_the_triggers_page_runs_in_the_workspace(places):
    """🔴 Before: a Test (no event) ran in the gateway's folder and said so."""
    result = await run_script_hook(_hook(), "", test=True)
    ran, event = _where(result.stdout)
    assert (ran, event["cwd"], event["test"]) == (places.real(places.workspace),) * 2 + (True,)


@pytest.mark.asyncio
async def test_a_delete_is_judged_against_the_folder_the_hook_runs_in(places, tmp_path):
    """🔴 Before: judged against the gateway's folder, so `rm -rf ../workspace`, a delete of the
    folder the hook runs in, was not refused, and ran from the gateway's folder."""
    store = _store(
        tmp_path / "home", _hook("rm -rf ../workspace", event=HOOK_EVENT_USER_PROMPT_SUBMIT)
    )
    (result,) = await store.fire_for_ids(HOOK_EVENT_USER_PROMPT_SUBMIT, ["where"], "hello")
    assert places.workspace.is_dir(), "the delete of the folder it runs in ran"
    # The action denylist, which every unattended dispatch asks before the action runs, judged it.
    assert result.error.startswith("blocked by the guardrails denylist"), result.error
    assert "delete the working folder" in result.error
    assert places.real(places.workspace) in result.error


@pytest.mark.asyncio
async def test_the_bash_action_judges_a_delete_against_the_folder_its_dispatch_set(places):
    """🔴 Before: the Bash action's own check judged the gateway's folder, so a delete of the folder
    it runs in, named from inside it, was not one."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.bash_provider import BashActionProvider

    ctx = ActionContext(event=HOOK_EVENT_PRE_TOOL_USE, cwd=places.real(places.project))
    result = await BashActionProvider().execute({"command": "rm -rf ../project"}, ctx)
    assert places.project.is_dir(), "the delete of the folder it runs in ran"
    assert not result.success and "folder" in result.error, result


@pytest.mark.asyncio
async def test_the_event_cannot_choose_where_a_hook_runs(places, tmp_path):
    """A `cwd` the call's input carries is the event's, and moves nothing: the folder comes from
    the chat the fire site read."""
    store = _store(tmp_path / "home", _hook())
    (result,) = await store.fire_for_ids(
        HOOK_EVENT_PRE_TOOL_USE,
        ["where"],
        tool_name="Bash",
        tool_input={"cwd": str(places.gateway)},
        cwd=str(places.project),
    )
    ran, event = _where(result.stdout)
    assert (ran, event["cwd"]) == (places.real(places.project),) * 2
    assert event["tool_input"] == {"cwd": str(places.gateway)}


@pytest.mark.parametrize("folder", ["gone", "relative"])
@pytest.mark.asyncio
async def test_a_chat_folder_that_is_not_one_refuses_the_hook(places, tmp_path, folder):
    """A chat folder that is gone, or one that is not a whole path, refuses the run: running it in
    the workspace, or relative to the gateway's folder (`../project` from it is a real folder),
    would act on the wrong files."""
    named = str(places.project.parent / "gone") if folder == "gone" else "../project"
    store = _store(tmp_path / "home", _hook("touch ran"))
    (result,) = await store.fire_for_ids(
        HOOK_EVENT_PRE_TOOL_USE, ["where"], tool_name="Bash", cwd=named
    )
    assert result.exit_code == 2 and "is not a folder it can run in" in result.error
    assert not any((p / "ran").exists() for p in (places.workspace, places.gateway, places.project))


@pytest.mark.asyncio
async def test_the_pre_tool_step_runs_a_hook_in_the_callers_folder(places, tmp_path, monkeypatch):
    """🔴 Before: the step every tool-call path asks (`pre_tool_hooks.on_tool`) took no folder."""
    from personalclaw import pre_tool_hooks

    store = _store(tmp_path / "home", _hook())
    monkeypatch.setattr(pre_tool_hooks, "bound_hook_ids", lambda agent: ["where"])
    seen: list[Any] = []
    said = await pre_tool_hooks.on_tool(
        "Bash", {}, agent=None, store=store, on_results=seen.extend, cwd=str(places.project)
    )
    assert not said.refused
    assert _where(seen[0].stdout)[0] == places.real(places.project)


@pytest.mark.asyncio
async def test_a_run_script_hook_runs_in_the_chats_folder(places, tmp_path, monkeypatch):
    """🔴 Before: a `run-script` hook's script ran in the gateway's folder."""
    from personalclaw import schedule_script

    crons = tmp_path / "crons"
    crons.mkdir()
    (crons / "where.py").write_text(
        "import os\n\ndef main(ctx):\n    return os.getcwd()\n", encoding="utf-8"
    )
    monkeypatch.setattr(schedule_script, "_crons_dir", lambda: crons)
    monkeypatch.setattr(schedule_script, "validate_file_path", lambda p: p)
    monkeypatch.setattr(schedule_script.gateway_base, "resolve_port", lambda: 9)
    hook = _hook(provider="run-script", provider_config={"script": f"{crons / 'where.py'}:main"})
    store = _store(tmp_path / "home", hook)
    (result,) = await store.fire_for_ids(
        HOOK_EVENT_PRE_TOOL_USE, ["where"], tool_name="Bash", cwd=str(places.project)
    )
    assert _where(result.stdout)[0] == places.real(places.project), result


# ── each fire site hands on the folder of the chat or helper it fires for ───────────────────────


class _Recorder:
    """The bound hooks' action, noting where its dispatch said it runs: the context's folder and
    the one its event was told."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str]] = []

    async def execute(self, config, ctx, timeout=30):
        from personalclaw.action_providers.base import ActionResult

        self.seen.append((ctx.event, str(getattr(ctx, "cwd", None)), str(ctx.payload.get("cwd"))))
        return ActionResult(success=True, exit_code=0)

    def folders(self) -> set[tuple[str, str]]:
        return {(cwd, told) for _event, cwd, told in self.seen}


@pytest.fixture
def bound(places, monkeypatch) -> _Recorder:
    """A `PreToolUse` and a `UserPromptSubmit` hook bound to the default agent, and an operator's
    pattern that approves `fs_write`, as the blocking-hook tests bind them."""
    import personalclaw.action_providers as action_providers
    from personalclaw.config.loader import config_dir

    recorder = _Recorder()
    monkeypatch.setattr(action_providers, "get_action_provider", lambda name: recorder)
    (config_dir() / "config.json").write_text(
        json.dumps(
            {
                "default_agent": "keeper",
                "agents": {"keeper": {"triggers": ["pre", "prompt"]}},
                "hooks": {"auto_approve_tools": ["fs_write"]},
                "updates": {"check_enabled": False},
            }
        ),
        encoding="utf-8",
    )
    store = ScriptHookStore()
    for hook_id, event in (
        ("pre", HOOK_EVENT_PRE_TOOL_USE),
        ("prompt", HOOK_EVENT_USER_PROMPT_SUBMIT),
    ):
        store.create(_hook(id=hook_id, name=hook_id, event=event).to_dict())
    monkeypatch.setattr("personalclaw.hooks._global_script_hook_store", store)
    return recorder


async def _a_chat_turn(places, tmp_path, *, app: str = "") -> dict:
    """One turn of a chat working in `project` (started by *app*, or by the owner), on an agent
    CLI: a prompt, a tool call its CLI reports, and the gate's request for that call. Returns what
    the turn asked the session manager to build its runtime with."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from test_a_blocking_hook_holds_on_every_path import _iter, _request

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import DashboardState, _ChatSession
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import HookManager, HooksConfig
    from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent
    from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_TOOL_CALL

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    client.provider_id = "acp:agent-cli"
    del client.cancel_session
    client.undelivered_steers = MagicMock(return_value=[])
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path / "c")
    )
    builder = MagicMock()
    builder.hooks = HookManager(HooksConfig(auto_approve_tools=["fs_write"]))
    builder.build_message.return_value = ("hello", None)
    state.context_builder = builder
    state._hook_store = ScriptHookStore()
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    session = _ChatSession(f"chat-in-a-project{'-' + app if app else ''}")
    session.workspace_dir = str(places.project)
    session.created_by_app = app
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter(
            [
                LLMEvent(
                    kind=EVENT_TOOL_CALL, title="fs_write", tool_call_id="c1", tool_input="{}"
                ),
                _request(),
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="done"),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await asyncio.wait_for(run_chat(state, session, "hello"), timeout=20)
    return dict(sessions.get_or_create.await_args.kwargs)


@pytest.mark.asyncio
async def test_a_chats_hooks_run_in_the_folder_the_chat_works_in(places, bound, tmp_path):
    """🔴 Before: the chat runner's fires (a prompt's, a tool call's, the gate's) named no folder,
    so each hook ran in the gateway's."""
    built = await _a_chat_turn(places, tmp_path)
    assert built["hooks_take_no_folder"] is False
    assert {event for event, _cwd, _told in bound.seen} >= {
        HOOK_EVENT_USER_PROMPT_SUBMIT,
        HOOK_EVENT_PRE_TOOL_USE,
    }, bound.seen
    assert bound.folders() == {(places.real(places.project),) * 2}, bound.seen


@pytest.mark.asyncio
async def test_a_chat_an_app_started_runs_the_owners_hooks_in_the_workspace(
    places, bound, tmp_path
):
    """An app may set its own chat's folder, and the hooks are the owner's commands, run with her
    authority: in a chat an app started, they run in the workspace, never in a folder it chose."""
    built = await _a_chat_turn(places, tmp_path, app="notes-helper")
    assert built["hooks_take_no_folder"] is True, "its built-in runtime takes no folder either"
    assert {event for event, _cwd, _told in bound.seen} >= {
        HOOK_EVENT_USER_PROMPT_SUBMIT,
        HOOK_EVENT_PRE_TOOL_USE,
    }, bound.seen
    assert bound.folders() == {(places.real(places.workspace),) * 2}, bound.seen


@pytest.mark.asyncio
async def test_a_helpers_hooks_run_in_the_helpers_folder(places, bound):
    """🔴 Before: a subagent's gate fired its hooks with no folder: the gateway's."""
    from unittest.mock import MagicMock, patch

    from test_a_blocking_hook_holds_on_every_path import _iter, _request
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.hooks import HookManager, HooksConfig
    from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent
    from personalclaw.subagent import SubagentInfo, SubagentManager

    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="")
    client = sessions.get_or_create.return_value[0]
    client.provider_id = "acp:agent-cli"
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter(
            [_request(), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")]
        )
    )
    ctx = _mock_ctx_builder()
    ctx.hooks = HookManager(HooksConfig(auto_approve_tools=["fs_write"]))
    manager = SubagentManager(sessions=sessions, ctx_builder=ctx, is_yolo=lambda: False)
    info = SubagentInfo(id="sa0001", task="tidy", parent_session_key="", cwd=str(places.project))
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel", MagicMock()):
        await manager._run_inner(info, "subagent:sa0001")
    assert [event for event, _cwd, _told in bound.seen] == [HOOK_EVENT_PRE_TOOL_USE]
    assert bound.folders() == {(places.real(places.project),) * 2}, bound.seen


async def _a_native_call(places, monkeypatch, **built: Any) -> None:
    """One call the built-in runtime makes in a session working in `project`, built as the bridge
    builds it with *built*, under YOLO, which approves whatever is left to approve."""
    from test_a_blocking_hook_holds_on_every_path import _Notes, _OneWrite

    import personalclaw.providers.provider_bridge as pb

    notes = _Notes()
    monkeypatch.setattr(pb, "resolve_provider_for_use_case", lambda *a, **k: _OneWrite())
    monkeypatch.setattr(pb, "_fallback_chat_model", lambda **k: "m")
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [notes])
    runtime = pb._build_native_runtime(
        use_case="chat",
        session_key="dashboard:chat-in-a-project",
        agent=None,
        model_override=None,
        cwd=str(places.project),
        **built,
    )
    runtime.set_approval_policy("yolo")
    await runtime.start()
    [event async for event in runtime.stream("go")]


@pytest.mark.asyncio
async def test_the_built_in_runtimes_hooks_run_in_its_sessions_folder(places, bound, monkeypatch):
    """🔴 Before: the runtime's hook step (`pre_tool_hooks.on_tool`) was bound with no folder."""
    await _a_native_call(places, monkeypatch)
    assert [event for event, _cwd, _told in bound.seen] == [HOOK_EVENT_PRE_TOOL_USE]
    assert bound.folders() == {(places.real(places.project),) * 2}, bound.seen


@pytest.mark.asyncio
async def test_the_built_in_runtime_of_an_apps_chat_runs_the_owners_hooks_in_the_workspace(
    places, bound, monkeypatch
):
    """The chat runner builds an app's chat's runtime with `hooks_take_no_folder`: its hook step
    runs the owner's hooks in the workspace, not in the folder the app set."""
    await _a_native_call(places, monkeypatch, hooks_take_no_folder=True)
    assert [event for event, _cwd, _told in bound.seen] == [HOOK_EVENT_PRE_TOOL_USE]
    assert bound.folders() == {(places.real(places.workspace),) * 2}, bound.seen


def test_the_bridge_hands_an_apps_chats_no_folder_rule_to_the_built_in_runtime():
    """`hooks_take_no_folder` reaches the native builder through the provider resolver, and never
    leaks into the model-axis resolvers, as the builder's other session arguments do."""
    from unittest.mock import MagicMock, patch

    import personalclaw.providers.provider_bridge as pb

    seen: dict = {}

    def _native(**kw):
        seen.update(kw)
        return MagicMock(name="NativeRuntime")

    with patch.object(pb, "_build_native_runtime", side_effect=_native):
        pb.resolve_provider_for_use_case(
            "chat", agent="personalclaw", provider_kind="native", hooks_take_no_folder=True
        )
    assert seen.get("hooks_take_no_folder") is True
