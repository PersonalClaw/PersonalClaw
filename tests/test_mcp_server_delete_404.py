"""`DELETE /api/mcp/servers/{name}` on an unknown name must carry an `error` key (#2942).

The handler already 404s correctly when nothing was removed — the status is right,
but the body used to be ``{"ok": False, "name": ..., "removed": False}`` with no
``error`` key at all. The frontend's ``errEnvelope`` parser finds neither ``error``
nor ``detail`` in that shape and falls back to rendering the toast as the bare
string ``HTTP 404``, even though the 409 branch of the very same handler (an
app-owned server) was written specifically to carry a human sentence. Fixed by
routing the 404 through the shared ``json_error`` envelope, reusing the generic
`not_found` wire code — no new code minted, no HTTP_ERROR_CODES row needed — while
keeping `ok`/`name`/`removed` at the top level so an existing reader of those keys
is unaffected.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web


class _NoLock:
    async def __aenter__(self):
        pass

    async def __aexit__(self, *a):
        pass


def _delete_request(name: str) -> MagicMock:
    request = MagicMock(spec=web.Request)
    request.method = "DELETE"
    request.match_info = {"name": name}
    return request


def _isolate(mcp_mod, tmp_path, monkeypatch):
    """Point every store this handler touches at tmp_path, and no-op the lock +
    agent-config sync so the test never reads/writes anything under the real home."""
    mc_path = tmp_path / "personalclaw.mcp.json"
    global_path = tmp_path / "global_mcp.json"
    monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
    monkeypatch.setattr(mcp_mod, "_GLOBAL_MCP_JSON", global_path)
    monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())
    monkeypatch.setattr(mcp_mod, "_server_in_agent_config", lambda name: False)
    monkeypatch.setattr(mcp_mod, "_sync_mcp_to_agent", lambda name, enabled, remove=False: None)
    monkeypatch.setattr(mcp_mod.sel(), "log_api_access", lambda **kw: None, raising=False)
    return mc_path


@pytest.mark.asyncio
async def test_delete_unknown_server_404s_with_error_envelope(tmp_path, monkeypatch):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    _isolate(mcp_mod, tmp_path, monkeypatch)

    resp = await mcp_mod.api_mcp_server_detail(_delete_request("qa23-nope-zzz"))
    assert resp.status == 404
    body = json.loads(resp.body)
    # The bug: this used to have no "error" key at all.
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"]
    # ok/name/removed stay at the top level for any existing reader of those keys.
    assert body["ok"] is False
    assert body["name"] == "qa23-nope-zzz"
    assert body["removed"] is False


@pytest.mark.asyncio
async def test_delete_existing_server_still_200s_and_removes_it(tmp_path, monkeypatch):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    mc_path = _isolate(mcp_mod, tmp_path, monkeypatch)
    mc_path.write_text(json.dumps({"mcpServers": {"real-srv": {"command": "x"}}}))

    resp = await mcp_mod.api_mcp_server_detail(_delete_request("real-srv"))
    assert resp.status == 200
    body = json.loads(resp.body)
    assert body == {"ok": True, "name": "real-srv", "removed": True}
    assert "real-srv" not in json.loads(mc_path.read_text())["mcpServers"]
