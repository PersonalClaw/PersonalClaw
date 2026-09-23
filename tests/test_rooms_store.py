"""AR-2/AR-3 — the room store, its member model, and the transcript it reuses.

These rails are organised around the two claims the atoms actually make, because both are
claims about REUSE and absence rather than about new behaviour, and neither is visible by
reading the room code alone:

* AR-2: the transcript persists through ``history.ConversationLog``, so rotation and
  archiving are the shipped code paths — tested by driving a real 2 MB transcript and
  asserting the rotation happened with no rotation code in ``rooms/``.
* AR-3: each member holds its OWN provider session with no shared context window — tested
  as two distinct provider objects under two distinct keys, plus the by-absence property
  that a ``room:`` key resolves to the INTERACTIVE, human-approves posture.

Everything writes under ``tmp_path``; ``config_dir`` is monkeypatched in an autouse
fixture so no test can reach the real home.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from personalclaw.rooms import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point config_dir and the home env at tmp_path (the real home is never touched)."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture
def enabled(monkeypatch):
    """A config with rooms on and one configured agent binding named ``analyst``."""
    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig()
    cfg.rooms.enabled = True
    cfg.agents = {"analyst": AgentProfile(), "skeptic": AgentProfile()}
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    return cfg


# ── the room record ─────────────────────────────────────────────────────────


def test_room_round_trips_create_list_archive(enabled):
    """AR-2's first done-when clause, end to end through the persisted index."""
    room = store.create_room("Pricing debate")
    assert room.id == "pricing-debate", "the id is a slug derived from the title"
    assert store.list_rooms()[0].id == room.id

    reloaded = store.get_room(room.id)
    assert reloaded is not None and reloaded.title == "Pricing debate"
    assert reloaded.archived is False and reloaded.rounds_used == 0

    store.archive_room(room.id)
    assert store.list_rooms() == [], "an archived room drops out of the default listing"
    assert [r.id for r in store.list_rooms(include_archived=True)] == [room.id]


def test_archiving_twice_is_a_no_op_not_an_error(enabled):
    room = store.create_room("Twice")
    store.archive_room(room.id)
    assert store.archive_room(room.id).archived is True


def test_two_rooms_with_the_same_title_get_distinct_ids(enabled):
    a = store.create_room("Same name")
    b = store.create_room("Same name")
    assert a.id != b.id, "two rooms must never share a directory"
    assert {a.id, b.id} == {"same-name", "same-name-2"}


def test_a_title_that_slugifies_to_nothing_still_yields_a_usable_id(enabled):
    """A title of pure punctuation has no slug, so the fallback must still be unique."""
    a = store.create_room("!!!")
    b = store.create_room("???")
    assert a.id and b.id and a.id != b.id


def test_a_blank_title_is_refused(enabled):
    with pytest.raises(store.RoomError) as exc:
        store.create_room("   ")
    assert exc.value.code == "room_title_required"


# ── the transcript: ConversationLog, not a new store ───────────────────────


def test_the_transcript_lands_at_the_declared_path(enabled):
    """AR-2 names ``rooms/<id>/transcript.jsonl`` verbatim; this is that path."""
    room = store.create_room("Path check")
    store.append_message(room.id, role="user", content="hello")

    path = store.transcript_path(room.id)
    assert path == store.rooms_dir() / room.id / "transcript.jsonl"
    assert path.exists()


def test_a_message_round_trips_its_speaker(enabled):
    room = store.create_room("Speakers")
    store.add_member(room.id, "analyst")
    store.append_message(room.id, role="user", content="human says")
    store.append_message(room.id, role="assistant", content="agent says", speaker="analyst")

    from personalclaw.history import speaker_of

    messages = store.read_messages(room.id)
    assert [m["content"] for m in messages] == ["human says", "agent says"]
    assert [speaker_of(m) for m in messages] == ["", "analyst"], "the human reads as ''"


