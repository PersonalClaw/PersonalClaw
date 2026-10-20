"""The `/v1` doorway.

These assert the ROUTE, not the helpers behind it. Every chat test drives
``POST /v1/chat/completions`` through a real aiohttp server with a stub turn runner,
because the interesting failures live in the wiring — an error shape that a helper
returns correctly and the route never reaches, a session key derived in a function
nothing calls, a `tool_calls` delta added by the translation layer rather than by the
helper under test.

Four properties carry the weight:

* **An unknown agent is a 404 in the dialect's error shape.** ``resolve_agent_bindings``
  would fall back to ``default_agent``, so the pre-check in ``resolve_agent`` is the only
  thing standing between an external caller and a confident wrong answer. Falsified by
  deleting the membership check and watching this go 200.
* **No `tool_calls` ever reach the wire**, even when the turn's transcript contains tool
  and permission rows — asserted with those rows present, so the test would notice a
  translation that started passing them through.
* **Statelessness reaches every place an earlier request lives on** — the transcript, and
  the runtime and resume id the session manager holds — and fails closed. Driven end to end,
  with a ``persistent_sessions`` client keeping its conversation beside it, in
  ``test_an_openai_endpoint_request_is_answered_alone.py``.
* **Zero bindable provider names in the dialect module**, with a vacuity case proving the
  grep can fail.

Isolation: ``PERSONALCLAW_HOME`` (read per call by ``config_dir``, so it is undoable)
rather than patching ``config.loader.config_dir``, which a consumer module's
``from ... import config_dir`` would have frozen past the undo.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.inbound import auth
from personalclaw.inbound import openai_dialect as dialect

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")

#: Names of BINDABLE providers/vendors — the ones a dialect is tempted to write when a
#: cosmetic alias needs mapping. Deliberately NOT including this surface's own name
#: (``openai``): `/v1` is a protocol many vendors implement, and
#: `docs/architecture/provider-boundary.md` blesses `/v1/audio` shapes in core by name.
#: The tenet is "no vendor-specific LOGIC", so the rail bans the names that could only
#: appear as routing decisions, not the wire format's identity.
BANNED_PROVIDER_NAMES = (
    "piper",
    "kokoro",
    "elevenlabs",
    "faster_whisper",
    "faster-whisper",
    "fasterwhisper",
    "anthropic",
    "claude",
    "gemini",
    "ollama",
    "bedrock",
    "openrouter",
    "deepgram",
    "azure",
    "vertex",
    "mistral",
    "cohere",
    "groq",
    "llama",
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """A private home per test, and no surface token leaking between them.

    Cleared on BOTH sides: ``create_surface_token`` mirrors into ``os.environ`` itself,
    so a token minted mid-test is a variable monkeypatch never recorded and never
    undoes — it would read as "this surface has a valid token" for every later test in
    this worker.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    yield
    for surface in _SURFACES:
        os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


def test_isolated_home_binds(tmp_path):
    """The vacuity floor under every other test here: the redirect actually took.

    Without this, a suite that silently kept pointing at the real ``~/.personalclaw``
    would still pass everything below — and would be writing to it.
    """
    from personalclaw.config.loader import config_dir

    assert Path(config_dir()) == tmp_path
    assert str(Path.home()) not in str(config_dir()) or str(tmp_path).startswith(str(Path.home()))


# ── Fixtures for a configured surface ─────────────────────────────────────────


def _enable(monkeypatch, *, agents=("researcher", "writer"), enabled=True, master=True):
    """Point ``AppConfig.load()`` at an external-access config without writing files."""
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AgentConfig, AppConfig

    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(
        enabled=master, openai=Surface(enabled=enabled, allow_remote=False)
    )
    cfg.agents = {name: AgentConfig() for name in agents}
    cfg.default_agent = agents[0] if agents else ""
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    return cfg


def _token() -> str:
    return auth.create_surface_token(dialect.OPENAI_SURFACE)


class _FakeSession:
    """The parts of ``_ChatSession`` the dialect touches, and nothing else."""

    def __init__(self, key: str, agent: str = "") -> None:
        self.key = key
        self.agent = agent
        self.messages: list[dict] = []
        self._pending: list[dict] = []
        self._owed_subagent_endings: list[str] = []
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

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


async def _no_other_agent(*_a, **_k):  # noqa: ANN002, ANN003
    raise AssertionError("no request here names another agent for an existing session")


class _FakeSessions:
    """The session manager, as far as the dialect asks it anything: to let a conversation go."""

    def __init__(self) -> None:
        self.forgotten: list[str] = []

    def forget_conversation(self, key: str) -> None:
        self.forgotten.append(key)


class _FakeState:
    def __init__(self) -> None:
        self._sessions: dict[str, _FakeSession] = {}
        self._background_tasks: set = set()
        self.sessions = _FakeSessions()

    def get_or_create_session(self, name=None, agent="", **kw) -> _FakeSession:
        if name not in self._sessions:
            self._sessions[name] = _FakeSession(name, agent=agent)
        return self._sessions[name]


