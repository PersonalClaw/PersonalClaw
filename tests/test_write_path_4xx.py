"""The rail for "a wrong TYPE must be a 4xx — never a 500, never a silent mutation" (#766,
#770, #427, #424).

**Not one primitive — one principle.** These four endpoints each mishandled a wrong-typed field, and
each wanted a DIFFERENT correct answer: a 400 for a chat message, coerce-or-ignore for a display
order, ignore-don't-delete for an agent's tool list, and stringify-don't-poison for event metadata.
A
single shared validator would have been the wrong shape for at least three of them.

What they share is that **every one of them had the right answer already written in a sibling**, and
the issues each name it. So each fix adopts its own local precedent rather than inventing a rule:

======  ==================================  ============================================
#766    `POST /api/chat`                    `meta`, four lines below, checks `isinstance`
#770    `PATCH /api/chat/folders/{id}`      `chat_tags.py`'s tag + column updates
#427    `PATCH /api/agents/detail/{name}`    the create path and `PUT /api/agents/{name}`
#424    `clean_event_metadata`               its own docstring's "bounded scalar" promise
======  ==================================  ============================================

A fifth was verified ALREADY FIXED and is not re-fixed here: `POST /api/memory/import` (#591) has
the `isinstance(body, dict)` guard the issue asks for.
"""

from __future__ import annotations

import json
import math
from unittest.mock import AsyncMock, MagicMock

import pytest


def _request(body, *, method="POST", match=None):
    req = MagicMock()
    req.method = method
    req.json = AsyncMock(return_value=body)
    req.match_info = match or {}
    req.get = lambda *a, **k: "dashboard"
    req.headers = {}
    return req


# ── #766: POST /api/chat ──────────────────────────────────────────────────────


