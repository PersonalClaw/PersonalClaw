"""Every page that shows an MCP server's command line or address masks the credentials in it with
one mask, and a save puts back each masked value it was not shown.

🔴 THE DEFECT (measured before this change). The Tools page's edit form showed a stored token in
clear: a server started as ``todo-mcp --api-token=<hex>`` read back with the token in the
Arguments field, while the question Allow asks about the same server masked it. The edit form used
the generic display mask (``security.redact_for_display``), which knows token SHAPES and the names
``api_key``, ``access_token`` and a few more, but not a credential-named flag such as
``--api-token``, while the Allow question and the import list used the argument mask
(``mcp_discovery.masked_args``). The MCP Tool Servers card in Settings → Providers masked nothing at
all, and it lists every server on each visit.

The contract now: the edit form and the card show a server through ``mcp_discovery.masked_args``,
``masked_command`` and ``masked_url``, the mask the Allow question uses. A save that sends a mask
back where it was shown keeps the stored value; one typed over replaces it; one whose flag or
address changed around it is refused, and nothing is saved, so the mask is never stored as a value
and a hidden credential is never carried to an address its owner typed without seeing it.

Every command here cannot resolve and every probe a save starts is left out: nothing runs.
"""

from __future__ import annotations

import asyncio
import json
import types
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request
from mcp_owner_allowed import confirmed

from personalclaw.apps.secret_fields import SECRET_MASK
from personalclaw.config import loader as config_loader

#: A command that cannot resolve: nothing a fixture names is ever started.
UNRESOLVABLE = "/nonexistent/pc-fixture-mcp"
#: Invented tokens, hex and base64url: no named rule knows either shape.
TOKEN = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c"
ROTATED = "9a8b7c6d5e4f30211203f4e5d6c7b8a99a8b7c6d"
OTHER = "Zm9vYmFyLWZpeHR1cmUtb25seS10b2tlbg"


@pytest.fixture
def home(monkeypatch):
    from personalclaw.dashboard.handlers import mcp as mcp_handlers

    home = config_loader.config_dir()
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    # What a save starts after it lands is another test's subject: here nothing is probed.
    # (`raising=False`: without the fix there is nothing to leave out, and the test fails on what
    # the form shows rather than on the patch.)
    monkeypatch.setattr(mcp_handlers, "_recheck", lambda request, *names: None, raising=False)
    return home


def _call(method: str, name: str, body: dict | None = None, headers: dict | None = None):
    from personalclaw.dashboard.handlers import mcp as mcp_handlers

    req = make_mocked_request(
        method, f"/api/mcp/servers/{name}", headers=headers or {}, match_info={"name": name}
    )

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return asyncio.run(mcp_handlers.api_mcp_server_detail(req))


def _read(name: str) -> dict[str, Any]:
    resp = _call("GET", name)
    assert resp.status == 200, resp.text
    return json.loads(resp.text)


def _save(name: str, body: dict[str, Any], read: dict[str, Any]):
    """What the edit form does: saves over the revision its read reported, once its owner agreed."""
    return _call("PUT", name, confirmed(body), headers={"If-Match": f'"{read["revision"]}"'})


def _spec(home, name: str) -> dict[str, Any]:
    return json.loads((home / "mcp.json").read_text(encoding="utf-8"))["mcpServers"][name]


def _started(home, name: str) -> dict[str, Any]:
    """The server as it is started: every value the file keeps in the credential store, in
    place. ``mcp.json`` itself holds a reference where each credential was."""
    from personalclaw.config.secret_refs import resolve_mcp_spec

    return resolve_mcp_spec(name, _spec(home, name))


def _add(name: str, body: dict[str, Any]) -> None:
    resp = _call("PUT", name, confirmed(body))
    assert resp.status == 200, resp.text


# ── the Tools page's edit form ──────────────────────────────────────────────


