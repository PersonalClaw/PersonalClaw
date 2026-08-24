"""ACP-AGENT-PARITY §2.6 gap 10 (atom ``AAP-9``) — an ACP save stamps its Project.

The defect, precisely: ``artifact_save`` stamps ``project_id=_current_project_id()``, which
read ONLY the native runtime's per-turn contextvar. An ACP CLI's tools run in a separate
``personalclaw mcp-core`` process where that contextvar is empty by construction — and
``provider_bridge`` pops ``project_id`` unconditionally, handing it to the native builder
alone, so nothing about the binding crosses into the ACP branch either. Every ACP save
therefore filed an artifact with no project and it never appeared on its Project page.

The fix resolves the binding SERVER-SIDE from the session key, which already crosses to
that process. Three properties are worth more than the happy path and are pinned here:

* the native path must never reach the HTTP fallback — it runs INSIDE the gateway, so a
  blocking self-request is at best wasted and at worst unservable;
* an unscoped session must stamp "" and never a default project (filing work under a
  project the user never chose is worse than an unstamped artifact);
* the endpoint keys off ``X-Session-Key`` and never off a caller-supplied name, so it
  cannot be turned into a cross-session read of someone else's binding.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import personalclaw.mcp_artifacts as ma
from personalclaw.mcp_core import _CURRENT_SESSION_KEY


class TestNativePathNeverAsksTheGateway:
    def test_a_bound_contextvar_short_circuits_before_any_http(self, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.agents.native.builtin_tools.current_project_id", lambda: "p-native"
        )
        called: list[str] = []
        monkeypatch.setattr(ma, "_session_bound_project_id", lambda: called.append("http") or "")
        assert ma._current_project_id() == "p-native"
        assert called == [], "the native path must not fall through to the gateway read"

    def test_in_process_native_with_no_project_returns_empty_without_asking(self, monkeypatch):
        """The load-bearing guard. An unscoped NATIVE turn has an empty project contextvar
        but a live session key, so without the in-process check it would issue a blocking
        GET from inside the gateway — the gateway waiting on itself."""
        monkeypatch.setattr(
            "personalclaw.agents.native.builtin_tools.current_project_id", lambda: ""
        )
        calls: list[str] = []
        monkeypatch.setattr(
            "personalclaw.mcp_core._get", lambda path: calls.append(path) or {"project_id": "p-x"}
        )
        token = _CURRENT_SESSION_KEY.set("dashboard:native-turn")
        try:
            assert ma._current_project_id() == ""
        finally:
            _CURRENT_SESSION_KEY.reset(token)
        assert calls == [], f"in-process turn issued a self-request: {calls}"


class TestAcpPathResolvesFromTheSessionKey:
    @pytest.fixture(autouse=True)
    def _out_of_process(self, monkeypatch):
        """No native contextvars: exactly the state of an ACP CLI's mcp-core process."""
        monkeypatch.setattr(
            "personalclaw.agents.native.builtin_tools.current_project_id", lambda: ""
        )
        token = _CURRENT_SESSION_KEY.set("")
        yield
        _CURRENT_SESSION_KEY.reset(token)

    def test_the_bound_project_is_stamped(self, monkeypatch):
        monkeypatch.setattr(ma, "_resolve_session_key", lambda: "dashboard:acp-chat")
        seen: list[str] = []
        monkeypatch.setattr(
            "personalclaw.mcp_core._get",
            lambda path: seen.append(path) or {"project_id": "p-acp"},
        )
        assert ma._current_project_id() == "p-acp"
        assert seen == ["/api/chat/sessions/bound-project"]

    def test_an_unscoped_session_stamps_nothing(self, monkeypatch):
        """A 200 with an empty id is the normal unscoped answer — not an error, and not a
        reason to substitute a default."""
        monkeypatch.setattr(ma, "_resolve_session_key", lambda: "dashboard:acp-chat")
        monkeypatch.setattr("personalclaw.mcp_core._get", lambda path: {"project_id": ""})
        assert ma._current_project_id() == ""

    def test_no_session_identity_asks_nothing(self, monkeypatch):
        monkeypatch.setattr(ma, "_resolve_session_key", lambda: "")
        calls: list[str] = []
        monkeypatch.setattr(
            "personalclaw.mcp_core._get", lambda path: calls.append(path) or {"project_id": "p"}
        )
        assert ma._current_project_id() == ""
        assert calls == []

    def test_a_gateway_error_stamps_nothing_and_does_not_raise(self, monkeypatch):
        """``_get`` reports transport failure as ``{"error": ...}`` rather than raising, so
        an unreachable gateway must not become a stamped project OR a failed save."""
        monkeypatch.setattr(ma, "_resolve_session_key", lambda: "dashboard:acp-chat")
        monkeypatch.setattr(
            "personalclaw.mcp_core._get", lambda path: {"error": "Connection refused"}
        )
        assert ma._current_project_id() == ""

    def test_a_raising_bridge_stamps_nothing(self, monkeypatch):
        monkeypatch.setattr(ma, "_resolve_session_key", lambda: "dashboard:acp-chat")

        def _boom(path):
            raise RuntimeError("socket exploded")

        monkeypatch.setattr("personalclaw.mcp_core._get", _boom)
        assert ma._current_project_id() == ""


