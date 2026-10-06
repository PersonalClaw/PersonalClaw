"""Every request a model provider sends keeps to the owner's Network egress settings.

A provider's Test and its model list went through the egress guard, but its chat, its stream, its
embeddings and its transcription, speech and image calls went through the vendor SDK's own HTTP
client, which asked nothing: with the provider's host on Denied hosts, Test was refused in words
that named the setting while a chat on that provider's model was answered, carrying what the turn
had read to the host she denied, and the security log held no row for it. Each of those requests
now asks the guard before it is sent, each redirect hop included: a denied host is never contacted,
the call fails in the sentence that names the setting and the host, and the refusal is in the
security log, as is every request let through.

Every endpoint here is a stand-in on this machine, so nothing leaves it.
"""

from __future__ import annotations

import http.server
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from personalclaw import mcp_core
from personalclaw.config.loader import config_dir
from personalclaw.guardrails.ceiling import ceiling_path, reset_ceiling
from personalclaw.llm.credentials import Credential
from personalclaw.sel import sel

openai = pytest.importorskip("openai", reason="the `openai` extra is not installed")

#: Where the refusal's sentence sends her, in the words the guard and the Test button use.
DENIED_HOSTS = "is on Denied hosts in Settings → Security → Network egress"
#: What a run with no network is told when it asks for anything else.
EGRESS_OFF_REASON = "egress is off for this run (safety profile egress tier 'off')"

_CHUNKS = [
    {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello"}}]},
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
]


#: The path a redirect sends a request on to, which the stand-in answers itself.
_MOVED = "/elsewhere"


class _Endpoint(http.server.ThreadingHTTPServer):
    """An OpenAI-compatible model server on this machine that records each request it is sent.

    Set :attr:`moved_to` and it answers a request with a redirect there instead, unless the
    request is one a redirect sent it (its path under :data:`_MOVED`)."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.paths: list[str] = []
        self.moved_to = ""

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 — http.server's name
        server: _Endpoint = self.server  # type: ignore[assignment]
        server.paths.append(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if server.moved_to and not self.path.startswith(_MOVED):
            self.send_response(307)
            self.send_header("Location", f"{server.moved_to}{self.path}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.endswith("/chat/completions"):
            self._stream_chat()
            return
        if self.path.endswith("/embeddings"):
            self._json({"object": "list", "data": [{"index": 0, "embedding": [0.5, 0.25]}]})
            return
        if self.path.endswith("/images/generations"):
            self._json({"created": 1, "data": [{"b64_json": "aW1n"}]})
            return
        if self.path.endswith("/audio/transcriptions"):
            self._json({"text": "heard you"})
            return
        if self.path.endswith("/audio/speech"):
            self._bytes(b"audio bytes", "audio/mpeg")
            return
        del raw
        self._json({"error": {"message": "not found"}}, status=404)

    def _stream_chat(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in _CHUNKS:
            row = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m"}
            self.wfile.write(f"data: {json.dumps({**row, **chunk})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def _json(self, payload: dict, *, status: int = 200) -> None:
        self._bytes(json.dumps(payload).encode(), "application/json", status=status)

    def _bytes(self, body: bytes, kind: str, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def endpoint(monkeypatch):
    # A developer's proxy would carry these requests off the machine, past the stand-in.
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    server = _Endpoint()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def _egress(**settings: list[str]) -> None:
    """This test home's Network egress settings."""
    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"egress": settings}}), encoding="utf-8"
    )


def _audited() -> list[tuple[str, str]]:
    return [
        (row.get("outcome", ""), row.get("resources", ""))
        for row in reversed(sel().recent(200))
        if row.get("operation") == "egress_fetch"
    ]


def _chat_provider(base: str):
    from personalclaw.llm.openai import OpenAIProvider

    credential = Credential(name="stand-in", kind="api_key", secret="fake-key-test", source="env")
    return OpenAIProvider(
        model="m", credential=credential, base_url=base, extra_options={"embedding_model": "e"}
    )


async def _answer(provider) -> str:
    from personalclaw.llm.base import EVENT_TEXT_CHUNK

    text = ""
    try:
        async for event in provider.stream("One word: hello."):
            if event.kind == EVENT_TEXT_CHUNK:
                text += event.text
    finally:
        await provider.shutdown()
    return text


def _refused_sentence(url: str, host: str) -> str:
    return f"{url} was not reached: {host} {DENIED_HOSTS}."


# ── chat, its stream and its embeddings ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_chat_to_a_denied_host_is_refused_before_anything_is_sent(endpoint):
    _egress(deny_hosts=["127.0.0.1"])
    provider = _chat_provider(endpoint.base)

    with pytest.raises(Exception) as refused:
        await _answer(provider)

    url = f"{endpoint.base}/chat/completions"
    assert endpoint.paths == [], "the chat reached the host on Denied hosts"
    assert str(refused.value) == _refused_sentence(url, "127.0.0.1")
    assert _audited() == [("denied", url)]


