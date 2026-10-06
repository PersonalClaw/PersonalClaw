"""What an agent's model is handed is masked, and a credential it needs is named, not shown.

🔴 THE DEFECT (measured on ``origin/main``). Every read an agent's model is handed went to the
model provider as stored unless its own tool happened to mask it: ``workflow_status`` and
``workflow_output``, the query-scored half of ``memory_recall``, ``triage_rules``' patterns, and
every ``read_file``, ``grep`` and ``bash`` output. The memory and lessons a new session starts with
were injected raw; a spawned agent was handed its task unmasked on purpose (``_raw_task``); a
scheduled prompt's ``{{secret:KEY}}`` was filled in before the agent read it; and ``env`` in the
agent's shell printed the credentials Settings → Secrets mirrors into the gateway's environment.

Each surface below plants a credential and reads back what the model is actually handed. The
credentials are fakes in real shapes, and ``_leaks`` checks every 12-character piece of each, so a
key cut in half by a projection is caught too.
"""

from __future__ import annotations

import asyncio
import json
import random
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider, format_tool_result
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.security import (
    HIDDEN_VALUE_KEPT,
    MARKER_CUT,
    redact_for_display,
    redact_for_model,
)
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult, tool_failure

#: A provider key in its real shape (the redactor's Anthropic rule), and a GitHub token.
KEY = "sk-ant-api03-" + "Q7x" * 14
TOKEN = "ghp_" + "Zr8" * 12
#: A stored password with no shape any pattern knows: only the tool that handed it out can mask it.
PASSWORD = "correct-horse-battery-staple"
MASK = "[REDACTED: credential]"


def _leaks(text: str, *secrets: str) -> list[str]:
    """Every secret whole, or any 12-character piece of one, found in *text*."""
    found = []
    for secret in secrets or (KEY, TOKEN):
        pieces = {secret[i : i + 12] for i in range(0, len(secret) - 11)}
        if secret in text or any(p in text for p in pieces):
            found.append(secret)
    return found


def _handed(result: ToolResult) -> str:
    """What the native loop hands the model for *result* (`format_tool_result`)."""
    return format_tool_result(result)


# ── the one mask ─────────────────────────────────────────────────────────────────────────────


def test_the_mask_is_idempotent_over_its_own_markers():
    """A second pass over masked text changes nothing, so a read masked for a page can be masked
    again at the model boundary. It used to take `password: [REDACTED: …]`'s field name with it."""
    names = ["api_key", "password", "secret_key", "access_token", "client_secret", "API_KEY"]
    seps = ["=", ": ", " = ", ":"]
    values = [KEY, TOKEN, "hunter2hunter2", "Bearer " + "q" * 30, "AKIAIOSFODNN7EXAMPLE"]
    rng = random.Random(3)
    for _ in range(3000):
        text = " ; ".join(
            rng.choice(names) + rng.choice(seps) + rng.choice(values)
            for _ in range(rng.randint(1, 3))
        )
        once = redact_for_model(text)
        assert redact_for_model(once) == once, text
    assert redact_for_model("password: [REDACTED: credential]") == (
        "password: [REDACTED: credential]"
    )


def test_the_model_mask_is_the_display_mask():
    """The model is shown the chips the user is shown, so a save that restores one restores both."""
    text = f"key={KEY} https://alice:pw123456@example.com/x {TOKEN}"
    assert redact_for_model(text) == redact_for_display(text)
    assert not _leaks(redact_for_model(text))


# ── the native loop's boundary ───────────────────────────────────────────────────────────────


class _Leaky(ToolProvider):
    """A tool no one has ever masked: it answers with whatever it is given."""

    def __init__(self, result: ToolResult) -> None:
        self._result = result

    @property
    def name(self) -> str:
        return "leaky"

    @property
    def display_name(self) -> str:
        return "Leaky"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="read_vault",
                description="d",
                parameters={"type": "object"},
                requires_approval=False,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return self._result


def _one_call() -> _ScriptedModel:
    return _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL, tool_call_id="c1", title="read_vault", tool_input="{}"
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )


def test_a_new_tool_is_masked_without_asking_to_be():
    """🔴 THE BOUNDARY. A provider nobody wrote a mask for, driven through a real native turn: the
    tool message the model reads on its next inference holds the marker, never the key, and the
    tool card shows the same string."""
    model = _one_call()
    runtime = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=[_Leaky(ToolResult(success=True, output=f"vault: {KEY}\ngh {TOKEN}"))],
    )

    async def turn() -> list[AgentEvent]:
        await runtime.start()
        return [ev async for ev in runtime.stream("go")]

    events = asyncio.run(turn())
    assert model.calls == 2, "the model must have been handed the tool result"
    handed = [m for m in model.seen_messages[1] if m.get("role") == "tool"]
    assert handed, "the second inference carries the tool message"
    body = str(handed[-1].get("content"))
    assert not _leaks(body), body
    assert MASK in body
    card = [e for e in events if e.kind == "tool_result"]
    assert card and MASK in str(card[-1].tool_output) and not _leaks(str(card[-1].tool_output))


def test_a_failed_answer_is_masked_too():
    result = ToolResult(
        success=False, error=f"auth failed for {KEY}", recovery_hints=[f"retry with {TOKEN}"]
    )
    handed = _handed(result)
    assert not _leaks(handed) and handed.count(MASK) == 2


# ── the ACP agent's boundary: PersonalClaw's MCP server ──────────────────────────────────────