async def _client(monkeypatch, *, script=None) -> tuple[TestClient, _FakeState]:
    """A live server with `/v1` mounted and a scripted turn runner.

    ``script`` is the list of rows the "turn" queues for its reader. The dialect's job is
    to translate them, so scripting them is exactly the seam under test; the shapes are
    the frames ``chat_runner`` really queues for a live reader (``stream_chunk`` sends
    ``("chunk", text, "chunk")``, ``signal_done`` the terminal ``("done", "", "done")``).
    """
    rows = script if script is not None else [("chunk", "hello ", "chunk"), ("done", "", "done")]

    async def _fake_run(state, session, message):
        for role, content, cls in rows:
            session.append(role, content, cls)
            await asyncio.sleep(0)

    state = _FakeState()
    app = web.Application()
    app["state"] = state
    # Injected, exactly as `dashboard/routes.py` does it — so these tests exercise the
    # real wiring contract rather than a patched-out import.
    dialect.register_routes(app, turn_runner=_fake_run, agent_mover=_no_other_agent)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, state


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _body(model="researcher", **extra) -> str:
    payload = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    payload.update(extra)
    return json.dumps(payload)


# ── 1. The doorway answers, in both model spellings ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["researcher", "personalclaw/researcher"])
async def test_both_model_spellings_reach_the_agent(monkeypatch, model):
    """The dual model spelling, asserted at the ROUTE: a client with a dropdown that cannot
    send a slash must not be a second-class caller."""
    _enable(monkeypatch)
    token = _token()
    client, state = await _client(monkeypatch)
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(model), headers=_auth(token))
        assert resp.status == 200, await resp.text()
        payload = await resp.json()
    finally:
        await client.close()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "hello"
    assert payload["choices"][0]["finish_reason"] == "stop"
    assert "usage" in payload
    # The agent actually reached the session, rather than the default being assumed.
    assert next(iter(state._sessions.values())).agent == "researcher"


