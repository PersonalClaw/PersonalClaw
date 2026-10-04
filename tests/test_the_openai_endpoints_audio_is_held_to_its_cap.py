"""The OpenAI-compatible endpoint's speech and transcription are held to the endpoint's cap.

A client's chat turn on ``/v1/chat/completions`` runs as that client's work: nobody watches it, so
the daily dollar cap holds it, the run ceiling an inbound turn is given holds it too, and what it
spends is counted against both. The endpoint's speech and transcription routes ran as no one's work.
The speech a client asked for, and the minutes it had transcribed, were judged as your own, which no
cap holds, and counted against nothing.

Now each runs as the client's work under the endpoint's scope and ceilings, as its chat turns do:
what it costs is counted where they count theirs, and a request past the cap is refused before
anything is spoken or transcribed, in the cap's own words. A key a client sends in
``X-Session-Key`` changes none of that: a client of this endpoint is signed in by the route itself,
and what it names there is not its work.

Each test drives the route through a real server, behind the middleware that makes a request the
work of whoever made it, with a client registered as Settings registers one.
"""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.memory_write_gate import memory_write_middleware
from personalclaw.guardrails import budgets as budgets_mod
from personalclaw.guardrails.budgets import get_meter
from personalclaw.inbound import openai_dialect as dialect
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import set_rate

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")

#: What the client asks to hear: 13 characters, so $1.30 at the price set for the voice below.
SAID = "Good evening."
#: Ten cents a character, written as the price per million characters "Set a price" takes.
PER_MCHAR = 100_000.0
#: The day's cap for work nobody watches: room for one request's speech, not two.
CAP = 2.0
#: One of your chats, as a client might name it.
CHAT = "dashboard:chat-3-1767225600"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A private home per test, no surface token leaking between tests, and a fresh meter."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    rates_mod._overlay_cache = None
    budgets_mod.reset_meter()
    yield tmp_path
    rates_mod._overlay_cache = None
    budgets_mod.reset_meter()
    for surface in _SURFACES:
        os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


def _enable(monkeypatch, *, cap: float = CAP):
    """The endpoint switched on with its token minted, and the day's dollar cap for work nobody
    watches set."""
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AgentConfig, AppConfig
    from personalclaw.inbound import auth

    auth.create_surface_token(dialect.OPENAI_SURFACE)
    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(
        enabled=True, openai=Surface(enabled=True, allow_remote=False)
    )
    cfg.agents = {"researcher": AgentConfig()}
    cfg.default_agent = "researcher"
    cfg.guardrails.budgets.max_dollars_per_day = cap
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    return cfg


def _registered_client() -> tuple[str, str]:
    """``(client_id, token)`` of a client registered for the endpoint."""
    from personalclaw.inbound.clients import create_client

    client, token = create_client("Kitchen speaker", surfaces=[dialect.OPENAI_SURFACE])
    return client.client_id, token


def _usage_rows(home: Path) -> list[dict]:
    path = home / "usage" / "turns.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _audited(home: Path, route: str) -> list[tuple[int, str]]:
    """``(status, refused)`` of each request to *route* the endpoint's audit log recorded."""
    path = home / "inbound_audit.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [(r["status"], r.get("refused_reason", "")) for r in rows if r["route"] == route]


async def _server() -> TestClient:
    """The endpoint's routes behind the middleware that makes a request the work of whoever made
    it, as the gateway mounts them."""

    async def _no_turns(*_a, **_k):
        raise AssertionError("no chat turn is asked for here")

    app = web.Application(middlewares=[memory_write_middleware()])
    app["state"] = None
    dialect.register_routes(app, turn_runner=_no_turns, agent_mover=_no_turns)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── speech ──────────────────────────────────────────────────────────────────────────────────


class _Voice:
    """A text-to-speech engine that can speak, records what it is asked to say, and the spend scope
    and ceiling each sentence was asked under."""

    name = "studio-voice"
    display_name = "Studio voice"

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.said: list[str] = []
        self.scopes: list[tuple[str, object]] = []

    async def can_synthesize(self, voice: str = "") -> bool:
        return True

    async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
        from personalclaw.guardrails.budgets import current_run_budget, current_run_key

        self.said.append(text)
        self.scopes.append((current_run_key(), current_run_budget()))
        path = self.folder / f"said-{len(self.said)}.wav"
        with wave.open(str(path), "wb") as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(8000)
            clip.writeframes(b"\x00\x00" * 800)
        return str(path)


def _bind(monkeypatch, voice: _Voice) -> None:
    """*voice* as the bound text-to-speech model, priced as the owner priced it."""
    set_rate("studio-voice:narrator-2", {"unit": "character", "per_mchar": PER_MCHAR})
    monkeypatch.setattr(
        dialect,
        "resolve_voice",
        lambda name="", **kw: {
            "provider": voice,
            "voice": "narrator-2",
            "speed": 1.0,
            "speech_voice": "",
        },
    )


async def _speech(client: TestClient, token: str, **headers: str):
    response = await client.post(
        dialect.ROUTE_SPEECH,
        data=json.dumps({"model": "tts-1", "input": SAID}),
        headers={"Authorization": f"Bearer {token}", **headers},
    )
    return response.status, await response.read()