def test_an_acp_agents_call_is_answered_masked(monkeypatch):
    """The twin of `format_tool_result` for an ACP CLI: what `tools/call` answers is masked, and a
    failure keeps `isError`."""
    from personalclaw import mcp_shared

    frames = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ok", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "bad", "arguments": {}},
        },
    ]
    stdin = types.SimpleNamespace(
        buffer=types.SimpleNamespace(
            readline=iter([(json.dumps(f) + "\n").encode() for f in frames] + [b""]).__next__,
        )
    )
    monkeypatch.setattr(mcp_shared.sys, "stdin", stdin)
    monkeypatch.setattr(mcp_shared, "_resolve_excluded_tools", lambda: set())
    answered: dict[Any, Any] = {}
    monkeypatch.setattr(
        mcp_shared, "respond", lambda req_id, result, error=None: answered.update({req_id: result})
    )

    def call(name: str, args: dict[str, Any]) -> str:
        if name == "ok":
            return f"the run's input: {KEY}"
        return tool_failure(f"upstream said {TOKEN}")

    mcp_shared.run_mcp_stdio_loop("personalclaw-core", "1", lambda: [], call)
    ok_text = json.dumps(answered[1])
    bad = answered[2]
    assert not _leaks(ok_text) and MASK in ok_text
    assert bad.get("isError") is True and not _leaks(json.dumps(bad)) and MASK in json.dumps(bad)


# ── every inbound surface's one wrapper ──────────────────────────────────────────────────────


def test_another_agent_reading_an_inbound_surface_gets_masked_text():
    """`inbound/framing.fence_payload` is what every inbound surface answers through: an MCP tool
    result, an A2A artifact, a bridge answer, a webhook's body on its way into a trigger."""
    from personalclaw.inbound import framing

    body = f"task description: deploy with {KEY}"
    fenced = framing.fence_payload(body, surface="mcp", client_id="c1", detail="task_get")
    assert not _leaks(fenced) and MASK in fenced
    result = framing.mcp_tool_result(body, tool="task_get")
    assert not _leaks(json.dumps(result))


# ── the surfaces the policy names, as the agent receives them ────────────────────────────────


def _agent_reads(module: str, name: str, args: dict[str, Any]) -> str:
    """One of PersonalClaw's own tools, called the way the native loop calls it."""
    provider = InProcessMcpToolProvider(module=module)
    return _handed(asyncio.run(provider.invoke(name, args)))


def test_workflow_status_and_output_are_masked(monkeypatch):
    from personalclaw.workflows import service

    monkeypatch.setattr(
        service,
        "status",
        lambda run_id: {"ok": True, "run": {"id": run_id, "inputs": {"token": KEY}}},
    )
    monkeypatch.setattr(
        service,
        "output",
        lambda run_id, node_id="": {"ok": True, "output": f"fetched: Authorization {TOKEN}"},
    )
    for name, args in (
        ("workflow_status", {"run_id": "a1b2c3d4"}),
        ("workflow_output", {"run_id": "a1b2c3d4", "node_id": "fetch"}),
    ):
        seen = _agent_reads("personalclaw.mcp_workflows", name, args)
        assert not _leaks(seen), (name, seen)
        assert MASK in seen


def test_memory_recall_and_approval_rule_patterns_are_masked(monkeypatch):
    from personalclaw import mcp_memory

    def fake_get(path: str) -> dict[str, Any]:
        if path.startswith("/api/memory/recall"):
            return {"result": f"- github: token {TOKEN}\n- provider: api_key={KEY}"}
        return {"rules": [{"verdict": "deny", "pattern": f"send:{KEY}", "key": "k1"}]}

    monkeypatch.setattr(mcp_memory, "_get", fake_get)
    recalled = _agent_reads("personalclaw.mcp_memory", "memory_recall", {"query": "github"})
    rules = _agent_reads("personalclaw.mcp_memory", "triage_rules_list", {})
    for seen in (recalled, rules):
        assert not _leaks(seen), seen
        assert MASK in seen
    assert "(id: k1)" in rules, "a rule is still revoked by the id the list shows"


def test_the_recall_route_masks_its_semantic_half():
    """The Memory page's recall test and the agent's `memory_recall` both read this route; its
    fact list was masked and its recall of the same facts was not. The lessons it recalls beside
    them are masked the same way."""
    from personalclaw.dashboard.handlers import memory as memory_handlers
    from personalclaw.memory_service import QueryVector

    class _Svc:
        def embed_query(self, text: str) -> QueryVector:
            return QueryVector(None)

        def semantic_context(self, query: str, *, cap: int, query_vector: Any = None) -> str:
            return f"github_token: {TOKEN}\nkey: api_key={KEY}"

        def record_recall(self, keys: list[str]) -> None:
            self.recorded = keys

        def recall_with_provenance(self, **kw: Any) -> list[dict[str, Any]]:
            return []

        def recall_lessons(self, **kw: Any) -> list[dict[str, Any]]:
            return [
                {
                    "text": f"Deploy with api_key={KEY} to staging only.",
                    "source": "",
                    "created_at": "",
                }
            ]

    svc = _Svc()
    request = MagicMock()
    request.query = {"q": "github"}
    request.headers = {}
    with (
        patch.object(memory_handlers, "_memory_refusal", lambda *a: ""),
        patch.object(memory_handlers, "_get_service", lambda state: svc),
    ):
        response = asyncio.run(memory_handlers.api_memory_recall(request))
    body = json.loads(response.text)["result"]
    assert not _leaks(body) and MASK in body
    assert "Deploy with" in body and "to staging only." in body, "the lesson is recalled, masked"
    assert "github_token" in svc.recorded, "the recall is still counted against the stored key"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "config.env").write_text(f"ANTHROPIC_KEY={KEY}\nPORT=8080\nGH={TOKEN}\n")
    return tmp_path


def test_a_file_read_grep_and_shell_output_are_masked(ws):
    tools = NativeBuiltinToolProvider(ws, session_key="s-read")
    for name, args in (
        ("read_file", {"path": "config.env"}),
        ("grep", {"query": "KEY", "path": "."}),
        ("bash", {"command": "cat config.env"}),
    ):
        seen = _handed(asyncio.run(tools.invoke(name, args)))
        assert not _leaks(seen), (name, seen)
        assert MASK in seen, (name, seen)


