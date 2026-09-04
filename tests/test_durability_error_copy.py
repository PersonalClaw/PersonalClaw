"""Crash outcomes speak guidance, not internals (audit: raw str(exc) as user copy).

A broad ``except Exception`` catch is where a traceback's last words used to become the
wire ``message`` on the durability import/restore routes — the two most trust-sensitive
flows the product has. These rails pin the split the handlers now implement:

* a CRASH answers 500 with a registered code and a way forward ("check the gateway
  log"), and the exception text goes to the log and the audit row — never the wire;
* a DESIGNED refusal is unaffected — restore's refusals travel as ``ok: false`` result
  values on the 200/409 path, and the knowledge store's authored ``ValueError`` texts
  ("no such item …") still pass through verbatim.

Tested at the route, like the sibling DSAR tests: the leak lived in the handler's
catch, so calling the service function directly would prove nothing.
"""

from __future__ import annotations

import io
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

SECRET = "secret-internal-path-0451"


def _durability_app() -> web.Application:
    from personalclaw.dashboard.handlers import durability as mod

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app.router.add_post("/api/durability/import", mod.api_durability_import)
    app.router.add_post("/api/durability/archive/{id}/restore", mod.api_durability_archive_restore)
    return app


@pytest.mark.asyncio
async def test_import_crash_answers_guidance_not_the_exception(monkeypatch, tmp_path):
    """A crash inside the apply leg answers the registered 500 whose message carries
    the way forward — the raising exception's text must not appear anywhere in it."""
    import personalclaw.portability as portability

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(portability, "validate_import_zip", lambda p: (True, "", {"version": 3}))

    def _boom(*a, **k):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(portability, "apply_import_zip", _boom)

    client = TestClient(TestServer(_durability_app()))
    await client.start_server()
    try:
        resp = await client.post(
            "/api/durability/import?mode=merge",
            data={"file": io.BytesIO(b"zipbytes")},
        )
        body = await resp.json()
        assert resp.status == 500, f"got {resp.status}: {body}"
        assert body["error"]["code"] == "import_failed"
        assert "gateway log" in body["error"]["message"]
        assert SECRET not in json.dumps(body)
        assert body.get("ok") is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_restore_crash_is_a_500_failure_not_a_refused_400(monkeypatch, tmp_path):
    """restore_apply reports designed refusals as ok:false VALUES; a raise is a crash.
    The route must answer restore_failed 500 with guidance — not the old
    restore_refused 400 carrying the exception's own words."""
    import personalclaw.snapshot as snap_mod

    archives = tmp_path / "snaps"
    archives.mkdir()
    (archives / "snap-1.tar.gz").write_bytes(b"")
    monkeypatch.setattr(snap_mod, "_default_snapshot_dir", lambda: str(archives))

    def _boom(*a, **k):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(snap_mod, "restore_apply", _boom)

    client = TestClient(TestServer(_durability_app()))
    await client.start_server()
    try:
        resp = await client.post(
            "/api/durability/archive/snap-1.tar.gz/restore",
            json={"mode": "merge", "confirm": True},
        )
        body = await resp.json()
        assert resp.status == 500, f"expected crash->500, got {resp.status}: {body}"
        assert body["error"]["code"] == "restore_failed"
        assert "gateway log" in body["error"]["message"]
        assert SECRET not in json.dumps(body)
    finally:
        await client.close()


def test_merge_tags_validates_into_before_the_store():
    """int() internals ("invalid literal for int() …") are not user copy: the handler
    validates the field itself, so the except reaching the wire is ValueError-only —
    the store's authored refusal texts — and TypeError can no longer surface."""
    import inspect

    from personalclaw.dashboard.handlers import knowledge as mod

    src = inspect.getsource(mod.merge_tag)
    assert (
        "except (TypeError, ValueError) as exc" not in src
    ), "the tuple-except is back — int() failures would surface Python internals again"
    assert 'into = int(body["into"])' in src and "except ValueError as exc" in src
