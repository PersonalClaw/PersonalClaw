"""ffmpeg is found where PersonalClaw says it looks, and finding it changes nothing else.

🔴 Before: the first transcription put the folder holding an ffmpeg (``~/.local/bin``,
``/opt/homebrew/bin``, ``/usr/local/bin``) at the front of the gateway's own ``PATH``, even when
ffmpeg was already on it. That ``PATH`` is every child's, so each server, hook and script started
after it resolved its programs from that folder too: a stdio tool server whose command was ``uvx``
ran a ``uvx`` the owner had never put on their ``PATH``.

Now ffmpeg is looked up, as an absolute path, on the ``PATH`` the gateway started with and then in
those folders, and that path is handed to what runs it. Where none is found, the place the owner
acts says so: the Speech settings, a video whose sound could not be read, and a long recording
that could not be cut into parts.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.stt.provider import SttError, TranscriptResult


def _ffmpeg(folder: Path) -> Path:
    """A stand-in ``ffmpeg`` that runs nothing."""
    folder.mkdir(parents=True, exist_ok=True)
    tool = folder / "ffmpeg"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)
    return tool


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A home of the test's own and a ``PATH`` of one empty folder."""
    home = tmp_path / "home"
    home.mkdir()
    on_path = tmp_path / "bin"
    on_path.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(on_path))
    return tmp_path


def _bound(provider):
    """*provider* bound for speech-to-text, which is on."""
    return (
        patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": True},
        ),
        patch("personalclaw.security.is_sensitive_path", return_value=False),
        patch("personalclaw.stt.registry.active_stt", return_value=(provider, "stt-v1")),
    )


def _provider(**kw):
    prov = MagicMock()
    prov.is_available = AsyncMock(return_value=True)
    prov.transcribe = AsyncMock(**kw)
    prov.transcribe_detailed = AsyncMock(return_value=TranscriptResult(text="hello"))
    return prov


@pytest.mark.asyncio
async def test_a_transcription_leaves_the_process_path_as_it_was(scratch):
    from personalclaw.transcribe import is_available, transcribe_audio, transcribe_audio_detailed

    _ffmpeg(scratch / "home" / ".local" / "bin")
    clip = scratch / "clip.wav"
    clip.write_bytes(b"RIFF" + b"\0" * 40)
    before = os.environ["PATH"]

    a, b, c = _bound(_provider(return_value="hello"))
    with a, b, c:
        assert await is_available()
        assert await transcribe_audio(str(clip)) == "hello"
        assert (await transcribe_audio_detailed(str(clip))).text == "hello"

    assert os.environ["PATH"] == before


def test_ffmpeg_on_the_startup_path_comes_first_then_the_named_folders(scratch, monkeypatch):
    from personalclaw import ffmpeg_binary

    fallback = scratch / "packages" / "bin"
    monkeypatch.setattr(ffmpeg_binary, "FALLBACK_DIRS", (str(fallback),))
    on_path = _ffmpeg(scratch / "bin")
    _ffmpeg(fallback)

    assert ffmpeg_binary.find_ffmpeg() == str(on_path)
    on_path.unlink()
    assert ffmpeg_binary.find_ffmpeg() == str(fallback / "ffmpeg")
    (fallback / "ffmpeg").unlink()
    assert ffmpeg_binary.find_ffmpeg() is None
    assert os.environ["PATH"] == str(scratch / "bin")


def test_a_folder_named_in_the_home_is_read_from_the_home_of_the_moment(scratch, monkeypatch):
    """``~/.local/bin`` is the home the process has when it looks, not the one it imported with."""
    from personalclaw import ffmpeg_binary

    monkeypatch.setattr(ffmpeg_binary, "FALLBACK_DIRS", ("~/.local/bin",))
    tool = _ffmpeg(scratch / "home" / ".local" / "bin")

    assert ffmpeg_binary.find_ffmpeg() == str(tool)


def test_a_relative_path_entry_and_a_file_that_cannot_run_are_not_ffmpeg(scratch, monkeypatch):
    from personalclaw import ffmpeg_binary

    monkeypatch.setattr(ffmpeg_binary, "FALLBACK_DIRS", ())
    _ffmpeg(scratch / "cwd")
    monkeypatch.chdir(scratch / "cwd")
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", str(scratch / "bin")]))
    unrunnable = scratch / "bin" / "ffmpeg"
    unrunnable.write_text("not a program", encoding="utf-8")
    unrunnable.chmod(0o644)

    assert ffmpeg_binary.find_ffmpeg() is None


