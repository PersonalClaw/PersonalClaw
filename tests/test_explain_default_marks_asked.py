"""The owner's Explanations default, once set, answers the question the first-run card asks.

Setting ``agent.explain`` through the config PATCH records ``explain_asked`` in the onboarding
state, so the card never asks what the owner already chose in Settings. Only the owner's own write
does: an app that may write the setting answers nothing for the owner, and a refused write or a
write of another setting records nothing either.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _asked() -> bool:
    from personalclaw.onboarding import load_onboarding_state

    return load_onboarding_state()["explain_asked"]


def _server(app_name: str = "") -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    @web.middleware
    async def as_caller(request, handler):
        if app_name:
            request["app"] = app_name
        return await handler(request)

    app = web.Application(middlewares=[as_caller])
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


@pytest.mark.asyncio
async def test_the_owners_explanations_default_records_the_question_as_asked(home):
    assert _asked() is False
    async with TestClient(TestServer(_server())) as client:
        r = await client.patch(
            "/api/config/personalclaw", json={"path": "agent.explain", "value": "step_by_step"}
        )
        assert r.status == 200, await r.text()
    assert _asked() is True


@pytest.mark.asyncio
async def test_a_refused_value_or_another_setting_records_nothing(home):
    async with TestClient(TestServer(_server())) as client:
        r = await client.patch(
            "/api/config/personalclaw", json={"path": "agent.explain", "value": "sideways"}
        )
        assert r.status == 400, await r.text()
        r = await client.patch(
            "/api/config/personalclaw", json={"path": "agent.reply_style", "value": "default"}
        )
        assert r.status == 200, await r.text()
    assert _asked() is False


@pytest.mark.asyncio
async def test_an_apps_write_answers_nothing_for_the_owner(home, monkeypatch):
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.handlers import core

    monkeypatch.setattr(core, "_app_config_fields", lambda name: ["agent.explain"])
    async with TestClient(TestServer(_server("some-app"))) as client:
        r = await client.patch(
            "/api/config/personalclaw", json={"path": "agent.explain", "value": "briefly"}
        )
        assert r.status == 200, await r.text()
    assert AppConfig.load().agent.explain == "briefly"
    assert _asked() is False


@pytest.mark.asyncio
async def test_a_store_that_cannot_record_it_does_not_fail_the_write(home, monkeypatch):
    from personalclaw import onboarding
    from personalclaw.config.loader import AppConfig

    def broken(patch):
        raise OSError("read-only")

    monkeypatch.setattr(onboarding, "merge_onboarding_state", broken)
    async with TestClient(TestServer(_server())) as client:
        r = await client.patch(
            "/api/config/personalclaw", json={"path": "agent.explain", "value": "with_background"}
        )
        assert r.status == 200, await r.text()
    assert AppConfig.load().agent.explain == "with_background"
