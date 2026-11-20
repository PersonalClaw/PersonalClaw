"""Every reader of a saved OpenAI-compatible Base URL reaches the address chat does.

One rule (`catalog.openai_compatible_base`): ``/v1`` is added to a Base URL with nothing after
the host, and a Base URL with a path is the provider's own and is kept as typed. Chat, its
stream and embeddings followed it. The model list (and so Test connection) did not: it added
``/v1`` to any Base URL whose path names no version, so ``https://router/api`` read "Connected"
from ``/api/v1/models`` while every chat posted to ``/api/chat/completions``. And image
generation, speech to text, text to speech and the capture proxy sent a bare host as typed, to
the root, while chat went under ``/v1``.

Now each of them reads the same address. A Base URL with a path and no version whose server
lists nothing there fails Test, and the sentence names the address with the version added.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

openai = pytest.importorskip("openai")

from personalclaw.llm.catalog import (  # noqa: E402
    ModelDiscoveryError,
    openai_compatible_discover_models,
    openai_compatible_models_url,
)
from personalclaw.llm.credentials import Credential  # noqa: E402
from personalclaw.llm.openai import OpenAIProvider  # noqa: E402

_KEY = Credential(name="t", kind="api_key", secret="placeholder", source="none")

#: (case, the Base URL as saved, the address every reader must reach)
_BASES = [
    ("a bare host", "http://127.0.0.1:8000", "http://127.0.0.1:8000/v1"),
    ("a bare host with a slash", "http://127.0.0.1:8000/", "http://127.0.0.1:8000/v1"),
    ("a path and no version", "https://router.vendor.example/api", None),
    ("a version", "https://api.vendor.example/v1", None),
    ("a version not last", "https://api.vendor.example/v1beta/openai", None),
]
_IDS = [row[0] for row in _BASES]


def _expected(typed: str, reached: str | None) -> str:
    return (reached or typed).rstrip("/")


def _sdk_base(base_url: str | None) -> str:
    """The base the openai SDK sends to for ``base_url`` (its default when ``None``)."""
    return str(openai.AsyncOpenAI(api_key="k", base_url=base_url).base_url).rstrip("/")


class _Recorder:
    """A stand-in ``openai`` module: notes the ``base_url`` a reader builds its client with, and
    answers every call with a stub, so nothing is sent."""

    def __init__(self) -> None:
        self.bases: list[str | None] = []
        client = MagicMock()
        client.audio.transcriptions.create = AsyncMock(return_value=MagicMock(text="hi"))
        client.audio.speech.create = AsyncMock(return_value=SimpleNamespace(content=b"mp3"))
        client.close = AsyncMock()
        self.client = client
        self.module = MagicMock()
        self.module.OpenAIError = openai.OpenAIError

        def _build(**kwargs: Any) -> Any:
            self.bases.append(kwargs.get("base_url"))
            return client

        self.module.AsyncOpenAI = _build


@pytest.mark.parametrize(("case", "typed", "reached"), _BASES, ids=_IDS)
def test_the_model_list_reads_where_chat_sends(case, typed, reached):
    """🔴 Red before for a path and no version: the list read ``/api/v1/models``."""
    chat = str(OpenAIProvider(model="m", credential=_KEY, base_url=typed)._client.base_url)

    assert chat.rstrip("/") == _expected(typed, reached), case
    assert openai_compatible_models_url(typed) == _expected(typed, reached) + "/models", case


@pytest.mark.parametrize(("case", "typed", "reached"), _BASES, ids=_IDS)
def test_image_generation_sends_where_chat_does(case, typed, reached):
    """🔴 Red before for a bare host: images went to the root."""
    from personalclaw.image_gen.openai_provider import OpenAIImageProvider

    prov = OpenAIImageProvider(provider_name="X", endpoint=typed, api_key="k")
    with patch.object(OpenAIImageProvider, "_resolve_api_key", return_value="k"):
        client = prov._client()

    assert str(client.base_url).rstrip("/") == _expected(typed, reached), case


@pytest.mark.parametrize(("case", "typed", "reached"), _BASES, ids=_IDS)
def test_speech_to_text_sends_where_chat_does(case, typed, reached, tmp_path):
    """🔴 Red before for a bare host: transcriptions went to the root."""
    from personalclaw.stt.openai_provider import OpenAISttProvider

    audio = tmp_path / "memo.webm"
    audio.write_bytes(b"x")
    sdk = _Recorder()
    prov = OpenAISttProvider(provider_name="X", endpoint=typed, api_key="k")
    with patch.dict("sys.modules", {"openai": sdk.module}):
        assert asyncio.run(prov.transcribe(str(audio), model="whisper-1")) == "hi"

    assert [_sdk_base(b) for b in sdk.bases] == [_expected(typed, reached)], case


@pytest.mark.parametrize(("case", "typed", "reached"), _BASES, ids=_IDS)
def test_text_to_speech_sends_where_chat_does(case, typed, reached, tmp_path):
    """🔴 Red before for a bare host: speech went to the root."""
    from personalclaw.tts.openai_provider import OpenAITtsProvider

    sdk = _Recorder()
    prov = OpenAITtsProvider(provider_name="X", endpoint=typed, api_key="k")
    with patch.dict("sys.modules", {"openai": sdk.module}):
        asyncio.run(prov.synthesize("hello", voice="tts-1", output_path=str(tmp_path / "a.mp3")))

    assert [_sdk_base(b) for b in sdk.bases] == [_expected(typed, reached)], case


@pytest.mark.parametrize(("case", "typed", "reached"), _BASES, ids=_IDS)
def test_the_capture_proxy_sends_a_saved_provider_where_chat_does(
    case, typed, reached, monkeypatch
):
    """🔴 Red before for a bare host: the capture proxy posted to ``<host>/chat/completions``."""
    from personalclaw.inbound import capture_proxy as cp

    monkeypatch.setattr(cp, "_client_upstream_name", lambda client: "router")
    monkeypatch.setattr(cp, "_provider_upstream", lambda name: (typed, "secret"))
    request = SimpleNamespace(headers={})

    upstream, refusal = cp._resolve_upstream(None, object(), cp.DIALECT_OPENAI, request)

    assert refusal == "" and upstream is not None
    assert upstream.url == _expected(typed, reached) + "/chat/completions", case


def test_the_capture_proxys_anthropic_upstream_is_kept_as_saved(monkeypatch):
    """The rule is the OpenAI dialect's: an Anthropic upstream is joined as it was."""
    from personalclaw.inbound import capture_proxy as cp

    monkeypatch.setattr(cp, "_client_upstream_name", lambda client: "claude")
    monkeypatch.setattr(cp, "_provider_upstream", lambda name: ("https://gw.vendor.example", "s"))

    upstream, _ = cp._resolve_upstream(
        None, object(), cp.DIALECT_ANTHROPIC, SimpleNamespace(headers={})
    )

    assert upstream is not None and upstream.url == "https://gw.vendor.example/messages"