@pytest.mark.asyncio
async def test_the_speech_settings_say_where_ffmpeg_is(scratch, monkeypatch):
    from personalclaw import ffmpeg_binary
    from personalclaw.stt.handlers import register_stt_routes

    monkeypatch.setattr(ffmpeg_binary, "FALLBACK_DIRS", ())
    tool = _ffmpeg(scratch / "bin")
    app = web.Application()
    register_stt_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/stt/ffmpeg")
        assert resp.status == 200
        assert await resp.json() == {"path": str(tool), "message": ""}


@pytest.mark.asyncio
async def test_the_speech_settings_say_in_plain_words_that_ffmpeg_is_missing(scratch, monkeypatch):
    from personalclaw import ffmpeg_binary
    from personalclaw.stt.handlers import register_stt_routes

    monkeypatch.setattr(ffmpeg_binary, "FALLBACK_DIRS", ("~/.local/bin", "/nonexistent/pc-bin"))
    app = web.Application()
    register_stt_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/stt/ffmpeg")
        body = await resp.json()

    assert resp.status == 200
    assert body["path"] is None
    assert body["message"] == ffmpeg_binary.ffmpeg_not_found()
    # Where it looked, and what to do, in the words the owner reads.
    assert body["message"].startswith(
        "ffmpeg isn't installed where PersonalClaw looks for it: the folders on the PATH the "
        "gateway started with, then ~/.local/bin and /nonexistent/pc-bin. Install it with "
    )
    assert body["message"].endswith(
        "and try again, or start the gateway with the folder that holds it on its PATH."
    )


@pytest.mark.asyncio
async def test_a_long_recording_that_could_not_be_cut_into_parts_says_why(scratch, monkeypatch):
    """The provider's own words stay first; the sentence after them says the recording was sent
    whole because ffmpeg, which cuts it into parts, is not there."""
    from personalclaw import ffmpeg_binary
    from personalclaw.transcribe import transcribe_audio

    monkeypatch.setattr(ffmpeg_binary, "FALLBACK_DIRS", ())
    monkeypatch.setenv("PERSONALCLAW_STT_SEGMENT_THRESHOLD", "10")
    big = scratch / "long.wav"
    big.write_bytes(b"\0" * 4096)
    refused = "This file is larger than the service accepts."

    a, b, c = _bound(_provider(side_effect=SttError(refused)))
    with a, b, c, pytest.raises(SttError) as raised:
        await transcribe_audio(str(big))

    assert str(raised.value) == (
        f"{refused} A recording this long is cut into parts with ffmpeg before it is transcribed, "
        f"and this one was sent whole. {ffmpeg_binary.ffmpeg_not_found()}"
    )


@pytest.mark.asyncio
async def test_a_short_recording_never_mentions_ffmpeg(scratch, monkeypatch):
    """The control: a recording sent whole because it is short fails in its provider's words."""
    from personalclaw import ffmpeg_binary
    from personalclaw.transcribe import transcribe_audio

    monkeypatch.setattr(ffmpeg_binary, "FALLBACK_DIRS", ())
    short = scratch / "short.wav"
    short.write_bytes(b"\0" * 64)
    a, b, c = _bound(_provider(side_effect=SttError("The service is busy.")))
    with a, b, c, pytest.raises(SttError) as raised:
        await transcribe_audio(str(short))

    assert str(raised.value) == "The service is busy."


@pytest.mark.asyncio
async def test_a_video_whose_sound_could_not_be_read_says_ffmpeg_is_missing(scratch, monkeypatch):
    from personalclaw import ffmpeg_binary
    from personalclaw.knowledge.pipeline.nodes.media_nodes import AvSplitNode

    monkeypatch.setattr(ffmpeg_binary, "FALLBACK_DIRS", ())
    video = scratch / "talk.mp4"
    video.write_bytes(b"\0" * 64)
    ctx = MagicMock(file_path=str(video), work_dir=str(scratch), item_id="item-1")

    out = await AvSplitNode().run({}, ctx)

    assert (out.success, out.error) == (False, ffmpeg_binary.ffmpeg_not_found())


def test_an_app_is_handed_the_same_lookup_and_the_same_words_through_the_sdk():
    """A speech or diarization app finds ffmpeg where core does, and says it is missing in the
    same words: the SDK hands it core's own functions, not a copy that could drift."""
    from personalclaw import ffmpeg_binary
    from personalclaw.sdk.diarization import ffmpeg_not_found as diarization_words
    from personalclaw.sdk.diarization import find_ffmpeg as diarization_find
    from personalclaw.sdk.stt import ffmpeg_not_found as stt_words
    from personalclaw.sdk.stt import find_ffmpeg as stt_find

    assert stt_find is diarization_find is ffmpeg_binary.find_ffmpeg
    assert stt_words is diarization_words is ffmpeg_binary.ffmpeg_not_found
