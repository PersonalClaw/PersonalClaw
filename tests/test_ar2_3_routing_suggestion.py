"""AR2-3 — the routing_suggestion emission path, end to end.

The decision gate (`routing.suggest_for_send`) is covered by test_agent_routing.py; this file
closes the three clauses that file did not observe:
  * the actual `routing_suggestion` WS broadcast fires (and only in a default+persistent chat),
  * a raising classifier never breaks the send,
  * `temporary` memory mode is suppressed the same as `incognito`.
The broadcast tests drive the real `api_chat` handler through the shared chat harness with the
model call stubbed, so no bound provider is needed (a keyword match produces the candidate).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.agents import routing


def _profile(specialty="", route_hints=""):
    return SimpleNamespace(specialty=specialty, route_hints=route_hints)


def _cfg(agents: dict, *, enabled=True, default="PersonalClaw"):
    return SimpleNamespace(
        agents=agents,
        default_agent=default,
        agents_routing=SimpleNamespace(enabled=enabled, min_confidence=0.62, cooldown_hours=24.0),
    )


_DBA = {"dba": _profile("database expert", "optimize slow sql query, fix db index")}
_MSG = "please optimize slow sql query now"


class TestSuggestForSendGuards:
    """The two decision-gate clauses test_agent_routing.py left open."""

    @pytest.fixture(autouse=True)
    def _tmp_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.providers.entity_routes.config_dir", lambda: tmp_path, raising=False
        )
        yield

    def _session(self, *, agent="", memory_mode="persistent"):
        return SimpleNamespace(
            key="s1",
            agent=agent,
            memory_mode=memory_mode,
            messages=[{"role": "user", "content": "q"}],
        )

    def test_a_raising_classifier_never_breaks_the_send(self, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.config.loader.AppConfig.load", staticmethod(lambda: _cfg(_DBA))
        )

        def boom(*a, **k):
            raise RuntimeError("classifier exploded")

        monkeypatch.setattr(routing, "classify", boom)
        # The outer guard in suggest_for_send must swallow it and return None, not propagate.
        assert routing.suggest_for_send(SimpleNamespace(), self._session(), _MSG) is None

    def test_temporary_mode_is_suppressed_like_incognito(self, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.config.loader.AppConfig.load", staticmethod(lambda: _cfg(_DBA))
        )
        # `temporary` shares the non-persistent gate; assert it independently (only `incognito`
        # was pinned before, so a regression narrowing the gate to != "incognito" would slip).
        sess = self._session(memory_mode="temporary")
        assert routing.suggest_for_send(SimpleNamespace(), sess, _MSG) is None


@pytest.mark.asyncio
class TestRoutingSuggestionBroadcast:
    """The on-the-wire clause: api_chat emits `routing_suggestion` when (and only when) the
    default+persistent chat's message fits an installed specialist."""

    async def _drive(self, tmp_path, monkeypatch, *, agent, memory_mode):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.providers.entity_routes.config_dir", lambda: tmp_path, raising=False
        )
        monkeypatch.setattr(
            "personalclaw.config.loader.AppConfig.load", staticmethod(lambda: _cfg(_DBA))
        )

        async def fake_run_chat(st, sl, msg):
            return

        monkeypatch.setattr("personalclaw.dashboard.chat_handlers.run_chat", fake_run_chat)

        broadcasts: list[tuple[str, dict]] = []
        state = _make_state(tmp_path)
        state.broadcast_ws = lambda kind, payload=None, **k: broadcasts.append((kind, payload))
        session = state.get_or_create_session("s1")
        session.agent = agent
        session.memory_mode = memory_mode

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat", json={"message": _MSG, "session": "s1"})
            resp.close()
        return [p for (kind, p) in broadcasts if kind == "routing_suggestion"]

    async def test_broadcast_fires_on_keyword_match_in_default_chat(self, tmp_path, monkeypatch):
        suggestions = await self._drive(tmp_path, monkeypatch, agent="", memory_mode="persistent")
        assert len(suggestions) == 1
        assert suggestions[0]["agent"] == "dba"
        assert suggestions[0]["session"] == "s1"
        assert suggestions[0]["method"] == "keyword"

    async def test_no_broadcast_for_an_explicit_agent_session(self, tmp_path, monkeypatch):
        # Explicit-agent sessions opt out of routing — the same send must emit nothing.
        suggestions = await self._drive(
            tmp_path, monkeypatch, agent="some-specialist", memory_mode="persistent"
        )
        assert suggestions == []
