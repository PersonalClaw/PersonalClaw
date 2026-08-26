"""Success Criteria 2, driven by the UNMODIFIED `openai` SDK and by `curl`.

The rest of the EA-2 suite asserts the wire shapes this dialect emits. That is not the
same claim as "a real client works": a shape can satisfy every field assertion and
still fail in an SDK that validates a field the tests did not think to check, or that
raises on an error envelope it cannot classify. So this module drives the doorway with
the genuine third-party client and with a genuine `curl` subprocess.

`openai` is an OPTIONAL extra (`pyproject.toml [openai]`), deliberately not a runtime
dependency — the provider-boundary tenet keeps vendor SDKs out of core. So these skip
when it is absent. A skip reads like a pass, which is why the skip reason names what
went unverified rather than staying silent, and why the shape-level assertions in
`test_ea2_openai_dialect.py` do not depend on this file being able to run.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from personalclaw.inbound import auth
from personalclaw.inbound import openai_dialect as dialect

openai_sdk = pytest.importorskip(
    "openai", reason="the `openai` extra is not installed — Success Criteria 2 UNVERIFIED here"
)

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    yield
    for surface in _SURFACES:
        os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


class _Session:
    def __init__(self, key: str, agent: str = "") -> None:
        self.key = key
        self.agent = agent
        self.messages: list[dict] = []
        self._pending: list[dict] = []
        self._has_reader = False
        self.task = None
        self.event = asyncio.Event()

    def append(self, role: str, content: str, cls: str = "", ts: str = "", **kw) -> None:
        msg = {"role": role, "content": content, "cls": cls, "ts": ts}
        self.messages.append(msg)
        self._pending.append(msg)
        self.event.set()

    def drain(self) -> list[dict]:
        out = list(self._pending)
        self._pending.clear()
        return out


class _State:
    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._background_tasks: set = set()

    def get_or_create_session(self, name=None, agent="", **kw) -> _Session:
        if name not in self._sessions:
            self._sessions[name] = _Session(name, agent=agent)
        return self._sessions[name]


def _configure(monkeypatch, *, persistent: bool):
    from personalclaw.config.loader import AgentConfig, AppConfig, ExternalAccessConfig
    from personalclaw.config.loader import ExternalAccessSurfaceConfig as Surface
    from personalclaw.inbound.clients import InboundClient

    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(enabled=True, openai=Surface(enabled=True))
    cfg.agents = {"researcher": AgentConfig()}
    cfg.default_agent = "researcher"
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))

    record = InboundClient(
        client_id="sdk-client", surfaces=["openai"], persistent_sessions=persistent
    )
    monkeypatch.setattr(dialect, "_lookup_client", lambda presented: record)
    return cfg


async def _serve(monkeypatch, *, turns=None, persistent=True):
    """A real TCP server with `/v1` mounted, plus the transcript the turn will emit."""
    seen_prompts: list[str] = []
    replies = list(turns or ["first reply", "second reply"])

    async def _fake_run(state, session, message):
        seen_prompts.append(message)
        reply = replies[min(len(seen_prompts) - 1, len(replies) - 1)]
        for token in reply.split(" "):
            session.append("chunk", token + " ", "chunk")
            await asyncio.sleep(0)
        session.append("done", "", "done")

    _configure(monkeypatch, persistent=persistent)
    state = _State()
    app = web.Application()
    app["state"] = state
    dialect.register_routes(app, turn_runner=_fake_run)
    server = TestServer(app)
    await server.start_server()
    return server, state, seen_prompts


def _sdk(server, token: str):
    """An UNMODIFIED SDK client pointed at the doorway. No custom transport, no shims."""
    return openai_sdk.OpenAI(
        base_url=f"http://127.0.0.1:{server.port}/v1",
        api_key=token,
        max_retries=0,
    )


@pytest.mark.asyncio
async def test_sdk_holds_a_multi_turn_conversation(monkeypatch):
    """Success Criteria 2, first half: an off-the-shelf client, two turns, continuity.

    Continuity is asserted at the SESSION, not just by both calls succeeding: the same
    `user` must land on ONE session key, which is what makes the second turn a
    continuation rather than two unrelated one-shots that happen to both answer.
    """
    server, state, prompts = await _serve(monkeypatch)
    token = auth.create_surface_token(dialect.OPENAI_SURFACE)
    client = _sdk(server, token)
    try:

        def _drive():
            first = client.chat.completions.create(
                model="personalclaw/researcher",
                messages=[{"role": "user", "content": "who are you?"}],
                user="alice",
            )
            second = client.chat.completions.create(
                model="researcher",  # the bare form, from a dropdown-only client
                messages=[{"role": "user", "content": "and what did I just ask?"}],
                user="alice",
            )
            return first, second

        first, second = await asyncio.to_thread(_drive)
    finally:
        await server.close()

    # The SDK parsed both, which is the claim: these are typed objects, not dicts.
    assert first.choices[0].message.content == "first reply"
    assert second.choices[0].message.content == "second reply"
    assert first.object == "chat.completion"
    assert first.usage is not None
    assert prompts == ["who are you?", "and what did I just ask?"]
    # Continuity: both turns on one session, and it is the contract's key shape.
    assert len(state._sessions) == 1, f"expected one session, got {list(state._sessions)}"
    key = next(iter(state._sessions))
    assert key == dialect.session_key_for("sdk-client", "alice")


@pytest.mark.asyncio
async def test_sdk_streams_and_sees_the_usage_block(monkeypatch):
    """T2-A1: `create(..., stream=True)` works verbatim in the SDK.

    The usage block is asserted THROUGH the SDK because that is the clause's point —
    "clients budget off it" is only true if the client's own parser surfaces it.
    """
    server, _, _ = await _serve(monkeypatch, turns=["alpha beta gamma"])
    token = auth.create_surface_token(dialect.OPENAI_SURFACE)
    client = _sdk(server, token)
    try:

        def _drive():
            stream = client.chat.completions.create(
                model="researcher",
                messages=[{"role": "user", "content": "stream please"}],
                stream=True,
                user="alice",
            )
            return list(stream)

        chunks = await asyncio.to_thread(_drive)
    finally:
        await server.close()

    assert chunks, "the SDK yielded no chunks"
    assert all(c.object == "chat.completion.chunk" for c in chunks)
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text.strip() == "alpha beta gamma"
    assert chunks[-1].usage is not None, "the SDK must surface the final frame's usage"
    assert chunks[-1].usage.total_tokens >= 0
    # And no chunk claimed a tool call — §2.3, checked through the SDK's own model.
    assert all(not (c.choices and c.choices[0].delta.tool_calls) for c in chunks)


@pytest.mark.asyncio
async def test_sdk_raises_a_typed_error_for_an_unknown_agent(monkeypatch):
    """T2-A1: "error shapes parse in the SDK".

    A 404 carrying the dashboard's ``{"error": "some string"}`` envelope makes the SDK
    raise an APIStatusError with no usable `code`. This asserts the SDK classified it as
    NotFoundError AND recovered the stable code — the two halves of the Amendment's
    "the dialect's wire contract wins, stable-code preserved".
    """
    server, _, _ = await _serve(monkeypatch)
    token = auth.create_surface_token(dialect.OPENAI_SURFACE)
    client = _sdk(server, token)
    try:

        def _drive():
            with pytest.raises(openai_sdk.NotFoundError) as excinfo:
                client.chat.completions.create(
                    model="personalclaw/no-such-agent",
                    messages=[{"role": "user", "content": "hi"}],
                )
            return excinfo.value

        error = await asyncio.to_thread(_drive)
    finally:
        await server.close()

    assert error.status_code == 404
    assert error.code == "unknown_agent", "the stable code must survive into the SDK"
    assert error.type == "invalid_request_error"


@pytest.mark.asyncio
async def test_sdk_raises_authentication_error_on_a_bad_key(monkeypatch):
    """The 401 envelope must classify too, or every misconfigured client gets a
    stack trace instead of "check your API key"."""
    server, _, _ = await _serve(monkeypatch)
    auth.create_surface_token(dialect.OPENAI_SURFACE)
    monkeypatch.setattr(dialect, "_lookup_client", lambda presented: None)
    client = _sdk(server, "sk-definitely-not-the-token")
    try:

        def _drive():
            with pytest.raises(openai_sdk.AuthenticationError) as excinfo:
                client.chat.completions.create(
                    model="researcher", messages=[{"role": "user", "content": "hi"}]
                )
            return excinfo.value

        error = await asyncio.to_thread(_drive)
    finally:
        await server.close()
    assert error.status_code == 401
    # Generic admission code (see the dialect test's note); the SDK still classifies it
    # as AuthenticationError, which is the half a real client acts on.
    assert error.code == "unauthorized"


@pytest.mark.asyncio
async def test_sdk_models_list_shows_only_agents(monkeypatch):
    """`client.models.list()` in the SDK's own typed form."""
    server, _, _ = await _serve(monkeypatch)
    token = auth.create_surface_token(dialect.OPENAI_SURFACE)
    client = _sdk(server, token)
    try:
        models = await asyncio.to_thread(lambda: list(client.models.list()))
    finally:
        await server.close()
    assert [m.id for m in models] == ["personalclaw/researcher"]
    assert all(m.owned_by == "personalclaw" for m in models)