def test_a_projected_result_and_its_retrieved_slices_never_split_a_key(ws):
    """A long result is projected and its raw kept for `tool_result_get`; both are cut from the
    masked text, so no slice can hand the model a key's prefix."""
    lines = [f"line {i}: api_key={KEY} {TOKEN}" for i in range(4000)]
    (ws / "big.log").write_text("\n".join(lines))
    tools = NativeBuiltinToolProvider(ws, session_key="s-big")
    # `read_file` hands the projection the file as stored; `bash` masks its output before that.
    for name, args in (("read_file", {"path": "big.log"}), ("bash", {"command": "cat big.log"})):
        first = asyncio.run(tools.invoke(name, args))
        handed = _handed(first)
        assert not _leaks(handed), name
        ref = (first.metadata or {}).get("raw_ref")
        assert ref, f"{name}: the result was projected and its raw retained"
        for start in (0, 7, 5003, 77777):
            piece = asyncio.run(
                tools.invoke(
                    "tool_result_get", {"result_id": ref, "start": start, "end": start + 999}
                )
            )
            assert piece.success and not _leaks(_handed(piece)), (name, start)


# ── what a prompt is assembled from ──────────────────────────────────────────────────────────


class _Memory:
    """A memory service holding a key in each block a session starts with."""

    _vs = None

    def get_context(self, **kw: Any) -> str:
        return f"[MEMORY] the deploy key is {KEY}\n"

    def episodic_context(self, **kw: Any) -> str:
        return f"[EPISODE] pasted {TOKEN} last week"

    def lessons_context(self, cwd: Any = None) -> str:
        return f"[LESSON] use api_key={KEY} for staging\n"

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **kw: ""


def _builder(tmp_path: Path) -> Any:
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    memory = MemoryStore(workspace=tmp_path / "ws")
    memory.init()
    builder = ContextBuilder(
        memory=memory,
        skills=SkillsLoader(skills_path=tmp_path / "sk", install_builtins=False),
    )
    builder.get_memory_for = staticmethod(lambda cwd=None, memory_store=None: memory)  # type: ignore[assignment]  # noqa: E501
    return builder


def test_the_memory_a_session_starts_with_is_masked_and_the_request_is_not(tmp_path):
    """Memory, lessons and episodes are read into the prompt; what the person typed is theirs."""
    with patch("personalclaw.memory_service.service_for", lambda memory: _Memory()):
        message, _ = _builder(tmp_path).build_message(
            f"my new key is {TOKEN}, store it", True, "dashboard:t1"
        )
    head, _, request = message.rpartition("my new key is")
    assert not _leaks(head, KEY, TOKEN), head
    assert MASK in head
    assert TOKEN in request, "the request is sent as it was typed"


class _Recalling(_Memory):
    """The memory the chat path reads outside the prompt's parts: the query-scored recall and
    the push reflex, each holding a credential."""

    def active_recall(self, text: str, cap: int = 2000) -> str:
        return f"- deploy: api_key={KEY}"

    def push_context(self, turns: list[str], **kw: Any) -> tuple[str, Any]:
        return f"[PUSHED] the bot token is {TOKEN}\n", None


def test_the_recall_the_chat_path_prepends_is_masked(tmp_path, monkeypatch):
    """Active recall and the push reflex are read into the prompt ahead of `build_message`'s
    parts, so each is masked as it is read."""
    import personalclaw.context_engine as ce
    from personalclaw import memory_locality

    builder = _builder(tmp_path)
    monkeypatch.setattr(ce, "_active_recall_enabled", lambda: (True, 5000))
    monkeypatch.setattr(ce, "_push_settings", lambda: (True, 0.0))
    monkeypatch.setattr(memory_locality, "compose_recall", lambda *a, local, **kw: local)
    with patch("personalclaw.memory_service.service_for", lambda memory: _Recalling()):
        recall = ce.active_recall_block(builder, "deploy", cwd=None, memory_store=None)
        pushed = ce.push_context_block(builder, "deploy", cwd=None, memory_store=None)
        out = ce.assemble_context(
            builder, "deploy", is_new_session=True, session_key="c1", cwd=None
        )
    assert "ACTIVE RECALL" in recall and "[PUSHED]" in pushed, "both blocks were read"
    for text in (recall, pushed, out.message):
        assert not _leaks(text), text
        assert MASK in text
    assert all(not _leaks(c.text) for c in out.components)


def test_a_cancelled_turn_read_back_is_masked_before_it_is_cut():
    """The turn a stop cut short is read back in front of the next request, which goes as typed,
    so it is masked where it is read. Masked before its 2000-character cap: cut first, the cap
    would leave a key's first 24 characters, which no pattern knows as a key."""
    from personalclaw.context import build_cancelled_turn_preamble

    turns = [
        {"role": "user", "content": "x" * 1975 + " " + KEY},
        {"role": "assistant", "content": f"started with {TOKEN}"},
    ]
    log = types.SimpleNamespace(recent=lambda key, max_messages=20: turns)
    preamble = build_cancelled_turn_preamble(log, "s1")
    assert "started with" in preamble, preamble
    assert not _leaks(preamble), preamble
    assert MASK in preamble


@pytest.mark.asyncio
async def test_what_a_chat_turn_puts_ahead_of_the_request_is_masked(tmp_path, monkeypatch):
    """An app's background context, a subagent's failure notice, the project's record and a hook's
    output are put in front of the request, and the request goes as typed, so each is masked where
    it joins. The request itself still goes as typed."""
    import time as _time

    from chat_test_helpers import _make_state

    from personalclaw.dashboard import chat_runner
    from personalclaw.dashboard.chat import run_chat
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

    typed = "ghp_" + "Yk4" * 12
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        chat_runner, "_project_context_preamble", lambda pid: f"[PROJECT] deploy key {KEY}"
    )
    state = _make_state(tmp_path)
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.context_builder = _builder(tmp_path)
    state.consolidator = None
    # The owner's hook commands inject their output, the way a command's output is read.
    state._hook_store = MagicMock()
    state._hook_store.fire_for_ids = AsyncMock(
        return_value=[
            types.SimpleNamespace(
                exit_code=0, stdout=f"env says GH={TOKEN}", hook_name="env", stderr=""
            )
        ]
    )
    session = state.get_or_create_session("s1")
    session.project_id = "p1"
    session._pending_context.append(
        {"source": "mail", "content": f"reset link token={TOKEN}", "injectedAt": _time.time()}
    )
    session._owed_subagent_endings.append(f"a subagent failed calling with api_key={KEY}")
    handed: list[str] = []
    client = AsyncMock()
    client.context_usage_pct = MagicMock(return_value=10.0)

    async def _stream(msg: str) -> Any:
        handed.append(str(msg))
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE)

    client.stream = _stream
    client.stream_command = _stream
    state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    await run_chat(state, session, f"use my token {typed}")
    assert handed, "the turn reached the model"
    sent = handed[-1]
    assert "[PROJECT]" in sent and "reset link" in sent and "a subagent failed" in sent, sent
    assert "[Hook context]" in sent and "env says" in sent, sent
    assert not _leaks(sent, KEY, TOKEN), sent
    assert typed in sent, "the request is sent as it was typed"


