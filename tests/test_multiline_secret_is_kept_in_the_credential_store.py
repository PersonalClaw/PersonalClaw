"""A multi-line secret (a PEM key, a service-account JSON) is kept in the credential store.

🔴 THE DEFECT (``origin/main``). The credential store's ``.env`` backend wrote ``KEY=VALUE`` one per
line, verbatim, and read it back the same way, so a value with a newline split into lines that
parsed as OTHER keys. #3617 therefore refused a multi-line value typed into the Add form (400), and
left one it found in a file (the boot move, an import from Claude Code) inline in ``mcp.json``, in
plaintext, and in every export, which is where the secrets most worth protecting (private keys)
stayed. The keychain backend always held them; only the file format could not.

So the format holds them. A value a line cannot carry verbatim is written double-quoted with
backslash escapes, the form python-dotenv reads back as the same string. That matters because
``personalclaw``'s CLI loads this same file with python-dotenv at startup, so both readers have to
agree. A value that needs no quoting is written exactly as before. NUL is still refused: no
environment variable can hold one.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request
from dotenv import dotenv_values

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import get_credential, save_credential

PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7fixture\n"
    "c2VjcmV0LWZpeHR1cmUtbGluZS10d28=\n"
    "-----END PRIVATE KEY-----"
)


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    home = config_loader.config_dir()
    monkeypatch.setattr("personalclaw.agent._USER_DIR", home)
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    return home


@pytest.mark.parametrize(
    "value",
    [
        PEM,
        '{\n  "type": "service_account",\n  "private_key": "-----BEGIN-----\\nabc\\n"\n}',
        "crlf\r\nline",
        'a "quoted" word',
        '"starts with a quote',
        "back\\slash and \\n literally",
        "has #hash inside",
        "  padded  ",
        "tab\tinside",
        "ends with a backslash\\",
    ],
)
def test_a_value_round_trips_through_the_env_file_and_python_dotenv_agrees(home, value) -> None:
    # Owned keys (PCSECRET_…) are the ones settings records write; a neighbour on each side
    # checks the line format did not disturb the rest of the file.
    save_credential("PCSECRET_TEST__BEFORE", "before-value")
    save_credential("PCSECRET_TEST__VALUE", value)
    save_credential("PCSECRET_TEST__AFTER", "after-value")
    assert get_credential("PCSECRET_TEST__VALUE") == value
    env = home / ".env"
    lines = [
        ln
        for ln in env.read_text(encoding="utf-8").splitlines()
        if ln.startswith("PCSECRET_TEST__")
    ]
    assert len(lines) == 3, f"the value was not kept on one line: {lines}"
    parsed = dotenv_values(env, interpolate=False)
    assert parsed["PCSECRET_TEST__VALUE"] == value, "python-dotenv reads the file differently"
    assert get_credential("PCSECRET_TEST__BEFORE") == "before-value"
    assert get_credential("PCSECRET_TEST__AFTER") == "after-value"
    # Rewriting a neighbour must not disturb an encoded value.
    save_credential("PCSECRET_TEST__AFTER", "after-value-2")
    assert get_credential("PCSECRET_TEST__VALUE") == value


def test_a_plain_value_is_written_exactly_as_before(home) -> None:
    save_credential("PCSECRET_TEST__PLAIN", "sk-abc123_DEF-456")
    assert "PCSECRET_TEST__PLAIN=sk-abc123_DEF-456\n" in (home / ".env").read_text(encoding="utf-8")


def _put(name: str, body: dict):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request("PUT", f"/api/mcp/servers/{name}", match_info={"name": name})

    async def _json():
        return body

    req.json = _json
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def test_the_add_form_stores_a_multi_line_value_and_the_server_gets_it_exactly(home) -> None:
    from personalclaw.mcp_client import _personalclaw_mcp_specs

    resp = _put("gdrive", {"command": "echo", "env": {"GOOGLE_PRIVATE_KEY": PEM}})
    assert resp.status == 200, resp.text
    raw = (home / "mcp.json").read_text(encoding="utf-8")
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7fixture" not in raw
    spec = json.loads(raw)["mcpServers"]["gdrive"]
    assert spec["env"]["GOOGLE_PRIVATE_KEY"].startswith("{{secret:")
    assert _personalclaw_mcp_specs()["gdrive"]["env"]["GOOGLE_PRIVATE_KEY"] == PEM


def test_a_multi_line_value_left_in_mcp_json_is_moved_at_boot(home) -> None:
    from personalclaw.config.secret_refs import migrate_plaintext_secrets
    from personalclaw.mcp_client import _personalclaw_mcp_specs

    (home / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"gdrive": {"command": "echo", "env": {"GOOGLE_PRIVATE_KEY": PEM}}}}
        ),
        encoding="utf-8",
    )
    migrate_plaintext_secrets()
    spec = json.loads((home / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["gdrive"]
    assert spec["env"]["GOOGLE_PRIVATE_KEY"].startswith("{{secret:"), "the PEM stayed inline"
    assert _personalclaw_mcp_specs()["gdrive"]["env"]["GOOGLE_PRIVATE_KEY"] == PEM


def test_a_nul_is_still_refused_with_a_sentence_that_says_why(home) -> None:
    resp = _put("bad", {"command": "echo", "env": {"TOKEN": "abc\x00def"}})
    assert resp.status == 400
    message = json.loads(resp.text)["error"]["message"]
    assert "TOKEN" in message and "NUL" in message, message
    assert not (home / "mcp.json").exists() or "bad" not in (home / "mcp.json").read_text()
