"""An MCP server's save, Allow and sign-out start its probe after their security row, and always.

🔴 THE DEFECT (eae670179, #5715's first commit). The save (``PUT /api/mcp/servers/{name}``) started
the server's probe again (``_recheck``), then awaited its security row (``sel_log_async``). That
await gave the probe a turn before the request answered: it spawned the server. A probe that does
not finish at once when cancelled (on Linux, a stdio server's) then held a request run to its end:
seven MCP tests timed out at 30 s on every Linux shard. Allow and sign-out did the same with
``_forget_connections``, which closes the server's connections and probes it again.

Each now writes its row first and starts the probe in a ``finally``, so nothing is awaited once the
probe is started, and the close and the probe happen whatever the row does: written, refused (the
log could not take it), or its request cancelled there. The refusal and the cancellation still
reach the caller.

The probe here is a stand-in that records how far it got, and the connections' close one that
records what it closed. A probe started after the request's last await gets its first step as the
request's loop closes, as on main, and is cancelled at its first await; one started before an await
gets past it (the real one spawns its server there).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request
from mcp_owner_allowed import confirmed

from personalclaw.config import loader as config_loader

NAME = "gh"


@pytest.fixture
def home():
    home = config_loader.config_dir()
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    return home


@pytest.fixture
def probes(monkeypatch) -> dict[str, list[Any]]:
    """What was asked of the probe and of the connections' close, and whether a probe got past its
    first await."""
    from personalclaw import mcp_client, mcp_discovery

    seen: dict[str, list[Any]] = {"asked": [], "ran": [], "closed": []}

    def recheck(names, hold=None, **_kw) -> None:
        async def probe() -> None:
            await asyncio.sleep(0)
            seen["ran"].append(tuple(names))
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                # A probe whose teardown does not finish at once (a stdio server's, on Linux).
                await asyncio.sleep(3)
                raise

        seen["asked"].append(tuple(names))
        task = asyncio.get_running_loop().create_task(probe())
        if hold is not None:
            hold.add(task)

    monkeypatch.setattr(mcp_discovery, "recheck", recheck)
    monkeypatch.setattr(
        mcp_client, "close_servers", lambda which: seen["closed"].append(which(NAME))
    )
    return seen


def _request(method: str, path: str, body: dict[str, Any] | None = None, headers=None):
    req = make_mocked_request(method, path, headers=headers or {}, match_info={"name": NAME})

    async def _json():
        return body or {}

    req.json = _json
    return req


def _save(body: dict[str, Any] | None = None):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    read = asyncio.run(mcp_mod.api_mcp_server_detail(_request("GET", f"/api/mcp/servers/{NAME}")))
    headers = {}
    if read.status == 200:
        headers["If-Match"] = f'"{json.loads(read.text)["revision"]}"'
    req = _request(
        "PUT", f"/api/mcp/servers/{NAME}", confirmed(body or {"command": "echo"}), headers
    )
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def _allow():
    from personalclaw import mcp_grants
    from personalclaw.dashboard.handlers import mcp as mcp_mod
    from personalclaw.mcp_discovery import list_servers

    server = next(s for s in list_servers(include_disabled=True) if s.name == NAME)
    body = confirmed({"revision": mcp_grants.revision(server)})
    return asyncio.run(
        mcp_mod.api_mcp_server_allow(_request("POST", f"/api/mcp/servers/{NAME}/allow", body))
    )


def _sign_out():
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = _request("DELETE", f"/api/mcp/servers/{NAME}/sign-in")
    return asyncio.run(mcp_mod.api_mcp_server_sign_in(req))


def _reset(seen: dict[str, list[Any]]) -> None:
    for v in seen.values():
        v.clear()


@pytest.mark.parametrize("change", ["save", "allow", "sign out"])
def test_the_probe_gets_no_turn_before_the_answer(home, probes, change) -> None:
    """🔴 Red on eae670179 for the save, Allow and sign-out: the row's await ran the probe."""
    assert _save().status == 200
    _reset(probes)
    resp = {"save": _save, "allow": _allow, "sign out": _sign_out}[change]()
    assert resp.status == 200, resp.text
    assert probes["asked"] == [(NAME,)], "the change did not probe the server again"
    assert probes["ran"] == [], "the probe got past its first await before the request answered"
    if change != "save":
        assert probes["closed"] == [True], "its connections were not closed"


@pytest.mark.parametrize("change", ["save", "allow", "sign out"])
@pytest.mark.parametrize("row", ["refused", "cancelled"])
def test_the_probe_and_the_close_happen_whatever_the_row_does(
    home, probes, monkeypatch, change, row
) -> None:
    """A row the log cannot take, or a request cancelled at its row, still closes the server's
    connections and probes it again, and the refusal or the cancellation reaches the caller."""
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    assert _save().status == 200
    _reset(probes)
    raised = {
        "refused": OSError("the security log could not take the row"),
        "cancelled": asyncio.CancelledError(),
    }[row]

    async def row_that_fails(*_a, **_kw):
        raise raised

    monkeypatch.setattr(mcp_mod, "sel_log_async", row_that_fails)
    with pytest.raises(type(raised)):
        {"save": _save, "allow": _allow, "sign out": _sign_out}[change]()
    assert probes["asked"] == [(NAME,)], "the change did not probe the server again"
    if change != "save":
        assert probes["closed"] == [True], "a connection outlived the change"
