"""A voice setting changed while a chat is open reaches that chat.

The chat reads its voice settings once and keeps them: the hands-free phrases and mute under
``voice.*``, and text-to-speech's "Speak replies aloud". So the gateway tells every open page when
one changes, with a ``refresh`` frame naming ``voice``, whoever changed it: Settings, a routing
lever, or a channel app saving through the SDK. Without that frame a chat open in another tab
kept reading replies aloud after the setting was switched off, and did not start after it was
switched on.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.providers import use_cases


class TestASavedUseCaseSettingIsAnnounced:
    def test_every_listener_is_told_which_use_case_was_saved(self):
        heard: list[str] = []

        def listener(use_case: str) -> None:
            heard.append(use_case)

        use_cases.subscribe_settings_saved(listener)
        use_cases.subscribe_settings_saved(listener)  # idempotent: told once, not twice
        try:
            use_cases.save_use_case_settings("tts", {"enabled": True, "auto_speak": False})
            use_cases.save_use_case_settings("stt", {"enabled": True})
        finally:
            use_cases.unsubscribe_settings_saved(listener)
        assert heard == ["tts", "stt"]
        assert use_cases.load_use_case_settings("tts")["auto_speak"] is False

    def test_a_listener_that_fails_neither_undoes_the_save_nor_silences_the_rest(self):
        heard: list[str] = []

        def broken(_use_case: str) -> None:
            raise RuntimeError("the page went away")

        def listener(use_case: str) -> None:
            heard.append(use_case)

        use_cases.subscribe_settings_saved(broken)
        use_cases.subscribe_settings_saved(listener)
        try:
            use_cases.save_use_case_settings("tts", {"enabled": True, "auto_speak": True})
        finally:
            use_cases.unsubscribe_settings_saved(broken)
            use_cases.unsubscribe_settings_saved(listener)
        assert heard == ["tts"]
        assert use_cases.load_use_case_settings("tts")["auto_speak"] is True

    def test_a_channel_apps_save_is_the_announced_one(self):
        """The Slack voice modal switches "Speak replies aloud" through the SDK's save."""
        from personalclaw.sdk.channel import save_use_case_settings

        assert save_use_case_settings is use_cases.save_use_case_settings


def _voice_relay(app: web.Application):
    """The dashboard's own start and stop hooks for the relay, run alone: the rest of the
    gateway's hooks start real subsystems."""
    from personalclaw.dashboard.lifecycle_hooks import register_lifecycle_hooks

    register_lifecycle_hooks(app)
    start = next(h for h in app.on_startup if h.__name__ == "_voice_settings_relay_startup")
    stop = next(h for h in app.on_cleanup if h.__name__ == "_voice_settings_relay_shutdown")
    return start, stop


class TestTheGatewayTellsTheOpenPages:
    @pytest.mark.asyncio
    async def test_a_text_to_speech_save_sends_the_voice_refresh_until_the_gateway_stops(self):
        app = web.Application()
        app["state"] = MagicMock()
        start, stop = _voice_relay(app)
        await start(app)
        try:
            use_cases.save_use_case_settings("tts", {"enabled": True, "auto_speak": False})
            app["state"].push_refresh.assert_called_once_with("voice")
            app["state"].push_refresh.reset_mock()
            # Speech-to-text's settings are not read by the chat's voice settings.
            use_cases.save_use_case_settings("stt", {"enabled": True})
            app["state"].push_refresh.assert_not_called()
        finally:
            await stop(app)
        # A stopped gateway's pages are not told; a later gateway in the process subscribes anew.
        use_cases.save_use_case_settings("tts", {"enabled": True, "auto_speak": True})
        app["state"].push_refresh.assert_not_called()


def _config_app(state: MagicMock) -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app["state"] = state
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


class TestAVoiceConfigEditTellsTheOpenPages:
    @pytest.mark.asyncio
    async def test_a_hands_free_edit_sends_the_voice_refresh_and_nothing_else_does(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({}), encoding="utf-8")
        state = MagicMock()
        with patch("personalclaw.config.loader.config_path", return_value=cfg):
            async with TestClient(TestServer(_config_app(state))) as c:
                resp = await c.patch(
                    "/api/config/personalclaw",
                    json={"path": "voice.exit_phrases", "add": "stop listening"},
                )
                assert resp.status == 200
                state.push_refresh.assert_called_once_with("voice")
                state.push_refresh.reset_mock()

                resp = await c.patch(
                    "/api/config/personalclaw",
                    json={"path": "voice.duplex_mute_enabled", "value": False},
                )
                assert resp.status == 200
                state.push_refresh.assert_called_once_with("voice")
                state.push_refresh.reset_mock()

                # A refused write changed nothing, so it tells nobody.
                resp = await c.patch(
                    "/api/config/personalclaw",
                    json={"path": "voice.exit_phrases", "value": ["stop"], "add": "halt"},
                )
                assert resp.status == 400
                # Nor does a setting the chat's voice settings do not hold.
                resp = await c.patch(
                    "/api/config/personalclaw",
                    json={"path": "dashboard.screen_share_enabled", "value": False},
                )
                assert resp.status == 200
                state.push_refresh.assert_not_called()
        stored = json.loads(cfg.read_text(encoding="utf-8"))
        assert "stop listening" in stored["voice"]["exit_phrases"]