def test_a_speaker_who_is_not_a_member_cannot_write_to_the_transcript(enabled):
    """The transcript is what every member reads, so a forged speaker is a trust failure."""
    room = store.create_room("Forgery")
    with pytest.raises(store.RoomError) as exc:
        store.append_message(room.id, role="assistant", content="x", speaker="ghost")
    assert exc.value.code == "room_member_not_found"
    assert not store.transcript_path(room.id).exists(), "a refused write writes nothing"


def test_an_archived_room_accepts_no_messages(enabled):
    room = store.create_room("Closed")
    store.archive_room(room.id)
    with pytest.raises(store.RoomError) as exc:
        store.append_message(room.id, role="user", content="x")
    assert exc.value.code == "room_archived"


def test_a_2mb_transcript_fires_the_inherited_rotation_and_archive(enabled):
    """AR-2's reuse clause: rotation is ConversationLog's, so a room grows no rotation code.

    Drives a real transcript past ``history._SESSION_MAX_BYTES`` and asserts the shipped
    policy ran — the file shrank to the keep-window and the overflow landed in an archive
    under the ROOM's own directory, which is the observable consequence of pointing a
    ``ConversationLog`` at ``rooms/<id>/`` rather than reimplementing the policy.
    """
    from personalclaw import history

    room = store.create_room("Rotation")
    path = store.transcript_path(room.id)
    archive_dir = store.room_dir(room.id) / "archive"

    # Rotation needs BOTH conditions the shipped policy checks: over
    # `_SESSION_MAX_BYTES` **and** more than `_SESSION_KEEP_LINES` lines
    # (`history.py:861-867`), so the chunk is sized to overshoot both — a handful of very
    # fat lines exceeds the byte budget and rotates nothing, because there would be
    # nothing left to drop.
    #
    # The loop stops on the ARCHIVE appearing rather than on the file shrinking below the
    # cap, because the shipped policy makes a size-based wait unterminable: 200 kept lines
    # of 8 KB is 1.6 MB, so every rotation lands back under the cap and the next append
    # pushes over it again. Bulk lines go through `room_log()` directly — the room's own
    # log object, so the rotation under test is still the room's — which keeps 250-odd
    # appends off the index-read path.
    log = store.room_log(room.id)
    chunk = "x" * 8_000
    for _ in range(400):
        log.append(store.TRANSCRIPT_KEY, role="user", content=chunk)
        if archive_dir.exists():
            break
    else:  # pragma: no cover - a failed rail, not a branch
        pytest.fail("400 appends of 8 KB never tripped the inherited rotation")

    assert path.stat().st_size < history._SESSION_MAX_BYTES, "the transcript was rotated"
    assert len(store.read_messages(room.id)) <= history._SESSION_KEEP_LINES
    assert list(archive_dir.iterdir()), "the rotated-out lines were archived, not dropped"

    # And the room is still usable afterwards: a post-rotation append reads back.
    store.append_message(room.id, role="user", content="after")
    assert store.read_messages(room.id)[-1]["content"] == "after"

    # And the reuse is structural, not incidental: no rotation constant lives in rooms/.
    source = (store.__file__,)
    for f in source:
        text = open(f, encoding="utf-8").read()
        assert "_SESSION_MAX_BYTES" not in text and "_KEEP_LINES" not in text


def test_the_write_path_redacts_every_role_including_the_human(enabled):
    """The room transcript is the inter-member wire, so the human's own words are redacted.

    Stricter than ``chat_persistence``, deliberately: AR-4 feeds this transcript to every
    member's provider, so a credential typed into a room would otherwise be handed to N
    agents. Same function (``security.redact_field``), stricter application.
    """
    room = store.create_room("Secrets")
    store.append_message(room.id, role="user", content="token sk-ant-api03-AAAAAAAAAAAAAAAAAAAA")

    persisted = store.read_messages(room.id)[0]["content"]
    assert "sk-ant-api03-AAAAAAAAAAAAAAAAAAAA" not in persisted


