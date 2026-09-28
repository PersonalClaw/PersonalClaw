"""A selftest is green only on what came OUT — never on the call merely returning.

The doctor's model selftest read only ``None`` as a failure, so a chat model that answered with an
empty reply was reported as working. Its text-to-speech probe, and the Models page's, had the same
shape one level down: each hands the engine an output file it made itself, so "a path came back"
was already true before the engine ran, and an engine that wrote nothing read "synthesis returned
audio". The Models page's own fake engine did exactly that and its test called it ok.
"""

from __future__ import annotations

import tempfile

import pytest

from personalclaw.dashboard.handlers import doctor

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def system_temp(tmp_path, monkeypatch):
    """The system temp folder, as the probe sees it: this test's own."""
    folder = tmp_path / "system-temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


async def _chat_probe(monkeypatch, reply: str) -> dict:
    """The selftest's chat row, for a Background model that answers *reply*."""
    import personalclaw.llm_helpers as llm_helpers

    async def _one_shot(prompt, **kwargs):
        return reply

    async def _no_voice(_timed):
        return None

    monkeypatch.setattr(llm_helpers, "one_shot_completion", _one_shot)
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: uc == "chat"
    )
    monkeypatch.setattr(doctor, "_tts_clone_probe", _no_voice)
    return (await doctor._run_selftest("any"))["capabilities"]["chat"]


@pytest.mark.parametrize("reply", ["", "  \n"], ids=["empty", "whitespace"])
async def test_an_empty_reply_is_not_a_working_model(monkeypatch, reply):
    """🔴 Red before the fix: `{"ok": True, "detail": "completion returned"}`."""
    assert await _chat_probe(monkeypatch, reply) == {
        "ok": False,
        "detail": "the Background model answered with an empty reply",
    }


async def test_a_reply_with_something_in_it_is(monkeypatch):
    """The positive control: the same probe goes green on a real answer."""
    assert await _chat_probe(monkeypatch, "pong") == {
        "ok": True,
        "detail": "the Background model replied",
    }


class _Engine:
    supports_cloning = False

    def __init__(self, *, writes: bytes) -> None:
        self.writes = writes

    async def synthesize(self, text, *, output_path="", **opts):
        """Hands back the path it was given — having written *writes* into it."""
        with open(output_path, "wb") as fh:
            fh.write(self.writes)
        return output_path


async def _tts_probe(monkeypatch, engine: _Engine) -> dict | None:
    import personalclaw.tts.registry as reg

    async def _route(params, text, *, output_path=""):
        return await params["provider"].synthesize(text, output_path=output_path)

    async def _timed(coro, timeout: float = 15.0):
        return await coro

    monkeypatch.setattr(reg, "active_voice_params", lambda **kw: {"provider": engine})
    monkeypatch.setattr(reg, "route_synthesis", _route)
    return await doctor._tts_clone_probe(_timed)


async def test_an_engine_that_wrote_no_audio_is_not_green(monkeypatch, system_temp):
    """🔴 Red before the fix: `{"ok": True, "detail": "synthesis returned audio"}` for an empty
    file — the one the probe had made itself."""
    result = await _tts_probe(monkeypatch, _Engine(writes=b""))
    assert result == {"ok": False, "detail": "synthesize returned nothing", "cloning": False}
    assert list(system_temp.iterdir()) == [], "the probe still removes its clip"


async def test_an_engine_that_wrote_audio_is(monkeypatch):
    result = await _tts_probe(monkeypatch, _Engine(writes=b"RIFF\x24\x00\x00\x00WAVE"))
    assert result == {"ok": True, "detail": "synthesis returned audio", "cloning": False}
