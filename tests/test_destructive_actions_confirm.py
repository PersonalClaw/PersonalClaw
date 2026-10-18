"""A destructive mutation states its intent before it runs (#606, #604).

Two routes destroyed state on a bare POST while a SIBLING in the same file already gated:

* ``POST /api/knowledge/tags/{id}/merge`` rewrites every item carrying the source tag and then
  DELETES that tag from the taxonomy — strictly more than ``delete_tag``, whose UI prompt spells
  out its blast radius four lines away in ``TagManager.tsx``. ``merge_items``, in the very same
  handler module, already requires ``confirm: true`` and says why.
* ``POST /api/task-lists/{list_id}/reset`` is the only path that empties ``execution_notes`` —
  the record of what was actually done on each task — and its control is a bare icon button.

The second half of #606 is a different defect in the same route: merging a nonexistent tag
answered ``200 {"ok": true, "moved": 0, "already": 0}``, because the store returned that dict for
a missing tag. That is byte-identical to a legitimate merge of a tag carrying no items, so
"absent" was indistinguishable from "zero" — a success shape that could mean "your subject does
not exist".

`TestNoNewUngatedDestructiveRoute` is the part that stops the class coming back: the two fixes
above are point fixes, and nothing about them prevents the next destructive verb shipping bare.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "k.db")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _merge(store, tag_id, body):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request("POST", f"/api/knowledge/tags/{tag_id}/merge", app=app)
    req.match_info["id"] = str(tag_id)

    async def _json():
        return body

    req.json = _json
    from personalclaw.dashboard.handlers import knowledge as H

    return _run(H.merge_tag(req))


def _two_tags(store):
    store.create_typed_item(item_type="note", title="a", content="x", tags=["src"])
    store.create_typed_item(item_type="note", title="b", content="y", tags=["dst"])
    ids = {row["name"]: row["id"] for row in store.list_tags()}
    return ids["src"], ids["dst"]


def _empty_tag(store):
    """A tag that EXISTS but carries no items — the discriminator for absent-vs-zero."""
    iid = store.create_typed_item(item_type="note", title="c", content="z", tags=["lonely"])
    store.delete_item(iid)
    return {row["name"]: row["id"] for row in store.list_tags()}["lonely"]


class TestTagMergeAsksFirst:
    def test_a_bare_merge_is_refused(self, store):
        src, dst = _two_tags(store)
        resp = _merge(store, src, {"into": dst})
        assert resp.status == 400
        assert b"confirm_required" in resp.body
        # and it did NOT happen: the source tag is still in the taxonomy
        assert src in {t["id"] for t in store.list_tags()}

    def test_a_confirmed_merge_runs(self, store):
        src, dst = _two_tags(store)
        resp = _merge(store, src, {"into": dst, "confirm": True})
        assert resp.status == 200
        assert src not in {t["id"] for t in store.list_tags()}

    def test_confirm_is_not_a_substitute_for_into(self, store):
        src, _ = _two_tags(store)
        resp = _merge(store, src, {"confirm": True})
        assert resp.status == 400
        assert b"into_required" in resp.body


class TestAMissingTagIsNotAZeroMerge:
    def test_an_unknown_source_is_404_not_ok_true(self, store):
        _, dst = _two_tags(store)
        resp = _merge(store, 999999, {"into": dst, "confirm": True})
        assert resp.status == 404, "a tag that does not exist must not answer with a success shape"
        assert b"tag_not_found" in resp.body

    def test_an_unknown_target_is_404_too(self, store):
        src, _ = _two_tags(store)
        resp = _merge(store, src, {"into": 999999, "confirm": True})
        assert resp.status == 404
        # refusing must not have deleted the source on the way out
        assert src in {t["id"] for t in store.list_tags()}

    def test_a_genuinely_empty_merge_still_succeeds(self, store):
        """The discriminator. If 404 were implemented by "moved == 0" this would break, and
        absent would still be conflated with zero — just in the other direction."""
        _, dst = _two_tags(store)
        lonely = _empty_tag(store)
        resp = _merge(store, lonely, {"into": dst, "confirm": True})
        assert resp.status == 200, "an existing tag carrying no items is a legal merge, not a 404"

    def test_the_store_names_which_side_is_missing(self, store):
        src, dst = _two_tags(store)
        with pytest.raises(LookupError, match="source"):
            store.merge_tags(999999, dst)
        with pytest.raises(LookupError, match="target"):
            store.merge_tags(src, 999999)
