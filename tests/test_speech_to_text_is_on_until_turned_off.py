"""A missing ``enabled`` means one thing, in the gateway and in Settings → Speech & Transcription.

🔴 Before: the gateway read a missing speech-to-text ``enabled`` as on (``.get("enabled", True)``,
four times in ``transcribe.py`` and once in the doctor) while the Voice panel read it as off
(``Boolean(settings.enabled)`` over the settings the gateway served, which carried no
``enabled``). So on an install that never touched the switch, the panel showed voice input off
while the microphone transcribed. The settings the gateway serves now carry the default, and
every gateway reader takes it from the same table.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


async def _served(use_case: str) -> dict:
    from personalclaw.providers.instance_routes import handle_get_use_case_settings

    app = web.Application()
    app.router.add_get("/api/models/use-cases/{use_case}/settings", handle_get_use_case_settings)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/models/use-cases/{use_case}/settings")
        assert resp.status == 200
        return (await resp.json())["settings"]


@pytest.mark.asyncio
async def test_settings_never_saved_are_served_with_the_default_the_gateway_uses(home):
    """What the panel's switch shows is what the gateway serves."""
    assert await _served("stt") == {"enabled": True}
    assert await _served("tts") == {"enabled": False}


@pytest.mark.asyncio
async def test_saved_settings_that_leave_it_out_are_served_with_it(home):
    from personalclaw.providers.use_cases import save_use_case_settings

    save_use_case_settings("stt", {"language": "en"})
    assert await _served("stt") == {"enabled": True, "language": "en"}


@pytest.mark.asyncio
async def test_a_choice_that_was_saved_wins(home):
    from personalclaw.providers.use_cases import save_use_case_settings

    save_use_case_settings("stt", {"enabled": False})
    assert await _served("stt") == {"enabled": False}


def test_the_microphone_follows_the_served_default(home):
    """A bound model and no speech-to-text settings: on, as the panel now shows."""
    from personalclaw import transcribe

    bound = object()
    with patch("personalclaw.stt.registry.active_stt", return_value=(bound, "stt-v1")):
        assert transcribe._bound_provider() is bound


@pytest.mark.parametrize("use_case", ["stt", "tts"])
def test_every_reader_takes_the_default_from_one_table(home, use_case):
    """A reader handed settings with no ``enabled`` (a patched loader, an older file) reaches the
    same answer as the served default."""
    from personalclaw.providers.use_cases import load_use_case_settings, use_case_enabled

    served = load_use_case_settings(use_case)["enabled"]
    assert use_case_enabled(use_case, {}) is served
    assert use_case_enabled(use_case, {"enabled": not served}) is (not served)


def test_the_voice_output_settings_read_the_same_default(home):
    """``active_voice_params`` read ``enabled`` with its own ``False``; it takes the table's."""
    from personalclaw.providers.use_cases import save_use_case_settings
    from personalclaw.tts import registry as tts_registry

    with patch.object(tts_registry, "active_tts", return_value=(object(), "voice-1")):
        assert tts_registry.active_voice_params()["enabled"] is False
        save_use_case_settings("tts", {"enabled": True})
        assert tts_registry.active_voice_params()["enabled"] is True
    assert json.loads((home / "extensions" / "use_case_settings" / "tts.json").read_text()) == {
        "enabled": True
    }
