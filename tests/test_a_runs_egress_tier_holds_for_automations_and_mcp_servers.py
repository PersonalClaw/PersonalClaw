"""An automation's action and a remote MCP server's connection keep to the run's egress tier.

A run's egress tier (its safety profile's, which the operator ceiling bounds) held for every request
a run's tool call made through the egress guard, but not for an automation's action. A trigger's
fire, its Run now, a hook and a workflow step ran their action with no run named to the guard, so a
ceiling that allows no run on the machine any network still let an automation's webhook or fetch go
out, held to the owner's Network egress settings alone. And the connection to a remote MCP server
never asked the guard at all: not the owner's Denied hosts, and not a run's tier.

Each of those dispatches now holds its action's requests to the run it judged the action under, and
every use of a remote MCP server's connection asks the guard about the server first, for the run the
call is made for, so a refused call reaches nothing and says why.

Every endpoint here is a stand-in on this machine or a reserved name, so nothing leaves it.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socket
import subprocess
import sys
import textwrap
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import port_guard
import pytest
from mcp_owner_allowed import allow_configured

from personalclaw import mcp_core
from personalclaw.action_providers import registry as actions
from personalclaw.action_providers.base import ActionResult
from personalclaw.config.loader import config_dir
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.guardrails.ceiling import ceiling_path, reset_ceiling
from personalclaw.net import guard
from personalclaw.net.guard import evaluate
from personalclaw.net.policy import CONNECTOR, egress_policy_for
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.sel import sel

#: What a run with no network is told, in the words the guard has for it.
EGRESS_OFF_REASON = "egress is off for this run (safety profile egress tier 'off')"
#: A chat's session, as the built-in agent binds it around each tool call it dispatches.
CHAT = "dashboard:research-chat"
#: The action an app contributes in these tests, shaped as the webhook action is.
POST = "post-stand-in"
#: A host on no list, under a reserved name: refused, or never looked up.
ELSEWHERE = "hooks.example"


# ── the stand-ins ────────────────────────────────────────────────────────────


class _Receiver(http.server.ThreadingHTTPServer):
    """A service on this machine that records every request it is sent."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _ReceiverHandler)
        self.paths: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _ReceiverHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.server.paths.append(self.path)  # type: ignore[attr-defined]
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer  # noqa: N815 — http.server's names


class _PostApp:
    """An action an app contributes, shaped as the webhook action is: one POST through the SDK's
    guarded fetch under the webhook policy with the owner's Network egress settings on it, and a
    refusal said in the guard's own words (``personalclaw.sdk.net.egress_refusal``)."""

    def __init__(self) -> None:
        self.answers: list[str] = []

    async def execute(self, action_config, ctx, timeout=30):
        from personalclaw.sdk.net import (
            WEBHOOK,
            EgressBlocked,
            egress_policy_for,
            egress_refusal,
            fetch,
        )

        url = str(action_config.get("url") or "")
        try:
            resp = await fetch(url, policy=egress_policy_for(WEBHOOK), method="POST", data=b"{}")
        except EgressBlocked as e:
            said = egress_refusal(url, e.decision)
            self.answers.append(said)
            return ActionResult(success=False, error=said)
        self.answers.append(resp.text)
        return ActionResult(success=200 <= resp.status < 300, stdout=resp.text)


@pytest.fixture
def receiver(monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    server = _Receiver()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def looked_up(monkeypatch):
    """Every name the guard looks up; a reserved name answers nothing."""
    names: list[str] = []

    def resolve(host: str) -> list[str]:
        names.append(host)
        if host == "127.0.0.1":
            return ["127.0.0.1"]
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(guard, "_resolve", resolve)
    return names


@pytest.fixture
def poster(monkeypatch):
    """The webhook-shaped action, registered so a dispatch can find it.

    The suite's live-write switch is off for it: what these tests read is the egress guard, and
    every request goes to this machine or to a name that answers nothing."""
    monkeypatch.delenv("PERSONALCLAW_DISABLE_LIVE_WRITES", raising=False)
    actions._ensure_default_providers_registered()
    app = _PostApp()
    monkeypatch.setitem(actions._providers, POST, app)
    return app


def _owner_allows(*hosts: str, denied: tuple[str, ...] = ()) -> None:
    """The owner's Allowed hosts (and Denied hosts) in Settings → Security → Network egress."""
    (config_dir() / "config.json").write_text(
        json.dumps(
            {"security": {"egress": {"allow_hosts": list(hosts), "deny_hosts": list(denied)}}}
        ),
        encoding="utf-8",
    )


def _ceiling(egress: str) -> None:
    """The operator ceiling bounding every run's egress tier to *egress*."""
    path = ceiling_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "scopes": {"egress": {"value": egress}}}), encoding="utf-8"
    )
    reset_ceiling()