def test_a_webhook_turn_reads_its_saved_context_back_masked(monkeypatch):
    """A webhook callback's saved context is read back from a prior session and put in front of
    the webhook's own message, which is sent as it came."""
    import time

    from personalclaw import webhook_callbacks
    from personalclaw.dashboard.handlers import hooks as hook_handlers

    callback = webhook_callbacks.Callback(
        id="ci", context_summary=f"last deploy used api_key={KEY}", registered_at=time.time()
    )
    restored = webhook_callbacks.restored_context(callback).text
    seen: dict[str, str] = {}

    async def inner(state: Any, session_key: str, message: str, agent: Any) -> str:
        seen["message"] = message
        return ""

    monkeypatch.setattr(hook_handlers, "_run_hook_inner", inner)
    state = MagicMock()
    state.sessions.reset = AsyncMock()
    state.sessions.record_failure = AsyncMock()

    async def go() -> None:
        await hook_handlers._hook_semaphore.acquire()
        await hook_handlers._run_hook_agent(
            state, "hook:ci", f"build {TOKEN} finished", "CI", None, False, 30, restored=restored
        )

    asyncio.run(go())
    message = seen["message"]
    head, _, sent_as_it_came = message.rpartition("build ")
    assert "last deploy used" in head and MASK in head and not _leaks(head, KEY), message
    assert TOKEN in sent_as_it_came, "the webhook's own message is sent as it came"


def test_an_attached_files_text_is_masked(monkeypatch):
    from personalclaw.dashboard import attachment_extract, chat_runner
    from personalclaw.knowledge.extract import Extracted

    extracted = Extracted(f"DB_URL=postgres://app:{PASSWORD}@db/app\nk={KEY}", True)
    extractor = types.SimpleNamespace(get=AsyncMock(return_value=extracted))
    monkeypatch.setattr(attachment_extract, "get_extractor", lambda: extractor)
    block = asyncio.run(chat_runner._attachment_text_blocks(["/uploads/app.env"]))
    assert not _leaks(block, KEY, PASSWORD), block
    assert "[REDACTED: url credential]" in block


