"""The route contracts: who may call them, and what it takes to overwrite a home.

These are the SECURITY properties of the DSAR surface, so they are tested at the route
rather than at the function: the refusal and the confirmation gate live in the handler,
and a test that called `create_export_zip` directly would prove nothing about whether an
app can reach it over HTTP.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home, set on the environment: the isolation the product ships, which every
    reader of the home — `portability._pc_dir()` included — follows."""
    h = tmp_path / "home"
    h.mkdir()
    (h / "config.json").write_text(json.dumps({"theme": "dark"}))
    (h / "tasks").mkdir()
    (h / "tasks" / "t1.json").write_text(json.dumps({"id": "t1"}))
    (h / ".env").write_text("ANTHROPIC_API_KEY=fake-anthropic-1\n")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.portability.config_dir", lambda: h)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    return h


def _app(*, app_token: str = "") -> web.Application:
    """The routes, with a middleware that stands in for the token-auth middleware.

    `app_token` non-empty simulates an app-scoped caller — the same `request["app"]`
    the real middleware sets from a token's `app` claim.
    """
    from personalclaw.dashboard.handlers import durability as mod

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        request["app"] = app_token
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app.router.add_post("/api/durability/export", mod.api_durability_export)
    app.router.add_post("/api/durability/import", mod.api_durability_import)
    app.router.add_get("/api/durability/archive", mod.api_durability_archive)
    app.router.add_post("/api/durability/archive/{id}/restore", mod.api_durability_archive_restore)
    return app


# ── the app-token refusal (least privilege) ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/durability/export"),
        ("post", "/api/durability/import"),
        ("post", "/api/durability/archive/x.tar.gz/restore"),
    ],
)
async def test_an_app_scoped_caller_is_refused(home, method, path):
    """An installed app may not export, import or restore whole-home state.

    Matches `apps.api_app_token`'s precedent. An export hands the caller everything
    PersonalClaw knows about the user; an app that can call it has exfiltrated the whole
    home with one request regardless of its declared permissions.
    """
    async with TestClient(TestServer(_app(app_token="some-app"))) as client:
        resp = await getattr(client, method)(path)
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "owner_only"


@pytest.mark.asyncio
async def test_the_owner_is_not_refused(home):
    """The vacuity floor for the test above: without it, a route that 403s EVERYONE would
    pass every refusal test while being completely broken."""
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/export", json={})
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/zip"


# ── export ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_returns_a_zip_with_no_secret_bytes(home):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/export", json={})
        blob = await resp.read()
    assert b"fake-anthropic-1" not in blob
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert any(n.endswith("MANIFEST.json") for n in names)
    assert any(n.endswith("config.json") for n in names)


@pytest.mark.asyncio
async def test_export_scopes_to_the_requested_domains(home):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/export", json={"domains": ["work"]})
        blob = await resp.read()
    names = [n.split("/", 1)[1] for n in zipfile.ZipFile(io.BytesIO(blob)).namelist() if "/" in n]
    assert "tasks/t1.json" in names
    assert "config.json" not in names
    assert 'filename="personalclaw-export-work-' in resp.headers["Content-Disposition"]


@pytest.mark.asyncio
async def test_export_names_the_valid_domains_on_a_typo(home):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/export", json={"domains": ["wrok"]})
        assert resp.status == 400
        body = await resp.json()
    assert body["error"]["code"] == "unknown_domain"
    assert "work" in body["error"]["message"]


@pytest.mark.asyncio
async def test_export_rejects_a_non_list_domains_field(home):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/export", json={"domains": "work"})
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "bad_domains"


# ── import: plan-first, and the confirm gate on replace ──────────────────────


def _archive(tmp: Path, *, theme: str = "imported") -> Path:
    """A v2 archive built by hand — no dependency on the exporter under test."""
    path = tmp / "in.zip"
    with zipfile.ZipFile(path, "w") as zf:
        root = "personalclaw-export-20260101T000000Z"
        zf.writestr(f"{root}/config.json", json.dumps({"theme": theme}))
        zf.writestr(f"{root}/MANIFEST.json", json.dumps({"version": 2, "contents": {}}))
    return path


def _multipart(path: Path) -> dict:
    return {"file": path.read_bytes()}


@pytest.mark.asyncio
async def test_import_without_a_mode_validates_and_applies_nothing(home, tmp_path):
    """Plan-first: no `mode` means look, don't touch. The safe default for a verb that
    can rewrite a home — a caller must see the archive and then ask again."""
    before = (home / "config.json").read_text()
    archive = _archive(tmp_path)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/import", data=_multipart(archive))
        body = await resp.json()
    assert resp.status == 200
    assert body["ok"] is True and body["applied"] is False
    assert body["manifest"]["version"] == 2
    assert body["manifest"]["verified"] is False, "a v2 archive carries no checksums"
    assert (home / "config.json").read_text() == before, "nothing may be written"


@pytest.mark.asyncio
async def test_import_merge_applies(home, tmp_path):
    archive = _archive(tmp_path)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/import?mode=merge", data=_multipart(archive))
        body = await resp.json()
    assert body["ok"] is True and body["applied"] is True
    # Merge is copy-if-missing, so the home's own config is untouched.
    assert json.loads((home / "config.json").read_text())["theme"] == "dark"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["mode=replace", "mode=replace&confirm=true"])