@contextmanager
def _in_run(session_key: str = CHAT):
    """A call made inside a run, bound as every seam that dispatches a tool call binds it."""
    token = mcp_core.set_current_session_key(session_key)
    try:
        yield
    finally:
        mcp_core.reset_current_session_key(token)


def _egress_rows(outcome: str) -> list[tuple[str, str]]:
    return [
        (row.get("caller_identity", ""), row.get("resources", ""))
        for row in reversed(sel().recent(300))
        if row.get("operation") == "egress_fetch" and row.get("outcome") == outcome
    ]


def _trigger(tid: str, provider: str, url: str) -> SimpleNamespace:
    """A store trigger whose action is *provider* aimed at *url*, its action allowed."""
    return SimpleNamespace(
        id=tid,
        kind="file",
        workflow={"inline": {"provider": provider, "config": {"url": url}}},
        capabilities={"providers": [provider]},
    )


def _fire(trigger) -> None:
    """The trigger fires, through the dispatch every unattended fire shares."""
    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(
            trigger, {"trigger_id": trigger.id, "kind": trigger.kind}, event="file.changed"
        )
    )


def _runs(tid: str) -> list[dict]:
    rows, _total = asyncio.run(ScheduleRunStore(config_dir()).list_for_job(tid, 0, 20))
    return rows


# ── a trigger's fire ─────────────────────────────────────────────────────────


def test_a_trigger_fire_whose_egress_is_off_sends_nothing(receiver):
    """The fetch action reaches only the hosts the owner allowed, and she allowed this one; a
    ceiling that gives no run any network still stops it, and its run says why, truly."""
    _owner_allows("127.0.0.1")
    _ceiling("off")
    url = f"{receiver.url}/feed"

    _fire(_trigger("file:feed", "net-fetch", url))

    assert receiver.paths == [], "an automation whose egress is off reached its host"
    (run,) = _runs("file:feed")
    assert EGRESS_OFF_REASON in run["error"], run
    assert ("net.fetch:fetch_action", url) in _egress_rows("denied")


def test_a_fetch_step_refused_by_the_runs_tier_names_no_host_setting(receiver):
    """No host on Allowed hosts lifts a run's tier, so the step's fix must not offer one: it names
    where the tier is set, and a retry is not offered, since one meets the same bound."""
    from personalclaw.action_providers.net_fetch_provider import NetFetchActionProvider
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_action
    from personalclaw.workflows.models import InstanceState, Node

    _owner_allows("127.0.0.1")
    _ceiling("off")
    node = Node.from_dict(
        {
            "kind": "action",
            "id": "fetch",
            "config": {"provider": "net-fetch", "with": {"url": f"{receiver.url}/feed"}},
        }
    )

    result = asyncio.run(
        dispatch_action(
            node, BindingContext(), get_provider=lambda _n: NetFetchActionProvider(), run_id="r1"
        )
    )

    assert result.state == InstanceState.FAILED, "the step reached its host"
    assert receiver.paths == []
    failure = result.failure
    assert EGRESS_OFF_REASON in failure.cause_plain, failure
    assert "Allowed hosts" not in failure.remediation, failure.remediation
    assert str(ceiling_path()) in failure.remediation, failure.remediation
    assert failure.recoverable is False


def test_a_trigger_fire_on_a_listed_tier_reaches_only_the_allowed_hosts(
    poster, receiver, looked_up
):
    _owner_allows("127.0.0.1")
    _ceiling("listed")

    _fire(_trigger("file:mine", POST, f"{receiver.url}/hook"))
    _fire(_trigger("file:theirs", POST, f"https://{ELSEWHERE}/hook"))

    assert receiver.paths == ["/hook"]
    (refused,) = _runs("file:theirs")
    assert "this run reaches only the hosts it lists" in refused["error"], refused
    assert f"add {ELSEWHERE} to Allowed hosts" in refused["error"], refused
    assert ELSEWHERE not in looked_up, "a host off the run's list was looked up"


