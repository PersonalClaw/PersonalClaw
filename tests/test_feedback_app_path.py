"""FS-4 — the App-path validation fixture (FEEDBACK-SIGNAL S2 T2.4, contract C3).

Proves the app boundary for feedback end to end with a real fixture app that
declares ``/api/feedback`` in ``permissions.api``:

* An app-scoped POST /api/feedback lands a record with ``source_app`` stamped
  **server-side** (never client-claimed) and its producer forced into the app
  namespace — ``producer_kind="app"``, ``producer_id="<app>:<producer>"`` — so an
  app can never impersonate a core producer (contract C3).
* The in-process ``sdk.feedback.record_feedback`` path lands an equivalent record
  (the SDK caller namespaces its own producer, per the SDK contract).
* An app path the fixture did NOT declare is rejected 403 by the enforcement
  middleware before the handler runs.

T2.4 ships the enforcement wired; this fixture is the executable proof, matching
the plan header's note that the raw mechanics "remain unwired by design" beyond
the route + middleware that already exist.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import feedback as fb
from personalclaw.apps import manager
from personalclaw.apps.permissions import APP_SCOPED_PREFIXES, app_request_denial
from personalclaw.dashboard.handlers.feedback import api_feedback_record

FIXTURE_APP = "feedback-fixture"


def _write_fixture_app(tmp_path, *, api_scope: list[str]) -> None:
    """Install a minimal fixture app declaring the given ``permissions.api`` scope."""
    appdir = tmp_path / "apps" / FIXTURE_APP
    appdir.mkdir(parents=True)
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": FIXTURE_APP,
                "version": "1.0.0",
                "displayName": "Feedback Fixture",
                "description": "Declares /api/feedback to exercise the app boundary.",
                "permissions": {"api": api_scope},
            }
        ),
        encoding="utf-8",
    )
    (appdir / "installed.json").write_text(
        # No `installed.json` means NOT installed (`_read_installed`), which the boundary
        # now refuses — a fixture without one models a partial install.
        json.dumps({"name": FIXTURE_APP, "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )


@asynccontextmanager
async def _client(tmp_path, *, api_scope: list[str]):
    """A TestServer mounting the REAL feedback route behind the REAL enforcement
    middleware, with a stub that sets ``request['app']`` to the fixture app (as an
    app-scoped token would). config_dir is patched to tmp_path so both the app
    manifest and the feedback.jsonl land under isolation."""
    _write_fixture_app(tmp_path, api_scope=api_scope)

    @web.middleware
    async def stub_identity(request, handler):
        request["app"] = FIXTURE_APP
        return await handler(request)

    # Mirror server.py's app_permission_middleware (the enforcement half).
    @web.middleware
    async def app_permission_middleware(request, handler):
        # Calls the REAL decision (`permissions.app_request_denial`) rather than
        # re-deriving it. The old copy inlined `if c is not None and ...`, which is the
        # fail-open shape the boundary itself had: a mirror that reproduces the bug it is
        # meant to catch. Only the logging/response half is local, as in `server.py`.
        app_name = request.get("app", "")
        if app_name and request.path.startswith(APP_SCOPED_PREFIXES):
            if app_request_denial(app_name, request.path):
                raise web.HTTPForbidden(text="denied")
        return await handler(request)

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        fb._invalidate()  # drop any cross-test index cached against a prior config_dir

        async def _ok(request: web.Request) -> web.Response:
            return web.json_response({"ok": True})

        app = web.Application(middlewares=[stub_identity, app_permission_middleware])
        app.router.add_post("/api/feedback", api_feedback_record)
        # An app path the fixture never declares — used for the 403 case.
        app.router.add_get("/api/secrets", _ok)
        app.router.add_post("/api/secrets", _ok)
        async with TestClient(TestServer(app)) as client:
            try:
                yield client
            finally:
                fb._invalidate()


@pytest.mark.asyncio
async def test_declared_app_path_stamps_source_app_and_forces_producer(tmp_path):
    """A declared /api/feedback POST records with source_app set and the producer
    forced to app:<name>:<producer> (contract C3)."""
    async with _client(tmp_path, api_scope=["/api/feedback"]) as c:
        resp = await c.post(
            "/api/feedback",
            json={
                "target_kind": "app_judgment",
                "target_id": "widget-42",
                "verdict": "down",
                "reason": "wrong answer",
                # The app tries to CLAIM a core producer; the server must override it.
                "producer_kind": "prompt",
                "producer_id": "classifier",
            },
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True

        rec = fb.current_verdict("app_judgment", "widget-42")
        assert rec is not None
        assert rec.verdict == "down"
        # source_app is stamped server-side from request["app"] — not the body.
        assert rec.source_app == FIXTURE_APP
        # Producer forced into the app namespace; the claimed "prompt" is discarded.
        assert rec.producer_kind == "app"
        assert rec.producer_id == f"{FIXTURE_APP}:classifier"


@pytest.mark.asyncio
async def test_app_producer_defaults_when_unspecified(tmp_path):
    """An app that omits producer_id still gets a namespaced producer (…:default)."""
    async with _client(tmp_path, api_scope=["/api/feedback"]) as c:
        resp = await c.post(
            "/api/feedback",
            json={"target_kind": "app_judgment", "target_id": "t1", "verdict": "up"},
        )
        assert resp.status == 200
        rec = fb.current_verdict("app_judgment", "t1")
        assert rec is not None
        assert rec.producer_kind == "app"
        assert rec.producer_id == f"{FIXTURE_APP}:default"


@pytest.mark.asyncio
async def test_undeclared_app_path_is_forbidden(tmp_path):
    """An app path outside the declared permissions.api scope 403s before the handler."""
    async with _client(tmp_path, api_scope=["/api/feedback"]) as c:
        assert (await c.get("/api/secrets")).status == 403
        # And a feedback scope alone does NOT admit an unrelated path.
        assert (await c.post("/api/secrets", json={})).status in (403, 405)


class TestSdkInProcessPath:
    """The sdk/feedback in-process path lands an equivalent app-namespaced record."""

    def test_sdk_record_feedback_lands_app_record(self, tmp_path):
        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            fb._invalidate()
            from personalclaw.sdk import feedback as sdk_fb

            # SDK contract: an in-process app caller namespaces its own producer.
            rec = sdk_fb.record_feedback(
                target_kind="app_judgment",
                target_id="sdk-1",
                verdict="down",
                reason="sdk path",
                producer_kind="app",
                producer_id=f"{FIXTURE_APP}:sdk-producer",
                source_app=FIXTURE_APP,
            )
            assert rec is not None
            assert rec.source_app == FIXTURE_APP
            assert rec.producer_kind == "app"
            assert rec.producer_id == f"{FIXTURE_APP}:sdk-producer"
            # Re-export identity: the SDK surface IS core's record_feedback.
            assert sdk_fb.record_feedback is fb.record_feedback
            fb._invalidate()
