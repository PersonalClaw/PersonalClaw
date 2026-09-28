"""An effort the runtime declared it cannot honor must be refused, not persisted.

The composer already hides its effort pill when the bound agent declares no options
(``effortsForAgent`` → ``[]``), but both write paths accepted, PERSISTED and echoed back an
effort regardless: codex measured ``supported_efforts: []`` (`C2`) and a bind with
``reasoning_effort: "low"`` was still stored on the session and in its history metadata. A
control the provider cannot honor is worse than a missing one — it is a setting the user is
told took effect.

These tests drive the endpoints over a seeded record of the runtime's last Test (where the
agents it offers, and their declared efforts, are kept) rather than calling the validator
directly, because the defect was never in a validator: it was that no write path consulted
the declaration at all.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.chat import (
    api_chat_session_acp_agent,
    api_chat_session_reasoning_effort,
)
from personalclaw.dashboard.handlers import providers as providers_mod
from personalclaw.dashboard.state import DashboardState, _ChatSession

RUNTIME = "acp:codex"


_COMMAND = ["/nonexistent/codex-acp"]


@pytest.fixture(autouse=True)
def _tested_runtime():
    """The runtime these sessions bind to, registered for the duration: its declared efforts
    are read from its last Test, which is kept per registered runtime and the command it runs.
    Each test's home is its own, so a record never leaks into another test."""
    from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY
    from personalclaw.llm.registry import ProviderEntry, get_default_registry

    registry = get_default_registry()
    registry.unregister_entry(RUNTIME)
    registry.register_entry(
        ProviderEntry(
            name=RUNTIME,
            type="acp_agent",
            model="",
            options={"command": list(_COMMAND), "dialect": "codex"},
            credential=None,
            declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
        )
    )
    yield
    registry.unregister_entry(RUNTIME)


def _seed(runtime: str, efforts: list[dict] | None, *, agents: bool = True) -> None:
    """Record a Test of *runtime* the way the Test route writes one.

    ``efforts=None`` omits the key entirely (a record shape without the field);
    ``agents=False`` records a Test that did not list the agents, with why.
    """
    import json

    from personalclaw.agents import runtime_tests

    agent: dict = {"id": f"{runtime}/a", "name": "A"}
    if efforts is not None:
        agent["supported_efforts"] = efforts
    record = {
        "runtime_id": runtime,
        "command": list(_COMMAND),
        "tested_at": "2026-09-28T10:00:00+00:00",
        "ready": True,
        "state": "ready",
        "detail": "",
        "login_command": None,
        "agents": [agent] if agents else None,
        "discovery_error": "" if agents else "got an answer that could not be read: bad shape",
    }
    runtime_tests.record_path(runtime).write_text(json.dumps(record), encoding="utf-8")


def _app(state: DashboardState) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post(
        "/api/chat/sessions/{session}/reasoning-effort", api_chat_session_reasoning_effort
    )
    app.router.add_post("/api/chat/sessions/{session}/acp-agent", api_chat_session_acp_agent)
    return app


def _state(session: _ChatSession) -> DashboardState:
    state = MagicMock(spec=DashboardState)
    state._sessions = {session.key: session}
    state.push_sessions_update = MagicMock()
    state.sessions = MagicMock()
    state.sessions.reset = AsyncMock()
    state.conversation_log = None  # persistence is exercised elsewhere
    return state


def _bound_session(runtime: str = RUNTIME) -> _ChatSession:
    s = _ChatSession("test")
    s.acp_provider = runtime
    return s


# ── the accessor: [] and None are different facts ─────────────────────────────────


class TestDeclaredEfforts:
    def test_a_declaration_of_none_is_not_the_same_as_unknown(self):
        """The whole fix rests on this distinction: `[]` is a backend that was ASKED and
        reported no effort axis (refusable); `None` is a runtime nobody has tested (must fail
        open). Collapsing them either blocks every bind on an untested runtime or silently
        accepts an effort codex said it cannot honor."""
        assert providers_mod.declared_efforts(RUNTIME) is None, "untested must read unknown"
        _seed(RUNTIME, [])
        assert providers_mod.declared_efforts(RUNTIME) == [], "declared none must read as []"

    def test_the_rows_are_the_backends_verbatim_option_dicts(self):
        """`supported_efforts` rows are `{"value", "label", …}` — the shape the composer
        renders. Stringifying a row instead of reading `value` would compare an effort
        against "{'value': 'low', …}" and refuse every legitimate bind."""
        _seed(RUNTIME, [{"value": "low", "label": "Low"}, {"value": "xhigh", "label": "X"}])
        assert providers_mod.declared_efforts(RUNTIME) == ["low", "xhigh"]

    def test_a_cached_but_failed_discovery_reads_unknown_not_empty(self):
        _seed(RUNTIME, [], agents=False)
        assert providers_mod.declared_efforts(RUNTIME) is None

    def test_a_payload_predating_the_field_reads_unknown_not_empty(self):
        _seed(RUNTIME, None)
        assert providers_mod.declared_efforts(RUNTIME) is None