def test_a_spawned_agents_task_is_masked():
    """`_raw_task` was 'the unredacted prompt for ACP agent execution'. A spawned agent's task is
    composed from reads, so it is masked as it becomes the agent's prompt; a reference is a name
    and passes through for the agent's tools."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentManager

    ctx = _mock_ctx_builder()
    manager = SubagentManager(sessions=_mock_sessions(), ctx_builder=ctx, is_yolo=lambda: True)

    async def go() -> None:
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            info = manager.spawn(f"deploy using {KEY}, then call {{{{secret:DEPLOY_TOKEN}}}}")
            assert info is not None
            await manager._tasks[info.id]
            await manager.flush_deliveries()

    asyncio.run(go())
    sent = ctx.build_message.call_args[0][0]
    assert not _leaks(sent) and MASK in sent
    assert "{{secret:DEPLOY_TOKEN}}" in sent


@pytest.mark.asyncio
async def test_a_heartbeat_task_is_masked():
    from test_gateway import _make_orchestrator
    from test_gateway import _mock_sessions as _gateway_sessions

    orch = _make_orchestrator()
    orch.sessions = _gateway_sessions()
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    orch.ctx_builder.hooks = MagicMock()
    orch.consolidator = MagicMock()
    orch.dashboard_state = None
    orch._deliver_result = AsyncMock()
    with patch(
        "personalclaw.gateway.stream_and_collect", new_callable=AsyncMock, return_value="ok"
    ):
        await orch._run_heartbeat_task(f"rotate {KEY} when it expires", "")
    sent = orch.ctx_builder.build_message.call_args[0][0]
    assert not _leaks(sent) and MASK in sent


def test_the_context_an_external_agent_is_routed_is_masked():
    """The routed context feeds `get_context`, the context endpoints and the adapter files an
    agent CLI reads straight off the disk; its memory lines are cut at 240 characters, so they
    are masked before anything cuts them."""
    from personalclaw.legibility import context_router as cr

    routed = cr.assemble(
        project_id="p1",
        project_name="Deploys",
        brief=f"ship it with api_key={KEY}",
        memories=[{"text": "x" * 230 + TOKEN}],
        knowledge=[{"id": "k1", "title": f"notes {KEY}", "summary": None}],
    )
    for text in (routed.render(), json.dumps(routed.to_dict()), cr.render_block(routed)):
        assert not _leaks(text), text
    assert "**k1**" not in routed.render(), "a knowledge item with a title keeps it"


# ── an agent's writes keep what it was shown masked ──────────────────────────────────────────


@pytest.fixture
def config_file(tmp_path: Path) -> tuple[Path, NativeBuiltinToolProvider]:
    path = tmp_path / "app.env"
    path.write_text(f"# app\nexport ANTHROPIC={KEY}\nexport GH={TOKEN}\nname = demo\n")
    tools = NativeBuiltinToolProvider(tmp_path, session_key="s-write")
    seen = _handed(asyncio.run(tools.invoke("read_file", {"path": "app.env"})))
    assert KEY not in seen and "export GH=[REDACTED: credential]" in seen
    return path, tools


def _call(tools: NativeBuiltinToolProvider, name: str, args: dict[str, Any]) -> ToolResult:
    return asyncio.run(tools.invoke(name, args))


def test_an_edit_written_against_the_masked_file_keeps_every_hidden_value(config_file):
    path, tools = config_file
    done = _call(
        tools, "edit_file", {"path": "app.env", "old_str": "name = demo", "new_str": "name = prod"}
    )
    assert done.success, done.error
    assert path.read_text() == f"# app\nexport ANTHROPIC={KEY}\nexport GH={TOKEN}\nname = prod\n"
    renamed = _call(
        tools,
        "edit_file",
        {
            "path": "app.env",
            "old_str": "export GH=[REDACTED: credential]",
            "new_str": "export GITHUB_TOKEN=[REDACTED: credential]",
        },
    )
    assert renamed.success, renamed.error
    assert f"export GITHUB_TOKEN={TOKEN}\n" in path.read_text()
    assert "stay as they were" in renamed.output


def test_an_edit_that_cuts_or_copies_a_marker_is_refused(config_file):
    path, tools = config_file
    before = path.read_text()
    cut = _call(
        tools, "edit_file", {"path": "app.env", "old_str": "GH=[REDACTED: cred", "new_str": "x"}
    )
    copied = _call(
        tools,
        "edit_file",
        {"path": "app.env", "old_str": "name = demo", "new_str": "name = [REDACTED: credential]"},
    )
    assert (cut.success, cut.error) == (False, MARKER_CUT)
    assert (copied.success, copied.error) == (False, HIDDEN_VALUE_KEPT)
    assert path.read_text() == before


def test_an_edit_can_remove_a_hidden_value(config_file):
    path, tools = config_file
    gone = _call(
        tools,
        "edit_file",
        {"path": "app.env", "old_str": "export ANTHROPIC=[REDACTED: credential]\n", "new_str": ""},
    )
    assert gone.success, gone.error
    assert KEY not in path.read_text() and f"export GH={TOKEN}" in path.read_text()


def test_a_whole_file_write_keeps_hidden_values_on_the_lines_it_kept(config_file):
    path, tools = config_file
    shown = redact_for_display(path.read_text())
    written = _call(
        tools,
        "write_file",
        {"path": "app.env", "content": shown.replace("name = demo", "name = prod") + "debug = 1\n"},
    )
    assert written.success, written.error
    assert path.read_text() == (
        f"# app\nexport ANTHROPIC={KEY}\nexport GH={TOKEN}\nname = prod\ndebug = 1\n"
    )


def test_a_whole_file_write_that_moves_a_hidden_value_is_refused(config_file):
    path, tools = config_file
    before = path.read_text()
    lines = redact_for_display(before).splitlines(keepends=True)
    lines[1], lines[2] = lines[2], lines[1]
    moved = _call(tools, "write_file", {"path": "app.env", "content": "".join(lines)})
    assert (moved.success, moved.error) == (False, HIDDEN_VALUE_KEPT)
    assert path.read_text() == before


def test_a_marker_written_into_a_new_file_is_text_and_says_so(tmp_path):
    tools = NativeBuiltinToolProvider(tmp_path)
    made = _call(tools, "write_file", {"path": "copy.env", "content": f"KEY={MASK}\n"})
    assert made.success and "written as plain text" in made.output
    assert (tmp_path / "copy.env").read_text() == f"KEY={MASK}\n"


def test_a_task_update_keeps_a_value_task_get_showed_masked(monkeypatch):
    from personalclaw.tasks import registry

    stored = types.SimpleNamespace(
        id="t1",
        to_dict=lambda: {"title": "Deploy", "description": f"use {KEY} on staging"},
    )
    captured: dict[str, Any] = {}

    async def update_task(item_id: str, **fields: Any) -> Any:
        captured.update(fields)
        return types.SimpleNamespace(id=item_id)

    monkeypatch.setattr(registry, "get_task", AsyncMock(return_value=stored))
    monkeypatch.setattr(registry, "engine_owned_refusal", AsyncMock(return_value=""))
    monkeypatch.setattr(registry, "update_task", update_task)
    tools = NativeBuiltinToolProvider(Path("."))
    monkeypatch.setattr(tools, "_task_line", lambda task: "t1", raising=False)
    shown = redact_for_display(f"use {KEY} on staging")
    done = _call(tools, "task_update", {"id": "t1", "description": shown + ", then prod"})
    assert done.success, done.error
    assert captured["description"] == f"use {KEY} on staging, then prod"


def test_criteria_sent_back_as_json_text_keep_a_value_task_get_showed_masked(monkeypatch):
    """A model that sends `exit_criteria` as the text of its JSON list is read as that list, and
    its hidden values are restored on the list: as a string it passed the mask step untouched, so
    the marker would have been stored in place of the real value."""
    from personalclaw.tasks import registry

    criterion = f"rotate {KEY} on staging"
    stored = types.SimpleNamespace(
        id="t1",
        to_dict=lambda: {
            "title": "Deploy",
            "exit_criteria": [
                {"description": criterion, "status": "incomplete", "comment": "", "met": False}
            ],
        },
    )
    captured: dict[str, Any] = {}

    async def update_task(item_id: str, **fields: Any) -> Any:
        captured.update(fields)
        return types.SimpleNamespace(id=item_id)

    monkeypatch.setattr(registry, "get_task", AsyncMock(return_value=stored))
    monkeypatch.setattr(registry, "engine_owned_refusal", AsyncMock(return_value=""))
    monkeypatch.setattr(registry, "update_task", update_task)
    tools = NativeBuiltinToolProvider(Path("."))
    monkeypatch.setattr(tools, "_task_line", lambda task: "t1", raising=False)
    sent = json.dumps([{"description": redact_for_display(criterion), "met": True}])
    done = _call(tools, "task_update", {"id": "t1", "exit_criteria": sent})
    assert done.success, done.error
    assert captured["exit_criteria"] == [{"description": criterion, "met": True}]


def test_an_automation_update_keeps_the_value_its_dry_run_showed_masked(tmp_path):
    """🔴 Live on `main` since the automation reads were masked (#3722): `automation_update` set
    the patch as sent, so the marker the agent was shown replaced the credential."""
    from personalclaw.nl_to_cron import Schedule
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    task = f"Deploy with {KEY}"
    created = T.create(
        store,
        name="Deploy",
        when="every day at 9am",
        workflow={"provider": "invoke-agent", "config": {"task_template": task}},
        created_by="user",
        cadence_to_cron=lambda _c: Schedule(expr="0 9 * * *"),
        owner_consented=True,
    )
    assert created.ok, created.text
    trigger_id = created.data["trigger"]["id"]
    shown = redact_for_display(task)
    patch_ = {"workflow": {"provider": "invoke-agent", "config": {"task_template": shown + " now"}}}
    assert T.update(store, trigger_id=trigger_id, patch=patch_).ok
    config = T._inline_action_of(store.get(trigger_id).trigger.workflow)["config"]
    assert config["task_template"] == f"{task} now"


def test_a_workflow_edit_keeps_a_value_the_run_showed_masked():
    from personalclaw.workflows.mutations import Op, apply_batch

    spec = {
        "name": "w",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "s", "config": {"prompt": f"use {KEY}"}}],
        },
        "inputs": {"token": TOKEN},
    }
    ops = [
        Op.from_dict(
            {
                "op": "update_node",
                "node_id": "s",
                "fields": {"prompt": redact_for_display(f"use {KEY}") + "!"},
            }
        ),
        Op.from_dict({"op": "set_input", "overrides": {"token": MASK}}),
    ]
    candidate, issues = apply_batch(ops, spec, {})
    assert issues == []
    assert candidate["root"]["children"][0]["config"]["prompt"] == f"use {KEY}!"
    assert candidate["inputs"]["token"] == TOKEN


# ── a credential a tool needs is named, and filled in where it runs ──────────────────────────


@pytest.fixture
def stored_secrets(monkeypatch):
    """Two credentials in the test home's Settings → Secrets store (`.env`), none in the
    environment: the store is the only place `bash` may fill a reference from."""
    from personalclaw.config.loader import config_dir, env_path

    config_dir()  # the home this writes into: finding where the file is makes nothing
    path = env_path()
    path.write_text(f"DEPLOY_PASS={PASSWORD}\nGITHUB_TOKEN={TOKEN}\n")
    path.chmod(0o600)
    for name in ("DEPLOY_PASS", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return path


def test_bash_fills_a_reference_where_it_runs_and_masks_it_in_what_it_prints(
    tmp_path, stored_secrets
):
    """🔴 The reference path. The command gets the real value (written to a file, read back by
    the test, server-side); the model is handed its output with the value masked, although no
    pattern knows its shape."""
    tools = NativeBuiltinToolProvider(tmp_path, sandbox_mode="off")
    ran = _call(
        tools,
        "bash",
        {"command": "printf '%s' '{{secret:DEPLOY_PASS}}' > used.txt && cat used.txt && echo"},
    )
    assert ran.success, ran.error
    assert (tmp_path / "used.txt").read_text() == PASSWORD, "the command received the value"
    handed = _handed(ran)
    assert not _leaks(handed, PASSWORD), handed
    assert MASK in handed


def test_a_reference_to_nothing_stored_runs_nothing(tmp_path, stored_secrets, monkeypatch):
    """Only Settings → Secrets fills a reference: not the gateway's environment, which a sandbox
    keeps from the command, and not a setting's own key."""
    monkeypatch.setenv("ONLY_IN_THE_ENVIRONMENT", "an-environment-value-1234")
    tools = NativeBuiltinToolProvider(tmp_path, sandbox_mode="off")
    for name in ("MISSING", "ONLY_IN_THE_ENVIRONMENT", "PCSECRET_APP_X__TOKEN"):
        refused = _call(tools, "bash", {"command": f"touch ran-{name} {{{{secret:{name}}}}}"})
        assert not refused.success and f"{{{{secret:{name}}}}}" in refused.error, refused.error
        assert "Nothing was run" in refused.error
        assert not (tmp_path / f"ran-{name}").exists()


def test_bash_masks_a_credential_its_environment_carries(tmp_path, stored_secrets, monkeypatch):
    """A credential the command's environment does carry is masked out of what it prints.

    The command runs with the child allowlist (``sandbox.build_child_env``), not a copy of the
    gateway's environment, so a credential reaches its environment only by name, through the
    owner's ``sandbox.env_passthrough`` (a ``gh`` that reads ``GITHUB_TOKEN``). Those two are
    passed through here; the third is not, and never reaches the command at all."""
    from personalclaw.config.loader import config_path

    config_path().write_text(
        json.dumps({"sandbox": {"env_passthrough": ["GITHUB_TOKEN", "SERVICE_AUTH_TOKEN"]}})
    )
    monkeypatch.setenv("GITHUB_TOKEN", "plainword-with-no-shape-4821")
    monkeypatch.setenv("SERVICE_AUTH_TOKEN", "another-plain-value-9913")
    monkeypatch.setenv("BILLING_API_TOKEN", "never-passed-through-5530")
    tools = NativeBuiltinToolProvider(tmp_path, sandbox_mode="off")
    command = 'echo "$GITHUB_TOKEN $SERVICE_AUTH_TOKEN [$BILLING_API_TOKEN]"'
    ran = _call(tools, "bash", {"command": command})
    assert ran.success, ran.error
    handed = _handed(ran)
    assert "plainword-with-no-shape-4821" not in handed
    assert "another-plain-value-9913" not in handed
    assert handed.count(MASK) == 2
    assert "[]" in handed, "a credential the owner did not pass through reached the command"


def test_bash_masks_a_gateway_credential_it_reached_another_way(
    tmp_path, stored_secrets, monkeypatch
):
    """What is masked is every credential the gateway holds, not only the ones the command was
    given: one it never received but printed anyway (read back from a file in its folder) is
    masked the same way."""
    monkeypatch.setenv("BILLING_API_TOKEN", "never-passed-through-5530")
    (tmp_path / "copied.txt").write_text("billing: never-passed-through-5530\n")
    tools = NativeBuiltinToolProvider(tmp_path, sandbox_mode="off")
    ran = _call(tools, "bash", {"command": 'printf "[%s]" "$BILLING_API_TOKEN"; cat copied.txt'})
    assert ran.success, ran.error
    handed = _handed(ran)
    assert handed.startswith("[]"), "the command's environment never carried it"
    assert "never-passed-through-5530" not in handed and f"billing: {MASK}" in handed, handed


def test_a_native_turn_asks_with_the_name_and_runs_with_the_value(tmp_path, stored_secrets):
    """End to end on the native loop: the approval card shows the reference, the approved command
    runs with the value, and the model's next inference is handed the output masked."""
    from personalclaw.llm.events import EVENT_PERMISSION_REQUEST

    command = "printf '%s' '{{secret:DEPLOY_PASS}}' > used.txt && cat used.txt"
    model = _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="c1",
                    title="bash",
                    tool_input=json.dumps({"command": command}),
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )
    runtime = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=[NativeBuiltinToolProvider(tmp_path, sandbox_mode="off")],
        cwd=tmp_path,
    )
    asked: list[AgentEvent] = []

    async def turn() -> None:
        await runtime.start()
        async for ev in runtime.stream("deploy"):
            if ev.kind == EVENT_PERMISSION_REQUEST:
                asked.append(ev)
                await runtime.approve_tool(ev.request_id)

    asyncio.run(asyncio.wait_for(turn(), timeout=30))
    assert asked, "bash asks before it runs"
    shown = json.dumps(asked[0].tool_input)
    assert "{{secret:DEPLOY_PASS}}" in shown and PASSWORD not in shown
    assert (tmp_path / "used.txt").read_text() == PASSWORD
    handed = [m for m in model.seen_messages[1] if m.get("role") == "tool"]
    assert handed and not _leaks(str(handed[-1]["content"]), PASSWORD)


