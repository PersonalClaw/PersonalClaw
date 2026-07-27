"""Agent routing (AGENT-ROUTING S1) — the deterministic classifier + suppression
store + the api_chat suggestion hook. Suggest-first, LLM never in the hot path."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from personalclaw.agents import routing


def _profile(specialty="", route_hints=""):
    return SimpleNamespace(specialty=specialty, route_hints=route_hints)


def _cfg(
    agents: dict, *, enabled=True, min_confidence=0.62, cooldown_hours=24.0, default="PersonalClaw"
):
    return SimpleNamespace(
        agents=agents,
        default_agent=default,
        agents_routing=SimpleNamespace(
            enabled=enabled, min_confidence=min_confidence, cooldown_hours=cooldown_hours
        ),
    )


class TestEligibleCandidates:
    def test_only_agents_with_metadata_and_not_reserved(self):
        cfg = _cfg(
            {
                "researcher": _profile(specialty="deep web research", route_hints="research this"),
                "bare": _profile(),  # no metadata → excluded
                "personalclaw-lite": _profile(
                    specialty="x", route_hints="y"
                ),  # reserved → excluded
            }
        )
        names = [c[0] for c in routing.eligible_candidates(cfg)]
        assert names == ["researcher"]


class TestClassify:
    def test_keyword_hit_needs_3word_phrase_and_margin(self):
        cands = [
            ("dba", "database expert", "optimize this slow sql query, fix the database index"),
            ("writer", "prose editor", "edit my essay, improve the writing"),
        ]
        # embed_fn None → keyword-only path. A clear ≥3-word phrase match.
        r = routing.classify("please optimize this slow sql query for me", cands)
        assert r is not None and r.agent == "dba" and r.method == "keyword"

    def test_short_message_does_not_spuriously_route(self, monkeypatch):
        # A 2-word hint phrase can hit ratio 1.0 but must be rejected (min 3 words).
        cands = [("dba", "db", "sql"), ("writer", "prose", "essay")]
        assert routing.classify("sql", cands) is None

    def test_no_candidates_or_empty_message(self):
        assert routing.classify("", [("a", "s", "h")]) is None
        assert routing.classify("hello", []) is None

    def test_embedding_hit_beats_keyword_when_bound(self, monkeypatch):
        # Orthogonal vectors: query aligns with "dba".
        vecs = {
            "run the deployment pipeline": [0.0, 1.0],
            "database expert optimize slow sql query": [1.0, 0.0],
            "devops deploy releases pipelines": [0.0, 1.0],
        }
        monkeypatch.setattr(routing, "_embed", lambda t: (vecs.get(t), "test:model"))
        cands = [
            ("dba", "database expert", "optimize slow sql query"),
            ("devops", "devops", "deploy releases pipelines"),
        ]
        r = routing.classify("run the deployment pipeline", cands, embed_cache={})
        assert r is not None and r.agent == "devops" and r.method == "embedding"

    def test_low_margin_stays_silent(self, monkeypatch):
        # Two near-identical vectors → margin < 0.1 → no suggestion.
        monkeypatch.setattr(routing, "_embed", lambda t: ([1.0, 0.0], "test:model"))
        cands = [("a", "sa", "ha"), ("b", "sb", "hb")]
        assert routing.classify("anything", cands, embed_cache={}) is None


class TestSuppressionStore:
    @pytest.fixture(autouse=True)
    def _tmp_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.providers.entity_routes.config_dir", lambda: tmp_path, raising=False
        )
        # entity settings path derives from config_dir; ensure a clean dir
        yield

    def test_dismiss_cooldown_then_mute(self):
        now = time.time()
        assert not routing.is_suppressed("dba", now=now, cooldown_hours=24.0)
        routing.record_dismiss("dba", now=now)
        # within cooldown → suppressed
        assert routing.is_suppressed("dba", now=now + 3600, cooldown_hours=24.0)
        # past cooldown → not suppressed (count 1, not muted)
        assert not routing.is_suppressed("dba", now=now + 25 * 3600, cooldown_hours=24.0)
        # three cumulative dismissals → muted regardless of cooldown
        routing.record_dismiss("dba", now=now)
        st = routing.record_dismiss("dba", now=now)
        assert st["muted"] is True
        assert routing.is_suppressed("dba", now=now + 999 * 3600, cooldown_hours=24.0)

    def test_unmute_clears(self):
        now = time.time()
        for _ in range(3):
            routing.record_dismiss("dba", now=now)
        assert "dba" in routing.routing_status()["muted"]
        routing.unmute("dba")
        assert "dba" not in routing.routing_status()["muted"]
        assert not routing.is_suppressed("dba", now=now, cooldown_hours=24.0)


class TestSuggestForSend:
    @pytest.fixture(autouse=True)
    def _tmp_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.providers.entity_routes.config_dir", lambda: tmp_path, raising=False
        )
        yield

    def _session(self, *, agent="", memory_mode="persistent", user_turns=1):
        msgs = [{"role": "user", "content": f"q{i}"} for i in range(user_turns)]
        return SimpleNamespace(key="s1", agent=agent, memory_mode=memory_mode, messages=msgs)

    def _patch_cfg(self, monkeypatch, cfg):
        monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", staticmethod(lambda: cfg))

    def test_suggests_in_default_chat(self, monkeypatch):
        cfg = _cfg({"dba": _profile("database expert", "optimize slow sql query, fix db index")})
        self._patch_cfg(monkeypatch, cfg)
        state = SimpleNamespace()
        r = routing.suggest_for_send(state, self._session(), "please optimize slow sql query now")
        assert r is not None and r.agent == "dba"

    def test_disabled_config_no_suggestion(self, monkeypatch):
        cfg = _cfg({"dba": _profile("db", "optimize slow sql query")}, enabled=False)
        self._patch_cfg(monkeypatch, cfg)
        assert (
            routing.suggest_for_send(SimpleNamespace(), self._session(), "optimize slow sql query")
            is None
        )

    def test_explicit_agent_session_no_suggestion(self, monkeypatch):
        cfg = _cfg({"dba": _profile("db", "optimize slow sql query")})
        self._patch_cfg(monkeypatch, cfg)
        sess = self._session(agent="some-other-agent")
        assert routing.suggest_for_send(SimpleNamespace(), sess, "optimize slow sql query") is None

    def test_incognito_no_suggestion(self, monkeypatch):
        cfg = _cfg({"dba": _profile("db", "optimize slow sql query")})
        self._patch_cfg(monkeypatch, cfg)
        sess = self._session(memory_mode="incognito")
        assert routing.suggest_for_send(SimpleNamespace(), sess, "optimize slow sql query") is None

    def test_frequency_cap(self, monkeypatch):
        cfg = _cfg({"dba": _profile("database expert", "optimize slow sql query, fix db index")})
        self._patch_cfg(monkeypatch, cfg)
        state = SimpleNamespace()
        msg = "please optimize slow sql query now"
        first = routing.suggest_for_send(state, self._session(user_turns=1), msg)
        assert first is not None
        # immediate next turn (turn 2) is inside the cap → no suggestion
        assert routing.suggest_for_send(state, self._session(user_turns=2), msg) is None

    def test_suppressed_agent_no_suggestion(self, monkeypatch):
        cfg = _cfg({"dba": _profile("database expert", "optimize slow sql query, fix db index")})
        self._patch_cfg(monkeypatch, cfg)
        routing.record_dismiss("dba", now=time.time())
        assert (
            routing.suggest_for_send(
                SimpleNamespace(), self._session(), "optimize slow sql query now"
            )
            is None
        )
