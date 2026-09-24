"""Branch mechanic (CHAT-CRAFT CC-7) — the four cases the atom enumerates, plus the
lineage read the child's breadcrumb depends on.

Branch is not rewind. Rewind REPLACES a timeline (the old tail becomes superseded);
Branch DUPLICATES one (both stay live and equal). The backend primitive is the already
shipped ``POST /api/chat/sessions/{session}/fork``, so nothing here re-tests forking in
general — it pins the four properties that each have a distinct failure mode:

  * branching from a USER message and from an ASSISTANT message — the index is into the
    backend's *visible* user/assistant list, so a role-dependent off-by-one is the
    natural bug and it produces a plausible-looking wrong transcript, not an error;
  * branching the SAME message repeatedly — a second branch off one message must not
    collide with the first;
  * a branch OF a branch — ``forked_from`` must chain to the immediate parent, or the
    breadcrumb walks to the wrong origin;

and the contract the breadcrumb reads: ``GET /api/chat/sessions/{session}`` must SERVE
``forked_from``, because that request is what a page reload makes. A parent held only in
navigation state is a breadcrumb that disappears on refresh.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state


def _visible(session) -> list[str]:
    """The session's transcript as the fork endpoint sees it (chat_fork.py:119)."""
    return [m["content"] for m in session.messages if m["role"] in ("user", "assistant")]


def _seed(state, name: str, title: str = "") -> object:
    """A four-message conversation: u1 a1 u2 a2 — visible indices 0..3."""
    s = state.get_or_create_session(name)
    if title:
        s.title = title
        s._titled = True
    s.append("user", "u1", "msg msg-u")
    s.append("assistant", "a1", "msg msg-a")
    s.append("user", "u2", "msg msg-u")
    s.append("assistant", "a2", "msg msg-a")
    s.drain()
    return s


class TestBranchAtEitherRole:
    """`at_message_index` is INCLUSIVE into the visible list, and the visible list
    interleaves both roles — so the same index means a different cut depending on which
    role sits there. Both must land exactly."""

    @pytest.mark.asyncio
    async def test_branch_from_a_user_message_cuts_at_that_message(self, tmp_path):
        state = _make_state(tmp_path)
        _seed(state, "src")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 2})
            assert r.status == 200
            data = await r.json()
        child = state._sessions.get(data["key"])
        # Inclusive of the clicked USER message, and it is the LAST message — the branch
        # opens with the question unanswered, ready to take a different direction.
        assert _visible(child) == ["u1", "a1", "u2"], _visible(child)
        assert data["messages"] == 3

    @pytest.mark.asyncio
    async def test_branch_from_an_assistant_message_carries_the_answer(self, tmp_path):
        state = _make_state(tmp_path)
        _seed(state, "src")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 1})
            assert r.status == 200
            data = await r.json()
        child = state._sessions.get(data["key"])
        # The common case ("take this analysis in two directions"): the branch must
        # INCLUDE the answer being branched from. Off by one the other way would cut it
        # off and hand the user a branch that never saw the analysis.
        assert _visible(child) == ["u1", "a1"], _visible(child)
        assert _visible(child)[-1] == "a1"

    @pytest.mark.asyncio
    async def test_the_two_roles_at_adjacent_indices_differ_by_exactly_one_message(self, tmp_path):
        """The off-by-one guard: index 1 (assistant) and index 2 (user) must produce
        transcripts differing by exactly the user message between them. If either side
        silently shifted, this equality breaks while both branches still 'work'."""
        state = _make_state(tmp_path)
        _seed(state, "src")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            at_assistant = await (
                await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 1})
            ).json()
            at_user = await (
                await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 2})
            ).json()
        a = _visible(state._sessions.get(at_assistant["key"]))
        u = _visible(state._sessions.get(at_user["key"]))
        assert u == a + ["u2"], f"assistant-cut={a} user-cut={u}"