class TestChatMessageType:
    """`body.get("message", "")` defends a MISSING key, not a wrong TYPE — so `.strip()` raised
    `AttributeError` and the route answered a bare 500 with no JSON body, while a MISSING message
    correctly answered 400."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [42, [1, 2], True, {"a": 1}, 3.5], ids=repr)
    async def test_a_non_string_message_is_a_400(self, bad):
        from personalclaw.dashboard.chat_handlers import api_chat

        req = _request({"message": bad, "session": ""})
        req.app = {"state": MagicMock()}
        resp = await api_chat(req)
        assert resp.status == 400
        assert "message" in json.loads(resp.body.decode())["error"]

    @pytest.mark.asyncio
    async def test_a_missing_message_is_NOT_caught_by_the_new_type_guard(self):
        """Vacuity floor: the guard must reject a wrong TYPE and not a missing KEY.

        Deliberately asserts what this test can establish. A missing `message` becomes `""` and
        travels on to the route's later checks, which in this mocked state answer 403 — so pinning a
        specific status here would be pinning the mock, not the behaviour. What matters is that the
        new guard did not swallow the missing case into its own error, and that nothing 500s.
        """
        from personalclaw.dashboard.chat_handlers import api_chat

        req = _request({"session": ""})
        req.app = {"state": MagicMock()}
        resp = await api_chat(req)
        assert resp.status != 500
        if resp.status == 400:
            assert "must be a string" not in json.loads(resp.body.decode()).get("error", "")


# ── #770: PATCH /api/chat/folders/{id} ────────────────────────────────────────


class TestFolderOrderCoercion:
    """A bare `int()` raised `ValueError` → 500, while the IDENTICAL payload against the sibling tag
    route answered 200 and ignored it. One of the two was wrong about the same field."""

    def _state(self):
        state = MagicMock()
        state._folders = [{"id": "f1", "name": "F", "order": 3, "collapsed": False}]
        return state

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["notanumber", None, [1], {"a": 1}], ids=repr)
    async def test_an_unparseable_order_is_ignored_not_a_500(self, bad):
        from personalclaw.dashboard.chat_folders import api_chat_folder_update

        state = self._state()
        req = _request({"order": bad}, method="PATCH", match={"id": "f1"})
        req.app = {"state": state}
        resp = await api_chat_folder_update(req)
        assert resp.status == 200
        assert state._folders[0]["order"] == 3, "the prior order must survive a bad value"

    @pytest.mark.asyncio
    async def test_a_real_order_still_applies(self):
        """Vacuity floor: a try/except that swallowed everything would pass the test above and make
        reordering silently stop working."""
        from personalclaw.dashboard.chat_folders import api_chat_folder_update

        state = self._state()
        req = _request({"order": 9}, method="PATCH", match={"id": "f1"})
        req.app = {"state": state}
        await api_chat_folder_update(req)
        assert state._folders[0]["order"] == 9

    @pytest.mark.asyncio
    async def test_a_numeric_STRING_order_still_coerces(self):
        """`"7"` is what an HTML input sends, and the sibling coerces it. Ignoring it would be a
        different regression than the one being fixed."""
        from personalclaw.dashboard.chat_folders import api_chat_folder_update

        state = self._state()
        req = _request({"order": "7"}, method="PATCH", match={"id": "f1"})
        req.app = {"state": state}
        await api_chat_folder_update(req)
        assert state._folders[0]["order"] == 7

    def test_it_matches_the_sibling_it_was_measured_against(self):
        """Pins the AGREEMENT. The issue's whole argument is that the tag route already had the
        right answer for this exact field, so the two must not drift apart again."""
        import inspect

        from personalclaw.dashboard import chat_folders, chat_tags

        folder_src = inspect.getsource(chat_folders.api_chat_folder_update)
        assert 'int(body["order"])' in folder_src
        assert "except (TypeError, ValueError)" in folder_src
        assert "except (TypeError, ValueError)" in inspect.getsource(chat_tags)


# ── #427: PATCH /api/agents/detail/{name} ─────────────────────────────────────


class TestAgentDetailPatch:
    def test_a_non_list_no_longer_DELETES_the_field(self):
        """🔴 The destructive one. `else: data.pop(key, None)` treated "wrong type" as "delete this
        field", so `{"tools": "@personalclaw-core"}` disarmed the live agent's whole MCP tool
        surface and answered `{"ok": true}` — on `personalclaw.json`, the runtime config the ACP
        agent reads.

        Asserted at the source because the handler needs a real `AGENTS_DIR` to drive, and the
        finding is the branch itself: both siblings ignore a non-list, and this one acted on it.
        """
        import inspect

        from personalclaw.dashboard.handlers import agents as A

        src = inspect.getsource(A.api_agent_detail)
        for_list_fields = src.split('for key in ("skills", "tools", "triggers")')[1]
        body = for_list_fields.split("_atomic_json_write")[0]
        assert (
            "data.pop(key, None)" not in body
        ), "a wrong type is being treated as a delete instruction again"
        assert "isinstance(val, list)" in body

    def test_the_scalar_body_guard_is_present(self):
        """A scalar answered 500, and `null` was worse: the PATCH branch is gated on
        `patch_body is not None`, so `null` skipped the mutation and returned a 200 GET body for a
        mutating request. Seven siblings in this same file already do this check."""
        import inspect

        from personalclaw.dashboard.handlers import agents as A

        src = inspect.getsource(A.api_agent_detail)
        assert "isinstance(patch_body, dict)" in src
        assert src.index("isinstance(patch_body, dict)") < src.index(
            "AGENTS_DIR.glob"
        ), "the shape check must run BEFORE the file loop, or a bad body still reaches it"

    def test_the_delete_guard_uses_the_reserved_SET(self):
        """It listed three FILENAMES against a five-name reserved set, covering one of them — and it
        cannot be authoritative in principle, because the match is on the file's INTERNAL name."""
        import inspect

        from personalclaw.agents.defaults import RESERVED_AGENT_NAMES
        from personalclaw.dashboard.handlers import agents as A

        src = inspect.getsource(A.api_agent_detail)
        assert "is_reserved_agent(name)" in src
        assert len(RESERVED_AGENT_NAMES) >= 5, "the set this now consults must be the real one"

    def test_the_write_is_atomic(self):
        """A bare `write_text` truncates the live runtime config if the process dies mid-write.
        `agent.py` and `apps/mcp_bridge.py` both use `_atomic_json_write` for this exact file."""
        import inspect

        from personalclaw.dashboard.handlers import agents as A

        src = inspect.getsource(A.api_agent_detail)
        assert "_atomic_json_write(f, data)" in src
        assert "f.write_text(json.dumps(data" not in src

    def test_the_mutations_are_audited(self):
        """The unguarded path was also the unlogged one — this handler had zero `log_api_access`
        calls while its siblings had two."""
        import inspect

        from personalclaw.dashboard.handlers import agents as A

        src = inspect.getsource(A.api_agent_detail)
        assert src.count("log_api_access") >= 2, "both DELETE and PATCH must audit"


