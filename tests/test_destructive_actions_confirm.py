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
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw
from personalclaw.knowledge.store import KnowledgeStore

SRC = Path(personalclaw.__file__).parent


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


class TestNoNewUngatedDestructiveRoute:
    """The rail. The two fixes above are point fixes; this is what makes the class stay fixed.

    Every POST route whose path carries a destructive verb must gate on ``confirm`` in its own
    handler, or be listed in ``_REVIEWED_UNGATED`` with a reason. A new ungated destructive verb
    then reds here instead of shipping.
    """

    # Verbs that destroy or discard state under a name that is NOT "delete". Delete routes are
    # excluded deliberately: the verb itself warns, and they are gated at the UI by `confirmDelete`.
    _DESTRUCTIVE = re.compile(r"/(merge|reset|purge|wipe|clear|prune|revoke|rotate)\b")

    # path -> why it legitimately needs no confirm gate. Every entry was read before being
    # written; "pre-existing" is not a reason, it is a TODO wearing one.
    _REVIEWED_UNGATED: dict[str, str] = {
        # A SECURITY action, and friction here is the harm: the owner reaches for this when a
        # device may be compromised, and it must take effect immediately. Confirming a lockout
        # protects nothing and delays containment.
        "/api/devices/{id}/revoke": "security containment — must not be slowed by a prompt",
        # RECOVERY, not destruction: it archives the existing SEL log under a timestamp and starts
        # a fresh HMAC chain, and it exists to recover from a chain that is already broken.
        "/api/sel/rotate": "recovery path; archives rather than discards",
        # Notifications are transient by construction and re-emitted by their sources; clearing
        # them discards a view, not a record.
        "/api/notifications/clear": "transient view state, re-emitted by its sources",
        # The INVERSE of destructive: it un-suppresses a producer, restoring visibility that was
        # previously hidden. Nothing is lost by running it.
        "/api/feedback/producers/clear": "un-suppresses — restores state rather than removing it",
    }

    def _post_routes(self) -> list[tuple[str, str, Path]]:
        found = []
        pat = re.compile(r"""add_post\(\s*["']([^"']+)["']\s*,\s*([A-Za-z_][\w.]*)""")
        for py in sorted(SRC.rglob("*.py")):
            for path, handler in pat.findall(py.read_text(encoding="utf-8")):
                found.append((path, handler, py))
        return found

    def test_the_route_scan_actually_finds_routes(self):
        # FLOOR 1: if `add_post` registration ever changes shape, every assertion below would pass
        # by iterating nothing. This is the only check that can see a broken scanner.
        routes = self._post_routes()
        assert len(routes) > 100, f"route scan found only {len(routes)} POST routes — scanner broke"

    def test_the_destructive_filter_actually_matches(self):
        # FLOOR 2: and if the verb pattern breaks, the candidate set empties and the real
        # assertion passes vacuously. Two floors, because one cannot see the other's failure.
        candidates = [r for r in self._post_routes() if self._DESTRUCTIVE.search(r[0])]
        assert len(candidates) >= 3, f"destructive-verb filter matched {len(candidates)} routes"

    def test_every_destructive_post_route_gates_on_confirm(self):
        ungated = []
        for path, handler, py in self._post_routes():
            if not self._DESTRUCTIVE.search(path):
                continue
            if path in self._REVIEWED_UNGATED:
                continue
            src = py.read_text(encoding="utf-8")
            name = handler.rsplit(".", 1)[-1]
            m = re.search(rf"^async def {re.escape(name)}\(.*?(?=^async def |\Z)", src, re.S | re.M)
            body = m.group(0) if m else ""
            if 'get("confirm")' not in body and "'confirm'" not in body:
                ungated.append(f"{path} -> {name} ({py.relative_to(SRC)})")
        assert not ungated, "destructive POST routes with no confirm gate:\n" + "\n".join(ungated)

    def test_the_exemption_list_cannot_rot(self):
        """An exemption for a route that no longer exists silently widens the rail."""
        live = {p for p, _, _ in self._post_routes()}
        stale = [p for p in self._REVIEWED_UNGATED if p not in live]
        assert not stale, f"_REVIEWED_UNGATED names routes that no longer exist: {stale}"
