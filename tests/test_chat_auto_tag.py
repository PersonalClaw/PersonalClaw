"""Tests for AI auto-tagging — title-time tagging (chat_title) + magic re-tag batch (chat_retag)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat_retag import (
    _apply_tags,
    _Candidate,
    _collect_candidates,
    _parse_retag_reply,
    _resolve_tag_ids,
    api_retag_all,
    api_retag_cancel,
    api_retag_status,
)
from personalclaw.dashboard.chat_tags import create_tag, find_tag_by_name
from personalclaw.dashboard.chat_title import (
    _apply_auto_tags,
    _build_tags_suffix,
    _maybe_auto_title,
    _parse_tags_line,
)
from personalclaw.dashboard.state import _ChatSession
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent


def _state_with_tags(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state._tags = [
        {"id": "t-work", "name": "Work", "color": "#111111", "order": 0, "status": False},
        {"id": "t-done", "name": "Done", "color": "#10b981", "order": 1, "status": True},
        {"id": "t-prog", "name": "In-Progress", "color": "#3b82f6", "order": 2, "status": True},
    ]
    return state


def _mock_title_stream(state, text):
    """Wire state.sessions.get_or_create to a client streaming *text*."""
    client = MagicMock()
    client.reject_tool = AsyncMock()

    async def _stream(prompt):
        _stream.last_prompt = prompt
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield LLMEvent(kind=EVENT_COMPLETE)

    client.stream = _stream
    state.sessions.get_or_create = AsyncMock(return_value=(client, False, False))
    state.sessions.release = MagicMock()
    return _stream


# ── Shared tag helpers (chat_tags) ──────────────────────────────────────────


class TestTagHelpers:
    def test_find_tag_by_name_case_insensitive(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        assert find_tag_by_name(state, "work")["id"] == "t-work"
        assert find_tag_by_name(state, "  WORK ")["id"] == "t-work"
        assert find_tag_by_name(state, "nope") is None
        assert find_tag_by_name(state, "") is None

    def test_create_tag_same_shape_as_ui_path(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        tag = create_tag(state, "Research")
        assert tag is not None
        assert set(tag) == {"id", "name", "color", "order", "status"}
        assert len(tag["id"]) == 12
        assert tag["order"] == 3  # appended after existing
        assert tag in state._tags
        # persisted to disk via save_tags
        saved = json.loads((tmp_path / "tags.json").read_text())
        assert any(t["name"] == "Research" for t in saved)

    def test_create_tag_rejects_empty(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        assert create_tag(state, "   ") is None


# ── Feature A: title-time auto-tagging ──────────────────────────────────────


class TestParseTagsLine:
    def test_parses_names(self):
        assert _parse_tags_line("My Title\nTAGS: Work, Research") == ["Work", "Research"]

    def test_none_marker(self):
        assert _parse_tags_line("Title\nTAGS: none") == []

    def test_missing_line(self):
        assert _parse_tags_line("Just a title") == []

    def test_dedupes_and_caps(self):
        out = _parse_tags_line("T\nTAGS: a, A, b, c, d, e")
        assert out == ["a", "b", "c", "d"]  # 4 max, case-insensitive dedupe

    def test_drops_oversized_names(self):
        assert _parse_tags_line("T\nTAGS: " + "x" * 50 + ", ok") == ["ok"]


class TestBuildTagsSuffix:
    def test_lists_existing_tags(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        suffix = _build_tags_suffix(state)
        assert "Work" in suffix and "Done" in suffix and "TAGS:" in suffix


class TestApplyAutoTags:
    def test_assigns_existing_and_creates_new(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        state._sessions["s1"] = session
        assigned = _apply_auto_tags(state, session, ["Work", "Brand New"])
        assert len(assigned) == 2
        assert assigned[0] == "t-work"
        new_tag = find_tag_by_name(state, "Brand New")
        assert new_tag is not None and new_tag["id"] == assigned[1]
        assert session.tags == assigned

    def test_caps_new_tag_creation_at_two(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        state._sessions["s1"] = session
        assigned = _apply_auto_tags(state, session, ["N1", "N2", "N3", "N4"])
        assert len(assigned) == 2  # only 2 new tags may be created
        assert find_tag_by_name(state, "N3") is None

    def test_skips_restricted_session(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1", memory_mode="incognito")
        assert _apply_auto_tags(state, session, ["Work"]) == []
        assert session.tags == []

    def test_skips_already_tagged_session(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        session.tags = ["t-done"]
        assert _apply_auto_tags(state, session, ["Work"]) == []
        assert session.tags == ["t-done"]


class TestMaybeAutoTitleTagging:
    @pytest.mark.asyncio
    async def test_title_and_tags_from_one_call(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        session.messages = [
            {"role": "user", "content": "help me plan the offsite"},
            {"role": "assistant", "content": "sure"},
        ]
        state._sessions["s1"] = session
        stream = _mock_title_stream(state, "Offsite Planning\nTAGS: Work, Events")
        await _maybe_auto_title(state, session)
        assert session.title == "Offsite Planning"
        assert session._titled is True
        # ONE LLM call carried both title and tags
        assert "TAGS:" in stream.last_prompt
        assert session.tags[0] == "t-work"
        assert find_tag_by_name(state, "Events") is not None
        assert len(session.tags) == 2

    @pytest.mark.asyncio
    async def test_no_tag_suffix_when_user_already_tagged(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        session.tags = ["t-work"]
        session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        state._sessions["s1"] = session
        stream = _mock_title_stream(state, "Hello Chat\nTAGS: Done")
        await _maybe_auto_title(state, session)
        assert "TAGS:" not in stream.last_prompt  # tag ask omitted entirely
        assert session.tags == ["t-work"]  # untouched

    @pytest.mark.asyncio
    async def test_incognito_session_never_tagged(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1", memory_mode="incognito")
        session.messages = [
            {"role": "user", "content": "secret stuff"},
            {"role": "assistant", "content": "ok"},
        ]
        state._sessions["s1"] = session
        stream = _mock_title_stream(state, "Secret\nTAGS: Work")
        await _maybe_auto_title(state, session)
        # incognito still gets a title, but is_restricted → the tag ask is
        # omitted from the prompt and no tags are ever applied
        assert session.title == "Secret"
        assert "TAGS:" not in stream.last_prompt
        assert session.tags == []

    @pytest.mark.asyncio
    async def test_temporary_session_never_tagged(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1", memory_mode="temporary")
        session.messages = [
            {"role": "user", "content": "scratch"},
            {"role": "assistant", "content": "ok"},
        ]
        state._sessions["s1"] = session
        _mock_title_stream(state, "Scratch\nTAGS: Work")
        await _maybe_auto_title(state, session)
        assert session.tags == []

    @pytest.mark.asyncio
    async def test_config_flag_off_disables_tagging(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        state._sessions["s1"] = session
        stream = _mock_title_stream(state, "Hello Chat\nTAGS: Work")
        with patch("personalclaw.dashboard.chat_title._auto_tag_enabled", return_value=False):
            await _maybe_auto_title(state, session)
        assert session.title == "Hello Chat"  # title still applied
        assert "TAGS:" not in stream.last_prompt
        assert session.tags == []

    @pytest.mark.asyncio
    async def test_tags_none_reply_leaves_untagged(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        state._sessions["s1"] = session
        _mock_title_stream(state, "Hello Chat\nTAGS: none")
        await _maybe_auto_title(state, session)
        assert session.title == "Hello Chat"
        assert session.tags == []


# ── Config chain ─────────────────────────────────────────────────────────────


class TestAutoTagConfig:
    def test_default_true_and_roundtrip(self, tmp_path, monkeypatch):
        from personalclaw.config.loader import AppConfig, DashboardConfig

        assert DashboardConfig().auto_tag_sessions is True
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"dashboard": {"auto_tag_sessions": False}}))
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_path)
        cfg = AppConfig.load()
        assert cfg.dashboard.auto_tag_sessions is False
        assert cfg.to_dict()["dashboard"]["auto_tag_sessions"] is False

    @pytest.mark.asyncio
    async def test_dashboard_config_api_roundtrip(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers.files import api_dashboard_config

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_path)
        app = web.Application()
        app.router.add_get("/api/dashboard/config", api_dashboard_config)
        app.router.add_put("/api/dashboard/config", api_dashboard_config)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/dashboard/config")
            data = await resp.json()
            assert data["auto_tag_sessions"] is True  # default on
            resp = await client.put("/api/dashboard/config", json={"auto_tag_sessions": False})
            assert resp.status == 200
            resp = await client.get("/api/dashboard/config")
            data = await resp.json()
            assert data["auto_tag_sessions"] is False
            # non-bool rejected
            resp = await client.put("/api/dashboard/config", json={"auto_tag_sessions": "yes"})
            assert resp.status == 400


# ── Feature B: magic re-tag batch ────────────────────────────────────────────


class TestParseRetagReply:
    def test_full_set(self):
        assert _parse_retag_reply("TAGS: Work, Done") == ["Work", "Done"]

    def test_unchanged_returns_none(self):
        assert _parse_retag_reply("TAGS: unchanged") is None

    def test_none_returns_empty(self):
        assert _parse_retag_reply("TAGS: none") == []

    def test_no_line_returns_none(self):
        assert _parse_retag_reply("I think this chat is about work.") is None


class TestCollectCandidates:
    def test_excludes_restricted_and_includes_disk(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        live = _ChatSession("live1")
        live.tags = ["t-work"]
        state._sessions["live1"] = live
        state._sessions["incog"] = _ChatSession("incog", memory_mode="incognito")
        state._sessions["temp"] = _ChatSession("temp", memory_mode="temporary")
        # a persisted disk-only session
        state.conversation_log.append("dashboard:old1", "user", "hello there")
        state.conversation_log.update_metadata("dashboard:old1", {"tags": ["t-done"]})
        # a persisted incognito session must be excluded
        state.conversation_log.append("dashboard:old2", "user", "sneaky")
        state.conversation_log.update_metadata("dashboard:old2", {"memory_mode": "incognito"})
        cands = _collect_candidates(state)
        keys = {c.key for c in cands}
        assert "live1" in keys and "old1" in keys
        assert "incog" not in keys and "temp" not in keys and "old2" not in keys
        old1 = next(c for c in cands if c.key == "old1")
        assert old1.tags == ["t-done"] and not old1.in_memory

    def test_live_session_shadows_disk_copy(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        state.conversation_log.append("dashboard:s1", "user", "hi")
        state._sessions["s1"] = _ChatSession("s1")
        cands = _collect_candidates(state)
        assert [c.key for c in cands].count("s1") == 1
        assert next(c for c in cands if c.key == "s1").in_memory


class TestRetagApply:
    def test_apply_to_disk_session_persists_metadata(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        state.conversation_log.append("dashboard:old1", "user", "hello")
        cand = _Candidate(key="old1", history_key="dashboard:old1", in_memory=False, tags=[])
        assert _apply_tags(state, cand, ["t-work"]) is True
        meta = state.conversation_log.get_metadata("dashboard:old1")
        assert meta["tags"] == ["t-work"]

    def test_apply_unchanged_returns_false(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        cand = _Candidate(key="x", history_key="", in_memory=True, tags=["t-work"])
        assert _apply_tags(state, cand, ["t-work"]) is False

    def test_resolve_creates_via_ui_path_capped(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        ids = _resolve_tag_ids(state, ["Work", "Fresh1", "Fresh2", "Fresh3"])
        assert ids[0] == "t-work"
        assert len(ids) == 3  # Work + 2 new (third new capped)
        assert find_tag_by_name(state, "Fresh1") is not None
        assert find_tag_by_name(state, "Fresh3") is None


def _retag_app(state):
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/sessions/retag-all", api_retag_all)
    app.router.add_get("/api/sessions/retag-all", api_retag_status)
    app.router.add_post("/api/sessions/retag-all/cancel", api_retag_cancel)
    return app


class TestRetagEndpoint:
    @pytest.mark.asyncio
    async def test_batch_updates_stale_status_tag(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        session.tags = ["t-prog"]  # stale: conversation says it's done
        session.messages = [
            {"role": "user", "content": "is the migration finished?"},
            {"role": "assistant", "content": "yes, fully deployed and done"},
        ]
        state._sessions["s1"] = session
        events = []
        state.broadcast_ws = MagicMock(side_effect=lambda t, d: events.append((t, d)))

        async def fake_llm(prompt, *, use_case="background"):
            assert "In-Progress" in prompt  # current tags shown
            return "TAGS: Work, Done"

        with patch("personalclaw.llm_helpers.one_shot_completion", side_effect=fake_llm):
            async with TestClient(TestServer(_retag_app(state))) as client:
                resp = await client.post("/api/sessions/retag-all")
                assert resp.status == 202
                job_id = (await resp.json())["id"]
                # wait for the background task to finish
                for _ in range(100):
                    resp = await client.get("/api/sessions/retag-all")
                    data = await resp.json()
                    if data["status"] != "running":
                        break
                    await asyncio.sleep(0.02)
        assert data["status"] == "done"
        assert data["id"] == job_id
        assert data["updated"] == 1
        assert sorted(session.tags) == sorted(["t-work", "t-done"])
        # progress + terminal events were broadcast
        types = [t for t, _ in events]
        assert "retag_progress" in types and "retag_done" in types

    @pytest.mark.asyncio
    async def test_restricted_sessions_untouched(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        incog = _ChatSession("incog", memory_mode="incognito")
        incog.messages = [
            {"role": "user", "content": "private"},
            {"role": "assistant", "content": "ok"},
        ]
        state._sessions["incog"] = incog
        state.broadcast_ws = MagicMock()
        calls = []

        async def fake_llm(prompt, *, use_case="background"):
            calls.append(prompt)
            return "TAGS: Work"

        with patch("personalclaw.llm_helpers.one_shot_completion", side_effect=fake_llm):
            async with TestClient(TestServer(_retag_app(state))) as client:
                await client.post("/api/sessions/retag-all")
                for _ in range(100):
                    resp = await client.get("/api/sessions/retag-all")
                    data = await resp.json()
                    if data["status"] != "running":
                        break
                    await asyncio.sleep(0.02)
        assert data["total"] == 0  # incognito never enumerated
        assert calls == []  # and never sent to the LLM
        assert incog.tags == []

    @pytest.mark.asyncio
    async def test_second_post_returns_running_job(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        state._sessions["s1"] = session
        state.broadcast_ws = MagicMock()
        gate = asyncio.Event()

        async def slow_llm(prompt, *, use_case="background"):
            await gate.wait()
            return "TAGS: unchanged"

        with patch("personalclaw.llm_helpers.one_shot_completion", side_effect=slow_llm):
            async with TestClient(TestServer(_retag_app(state))) as client:
                first = await (await client.post("/api/sessions/retag-all")).json()
                second_resp = await client.post("/api/sessions/retag-all")
                second = await second_resp.json()
                assert second_resp.status == 200  # not a new job
                assert second["id"] == first["id"]
                gate.set()
                for _ in range(100):
                    data = await (await client.get("/api/sessions/retag-all")).json()
                    if data["status"] != "running":
                        break
                    await asyncio.sleep(0.02)
                assert data["status"] == "done"

    @pytest.mark.asyncio
    async def test_cancel_running_job(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        session = _ChatSession("s1")
        session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        state._sessions["s1"] = session
        state.broadcast_ws = MagicMock()

        async def hang_llm(prompt, *, use_case="background"):
            await asyncio.sleep(3600)

        with patch("personalclaw.llm_helpers.one_shot_completion", side_effect=hang_llm):
            async with TestClient(TestServer(_retag_app(state))) as client:
                await client.post("/api/sessions/retag-all")
                resp = await client.post("/api/sessions/retag-all/cancel")
                assert resp.status == 200
                for _ in range(100):
                    data = await (await client.get("/api/sessions/retag-all")).json()
                    if data["status"] != "running":
                        break
                    await asyncio.sleep(0.02)
                assert data["status"] == "cancelled"
                assert session.tags == []

    @pytest.mark.asyncio
    async def test_cancel_without_job_404s(self, tmp_path, monkeypatch):
        state = _state_with_tags(tmp_path, monkeypatch)
        async with TestClient(TestServer(_retag_app(state))) as client:
            resp = await client.post("/api/sessions/retag-all/cancel")
            assert resp.status == 404
            resp = await client.get("/api/sessions/retag-all")
            assert (await resp.json())["status"] == "idle"
