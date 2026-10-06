"""An agent on an agent CLI makes, changes and deletes your automations, and keeps everything else
its PersonalClaw tools save, in a home its sandbox fences.

On Linux an agent CLI runs in the host's sandbox, and so does the tool server it starts
(``personalclaw mcp-core``): nothing can be added, replaced, renamed or removed at the top of the
home from inside it, and a folder made there after the CLI started cannot be written. Measured on
a Linux host through the real launcher, before this:

* ``automation_create``, ``set_onetime_task``, ``hook_register`` and ``dashboard_tile_propose``
  each ended the tool server with "Read-only file system" (every save of ``triggers.json``,
  ``webhook_callbacks.json`` and ``dashboard_views.json`` replaces a file at the top of the home),
  so every later call of the CLI's turn failed too;
* ``skill_promote``, ``template_save_from_session`` and ``project_context_review`` filed nothing
  and said the review queue could not take it, that the steps were declined although their score
  cleared the bar, or that the item was empty: the queue's folder was not there at launch, and the
  Inbox row beside each proposal is a file at the top of the home;
* an image the agent made was never charged to the usage ledger, a folder the first metered call
  makes;
* once the gateway rotated the security log, the next call's row ended the server.

Each of those calls is now made by the gateway (``mcp_core.made_by_the_gateway``), through the door
every sandboxed process of PersonalClaw's makes its tool calls by, as the chat's work; a call whose
work fails says so; and a row the log no longer takes is said in the server's own log. The tool
server is started as the client declares it and spoken to over stdio, against the real gateway
(``start_dashboard``, asking for a sign-in), with the sandbox's fence stood in for by
``home_fence``: what the real launcher fences is checked on Linux by
``test_the_home_fence_holds_its_names``.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import home_fence
import pytest
from test_an_agent_clis_workflow_tools_reach_the_gateway import (  # noqa: F401
    CLI_MODEL,
    KEY,
    TEMPORARY_KEY,
    gateway,
)

from personalclaw import lasting_work, mcp_automation, mcp_core, mcp_shared, memory_writes
from personalclaw.acp.mcp_servers import core_mcp_servers
from personalclaw.inbox import InboxStore
from personalclaw.learning import proposals
from personalclaw.tool_providers.base import ToolFailure
from personalclaw.triggers.store import TriggerStore

#: How long one call may take to be answered, inside the suite's limit for a test.
ANSWER_SECS = 90


class _ToolServer:
    """The tool server an agent CLI starts for the chat *key* (:data:`KEY` unless named), from the
    client's declaration, in a home its sandbox fences, spoken to over stdio one call at a time."""

    def __init__(
        self, gw: SimpleNamespace, scratch: Path, *, key: str = KEY, **extra_env: str
    ) -> None:
        self.gw = gw
        self.log = scratch / "fence.log"
        self.stderr_path = scratch / "tool-server.stderr"
        (server,) = core_mcp_servers(session_key=key)
        self.argv = [server["command"], *server["args"]]
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(gw.user_home)}
        self.env.update({entry["name"]: entry["value"] for entry in server["env"]})
        self.env.update(home_fence.fenced(scratch / "fence-site", gw.home, self.log))
        self.env.update(extra_env)
        self.proc: asyncio.subprocess.Process | None = None
        self.number = 0

    async def __aenter__(self) -> "_ToolServer":
        # What an agent CLI's launch makes before the sandbox applies (`AcpProcess.spawn`).
        mcp_core.make_its_folders()
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.stderr_path.open("w"),
            env=self.env,
        )
        await self._ask("initialize", {})
        return self

    async def __aexit__(self, *_exc: object) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.close()
        await asyncio.wait_for(self.proc.wait(), timeout=ANSWER_SECS)

    @property
    def stderr(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8", errors="replace")

    @property
    def refused(self) -> list[dict[str, Any]]:
        """What the fence refused this process."""
        return home_fence.refused(self.log)

    async def _ask(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        assert self.proc is not None and self.proc.stdin is not None
        assert self.proc.stdout is not None
        self.number += 1
        frame = {"jsonrpc": "2.0", "id": self.number, "method": method, "params": params}
        try:
            self.proc.stdin.write((json.dumps(frame) + "\n").encode())
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return None
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=ANSWER_SECS)
            if not line:
                return None
            reply = json.loads(line)
            if reply.get("id") == self.number:
                return reply

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """``(ok, text)`` as the CLI reads the call's answer; a server that ended answers
        ``(False, …)`` with how it ended."""
        reply = await self._ask("tools/call", {"name": name, "arguments": arguments})
        if reply is None:
            return False, f"the tool server ended: {self.stderr[-2500:]}"
        result = reply["result"]
        text = " ".join(part.get("text", "") for part in result.get("content", []))
        return not result.get("isError"), text


def _data(text: str) -> dict[str, Any]:
    """The structured data an automation tool's answer carries."""
    found = re.search(r"<automation-data>(.*?)</automation-data>", text, re.S)
    assert found, text
    return json.loads(found.group(1))


def _audited(home: Path, tool: str) -> list[tuple[str, str]]:
    """``(source, outcome)`` of each security-log row for a call of *tool*, in order."""
    log = home / "security_events.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [(r["source"], r["outcome"]) for r in rows if r.get("operation") == tool]


def _stored(trigger_id: str) -> Any:
    """The automation the gateway's store holds under *trigger_id*, or None."""
    row = TriggerStore().get(trigger_id)
    return row.trigger if row is not None else None


# ── automations ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_agent_clis_automation_is_made_changed_and_deleted_in_a_fenced_home(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Red on main: the create ended the tool server with "Read-only file system", and
    every call after it went unanswered."""
    async with _ToolServer(gateway, tmp_path) as server:
        ok, made = await server.call(
            "automation_create",
            {"name": "Bins reminder", "when": "in 3 hours", "say": "Bins out tonight."},
        )
        assert ok, made
        trigger_id = _data(made)["trigger"]["id"]
        stored = _stored(trigger_id)
        assert stored is not None and stored.name == "Bins reminder", made
        assert stored.created_by == "agent"

        ok, renamed = await server.call(
            "automation_update",
            {"id": trigger_id, "patch": json.dumps({"name": "Bins, Tuesday night"})},
        )
        assert ok, renamed
        assert _stored(trigger_id).name == "Bins, Tuesday night"

        ok, paused = await server.call("automation_pause", {"id": trigger_id})
        assert ok and _stored(trigger_id).enabled is False, paused
        ok, resumed = await server.call("automation_resume", {"id": trigger_id})
        assert ok and _stored(trigger_id).enabled is True, resumed

        ok, listed = await server.call("automation_list", {})
        assert ok and "Bins, Tuesday night" in listed, listed

        # Its own refusal, said as the gateway said it: nothing is deleted without confirm.
        ok, kept = await server.call("automation_delete", {"id": trigger_id, "confirm": False})
        assert not ok and "confirm" in kept, kept
        assert _stored(trigger_id) is not None

        ok, deleted = await server.call("automation_delete", {"id": trigger_id, "confirm": True})
        assert ok, deleted
        assert _stored(trigger_id) is None

    # Recorded where the gateway made it: the route's row and its handler's, and none of the tool
    # server's own beside them.
    assert _audited(gateway.home, "automation_create") == [
        ("mcp", "completed"),
        ("tool_invoke", "completed"),
    ]
    assert server.refused == [], server.refused


@pytest.mark.asyncio
async def test_an_agent_clis_one_time_task_is_kept_and_cleared_in_a_fenced_home(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Red on main: the task's save ended the tool server."""
    async with _ToolServer(gateway, tmp_path) as server:
        ok, made = await server.call(
            "set_onetime_task",
            {"name": "Check the build", "when": "in 20 minutes", "message": "Look at the build."},
        )
        assert ok, made
        assert "Created automation 'Check the build'" in made, made
        trigger_id = _data(made)["trigger"]["id"]
        assert _stored(trigger_id).created_by == "agent"

        ok, cleared = await server.call("automation_delete_all", {"confirm": True})
        assert ok and trigger_id in _data(cleared)["deleted"], cleared
        assert _stored(trigger_id) is None

    assert server.refused == [], server.refused


@pytest.mark.asyncio
async def test_a_steps_wake_up_on_an_agent_cli_names_its_own_run(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """A workflow step on an agent CLI asks to be woken with ``resume_run_id: "self"``: the run the
    wake-up resumes is the step's own, from the lineage its tool server was started with, though
    the gateway, which runs for no step, makes the call. 🔴 Red on main: the save ended the
    tool server."""
    from personalclaw.triggers.wakeup import resume_target_of

    lineage = {"__wf_run_id": "r-weekly-numbers", "__wf_depth": "1", "__wf_read_only": "0"}
    async with _ToolServer(gateway, tmp_path, **lineage) as server:
        ok, made = await server.call(
            "set_onetime_task",
            {
                "name": "Look again",
                "when": "in 2 hours",
                "message": "Check the numbers.",
                "resume_run_id": "self",
            },
        )

    assert ok, made
    target = resume_target_of(_stored(_data(made)["trigger"]["id"]))
    assert target["run_id"] == "r-weekly-numbers", target
    assert server.refused == [], server.refused


# ── a callback, a tile and proposals ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_agent_clis_callback_and_tile_are_kept_in_a_fenced_home(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Red on main: each save ended the tool server ("Read-only file system" on its lock
    file at the top of the home)."""
    from personalclaw import webhook_callbacks
    from personalclaw.dashboard import views_store

    async with _ToolServer(gateway, tmp_path) as server:
        ok, hooked = await server.call(
            "hook_register", {"hook_id": "build-done", "context_summary": "Waiting for the build."}
        )
        assert ok and "Hook registered: build-done" in hooked, hooked
        ok, tiled = await server.call("dashboard_tile_propose", {"slug": "weekly-numbers"})
        assert ok and "Proposed tile 'artifact:weekly-numbers'" in tiled, tiled

    callback = webhook_callbacks.get("build-done")
    assert callback is not None and callback.context_summary == "Waiting for the build."
    tiles = views_store.get_view(views_store.PRESET_OVERVIEW_ID).tiles
    assert [(t.ref, t.added_by) for t in tiles if t.ref == "artifact:weekly-numbers"] == [
        ("artifact:weekly-numbers", "agent")
    ]
    assert server.refused == [], server.refused


@pytest.mark.asyncio
async def test_an_agent_clis_proposals_are_filed_and_raised_in_a_fenced_home(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Red on main: nothing was filed, and the answers blamed the request: "the review
    queue could not accept it", "Declined … clears 0.70", "Each item was empty"."""
    async with _ToolServer(gateway, tmp_path) as server:
        ok, skill = await server.call(
            "skill_promote",
            {
                "name": "Release checklist",
                "description": "When cutting a release",
                "procedure": "1. Bump the version\n2. Tag the commit",
                "rationale": "Done twice in this chat.",
            },
        )
        assert ok and skill.startswith("Filed a skill proposal"), skill
        ok, template = await server.call(
            "template_save_from_session",
            {
                "name": "cut-a-release",
                "description": "Release a new version",
                "steps": [
                    "Bump the version in {{file}} to {{version}}",
                    "Run the tests on {{branch}} after the bump",
                    "Tag {{version}} once the tests pass",
                    "Push {{branch}} and the tag {{version}}",
                    "Write the release notes for {{version}} from the changelog",
                ],
            },
        )
        assert ok and template.startswith("Filed a DRAFT template proposal"), template
        ok, reviewed = await server.call(
            "project_context_review",
            {
                "project_id": "garden-planner",
                "items": [
                    {
                        "kind": "project_instruction",
                        "body": "Run the checks before every commit.",
                        "rationale": "Asked twice in this chat.",
                    }
                ],
            },
        )
        assert ok and reviewed.startswith("Filed 1 project-context proposal"), reviewed

    filed = {p.kind for p in proposals.list_pending()}
    assert {"skill", "template", "project_instruction"} <= filed, filed
    inbox = InboxStore()
    inbox.load()
    raised = {item.refs.get("learning_proposal") for item in inbox.items.values()}
    assert {p.id for p in proposals.list_pending()} <= raised, raised
    assert server.refused == [], server.refused


# ── a model's work ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_image_an_agent_cli_makes_is_charged_where_every_call_is(
    gateway, tmp_path, monkeypatch, request  # noqa: F811 - the imported fixture
):
    """🔴 Red on main: the image was made, and the usage ledger never heard of it (the
    ledger's folder is made by the first metered call, at the top of the home)."""
    from personalclaw.image_gen.registry import refresh_providers
    from personalclaw.providers.use_cases import save_active_models

    monkeypatch.setenv("PCLAW_IMAGE_GEN_STUB", "1")
    # The registry registers the stub once per process, on its first resolution: an earlier
    # test in this worker that resolved a provider without the stub would leave it out. Re-arm
    # it now, and again afterwards so the stub does not outlive this test.
    refresh_providers()
    request.addfinalizer(refresh_providers)
    save_active_models({"image_gen": ["stub:stub-1"]})
    async with _ToolServer(gateway, tmp_path, PCLAW_IMAGE_GEN_STUB="1") as server:
        ok, made = await server.call("image_generate", {"prompt": "a lighthouse at dusk"})
    assert ok and made.startswith("Generated image 'a lighthouse at dusk'"), made

    ledger = gateway.home / "usage" / "turns.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    assert [(r["session_key"], r["unit"], r["quantity"]) for r in rows] == [(KEY, "image", 1.0)]
    assert server.refused == [], server.refused