async def test_an_import_that_replaces_is_refused_as_the_archive_restore_refuses_one(
    home, tmp_path, query
):
    """🔴 Red before: `mode=replace&confirm=true` moved the live home aside and wrote the archive in
    its place, under the running gateway — the replace the archive restore refuses. One rule for
    both: refused, `gateway_running`, before the upload is read, naming the command that does it
    with the gateway stopped — and that command runs."""
    import re
    import shlex

    from personalclaw.cli import build_parser

    archive = _archive(tmp_path)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(f"/api/durability/import?{query}", data=_multipart(archive))
        body = await resp.json()
    assert resp.status == 409
    assert body["error"]["code"] == "gateway_running"
    assert json.loads((home / "config.json").read_text())["theme"] == "dark"
    assert not list(home.glob("pre-restore-*")), "nothing was moved aside"
    command = re.search(r"`(personalclaw restore [^`]+)`", body["error"]["message"])
    assert command, body["error"]["message"]
    args = build_parser().parse_args(shlex.split(command.group(1))[1:])
    assert (args.command, args.mode, args.snapshot) == ("restore", "replace", "<export.zip>")


@pytest.mark.asyncio
async def test_the_archive_merge_restore_runs_while_the_gateway_is_up(home, tmp_path, monkeypatch):
    """🔴 Red before: the merge reached a restore that probed for a running gateway of this home,
    and inside the gateway it always found one — Merge-restore answered 409 every time. It merges
    now, as an import's merge does, and says the gateway picks everything up once restarted."""
    from personalclaw import snapshot as snap_mod

    snaps = tmp_path / "snaps"
    monkeypatch.setattr(snap_mod, "_default_snapshot_dir", lambda: str(snaps))
    (home / "tasks" / "from-snap.json").write_text(json.dumps({"id": "from-snap"}))
    assert snap_mod.snapshot_main([str(snaps)]) == 0
    (archive,) = snaps.glob("*.tar.gz")
    (home / "tasks" / "from-snap.json").unlink()
    monkeypatch.setattr(snap_mod, "_is_gateway_running", lambda: True)  # as it is, serving this

    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            f"/api/durability/archive/{archive.name}/restore",
            json={"mode": "merge", "confirm": True},
        )
        body = await resp.json()

    assert resp.status == 200, body
    assert body["ok"] is True and body["restart"] == snap_mod.MERGE_RESTART_NOTE
    assert (home / "tasks" / "from-snap.json").is_file(), "the snapshot's row came back"


@pytest.mark.asyncio
async def test_import_rejects_an_unknown_mode(home, tmp_path):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/import?mode=obliterate")
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "bad_mode"


@pytest.mark.asyncio
async def test_import_requires_a_multipart_body(home):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/import?mode=merge", json={})
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "multipart_required"


@pytest.mark.asyncio
async def test_import_rejects_a_corrupt_archive_before_writing(home, tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip at all")
    before = (home / "config.json").read_text()
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/durability/import?mode=merge", data={"file": bad.read_bytes()}
        )
        body = await resp.json()
    assert resp.status == 400
    assert body["error"]["code"] == "invalid_archive"
    assert (home / "config.json").read_text() == before


# ── archive + archive restore ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_lists_nothing_on_a_fresh_home(home):
    async with TestClient(TestServer(_app())) as client:
        body = await (await client.get("/api/durability/archive")).json()
    assert body["archives"] == []
    assert body["last_drill"]["ran"] is False, "a fresh home has never drilled"
    assert body["last_drill"]["ok"] is None, "unknown must never render as a pass"


@pytest.mark.asyncio
async def test_archive_restore_rejects_an_archive_outside_the_snapshot_dir(home):
    """Path containment: a caller may not point a restore at any tar on disk."""
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/archive/..%2F..%2Fetc%2Fpasswd/restore", json={})
        assert resp.status == 404
        assert (await resp.json())["error"]["code"] == "archive_not_found"


@pytest.mark.asyncio
async def test_archive_restore_refuses_replace_over_http_always(home):
    """🔴 A replace over HTTP is refused unconditionally, `confirm` or not.

    Driven, not reasoned: a `mode=replace&confirm=true` request to a gateway on --port
    10188 returned 200 and PERFORMED the replace over the live home. Cause —
    `snapshot._is_gateway_running()` probes the CONFIGURED port, so on any non-default
    port it probed a dead socket and reported "not running". Serving this request is
    proof the gateway is up, so the handler answers from that instead of the network.
    """
    async with TestClient(TestServer(_app())) as client:
        for body in ({"mode": "replace"}, {"mode": "replace", "confirm": True}):
            resp = await client.post("/api/durability/archive/whatever.tar.gz/restore", json=body)
            assert resp.status == 409, body
            assert (await resp.json())["error"]["code"] == "gateway_running", body


@pytest.mark.asyncio
async def test_archive_restore_merge_without_confirm_is_refused(home):
    """Checked BEFORE the archive is resolved, so the refusal is the gate rather than a
    side effect of a 404. A merge writes into the live home, so it is confirmed too."""
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/durability/archive/whatever.tar.gz/restore", json={"mode": "merge"}
        )
        assert resp.status == 409
        assert (await resp.json())["error"]["code"] == "confirm_required"


@pytest.mark.asyncio
async def test_archive_restore_rejects_an_unknown_component(home):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/durability/archive/x.tar.gz/restore",
            json={"mode": "merge", "confirm": True, "components": ["nonsense"]},
        )
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "unknown_component"