def test_the_edit_form_masks_a_credential_named_flag_as_the_allow_question_does(home) -> None:
    from personalclaw import mcp_grants

    _add("todo", {"command": UNRESOLVABLE, "args": ["todo-mcp", f"--api-token={TOKEN}"]})
    read = _read("todo")
    assert TOKEN not in json.dumps(read), "the stored token reached the edit form"
    assert read["args"] == ["todo-mcp", f"--api-token={SECRET_MASK}"]
    # The one mask: what the Allow question shows of the same server.
    shown = mcp_grants.shown(mcp_grants.server_of("todo", _spec(home, "todo")))
    assert shown["args"] == read["args"]


def test_a_mask_left_as_shown_keeps_the_token_and_one_typed_over_replaces_it(home) -> None:
    _add("todo", {"command": UNRESOLVABLE, "args": ["todo-mcp", f"--api-token={TOKEN}"]})
    read = _read("todo")
    kept = _save("todo", {"command": read["command"], "args": [*read["args"], "--verbose"]}, read)
    assert kept.status == 200, kept.text
    assert _started(home, "todo")["args"] == ["todo-mcp", f"--api-token={TOKEN}", "--verbose"]
    assert TOKEN not in (home / "mcp.json").read_text(encoding="utf-8")

    read = _read("todo")
    replaced = _save(
        "todo",
        {"command": read["command"], "args": ["todo-mcp", f"--api-token={ROTATED}", "--verbose"]},
        read,
    )
    assert replaced.status == 200, replaced.text
    assert _started(home, "todo")["args"] == ["todo-mcp", f"--api-token={ROTATED}", "--verbose"]
    assert ROTATED not in json.dumps(_read("todo"))


def test_a_mask_whose_flag_changed_is_refused_and_nothing_is_saved(home) -> None:
    _add("todo", {"command": UNRESOLVABLE, "args": ["todo-mcp", f"--api-token={TOKEN}"]})
    read = _read("todo")
    resp = _save(
        "todo", {"command": read["command"], "args": ["todo-mcp", f"--key={SECRET_MASK}"]}, read
    )
    assert resp.status == 409, resp.text
    error = json.loads(resp.text)["error"]
    assert error["code"] == "mask_conflict" and "Nothing was saved" in error["message"]
    assert _started(home, "todo")["args"] == ["todo-mcp", f"--api-token={TOKEN}"]


def test_each_masked_flag_value_keeps_its_own_value_when_the_arguments_around_it_move(
    home,
) -> None:
    """Two masked values show alike; the flag before each says which one it stands for."""
    _add("pair", {"command": UNRESOLVABLE, "args": ["--api-key", TOKEN, "--token", OTHER]})
    read = _read("pair")
    assert read["args"] == ["--api-key", SECRET_MASK, "--token", SECRET_MASK]
    resp = _save("pair", {"command": read["command"], "args": ["--token", SECRET_MASK]}, read)
    assert resp.status == 200, resp.text
    assert _started(home, "pair")["args"] == ["--token", OTHER]


def test_an_argument_with_nothing_to_mask_comes_back_exactly_as_it_was(home) -> None:
    """A path with two spaces in it is one argument the server needs as written."""
    args = ["--root", "/srv/My  Notes", "--port=7000"]
    _add("files", {"command": UNRESOLVABLE, "args": args})
    read = _read("files")
    assert read["args"] == args
    resp = _save("files", {"command": read["command"], "args": read["args"]}, read)
    assert resp.status == 200, resp.text
    assert _spec(home, "files")["args"] == args


def test_a_url_shows_its_token_masked_and_a_changed_address_never_takes_it(home) -> None:
    url = f"https://mcp.example.test/mcp?token={TOKEN}"
    _add("hosted", {"transport": "http", "url": url})
    read = _read("hosted")
    assert read["url"] == f"https://mcp.example.test/mcp?token={SECRET_MASK}"
    kept = _save("hosted", {"transport": "http", "url": read["url"]}, read)
    assert kept.status == 200, kept.text
    assert _started(home, "hosted")["url"] == url

    read = _read("hosted")
    moved = _save(
        "hosted",
        {"transport": "http", "url": f"https://elsewhere.example.test/mcp?token={SECRET_MASK}"},
        read,
    )
    assert moved.status == 409, moved.text
    assert json.loads(moved.text)["error"]["code"] == "mask_conflict"
    assert _started(home, "hosted")["url"] == url, "the hidden token went to a new address"