# ── #424: clean_event_metadata ────────────────────────────────────────────────


def _strict_json_roundtrip(payload: object) -> bool:
    """Serialize, then parse the way a BROWSER does.

    Python's `json.loads` accepts the bare `Infinity`/`NaN` tokens that `json.dumps` emits, so a
    plain round-trip in Python proves nothing — which is exactly why this reached production.
    `parse_constant` is the hook that makes the parse as strict as `JSON.parse`.
    """
    text = json.dumps(payload)
    try:
        json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        return True
    except ValueError:
        return False


class TestEventMetadataStaysJSON:
    @pytest.mark.parametrize(
        "bad",
        [1e309, float("inf"), float("-inf"), float("nan")],
        ids=["1e309", "inf", "-inf", "nan"],
    )
    def test_a_non_finite_number_does_not_poison_the_payload(self, bad):
        """🔴 One `POST /events` with `widget_index: 1e309` made the ENTIRE artifacts library
        unreadable: `json.dumps` writes `Infinity`, which is not JSON, so `JSON.parse` rejects the
        whole response — not just that artifact's row."""
        from personalclaw.artifacts.models import clean_event_metadata

        out = clean_event_metadata({"widget_index": bad})
        assert _strict_json_roundtrip(out), f"{bad!r} still serializes to invalid JSON: {out}"

    def test_the_out_of_range_value_is_RECORDED_not_dropped(self):
        """It falls through to the `str()` branch rather than vanishing: this is metadata, and "inf"
        records that something out-of-range arrived instead of hiding it."""
        from personalclaw.artifacts.models import clean_event_metadata

        assert clean_event_metadata({"widget_index": float("inf")}) == {"widget_index": "inf"}

    @pytest.mark.parametrize("good", [0, 3, -7, 2.5, True, False, "x"], ids=repr)
    def test_ordinary_values_are_unchanged(self, good):
        """Vacuity floor. A guard that stringified every number would pass the tests above and
        change the type of every existing metadata value on the wire."""
        from personalclaw.artifacts.models import clean_event_metadata

        assert clean_event_metadata({"k": good}) == {"k": good}

    def test_the_premise_holds(self):
        """Asserted, not assumed: `json.dumps` really does emit a bare token, and Python's own
        loader really does accept it. If either stops being true this rail is measuring nothing."""
        assert json.dumps({"v": float("inf")}) == '{"v": Infinity}'
        assert math.isinf(json.loads('{"v": Infinity}')["v"])
        assert not _strict_json_roundtrip({"v": float("inf")})


# ── the one that was already fixed ────────────────────────────────────────────


def test_memory_import_already_guards_its_body_shape():
    """#591 asked for an `isinstance(body, dict)` guard on `POST /api/memory/import`. It is already
    there. Pinned rather than re-fixed, so the issue can be closed with evidence and so a later
    change cannot quietly remove it."""
    import inspect

    from personalclaw.dashboard.handlers import memory as M

    src = inspect.getsource(M.api_memory_import)
    assert "isinstance(data, dict)" in src
    assert "JSON body must be an object" in src
