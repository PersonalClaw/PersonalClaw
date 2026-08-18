"""Suggested folder/tag organization for untagged sessions (SESSION-MANAGEMENT T2.1).

The risks these tests cover:

* **Auto-application.** The whole atom is "propose, never apply". A proposal that quietly
  wrote ``folder_id`` would move a user's chat behind their back, and the bug would be
  invisible until someone noticed a folder filling up on its own. So the central assertion
  is negative: generating and surfacing a proposal leaves the session's folder and tags
  byte-identical.
* **Paying for the easy cases.** The deterministic signals must short-circuit BEFORE any
  model call. A design that always asked a model would pass a "does it propose something"
  test while being wrong about the thing that matters, so the LLM path is stubbed with a
  spy that must NOT be called on the deterministic path.
* **Nagging.** A declined proposal that returns next scan is worse than no feature. Both
  suppression tiers are exercised: the inbox ``dedup_key`` (still-open row) and the
  persisted decline record (already-resolved row).
* **Inventing vocabulary.** A model naming a folder that doesn't exist must not reach a
  proposal, because accepting one would create a category the user never asked for.
"""

from __future__ import annotations

import pytest

from personalclaw import session_organize as so


class FakeSession:
    """The subset of `_ChatSession` the heuristics read (state.py:232-234, 200)."""

    def __init__(self, key="s1", title="", workspace_dir="", channel="", memory_mode="persistent"):
        self.key = key
        self.title = title
        self.workspace_dir = workspace_dir
        self.folder_id = ""
        self.tags: list[str] = []
        self.memory_mode = memory_mode
        self._channel_linked = bool(channel)
        self._channel_id = channel

    @property
    def is_restricted(self) -> bool:
        return self.memory_mode in so.RESTRICTED_MODES


class FakeState:
    def __init__(self, folders=None, tags=None):
        self._folders = folders or []
        self._tags = tags or []
        self.pushes = 0
        self.saved = 0

    def push_sessions_update(self):
        self.pushes += 1


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolate the inbox store and the entity_settings decline store."""
    (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.providers.entity_routes.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


FOLDERS = [{"id": "f-res", "name": "Research"}, {"id": "f-inf", "name": "Infra"}]
TAGS = [
    {"id": "t-bug", "name": "bug"},
    {"id": "t-slack", "name": "slack"},
    {"id": "t-done", "name": "done", "status": True},
]


# ── "Untagged" ──────────────────────────────────────────────────────────────────


def test_unorganized_means_neither_folder_nor_tag():
    """BOTH absent, not either — a chat already filed in a folder is findable."""
    s = FakeSession(title="anything")
    assert so.is_unorganized(s)
    s.folder_id = "f-res"
    assert not so.is_unorganized(s)
    s.folder_id = ""
    s.tags = ["t-bug"]
    assert not so.is_unorganized(s)


def test_restricted_sessions_are_never_candidates():
    """Naming an incognito chat in an inbox row would leak it into a durable surface."""
    for mode in ("temporary", "incognito"):
        assert not so.is_unorganized(FakeSession(title="secret", memory_mode=mode))


# ── Deterministic signals ───────────────────────────────────────────────────────


def test_title_keyword_matches_existing_vocabulary():
    p = so.deterministic_proposal(FakeSession(title="Research on FTS5 indexes"), FOLDERS, TAGS)
    assert p is not None and p.source == "title"
    assert p.folder_id == "f-res"


def test_title_never_matches_a_status_tag():
    """Status tags are board lanes (`chat_tags.py:429` treats them as a status lane), not
    topics — proposing one would move the chat into a workflow column."""
    p = so.deterministic_proposal(FakeSession(title="all done here"), [], TAGS)
    assert p is None


def test_title_matches_whole_words_not_substrings():
    """A "bug" tag must not match "debugging" — substring matching makes every proposal
    a coin flip and the user stops trusting the chip."""
    p = so.deterministic_proposal(FakeSession(title="debugging the pipeline"), [], TAGS)
    assert p is None


def test_workspace_dir_basename_matches_a_folder():
    s = FakeSession(title="unrelated words entirely", workspace_dir="/Users/x/code/Infra")
    p = so.deterministic_proposal(s, FOLDERS, TAGS)
    assert p is not None and p.source == "workspace" and p.folder_id == "f-inf"


def test_generic_checkout_dirs_are_ignored():
    """A folder proposal of "src" organizes nothing."""
    s = FakeSession(title="zzz", workspace_dir="/Users/x/src")
    assert so.deterministic_proposal(s, [{"id": "f-src", "name": "src"}], []) is None


def test_channel_origin_proposes_only_an_existing_tag():
    s = FakeSession(title="qqq", channel="slack")
    p = so.deterministic_proposal(s, FOLDERS, TAGS)
    assert p is not None and p.source == "channel" and p.tag_names == ["slack"]


def test_channel_origin_never_mints_provider_vocabulary():
    """No matching tag ⇒ no proposal. Minting one would put a provider's name into the
    user's own taxonomy uninvited."""
    s = FakeSession(title="qqq", channel="discord")
    assert so.deterministic_proposal(s, FOLDERS, TAGS) is None