class TestBranchTheSameMessageRepeatedly:
    @pytest.mark.asyncio
    async def test_same_message_branches_twice_into_two_independent_children(self, tmp_path):
        state = _make_state(tmp_path)
        _seed(state, "src", title="Original")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            first = await (
                await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 1})
            ).json()
            second = await (
                await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 1})
            ).json()

        # Distinct slots — a repeat branch must not collide with, or overwrite, the first.
        assert first["key"] != second["key"]
        c1, c2 = state._sessions.get(first["key"]), state._sessions.get(second["key"])
        assert _visible(c1) == _visible(c2) == ["u1", "a1"]
        # Both point at the same origin, and the origin is untouched by either.
        assert c1.forked_from == c2.forked_from == "dashboard:src"
        assert _visible(state._sessions.get("src")) == ["u1", "a1", "u2", "a2"]

    @pytest.mark.asyncio
    async def test_branches_diverge_independently_after_the_split(self, tmp_path):
        """Both timelines stay live and equal — that is the whole mechanic. Writing into
        one branch must not appear in the other, nor in the parent."""
        state = _make_state(tmp_path)
        _seed(state, "src")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            a = await (
                await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 1})
            ).json()
            b = await (
                await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 1})
            ).json()
        left, right = state._sessions.get(a["key"]), state._sessions.get(b["key"])
        left.append("user", "left direction", "msg msg-u")
        left.drain()
        right.append("user", "right direction", "msg msg-u")
        right.drain()
        assert _visible(left) == ["u1", "a1", "left direction"]
        assert _visible(right) == ["u1", "a1", "right direction"]
        assert _visible(state._sessions.get("src")) == ["u1", "a1", "u2", "a2"]


class TestBranchOfABranch:
    @pytest.mark.asyncio
    async def test_nesting_chains_to_the_immediate_parent_not_the_root(self, tmp_path):
        state = _make_state(tmp_path)
        _seed(state, "root", title="Original")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            mid = await (
                await client.post("/api/chat/sessions/root/fork", json={"at_message_index": 1})
            ).json()
            mid_session = state._sessions.get(mid["key"])
            mid_session.append("user", "u3", "msg msg-u")
            mid_session.append("assistant", "a3", "msg msg-a")
            mid_session.drain()
            leaf = await (
                await client.post(
                    f"/api/chat/sessions/{mid['key']}/fork", json={"at_message_index": 2}
                )
            ).json()

        leaf_session = state._sessions.get(leaf["key"])
        # The breadcrumb walks ONE hop: a leaf must name the intermediate branch, not the
        # root, or "Branched from" sends the user to a conversation they never branched.
        assert leaf_session.forked_from == f"dashboard:{mid['key']}"
        assert leaf_session.forked_from != "dashboard:root"
        # …and the leaf carries the intermediate's own added history, indexed against the
        # INTERMEDIATE's visible list (u1 a1 u3) — not the root's.
        assert _visible(leaf_session) == ["u1", "a1", "u3"], _visible(leaf_session)

    @pytest.mark.asyncio
    async def test_three_deep_still_chains_one_hop_at_a_time(self, tmp_path):
        state = _make_state(tmp_path)
        _seed(state, "root")
        app = _make_app(state)
        keys = ["root"]
        async with TestClient(TestServer(app)) as client:
            for _ in range(3):
                child = await (
                    await client.post(f"/api/chat/sessions/{keys[-1]}/fork", json={})
                ).json()
                keys.append(child["key"])
        for parent, child in zip(keys, keys[1:]):
            assert state._sessions.get(child).forked_from == f"dashboard:{parent}"


