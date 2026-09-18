"""FS-4 — the App-path validation fixture (FEEDBACK-SIGNAL S2 T2.4, contract C3).

Proves the app boundary for feedback end to end with a real fixture app that
declares ``/api/feedback`` in ``permissions.api``:

* An app-scoped POST /api/feedback lands a record with ``source_app`` stamped
  **server-side** (never client-claimed) and its producer forced into the app
  namespace — ``producer_kind="app"``, ``producer_id="<app>:<producer>"`` — so an
  app can never impersonate a core producer (contract C3).
* Its TARGET is forced to ``app_judgment`` too, so an app cannot supersede the
  user's own verdict on a core target (#2784) — see
  ``TestAnAppCannotSupersedeACoreVerdict``, which drives BOTH doors.
* The in-process ``sdk.feedback.record_feedback`` path lands an equivalent record;
  core namespaces the producer, so the SDK caller passes a bare producer id.
* An app path the fixture did NOT declare is rejected 403 by the enforcement
  middleware before the handler runs.

T2.4 ships the enforcement wired; this fixture is the executable proof, matching
the plan header's note that the raw mechanics "remain unwired by design" beyond
the route + middleware that already exist.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
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

            # An app-scoped SDK caller passes a BARE producer id; core namespaces it. The
            # caller used to be told to pre-namespace, which was the rule living in two
            # places at once — see the module docstring and #2784.
            rec = sdk_fb.record_feedback(
                target_kind="app_judgment",
                target_id="sdk-1",
                verdict="down",
                reason="sdk path",
                producer_kind="app",
                producer_id="sdk-producer",
                source_app=FIXTURE_APP,
            )
            assert rec is not None
            assert rec.source_app == FIXTURE_APP
            assert rec.producer_kind == "app"
            assert rec.producer_id == f"{FIXTURE_APP}:sdk-producer"
            # Re-export identity: the SDK surface IS core's record_feedback.
            assert sdk_fb.record_feedback is fb.record_feedback
            fb._invalidate()


# ── the app boundary on the TARGET, across every door (#2784) ─────────────────


class TestAnAppCannotSupersedeACoreVerdict:
    """The half of the boundary that was unreachable, and the parity it has to hold.

    ``handlers/feedback.py`` used to force the target kind with
    ``"app_judgment" if target_kind not in fb.TARGET_KINDS else target_kind`` — thirty lines
    below a guard that had already 400'd every kind outside ``TARGET_KINDS``, so the
    condition was invariantly false and the line was a self-assignment. An installed app
    declaring ``/api/feedback`` could therefore POST ``target_kind:
    "inbox_classification"`` against a real inbox item id and, because the supersede index
    is keyed ``(target_kind, target_id)`` with last-write-wins, flip the verdict the USER's
    own 👎 had set on that item.

    Every case below drives an app claiming a CORE target kind, which no case in this file
    did before — they all passed ``app_judgment`` explicitly, so the record landed in the
    right place whether the forcing worked or not.

    **Two doors, asserted for parity, and the enumeration is closed by construction:** the
    HTTP route and ``sdk.feedback.record_feedback``. The last test proves the pair is
    exhaustive rather than merely the two that came to mind — every write goes through
    ``fb.record_feedback``, and the SDK name IS that function object.
    """

    CORE_KIND = "inbox_classification"
    CORE_ID = "inbox_item_7"

    @pytest.mark.asyncio
    async def test_the_http_door_lands_the_app_record_under_app_judgment(self, tmp_path):
        async with _client(tmp_path, api_scope=["/api/feedback"]) as c:
            # The user's own verdict on a core target, recorded first (no source_app).
            assert (
                fb.record_feedback(
                    target_kind=self.CORE_KIND,
                    target_id=self.CORE_ID,
                    verdict="down",
                    producer_kind="prompt",
                    producer_id="classifier",
                )
                is not None
            )

            resp = await c.post(
                "/api/feedback",
                json={
                    "target_kind": self.CORE_KIND,  # the app CLAIMS a core kind
                    "target_id": self.CORE_ID,  # against the user's real target
                    "verdict": "up",
                },
            )
            assert resp.status == 200

            # The user's 👎 survives — the app's record did not supersede it.
            core = fb.current_verdict(self.CORE_KIND, self.CORE_ID)
            assert core is not None
            assert core.verdict == "down", "an app flipped the user's verdict on a core target"
            assert core.source_app == ""

            # ...and the app's record is observable where it belongs.
            landed = fb.current_verdict("app_judgment", self.CORE_ID)
            assert landed is not None
            assert landed.verdict == "up"
            assert landed.source_app == FIXTURE_APP
            assert landed.producer_id == f"{FIXTURE_APP}:default"

    def test_the_sdk_door_lands_the_app_record_under_app_judgment(self, tmp_path):
        """Parity: the in-process door had NO target boundary at all before this."""
        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            fb._invalidate()
            from personalclaw.sdk import feedback as sdk_fb

            assert (
                fb.record_feedback(
                    target_kind=self.CORE_KIND,
                    target_id=self.CORE_ID,
                    verdict="down",
                    producer_kind="prompt",
                    producer_id="classifier",
                )
                is not None
            )
            rec = sdk_fb.record_feedback(
                target_kind=self.CORE_KIND,
                target_id=self.CORE_ID,
                verdict="up",
                source_app=FIXTURE_APP,
            )
            assert rec is not None
            assert rec.target_kind == "app_judgment"

            core = fb.current_verdict(self.CORE_KIND, self.CORE_ID)
            assert core is not None and core.verdict == "down"
            assert fb.current_verdict("app_judgment", self.CORE_ID).verdict == "up"
            fb._invalidate()

    def test_a_core_caller_is_untouched(self, tmp_path):
        """The vacuity floor: forcing must apply to app callers ONLY.

        A `record_feedback` that forced `app_judgment` unconditionally would satisfy both
        cases above and break every core surface, so the pass is only meaningful next to
        this.
        """
        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            fb._invalidate()
            rec = fb.record_feedback(
                target_kind=self.CORE_KIND,
                target_id="core-only",
                verdict="up",
                producer_kind="prompt",
                producer_id="classifier",
            )
            assert rec is not None
            assert rec.target_kind == self.CORE_KIND
            assert rec.producer_id == "classifier"  # NOT namespaced
            fb._invalidate()

    def test_an_app_typo_is_still_dropped_rather_than_laundered(self, tmp_path):
        """Forcing runs AFTER the vocabulary check, so the closed vocabulary still binds.

        Forcing first would turn any string an app sent into a valid `app_judgment` record,
        which trades one boundary hole for a different one.
        """
        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            fb._invalidate()
            assert (
                fb.record_feedback(
                    target_kind="inbox_clasification",  # typo, not in TARGET_KINDS
                    target_id="t",
                    verdict="up",
                    source_app=FIXTURE_APP,
                )
                is None
            )
            fb._invalidate()

    def test_record_feedback_is_the_only_door_so_the_two_cases_above_are_exhaustive(self):
        """Closes the enumeration: an enumerated rail cannot see its own blind spot.

        Both doors are asserted above. This is what makes "both" mean "all": the SDK export
        is core's own function object (not a wrapper that could drift), and no other module
        in the tree appends to the store.
        """
        import ast

        from personalclaw.sdk import feedback as sdk_fb

        assert sdk_fb.record_feedback is fb.record_feedback

        root = Path(fb.__file__).resolve().parents[1]
        writers: set[str] = set()
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "record_feedback" not in text and "feedback" not in path.name:
                continue
            for node in ast.walk(ast.parse(text)):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                name = getattr(target, "attr", None) or getattr(target, "id", None)
                if name in ("_append",) and path.name != "feedback.py":
                    writers.add(str(path.relative_to(root)))
        assert writers == set(), (
            "something outside personalclaw/feedback.py appends feedback records, so the "
            f"app boundary has a third door: {sorted(writers)}"
        )