class TestTheEndpointKeysOffTheHeaderOnly:
    """`api_chat_session_bound_project` is reachable by any caller holding the internal
    secret, so the session it answers for must be the one it can PROVE it is."""

    def _request(self, headers: dict, sessions: dict):
        state = SimpleNamespace(_sessions=sessions)
        return SimpleNamespace(app={"state": state}, headers=headers)

    async def _call(self, request):
        from personalclaw.dashboard.chat_handlers import api_chat_session_bound_project

        resp = await api_chat_session_bound_project(request)
        import json

        return json.loads(resp.body.decode())

    @pytest.mark.asyncio
    async def test_the_header_names_the_session(self):
        sessions = {"a": SimpleNamespace(project_id="p-a")}
        got = await self._call(self._request({"X-Session-Key": "dashboard:a"}, sessions))
        assert got == {"project_id": "p-a"}

    @pytest.mark.asyncio
    async def test_an_absent_header_is_an_empty_id_not_an_error(self):
        got = await self._call(self._request({}, {"a": SimpleNamespace(project_id="p-a")}))
        assert got == {"project_id": ""}

    @pytest.mark.asyncio
    async def test_the_ui_pseudo_session_resolves_to_nothing(self):
        """``dashboard:ui`` is the browser's own key, not a chat session — the same
        exclusion ``handlers/context._session_project_id`` makes."""
        got = await self._call(
            self._request(
                {"X-Session-Key": "dashboard:ui"}, {"ui": SimpleNamespace(project_id="p")}
            )
        )
        assert got == {"project_id": ""}

    @pytest.mark.asyncio
    async def test_an_unknown_session_resolves_to_nothing(self):
        got = await self._call(self._request({"X-Session-Key": "dashboard:ghost"}, {}))
        assert got == {"project_id": ""}

    @pytest.mark.asyncio
    async def test_a_session_with_no_project_resolves_to_empty_never_a_default(self):
        sessions = {"a": SimpleNamespace(project_id="")}
        got = await self._call(self._request({"X-Session-Key": "dashboard:a"}, sessions))
        assert got == {"project_id": ""}


def test_the_literal_route_is_registered_before_the_dynamic_one():
    """Vacuity floor for the route itself: registered AFTER ``{session}`` it would be
    captured as a session named "bound-project" and the endpoint would be unreachable —
    the exact hazard ``bulk``/``templates`` are commented for in ``server.py``."""
    import pathlib

    import personalclaw

    src = (pathlib.Path(personalclaw.__file__).parent / "dashboard" / "server.py").read_text()
    lit = src.index('add_get("/api/chat/sessions/bound-project"')
    dyn = src.index('add_get("/api/chat/sessions/{session}"')
    assert lit < dyn, "bound-project must register before the {session} pattern"
