"""A credential in an MCP server's arguments or URL lives in the credential store; the file holds
a reference.

``env`` and ``headers`` values were already stored
(``test_mcp_server_secrets_live_in_the_credential_store``), but a token passed as an argument
(``--api-token=…``, ``--header "Authorization: …"``) or carried in the URL (``https://user:pw@…``,
``?token=…``) stayed in ``mcp.json`` as written. A server imported from another tool's setup with
one kept it there, so every snapshot, every shard export and every sync carried it, and Tools ›
Remove left it in the shard until the next hourly export.

Each spot where the place (a flag named for a credential, a header, a login, a query key) or the
format says a credential sits is now stored under a key the server owns; the start puts the value
back; the owner's yes is sealed without it; and a home that has the definition but not the value
asks for it instead of starting the server with a placeholder.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import tarfile
import textwrap
import zipfile

import pytest
from aiohttp.test_utils import make_mocked_request
from mcp_owner_allowed import allow, allow_configured, confirmed

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_names, delete_credential, get_credential
from personalclaw.config.secret_refs import (
    ForeignSecretReference,
    migrate_plaintext_secrets,
    resolve_mcp_spec,
    store_mcp_spec,
)
from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs

#: Invented fixture values: no real service issued any of them.
TOKEN = "fixture-todo-token-0000aaaa1111bbbb2222"
ROTATED = "fixture-todo-token-3333cccc4444dddd5555"
BEARER = "Bearer fixture-bearer-0000aaaa1111bbbb"
LOGIN = "fixture-user:fixture-pass-0000"
QUERY_KEY = "fixture-query-key-0000aaaa1111"
#: A key in a provider's format, built so no scanner reads the source as one.
KEY_FORMAT = "sk-proj-" + "fixture" * 4
#: A long word that only looks like a token: a package name. Masked on every page, never moved.
LOOKALIKE = "mcp-server-sqlite-v2-2024"
ADDRESS = "https://mcp.example.test/mcp"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


# A stdio MCP server that reports the token it was started with: the one tool's DESCRIPTION
# carries a digest of the `--api-token=` argument, so a probe (initialize + tools/list only) can
# see what the child received without the value appearing anywhere.
_ARGV_SERVER = textwrap.dedent("""
    import hashlib, sys
    from mcp.server.fastmcp import FastMCP

    _given = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--api-token=")), "")
    mcp = FastMCP("argv")

    @mcp.tool(description=f"token digest {hashlib.sha256(_given.encode()).hexdigest()[:16]}")
    def given() -> str:
        return hashlib.sha256(_given.encode()).hexdigest()[:16]

    if __name__ == "__main__":
        mcp.run()
    """)


@pytest.fixture
def home():
    home = config_loader.config_dir()
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []})
    )
    return home


@pytest.fixture
def argv_server(tmp_path):
    script = tmp_path / "argv_server.py"
    script.write_text(_ARGV_SERVER)
    return str(script)


def _put(name: str, body: dict):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    read = asyncio.run(
        mcp_mod.api_mcp_server_detail(
            make_mocked_request("GET", f"/api/mcp/servers/{name}", match_info={"name": name})
        )
    )
    headers = {"If-Match": f'"{json.loads(read.text)["revision"]}"'} if read.status == 200 else {}
    req = make_mocked_request(
        "PUT", f"/api/mcp/servers/{name}", headers=headers, match_info={"name": name}
    )

    async def _json():
        return confirmed(body)

    req.json = _json
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def _get(name: str) -> dict:
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request("GET", f"/api/mcp/servers/{name}", match_info={"name": name})
    return json.loads(asyncio.run(mcp_mod.api_mcp_server_detail(req)).text)


def _delete(name: str):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request("DELETE", f"/api/mcp/servers/{name}", match_info={"name": name})
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(server: str) -> list[str]:
    from personalclaw.config.secret_refs import mcp_server_prefix

    return [k for k in credential_names() if k.startswith(mcp_server_prefix(server))]


def _ref_in(text: str) -> str:
    """The one store key a stored argument or URL refers to."""
    from personalclaw.workflows.secrets import SECRET_BINDING_RE

    [key] = SECRET_BINDING_RE.findall(text)
    return key


# ── the file holds a reference ──────────────────────────────────────────────


def test_a_token_in_the_arguments_leaves_only_a_reference_in_both_documents(home):
    body = {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]}
    assert _put("todo", body).status == 200

    spec = _read(home / "mcp.json")["mcpServers"]["todo"]
    assert spec["args"][0] == "todo-mcp"
    assert spec["args"][1].startswith("--api-token={{secret:PCSECRET_MCP_")
    assert get_credential(_ref_in(spec["args"][1])) == TOKEN
    for document in (home / "mcp.json", home / "agents" / "personalclaw.json"):
        assert TOKEN not in document.read_text()
    # The agent config's copy refers to the same key.
    copy = _read(home / "agents" / "personalclaw.json")["mcpServers"]["todo"]
    assert copy["args"][1] == spec["args"][1]


def test_each_place_a_credential_sits_is_stored_and_everything_else_stays_as_written(home):
    args = [
        "-y",
        LOOKALIKE,
        "--api-key",
        TOKEN,
        "--header",
        f"Authorization: {BEARER}",
        f"https://{LOGIN}@mcp.example.test/mcp?api_key={QUERY_KEY}&region=eu",
        KEY_FORMAT,
        "--db",
        "/tmp/fixture.db",
    ]
    stored = store_mcp_spec("todo", {"command": "npx", "args": args}, strict=True)
    text = json.dumps(stored)
    for secret in (TOKEN, BEARER.split()[1], LOGIN, QUERY_KEY, KEY_FORMAT):
        assert secret not in text
    kept = stored["args"]
    # What is not a credential stays exactly as written, the look-alike package name included.
    assert kept[:3] == ["-y", LOOKALIKE, "--api-key"]
    assert kept[4] == "--header" and kept[5].startswith("Authorization: {{secret:")
    assert kept[6].startswith("https://{{secret:") and kept[6].endswith("&region=eu")
    assert "@mcp.example.test/mcp?api_key={{secret:" in kept[6]
    assert kept[8:] == ["--db", "/tmp/fixture.db"]
    # The start reads every value back, exactly where it was.
    assert resolve_mcp_spec("todo", stored)["args"] == args
    # Storing the stored form again changes nothing and stores nothing new.
    before = sorted(_keys("todo"))
    assert store_mcp_spec("todo", stored, strict=True) == stored
    assert sorted(_keys("todo")) == before


def test_a_remote_servers_address_keeps_its_credential_in_the_store(home):
    url = f"{ADDRESS}?token={QUERY_KEY}"
    assert _put("hosted", {"transport": "http", "url": url}).status == 200
    spec = _read(home / "mcp.json")["mcpServers"]["hosted"]
    assert QUERY_KEY not in (home / "mcp.json").read_text()
    assert spec["url"].startswith(f"{ADDRESS}?token={{{{secret:")
    assert resolve_mcp_spec("hosted", spec)["url"] == url


# ── the started server receives the value ───────────────────────────────────


def test_the_spawned_server_receives_the_value_in_its_arguments(home, argv_server):
    body = {"command": sys.executable, "args": [argv_server, f"--api-token={TOKEN}"]}
    assert _put("argv", body).status == 200
    assert TOKEN not in (home / "mcp.json").read_text()

    async def listed() -> str:
        reg = McpClientRegistry()
        try:
            reg.load_from_specs(_personalclaw_mcp_specs())
            conn = reg.get("argv")
            assert conn is not None
            ok, out = await conn.call_tool("given", {})
            assert ok, out
            return out
        finally:
            await reg.shutdown_all()

    assert asyncio.run(listed()) == _digest(TOKEN)


def test_the_probe_starts_the_server_with_the_value(home, argv_server):
    from personalclaw.mcp_discovery import McpServerInfo, probe_server

    stored = store_mcp_spec(
        "argv",
        {"command": sys.executable, "args": [argv_server, f"--api-token={TOKEN}"]},
        strict=True,
    )
    assert TOKEN not in json.dumps(stored)
    server = McpServerInfo(name="argv", command=sys.executable, args=stored["args"])
    allow(server)
    probed = asyncio.run(probe_server(server))
    assert probed.status == "ok", probed.error
    [tool] = probed.tools
    assert tool["description"] == f"token digest {_digest(TOKEN)}"


# ── nothing that copies the home carries the value ──────────────────────────


def test_no_snapshot_export_shard_export_or_sync_carries_the_argument_token(home, tmp_path):
    from personalclaw.durability.shards import export_shards
    from personalclaw.durability.sync_cycle import run_sync_cycle
    from personalclaw.portability import create_export_zip
    from personalclaw.snapshot import snapshot_main
    from tests.test_durability_sync_cycle import SharedStore

    body = {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]}
    assert _put("todo", body).status == 200

    assert snapshot_main([str(tmp_path / "snaps")]) == 0
    [archive] = list((tmp_path / "snaps").glob("personalclaw-snapshot-*.tar.gz"))
    with tarfile.open(archive, "r:gz") as tar:
        snapshot = [tar.extractfile(i).read() for i in tar.getmembers() if i.isfile()]
    data, _manifest = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        export = [zf.read(i) for i in zf.infolist() if not i.is_dir()]
    export_shards(home, tmp_path / "shards")
    shards = [p.read_bytes() for p in (tmp_path / "shards").rglob("*") if p.is_file()]
    store = SharedStore()
    assert run_sync_cycle(store, home, self_id="A", now="t").ok
    synced = list(store.objects.values())

    for copies in (snapshot, export, shards, synced):
        assert copies and not [c for c in copies if TOKEN.encode() in c]
    # The definition still travels, with the reference a restore resolves where the value is.
    assert any(b"--api-token={{secret:PCSECRET_MCP_" in c for c in shards)


def test_removing_the_server_deletes_the_value_its_arguments_stored(home):
    assert (
        _put("todo", {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]}).status == 200
    )
    assert _keys("todo")
    assert _delete("todo").status == 200
    assert _keys("todo") == []
    assert TOKEN not in (home / ".env").read_text()


# ── the edit form ───────────────────────────────────────────────────────────


def test_the_edit_form_shows_the_mask_and_its_save_keeps_or_replaces_the_value(home):
    from personalclaw.apps.secret_fields import SECRET_MASK

    assert (
        _put("todo", {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]}).status == 200
    )
    [key] = _keys("todo")
    shown = _get("todo")
    assert shown["args"] == ["todo-mcp", f"--api-token={SECRET_MASK}"]

    # Saved as it was shown, plus an argument: the stored value stays, under the same key.
    resp = _put("todo", {"command": "uvx", "args": [*shown["args"], "--verbose"]})
    assert resp.status == 200, resp.text
    assert _keys("todo") == [key] and get_credential(key) == TOKEN
    assert _read(home / "mcp.json")["mcpServers"]["todo"]["args"][2] == "--verbose"

    # A value typed over the mask replaces it, and the old one is gone from the store.
    resp = _put("todo", {"command": "uvx", "args": ["todo-mcp", f"--api-token={ROTATED}"]})
    assert resp.status == 200, resp.text
    assert _keys("todo") == [key] and get_credential(key) == ROTATED
    assert TOKEN not in (home / ".env").read_text()
    assert ROTATED not in (home / "mcp.json").read_text()


def test_a_reference_to_another_servers_key_is_refused(home):
    assert (
        _put("todo", {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]}).status == 200
    )
    [key] = _keys("todo")
    borrowed = {"command": "uvx", "args": ["other-mcp", "--api-token={{secret:" + key + "}}"]}
    resp = _put("other", borrowed)
    assert resp.status == 400
    assert json.loads(resp.text)["error"]["code"] == "secret_owned_elsewhere"
    with pytest.raises(ForeignSecretReference):
        resolve_mcp_spec("other", borrowed)


# ── the owner's yes ─────────────────────────────────────────────────────────


def test_the_owners_yes_is_sealed_without_the_values_and_still_to_what_runs(home):
    from personalclaw import mcp_grants

    def seal(spec: dict) -> dict:
        return mcp_grants.definition(mcp_grants.server_of("todo", spec))

    written = {"command": "uvx", "args": [LOOKALIKE, f"--api-token={TOKEN}"]}
    stored = store_mcp_spec("todo", written, strict=True)
    # Written or stored, the same definition: the value is not in the seal.
    assert seal(written) == seal(stored)
    assert seal({**written, "args": [LOOKALIKE, f"--api-token={ROTATED}"]}) == seal(written)
    # What runs is: a look-alike package name masked on every page is still sealed as written.
    assert seal({**written, "args": ["mcp-server-sqlite-v3-2025", f"--api-token={TOKEN}"]}) != (
        seal(written)
    )
    # A save through the Tools page leaves the server allowed as it was saved.
    assert _put("todo", written).status == 200
    allowed = mcp_grants.server_of("todo", _read(home / "mcp.json")["mcpServers"]["todo"])
    assert mcp_grants.allowed(allowed)


# ── the gateway's start moves what an earlier release left ──────────────────


def test_the_boot_move_stores_an_imported_servers_argument_token_and_keeps_its_allow(home):
    from personalclaw import mcp_grants

    written = {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]}
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"todo": written}}))
    (home / "agents" / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {"todo": {**written, "command": "/usr/bin/uvx"}}})
    )
    # The owner's yes as an earlier release sealed it: to the arguments as written.
    before = mcp_grants.server_of("todo", written)
    as_written = {**mcp_grants.definition(before), "args": written["args"], "url": ""}
    mcp_grants.BOOK.give("todo", json.dumps(as_written, sort_keys=True, separators=(",", ":")))
    assert not mcp_grants.allowed(before)

    moved = migrate_plaintext_secrets()
    assert "mcp.json" in moved and "agents/personalclaw.json" in moved
    live = _read(home / "mcp.json")["mcpServers"]["todo"]
    copy = _read(home / "agents" / "personalclaw.json")["mcpServers"]["todo"]
    assert get_credential(_ref_in(live["args"][1])) == TOKEN
    assert copy["args"] == live["args"]
    for document in (home / "mcp.json", home / "agents" / "personalclaw.json"):
        assert TOKEN not in document.read_text()
    # The yes the owner gave is kept: the server does not wait for an Allow nobody asked for.
    assert mcp_grants.allowed(mcp_grants.server_of("todo", live))
    # Idempotent.
    assert migrate_plaintext_secrets() == []


# ── a home that has the definition and not the value asks for it ────────────


def test_a_home_restored_without_the_value_asks_for_it_and_starts_nothing(home):
    from personalclaw.config.secret_refs import MissingSecretValue
    from personalclaw.mcp_discovery import list_servers, probe_server

    assert (
        _put("todo", {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]}).status == 200
    )
    # Restored onto another machine: the definition came, the credential store did not.
    for key in _keys("todo"):
        delete_credential(key)
    spec = _read(home / "mcp.json")["mcpServers"]["todo"]
    with pytest.raises(MissingSecretValue) as missing:
        resolve_mcp_spec("todo", spec)
    assert str(missing.value) == (
        "todo needs the value of --api-token, which is not saved on this machine, so it was not "
        "started. Edit it on the Tools page and type the value in place of ••••••••."
    )
    allow_configured("todo")
    assert "todo" not in _personalclaw_mcp_specs()
    [server] = [s for s in list_servers() if s.name == "todo"]
    probed = asyncio.run(probe_server(server))
    assert probed.status == "error"
    assert probed.error == str(missing.value)