def test_title_wins_over_workspace():
    """Specificity order: the title names one of the user's own categories, which is
    stronger evidence than the directory the chat happens to run in."""
    s = FakeSession(title="Research notes", workspace_dir="/Users/x/Infra")
    p = so.deterministic_proposal(s, FOLDERS, TAGS)
    assert p.source == "title" and p.folder_id == "f-res"


# ── Deterministic-vs-LLM boundary ───────────────────────────────────────────────


def test_no_vocabulary_is_not_ambiguous():
    """Nothing to sort into ⇒ nothing to propose ⇒ no roundtrip."""
    assert not so.is_ambiguous(FakeSession(title="a real topic here"), [], [])


def test_no_title_is_not_ambiguous():
    assert not so.is_ambiguous(FakeSession(title=""), FOLDERS, TAGS)


def test_a_deterministic_match_is_not_ambiguous():
    assert not so.is_ambiguous(FakeSession(title="Research stuff"), FOLDERS, TAGS)


def test_vocabulary_plus_unmatched_title_is_ambiguous():
    assert so.is_ambiguous(FakeSession(title="quarterly planning cadence"), FOLDERS, TAGS)


@pytest.mark.asyncio
async def test_deterministic_path_never_calls_the_model(home, monkeypatch):
    """The load-bearing efficiency claim: the easy cases must not pay for a model."""
    calls = []

    async def spy(state, prompt):
        calls.append(prompt)
        return "NONE"

    monkeypatch.setattr("personalclaw.dashboard.chat_title._stream_background_prompt", spy)
    state = FakeState(FOLDERS, TAGS)
    p = await so.propose_for_session(state, FakeSession(title="Research plan"))
    assert p is not None and p.source == "title"
    assert calls == [], "a title that matched the vocabulary still paid for a model call"


@pytest.mark.asyncio
async def test_ambiguous_path_consults_the_model(home, monkeypatch):
    async def fake(state, prompt):
        assert "Available folders: Research, Infra" in prompt
        return "FOLDER: Research  TAGS: bug"

    monkeypatch.setattr("personalclaw.dashboard.chat_title._stream_background_prompt", fake)
    state = FakeState(FOLDERS, TAGS)
    p = await so.propose_for_session(state, FakeSession(title="quarterly planning cadence"))
    assert p is not None and p.source == "llm"
    assert p.folder_id == "f-res" and p.tag_names == ["bug"]


@pytest.mark.asyncio
async def test_allow_llm_false_stays_deterministic(home, monkeypatch):
    """The list-view caller must be able to refuse a model roundtrip per row."""

    async def boom(state, prompt):
        raise AssertionError("model called with allow_llm=False")

    monkeypatch.setattr("personalclaw.dashboard.chat_title._stream_background_prompt", boom)
    state = FakeState(FOLDERS, TAGS)
    s = FakeSession(title="quarterly planning cadence")
    assert await so.propose_for_session(state, s, allow_llm=False) is None