# ── the MCP Tool Servers card in Settings → Providers ────────────────────────


def _card(monkeypatch):
    """The card's provider as the instance routes find it, with its real manifest's schema."""
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.apps.native_contract import NATIVE_DIR

    manifest = AppManifest.from_json_file(NATIVE_DIR / "mcp-tools" / "app.json")
    ext = types.SimpleNamespace(provider_config=manifest.provider)
    registry = types.SimpleNamespace(get=lambda name: ext if name == "mcp-tools" else None)
    monkeypatch.setattr("personalclaw.providers.registry.get_provider_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.instance_routes._rebuild_agent_config_safe", lambda: None
    )
    monkeypatch.setattr(
        "personalclaw.providers.instance_routes._mcp_changed", lambda *a, **k: None, raising=False
    )


def _card_call(handler, method: str, instance: str | None = None, body=None, headers=None):
    path = "/api/providers/mcp-tools/instances" + (f"/{instance}" if instance else "")
    match = {"name": "mcp-tools", **({"id": instance} if instance else {})}
    req = make_mocked_request(method, path, headers=headers or {}, match_info=match)

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return asyncio.run(handler(req))


def test_the_card_lists_every_server_with_its_credentials_masked(home, monkeypatch) -> None:
    from personalclaw.providers import instance_routes

    _card(monkeypatch)
    _add("todo", {"command": UNRESOLVABLE, "args": ["todo-mcp", f"--api-token={TOKEN}"]})
    _add("hosted", {"transport": "http", "url": f"https://mcp.example.test/mcp?token={OTHER}"})
    resp = _card_call(instance_routes.handle_list_instances, "GET")
    assert resp.status == 200, resp.text
    assert TOKEN not in resp.text and OTHER not in resp.text, "a stored token reached the card"
    cards = {i["id"]: i["config"] for i in json.loads(resp.text)["instances"]}
    assert cards["todo"]["args"] == f"todo-mcp --api-token={SECRET_MASK}"
    assert cards["hosted"]["endpoint"] == f"https://mcp.example.test/mcp?token={SECRET_MASK}"


def test_the_card_saves_a_masked_server_with_its_token_kept(home, monkeypatch) -> None:
    from personalclaw.providers import instance_routes

    _card(monkeypatch)
    _add("todo", {"command": UNRESOLVABLE, "args": ["todo-mcp", f"--api-token={TOKEN}"]})
    listed = json.loads(_card_call(instance_routes.handle_list_instances, "GET").text)
    [card] = [i for i in listed["instances"] if i["id"] == "todo"]
    body = confirmed({"config": {**card["config"], "args": card["config"]["args"] + " --verbose"}})
    resp = _card_call(
        instance_routes.handle_update_instance,
        "PUT",
        "todo",
        body,
        headers={"If-Match": f'"{card["revision"]}"'},
    )
    assert resp.status == 200, resp.text
    assert TOKEN not in resp.text
    assert _started(home, "todo")["args"] == ["todo-mcp", f"--api-token={TOKEN}", "--verbose"]

    moved = confirmed({"config": {**card["config"], "args": f"todo-mcp --key={SECRET_MASK}"}})
    listed = json.loads(_card_call(instance_routes.handle_list_instances, "GET").text)
    [card] = [i for i in listed["instances"] if i["id"] == "todo"]
    refused = _card_call(
        instance_routes.handle_update_instance,
        "PUT",
        "todo",
        moved,
        headers={"If-Match": f'"{card["revision"]}"'},
    )
    assert refused.status == 409, refused.text
    assert json.loads(refused.text)["error"]["code"] == "mask_conflict"
    assert _started(home, "todo")["args"] == ["todo-mcp", f"--api-token={TOKEN}", "--verbose"]