def test_the_model_turn_providers_keep_a_reference_as_the_name():
    from personalclaw.action_providers import registry
    from personalclaw.action_providers.base import ActionProvider

    registry._ensure_default_providers_registered()
    for name in ("invoke-agent", "run-prompt", "second-opinion", "best-of-n"):
        provider = registry.get_action_provider(name)
        assert provider is not None and provider.hands_config_to_a_model is True, name
    for name in ("bash", "notify", "run-workflow"):
        provider = registry.get_action_provider(name)
        assert provider is not None and provider.hands_config_to_a_model is False, name
    declared = ActionProvider.hands_config_to_a_model
    assert declared.fget(object()) is False  # type: ignore[attr-defined]


class _Recorder:
    hands_config_to_a_model = True

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    async def execute(self, config: dict[str, Any], ctx: Any, timeout: int = 30) -> Any:
        self.seen = dict(config)
        return types.SimpleNamespace(success=True)


def test_a_trigger_that_is_a_model_turn_hands_the_agent_the_name():
    """A scheduled prompt's `{{secret:KEY}}` used to be filled in before the agent read it."""
    import personalclaw.action_providers as AP
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.triggers import secrets as S

    recorder = _Recorder()
    trigger = types.SimpleNamespace(
        id="clock:deploy",
        workflow={
            "inline": {
                "provider": "invoke-agent",
                "config": {"task_template": "Deploy using {{secret:DEPLOY_PASS}}"},
            }
        },
        capabilities={"providers": ["invoke-agent"]},
    )
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: recorder
        with patch.object(S, "default_resolver", lambda k: PASSWORD):
            asyncio.run(
                object.__new__(GatewayOrchestrator)._fire_store_trigger(
                    trigger, {"trigger_id": "clock:deploy"}
                )
            )
    finally:
        AP.get_action_provider = real
    assert recorder.seen["task_template"] == "Deploy using {{secret:DEPLOY_PASS}}"