@pytest.mark.asyncio
async def test_a_stateless_completion_to_a_denied_host_is_refused(endpoint):
    _egress(deny_hosts=["127.0.0.1"])
    provider = _chat_provider(endpoint.base)

    with pytest.raises(Exception) as refused:
        async for _event in provider.complete([{"role": "user", "content": "hello"}]):
            pass

    assert endpoint.paths == []
    assert DENIED_HOSTS in str(refused.value)
    assert _audited() == [("denied", f"{endpoint.base}/chat/completions")]


@pytest.mark.asyncio
async def test_embeddings_for_a_denied_host_are_refused(endpoint):
    _egress(deny_hosts=["127.0.0.1"])
    provider = _chat_provider(endpoint.base)

    with pytest.raises(Exception) as refused:
        await provider.embed(["a note to index"])

    assert endpoint.paths == []
    assert str(refused.value) == _refused_sentence(f"{endpoint.base}/embeddings", "127.0.0.1")


@pytest.mark.asyncio
async def test_a_chat_on_her_own_model_server_still_answers_and_is_audited(endpoint):
    """Her model server on this computer, configured as the provider's endpoint, needs no entry
    in Allowed hosts: the endpoint is hers. The request it answers is in the security log."""
    provider = _chat_provider(endpoint.base)

    assert await _answer(provider) == "hello"
    assert endpoint.paths == ["/v1/chat/completions"]
    assert _audited() == [("allowed", f"{endpoint.base}/chat/completions")]


@pytest.mark.asyncio
async def test_a_redirect_to_a_denied_host_is_refused_before_it_is_followed(endpoint):
    """The endpoint she allowed sends the request on to a host she denied: the hop is asked
    before it is sent, and refused."""
    _egress(deny_hosts=["localhost"])
    endpoint.moved_to = f"http://localhost:{endpoint.port}{_MOVED}"
    provider = _chat_provider(endpoint.base)

    with pytest.raises(Exception) as refused:
        await _answer(provider)

    moved = f"http://localhost:{endpoint.port}{_MOVED}/v1/chat/completions"
    assert endpoint.paths == ["/v1/chat/completions"], "the redirect to a denied host was followed"
    assert str(refused.value) == _refused_sentence(moved, "localhost")
    assert _audited() == [("allowed", f"{endpoint.base}/chat/completions"), ("denied", moved)]


@pytest.mark.asyncio
async def test_a_redirect_off_her_endpoint_to_this_computer_is_refused(endpoint):
    """Only the endpoint she configured may be on her own machine: a hop elsewhere on it is held
    to the public-only stance and her Allowed hosts, as any fetch is."""
    endpoint.moved_to = f"http://localhost:{endpoint.port}{_MOVED}"
    provider = _chat_provider(endpoint.base)

    with pytest.raises(Exception) as refused:
        await _answer(provider)

    assert endpoint.paths == ["/v1/chat/completions"]
    said = str(refused.value)
    assert "add localhost to Allowed hosts in Settings → Security → Network egress" in said, said


@pytest.mark.asyncio
async def test_a_refusal_is_final_at_once_and_not_sent_again(endpoint):
    """The client library retries a request whose sending failed; a refusal is not one, so the
    guard is asked once and one refusal is logged."""
    _egress(deny_hosts=["127.0.0.1"])
    provider = _chat_provider(endpoint.base)

    with pytest.raises(Exception):
        await _answer(provider)

    assert [outcome for outcome, _url in _audited()] == ["denied"]


# ── a run's tier is not the model's ───────────────────────────────────────────


def _ceiling(egress: str) -> None:
    """The operator ceiling bounding every run's egress tier to *egress*."""
    path = ceiling_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "scopes": {"egress": {"value": egress}}}), encoding="utf-8"
    )
    reset_ceiling()


@contextmanager
def _in_run(session_key: str = "dashboard:research-chat"):
    token = mcp_core.set_current_session_key(session_key)
    try:
        yield
    finally:
        mcp_core.reset_current_session_key(token)


@pytest.mark.asyncio
async def test_a_run_with_no_network_still_reaches_its_model(endpoint):
    """A model is the agent's own, shared by every run: a run whose egress tier is off thinks
    with it all the same, while its own requests are refused (the control)."""
    from personalclaw.net import CONNECTOR, EgressBlocked, egress_policy_for, fetch

    _ceiling("off")
    _egress(allow_hosts=["127.0.0.1"])
    provider = _chat_provider(endpoint.base)

    with _in_run():
        assert await _answer(provider) == "hello"
        with pytest.raises(EgressBlocked) as refused:
            await fetch(f"{endpoint.base}/models", policy=egress_policy_for(CONNECTOR))

    assert EGRESS_OFF_REASON in str(refused.value)
    assert endpoint.paths == ["/v1/chat/completions"]