def test_a_trigger_fire_on_the_default_tier_still_reaches_its_host(poster, receiver):
    _owner_allows("127.0.0.1")

    _fire(_trigger("file:mine", POST, f"{receiver.url}/hook"))

    assert receiver.paths == ["/hook"]
    assert _egress_rows("denied") == []


def test_run_now_of_an_automation_whose_egress_is_off_sends_nothing(poster, receiver):
    """A run by hand is the same dispatch as the fire, and holds to the same bounds."""
    from personalclaw.dashboard.handlers import trigger_runs
    from personalclaw.triggers.models import Trigger

    _owner_allows("127.0.0.1")
    _ceiling("off")
    trigger = Trigger(
        id="clock:hook",
        name="Post the digest",
        kind="clock",
        created_by="user",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        workflow={"inline": {"provider": POST, "config": {"url": f"{receiver.url}/hook"}}},
        capabilities={"providers": [POST]},
    )

    ran, note = asyncio.run(
        trigger_runs._dispatch_store_action(trigger, {"trigger_id": trigger.id, "manual": True})
    )

    assert ran is False and receiver.paths == []
    assert EGRESS_OFF_REASON in note, note


# ── a hook and a workflow step ───────────────────────────────────────────────


def test_a_hooks_action_whose_egress_is_off_sends_nothing(poster, receiver):
    from personalclaw.hooks import HOOK_EVENT_USER_PROMPT_SUBMIT, ScriptHook, run_script_hook

    _owner_allows("127.0.0.1")
    _ceiling("off")
    hook = ScriptHook(
        id="post-prompt",
        name="Post each prompt",
        event=HOOK_EVENT_USER_PROMPT_SUBMIT,
        provider=POST,
        provider_config={"url": f"{receiver.url}/hook"},
        capabilities={"providers": [POST]},
    )

    asyncio.run(run_script_hook(hook, "a prompt"))  # on the agent's own event, run by nobody

    assert receiver.paths == []
    assert poster.answers == [f"{receiver.url}/hook was not reached: {EGRESS_OFF_REASON}."]


def test_a_workflow_steps_action_whose_egress_is_off_sends_nothing(poster, receiver):
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_action
    from personalclaw.workflows.models import InstanceState, Node

    _owner_allows("127.0.0.1")
    _ceiling("off")
    node = Node.from_dict(
        {
            "kind": "action",
            "id": "post",
            "config": {"provider": POST, "with": {"url": f"{receiver.url}/hook"}},
        }
    )

    result = asyncio.run(
        dispatch_action(node, BindingContext(), get_provider=lambda _n: poster, run_id="run-1")
    )

    assert result.state == InstanceState.FAILED
    assert receiver.paths == []
    assert EGRESS_OFF_REASON in poster.answers[0], poster.answers


# ── whose run a request is held to ───────────────────────────────────────────


def test_an_agent_an_action_starts_is_held_to_its_own_run(monkeypatch, looked_up):
    """An action's requests are held to the run the action was judged under, and a tool call made
    inside that work, which binds a session of its own, to that session's run."""
    from personalclaw.guardrails import policy as guardrails
    from personalclaw.net.policy import egress_held_to

    held = guardrails.HEADLESS.with_overrides(egress_tier="off")
    real = guardrails.profile_for_session
    monkeypatch.setattr(
        guardrails,
        "profile_for_session",
        lambda key: held if key == "unattended:trigger:file:feed" else real(key),
    )
    url = f"https://{ELSEWHERE}/x"

    with egress_held_to("unattended:trigger:file:feed"):
        the_action = evaluate(url, egress_policy_for(CONNECTOR))
        with _in_run():
            its_agent = evaluate(url, egress_policy_for(CONNECTOR))
        after = evaluate(url, egress_policy_for(CONNECTOR))
    with _in_run():
        with egress_held_to("unattended:hook:post-prompt"):
            in_a_turn = evaluate(url, egress_policy_for(CONNECTOR))

    assert the_action.category == "egress_off"
    assert its_agent.category == "unresolvable", "the agent was held to the action's run"
    assert after.category == "egress_off"
    assert in_a_turn.category == "unresolvable"  # the hook's own run, whose tier is "all"


