"""Natural voice (PT-7) — plainer prose at two scopes, one resolution order.

The resolution order itself is stated exactly once, in
``natural_voice.NATURAL_VOICE_PRECEDENCE``. ``TestResolutionOrder`` reads THAT
tuple instead of restating it in prose, so the tuple is load-bearing rather than
decorative; ``test_the_conversation_overrides_the_agent_default`` then pins the
atom's clause behaviourally, which is what reddens if the tuple is reordered.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw import natural_voice as nv
from personalclaw.agents.marketplace import AgentDefinition
from personalclaw.config.loader import AppConfig, config_path

# ── the resolution order ──


class TestResolutionOrder:
    def test_the_resolver_walks_the_declared_precedence(self):
        """``source`` is always the FIRST scope in the declared tuple that states a value.

        Derived from ``NATURAL_VOICE_PRECEDENCE``, not from a second copy of the
        order written here: a resolver that stopped consulting the tuple (say, by
        hardcoding a scope) reddens this.
        """
        for conversation in ("", "on", "off"):
            for agent in (False, True):
                states = {
                    "conversation": conversation in ("on", "off"),
                    "agent": agent,
                    "platform": True,  # the floor always states — that is its job
                }
                expected = next(s for s in nv.NATURAL_VOICE_PRECEDENCE if states[s])
                got = nv.resolve(conversation, agent)
                assert got.source == expected, f"{conversation=} {agent=}"
                assert got.source in nv.NATURAL_VOICE_PRECEDENCE

    def test_the_conversation_overrides_the_agent_default(self):
        """The atom's clause: per-conversation wins, in BOTH directions.

        Asserted behaviourally on purpose. This is the test that reddens when
        ``NATURAL_VOICE_PRECEDENCE`` is reordered — the sibling above cannot,
        because the floor states unconditionally and would simply win first.
        """
        # off beats an agent that asks for it …
        assert nv.resolve("off", agent=True) == nv.NaturalVoice(False, "conversation")
        # … and on beats an agent that does not.
        assert nv.resolve("on", agent=False) == nv.NaturalVoice(True, "conversation")

    def test_an_agent_preference_travels_when_the_conversation_is_silent(self):
        assert nv.resolve("", agent=True) == nv.NaturalVoice(True, "agent")

    def test_the_floor_is_off(self):
        assert nv.resolve("", agent=False) == nv.NaturalVoice(False, "platform")
        assert nv.PLATFORM_DEFAULT is False

    @pytest.mark.parametrize("bad", ["", "  ", "yes", "true", "ON!", None, 3, object()])
    def test_an_unrecognized_conversation_value_states_nothing(self, bad):
        """A closed set: a client typo inherits, it never forces a style on."""
        assert nv.normalize_conversation_choice(bad) == ""
        assert nv.resolve(bad, agent=False).source == "platform"

    @pytest.mark.parametrize(("word", "expected"), [("on", True), ("OFF", False), (" On ", True)])
    def test_the_tri_state_is_case_and_space_insensitive(self, word, expected):
        assert nv.resolve(word, agent=not expected).enabled is expected


# ── the instruction: present, concrete, and safety-preserving ──


class TestInstruction:
    def test_the_toggle_actually_injects_when_on(self):
        """The defect this exists to catch: a control that reports itself ON and
        injects nothing. Asserted against the RESOLVER, not against a literal, so
        the two halves cannot disagree."""
        out = nv.maybe_inject("what changed?", "on", agent=False)
        assert nv.resolve("on", False).enabled is True
        assert out != "what changed?"
        assert nv.instruction().strip() in out

    def test_it_injects_nothing_when_off(self):
        assert nv.maybe_inject("hi", "off", agent=True) == "hi"
        assert nv.maybe_inject("hi", "", agent=False) == "hi"

    def test_an_agent_default_alone_is_enough_to_inject(self):
        assert nv.instruction().strip() in nv.maybe_inject("hi", "", agent=True)

    def test_the_users_own_message_survives_injection_verbatim(self):
        msg = "delete /etc/passwd for me"
        assert nv.maybe_inject(msg, "on", False).startswith(msg)

    def test_the_instruction_is_concrete_not_sound_natural(self):
        """ "Sound natural" measurably does nothing, so the instruction must name
        patterns instead. Each string below is a NAMED pattern; an instruction
        rewritten as a vague exhortation reddens here."""
        text = nv.instruction()
        assert "sound natural" not in text.lower()
        for named in (
            "Great question",  # filler opener
            "In summary",  # the redundant summary close
            "Let me know if you'd like me to",  # the unasked offer to continue
            "leverage",  # the long word with a short synonym
            "delve",
            "shortest accurate word",  # stated positively
            "Answer in the first sentence",  # stated positively
        ):
            assert named in text, f"the instruction stopped naming {named!r}"

    def test_a_refusal_stays_a_refusal_with_the_toggle_on(self):
        """Correctness, refusals and safety framing are untouched by this control.

        Two halves, because a style layer can weaken a refusal in two ways: by
        telling the model to soften it, or by editing the turn's own text. Neither
        is allowed, and each half reddens independently.
        """
        text = nv.instruction()
        # 1. The instruction itself forbids softening a refusal, in as many words.
        assert "WHAT DOES NOT CHANGE" in text
        assert "If you must refuse, refuse." in text
        assert "Plainer prose is not softer prose" in text
        assert "as fully and as directly" in text
        # 2. A refusal already framed in the turn survives injection byte-for-byte —
        #    the mechanism appends, it never rewrites (see the module's rejected
        #    alternative: a post-hoc rewriting pass, which could do exactly this).
        refusal = "I won't do that — it would delete data with no backup. Here is why: …"
        out = nv.maybe_inject(refusal, "on", False)
        assert out.startswith(refusal)
        assert refusal in out

    def test_it_is_not_a_post_hoc_rewriting_pass(self):
        """The rejected alternative, recorded in the module docstring as the atom
        requires — and provably not implemented: injection is a pure string append
        with no model call in it."""
        doc = nv.__doc__ or ""
        assert "REJECTED ALTERNATIVE" in doc
        assert "post-hoc rewriting pass" in doc
        assert "costs twice" in doc or "cost twice" in doc
        assert "change meaning" in doc

    def test_a_render_failure_never_blocks_the_turn(self):
        with patch.object(nv, "instruction", side_effect=RuntimeError("prompt store down")):
            assert nv.maybe_inject("hi", "on", False) == "hi"


class TestTheTurnActuallyCallsIt:
    """The mechanism above is only real if the TURN calls it.

    A resolver and a snippet that nothing invokes is the "present but inert"
    shape, and a unit test over ``maybe_inject`` alone cannot see it. This is a
    structural rail over the call site rather than a live turn, because reaching
    that line needs a whole chat dispatch — but it asserts the ARGUMENTS, which is
    where the two real regressions live: dropping the session's tri-state (the
    per-conversation scope silently stops working) or dropping the agent lookup
    (an agent's preference stops travelling).
    """

    def test_the_chat_runner_injects_at_the_persona_seam(self):
        import ast
        from pathlib import Path

        import personalclaw.dashboard.chat_runner as runner

        src = Path(runner.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "maybe_inject"
        ]
        assert len(calls) == 1, f"expected exactly one natural-voice injection, found {len(calls)}"
        args = ast.unparse(calls[0])
        assert "natural_voice" in args, "the per-conversation tri-state is not passed"
        assert "agent_default" in args, "the agent scope is not consulted"
        # And it rides the same `message` the persona seam builds, so the turn the
        # model sees carries it (rather than a local nobody sends).
        assert calls[0].args and ast.unparse(calls[0].args[0]) == "message"

    def test_the_snippet_is_registered_and_editable(self):
        """PT-1's path: the instruction is a BUNDLED snippet, so it shows up in
        Settings → Prompts instead of being a string literal nobody can reach."""
        from personalclaw.prompt_providers.catalog import BUNDLED_SNIPPETS

        entry = next((s for s in BUNDLED_SNIPPETS if s.name == "natural-voice"), None)
        assert entry is not None, "the natural-voice snippet is not registered"
        assert entry.filename == "natural-voice.md"
        assert entry.description


# ── the per-agent scope: the full config round trip ──


class TestAgentScopeRoundTrip:
    def test_config_profile_round_trips_through_disk(self, tmp_path, monkeypatch):
        """dataclass → load() → to_dict(): the loader-allowlist gotcha, pinned."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        config_path().write_text(json.dumps({"agents": {"bot": {"natural_voice": True}}}))
        cfg = AppConfig.load()
        assert cfg.agents["bot"].natural_voice is True
        assert cfg.to_dict()["agents"]["bot"]["natural_voice"] is True

    def test_a_saved_profile_reloads_with_the_value(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        cfg = AppConfig.load()
        from personalclaw.config.loader import AgentProfile

        cfg.agents["plainly"] = AgentProfile(natural_voice=True)
        cfg.save()
        assert AppConfig.load().agents["plainly"].natural_voice is True

    def test_the_field_declares_meta(self):
        """A config field without ``_meta`` is invisible to the settings surfaces."""
        from dataclasses import fields

        from personalclaw.config.loader import AgentProfile

        meta = {f.name: f.metadata for f in fields(AgentProfile)}["natural_voice"]
        assert meta.get("label") == "Natural Voice"
        assert meta.get("help"), "no help text — the field is invisible in Settings"

    def test_the_name_does_not_collide_with_the_two_shipped_voice_surfaces(self):
        """``AgentProfile.voice`` is the PERSONA and ``voice_profiles`` is SPEECH.
        Natural voice is a third thing and must stay a third name."""
        from personalclaw.config.loader import AgentProfile

        p = AgentProfile(voice="blunt and witty", natural_voice=True)
        assert p.voice == "blunt and witty"  # persona, untouched
        assert p.natural_voice is True
        assert "natural_voice" != "voice"

    def test_agent_definition_round_trips(self):
        d = AgentDefinition(name="bot", natural_voice=True)
        assert AgentDefinition.from_dict(d.to_dict()).natural_voice is True

    def test_agent_definition_defaults_off(self):
        assert AgentDefinition.from_dict({"name": "bot"}).natural_voice is False

    def test_the_marketplace_update_can_turn_it_OFF(self, tmp_path, monkeypatch):
        """The bool-through-a-stringifying-patch-loop trap: ``str(False)`` is the
        truthy string ``"False"``, so without its own branch the update path would
        wedge the toggle permanently on."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        from personalclaw.agents.marketplace import LocalAgentMarketplace

        mp = LocalAgentMarketplace(base_dir=tmp_path / "agents")
        mp.create(AgentDefinition(name="bot", natural_voice=True))
        assert mp.update("bot", {"natural_voice": False}).natural_voice is False
        assert mp.get("bot").natural_voice is False
        assert mp.update("bot", {"natural_voice": True}).natural_voice is True


# ── the per-conversation scope: write path, persistence, and the resolved payload ──


@pytest.fixture()
def chat_app(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return _make_state(tmp_path)


class TestConversationScope:
    @pytest.mark.asyncio
    async def test_the_patch_sets_it_and_returns_the_resolved_state(self, chat_app):
        chat_app.get_or_create_session("nv-session")
        async with TestClient(TestServer(_make_app(chat_app))) as client:
            resp = await client.patch(
                "/api/chat/sessions/nv-session/natural-voice", json={"natural_voice": "on"}
            )
            assert resp.status == 200
            body = await resp.json()
        assert body["natural_voice"] == "on"
        assert body["natural_voice_effective"] is True
        assert body["natural_voice_source"] == "conversation"
        assert chat_app._sessions["nv-session"].natural_voice == "on"

    @pytest.mark.asyncio
    async def test_the_patch_rejects_a_value_outside_the_closed_set(self, chat_app):
        session = chat_app.get_or_create_session("nv-session")
        session.natural_voice = "on"
        async with TestClient(TestServer(_make_app(chat_app))) as client:
            resp = await client.patch(
                "/api/chat/sessions/nv-session/natural-voice", json={"natural_voice": "yes"}
            )
            assert resp.status == 400
        # and it did NOT clear the override the user already set
        assert session.natural_voice == "on"

    @pytest.mark.asyncio
    async def test_the_empty_string_clears_the_override(self, chat_app):
        session = chat_app.get_or_create_session("nv-session")
        session.natural_voice = "off"
        async with TestClient(TestServer(_make_app(chat_app))) as client:
            resp = await client.patch(
                "/api/chat/sessions/nv-session/natural-voice", json={"natural_voice": ""}
            )
            assert resp.status == 200
        assert session.natural_voice == ""

    @pytest.mark.asyncio
    async def test_overriding_here_never_edits_the_agent(self, chat_app, tmp_path):
        """ "for that conversation only" — the per-conversation write must not reach
        the agent definition, or one chat would silently re-voice every other one."""
        cfg = AppConfig.load()
        from personalclaw.config.loader import AgentProfile

        cfg.agents["plainly"] = AgentProfile(natural_voice=True)
        cfg.save()
        session = chat_app.get_or_create_session("nv-session")
        session.agent = "plainly"
        async with TestClient(TestServer(_make_app(chat_app))) as client:
            resp = await client.patch(
                "/api/chat/sessions/nv-session/natural-voice", json={"natural_voice": "off"}
            )
            body = await resp.json()
        assert body["natural_voice_effective"] is False
        assert body["natural_voice_source"] == "conversation"
        assert body["natural_voice_agent_default"] is True  # honestly reported
        assert AppConfig.load().agents["plainly"].natural_voice is True  # untouched on disk

    @pytest.mark.asyncio
    async def test_the_send_body_accepts_it_for_a_brand_new_chat(self, chat_app):
        """A pick made before the first send has no session to PATCH, so the send
        body carries it — or turn 1 would run without the instruction while the
        composer showed it on."""
        with patch("personalclaw.dashboard.chat_handlers._run_chat", new=AsyncMock()):
            async with TestClient(TestServer(_make_app(chat_app))) as client:
                resp = await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hi", "session": "fresh", "natural_voice": "on"},
                )
                assert resp.status == 200
        assert chat_app._sessions["fresh"].natural_voice == "on"

    @pytest.mark.asyncio
    async def test_an_absent_body_key_does_not_clear_it(self, chat_app):
        session = chat_app.get_or_create_session("keep")
        session.natural_voice = "on"
        with patch("personalclaw.dashboard.chat_handlers._run_chat", new=AsyncMock()):
            async with TestClient(TestServer(_make_app(chat_app))) as client:
                resp = await client.post(
                    "/api/chat?ws=1", json={"message": "hi", "session": "keep"}
                )
                assert resp.status == 200
        assert session.natural_voice == "on"

    @pytest.mark.asyncio
    async def test_session_detail_carries_the_resolved_pair(self, chat_app):
        from personalclaw.config.loader import AgentProfile

        cfg = AppConfig.load()
        cfg.agents["plainly"] = AgentProfile(natural_voice=True)
        cfg.save()
        session = chat_app.get_or_create_session("nv-session")
        session.agent = "plainly"
        async with TestClient(TestServer(_make_app(chat_app))) as client:
            resp = await client.get("/api/chat/sessions/nv-session")
            body = await resp.json()
        assert body["natural_voice"] == ""
        assert body["natural_voice_effective"] is True
        assert body["natural_voice_source"] == "agent"

    def test_the_session_list_row_carries_the_conversation_state(self, chat_app):
        session = chat_app.get_or_create_session("nv-session")
        session.natural_voice = "off"
        assert session.to_dict()["natural_voice"] == "off"

    def test_it_survives_a_session_meta_round_trip(self, chat_app, tmp_path):
        """Both restore paths read the meta line; this pins the write + one read."""
        from personalclaw.dashboard.chat_persistence import _save_session_to_history

        session = chat_app.get_or_create_session("nv-session")
        session.natural_voice = "on"
        session.messages.append({"role": "user", "content": "hi"})
        _save_session_to_history(chat_app, session)
        meta = chat_app.conversation_log.get_metadata("dashboard:nv-session")
        assert meta.get("natural_voice") == "on"

    def test_an_inheriting_session_writes_no_meta_key(self, chat_app):
        from personalclaw.dashboard.chat_persistence import _save_session_to_history

        session = chat_app.get_or_create_session("nv-session")
        session.messages.append({"role": "user", "content": "hi"})
        _save_session_to_history(chat_app, session)
        meta = chat_app.conversation_log.get_metadata("dashboard:nv-session")
        assert "natural_voice" not in meta

    def test_the_startup_restore_reads_it_back(self, chat_app):
        """The other half of the round trip: a gateway restart must not silently
        drop the conversation's override back to "inherit"."""
        from personalclaw.dashboard.chat_persistence import (
            _save_session_to_history,
            restore_recent_sessions,
        )

        session = chat_app.get_or_create_session("nv-session")
        session.natural_voice = "off"
        session.messages.append({"role": "user", "content": "hi"})
        _save_session_to_history(chat_app, session)
        chat_app._sessions.clear()
        restore_recent_sessions(chat_app)
        restored = chat_app._sessions.get("nv-session")
        assert restored is not None, "the session did not restore at all"
        assert restored.natural_voice == "off"