def test_a_client_supplied_base_is_kept_as_sent(monkeypatch):
    """Passthrough's base comes from the calling agent, not a saved Base URL: sent as given."""
    from personalclaw.inbound import capture_proxy as cp

    request = SimpleNamespace(
        headers={cp.UPSTREAM_KEY_HEADER: "theirs", cp.UPSTREAM_BASE_HEADER: "http://127.0.0.1:9"}
    )

    upstream, _ = cp._resolve_upstream(None, object(), cp.DIALECT_OPENAI, request)

    assert upstream is not None and upstream.url == "http://127.0.0.1:9/chat/completions"


class _ServesOnly:
    """A loopback server that lists models at one path and answers 404 everywhere else."""

    def __init__(self, path: str) -> None:
        self.requested: list[str] = []
        outer = self
        body = json.dumps({"object": "list", "data": [{"id": "m1", "object": "model"}]}).encode()

        class _H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
                outer.requested.append(self.path)
                found = self.path == path
                payload = body if found else b'{"error":{"message":"not found"}}'
                self.send_response(200 if found else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.base = f"http://127.0.0.1:{self._srv.server_address[1]}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def test_a_path_with_no_version_whose_server_is_under_v1_fails_test_naming_the_address():
    """🔴 Red before: the list read ``/api/v1/models`` and said Connected, and every chat on the
    same Base URL then posted to ``/api/chat/completions``, which this server does not serve."""
    srv = _ServesOnly("/api/v1/models")
    try:
        with pytest.raises(ModelDiscoveryError) as caught:
            asyncio.run(openai_compatible_discover_models(f"{srv.base}/api", "fake-key-test"))
    finally:
        srv.close()

    assert srv.requested == ["/api/models"]
    assert f"{srv.base}/api/v1" in str(caught.value)


def test_a_path_with_no_version_whose_server_serves_it_lists_its_models():
    srv = _ServesOnly("/api/models")
    try:
        got = asyncio.run(openai_compatible_discover_models(f"{srv.base}/api", "fake-key-test"))
    finally:
        srv.close()

    assert [m.id for m in got] == ["m1"]


def test_a_404_under_a_named_version_suggests_no_other_version():
    """The hint is for a Base URL that names no version: one that does is the provider's own."""
    srv = _ServesOnly("/elsewhere")
    try:
        with pytest.raises(ModelDiscoveryError) as caught:
            asyncio.run(openai_compatible_discover_models(f"{srv.base}/v4", "fake-key-test"))
    finally:
        srv.close()

    assert f"{srv.base}/v4/v1" not in str(caught.value)


@pytest.mark.parametrize(
    "path",
    ["", "/api/chat/completions", "/api/models"],
    ids=["a bare host, already under /v1", "a pasted chat path", "a pasted models path"],
)
def test_a_404_that_a_version_would_not_fix_suggests_none(path):
    """A bare host's list already reads under ``/v1``, and a pasted request path is what the
    404's sentence already says to trim: neither is told to add a version."""
    srv = _ServesOnly("/elsewhere")
    try:
        with pytest.raises(ModelDiscoveryError) as caught:
            asyncio.run(openai_compatible_discover_models(f"{srv.base}{path}", "fake-key-test"))
    finally:
        srv.close()

    assert "add it to the Base URL" not in str(caught.value)


def test_the_suggested_address_keeps_a_query_where_it_was():
    from personalclaw.llm.catalog import _missing_version_hint

    hint = _missing_version_hint("https://router.vendor.example/api?tenant=t1")

    assert "https://router.vendor.example/api/v1?tenant=t1." in hint