@pytest.mark.asyncio
async def test_a_run_with_no_network_makes_no_image(endpoint):
    """An image is a thing the run asks for, not the agent's own model: a run whose egress tier
    is off is refused it, as it is refused a fetch, and nothing reaches the image provider."""
    from personalclaw.image_gen.openai_provider import OpenAIImageProvider
    from personalclaw.image_gen.provider import ImageGenError

    _ceiling("off")
    images = OpenAIImageProvider(
        provider_name="stand-in", endpoint=endpoint.base, api_key="fake-key"
    )

    with _in_run(), pytest.raises(ImageGenError) as refused:
        await images.generate("a lighthouse", model="gpt-image-1")

    assert EGRESS_OFF_REASON in str(refused.value)
    assert endpoint.paths == []


# ── what the chat says ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_turn_says_the_refusal_in_the_guards_words(endpoint):
    from personalclaw.llm_helpers import humanize_provider_error

    _egress(deny_hosts=["127.0.0.1"])
    provider = _chat_provider(endpoint.base)

    with pytest.raises(Exception) as refused:
        await _answer(provider)

    url = f"{endpoint.base}/chat/completions"
    assert humanize_provider_error(refused.value) == _refused_sentence(url, "127.0.0.1")


# ── transcription, speech and images ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_transcription_for_a_denied_host_is_refused(endpoint, tmp_path: Path):
    from personalclaw.stt.openai_provider import OpenAISttProvider
    from personalclaw.stt.provider import SttError

    _egress(deny_hosts=["127.0.0.1"])
    audio = tmp_path / "note.wav"
    audio.write_bytes(b"RIFF....WAVE")
    stt = OpenAISttProvider(provider_name="stand-in", endpoint=endpoint.base, api_key="fake-key")

    with pytest.raises(SttError) as refused:
        await stt.transcribe(str(audio), model="whisper-1")

    url = f"{endpoint.base}/audio/transcriptions"
    assert endpoint.paths == []
    assert str(refused.value) == _refused_sentence(url, "127.0.0.1")
    assert _audited() == [("denied", url)]


@pytest.mark.asyncio
async def test_speech_for_a_denied_host_is_refused(endpoint, tmp_path: Path):
    from personalclaw.tts.openai_provider import OpenAITtsProvider

    _egress(deny_hosts=["127.0.0.1"])
    tts = OpenAITtsProvider(provider_name="stand-in", endpoint=endpoint.base, api_key="fake-key")

    spoken = await tts.synthesize("hello", voice="tts-1", output_path=str(tmp_path / "out.mp3"))

    assert spoken is None
    assert endpoint.paths == []
    assert _audited() == [("denied", f"{endpoint.base}/audio/speech")]


@pytest.mark.asyncio
async def test_an_image_for_a_denied_host_is_refused(endpoint):
    from personalclaw.image_gen.openai_provider import OpenAIImageProvider
    from personalclaw.image_gen.provider import ImageGenError

    _egress(deny_hosts=["127.0.0.1"])
    images = OpenAIImageProvider(
        provider_name="stand-in", endpoint=endpoint.base, api_key="fake-key"
    )

    with pytest.raises(ImageGenError) as refused:
        await images.generate("a lighthouse", model="gpt-image-1")

    url = f"{endpoint.base}/images/generations"
    assert endpoint.paths == []
    assert str(refused.value) == _refused_sentence(url, "127.0.0.1")


@pytest.mark.asyncio
async def test_an_image_on_her_own_server_is_made(endpoint):
    from personalclaw.image_gen.openai_provider import OpenAIImageProvider

    images = OpenAIImageProvider(
        provider_name="stand-in", endpoint=endpoint.base, api_key="fake-key"
    )

    made = await images.generate("a lighthouse", model="gpt-image-1")

    assert [m.b64 for m in made] == ["aW1n"]
    assert _audited() == [("allowed", f"{endpoint.base}/images/generations")]


# ── the Anthropic wire ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_anthropic_chat_to_a_denied_host_is_refused(endpoint):
    pytest.importorskip("anthropic", reason="the `anthropic` extra is not installed")
    from personalclaw.llm.anthropic import AnthropicProvider

    _egress(deny_hosts=["127.0.0.1"])
    credential = Credential(name="stand-in", kind="api_key", secret="fake-key-test", source="env")
    base = f"http://127.0.0.1:{endpoint.port}"
    provider = AnthropicProvider(model="m", credential=credential, base_url=base)

    with pytest.raises(Exception) as refused:
        await _answer(provider)

    url = f"{base}/v1/messages"
    assert endpoint.paths == []
    assert str(refused.value) == _refused_sentence(url, "127.0.0.1")
    assert _audited() == [("denied", url)]
