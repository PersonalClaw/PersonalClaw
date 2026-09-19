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


async def _noop_run(state, session, message):
    """Stand-in for the turn runner: `api_chat` schedules it as a task, and the routing
    hook fires beside that scheduling, not inside it."""
    return None


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

    def test_suppression_case_insensitive(self):
        now = time.time()
        assert not routing.is_suppressed("DBA", now=now, cooldown_hours=24.0)
        routing.record_dismiss("DBA", now=now)
        # Should be suppressed regardless of case
        assert routing.is_suppressed("dba", now=now + 3600, cooldown_hours=24.0)
        assert routing.is_suppressed("DbA", now=now + 3600, cooldown_hours=24.0)

        # Unmuting should also be case-insensitive
        for _ in range(2):
            routing.record_dismiss("dBa", now=now)
        assert "dba" in routing.routing_status()["muted"]
        routing.unmute("DbA")
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


class TestSuggestionReachesTheFirstMessage:
    """🔴 The suggestion must reach the send that CREATED the session (issue 569).

    `api_chat` broadcasts `routing_suggestion` synchronously — before the run task's
    first await — so it is the EARLIEST frame of a send. On a brand-new chat the
    frontend creates the session and navigates, which re-keys its ChatSession; that
    remount closes the socket that would have received the frame while its replacement
    is still handshaking. Measured against a live gateway: the broadcast reached every
    other socket on the page and NO chat socket, so the SEL audit recorded a suggestion
    as surfaced while the chip never rendered — on the one message where routing is most
    useful, because the user has not chosen a specialist yet.

    The fix ships the SAME payload on the send response, which exists only because the
    request did and therefore cannot be raced. Pinned here: the response carries it, and
    it is the same object as the broadcast — a second dict would be a second chance to
    name a different session, and a suggestion delivered to the WRONG chat is worse than
    one that is dropped.
    """

    @pytest.fixture(autouse=True)
    def _routing_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.providers.entity_routes.config_dir", lambda: tmp_path, raising=False
        )
        cfg = _cfg(
            {"dba": _profile("database expert", "optimize slow sql query, fix db index")},
            default="general",
        )
        monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", staticmethod(lambda: cfg))
        # The turn itself is not what is under test; a real run would need a model.
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_handlers._run_chat_scoped",
            _noop_run,
            raising=True,
        )
        yield

    @pytest.mark.asyncio
    async def test_ws_send_response_carries_the_broadcast_payload(self, tmp_path, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        from tests.chat_test_helpers import _make_app, _make_state

        state = _make_state(tmp_path)
        # `api_chat` refuses a key it has never seen (`session_key_exists`, 404
        # session_not_found) so that a stale tab or a retried send cannot resurrect a
        # deleted chat. The real first message is create-THEN-send, so seed the key the
        # way creation does — a log file is that predicate — rather than weakening it.
        state.conversation_log.append("s-new", "user", "seed")
        sent: list[tuple[str, object]] = []
        monkeypatch.setattr(
            state, "broadcast_ws", lambda t, d, **kw: sent.append((t, d)), raising=False
        )
        async with TestClient(TestServer(_make_app(state))) as c:
            resp = await c.post(
                "/api/chat?ws=1",
                json={"message": "please optimize slow sql query now", "session": "s-new"},
            )
            assert resp.status == 200
            body = await resp.json()

        broadcast = [d for t, d in sent if t == "routing_suggestion"]
        assert len(broadcast) == 1, "the live transport must still fire for other clients"
        assert body.get("routing_suggestion") == broadcast[0], (
            "the response and the broadcast must be the SAME payload — two dicts are two "
            "chances to name a different session"
        )
        assert body["routing_suggestion"]["session"] == body["session"] == "s-new"
        assert body["routing_suggestion"]["agent"] == "dba"

    @pytest.mark.asyncio
    async def test_no_suggestion_means_no_key_on_the_response(self, tmp_path, monkeypatch):
        """A send with nothing to suggest must not ship an empty/placeholder suggestion —
        the frontend keys the chip off the field's presence."""
        from aiohttp.test_utils import TestClient, TestServer

        from tests.chat_test_helpers import _make_app, _make_state

        state = _make_state(tmp_path)
        state.conversation_log.append("s-plain", "user", "seed")  # see the sibling test
        monkeypatch.setattr(state, "broadcast_ws", lambda *a, **kw: None, raising=False)
        async with TestClient(TestServer(_make_app(state))) as c:
            resp = await c.post(
                "/api/chat?ws=1", json={"message": "hello there", "session": "s-plain"}
            )
            assert resp.status == 200
            body = await resp.json()
        assert "routing_suggestion" not in body