@pytest.mark.asyncio
async def test_non_stream_returns_exactly_one_completion(monkeypatch):
    """Non-stream waits and returns ONE completion — not a stream, not a list."""
    _enable(monkeypatch)
    token = _token()
    client, _ = await _client(
        monkeypatch,
        script=[
            ("chunk", "one ", "chunk"),
            ("chunk", "two", "chunk"),
            ("done", "", "done"),
        ],
    )
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(), headers=_auth(token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.content_type == "application/json"
    assert len(payload["choices"]) == 1
    assert payload["choices"][0]["message"]["content"] == "one two"


# ── 2. The unknown-agent 404, in the dialect's error shape ────────────────────


@pytest.mark.asyncio
async def test_unknown_agent_is_404_in_the_wire_error_shape(monkeypatch):
    """The clause a silent fallback would break.

    ``resolve_agent_bindings`` answers an unknown name with ``default_agent``, so
    without the membership pre-check this request would 200 with the WRONG agent's
    reply and the caller could not tell. Asserted on the route, and on every field an
    SDK parses — a 404 carrying the dashboard's ``{"error": "..."}`` string envelope
    raises an unhelpful parse error in a real client instead of an API error.
    """
    _enable(monkeypatch, agents=("researcher",))
    token = _token()
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(
            dialect.ROUTE_CHAT, data=_body("no-such-agent"), headers=_auth(token)
        )
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 404
    error = payload["error"]
    # The wire shape: an object with these three keys, NOT a bare string.
    assert isinstance(error, dict)
    assert set(("message", "type", "code")) <= set(error)
    assert error["code"] == "unknown_agent", "the stable code must survive the wire envelope"
    assert error["type"] == "invalid_request_error"
    assert "no-such-agent" in error["message"]


@pytest.mark.asyncio
async def test_binding_pin_beats_the_model_field(monkeypatch):
    """A request argument can never override a binding — 403, not a swap."""
    from personalclaw.inbound.clients import InboundClient

    cfg = _enable(monkeypatch, agents=("researcher", "writer"))
    pinned = InboundClient(client_id="c1", agent="researcher", surfaces=["openai"])
    monkeypatch.setattr(dialect, "_lookup_client", lambda presented: pinned)
    agent, refusal = dialect.resolve_agent("writer", pinned, cfg)
    assert agent == ""
    assert refusal is not None and refusal.status == 403
    # And the pin is honoured when the client names nothing at all.
    agent, refusal = dialect.resolve_agent("", pinned, cfg)
    assert (agent, refusal) == ("researcher", None)


# ── 3. SSE translation ────────────────────────────────────────────────────────


def _sse_frames(raw: str) -> tuple[list[dict], bool]:
    """The parsed `data:` frames and whether the `[DONE]` sentinel closed the stream."""
    frames: list[dict] = []
    done = False
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: ") :].strip()
        if payload == "[DONE]":
            done = True
            continue
        frames.append(json.loads(payload))
    return frames, done


@pytest.mark.asyncio
async def test_sse_chunks_then_usage_then_done(monkeypatch):
    """`chat.completion.chunk` frames, `usage` on the FINAL frame, `[DONE]` last.

    The usage placement is asserted specifically because a block carried on an earlier
    frame is one a budgeting client stops reading for.
    """
    _enable(monkeypatch)
    token = _token()
    client, _ = await _client(
        monkeypatch,
        script=[("chunk", "a", "chunk"), ("chunk", "b", "chunk"), ("done", "", "done")],
    )
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(stream=True), headers=_auth(token))
        assert resp.status == 200
        assert resp.content_type == "text/event-stream"
        raw = await resp.text()
    finally:
        await client.close()
    frames, done = _sse_frames(raw)
    assert done, "the [DONE] sentinel must close the stream"
    assert all(f["object"] == "chat.completion.chunk" for f in frames)
    text = "".join(f["choices"][0]["delta"].get("content", "") for f in frames)
    assert text == "ab"
    assert "usage" not in frames[0], "usage on a non-final frame is read too early"
    final = frames[-1]
    assert "usage" in final and set(final["usage"]) == {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
    assert final["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_tool_activity_never_becomes_tool_calls_deltas(monkeypatch):
    """The caller is not the tool executor.

    The scripted transcript CONTAINS tool and permission rows, so this test would catch
    a translation that began passing them through — a version that simply never sees a
    tool row would pass vacuously.
    """
    _enable(monkeypatch)
    token = _token()
    client, _ = await _client(
        monkeypatch,
        script=[
            ("chunk", "checking", "chunk"),
            ("tool", "memory_recall(...)", "tool"),
            ("chunk", " done", "chunk"),
            ("done", "", "done"),
        ],
    )
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(stream=True), headers=_auth(token))
        raw = await resp.text()
    finally:
        await client.close()
    assert "tool_calls" not in raw, "tool calls execute server-side and never reach the wire"
    frames, _ = _sse_frames(raw)
    assert all("tool_calls" not in f["choices"][0]["delta"] for f in frames)
    text = "".join(f["choices"][0]["delta"].get("content", "") for f in frames)
    assert text == "checking done"


@pytest.mark.asyncio
async def test_needs_approval_returns_the_dashboard_pointer_and_stops(monkeypatch):
    """A one-shot HTTP caller must be answered, not held open for a human."""
    _enable(monkeypatch)
    token = _token()
    client, _ = await _client(
        monkeypatch,
        script=[
            ("chunk", "I need to write a file.", "chunk"),
            ("permission", "write_file", "permission"),
            ("done", "", "done"),
        ],
    )
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(), headers=_auth(token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 200
    content = payload["choices"][0]["message"]["content"]
    assert "dashboard" in content.lower()
    assert dialect.APPROVAL_NOTICE in content
    assert payload["choices"][0]["finish_reason"] == "stop"


# ── 4. Session mapping ────────────────────────────────────────────────────────


def test_session_key_shape_and_hashing():
    """``inbound:<client_id>:<sha8>`` — and the caller's tag is HASHED.

    The hash is not cosmetic: the tag is external input and the key becomes a filename,
    a log field and a guardrail identity, so a traversal-shaped tag must not survive
    into it.
    """
    key = dialect.session_key_for("c1", "alice")
    assert key.startswith("inbound:c1:")
    assert len(key.split(":")[2]) == 8
    assert dialect.session_key_for("c1", "alice") == key, "same tag, same session"
    assert dialect.session_key_for("c1", "bob") != key, "different tag, different session"
    nasty = dialect.session_key_for("c1", "../../etc/passwd")
    assert "/" not in nasty and ".." not in nasty


def test_the_key_middle_segment_is_the_spend_scope():
    """Why the key shape is load-bearing beyond identity.

    ``chat_handlers._run_chat_scoped`` reads segment 1 of an ``inbound:`` key as the
    SpendMeter run scope. That is what makes the budget PER-CLIENT, so a change to the
    key shape silently relabels every client's spend into one bucket.
    """
    key = dialect.session_key_for("acme-bot", "alice")
    assert key.split(":")[1] == "acme-bot"


def test_unattended_classification_covers_this_key():
    """Every turn through this surface resolves to HEADLESS by construction.

    ``SafetyProfile`` is a dataclass, not an enum, so the profile is identified by
    ``.name`` — and the interactive case is asserted beside it, because a
    ``profile_for_session`` that returned the headless profile for EVERYTHING would
    satisfy the first assertion alone.
    """
    from personalclaw.constants import dashboard_session_key
    from personalclaw.guardrails.policy import is_unattended_session, profile_for_session

    key = dialect.session_key_for("c1", "alice")
    assert is_unattended_session(key)
    assert profile_for_session(key).name == "headless"
    assert profile_for_session("mychat").name == "interactive", "vacuity floor"
    # The wrapped provider form too — the posture must not depend on who is asking.
    assert is_unattended_session(dashboard_session_key(key))
    assert profile_for_session(dashboard_session_key(key)).name == "headless"


def test_session_tag_ignored_unless_the_client_opted_in(monkeypatch):
    """`user` and the header are honoured only behind ``persistent_sessions``."""
    from personalclaw.inbound.clients import InboundClient

    class _Req:
        headers = {dialect.SESSION_HEADER: "from-header"}

    stateless = InboundClient(client_id="c1", persistent_sessions=False)
    tag, persistent = dialect.session_tag_from({"user": "alice"}, _Req(), stateless)
    assert (tag, persistent) == (dialect.DEFAULT_SESSION_TAG, False)

    opted_in = InboundClient(client_id="c1", persistent_sessions=True)
    tag, persistent = dialect.session_tag_from({"user": "alice"}, _Req(), opted_in)
    assert (tag, persistent) == ("alice", True), "`user` wins the tie"
    tag, persistent = dialect.session_tag_from({}, _Req(), opted_in)
    assert (tag, persistent) == ("from-header", True), "the header is the escape hatch"


def test_persistent_sessions_parses_only_a_real_true(tmp_path):
    """A mangled record must not GRANT continuity.

    ``bool("false")`` is True, which for this flag would silently hand a standing
    session to a client whose record got corrupted — the inverse of ``disabled``, whose
    True is the closed position. Both directions are asserted so a later "consistency"
    fix to share one parse fails here.
    """
    import json as _json

    from personalclaw.inbound.clients import clients_path, load_clients

    path = clients_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _json.dumps(
            {
                "yes": {"persistent_sessions": True},
                "stringy": {"persistent_sessions": "false"},
                "absent": {},
            }
        )
    )
    loaded = load_clients()
    assert loaded["yes"].persistent_sessions is True
    assert loaded["stringy"].persistent_sessions is False, "a string must not grant continuity"
    assert loaded["absent"].persistent_sessions is False


@pytest.mark.asyncio
async def test_a_stand_alone_reset_lets_go_of_what_the_session_manager_holds(monkeypatch):
    """The reset reaches every place an earlier request lives on, under the key the session
    manager holds them by.

    The transcript is the session's; the runtime and the id an agent CLI resumes its
    conversation from are the session manager's, keyed by the name the session's turns run
    under (``dashboard_history_key``). The id used to be purged under the bare session name,
    which the manager never writes, so it survived every reset, and the runtime was not reached
    at all. Asserted on a real session manager, a session the reset does not name beside it.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.constants import dashboard_history_key
    from personalclaw.session import SessionManager, _Session

    _enable(monkeypatch)
    key = dialect.session_key_for("c1", "default")
    other = dashboard_history_key(dialect.session_key_for("c1", "kept"))
    manager = SessionManager(AppConfig())
    for name in (dashboard_history_key(key), other):
        manager._session_map.set(name, f"resume-{name}")
        manager._sessions[name] = _Session(provider=object())
    state = _FakeState()
    state.sessions = manager

    session = _FakeSession(key)
    session.append("assistant", "an earlier turn", "msg")
    session._owed_subagent_endings.append("a note an earlier turn's subagent left")
    dialect._reset_session(session, key, state)

    assert session.messages == [] and session._pending == []
    assert session._owed_subagent_endings == []
    assert manager._session_map.get(dashboard_history_key(key)) is None
    assert manager._sessions[dashboard_history_key(key)].forgotten
    # A session the reset does not name keeps its runtime and its id.
    assert manager._session_map.get(other) == f"resume-{other}"
    assert not manager._sessions[other].forgotten


@pytest.mark.asyncio
async def test_a_request_whose_reset_cannot_be_made_is_not_run(monkeypatch):
    """The reset is a control, so it fails closed: a request that cannot be started apart from
    the ones before it is refused in the wire's error shape, and its turn never starts."""
    _enable(monkeypatch)
    token = _token()
    client, state = await _client(monkeypatch)

    def _cannot(key: str) -> None:
        raise OSError("the session map could not be written")

    state.sessions.forget_conversation = _cannot
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(), headers=_auth(token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 503, payload
    assert payload["error"]["code"] == "service_unavailable"
    (session,) = state._sessions.values()
    assert session.task is None, "the turn was started"
    assert session._has_reader is False, "the session was left claimed"


# ── 5. GET /v1/models ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_models_lists_agents_and_never_provider_models(monkeypatch):
    """Agents only. A `/v1/models` answering with bound MODELS would turn the
    doorway into an outward proxy for them."""
    _enable(monkeypatch, agents=("researcher", "writer"))
    token = _token()
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.get(dialect.ROUTE_MODELS, headers=_auth(token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 200
    ids = [row["id"] for row in payload["data"]]
    assert ids == ["personalclaw/researcher", "personalclaw/writer"]
    assert all(row["owned_by"] == "personalclaw" for row in payload["data"])


def test_models_shows_a_pinned_client_only_its_own_agent(monkeypatch):
    """A client cannot discover an agent it would only be 403'd for selecting."""
    from personalclaw.inbound.clients import InboundClient

    cfg = _enable(monkeypatch, agents=("researcher", "writer"))
    assert dialect.visible_agents(InboundClient(client_id="c1", agent="writer"), cfg) == ["writer"]
    assert dialect.visible_agents(InboundClient(client_id="c1"), cfg) == ["researcher", "writer"]


# ── 6. Admission ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_surface_is_404_and_says_nothing(monkeypatch):
    """404 rather than 403, and the message must not describe what it is denying."""
    _enable(monkeypatch, enabled=False)
    token = _token()
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(), headers=_auth(token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 404
    assert payload["error"]["code"] == "not_found"
    body = json.dumps(payload).lower()
    for leak in ("external_access", "enabled", "token", "openai"):
        assert leak not in body, f"a disabled surface must not mention {leak!r}"


@pytest.mark.asyncio
async def test_master_switch_also_closes_the_doorway(monkeypatch):
    """Gate layer 1, asserted separately: the per-surface flag is ON here."""
    _enable(monkeypatch, enabled=True, master=False)
    token = _token()
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(), headers=_auth(token))
    finally:
        await client.close()
    assert resp.status == 404


@pytest.mark.asyncio
async def test_bad_bearer_is_401_in_the_wire_error_shape(monkeypatch):
    """An SDK must raise an authentication error, not a parse error."""
    _enable(monkeypatch)
    _token()
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(
            dialect.ROUTE_CHAT, data=_body(), headers=_auth("not-the-token" * 4)
        )
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 401
    # The GENERIC row, not a surface-specific one: the inbound-MCP section of
    # `HTTP_ERROR_CODES` records the rule that admission codes must not name this
    # surface or say which kill switch fired, because that hands a prober what the
    # status is chosen to withhold. The SDK classifies on the 401 status, so the
    # generic code costs a caller nothing.
    assert payload["error"]["code"] == "unauthorized"
    assert isinstance(payload["error"]["message"], str)


@pytest.mark.asyncio
async def test_incident_mode_is_503_not_404(monkeypatch):
    """Gate layer 4: an incident is temporary, so it must not tell a client "gone"."""
    _enable(monkeypatch)
    token = _token()
    monkeypatch.setattr("personalclaw.guardrails.incident.incident_active", lambda: True)
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(), headers=_auth(token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 503
    assert payload["error"]["type"] == "server_error"


# ── 6b. The injected turn runner ──────────────────────────────────────────────


def test_the_dialect_does_not_import_the_http_surface():
    """The ``core-must-not-import-the-http-surface`` inversion, pinned at the source.

    ``scripts/gate_report.py`` caught this on the first draft: the dialect imported
    ``chat_handlers._run_chat_scoped`` directly, an ``inbound/`` -> ``dashboard/`` edge.
    The gate is shrink-only and would grandfather the edge once a baseline was
    regenerated, so this assertion is the one that stays specific to THIS module.
    """
    source = _dialect_source()
    assert "personalclaw.dashboard" not in source, (
        "the dialect must not import the HTTP surface — the composition root injects "
        "the turn runner (see register_routes)"
    )


def test_the_composition_root_actually_injects_the_runner():
    """The call site, not the parameter.

    A required keyword argument proves the dialect ASKS for a runner; it does not prove
    anything hands one over. Without this, `register_routes` could be called nowhere and
    every `/v1` chat turn would 503 in production while the whole suite above stayed
    green on its own injected fake. The same holds for the change of a session's agent,
    which must be the one every dashboard door makes.
    """
    from personalclaw.dashboard import routes as routes_module

    source = Path(routes_module.__file__).read_text(encoding="utf-8")
    assert (
        "_register_openai(app, turn_runner=_run_chat_scoped, agent_mover=move_to_agent)" in source
    ), "dashboard/routes.py must inject the turn runner and the agent move into the /v1 dialect"
    assert "from personalclaw.dashboard.running_turn import move_to_agent" in source


@pytest.mark.asyncio
async def test_a_missing_agent_mover_is_an_honest_503(monkeypatch):
    """A request that needs its session moved to another agent, on a surface wired without the
    move, is refused rather than served by the agent the session already runs."""
    _enable(monkeypatch)
    token = _token()
    state = _FakeState()
    key = dialect.session_key_for(dialect.OPENAI_SURFACE, dialect.DEFAULT_SESSION_TAG)
    state.get_or_create_session(key, agent="writer")
    ran: list[str] = []

    async def _run(state, session, message):  # noqa: ANN001
        ran.append(message)

    app = web.Application()
    app["state"] = state
    dialect.register_routes(app, turn_runner=_run, agent_mover=None)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body("researcher"), headers=_auth(token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 503
    assert payload["error"]["code"] == "service_unavailable"
    assert ran == [] and state._sessions[key].agent == "writer"


@pytest.mark.asyncio
async def test_an_agent_cli_picked_for_the_session_does_not_answer_for_the_agent_named(
    monkeypatch,
):
    """A session the owner bound to a discovered agent CLI in the dashboard is answered by that
    CLI, not by its saved agent. A request naming the saved agent is moved to it, so the agent
    it names is what answers; the request for an unbound session moves nothing."""
    _enable(monkeypatch)
    token = _token()
    state = _FakeState()
    key = dialect.session_key_for(dialect.OPENAI_SURFACE, dialect.DEFAULT_SESSION_TAG)
    session = state.get_or_create_session(key, agent="researcher")
    session.acp_provider = "acp:example-cli"
    moved: list[str] = []

    async def _move(state, session, agent):  # noqa: ANN001
        moved.append(agent)
        session.agent, session.acp_provider = agent, ""
        return False

    async def _run(state, session, message):  # noqa: ANN001
        session.append("chunk", "ok", "chunk")
        session.append("done", "", "done")

    app = web.Application()
    app["state"] = state
    dialect.register_routes(app, turn_runner=_run, agent_mover=_move)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        first = await client.post(
            dialect.ROUTE_CHAT, data=_body("researcher"), headers=_auth(token)
        )
        assert first.status == 200, await first.text()
        again = await client.post(
            dialect.ROUTE_CHAT, data=_body("researcher"), headers=_auth(token)
        )
        assert again.status == 200, await again.text()
    finally:
        await client.close()
    assert moved == ["researcher"], "the saved agent named was not what answered"
    assert session.acp_provider == ""


@pytest.mark.asyncio
async def test_a_missing_runner_is_an_honest_503(monkeypatch):
    """A wiring failure must refuse, not raise a 500 or hang the caller."""
    _enable(monkeypatch)
    token = _token()
    state = _FakeState()
    app = web.Application()
    app["state"] = state
    dialect.register_routes(app, turn_runner=None, agent_mover=_no_other_agent)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.post(dialect.ROUTE_CHAT, data=_body(), headers=_auth(token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 503
    assert payload["error"]["code"] == "service_unavailable"


# ── 6c. Wire codes stay statically checkable through the wrapper ──────────────


def test_every_dialect_error_code_is_a_registered_literal():
    """The compensating rail for this module's one dynamic ``json_error`` site.

    ``openai_error`` forwards a keyword-only ``code`` into ``json_error``, so the
    shared census scanner (which follows ``json_response`` payload wrappers and
    module-level string constants, not forwarded code parameters) counts it as a site
    whose code it cannot read — and ``_DYNAMIC_CODE_SITE_CEILING`` was raised from 16
    to 17 for it. That ceiling exists to stop a COMPUTED code entering the wire
    unregistered, so this test restores exactly that guarantee at the level where the
    indirection actually is: every ``openai_error`` call passes a bare literal, and
    every one of those literals is in the append-only registry.

    Parsed with ``ast`` rather than grepped, so an f-string in the code slot is a
    failure here rather than a string that happens to match.
    """
    import ast

    from personalclaw.http_errors import HTTP_ERROR_CODES

    tree = ast.parse(_dialect_source())
    literals: list[str] = []
    computed: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "openai_error":
            continue
        code = next((kw.value for kw in node.keywords if kw.arg == "code"), None)
        if isinstance(code, ast.Constant) and isinstance(code.value, str):
            literals.append(code.value)
        else:
            computed.append(node.lineno)

    assert not computed, (
        "openai_error was called with a computed code at line(s) "
        f"{computed} — pass a literal so the registry check can see it"
    )
    # Vacuity floor: a matcher that found nothing would satisfy the assertion above.
    assert len(literals) >= 10, f"the matcher found only {len(literals)} call sites"
    unregistered = sorted({c for c in literals if c not in HTTP_ERROR_CODES})
    assert not unregistered, (
        "these dialect wire codes are not in the append-only registry — add them with "
        f"their one-line meaning in this change: {unregistered}"
    )


# ── 7. The no-provider-names rail ─────────────────────────────────────────────


def _dialect_source() -> str:
    return Path(dialect.__file__).read_text(encoding="utf-8")


def scan_for_provider_names(source: str) -> list[str]:
    """The rail itself, extracted so the vacuity case can call it on a mutated string.

    A rail that can only run against the real file cannot be proven able to fail.
    """
    lowered = source.lower()
    return sorted({name for name in BANNED_PROVIDER_NAMES if name in lowered})


def test_no_bindable_provider_name_appears_in_the_dialect():
    """The tenet, as a gate: `tts-1`/`whisper-1` must not make the code name a vendor.

    Scoped to THIS module on purpose. ``tts/registry.py`` legitimately mentions vendor
    voice personas in a docstring, and a repo-wide version of this rail would either be
    red forever or be weakened until it caught nothing. What is being pinned is that
    the DIALECT's own resolution path names no bindable provider — everything goes
    through ``resolve_voice`` / ``transcribe_audio``.
    """
    found = scan_for_provider_names(_dialect_source())
    assert found == [], f"provider names leaked into the dialect: {found}"


def test_the_rail_can_fail():
    """Vacuity proof. A guard nobody has watched go red is not known to be a guard.

    Both halves matter: a banned name is caught, and the surface's own protocol name is
    NOT — otherwise the rail would be unsatisfiable and the next person would delete it.
    """
    assert scan_for_provider_names("voice = piper_voice()") == ["piper"]
    assert scan_for_provider_names("x = faster-whisper") == ["faster-whisper"]
    assert scan_for_provider_names("OPENAI_SURFACE = 'openai'") == []
    assert scan_for_provider_names("model == 'tts-1'") == []


def test_cosmetic_aliases_are_accepted_and_discarded():
    """The aliases reach the code as strings and change nothing.

    Asserted structurally: the module never BRANCHES on them. A dialect that mapped
    ``tts-1`` to an engine would have to write the engine's name, which the rail above
    catches — but it could also branch on the alias to pick between two resolution
    seams, which the rail would not. This pins that too.
    """
    source = _dialect_source()
    for alias in ("tts-1", "whisper-1"):
        for pattern in (f'== "{alias}"', f"== '{alias}'", f'"{alias}":', f"'{alias}':"):
            assert pattern not in source, f"the dialect branches on the cosmetic alias {alias!r}"


def test_resolve_voice_is_the_only_voice_seam():
    """The voice seam: ONE function for profiles to be re-implemented against."""
    assert callable(dialect.resolve_voice)
    source = _dialect_source()
    # Every voice resolution in this module goes through the seam, so the count of
    # direct `active_voice_params` calls is exactly one — the seam's own body.
    assert source.count("active_voice_params(") == 1


# ── 8. Audio aliases ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_speech_returns_bound_provider_audio(monkeypatch):
    """`/v1/audio/speech` renders through whatever the user bound.

    The stub stands in for the bound provider; what is asserted is that the dialect
    hands it the params ``resolve_voice`` returned and streams its bytes back — it never
    selects an engine itself.
    """
    _enable(monkeypatch)
    token = _token()
    seen: dict = {}

    async def _fake_stream(provider, text, *, voice, speed, speech_voice):
        seen.update(provider=provider, text=text, voice=voice, speed=speed)
        yield 0, text, b"RIFF-fake-wav"

    monkeypatch.setattr(
        dialect,
        "resolve_voice",
        lambda name="", **kw: {
            "provider": "BOUND-PROVIDER-OBJECT",
            "voice": "bound-voice",
            "speed": 1.0,
            "speech_voice": "",
        },
    )
    monkeypatch.setattr("personalclaw.voice_reply.streaming_voice_reply", _fake_stream)
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(
            dialect.ROUTE_SPEECH,
            data=json.dumps({"model": "tts-1", "input": "hello there", "voice": "ignored"}),
            headers=_auth(token),
        )
        audio = await resp.read()
    finally:
        await client.close()
    assert resp.status == 200
    assert resp.content_type == "audio/wav"
    assert audio == b"RIFF-fake-wav"
    # The cosmetic `model` never reached provider selection.
    assert seen["provider"] == "BOUND-PROVIDER-OBJECT"
    assert seen["voice"] == "bound-voice"


@pytest.mark.asyncio
async def test_speech_without_a_bound_voice_is_503(monkeypatch):
    """No binding is an honest refusal, not a silent default engine."""
    _enable(monkeypatch)
    token = _token()
    monkeypatch.setattr(dialect, "resolve_voice", lambda name="", **kw: None)
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(
            dialect.ROUTE_SPEECH,
            data=json.dumps({"model": "tts-1", "input": "hi"}),
            headers=_auth(token),
        )
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 503
    assert payload["error"]["code"] == "no_bound_voice"


@pytest.mark.asyncio
async def test_speech_with_a_voice_its_provider_cannot_speak_is_503_naming_why(monkeypatch):
    """A bound voice the provider says it cannot speak with now is refused before synthesis, as
    a configuration answer, and not the "synthesis failed" fault a client would retry."""
    _enable(monkeypatch)
    token = _token()
    asked: list[str] = []

    class _NotReady:
        name = "fake-voice"
        display_name = "Fake Voice"

        async def can_synthesize(self, voice: str = "") -> bool:
            asked.append(voice)
            return False

        async def synthesize(self, *_a, **_kw):
            raise AssertionError("a voice that cannot speak was asked to")

    monkeypatch.setattr(
        dialect,
        "resolve_voice",
        lambda name="", **kw: {
            "provider": _NotReady(),
            "voice": "bound-voice",
            "speed": 1.0,
            "speech_voice": "",
        },
    )
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(
            dialect.ROUTE_SPEECH,
            data=json.dumps({"model": "tts-1", "input": "hi"}),
            headers=_auth(token),
        )
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 503
    assert payload["error"]["code"] == "tts_not_ready"
    assert "Fake Voice" in payload["error"]["message"]
    assert asked == ["bound-voice"]


@pytest.mark.asyncio
async def test_transcriptions_uses_the_bound_stt_and_ignores_the_model(monkeypatch):
    """`/v1/audio/transcriptions` is an alias over the bound STT provider."""
    _enable(monkeypatch)
    token = _token()
    seen: dict = {}

    async def _fake_transcribe(path):
        seen["path"] = path
        return "the transcript"

    monkeypatch.setattr("personalclaw.transcribe.is_available", lambda: _true())
    monkeypatch.setattr("personalclaw.transcribe.transcribe_audio", _fake_transcribe)
    client, _ = await _client(monkeypatch)
    try:
        form = {"model": "whisper-1", "file": _upload()}
        resp = await client.post(dialect.ROUTE_TRANSCRIPTIONS, data=form, headers=_auth(token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 200
    assert payload == {"text": "the transcript"}
    assert seen["path"].endswith(".webm")


async def _true() -> bool:
    return True


def _upload():
    import io

    buf = io.BytesIO(b"fake audio bytes")
    buf.name = "clip.webm"
    return buf


@pytest.mark.asyncio
async def test_transcriptions_503_when_no_stt_is_installed(monkeypatch):
    _enable(monkeypatch)
    token = _token()

    async def _false() -> bool:
        return False

    monkeypatch.setattr("personalclaw.transcribe.is_available", lambda: _false())
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(
            dialect.ROUTE_TRANSCRIPTIONS, data={"file": _upload()}, headers=_auth(token)
        )
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 503
    assert payload["error"]["code"] == "stt_unavailable"


@pytest.mark.asyncio
async def test_a_failed_transcription_relays_the_providers_sentence_as_it_is(monkeypatch):
    """🔴 Red before: "Transcription failed: " in front of a sentence that already says what
    failed and what to do."""
    from personalclaw.sdk.stt import SttError

    _enable(monkeypatch)
    token = _token()
    reason = "Amazon Transcribe couldn't transcribe this audio. Try again in a moment."

    async def _true() -> bool:
        return True

    async def _refused(_path):
        raise SttError(reason)

    monkeypatch.setattr("personalclaw.transcribe.is_available", lambda: _true())
    monkeypatch.setattr("personalclaw.transcribe.transcribe_audio", _refused)
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(
            dialect.ROUTE_TRANSCRIPTIONS, data={"file": _upload()}, headers=_auth(token)
        )
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 502
    assert (payload["error"]["code"], payload["error"]["message"]) == (
        "transcription_failed",
        reason,
    )


@pytest.mark.asyncio
async def test_a_transcription_that_came_back_with_nothing_is_a_failure_not_empty_text(
    monkeypatch,
):
    """🔴 Red before: ``{"text": ""}`` with a 200 — a failed transcription handed to a client as a
    recording with no speech. Driven through the real transcribe path with a bound provider."""
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.transcribe import NO_TRANSCRIPT_NO_REASON

    _enable(monkeypatch)
    token = _token()
    provider = MagicMock()
    provider.is_available = AsyncMock(return_value=True)
    provider.transcribe = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "personalclaw.stt.registry.active_stt", lambda: (provider, "fake-transcriber")
    )
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_use_case_settings", lambda _uc: {"enabled": True}
    )
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(
            dialect.ROUTE_TRANSCRIPTIONS, data={"file": _upload()}, headers=_auth(token)
        )
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 502
    assert (payload["error"]["code"], payload["error"]["message"]) == (
        "transcription_failed",
        NO_TRANSCRIPT_NO_REASON,
    )


@pytest.mark.asyncio
async def test_transcriptions_503_says_the_bound_providers_reason(monkeypatch):
    """🔴 Red before: "Install a transcription model" for a bound model whose provider knew it
    could not sign in."""
    _enable(monkeypatch)
    token = _token()
    reason = "No credentials were found for this account. Sign in, then try again."

    async def _false() -> bool:
        return False

    async def _why() -> str:
        return reason

    monkeypatch.setattr("personalclaw.transcribe.is_available", lambda: _false())
    monkeypatch.setattr("personalclaw.transcribe.unavailable_reason", lambda: _why())
    client, _ = await _client(monkeypatch)
    try:
        resp = await client.post(
            dialect.ROUTE_TRANSCRIPTIONS, data={"file": _upload()}, headers=_auth(token)
        )
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 503
    assert (payload["error"]["code"], payload["error"]["message"]) == ("stt_unavailable", reason)
