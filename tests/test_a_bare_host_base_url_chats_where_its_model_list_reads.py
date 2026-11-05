"""A bare-host Base URL chats where its model list reads.

For an OpenAI-compatible provider, the model list and Test connection read `<Base URL>/v1/models`
when the Base URL names no version (`catalog.openai_compatible_models_url`), while chat, its
stream and embeddings sent to the Base URL as typed: `<Base URL>/chat/completions`. A vLLM server
saved at its bare address, as the vLLM app's default and its help have it, read "Connected — N
model(s) available", and its first chat went to a path the server does not serve and failed.

A bare host now chats under `/v1`, where its model list reads (`catalog.openai_compatible_base`).
A Base URL with any path is the provider's own and chats exactly as typed, as before, a trailing
slash included. What stays open: a Base URL with a path and no version (`…/api`) still lists its
models under `/v1` while it chats as typed, until the Base URL is normalized when it is saved.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openai")

from personalclaw.llm.catalog import (  # noqa: E402
    openai_compatible_base,
    openai_compatible_models_url,
)
from personalclaw.llm.credentials import Credential  # noqa: E402
from personalclaw.llm.openai import OpenAIProvider  # noqa: E402

_KEY = Credential(name="t", kind="api_key", secret="placeholder", source="none")


def _chat_base(base_url: str | None) -> str:
    """Where the provider's chat, stream and embeddings send to."""
    provider = OpenAIProvider(model="m", credential=_KEY, base_url=base_url)
    return str(provider._client.base_url).rstrip("/")


@pytest.mark.parametrize(
    "typed",
    [
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8000/",
        "https://api.vendor.example",
    ],
)
def test_a_base_url_with_no_version_chats_under_v1_as_its_model_list_reads(typed):
    assert _chat_base(typed) == typed.rstrip("/") + "/v1"
    assert openai_compatible_models_url(typed) == _chat_base(typed) + "/models"


@pytest.mark.parametrize(
    "typed",
    [
        "http://127.0.0.1:8000/v1",
        "https://api.vendor.example/api/coding/paas/v4",
        "https://api.vendor.example/v1beta/openai",
    ],
)
def test_a_base_url_that_names_its_version_is_kept_as_typed(typed):
    assert _chat_base(typed) == typed
    assert openai_compatible_models_url(typed) == typed + "/models"


@pytest.mark.parametrize(
    "typed", ["http://127.0.0.1:8000/v1/", "https://api.vendor.example/v1beta/openai/"]
)
def test_a_versioned_base_url_keeps_its_trailing_slash(typed):
    """Returned untouched: a slash is stripped only where `/v1` is added (an app's own tests
    assert the base it hands the client, slash and all)."""
    assert openai_compatible_base(typed) == typed


def test_a_base_url_with_a_path_and_no_version_chats_as_typed():
    """The provider's own path, as on main. Its model list still reads `/v1/models` under it:
    the half this item leaves open."""
    typed = "https://router.vendor.example/api"

    assert openai_compatible_base(typed) == typed
    assert _chat_base(typed) == typed
    assert openai_compatible_models_url(typed) == typed + "/v1/models"


def test_no_base_url_is_the_sdk_default(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    assert _chat_base(None) == "https://api.openai.com/v1"


@pytest.mark.asyncio
async def test_a_chat_to_a_bare_host_reaches_a_server_that_serves_only_v1(monkeypatch):
    """Driven: a server on this machine that answers only under `/v1`, its Base URL saved as the
    bare host. Sent as typed, the chat went to `/chat/completions` and was refused with a 404."""
    import http.server
    import threading

    from test_a_model_call_keeps_to_the_network_egress_settings import (
        _answer,
        _chat_provider,
        _Endpoint,
        _Handler,
    )

    class _OnlyV1(_Handler):
        def do_POST(self) -> None:  # noqa: N802 — http.server's name
            if self.path.startswith("/v1/"):
                super().do_POST()
                return
            self.server.paths.append(self.path)  # type: ignore[attr-defined]
            self._json({"error": {"message": "not found"}}, status=404)

    class _Server(_Endpoint):
        def __init__(self) -> None:
            http.server.ThreadingHTTPServer.__init__(self, ("127.0.0.1", 0), _OnlyV1)
            self.paths: list[str] = []
            self.moved_to = ""

    for name in (
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    server = _Server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        bare = f"http://127.0.0.1:{server.port}"
        assert await _answer(_chat_provider(bare)) == "hello"
        assert server.paths == ["/v1/chat/completions"]
    finally:
        server.shutdown()
        server.server_close()