@pytest.mark.asyncio
async def test_speech_a_client_asks_for_counts_against_the_cap_and_is_refused_past_it(
    home, monkeypatch
):
    _enable(monkeypatch)
    client_id, token = _registered_client()
    voice = _Voice(home)
    _bind(monkeypatch, voice)
    server = await _server()
    try:
        first = await _speech(server, token)
        second = await _speech(server, token)
    finally:
        await server.close()

    assert first[0] == 200, first[1]
    assert first[1].startswith(b"RIFF")
    # Counted against the day, as a chat turn of the client's is.
    assert get_meter().day_totals().dollars == pytest.approx(1.30)
    status, body = second
    refused = json.loads(body)["error"]
    assert status == 503, body
    assert refused["code"] == "tts_spend_refused"
    assert "daily" in refused["message"] and "$2.00" in refused["message"], refused
    # Refused before it was spoken: the voice was asked once, and nothing more was counted.
    assert voice.said == [SAID]
    assert get_meter().day_totals().dollars == pytest.approx(1.30)
    (row,) = _usage_rows(home)
    assert (row["source"], row["unit"], row["quantity"]) == ("background", "character", 13)
    assert row["session_key"].startswith(f"inbound:{client_id}")
    assert _audited(home, dialect.ROUTE_SPEECH) == [(200, ""), (503, "spend cap")]


@pytest.mark.asyncio
async def test_speech_runs_under_the_scope_and_ceiling_of_the_clients_chat_turns(home, monkeypatch):
    from personalclaw.guardrails.budgets import safety_budget_for_inbound

    _enable(monkeypatch)
    client_id, token = _registered_client()
    voice = _Voice(home)
    _bind(monkeypatch, voice)
    server = await _server()
    try:
        status, body = await _speech(server, token)
    finally:
        await server.close()

    assert status == 200, body
    assert voice.scopes == [(client_id, safety_budget_for_inbound())]
    # The run's counter is let go when the request ends, as a chat turn's is.
    assert get_meter().run_totals(client_id).dollars == 0.0


@pytest.mark.asyncio
async def test_a_client_that_names_one_of_your_chats_is_still_held_to_the_cap(home, monkeypatch):
    _enable(monkeypatch, cap=1.0)
    _client_id, token = _registered_client()
    voice = _Voice(home)
    _bind(monkeypatch, voice)
    server = await _server()
    try:
        status, body = await _speech(server, token, **{"X-Session-Key": CHAT})
    finally:
        await server.close()

    assert status == 503, body
    assert json.loads(body)["error"]["code"] == "tts_spend_refused"
    assert voice.said == []


# ── transcription ───────────────────────────────────────────────────────────────────────────


class _Ears:
    """A speech-to-text engine that hears the same words in every recording."""

    name = "studio-ears"
    display_name = "Studio ears"

    def __init__(self) -> None:
        self.heard = 0

    async def is_available(self) -> bool:
        return True

    async def transcribe(self, audio_path, model="", language=""):
        self.heard += 1
        return "plant the tomatoes on Saturday"


def _bind_ears(monkeypatch, ears: _Ears) -> None:
    """*ears* as the bound speech-to-text model, priced at $1.20 a minute, hearing two minutes in
    every upload: $2.40 a request."""
    from personalclaw import transcribe

    async def _available() -> bool:
        return True

    set_rate("studio-ears:dictation-1", {"unit": "minute", "per_minute": 1.2})
    monkeypatch.setattr(transcribe, "is_available", _available)
    monkeypatch.setattr(transcribe, "_resolve", lambda path: (ears, "dictation-1", ""))
    monkeypatch.setattr(transcribe, "audio_seconds", lambda path: 120.0)


async def _transcription(client: TestClient, token: str):
    import io

    clip = io.BytesIO(b"recorded audio")
    clip.name = "note.webm"
    response = await client.post(
        dialect.ROUTE_TRANSCRIPTIONS,
        data={"model": "whisper-1", "file": clip},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.status, await response.json()


@pytest.mark.asyncio
async def test_a_transcription_a_client_asks_for_counts_against_the_cap_and_is_refused_past_it(
    home, monkeypatch
):
    _enable(monkeypatch, cap=4.0)
    client_id, token = _registered_client()
    ears = _Ears()
    _bind_ears(monkeypatch, ears)
    server = await _server()
    try:
        first = await _transcription(server, token)
        second = await _transcription(server, token)
    finally:
        await server.close()

    assert first == (200, {"text": "plant the tomatoes on Saturday"})
    assert get_meter().day_totals().dollars == pytest.approx(2.40)
    status, body = second
    assert status == 502, body
    assert "daily" in body["error"]["message"] and "$4.00" in body["error"]["message"], body
    assert ears.heard == 1
    (row,) = _usage_rows(home)
    assert (row["source"], row["unit"], row["quantity"]) == ("background", "minute", 2.0)
    assert row["session_key"].startswith(f"inbound:{client_id}")
    assert _audited(home, dialect.ROUTE_TRANSCRIPTIONS) == [
        (200, ""),
        (502, "transcription failed"),
    ]