@pytest.mark.asyncio
async def test_curl_audio_speech_returns_bound_tts_audio(monkeypatch):
    """Success Criteria 2, second half — with a real `curl`, as the clause words it.

    A stub stands in for the bound TTS provider because a real one needs a downloaded
    voice model; what is verified end-to-end is the HTTP contract an external caller
    sees (status, content type, body bytes) and that the cosmetic ``model: "tts-1"``
    never chose the engine — the params handed to the provider come from
    ``resolve_voice``.
    """
    if shutil.which("curl") is None:  # pragma: no cover — curl is present on macOS/CI
        pytest.skip("curl is not installed — the Success Criteria 2 audio leg is UNVERIFIED")

    handed: dict = {}

    async def _fake_stream(provider, text, *, voice, speed, speech_voice):
        handed.update(provider=provider, voice=voice, text=text)
        yield 0, text, b"RIFF____WAVEfake-bound-audio"

    monkeypatch.setattr(
        dialect,
        "resolve_voice",
        lambda name="", **kw: {
            "provider": "THE-BOUND-PROVIDER",
            "voice": "the-bound-voice",
            "speed": 1.0,
            "speech_voice": "",
        },
    )
    monkeypatch.setattr("personalclaw.voice_reply.streaming_voice_reply", _fake_stream)
    server, _, _ = await _serve(monkeypatch)
    token = auth.create_surface_token(dialect.OPENAI_SURFACE)
    url = f"http://127.0.0.1:{server.port}{dialect.ROUTE_SPEECH}"
    payload = json.dumps({"model": "tts-1", "input": "hello from curl", "voice": "whatever"})
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [
                "curl",
                "-sS",
                "--fail-with-body",
                "-o",
                "-",
                "-w",
                "\n%{http_code}\n%{content_type}",
                "-H",
                f"Authorization: Bearer {token}",
                "-H",
                "Content-Type: application/json",
                "-d",
                payload,
                url,
            ],
            capture_output=True,
        )
    finally:
        await server.close()

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    body, status, content_type = proc.stdout.rsplit(b"\n", 2)
    assert status == b"200"
    assert content_type.startswith(b"audio/wav")
    assert body == b"RIFF____WAVEfake-bound-audio"
    # The cosmetic alias never reached provider selection.
    assert handed["provider"] == "THE-BOUND-PROVIDER"
    assert handed["voice"] == "the-bound-voice"
    assert handed["text"] == "hello from curl"
