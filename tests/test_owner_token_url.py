"""The owner-token link keeps the deep link and leaves the address bar (day-56b `s26`).

Measured on a fresh instance: opening ``#/chat/<key>`` without a session showed the Connect
gate; pasting the token navigated to ``/?token=<tok>`` — the route gone, because the gate built
``origin + '?token='`` — and the SPA then sat at ``/?token=<tok>#/dashboard``, the owner token in
the address bar and the history entry for the rest of the visit.

The browser half is ONE script, ``dashboard/owner_token_url.js`` (its behaviour is executed in
``web/src/app/ownerTokenUrl.test.ts``). These tests pin where the gateway puts it — the three
documents that stand between a person and the dashboard — and that a wheel carries it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from personalclaw.dashboard import owner_token_url
from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

_REPO = Path(__file__).resolve().parent.parent
_INDEX = "<!doctype html><html><head><title>PersonalClaw</title></head><body></body></html>"


def _request(path: str = "/", query: dict | None = None) -> MagicMock:
    req = MagicMock(spec=web.Request)
    req.path = path
    req.query = query or {}
    req.cookies = {}
    req.remote = "127.0.0.1"
    req.headers = {}
    req.method = "GET"
    return req


@pytest.fixture
def dist(tmp_path, monkeypatch):
    import personalclaw.dashboard.handlers.core as core

    (tmp_path / "index.html").write_text(_INDEX, encoding="utf-8")
    monkeypatch.setattr(core, "_DIST_DIR", tmp_path)
    return core


class TestTheSpaDocument:
    @pytest.mark.asyncio
    async def test_a_token_link_scrubs_itself_before_anything_else_in_head(self, dist):
        resp = await dist.index(_request(query={"token": "OWNER-SECRET"}))
        html = resp.text
        # FIRST in <head>: the address bar is cleaned before the parser reaches a single
        # resource declaration, and whether or not the app bundle then loads.
        assert html.startswith(f"<!doctype html><html><head><script {owner_token_url.SCRUB_ATTR}>")
        assert "PersonalClawOwnerToken" in html
        assert html.count(f"<script {owner_token_url.SCRUB_ATTR}>") == 1

    @pytest.mark.asyncio
    async def test_a_plain_load_is_served_unchanged(self, dist):
        resp = await dist.index(_request())
        assert resp.text == _INDEX

    def test_injection_is_idempotent_and_leaves_a_headless_document_alone(self):
        once = owner_token_url.inject_scrub(_INDEX)
        assert owner_token_url.inject_scrub(once) == once
        assert owner_token_url.inject_scrub("<html></html>") == "<html></html>"


class TestTheConnectGate:
    @pytest.mark.asyncio
    async def test_the_gate_keeps_the_route_and_scrubs_a_refused_token(self):
        async def _never(request):  # pragma: no cover — the gate answers first
            raise AssertionError("an unauthenticated page request reached the handler")

        resp = await token_auth_middleware()(_request(path="/"), _never)
        assert resp.status == 403
        html = resp.text
        assert "403 — Token required" in html
        # The target is built by the shared helper (origin + token + validated route)...
        assert "PersonalClawOwnerToken.connectUrl(v)" in html
        assert f"<script {owner_token_url.SCRUB_ATTR}>" in html
        # ...and the old construction that dropped the route is gone.
        assert "window.location.host+'?token='" not in html

    @pytest.mark.asyncio
    async def test_an_invalid_token_link_gets_the_same_gate(self):
        async def _never(request):  # pragma: no cover
            raise AssertionError("a refused token reached the handler")

        forged = generate_token("u", ttl_seconds=60)[:-4] + "AAAA"
        resp = await token_auth_middleware()(_request(query={"token": forged}), _never)
        assert resp.status == 403
        assert f"<script {owner_token_url.SCRUB_ATTR}>" in resp.text


class TestTheSignInPage:
    def test_a_successful_sign_in_lands_on_the_opened_route(self):
        from personalclaw.dashboard.handlers.auth import _LOGIN_HTML

        assert "PersonalClawOwnerToken.home()" in _LOGIN_HTML
        assert "window.location.href = '/'" not in _LOGIN_HTML
        # The helper is defined in the same document, ahead of the code that calls it.
        assert _LOGIN_HTML.index("root.PersonalClawOwnerToken") < _LOGIN_HTML.index(
            "PersonalClawOwnerToken.home()"
        )


def test_the_wheel_carries_the_script():
    """Read at import (a missing file fails the gateway at start), so it must be package data:
    a wheel built without the line would not boot — the shipped-artifact failure a source tree
    cannot show."""
    data = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    declared = data["tool"]["setuptools"]["package-data"]["personalclaw"]
    assert "dashboard/owner_token_url.js" in declared
    assert (_REPO / "src" / "personalclaw" / "dashboard" / "owner_token_url.js").is_file()