def test_a_workflow_stage_gets_the_name_and_an_action_step_the_value(tmp_path, monkeypatch):
    """One run, one reference, two steps: the stage's agent is handed the name, and the action
    that runs its config itself is handed the value."""
    from test_workflows_stage_completion import _FakeSubagents

    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    monkeypatch.setenv("DEPLOY_PASS", PASSWORD)
    ran: dict[str, Any] = {}

    class _Action:
        async def execute(self, config: dict[str, Any], ctx: Any, timeout: int = 30) -> Any:
            ran.update(config)
            return types.SimpleNamespace(
                success=True, output="done", error="", exit_code=0, outcome="succeeded"
            )

    spec = {
        "name": "deploy",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {
                    "kind": "action",
                    "id": "push",
                    "config": {
                        "provider": "recorder",
                        "with": {"command": "push {{secret:DEPLOY_PASS}}"},
                    },
                },
                {
                    "kind": "stage",
                    "id": "check",
                    "config": {"prompt": "verify with {{secret:DEPLOY_PASS}}"},
                },
            ],
        },
    }
    fake = _FakeSubagents()
    run = store.create(WorkflowRun(id="", workflow_name="deploy"))
    store.write_spec(run.id, spec)
    controller = RunController(
        run,
        spec,
        services=EngineServices(
            subagents=fake, cwd=str(tmp_path), get_provider=lambda name: _Action()
        ),
    )
    asyncio.run(controller.run_to_completion(timeout=6.0))
    assert ran.get("command") == f"push {PASSWORD}"
    assert fake.spawns, "the stage spawned its agent"
    task = str(fake.spawns[0].get("task"))
    assert "{{secret:DEPLOY_PASS}}" in task and PASSWORD not in task


# ── PersonalClaw's own chores ────────────────────────────────────────────────────────────────