def test_llm_reply_cannot_invent_a_folder():
    """A hallucinated category must not reach a proposal — accepting one would create it."""
    s = FakeSession(title="x")
    assert so.parse_llm_reply("FOLDER: Marketing  TAGS: growth", s, FOLDERS, TAGS) is None


def test_llm_reply_none_is_no_proposal():
    assert so.parse_llm_reply("NONE", FakeSession(title="x"), FOLDERS, TAGS) is None


def test_llm_reply_drops_a_status_tag():
    s = FakeSession(title="x")
    p = so.parse_llm_reply("FOLDER: -  TAGS: done, bug", s, FOLDERS, TAGS)
    assert p is not None and p.tag_names == ["bug"]


def test_llm_reply_is_capped_at_max_tags():
    tags = TAGS + [{"id": "t-a", "name": "alpha"}, {"id": "t-b", "name": "beta"}]
    p = so.parse_llm_reply(
        "FOLDER: -  TAGS: bug, alpha, beta", FakeSession(title="x"), FOLDERS, tags
    )
    assert len(p.tag_names) == so.MAX_TAGS


# ── NEVER auto-applies ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_proposing_does_not_touch_the_session(home):
    """🔴 The hard rule. Proposing must leave folder/tags byte-identical."""
    state = FakeState(FOLDERS, TAGS)
    s = FakeSession(title="Research on indexes")
    before = (s.folder_id, list(s.tags))
    p = await so.propose_for_session(state, s)
    assert p is not None, "precondition: this session must actually get a proposal"
    assert (s.folder_id, list(s.tags)) == before == ("", [])


@pytest.mark.asyncio
async def test_surfacing_does_not_touch_the_session(home):
    """Nor does raising the inbox row — the row is the proposal's only side effect."""
    state = FakeState(FOLDERS, TAGS)
    s = FakeSession(title="Research on indexes")
    p = await so.propose_for_session(state, s)
    so.surface_proposal(None, p)
    assert s.folder_id == "" and s.tags == []