def test_work_handed_to_the_gateways_worker_threads_keeps_the_run(looked_up):
    """The gateway's default worker pool carries the caller's context, so a request one of its
    threads makes is held to the run that handed it over, as one on the loop is."""
    from personalclaw.memory_writes import carry_scope_into_worker_threads
    from personalclaw.net.policy import egress_held_to

    _ceiling("off")
    url = f"https://{ELSEWHERE}/x"

    async def handed_over():
        loop = asyncio.get_running_loop()
        carry_scope_into_worker_threads(loop)
        with egress_held_to("unattended:trigger:file:feed"):
            return await loop.run_in_executor(None, evaluate, url, egress_policy_for(CONNECTOR))

    assert asyncio.run(handed_over()).category == "egress_off"
    assert ELSEWHERE not in looked_up


# ── a remote MCP server's connection ─────────────────────────────────────────

NAME = "docs-search"

# A real MCP server at a URL on this machine, which logs the method and path of every request.
_MCP_SERVER = textwrap.dedent("""
    import json, os, socket, sys

    import uvicorn
    from mcp.server.fastmcp import FastMCP

    PORT_FILE, LOG_FILE = sys.argv[1:3]
    mcp = FastMCP("docs-search")


    @mcp.tool(description="greet someone")
    def hello(name: str) -> str:
        return f"hello {name}"


    inner = mcp.streamable_http_app()


    async def app(scope, receive, send):
        if scope["type"] == "http":
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps({"method": scope["method"], "path": scope["path"]}) + "\\n")
        await inner(scope, receive, send)


    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    with open(PORT_FILE + ".tmp", "w") as f:
        f.write(str(sock.getsockname()[1]))
    os.replace(PORT_FILE + ".tmp", PORT_FILE)
    uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on")).run(sockets=[sock])
    """)


@dataclass
class _Remote:
    url: str
    log: Path

    def requests(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """The server, configured in ``mcp.json`` at its URL and allowed by the owner."""
    from personalclaw.config.secret_refs import write_mcp_document

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    script = tmp_path / "docs_search_server.py"
    script.write_text(_MCP_SERVER, encoding="utf-8")
    port_file = tmp_path / "port"
    served = _Remote("", tmp_path / "requests.jsonl")
    stderr = tmp_path / "server.stderr"
    with stderr.open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, str(script), str(port_file), str(served.log)],
            stdout=subprocess.DEVNULL,
            stderr=err,
        )
    try:
        deadline = time.monotonic() + 30
        while not port_file.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"the stand-in server did not start: {stderr.read_text()!r}")
            time.sleep(0.05)
        port = int(port_file.read_text(encoding="utf-8").strip())
        port_guard.GUARD.own(port)  # the server this test started chose it
        served.url = f"http://127.0.0.1:{port}/mcp"
        write_mcp_document(
            config_dir() / "mcp.json", {"mcpServers": {NAME: {"type": "http", "url": served.url}}}
        )
        allow_configured(NAME)  # the owner's Allow on the Tools page
        yield served
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _calls(remote: _Remote, *steps: str) -> list[tuple[bool, str, int]]:
    """``hello`` called once per step over one connection, as the native client makes the call:
    ``run`` inside a chat's run, ``owner`` outside any run (the owner's own action). Each answer
    comes with how many requests the server had been sent by the time it arrived."""
    from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs

    async def run() -> list[tuple[bool, str, int]]:
        reg = McpClientRegistry()
        try:
            reg.load_from_specs(_personalclaw_mcp_specs())
            conn = reg.get(NAME)
            assert conn is not None, "the native client has no connection for the server"
            answers = []
            for step in steps:
                if step == "run":
                    with _in_run():
                        ok, said = await conn.call_tool("hello", {"name": "claw"})
                else:
                    ok, said = await conn.call_tool("hello", {"name": "claw"})
                answers.append((ok, said, len(remote.requests())))
            return answers
        finally:
            await reg.shutdown_all()

    return asyncio.run(run())


def test_a_remote_mcp_server_off_a_listed_runs_hosts_is_refused_before_it_is_reached(remote):
    _owner_allows("docs.example")
    _ceiling("listed")

    ((ok, said, seen),) = _calls(remote, "run")

    assert ok is False
    assert seen == 0 and remote.requests() == [], "the server was reached"
    assert said.startswith(f"PersonalClaw's network settings refused {remote.url}"), said
    assert "this run reaches only the hosts it lists" in said, said
    assert "add 127.0.0.1 to Allowed hosts" in said, said
    assert ("mcp:mcp_server", remote.url) in _egress_rows("denied")


