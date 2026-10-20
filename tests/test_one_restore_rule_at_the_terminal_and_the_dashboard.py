"""One restore rule for the terminal and the dashboard: a merge runs while the gateway runs, and a
replace is refused in the same words by both.

🔴 The dashboard merged a snapshot or an export archive into a running home and refused only a
replace, naming ``personalclaw restore <archive> --mode replace``. That command refused EVERY
restore while the gateway ran, the merge the dashboard had just run included, and said so in words
of its own: "Gateway is running. Stop it first (personalclaw stop) or use --force." So the one
restore a running home can take was refused at the terminal, and the two surfaces gave the same
refusal two ways.

A merge only fills in what the home lacks, through the locks the running gateway's stores write
under, so it runs from either. A replace moves the live home aside under a gateway that holds that
state open, and both refuse it while the gateway runs, with one sentence naming the command that
does it once the gateway is stopped. ``--force`` stays the terminal's own override
(``test_snapshot.TestGatewayRunningRefusal``).
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home with space"
    h.mkdir()
    (h / "config.json").write_text(json.dumps({}), encoding="utf-8")
    (h / "tags.json").write_text(json.dumps([{"id": "t-here", "name": "Mine"}]), encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    from personalclaw import gateway_base

    # This process, as a gateway of this home records itself once it has bound its port.
    monkeypatch.setenv(gateway_base.PORT_ENV, "19999")
    gateway_base.publish(19999)
    monkeypatch.setattr("personalclaw.snapshot._running_gateway", gateway_base.live_gateway)
    return h


def _snapshot_holding(home: Path, tmp_path: Path, tags: list[dict]) -> Path:
    """A snapshot of a home holding *tags*, in this home's snapshot directory, as the dashboard's
    archive browser lists one."""
    import tarfile

    snapshots = home / "snapshots"
    snapshots.mkdir(exist_ok=True)
    stage = tmp_path / "stage" / "personalclaw-snapshot-20260928-120000"
    stage.mkdir(parents=True)
    (stage / "config.json").write_text("{}", encoding="utf-8")
    (stage / "tags.json").write_text(json.dumps(tags), encoding="utf-8")
    archive = snapshots / "personalclaw-snapshot-20260928-120000.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stage, arcname=stage.name)
    return archive


def _tags(home: Path) -> set[str]:
    return {t["id"] for t in json.loads((home / "tags.json").read_text(encoding="utf-8"))}


def test_a_merge_runs_at_the_terminal_while_the_gateway_runs(home, tmp_path):
    from personalclaw.snapshot import restore_main

    archive = _snapshot_holding(home, tmp_path, [{"id": "t-archived", "name": "Archived"}])

    assert restore_main([str(archive), "--mode", "merge"]) == 0

    assert _tags(home) == {"t-here", "t-archived"}


def test_an_export_archive_merges_at_the_terminal_while_the_gateway_runs(home, tmp_path):
    from personalclaw.snapshot import restore_main

    archive = tmp_path / "personalclaw-export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "personalclaw-export/MANIFEST.json", json.dumps({"version": 1, "format": "zip"})
        )
        zf.writestr(
            "personalclaw-export/tags.json", json.dumps([{"id": "t-exported", "name": "X"}])
        )

    assert restore_main([str(archive), "--mode", "merge"]) == 0

    assert _tags(home) == {"t-here", "t-exported"}


async def _dashboards_refusal(archive: Path) -> str:
    from personalclaw.dashboard.handlers import durability as mod

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app.router.add_post("/api/durability/archive/{id}/restore", mod.api_durability_archive_restore)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            f"/api/durability/archive/{archive.name}/restore", json={"mode": "replace"}
        )
        assert resp.status == 409
        body = await resp.json()
    assert body["error"]["code"] == "gateway_running"
    return body["error"]["message"]


@pytest.mark.asyncio
async def test_a_replace_is_refused_at_the_terminal_in_the_dashboards_words(home, tmp_path, capsys):
    from personalclaw.snapshot import restore_main

    archive = _snapshot_holding(home, tmp_path, [{"id": "t-archived", "name": "Archived"}])
    said_there = await _dashboards_refusal(archive)

    assert restore_main([str(archive.resolve()), "--mode", "replace"]) == 1

    said_here = capsys.readouterr().err.strip()
    assert re.sub(r"^\W+", "", said_here) == said_there, (said_here, said_there)
    assert f"the running gateway (pid {os.getpid()}, port 19999) holds open" in said_there
    assert _tags(home) == {"t-here"}, "nothing was replaced"
    assert not list(home.glob("pre-restore-*"))