def test_export_payload_hands_over_everything_a_renderer_needs(enabled):
    """The store supplies the payload; the HTTP handler renders it.

    The rendering itself is asserted in ``test_rooms_api.py`` against the shipped
    ``session_export.render``, because that is where the call lives — see the companion
    rail below for why it cannot live here.
    """
    room = store.create_room("Exportable")
    store.append_message(room.id, role="user", content="hello room")

    exported, meta, messages = store.export_payload(room.id)
    assert exported.id == room.id and exported.title == "Exportable"
    assert isinstance(meta, dict), "the inherited ConversationLog metadata line"
    assert [m["content"] for m in messages] == ["hello room"]

    with pytest.raises(store.RoomError) as caught:
        store.export_payload("no-such-room")
    assert caught.value.code == "room_not_found"


def test_the_store_never_imports_the_http_surface(enabled):
    """`rooms/` is domain code: an import of ``dashboard/`` inverts the dependency.

    A domain module that reaches up into the HTTP surface can no longer be exercised
    without standing up the web app, which is how a feature ends up reachable only through
    one route. ``scripts/generate_structural_baseline.py``'s
    ``core-must-not-import-the-http-surface`` rule is the census; this is the local rail, so
    the failure names ``rooms/`` instead of arriving as a whole-tree counter that rose.
    Deferred imports count — the census resolves them, and hiding one inside a function
    body would satisfy a naive grep while leaving the edge in place.
    """
    import personalclaw.rooms.turn as turn_module

    for module in (store, turn_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "personalclaw.dashboard" not in stripped, f"{module.__name__}: {stripped}"


# ── members (AR-3) ─────────────────────────────────────────────────────────


def test_members_persist_with_their_role_blurb_and_policy(enabled):
    room = store.create_room("Roster")
    store.add_member(room.id, "analyst", role_blurb="argues from the numbers")
    store.add_member(room.id, "skeptic", listen_policy="mention")

    members = store.get_room(room.id).members
    assert [m.name for m in members] == ["analyst", "skeptic"]
    assert members[0].role_blurb == "argues from the numbers"
    assert members[0].listen_policy == "all", "the default"
    assert members[1].listen_policy == "mention"


@pytest.mark.parametrize("policy", ["all", "mention", "silent"])
def test_all_three_listen_policies_persist_and_reload(enabled, policy):
    """AR-3's done-when names exactly these three; each must survive a round trip."""
    room = store.create_room(f"Policy {policy}")
    store.add_member(room.id, "analyst", listen_policy=policy)
    assert store.get_room(room.id).members[0].listen_policy == policy


def test_a_fourth_listen_policy_is_refused_and_writes_nothing(enabled):
    room = store.create_room("Bad policy")
    with pytest.raises(store.RoomError) as exc:
        store.add_member(room.id, "analyst", listen_policy="whisper")
    assert exc.value.code == "room_invalid_listen_policy"
    assert store.get_room(room.id).members == []


def test_a_member_naming_an_unconfigured_binding_is_refused(enabled):
    """Fail closed. Checked against ``config.agents`` directly, because
    ``resolve_agent_bindings`` falls back to the default agent and so can never say no."""
    room = store.create_room("Unknown agent")
    with pytest.raises(store.RoomError) as exc:
        store.add_member(room.id, "nobody")
    assert exc.value.code == "room_member_unknown_agent"
    assert store.get_room(room.id).members == []


def test_resolve_agent_bindings_cannot_be_the_existence_check(enabled):
    """The measurement behind the DISCOVERY that shaped the check above.

    If this ever starts raising or returning something falsey for an unknown name, the
    direct ``config.agents`` membership test in ``_validate_member_name`` becomes
    redundant and should be reconsidered — so the reasoning is pinned, not just the code.
    """
    from personalclaw.config.loader import AppConfig, resolve_agent_bindings

    resolved = resolve_agent_bindings(AppConfig.load(), "definitely-not-an-agent")
    assert resolved is not None, "it resolves rather than refusing — hence the direct check"


def test_a_malformed_member_name_is_refused(enabled):
    room = store.create_room("Bad name")
    for bad in ("../escape", "has space", "semi;colon", ""):
        with pytest.raises(store.RoomError) as exc:
            store.add_member(room.id, bad)
        assert exc.value.code in (
            "room_member_name_invalid",
            "room_member_name_required",
        ), bad


def test_adding_the_same_member_twice_is_refused(enabled):
    room = store.create_room("Dupe")
    store.add_member(room.id, "analyst")
    with pytest.raises(store.RoomError) as exc:
        store.add_member(room.id, "analyst")
    assert exc.value.code == "room_member_exists"
    assert len(store.get_room(room.id).members) == 1


def test_the_member_cap_is_the_configured_one(enabled):
    enabled.rooms.max_members = 1
    room = store.create_room("Capped")
    store.add_member(room.id, "analyst")
    with pytest.raises(store.RoomError) as exc:
        store.add_member(room.id, "skeptic")
    assert exc.value.code == "room_member_limit"


def test_removing_a_non_member_is_refused_rather_than_silently_succeeding(enabled):
    room = store.create_room("Remove")
    store.add_member(room.id, "analyst")
    store.remove_member(room.id, "analyst")
    assert store.get_room(room.id).members == []
    with pytest.raises(store.RoomError) as exc:
        store.remove_member(room.id, "analyst")
    assert exc.value.code == "room_member_not_found"


def test_an_archived_room_takes_no_new_members(enabled):
    room = store.create_room("Shut")
    store.archive_room(room.id)
    with pytest.raises(store.RoomError) as exc:
        store.add_member(room.id, "analyst")
    assert exc.value.code == "room_archived"


# ── the two postures over one index ────────────────────────────────────────


def _corrupt_index():
    path = store.rooms_dir() / store.INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json at all", encoding="utf-8")


def test_the_listing_fails_open_on_a_corrupt_index(enabled):
    """A listing surface degrades to empty rather than taking the page down."""
    store.create_room("Will be lost to view")
    _corrupt_index()
    assert store.list_rooms() == []
    assert store.get_room("will-be-lost-to-view") is None


def test_the_turn_roster_fails_closed_on_a_corrupt_index(enabled):
    """The roster decides who speaks, so it refuses rather than guessing an empty room."""
    room = store.create_room("Roster closed")
    _corrupt_index()
    with pytest.raises(store.RoomError) as exc:
        store.members_for_turn(room.id)
    assert exc.value.code == "room_state_unreadable"


def test_a_write_never_clobbers_a_corrupt_index(enabled):
    """The direction in which failing open would be data loss, not degradation."""
    _corrupt_index()
    with pytest.raises(store.RoomError) as exc:
        store.create_room("Would overwrite")
    assert exc.value.code == "room_state_unreadable"
    raw = (store.rooms_dir() / store.INDEX_FILENAME).read_text(encoding="utf-8")
    assert raw == "{ not json at all", "the unreadable file is left exactly as found"


def test_a_room_record_with_an_unusable_id_is_not_trusted(enabled):
    """An id that is not a slug would name a directory we did not choose."""
    path = store.rooms_dir() / store.INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rooms": [{"id": "../escape", "title": "x"}]}), encoding="utf-8")
    assert store.list_rooms() == [], "fails open on the listing"
    with pytest.raises(store.RoomError):
        store.require_room("../escape")


# ── config ─────────────────────────────────────────────────────────────────


def test_a_room_budget_of_zero_inherits_the_configured_default(enabled):
    room = store.create_room("Budget")
    assert room.round_budget == 0
    assert store.effective_round_budget(room) == enabled.rooms.round_budget == 6

    room.round_budget = 3
    assert store.effective_round_budget(room) == 3


def test_rooms_is_off_by_default():
    """The kill switch ships off, so the feature cannot appear without being asked for."""
    from personalclaw.config.loader import AppConfig

    assert AppConfig().rooms.enabled is False


def test_the_inheritable_default_can_never_be_zero(tmp_path, monkeypatch):
    """A 0 default would resolve to an unbounded loop for every room that inherits it."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"rooms": {"enabled": True, "round_budget": 0, "max_members": 0}}),
        encoding="utf-8",
    )
    loaded = cfg.AppConfig.load()
    assert loaded.rooms.round_budget >= 1
    assert loaded.rooms.max_members >= 1


# ── the per-member session (AR-3's core property) ──────────────────────────


class _FakeProvider:
    """Just an identity — the property under test is distinctness, not behaviour."""

    def __init__(self, key: str) -> None:
        self.key = key


class _FakeSessions:
    """Records keys and hands back one provider per key, like SessionManager does."""

    def __init__(self) -> None:
        self.providers: dict[str, _FakeProvider] = {}
        self.released: list[str] = []

    async def get_or_create(self, key, agent=None, **kwargs):
        is_new = key not in self.providers
        provider = self.providers.setdefault(key, _FakeProvider(key))
        return provider, is_new, False

    def release(self, key, *, cleanup=False):
        self.released.append(key)


def test_two_members_of_one_room_hold_two_distinct_provider_sessions(enabled):
    """AR-3's central claim: no shared context window between members."""
    from personalclaw.rooms import turn

    room = store.create_room("No sharing")
    store.add_member(room.id, "analyst")
    store.add_member(room.id, "skeptic")
    sessions = _FakeSessions()

    async def drive():
        async with turn.member_session(sessions, room.id, "analyst") as a:
            pass
        async with turn.member_session(sessions, room.id, "skeptic") as b:
            pass
        return a, b

    a, b = asyncio.run(drive())
    assert a is not b, "two members, two providers — never one session for the room"
    assert set(sessions.providers) == {
        f"room:{room.id}:analyst",
        f"room:{room.id}:skeptic",
    }
    assert sessions.released == list(sessions.providers), "every acquire released its permit"


def test_the_semaphore_is_released_even_when_the_turn_raises(enabled):
    """A leaked permit wedges that member's next turn while the room looks healthy."""
    from personalclaw.rooms import turn

    room = store.create_room("Boom")
    store.add_member(room.id, "analyst")
    sessions = _FakeSessions()

    async def drive():
        async with turn.member_session(sessions, room.id, "analyst"):
            raise RuntimeError("the provider blew up mid-turn")

    with pytest.raises(RuntimeError):
        asyncio.run(drive())
    assert sessions.released == [f"room:{room.id}:analyst"]


def test_a_non_member_gets_no_session(enabled):
    from personalclaw.rooms import turn

    room = store.create_room("Outsider")
    sessions = _FakeSessions()

    async def drive():
        async with turn.member_session(sessions, room.id, "analyst"):
            pass

    with pytest.raises(store.RoomError) as exc:
        asyncio.run(drive())
    assert exc.value.code == "room_member_not_found"
    assert sessions.providers == {}, "no session is minted for a refused member"


def test_a_room_session_key_leaves_the_human_as_sole_approver(enabled):
    """The by-absence property AR-3's done-when names, pinned so a later atom can't undo it.

    ``room:`` is in no stateless or unattended prefix tuple, so the INTERACTIVE profile
    applies and its approval mode is ``ask``. If a future change registered ``room:`` as
    unattended, HEADLESS's ``hook_based`` approval would silently remove the human from
    the loop of the one surface whose whole point is that they are in it.
    """
    from personalclaw.guardrails.policy import is_unattended_session, profile_for_session
    from personalclaw.rooms.turn import session_key

    key = session_key("some-room", "analyst")
    assert key == "room:some-room:analyst"
    assert is_unattended_session(key) is False
    profile = profile_for_session(key)
    assert profile.approval == "ask", "the human approves every room action"


def test_the_room_prefix_is_absent_from_both_prefix_tuples(enabled):
    """Stated directly, because the test above would still pass if `room:` were added to
    a tuple whose semantics later changed. This is the invariant, not its consequence."""
    from personalclaw import session as session_mod
    from personalclaw.guardrails import policy

    assert not any(p.startswith("room") for p in session_mod._STATELESS_PREFIXES)
    assert not any(p.startswith("room") for p in policy._EXTRA_UNATTENDED_PREFIXES)