# ── what the server says when a write cannot be made ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_row_the_security_log_no_longer_takes_ends_no_call(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """The gateway rotates its security log: the file at its name is a new one, which the CLI's
    sandbox lets its tool server only read. 🔴 Red on main: the next call's row ended the
    server."""
    log = gateway.home / "security_events.jsonl"
    log.touch()
    async with _ToolServer(gateway, tmp_path) as server:
        ok, before = await server.call("skill_search", {"query": "release"})
        assert ok, before
        # The rotation: the live file moves into the archive and a new one takes its name.
        archive = gateway.home / "sel_archive"
        archive.mkdir(exist_ok=True)
        log.rename(archive / "security_events.rotated.jsonl")
        log.touch()
        ok, after = await server.call("skill_search", {"query": "release"})
        assert ok and after == before, after
        ok, again = await server.call("skill_search", {"query": "checklist"})
        assert ok, again

    assert "could not take this row" in server.stderr, server.stderr[-2000:]
    assert [r["path"] for r in server.refused] == [str(log.resolve())] * 2, server.refused


@pytest.mark.asyncio
async def test_a_call_the_gateway_does_not_answer_says_so_and_saves_nothing(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """Never a false success: with no gateway to make it, the call is said not made."""
    before = TriggerStore().load()
    stopped = SimpleNamespace(**{**vars(gateway), "port": 9})
    server = _ToolServer(stopped, tmp_path)
    server.env["PERSONALCLAW_PORT"] = "9"
    async with server:
        ok, made = await server.call(
            "automation_create", {"name": "Bins reminder", "when": "in 3 hours", "say": "Bins."}
        )

    assert not ok, made
    assert made.startswith(
        "Error: Could not make this call: PersonalClaw's gateway makes it, and it did not answer"
    ), made
    assert [r.trigger.id for r in TriggerStore().load()] == [r.trigger.id for r in before]
    # Nothing else recorded it: the tool server's own row says it failed.
    assert _audited(gateway.home, "automation_create") == [("mcp", "failed")]
    assert server.refused == [], server.refused


def test_a_call_the_gateway_did_not_answer_in_time_says_it_may_have_been_made(monkeypatch):
    """A call the gateway did not answer in time may have been made there: it is not said made,
    nor said not made, and the tool server records it, since the gateway may not have."""
    sent: list[tuple[str, dict[str, Any], float | None]] = []

    def slow(path: str, body: dict[str, Any], *, timeout: float | None = None) -> dict:
        sent.append((path, body, timeout))
        return {
            "error": "the gateway did not answer POST /api/tools/invoke within 30 s",
            "timed_out": True,
        }

    monkeypatch.setattr(mcp_core, "_post", slow)
    made = mcp_core.made_by_the_gateway("hook_register", {"hook_id": "build-done"}, timeout=None)

    assert isinstance(made.answer, ToolFailure) and made.audited is False
    assert made.answer == (
        "Error: Asked PersonalClaw's gateway to make this call, and the gateway did not answer "
        "POST /api/tools/invoke within 30 s: it may have made it, so look before you call it again."
    )
    assert sent == [
        (
            "/api/tools/invoke",
            {"tool": "hook_register", "arguments": {"hook_id": "build-done"}},
            None,
        )
    ]


@pytest.mark.asyncio
async def test_a_call_the_gateway_refuses_is_said_in_its_words_and_saves_nothing(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """The gateway holds the call to what it holds a chat's to: a tool the owner switched off is
    refused, with the route's own sentence and code, and nothing is saved. 🔴 Red on main:
    the server made the call anyway, and its save ended the server."""
    from personalclaw.tool_providers import registry, tool_prefs

    resolved = await registry.resolve(registry.tool_surface(None), "automation_create")
    assert resolved is not None
    assert tool_prefs.set_enabled(resolved[0].name, "automation_create", False)["ok"]
    before = [r.trigger.id for r in TriggerStore().load()]
    async with _ToolServer(gateway, tmp_path) as server:
        ok, made = await server.call(
            "automation_create", {"name": "Bins reminder", "when": "in 3 hours", "say": "Bins."}
        )

    assert not ok, made
    assert made.startswith("Error [tool_disabled]: 'automation_create' is disabled"), made
    assert [r.trigger.id for r in TriggerStore().load()] == before
    assert _audited(gateway.home, "automation_create") == [("tool_invoke", "denied")]
    assert server.refused == [], server.refused


@pytest.mark.asyncio
async def test_a_temporary_chats_lasting_work_on_an_agent_cli_is_refused_where_it_is_made(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """The gateway makes the call as the work of the chat the tool server serves, so a Temporary
    chat's automation and callback are refused there as they are from the chat itself: each
    outlasts the chat. Each refusal is said in its words, under its code, and nothing is saved.
    The control for the move: the tool server refused these itself before, and the gateway must
    see the chat's memory mode to refuse them now."""
    from personalclaw import session_restrictions, webhook_callbacks

    gateway.state.get_or_create_session(
        TEMPORARY_KEY.removeprefix("dashboard:"), memory_mode="temporary"
    )
    session_restrictions.mark_own_model(TEMPORARY_KEY, CLI_MODEL)
    before = [r.trigger.id for r in TriggerStore().load()]
    async with _ToolServer(gateway, tmp_path, key=TEMPORARY_KEY) as server:
        ok, made = await server.call(
            "automation_create", {"name": "Bins reminder", "when": "in 3 hours", "say": "Bins."}
        )
        hooked_ok, hooked = await server.call(
            "hook_register", {"hook_id": "build-done", "context_summary": "Waiting for the build."}
        )

    assert not ok, made
    assert made.startswith(f"Error [{lasting_work.CODE}]: "), made
    assert "an automation is kept after the chat" in made, made
    assert [r.trigger.id for r in TriggerStore().load()] == before
    assert not hooked_ok and hooked.startswith(f"Error [{lasting_work.CODE}]: "), hooked
    assert webhook_callbacks.get("build-done") is None
    # Recorded once, by the route that refused it, and not again by the tool server (the rule that
    # refused it records its own row, as it does wherever it refuses).
    for tool in ("automation_create", "hook_register"):
        rows = [row for row in _audited(gateway.home, tool) if row[0] != "lasting_work"]
        assert rows == [("tool_invoke", "denied")], tool
    assert server.refused == [], server.refused