def test_a_remote_mcp_server_on_the_allowed_hosts_still_answers_a_listed_run(remote):
    _owner_allows("127.0.0.1")
    _ceiling("listed")

    ((ok, said, seen),) = _calls(remote, "run")

    assert (ok, said) == (True, "hello claw")
    assert seen > 0, "the server was never reached"


def test_a_remote_mcp_server_still_answers_a_run_on_the_default_tier(remote):
    answers = _calls(remote, "run", "owner")

    assert [(ok, said) for ok, said, _ in answers] == [(True, "hello claw")] * 2


def test_an_open_connection_sends_nothing_for_a_run_whose_egress_is_off(remote):
    """The owner's own action opened the connection; a run with no network calls over it next."""
    _ceiling("off")

    owner, run = _calls(remote, "owner", "run")

    assert owner[:2] == (True, "hello claw")
    assert run[:2] == (False, f"{remote.url} was not reached: {EGRESS_OFF_REASON}.")
    assert run[2] == owner[2], "the run's call reached the server over the open connection"
    assert ("mcp:mcp_server", remote.url) in _egress_rows("allowed")  # the connection's start
    assert ("mcp:mcp_server", remote.url) in _egress_rows("denied")  # the run's call


class _ToolModel:
    """A model that takes tools, for a turn whose tool list is what is read."""

    supports_tools = True


def _a_turns_tools(reg) -> list[str]:
    """The tools the native agent can call in a chat's turn, MCP servers' among them."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module

    module = load_bundle_module(NATIVE_DIR / "mcp-tools", "mcp-tools", "provider")

    async def run() -> list[str]:
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="research", provider="native", model="m"),
            model_provider=_ToolModel(),
            tool_providers=[module.McpToolProvider(lambda: reg)],
            session_key=CHAT,
        )
        await runtime.start()
        return sorted(runtime._tool_index)

    return asyncio.run(run())


def _registry():
    from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs

    reg = McpClientRegistry()
    reg.load_from_specs(_personalclaw_mcp_specs())
    return reg


def test_a_turn_listing_its_tools_does_not_start_a_server_its_run_does_not_reach(remote):
    """A turn lists its tools before it calls one, outside every call, and listing a remote
    server's tools starts its connection: the listing is held to the turn's run, as a call is."""
    _ceiling("off")
    reg = _registry()
    try:
        offered = _a_turns_tools(reg)
    finally:
        asyncio.run(reg.shutdown_all())

    assert remote.requests() == [], "a run with no network reached the server to list its tools"
    assert offered == [], "the turn was offered tools it cannot reach"


def test_a_turn_on_the_default_tier_still_lists_a_servers_tools(remote):
    reg = _registry()
    try:
        offered = _a_turns_tools(reg)
    finally:
        asyncio.run(reg.shutdown_all())

    assert offered == [f"mcp/{NAME}/hello"]


def test_two_calls_that_start_the_connection_at_once_start_it_once(remote):
    """The start waits for the guard's look-up, so a second call that arrives meanwhile must find
    the start the first made, not make a second connection beside it."""
    from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs

    async def run() -> tuple[list[bool], int]:
        reg = McpClientRegistry()
        try:
            reg.load_from_specs(_personalclaw_mcp_specs())
            conn = reg.get(NAME)
            assert conn is not None
            started = await asyncio.gather(conn.ensure_started(), conn.ensure_started())
            actors = [t for t in asyncio.all_tasks() if t.get_name() == f"mcp-conn-{NAME}"]
            return list(started), len(actors)
        finally:
            await reg.shutdown_all()

    started, actors = asyncio.run(run())

    assert started == [True, True]
    assert actors == 1


def test_a_remote_mcp_server_on_denied_hosts_is_not_reached_and_its_card_says_why(remote):
    from personalclaw.mcp_discovery import probe_one

    _owner_allows(denied=("127.0.0.1",))

    info = asyncio.run(probe_one(NAME))

    assert info is not None and info.status == "error"
    assert info.error == (
        f"{remote.url} was not reached: 127.0.0.1 is on Denied hosts in "
        "Settings → Security → Network egress."
    ), info.error
    assert remote.requests() == []
