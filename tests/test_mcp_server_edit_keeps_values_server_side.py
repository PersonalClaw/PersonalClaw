"""An MCP server can be edited, and its stored values never come back to the browser.

🔴 THE DEFECT (``origin/main``). There was no edit for an existing MCP server. The Tools page could
add one (``PUT /api/mcp/servers/{name}``) and remove it; the provider card edits command and
arguments but has no environment at all. So a server moved into the credential store at boot, with
every value stored, ``LOG_LEVEL`` included, could only be fixed by deleting it and adding it again,
retyping every token. And ``PUT`` refreshed only ``mcp.json``: the agent config's copy of an
existing server was left as it was, and ``list_servers`` reads that copy first, so the edited file
was not the server the Tools page listed and probed.

The edit goes through the same write path as the add, ``PUT /api/mcp/servers/{name}``, which
``secret_refs`` turns into references. The form reads ``GET /api/mcp/servers/{name}``: each
variable's NAME, whether it is plain, a plain one's value, and for a stored one only whether a value
is saved. A variable sent back in ``keepEnv`` keeps the value already stored; one sent with a value
is stored again.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_names, get_credential
from personalclaw.config.secret_refs import make_ref, mcp_server_prefix, ref_key

TOKEN = "ghp_fixtureEditFormToken0011223344556677"
NEW_TOKEN = "ghp_fixtureRotatedToken8899aabbccddeeff"
SETTING = "fixture-region-eu-west-9"


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


def _call(method: str, name: str, body: dict | None = None):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request(method, f"/api/mcp/servers/{name}", match_info={"name": name})

    async def _json():
        return body

    req.json = _json
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def _doc(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _spec(home) -> dict:
    return _doc(home / "mcp.json")["mcpServers"]["gh"]


def _agent_copy(home) -> dict:
    return _doc(home / "agents" / "personalclaw.json")["mcpServers"]["gh"]


def _stored_key(home, var: str) -> str:
    key = ref_key(_spec(home)["env"][var])
    assert key, f"{var} is not stored: {_spec(home)}"
    return key


def _owned_keys() -> list[str]:
    return [k for k in credential_names() if k.startswith(mcp_server_prefix("gh"))]


def _add(home) -> None:
    resp = _call(
        "PUT",
        "gh",
        {
            "command": "echo",
            "args": ["--port", "7"],
            "env": {"GITHUB_TOKEN": TOKEN, "LOG_LEVEL": "debug"},
            "plainEnv": ["LOG_LEVEL"],
        },
    )
    assert resp.status == 200, resp.text


def test_the_edit_form_reads_names_and_plain_values_never_a_stored_value(home) -> None:
    _add(home)
    resp = _call("GET", "gh")
    assert resp.status == 200, resp.text
    assert TOKEN not in resp.text, "the stored value reached the browser"
    body = json.loads(resp.text)
    assert body["command"] == "echo"
    assert body["args"] == ["--port", "7"]
    assert body["editable"] is True
    assert body["env"] == [
        {"name": "GITHUB_TOKEN", "plain": False, "hasValue": True},
        {"name": "LOG_LEVEL", "plain": True, "value": "debug"},
    ]


def test_saving_an_edit_without_touching_a_secret_keeps_it(home) -> None:
    from personalclaw.mcp_discovery import list_servers

    _add(home)
    key = _stored_key(home, "GITHUB_TOKEN")
    resp = _call(
        "PUT",
        "gh",
        {
            "command": "echo",
            "args": ["--port", "8"],
            "env": {"LOG_LEVEL": "info"},
            "plainEnv": ["LOG_LEVEL"],
            "keepEnv": ["GITHUB_TOKEN"],
        },
    )
    assert resp.status == 200, resp.text
    assert get_credential(key) == TOKEN, "keeping a value lost it"
    assert _spec(home)["env"] == {"GITHUB_TOKEN": make_ref(key), "LOG_LEVEL": "info"}
    assert _spec(home)["args"] == ["--port", "8"]
    # The agent config's copy follows the edit, and so does what the Tools page lists and probes.
    assert _agent_copy(home)["args"] == ["--port", "8"]
    assert _agent_copy(home)["env"] == {"GITHUB_TOKEN": make_ref(key), "LOG_LEVEL": "info"}
    assert next(s for s in list_servers() if s.name == "gh").args == ["--port", "8"]


def test_removing_arguments_and_a_variable_removes_them_from_both_documents(home) -> None:
    _add(home)
    resp = _call("PUT", "gh", {"command": "echo", "keepEnv": ["GITHUB_TOKEN"]})
    assert resp.status == 200, resp.text
    for doc in (_spec(home), _agent_copy(home)):
        assert not doc.get("args"), f"the cleared arguments survived: {doc}"
        assert set(doc["env"]) == {"GITHUB_TOKEN"}, f"the removed variable survived: {doc}"
        assert "LOG_LEVEL" not in doc.get("plainEnv", [])


def test_a_new_value_is_stored_again_and_the_old_one_is_gone(home) -> None:
    from personalclaw.mcp_client import _personalclaw_mcp_specs

    _add(home)
    key = _stored_key(home, "GITHUB_TOKEN")
    resp = _call(
        "PUT",
        "gh",
        {
            "command": "echo",
            "env": {"GITHUB_TOKEN": NEW_TOKEN, "LOG_LEVEL": "debug"},
            "plainEnv": ["LOG_LEVEL"],
        },
    )
    assert resp.status == 200, resp.text
    assert get_credential(key) == NEW_TOKEN
    assert TOKEN not in (home / ".env").read_text(encoding="utf-8")
    assert _personalclaw_mcp_specs()["gh"]["env"]["GITHUB_TOKEN"] == NEW_TOKEN


def test_marking_a_stored_value_plain_moves_it_into_the_file(home) -> None:
    # A server moved at boot has every value stored, settings included — the case #3617 left as
    # "to mark one plain, re-add the server".
    (home / "mcp.json").write_text(
        json.dumps({"mcpServers": {"gh": {"command": "echo", "env": {"REGION": SETTING}}}}),
        encoding="utf-8",
    )
    from personalclaw.config.secret_refs import migrate_plaintext_secrets

    migrate_plaintext_secrets()
    assert ref_key(_spec(home)["env"]["REGION"]), "precondition: the boot move stored it"

    resp = _call("PUT", "gh", {"command": "echo", "keepEnv": ["REGION"], "plainEnv": ["REGION"]})
    assert resp.status == 200, resp.text
    assert _spec(home)["env"] == {"REGION": SETTING}
    assert _spec(home)["plainEnv"] == ["REGION"]
    assert _owned_keys() == [], "the value marked plain is still in the credential store"


def test_keeping_a_value_the_server_does_not_have_is_refused(home) -> None:
    _add(home)
    resp = _call("PUT", "gh", {"command": "echo", "keepEnv": ["NEVER_SET"]})
    assert resp.status == 400
    assert "NEVER_SET" in json.loads(resp.text)["error"]["message"]
    assert _spec(home)["env"]["GITHUB_TOKEN"].startswith("{{secret:"), "a refusal changed the file"


def test_an_edit_keeps_what_the_form_does_not_own(home) -> None:
    _add(home)
    doc = _doc(home / "mcp.json")
    doc["mcpServers"]["gh"].update(disabled=True, disabledTools=["delete_repo"], cwd="/srv/gh")
    (home / "mcp.json").write_text(json.dumps(doc), encoding="utf-8")

    resp = _call("PUT", "gh", {"command": "echo", "keepEnv": ["GITHUB_TOKEN"]})
    assert resp.status == 200, resp.text
    spec = _spec(home)
    assert spec["disabled"] is True, "saving an edit switched the server back on"
    assert spec["disabledTools"] == ["delete_repo"], "saving an edit re-enabled a tool"
    assert spec["cwd"] == "/srv/gh"


def test_a_server_the_user_does_not_own_reads_as_not_editable_and_says_why(home) -> None:
    (home / "mcp.json").write_text(
        json.dumps({"mcpServers": {"someapp:server": {"command": "node", "args": ["srv.js"]}}}),
        encoding="utf-8",
    )
    # The managed server lives in the agent config, where the rebuild installs it.
    (home / "agents" / "personalclaw.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "personalclaw-core": {
                        "command": "/opt/pc/bin/personalclaw",
                        "args": ["mcp-core"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    for name, owner in (("personalclaw-core", "PersonalClaw"), ("someapp:server", "someapp")):
        resp = _call("GET", name)
        assert resp.status == 200, resp.text
        body = json.loads(resp.text)
        assert body["editable"] is False, body
        assert owner in body["reason"], body
    assert _call("GET", "never-added").status == 404


def test_saving_over_a_server_the_user_does_not_own_is_refused_and_changes_nothing(home) -> None:
    before = (home / "mcp.json").read_bytes() if (home / "mcp.json").exists() else None
    for name, owner in (("personalclaw-core", "PersonalClaw"), ("someapp:server", "someapp")):
        resp = _call("PUT", name, {"command": "/tmp/not-the-real-server"})
        assert resp.status == 409, resp.text
        error = json.loads(resp.text)["error"]
        assert error["code"] == "mcp_server_not_editable"
        assert owner in error["message"]
    after = (home / "mcp.json").read_bytes() if (home / "mcp.json").exists() else None
    assert after == before, "a refused save wrote mcp.json"


def test_arguments_that_are_not_strings_are_refused(home) -> None:
    resp = _call("PUT", "gh", {"command": "echo", "args": ["--port", 7]})
    assert resp.status == 400
    assert json.loads(resp.text)["error"]["code"] == "invalid_field_type"
    assert not (home / "mcp.json").exists(), "a refused save wrote mcp.json"
