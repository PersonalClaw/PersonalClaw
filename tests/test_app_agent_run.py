"""App-facing background agent API (#30).

An app that declares the ``agent`` permission can run a headless agent task and
poll its result — the NON-iframe agentic path (for apps that act on agent output
rather than show a human a chat window). Gated by permissions.agent; proxies to
the subagent runner. An app WITHOUT the permission is 403'd.

Authorization is two-layered (#410): the permission gate reads the CALLING app's
verified identity (``request["app"]``), never the caller-chosen ``{name}`` path
segment, and a run's data is only served to the app that spawned it.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import app_manager, manager
from personalclaw.dashboard.handlers.apps import register_app_routes


class _FakeSubagents:
    """Minimal subagents store: spawn returns a done info with a result.

    ``_runs`` mirrors ``SubagentManager._agents`` — ONE flat table every spawner
    shares — so ``record()`` can seed a run owned by a different spawner, which is
    what the ownership check has to keep out.
    """

    max_concurrent = 4

    def __init__(self):
        self._runs = {}

    def record(self, run_id, *, parent_session_key, task="someone else's task"):
        """Seed a run as if another spawner created it (owner /api/spawn, cron, a
        workflow stage, or a different app)."""
        info = SimpleNamespace(
            id=run_id,
            task=task,
            done=True,
            started=0.0,
            turns=2,
            result=f"Summarized: {task}",
            error="",
            result_path="",
            last_tool="",
            parent_session_key=parent_session_key,
            approval_mode=None,
            silent=False,
        )
        self._runs[run_id] = info
        return info

    def spawn(
        self,
        task,
        *,
        parent_session_key="",
        agent="",
        max_turns=0,
        approval_mode=None,
        capability_class=None,
        silent=False,
        cwd="",
    ):
        info = self.record(
            f"run-{len(self._runs) + 1}", parent_session_key=parent_session_key, task=task
        )
        info.approval_mode = approval_mode
        info.capability_class = capability_class
        info.silent = silent
        return info

    def get(self, run_id):
        return self._runs.get(run_id)


@asynccontextmanager
async def _client(tmp_path, *, calling_app=""):
    """A client for the app-agent routes. ``calling_app`` stamps ``request["app"]``
    the way the token-auth middleware does for an app-scoped token; leaving it empty
    is an owner-initiated (dashboard / CLI) call."""
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        if calling_app:

            @web.middleware
            async def stamp_app(request, handler):
                request["app"] = calling_app
                return await handler(request)

            app.middlewares.append(stamp_app)
        app["state"] = SimpleNamespace(subagents=_FakeSubagents())
        register_app_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


def _install(tmp_path: Path, name: str, *, agent_perm: bool):
    d = tmp_path / "src" / name
    d.mkdir(parents=True)
    mani = {"name": name, "version": "1.0.0", "displayName": name, "description": "x"}
    if agent_perm:
        mani["permissions"] = {"agent": True}
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    res = app_manager.install(d)
    assert res.ok, res.error


@pytest.mark.asyncio
async def test_agent_run_requires_permission(tmp_path):
    async with _client(tmp_path) as client:
        _install(tmp_path, "noperm", agent_perm=False)
        r = await client.post("/api/apps/noperm/agent-run", json={"task": "do a thing"})
        assert r.status == 403
        body = await r.json()
        assert "agent" in body["error"]


@pytest.mark.asyncio
async def test_agent_run_and_poll(tmp_path):
    async with _client(tmp_path) as client:
        _install(tmp_path, "runner", agent_perm=True)
        # start
        r = await client.post("/api/apps/runner/agent-run", json={"task": "summarize my notes"})
        assert r.status == 202, await r.text()
        rid = (await r.json())["id"]
        # poll
        r2 = await client.get(f"/api/apps/runner/agent-run/{rid}")
        assert r2.status == 200
        d = await r2.json()
        assert d["done"] is True
        assert d["result"] == "Summarized: summarize my notes"
        assert d["turns"] == 2  # turns must be present in the DONE response too


@pytest.mark.asyncio
async def test_agent_run_missing_task(tmp_path):
    async with _client(tmp_path) as client:
        _install(tmp_path, "runner", agent_perm=True)
        r = await client.post("/api/apps/runner/agent-run", json={})
        assert r.status == 400


@pytest.mark.asyncio
async def test_agent_run_status_requires_permission(tmp_path):
    async with _client(tmp_path) as client:
        _install(tmp_path, "noperm", agent_perm=False)
        r = await client.get("/api/apps/noperm/agent-run/run-1")
        assert r.status == 403


# ── #410: the permission gate reads the CALLER, not the URL ──


@pytest.mark.asyncio
async def test_agent_run_gates_on_calling_app_not_url_name(tmp_path):
    """An app naming a DIFFERENT, agent-permitted app in the path is still denied.

    ``/api/apps/*`` is reachable by prefix match from a declared ``api`` permission,
    so borrowing the path segment must not borrow the permission with it."""
    async with _client(tmp_path, calling_app="borrower") as client:
        _install(tmp_path, "borrower", agent_perm=False)
        _install(tmp_path, "runner", agent_perm=True)
        r = await client.post("/api/apps/runner/agent-run", json={"task": "do a thing"})
        assert r.status == 403
        # The message must name the app actually checked, not the path segment.
        body = await r.json()
        assert "borrower" in body["error"]
        assert "runner" not in body["error"]


@pytest.mark.asyncio
async def test_agent_run_status_gates_on_calling_app_not_url_name(tmp_path):
    async with _client(tmp_path, calling_app="borrower") as client:
        _install(tmp_path, "borrower", agent_perm=False)
        _install(tmp_path, "runner", agent_perm=True)
        client.app["state"].subagents.record("run-1", parent_session_key="app:runner")
        r = await client.get("/api/apps/runner/agent-run/run-1")
        assert r.status == 403
        body = await r.json()
        assert "borrower" in body["error"]


@pytest.mark.asyncio
async def test_agent_run_owner_initiated_is_unrestricted(tmp_path):
    """An owner-initiated call (no app identity) keeps working exactly as before:
    the path segment is its only identity, and it is not scoped to a run owner."""
    async with _client(tmp_path) as client:
        _install(tmp_path, "runner", agent_perm=True)
        # A run the OWNER spawned via POST /api/spawn — parent_session_key is empty.
        client.app["state"].subagents.record("owner-run", parent_session_key="", task="owner task")
        r = await client.get("/api/apps/runner/agent-run/owner-run")
        assert r.status == 200, await r.text()
        d = await r.json()
        assert d["task"] == "owner task"
        assert d["result"] == "Summarized: owner task"


# ── #410: a run's data is only served to the app that spawned it ──


@pytest.mark.asyncio
async def test_agent_run_status_denies_run_owned_by_another_spawner(tmp_path):
    """An agent-permitted app cannot read a run it did not spawn — the live repro:
    a run from the owner's POST /api/spawn (``parent_session_key == ""``) came back
    200 with its task text. 404 so the endpoint is not an id oracle."""
    async with _client(tmp_path, calling_app="runner") as client:
        _install(tmp_path, "runner", agent_perm=True)
        client.app["state"].subagents.record(
            "owner-run", parent_session_key="", task="the owner's private task"
        )
        r = await client.get("/api/apps/runner/agent-run/owner-run")
        assert r.status == 404
        body = await r.json()
        assert body["error"] == "not found"
        # Nothing about the run leaks — not even on the not-done path, where `task`
        # is returned before any result exists.
        assert "private task" not in await r.text()


@pytest.mark.asyncio
async def test_agent_run_status_allows_own_run(tmp_path):
    """The app's OWN run (``parent_session_key == "app:<name>"``) still reads back."""
    async with _client(tmp_path, calling_app="runner") as client:
        _install(tmp_path, "runner", agent_perm=True)
        r = await client.post("/api/apps/runner/agent-run", json={"task": "summarize my notes"})
        assert r.status == 202, await r.text()
        rid = (await r.json())["id"]
        assert client.app["state"].subagents.get(rid).parent_session_key == "app:runner"
        r2 = await client.get(f"/api/apps/runner/agent-run/{rid}")
        assert r2.status == 200, await r2.text()
        d = await r2.json()
        assert d["done"] is True
        assert d["result"] == "Summarized: summarize my notes"
