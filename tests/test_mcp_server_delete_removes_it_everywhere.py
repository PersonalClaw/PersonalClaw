"""Removing an MCP server removes it everywhere: both documents, and every value it owns.

🔴 THE DEFECT (measured by #3617's lane, and here on ``origin/main``). The MCP Tool Servers card in
Settings → Providers deleted a server from ``mcp.json`` only (``mcp_instances.delete_instance``).
The rebuild that followed starts from the existing agent config and merges additively, so the
server's copy in ``agents/personalclaw.json`` survived: still listed on the Tools page
(``list_servers`` reads the agent config first), still in ``tools``/``allowedTools``, and still
holding references to its credential-store keys, so ``write_mcp_document`` correctly refused to
delete them. Four routes removed a server four different ways; the Tools page's was the only one
that reached both files.

Now there is one delete (``secret_refs.remove_mcp_servers``), and both surfaces a user can remove a
server from go through it.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_names, get_credential

TOKEN = "ghp_fixtureDeleteMeToken00112233445566"
KEPT_TOKEN = "ghp_fixtureNeighbourToken99887766554433"


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    home = config_loader.config_dir()
    # `rebuild_agent_config` reads `agent._USER_DIR / "mcp.json"`, frozen at import.
    monkeypatch.setattr("personalclaw.agent._USER_DIR", home)
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    return home


def _put(name: str, body: dict):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request("PUT", f"/api/mcp/servers/{name}", match_info={"name": name})

    async def _json():
        return body

    req.json = _json
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def _delete_on_the_tools_page(name: str):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request("DELETE", f"/api/mcp/servers/{name}", match_info={"name": name})
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def _delete_on_the_provider_card(name: str):
    from personalclaw.providers.instance_routes import handle_delete_instance

    req = make_mocked_request(
        "DELETE",
        f"/api/providers/mcp-tools/instances/{name}",
        match_info={"name": "mcp-tools", "id": name},
    )
    return asyncio.run(handle_delete_instance(req))


def _doc(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _owned_keys(server: str) -> list[str]:
    from personalclaw.config.secret_refs import mcp_server_prefix

    return [k for k in credential_names() if k.startswith(mcp_server_prefix(server))]


def _two_servers(home) -> None:
    """``doomed`` and ``neighbour``, each with a stored token, in both documents."""
    from personalclaw.agent import rebuild_agent_config

    for name, token in (("doomed", TOKEN), ("neighbour", KEPT_TOKEN)):
        assert _put(name, {"command": "echo", "env": {"GITHUB_TOKEN": token}}).status == 200
    rebuild_agent_config()
    agent = _doc(home / "agents" / "personalclaw.json")
    assert {"doomed", "neighbour"} <= set(agent["mcpServers"]), "precondition: both are in both"
    assert "@doomed" in agent["tools"] and "@doomed" in agent["allowedTools"]
    assert _owned_keys("doomed"), "precondition: the token is in the credential store"


@pytest.mark.parametrize(
    "delete",
    [_delete_on_the_provider_card, _delete_on_the_tools_page],
    ids=["provider card", "tools page"],
)
def test_a_delete_removes_the_server_from_both_documents_and_its_secrets(home, delete) -> None:
    from personalclaw.agent import rebuild_agent_config
    from personalclaw.mcp_discovery import list_servers

    _two_servers(home)
    resp = delete("doomed")
    assert resp.status == 200, resp.text

    assert "doomed" not in _doc(home / "mcp.json")["mcpServers"]
    agent = _doc(home / "agents" / "personalclaw.json")
    assert "doomed" not in agent["mcpServers"], "the agent config kept the server"
    assert "@doomed" not in agent["tools"] and "@doomed" not in agent["allowedTools"]
    assert _owned_keys("doomed") == [], "the server's stored values outlived it"
    assert TOKEN not in (home / ".env").read_text(encoding="utf-8")
    assert "doomed" not in {s.name for s in list_servers()}, "the Tools page still lists it"

    # The rebuild every later change runs must not bring it back from anywhere.
    rebuild_agent_config()
    assert "doomed" not in _doc(home / "agents" / "personalclaw.json")["mcpServers"]

    # Blast radius: the other server and its value are untouched.
    assert "neighbour" in _doc(home / "mcp.json")["mcpServers"]
    assert "neighbour" in _doc(home / "agents" / "personalclaw.json")["mcpServers"]
    assert [get_credential(k) for k in _owned_keys("neighbour")] == [KEPT_TOKEN]


def test_deleting_a_server_that_exists_nowhere_is_a_404_that_says_so(home) -> None:
    resp = _delete_on_the_provider_card("never-added")
    assert resp.status == 404
    resp = _delete_on_the_tools_page("never-added")
    assert resp.status == 404
    assert "never-added" in json.loads(resp.text)["error"]["message"]