@pytest.mark.asyncio
async def test_a_call_refused_before_the_route_is_recorded_once_by_the_tool_server(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """A refusal the gateway makes before its route runs (here, of a credential it does not hold)
    records no tool call, so the tool server records the call, once, and says the refusal."""
    from personalclaw import webhook_callbacks

    (gateway.home / ".local_secret").write_text("0" * 32, encoding="utf-8")
    async with _ToolServer(gateway, tmp_path) as server:
        ok, hooked = await server.call(
            "hook_register", {"hook_id": "build-done", "context_summary": "Waiting for the build."}
        )

    assert not ok and hooked.startswith("Error [internal_secret_invalid]: "), hooked
    assert webhook_callbacks.get("build-done") is None
    assert _audited(gateway.home, "hook_register") == [("mcp", "failed")]
    assert server.refused == [], server.refused


@pytest.mark.asyncio
async def test_a_call_the_gateway_makes_is_confirmed_exactly_when_the_route_asks(
    gateway,  # noqa: F811 - the imported fixture
):
    """The route asks the caller of a call that may destroy to name that tier (``confirm_risk``).
    The tool server names it for a tool that declares itself destructive, so each call it hands
    on is confirmed exactly when the route asks: none is refused for want of the word, and none
    is given it that does not need it."""
    from personalclaw.task_modes import MAY_DESTROY, resolve_effective_risk
    from personalclaw.tool_providers import registry
    from personalclaw.tool_providers.base import RiskLevel, risk_from_annotations

    confirmed: list[str] = []
    for name in sorted(GATEWAY_MAKES):
        resolved = await registry.resolve(registry.tool_surface(None), name)
        assert resolved is not None, name
        asks = resolve_effective_risk(resolved[1].risk_level, name, "", {}) in MAY_DESTROY
        declared = (mcp_core.own_tool(name) or {}).get("annotations")
        confirms = risk_from_annotations(declared, trusted=True) is RiskLevel.DESTRUCTIVE
        assert asks == confirms, name
        if confirms:
            confirmed.append(name)
    assert confirmed == ["automation_delete", "automation_delete_all"]


#: The answers ``POST /api/tools/invoke`` can give a call the tool server hands it, as ``_post``
#: returns them: what the call is said as, and whether the gateway recorded it.
_ANSWERS = {
    "made": ({"ok": True, "output": "Hook registered.", "error": ""}, "Hook registered.", True),
    "failed by the tool": (
        {"ok": False, "output": "", "error": "no such hook", "code": "not_found"},
        "Error [not_found]: no such hook",
        True,
    ),
    "refused by the tool": (
        {"ok": False, "output": "", "error": "not now", "not_run": "refused_by_tool"},
        "Error: not now",
        True,
    ),
    "refused by the route": (
        {
            "error": "'hook_register' is disabled",
            "error_detail": {"code": "tool_disabled", "message": "'hook_register' is disabled"},
            "not_run": "tool_disabled",
        },
        "Error [tool_disabled]: 'hook_register' is disabled",
        True,
    ),
    "refused before the route": (
        {
            "error": "Unauthorized",
            "error_detail": {"code": "unauthorized", "message": "Unauthorized"},
        },
        "Error [unauthorized]: Unauthorized",
        False,
    ),
    "not read": (
        {"ok": False, "error": "tool not found: hook_register"},
        "Error: tool not found: hook_register",
        False,
    ),
    "not answered": (
        {"error": "<urlopen error [Errno 61] Connection refused>"},
        "Error: Could not make this call: PersonalClaw's gateway makes it, and it did not answer "
        "(<urlopen error [Errno 61] Connection refused>).",
        False,
    ),
    "not answered in time": (
        {
            "error": "the gateway did not answer POST /api/tools/invoke within 30 s",
            "timed_out": True,
        },
        "Error: Asked PersonalClaw's gateway to make this call, and the gateway did not answer "
        "POST /api/tools/invoke within 30 s: it may have made it, so look before you call it "
        "again.",
        False,
    ),
}


@pytest.mark.parametrize("answer", list(_ANSWERS))
def test_every_call_the_gateway_is_handed_is_recorded_once(answer, tmp_path, monkeypatch):
    """The tool server records a call it handed on only when the gateway did not: a call the route
    made or refused is the route's row, and any other answer, a refusal before the route among
    them, is the tool server's."""
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", True)
    reply, said, recorded_there = _ANSWERS[answer]
    monkeypatch.setattr(mcp_core, "_post", lambda *_a, **_kw: dict(reply))

    def here(_name: str, _args: dict[str, Any]) -> str:
        raise AssertionError("the tool server made the call itself")

    got = mcp_shared.call_tool_with_logging(
        "hook_register",
        {},
        lambda _n, a: a,
        here,
        session_key="mcp_core",
        downstream_service="t",
        gateway_makes={"hook_register": None},
    )

    assert got == said
    assert isinstance(got, ToolFailure) is (answer != "made")
    assert _audited(home, "hook_register") == ([] if recorded_there else [("mcp", "failed")])


@pytest.mark.parametrize(
    "refusal",
    [
        lasting_work.Refused("Nothing was set up: Jonas asked for this.", code=lasting_work.ASKED),
        lasting_work.Refused("This chat keeps nothing: none was registered."),
        memory_writes.MemoryWriteRefused("lessons"),
    ],
    ids=["someone else's say-so", "a private chat", "a memory write"],
)
def test_a_refusal_a_store_raises_is_said_in_its_words_under_its_code(
    refusal, tmp_path, monkeypatch
):
    """A store that refuses a call raises its refusal for each door to answer: here it is the
    call's answer, under the refusal's code, as the route answers it, never an uncoded
    "could not finish"."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))

    def refuses(_name: str, _args: dict[str, Any]) -> str:
        raise refusal

    answer = mcp_shared.call_tool_with_logging(
        "hook_register",
        {},
        lambda _n, a: a,
        refuses,
        session_key="mcp_core",
        downstream_service="t",
    )

    assert isinstance(answer, ToolFailure)
    assert answer == f"Error [{refusal.code}]: {refusal}", answer


def test_a_call_whose_work_fails_says_what_it_could_not_do(tmp_path, monkeypatch):
    """A write the call's work could not make is its answer, in the error's words, and recorded
    as a failed call. 🔴 Red on main: it raised, which ended the tool server."""
    home = tmp_path / ".personalclaw"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

    def refused(_name: str, _args: dict[str, Any]) -> str:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS), str(home / "notes.json"))

    answer = mcp_shared.call_tool_with_logging(
        "note_save", {}, lambda _n, a: a, refused, session_key="mcp_core", downstream_service="t"
    )

    assert isinstance(answer, ToolFailure)
    said = f"~/.personalclaw/notes.json: {os.strerror(errno.EROFS)}."
    assert answer == f"Error: note_save could not finish: {said}", answer
    rows = [json.loads(line) for line in (home / "security_events.jsonl").read_text().splitlines()]
    assert [(r["operation"], r["outcome"]) for r in rows] == [("note_save", "failed")]


def test_a_call_that_works_is_answered_as_before(tmp_path, monkeypatch):
    """The control: a call whose work does not fail answers what it answered."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))

    answer = mcp_shared.call_tool_with_logging(
        "note_read",
        {},
        lambda _n, a: a,
        lambda _n, _a: "Two notes.",
        session_key="mcp_core",
        downstream_service="t",
    )

    assert answer == "Two notes." and not isinstance(answer, ToolFailure)


# ── which calls the gateway makes ─────────────────────────────────────────────────────────────

#: The tools whose calls the gateway makes for the tool server an agent CLI runs: every automation
#: tool, the callback, tile and proposal tools, and the four whose work is a model's.
GATEWAY_MAKES = {
    *(tool["name"] for tool in mcp_automation._list_tools()),
    "hook_register",
    "dashboard_tile_propose",
    "skill_promote",
    "template_save_from_session",
    "project_context_review",
    "propose_template_diff",
    "best_of_n",
    "image_generate",
    "video_generate",
    "visualize",
}


def test_the_gateway_makes_exactly_the_calls_whose_work_writes_its_stores(monkeypatch):
    """Each tool the server lists is declared by its module as the gateway's or as its own; the
    ones that write a store at the top of the home are the gateway's, each waited for as its work
    takes, and every other tool is made in the tool server as before."""
    declared: dict[str, Any] = {}

    def noting(name: str, *_a: Any, gateway_makes: Any = None, **_kw: Any) -> str:
        declared[name] = gateway_makes[name] if gateway_makes and name in gateway_makes else "here"
        return "answered"

    monkeypatch.setattr(mcp_shared, "call_tool_with_logging", noting)
    listed = {tool["name"] for tool in mcp_core._aggregated_list_tools()}
    for name in listed:
        mcp_core._aggregated_call_tool(name, {})

    made_there = {name for name, waits in declared.items() if waits != "here"}
    assert made_there == GATEWAY_MAKES, made_there ^ GATEWAY_MAKES
    # A model's work is waited for as long as it takes: the four whose work is a model's, and the
    # three whose `when` a model may read.
    long_waits = {name for name in made_there if declared[name] is not None}
    assert long_waits == {
        "best_of_n",
        "image_generate",
        "video_generate",
        "visualize",
        "automation_create",
        "set_onetime_task",
        "set_recurring_task",
    }
    assert {declared[name] for name in long_waits} == {mcp_core.GATEWAY_MODEL_WORK_TIMEOUT_SECS}


def test_only_the_agent_clis_tool_server_hands_a_call_to_the_gateway(tmp_path, monkeypatch):
    """In the gateway, where the native agent's calls run, the same call is made in place."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    made: list[str] = []
    monkeypatch.setattr(
        mcp_core,
        "made_by_the_gateway",
        lambda name, _args, **_kw: made.append(name) or mcp_core.GatewayMade("there", True),
    )

    def call() -> str:
        return mcp_shared.call_tool_with_logging(
            "note_save",
            {},
            lambda _n, a: a,
            lambda _n, _a: "here",
            session_key="mcp_core",
            downstream_service="t",
            gateway_makes={"note_save": None},
        )

    assert call() == "here" and made == []
    monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", True)
    assert call() == "there" and made == ["note_save"]
    # The call made in place is recorded here; the one the gateway recorded is not again.
    assert _audited(tmp_path / "home", "note_save") == [("mcp", "completed")]


@pytest.mark.asyncio
async def test_an_agent_clis_start_makes_the_folders_its_own_tools_write_in(tmp_path, monkeypatch):
    """Like the skills folder: the locks, artifacts and workflows folders the tools the server
    makes itself write in are made before the CLI starts. 🔴 Red on main: none was made."""
    import sys

    from personalclaw.acp.transport import AcpProcess

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    seen = tmp_path / "seen.json"
    look = (
        "import json, os, sys; json.dump([os.path.isdir(p) for p in sys.argv[2:]], "
        "open(sys.argv[1], 'w'))"
    )
    folders = [home / "skills", home / "locks", home / "artifacts", home / "workflows"]
    agent = AcpProcess(
        command=[sys.executable, "-c", look, str(seen), *map(str, folders)],
        work_dir=tmp_path / "work",
        sandbox_mode="off",
    )
    with patch("personalclaw.session._track_pid"), patch("personalclaw.session._track_session_pid"):
        await agent.spawn()
        await asyncio.wait_for(agent.process.wait(), timeout=60)
    agent.teardown()

    assert json.loads(seen.read_text()) == [True, True, True, True]
