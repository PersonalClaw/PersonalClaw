"""App-facing background agent API (#30).

An app that declares the ``agent`` permission can run a headless agent task and
poll its result — the NON-iframe agentic path (for apps that act on agent output
rather than show a human a chat window). Gated by permissions.agent; proxies to
the subagent runner. An app WITHOUT the permission is 403'd.
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
    """Minimal subagents store: spawn returns a done info with a result."""

    max_concurrent = 4

    def __init__(self):
        self._runs = {}

    def spawn(
        self,
        task,
        *,
        parent_session_key="",
        agent="",
        max_turns=0,
        approval_mode=None,
        silent=False,
        cwd="",
    ):
        info = SimpleNamespace(
            id="run-1",
            task=task,
            done=True,
            started=0.0,
            turns=2,
            result=f"Summarized: {task}",
            error="",
            result_path="",
            last_tool="",
            parent=parent_session_key,
            approval_mode=approval_mode,
            silent=silent,
        )
        self._runs[info.id] = info
        return info

    def get(self, run_id):
        return self._runs.get(run_id)


@asynccontextmanager
async def _client(tmp_path):
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        app = web.Application()
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
