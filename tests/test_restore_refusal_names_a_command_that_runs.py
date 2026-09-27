"""A replace restore refused over HTTP names the command that does it, and that command runs.

🔴 The refusal said "stop the gateway and run `personalclaw restore --replace` instead". The
flag is ``--mode replace``, and ``restore`` needs the archive, so the one command the refusal
offered failed with ``unrecognized arguments: --replace``. It names the archive's path now,
quoted for a shell, and only when the archive is one this home's snapshot directory holds: the
id came in on the request, and the message is something a user pastes into a terminal.
"""

from __future__ import annotations

import json
import re
import shlex

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.cli import build_parser

_COMMAND = re.compile(r"`(personalclaw restore [^`]+)`")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home with space"
    h.mkdir()
    (h / "config.json").write_text(json.dumps({}))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    return h


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import durability as mod

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app.router.add_post("/api/durability/archive/{id}/restore", mod.api_durability_archive_restore)
    return app


async def _refusal(archive_id: str) -> str:
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            f"/api/durability/archive/{archive_id}/restore", json={"mode": "replace"}
        )
        assert resp.status == 409
        body = await resp.json()
    assert body["error"]["code"] == "gateway_running"
    return body["error"]["message"]


@pytest.mark.asyncio
async def test_the_refusal_names_a_restore_command_the_cli_takes(home):
    from personalclaw.snapshot import _default_snapshot_dir

    snapshots = home / "snapshots"
    snapshots.mkdir()
    archive = snapshots / "personalclaw-snapshot-20260926-120000.tar.gz"
    archive.write_bytes(b"not read by a refusal")
    assert _default_snapshot_dir() == str(snapshots)

    message = await _refusal(archive.name)

    found = _COMMAND.search(message)
    assert found, message
    args = build_parser().parse_args(shlex.split(found.group(1))[1:])
    assert (args.command, args.mode) == ("restore", "replace")
    assert args.snapshot == str(archive.resolve())


@pytest.mark.asyncio
async def test_an_id_that_is_not_an_archive_is_not_echoed_into_the_command(home):
    message = await _refusal("x%3B%20rm%20-rf%20~")

    assert "rm -rf" not in message
    assert "`personalclaw restore <archive> --mode replace`" in message