class TestBreadcrumbSurvivesAReload:
    """The breadcrumb reads persisted state through the endpoint a reload calls. If the
    detail endpoint does not serve it, the breadcrumb can only exist in whatever
    navigation created the branch — and vanishes on the first refresh."""

    @pytest.mark.asyncio
    async def test_detail_endpoint_serves_forked_from_and_the_parents_title(self, tmp_path):
        state = _make_state(tmp_path)
        _seed(state, "src", title="Q3 planning thread")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            child = await (
                await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 1})
            ).json()
            # Exactly the request a browser reload of #/chat/<key> makes.
            detail = await (await client.get(f"/api/chat/sessions/{child['key']}")).json()

        assert detail["forked_from"] == "dashboard:src"
        assert detail["forked_from_title"] == "Q3 planning thread"

    @pytest.mark.asyncio
    async def test_an_unbranched_session_reports_no_lineage(self, tmp_path):
        state = _make_state(tmp_path)
        _seed(state, "plain")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            detail = await (await client.get("/api/chat/sessions/plain")).json()
        # Empty, not absent-and-undefined-shaped: the field is always present so the
        # frontend branches on one value rather than on presence.
        assert detail["forked_from"] == ""
        assert detail["forked_from_title"] == ""

    @pytest.mark.asyncio
    async def test_title_is_resolved_at_read_time_so_renaming_the_parent_follows(self, tmp_path):
        """The child's own title ("Fork of X") is a copy frozen at branch time. The
        breadcrumb must NOT be that copy — rename the parent and the breadcrumb follows."""
        state = _make_state(tmp_path)
        parent = _seed(state, "src", title="Old name")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            child = await (await client.post("/api/chat/sessions/src/fork", json={})).json()
            assert child["title"] == "Fork of Old name"  # the frozen copy
            parent.title = "New name"
            detail = await (await client.get(f"/api/chat/sessions/{child['key']}")).json()

        assert detail["forked_from_title"] == "New name"
        assert detail["title"] == "Fork of Old name", "the child's own title is untouched"

    @pytest.mark.asyncio
    async def test_a_deleted_origin_reports_an_empty_title_not_a_dead_link(self, tmp_path):
        """ "" means the origin no longer resolves, which is what lets the frontend render
        unlinked text instead of a link into nothing. `forked_from` still says the session
        WAS branched — that history is not rewritten."""
        state = _make_state(tmp_path)
        _seed(state, "src", title="Doomed")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            child = await (await client.post("/api/chat/sessions/src/fork", json={})).json()
            state._sessions.pop("src", None)
            if state.conversation_log:
                path = state.conversation_log._path("dashboard:src")
                if path.exists():
                    path.unlink()
                state.conversation_log._meta_cache.pop("dashboard:src", None)
            detail = await (await client.get(f"/api/chat/sessions/{child['key']}")).json()

        assert detail["forked_from"] == "dashboard:src"
        assert detail["forked_from_title"] == ""

    @pytest.mark.asyncio
    async def test_lineage_survives_the_parent_being_disk_only(self, tmp_path):
        """A parent evicted from memory (gateway restart / restore_sessions=false) still
        names itself: the title comes off the persisted metadata line."""
        state = _make_state(tmp_path)
        _seed(state, "src", title="Long-running record")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            child = await (await client.post("/api/chat/sessions/src/fork", json={})).json()
            # Drop the live parent, keeping its persisted history — the fork endpoint
            # already flushed it to disk.
            state._sessions.pop("src", None)
            detail = await (await client.get(f"/api/chat/sessions/{child['key']}")).json()

        assert detail["forked_from"] == "dashboard:src"
        assert detail["forked_from_title"] == "Long-running record"


class TestExistingRefusalsAreReusedNotReinvented:
    @pytest.mark.asyncio
    async def test_a_non_persistent_session_refuses_rather_than_branching(self, tmp_path):
        """The frontend hides the affordance on temporary/incognito (canFork reads the
        session's memory_mode); this pins the refusal the hiding is derived FROM, so the
        two can't drift into a visible button that errors."""
        state = _make_state(tmp_path)
        s = _seed(state, "temp")
        s.memory_mode = "temporary"
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/chat/sessions/temp/fork", json={"at_message_index": 1})
            body = await r.json()
        assert r.status == 400
        assert body["error"] == "cannot fork a non-persistent session"

    @pytest.mark.asyncio
    async def test_the_fork_cap_429_carries_a_readable_sentence(self, tmp_path):
        """errText() (web/src/lib/errText.ts) surfaces a JSON body's `error` string
        verbatim, so this text is literally what the user reads. A bare status or a
        serialized object would reach them as "HTTP 429"."""
        import personalclaw.dashboard.chat_fork as chat_fork

        state = _make_state(tmp_path)
        _seed(state, "src")
        app = _make_app(state)
        original = chat_fork._MAX_SESSIONS_FOR_FORK
        chat_fork._MAX_SESSIONS_FOR_FORK = 1
        try:
            async with TestClient(TestServer(app)) as client:
                r = await client.post("/api/chat/sessions/src/fork", json={})
                body = await r.json()
        finally:
            chat_fork._MAX_SESSIONS_FOR_FORK = original
        assert r.status == 429
        assert body["error"] == "session cap reached (1)"
        assert isinstance(body["error"], str) and body["error"][0].isalpha()

    @pytest.mark.asyncio
    async def test_an_out_of_range_index_is_refused_not_clamped(self, tmp_path):
        """A clamp would make a mis-translated frontend index look like it worked. The
        endpoint refuses, which is what makes an index bug findable."""
        state = _make_state(tmp_path)
        _seed(state, "src")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 9})
            body = await r.json()
        assert r.status == 400
        assert "out of range" in body["error"]
        assert "have 4 visible messages" in body["error"]