# ── POST /reasoning-effort ─────────────────────────────────────────────────────────


class TestPerTurnEndpointHonorsTheDeclaration:
    @pytest.mark.asyncio
    async def test_a_runtime_declaring_none_refuses_an_effort(self):
        _seed(RUNTIME, [])
        session = _bound_session()
        async with TestClient(TestServer(_app(_state(session)))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort", json={"reasoning_effort": "low"}
            )
            assert resp.status == 400
            body = await resp.json()
            assert RUNTIME in body["error"]["message"]
            assert "no reasoning-effort options" in body["error"]["message"]
        assert session.reasoning_effort == "", "the refused effort was persisted anyway"

    @pytest.mark.asyncio
    async def test_an_effort_outside_a_declared_set_is_refused_and_the_set_is_named(self):
        _seed(RUNTIME, [{"value": "low"}, {"value": "high"}])
        session = _bound_session()
        async with TestClient(TestServer(_app(_state(session)))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort", json={"reasoning_effort": "medium"}
            )
            assert resp.status == 400
            msg = (await resp.json())["error"]["message"]
            assert "low, high" in msg and "'medium'" in msg
        assert session.reasoning_effort == ""

    @pytest.mark.asyncio
    async def test_a_declared_effort_is_accepted(self):
        _seed(RUNTIME, [{"value": "low"}, {"value": "high"}])
        session = _bound_session()
        async with TestClient(TestServer(_app(_state(session)))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort", json={"reasoning_effort": "high"}
            )
            assert resp.status == 200
        assert session.reasoning_effort == "high"

    @pytest.mark.asyncio
    async def test_an_unknown_declaration_fails_OPEN(self):
        """An untested runtime must not make the control unusable — the format check is still
        the bar, and refusing here would break the picker until the runtime's first Test."""
        session = _bound_session()
        async with TestClient(TestServer(_app(_state(session)))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort", json={"reasoning_effort": "low"}
            )
            assert resp.status == 200
        assert session.reasoning_effort == "low"

    @pytest.mark.asyncio
    async def test_clearing_is_always_allowed_even_when_the_runtime_declares_none(self):
        _seed(RUNTIME, [])
        session = _bound_session()
        session.reasoning_effort = "low"  # e.g. persisted before this rail existed
        async with TestClient(TestServer(_app(_state(session)))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort", json={"reasoning_effort": ""}
            )
            assert resp.status == 200, "a user must always be able to UNPIN"
        assert session.reasoning_effort == ""


# ── POST /acp-agent (the bind path) ───────────────────────────────────────────────


class TestBindPathHonorsTheDeclaration:
    @pytest.mark.asyncio
    async def test_binding_with_an_effort_the_runtime_declares_none_of_is_refused(self):
        _seed(RUNTIME, [])
        session = _ChatSession("test")
        async with TestClient(TestServer(_app(_state(session)))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/acp-agent",
                json={"provider": RUNTIME, "reasoning_effort": "low"},
            )
            assert resp.status == 400
            assert "no reasoning-effort options" in (await resp.json())["error"]["message"]
        assert session.reasoning_effort == ""
        assert session.acp_provider == "", "a refused bind must not half-apply"

    @pytest.mark.asyncio
    async def test_a_backend_declared_value_outside_the_old_ladder_is_now_accepted(self):
        """The bind path used to enforce a hardcoded ``low/medium/high/max``, so a backend
        offering ``xhigh`` had its own value refused — and the per-turn endpoint, which has
        no fixed scale, accepted it. The two paths now apply the same bar."""
        _seed(RUNTIME, [{"value": "xhigh", "label": "Extra high"}])
        session = _ChatSession("test")
        async with TestClient(TestServer(_app(_state(session)))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/acp-agent",
                json={"provider": RUNTIME, "reasoning_effort": "xhigh"},
            )
            assert resp.status == 200, await resp.text()
            assert (await resp.json())["reasoning_effort"] == "xhigh"
        assert session.reasoning_effort == "xhigh"

    @pytest.mark.asyncio
    async def test_a_malformed_token_is_still_refused_on_the_format_bar(self):
        _seed(RUNTIME, [{"value": "low"}])
        session = _ChatSession("test")
        async with TestClient(TestServer(_app(_state(session)))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/acp-agent",
                json={"provider": RUNTIME, "reasoning_effort": "low; rm -rf /"},
            )
            assert resp.status == 400
            assert "short lowercase token" in (await resp.json())["error"]["message"]