def test_a_chore_masks_every_prompt_it_is_handed(monkeypatch):
    """A title, follow-ups, suggestions and memory consolidation are chores (``chores.run_chore``).
    No person types one: each prompt is composed from stored text, so it is masked before a model
    reads it, and a person's own chat is sent as typed."""
    from personalclaw import chores
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

    prompt = f"Current memory: user.github_token = {TOKEN}"
    sent: list[str] = []

    class _Model:
        supports_tools = False

        async def start(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

        async def stream(self, message: str):
            sent.append(message)
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok")
            yield AgentEvent(kind=EVENT_COMPLETE)

    registry = ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type="scripted",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=32_768,
        ),
        lambda **_kw: _Model(),
    )
    registry.register_entry(ProviderEntry(name="scripted", type="scripted", model="m"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"background": ["scripted:m"]},
    )

    answer = asyncio.run(chores.run_chore(prompt, usage=chores.chore_usage("dashboard:t1")))
    assert answer == "ok"
    assert len(sent) == 1, sent
    assert not _leaks(sent[0], TOKEN) and MASK in sent[0]

    model = _ScriptedModel(
        [[AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)]]
    )
    runtime = NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=[], session_key="dashboard:t1"
    )

    async def turn() -> None:
        await runtime.start()
        [ev async for ev in runtime.stream(prompt)]

    asyncio.run(turn())
    typed = json.dumps(model.seen_messages[0])
    assert TOKEN in typed and MASK not in typed, "a person's own chat is sent as typed"


@pytest.fixture
def facts(tmp_path, monkeypatch):
    """A real memory store holding one fact with a credential in it."""
    from personalclaw.vector_memory import VectorMemoryStore

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    assert store.set_semantic("user.github_token", f"the bot token is {TOKEN}", 1.0, "seed") is None
    return store


def _fact(store: Any, key: str) -> Any:
    row = store.get_semantic(key)
    return json.loads(row["value_json"]) if row else None


def test_consolidation_keeps_the_value_behind_a_marker_it_writes_back(facts):
    """Consolidation read the fact masked, so its rewrite carries a marker: the stored value is
    put back from the fact, for an update of it and for a supersede onto a new key."""
    from personalclaw import memory_formation as mf

    shown = redact_for_display(f"the bot token is {TOKEN}")
    seen = [mf.Overlap(key="user.github_token", value_str=shown, why="same_key")]
    update = mf.Candidate(
        index=0, key="user.github_token", value=shown + " (rotated)", confidence=0.9, overlaps=seen
    )
    report = mf.apply_decisions(
        facts,
        [update],
        {0: mf.Decision(index=0, verdict=mf.VERDICT_UPDATE, target="user.github_token")},
        source="consolidation:s1",
    )
    assert report.updated == 1, report.summary()
    assert _fact(facts, "user.github_token") == f"the bot token is {TOKEN} (rotated)"
    moved = mf.Candidate(
        index=1, key="user.bot_token", value="bot: " + shown.split("is ")[1], confidence=0.9
    )
    moved.overlaps = [mf.Overlap(key="user.github_token", value_str=shown + " (rotated)")]
    report = mf.apply_decisions(
        facts,
        [moved],
        {1: mf.Decision(index=1, verdict=mf.VERDICT_SUPERSEDE, target="user.github_token")},
        source="consolidation:s1",
    )
    assert (report.added, report.superseded, report.rejected) == (1, 1, 0), report.summary()
    assert _fact(facts, "user.bot_token") == f"bot: {TOKEN}"


def test_consolidation_updates_the_fact_a_masked_key_names(facts):
    """A key can hold a credential too, and the model is shown it masked: an update under the
    masked key lands on the fact whose key shows as it, and one that names no fact is refused."""
    from personalclaw import memory_formation as mf

    key = "user.token_ghp_" + "zr8" * 12
    assert facts.set_semantic(key, "the old note", 0.9, "seed") is None
    shown = redact_for_display(key)
    assert shown == "user.token_[REDACTED: credential]"
    renamed = mf.Candidate(index=0, key=shown, value="the new note", confidence=0.9)
    unknown = mf.Candidate(index=1, key="user.[REDACTED: credential]", value="x", confidence=0.9)
    report = mf.apply_decisions(facts, [renamed, unknown], {}, source="consolidation:s1")
    assert (report.added, report.rejected) == (1, 1), report.summary()
    assert _fact(facts, key) == "the new note"
    assert sorted(row["key"] for row in facts.get_all_semantic()) == sorted(
        ["user.github_token", key]
    )


def test_a_supersede_under_a_masked_key_retires_toward_the_fact_it_names(facts):
    """🔴 Red on integration: the fact it replaced was retired toward the masked spelling, a key
    that holds nothing, so it dropped out of recall with nothing in its place."""
    from personalclaw import memory_formation as mf

    key = "user.token_ghp_" + "zr8" * 12
    assert facts.set_semantic(key, "the old note", 0.9, "seed") is None
    moved = mf.Candidate(
        index=0, key=redact_for_display(key), value="the bot token now lives here", confidence=0.9
    )
    moved.overlaps = [
        mf.Overlap(
            key="user.github_token", value_str=redact_for_display(f"the bot token is {TOKEN}")
        )
    ]
    report = mf.apply_decisions(
        facts,
        [moved],
        {0: mf.Decision(index=0, verdict=mf.VERDICT_SUPERSEDE, target="user.github_token")},
        source="consolidation:s1",
    )

    assert (report.added, report.superseded) == (1, 1), report.summary()
    replaced = facts.db.execute(
        "SELECT is_deleted, superseded_by FROM semantic_memory WHERE key = 'user.github_token'"
    ).fetchone()
    assert replaced["is_deleted"] == 1 and replaced["superseded_by"] == key
    assert _fact(facts, key) == "the bot token now lives here"


def test_a_consolidated_markdown_rewrite_keeps_hidden_values_or_is_left():
    from personalclaw.history import _kept_lines

    stored = f"# Preferences\n- deploys with api_key={KEY}\n- tabs over spaces\n"
    shown = redact_for_display(stored)
    kept = _kept_lines(shown.replace("tabs over spaces", "spaces over tabs"), stored)
    assert kept == stored.replace("tabs over spaces", "spaces over tabs")
    rewritten = shown.replace("deploys with", "ships with")
    assert _kept_lines(rewritten, stored) == "", "a rewritten hidden line is left as stored"
    assert _kept_lines(None, stored) == ""