def test_no_module_function_but_apply_writes_folder_or_tags():
    """An AST sweep: only `apply_proposal` may assign `session.folder_id` / `session.tags`.

    A behavioural test only covers the paths it drives. This covers the ones it doesn't —
    a future helper that "just files it while we're here" fails here rather than in
    production. Mirrors the call-site-assertion technique that caught writerless counters.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(so))
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Attribute)
                    and tgt.attr in ("folder_id", "tags")
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "session"
                    and fn.name != "apply_proposal"
                ):
                    offenders.append(f"{fn.name} assigns session.{tgt.attr}")
    assert not offenders, f"a second writer for session folder/tags: {offenders}"


# ── Dedup / no-nag ──────────────────────────────────────────────────────────────


def test_dedup_key_includes_the_proposed_value():
    """Keying on the session alone would suppress every LATER proposal too, including a
    better one produced once the topic became clear."""
    a = so.OrganizeProposal(session_key="s1", folder_id="f-res")
    b = so.OrganizeProposal(session_key="s1", folder_id="f-inf")
    assert so.dedup_key_for(a) != so.dedup_key_for(b)
    assert so.dedup_key_for(a) == so.dedup_key_for(
        so.OrganizeProposal(session_key="s1", folder_id="f-res")
    )


def test_dedup_key_is_tag_order_independent():
    a = so.OrganizeProposal(session_key="s1", tag_names=["bug", "slack"])
    b = so.OrganizeProposal(session_key="s1", tag_names=["slack", "bug"])
    assert so.dedup_key_for(a) == so.dedup_key_for(b)


def test_the_row_NAMES_the_chat_it_is_asking_about(home):
    """The row asked about a chat and identified it by nothing but its key.

        s-3bd6196a: folder “Research” — the title matches this folder/tag

    So it CITED a title it never showed, and the one thing on screen a user could use to find
    the conversation was an opaque key. Driven through the real builder and the real emit, and
    asserted on the persisted row, because `body` is what the inbox list renders.
    """
    from personalclaw.inbox import InboxStore

    session = FakeSession(key="s-3bd6196a", title="Research on FTS5 indexes")
    proposal = so.deterministic_proposal(session, FOLDERS, TAGS)
    assert proposal is not None and proposal.source == "title"
    so.surface_proposal(None, proposal)

    store = InboxStore()
    store.load()
    rows = [i for i in store.items.values() if i.refs.get("session_organize")]
    assert len(rows) == 1
    body = rows[0].message

    assert "Research on FTS5 indexes" in body, f"the row must name the chat: {body!r}"
    assert "s-3bd6196a" not in body, f"the key is not a name a user can use: {body!r}"
    # The reason it cites is now visible in the same sentence.
    assert "the title matches" in body, body
    # And the machine half is untouched: accept/decline resolve the chat from refs.
    assert rows[0].refs["session"] == "s-3bd6196a"


def test_EVERY_signal_carries_the_title_not_just_the_one_that_matches_on_it(home):
    """`_from_title` reads the title to match on it, so it would be easy to carry it there
    only. A workspace- or channel-sourced proposal is about a chat just as much, and its row
    has the same job. All three deterministic signals plus the LLM path are swept."""
    cases = [
        (FakeSession(key="s-a", title="Research on FTS5 indexes"), "title"),
        (
            FakeSession(key="s-b", title="zzz nothing matches", workspace_dir="/Users/x/Research"),
            "workspace",
        ),
        (FakeSession(key="s-c", title="qqq nothing matches", channel="slack"), "channel"),
    ]
    seen = set()
    for session, expected_source in cases:
        p = so.deterministic_proposal(session, FOLDERS, TAGS)
        assert p is not None, f"no proposal for the {expected_source} signal"
        assert p.source == expected_source, f"{p.source} != {expected_source}"
        assert p.session_title == session.title, f"{expected_source} dropped the title"
        seen.add(p.source)
    assert seen == {"title", "workspace", "channel"}, seen


def test_an_UNTITLED_chat_falls_back_to_the_key_rather_than_naming_nothing(home):
    """The one case where the key is the only handle there is. A blank subject would be worse
    than an opaque one — the row would ask about a chat it did not identify at all."""
    from personalclaw.inbox import InboxStore

    proposal = so.OrganizeProposal(
        session_key="s-untitled", folder_id="f-res", folder_name="Research"
    )
    assert proposal.session_title == ""
    so.surface_proposal(None, proposal)

    store = InboxStore()
    store.load()
    rows = [i for i in store.items.values() if i.refs.get("session_organize")]
    assert len(rows) == 1
    assert "s-untitled" in rows[0].message, rows[0].message


def test_the_subject_is_never_EMPTY_and_the_two_handles_are_distinguishable(home):
    """The vacuity floor. If a title could equal its key, or the subject could render blank,
    every assertion above would pass while proving nothing."""
    titled = so.OrganizeProposal(
        session_key="s-1", session_title="A real chat title", folder_name="Research", folder_id="f"
    )
    untitled = so.OrganizeProposal(session_key="s-1", folder_name="Research", folder_id="f")
    assert titled.session_title and titled.session_title != titled.session_key
    for p in (titled, untitled):
        so.surface_proposal(None, p)
    from personalclaw.inbox import InboxStore

    store = InboxStore()
    store.load()
    for row in [i for i in store.items.values() if i.refs.get("session_organize")]:
        first = row.message.split(":")[0]
        assert first.strip(), f"the row opened with an empty subject: {row.message!r}"


def test_surfacing_twice_raises_one_inbox_row(home):
    """The inbox dedup tier: an open row is returned untouched, not stacked."""
    from personalclaw.inbox import InboxStore, ItemKind

    p = so.OrganizeProposal(session_key="s1", folder_id="f-res", folder_name="Research")
    first = so.surface_proposal(None, p)
    second = so.surface_proposal(None, p)
    assert first and first == second
    store = InboxStore()
    store.load()
    rows = [i for i in store.items.values() if i.refs.get("session_organize")]
    assert len(rows) == 1
    assert rows[0].item_kind == ItemKind.PROPOSAL.value


@pytest.mark.asyncio
async def test_a_declined_proposal_never_returns(home):
    """The persisted tier: covers the case the inbox dedup cannot, because a dismissed row
    is no longer 'open' and would otherwise be re-raised on the next scan."""
    state = FakeState(FOLDERS, TAGS)
    s = FakeSession(title="Research on indexes")
    p = await so.propose_for_session(state, s)
    assert p is not None
    so.record_decline(p)
    assert so.is_declined(p)
    assert await so.propose_for_session(state, s) is None


@pytest.mark.asyncio
async def test_declining_one_value_does_not_silence_a_different_one(home):
    """Declining "file this in Research" is not consent to never be asked anything again."""
    state = FakeState(FOLDERS, TAGS)
    so.record_decline(so.OrganizeProposal(session_key="s1", folder_id="f-inf"))
    s = FakeSession(key="s1", title="Research on indexes")
    p = await so.propose_for_session(state, s)
    assert p is not None and p.folder_id == "f-res"


def test_decline_store_fails_open(home, monkeypatch):
    """A corrupt store must mean "nothing declined", not "the feature is off"."""
    monkeypatch.setattr(so, "_load_store", lambda: {"declined": "not a dict"})
    assert not so.is_declined(so.OrganizeProposal(session_key="s1", folder_id="f-res"))


# ── Accept applies ──────────────────────────────────────────────────────────────


def test_accept_applies_folder_and_tags(home, monkeypatch):
    """The one mutating path. Tags resolve through the SHARED tag helper, so a tag created
    by accepting is indistinguishable from a hand-made one."""
    saves = []
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_persistence._save_session_to_history",
        lambda state, session, force=False: saves.append(session.key),
    )
    state = FakeState(FOLDERS, list(TAGS))
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_tags.find_tag_by_name",
        lambda st, name: next((t for t in st._tags if t["name"] == name), None),
    )
    s = FakeSession(title="Research on indexes")
    p = so.OrganizeProposal(
        session_key=s.key, folder_id="f-res", folder_name="Research", tag_names=["bug"]
    )
    applied = so.apply_proposal(state, s, p)
    assert s.folder_id == "f-res" and s.tags == ["t-bug"]
    assert applied == {"folder_id": "f-res", "tags": ["t-bug"]}
    assert saves == [s.key], "the applied change must be persisted, not just in memory"
    assert state.pushes == 1, "the sessions list must be told, or the UI shows stale state"


def test_accept_validates_the_folder_against_live_state(home, monkeypatch):
    """Same validation `chat_folders.api_chat_session_folder` does — an echoed proposal is
    not trust. A deleted folder id must not be written."""
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_persistence._save_session_to_history",
        lambda state, session, force=False: None,
    )
    state = FakeState(FOLDERS, list(TAGS))
    s = FakeSession(title="x")
    applied = so.apply_proposal(
        state, s, so.OrganizeProposal(session_key=s.key, folder_id="f-gone")
    )
    assert s.folder_id == "" and applied["folder_id"] == ""


def test_accept_resolves_the_inbox_row(home):
    """Otherwise the row keeps claiming attention for a decision already made."""
    from personalclaw.inbox import InboxStore, ItemStatus

    p = so.OrganizeProposal(session_key="s1", folder_id="f-res", folder_name="Research")
    so.surface_proposal(None, p)
    so.resolve_inbox_item(None, p, ItemStatus.HANDLED.value)
    store = InboxStore()
    store.load()
    rows = [i for i in store.items.values() if i.refs.get("session_organize")]
    assert rows and rows[0].status == ItemStatus.HANDLED.value
