"""KL-20 — knowledge as plain files the owner owns, and the four ways that goes wrong.

The atom's clauses are testable in a very specific way: each one names a defect that looks
like working software from the outside. So the suite is organised around the defects rather
than around the functions:

1. **It is an EXPORT, not ownership.** The projection overwrites an edit made in a text editor
   instead of reading it back. Everything else still passes — files appear, front-matter is
   right, the graph is intact — and the one property the atom is about is absent.
   ``test_owner_edit_is_read_back_not_overwritten`` and
   ``test_read_back_keeps_the_owners_own_subheadings``.
2. **The projection retriggers itself.** The content hash covers the front-matter field the
   projection writes, so writing the hash changes the thing it hashed and every pass finds
   work. Nothing errors; the maintenance host just never idles.
   ``test_projection_does_not_retrigger_itself`` and its hash-scope twin.
3. **A two-sided change resolves silently toward the database.** The owner's edit is applied
   over a change made in the app, or the app's change is applied over the owner's file. Either
   way somebody's text is gone and no surface says so.
   ``test_two_sided_change_surfaces_and_writes_nothing``.
4. **Deletion is not really deletion.** A page the owner deleted comes back on the next tick;
   an item deleted in the app leaves its file behind forever.
   ``test_deleted_page_is_not_recreated`` and ``test_deleted_item_leaves_no_file``.

Plus the two that make the whole thing safe to ship: the gate is OFF by default and provably
turns something on (``test_gate_off_writes_nothing`` / ``test_gate_on_projects_files``), and
every test runs against an isolated home — ``test_the_real_home_is_never_touched`` asserts
that rather than trusting it, because ``config_dir`` patching famously misses a store that
captured its path at import time.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from personalclaw.knowledge import maintenance, maintenance_passes
from personalclaw.knowledge import vault as kv
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.memory_vault import (
    CONFLICT_KEY,
    HASH_KEY,
    body_hash,
    parse_frontmatter,
    split_page,
)


def _write_config(home: Path, mode: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(
        json.dumps({"knowledge": {"vault_mode": mode}}), encoding="utf-8"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. Nothing in this suite may touch ``~/.personalclaw``.

    This is the single most dangerous suite in the knowledge area for real-data loss: the
    subject under test WRITES AND DELETES FILES under whatever ``config_dir()`` resolves to.
    ``PERSONALCLAW_HOME`` is set rather than ``config_dir`` monkeypatched, because a patched
    accessor misses a module that resolved its path at import time — and here that would mean
    a projector pointed at the real vault.
    """
    h = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    _write_config(h, "off")
    maintenance.clear_passes()
    yield h
    maintenance.clear_passes()


@pytest.fixture
def store(home):
    """A real store at the CANONICAL path under the isolated home.

    `knowledge_db_path()` rather than a tmp file of our own: the Doctor probe resolves the
    database through that one helper (a second copy of the path once split the store's brain),
    so a suite that put its db somewhere else would leave the probe reading an empty file and
    reporting "no projection" no matter what the projection did.
    """
    s = KnowledgeStore(str(knowledge_db_path()))
    try:
        yield s
    finally:
        s.close()


def _note(store, title, content="some body text", **kw):
    return store.create_typed_item(item_type="note", title=title, content=content, **kw)


def _vault(store, home, mode="two_way"):
    _write_config(home, mode)
    v = kv.vault_for(store)
    assert v is not None, "vault_for returned None for a non-off mode"
    return v


def _page(vault, store, item_id):
    return vault.path / f"items/{kv.page_basename(store.get_item(item_id))}.md"


def _touch_later(path: Path) -> None:
    """Age a file's mtime forward so a change is unambiguous to a stat-based candidate scan.

    A test that wrote and re-read inside one filesystem timestamp tick would be measuring the
    clock's resolution rather than the code, and it would pass or fail by machine.
    """
    stamp = time.time() + 2
    os.utime(path, (stamp, stamp))


# ── the gate: off by default, and provably turns something on ────────────────


class TestGate:
    def test_off_is_the_default(self, store, home):
        """No `vault_mode` in config at all resolves to `off`, not to a mirror."""
        (home / "config.json").write_text("{}", encoding="utf-8")
        assert kv.vault_mode_from_config() == "off"
        assert kv.vault_for(store) is None

    def test_gate_off_writes_nothing(self, store, home):
        """The pass runs, reports zero, and creates no directory. The default must be inert."""
        _note(store, "Alpha")
        assert kv.projection_pass(batch_size=10) == 0
        assert not kv.vault_path_from_config().exists()

    def test_gate_on_projects_files(self, store, home, monkeypatch):
        """Flipping the mode makes real files appear — the gate turns something ON.

        The pair matters: a gate whose only proof is that it exists is the inert-control
        failure, and a writer with no gate is the expensive one. This asserts both halves with
        the same store and the same items.
        """
        _note(store, "Alpha")
        _write_config(home, "mirror")
        # Patched on the PACKAGE, which is where `projection_pass` imports it from. Patching
        # `kv.get_knowledge_store` would need `raising=False` (the name is not bound there) and
        # would be a patch that does nothing — a green test measuring the real singleton.
        import personalclaw.knowledge as knowledge_pkg

        monkeypatch.setattr(knowledge_pkg, "get_knowledge_store", lambda: store)
        assert kv.projection_pass(batch_size=10) == 1
        pages = sorted(p.name for p in (kv.vault_path_from_config() / "items").glob("*.md"))
        assert len(pages) == 1

    def test_an_unreadable_config_is_off(self, store, home):
        """Garbage in config.json must not start writing the owner's files."""
        (home / "config.json").write_text("{ not json", encoding="utf-8")
        assert kv.vault_mode_from_config() == "off"


# ── the projection: identity + relations, in plain markdown ──────────────────


class TestProjection:
    def test_identity_and_relations_reach_the_file(self, store, home):
        a = _note(store, "Alpha note", content="alpha body", tags=["x", "y"])
        b = _note(store, "Beta note", content="beta body")
        assert store.add_item_relation(a, b, "depends_on")
        shelf = store.create_collection(name="Shelf")
        store.add_to_collection(shelf, a)

        v = _vault(store, home)
        v.sync_batch()

        block, body = split_page(_page(v, store, a).read_text(encoding="utf-8"))
        fm = parse_frontmatter(block)
        assert fm["id"] == a
        assert fm["type"] == kv.PAGE_TYPE
        assert fm["tags"] == ["x", "y"]
        assert fm["collections"] == ["Shelf"]
        assert fm["relations"] == [f"depends_on:{b}"]
        # The same relation as a link a human can follow, derived from the ROW — the target's
        # page exists, so the link resolves.
        assert f"[[{kv.page_basename(store.get_item(b))}]]" in body
        assert "alpha body" in body

    def test_a_relation_to_an_unprojected_item_is_not_linked(self, store, home):
        """A link to a page that does not exist would make the vault's own lint fire."""
        a = _note(store, "Alpha")
        b = _note(store, "Beta")
        store.add_item_relation(a, b, "depends_on")
        v = _vault(store, home)
        # Bound to one item, so exactly one of the pair has a page in this batch.
        v.sync_batch(max_items=1)
        pages = list((v.path / "items").glob("*.md"))
        assert len(pages) == 1
        text = pages[0].read_text(encoding="utf-8")
        # The relation is still IDENTITY in the front-matter (it is a fact about the item), but
        # there is no `## Relations` link section, because the other end has no page yet and a
        # link the vault cannot resolve would make its own broken-link check fire on correct
        # output. Ordering by `items.id` decides which of the two got projected, so this
        # asserts the property rather than a particular page.
        assert "## Relations" not in text
        assert "depends_on:" in text

    def test_a_relation_added_later_reaches_the_file(self, store, home):
        """A join-table change must re-arm the projection, on BOTH ends.

        The backlog is keyed on `items.updated_at`, which a relation insert does not touch. So
        without the explicit re-arm the `relations:` block would stay stale until something
        else happened to edit the item — a page quietly one edge short, for as long as nobody
        looked.
        """
        a = _note(store, "Alpha")
        b = _note(store, "Beta")
        v = _vault(store, home)
        v.sync_batch()
        assert v.sync_batch()["units"] == 0

        store.add_item_relation(a, b, "depends_on")
        v.sync_batch()

        for item_id, other in ((a, b), (b, a)):
            block, body = split_page(_page(v, store, item_id).read_text(encoding="utf-8"))
            fm = parse_frontmatter(block)
            assert fm["relations"] == [f"depends_on:{other}"], f"{item_id} lost the edge"
            assert f"[[{kv.page_basename(store.get_item(other))}]]" in body
        assert not [f for f in v.lint_flags() if f[0] == "knowledge_vault_broken_link"]

    def test_a_collection_change_reaches_the_file(self, store, home):
        a = _note(store, "Alpha")
        v = _vault(store, home)
        v.sync_batch()
        shelf = store.create_collection(name="Shelf")
        store.add_to_collection(shelf, a)
        v.sync_batch()
        block, _ = split_page(_page(v, store, a).read_text(encoding="utf-8"))
        assert parse_frontmatter(block)["collections"] == ["Shelf"]
        store.remove_from_collection(shelf, a)
        v.sync_batch()
        block, _ = split_page(_page(v, store, a).read_text(encoding="utf-8"))
        assert "collections" not in parse_frontmatter(block)

    def test_projection_is_bounded(self, store, home):
        """A large library cannot stall the host: one sub-batch writes at most its bound."""
        for i in range(7):
            _note(store, f"Item {i}")
        v = _vault(store, home, "mirror")
        assert v.sync_batch(max_items=2)["written"] == 2
        assert v.sync_batch(max_items=2)["written"] == 2
        assert store.count_items_needing_vault_projection() == 3

    def test_a_settled_vault_reports_zero(self, store, home):
        """0 is what stops KL-14's sub-batch loop. A pass that never returns 0 busy-loops."""
        _note(store, "Alpha")
        v = _vault(store, home)
        assert v.sync_batch()["units"] == 1
        assert v.sync_batch()["units"] == 0
        assert v.sync_batch()["units"] == 0

    def test_a_retitle_does_not_leave_the_old_page_behind(self, store, home):
        """The basename is title-derived, so a retitle renames the file — both, not two."""
        a = _note(store, "Old title")
        v = _vault(store, home)
        v.sync_batch()
        old = _page(v, store, a)
        assert old.exists()
        store.update_item(a, title="New title")
        v.sync_batch()
        assert not old.exists()
        assert _page(v, store, a).exists()

    def test_an_item_too_large_is_refused_not_truncated(self, store, home, monkeypatch):
        """Truncating and then reading back would write the truncation into the store.

        So the bound refuses the page. The item's content must be byte-identical afterwards —
        that is the whole difference between a bound and data loss.
        """
        monkeypatch.setattr(kv, "MAX_ITEM_BYTES", 32)
        big = "x" * 200
        a = _note(store, "Big", content=big)
        v = _vault(store, home)
        v.sync_batch()
        assert not (v.path / "items").exists() or not list((v.path / "items").glob("*.md"))
        assert store.get_item(a)["content"] == big
        assert any(c[0] == "knowledge_vault_conflict" for c in v.lint_flags())
        # And it leaves the backlog, so the host does not re-render it every sub-batch.
        assert store.count_items_needing_vault_projection() == 0


# ── ownership: an editor edit is READ BACK, not overwritten ──────────────────


class TestTwoWay:
    def test_owner_edit_is_read_back_not_overwritten(self, store, home):
        """The export-vs-ownership line. Falsified by making the projection win instead."""
        a = _note(store, "Alpha", content="original body")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        page.write_text(
            page.read_text(encoding="utf-8").replace("original body", "OWNER TEXT"),
            encoding="utf-8",
        )
        _touch_later(page)

        result = v.sync_batch()

        assert result["absorbed"] == 1
        assert result["conflicts"] == 0
        assert store.get_item(a)["content"].strip() == "OWNER TEXT"
        # And the re-projected page carries the owner's text, not the old one.
        assert "OWNER TEXT" in page.read_text(encoding="utf-8")
        assert "original body" not in page.read_text(encoding="utf-8")

    def test_read_back_keeps_the_owners_own_subheadings(self, store, home):
        """A knowledge item is a DOCUMENT. Its `## ` sections are the owner's, not ours.

        `extract_edited_value`'s heading stop is right for a one-sentence memory value and
        catastrophic here: left on, absorbing an edited article truncates it at its first
        subheading and writes the truncation back to the store.
        """
        a = _note(store, "Alpha", content="intro\n\n## Section\n\ndetail")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        page.write_text(
            page.read_text(encoding="utf-8").replace("intro", "NEW INTRO"), encoding="utf-8"
        )
        _touch_later(page)
        v.sync_batch()
        content = store.get_item(a)["content"]
        assert "NEW INTRO" in content
        assert "## Section" in content
        assert "detail" in content

    def test_relations_survive_a_read_back(self, store, home):
        """The atom's own validation bar: the change is read back WITH RELATIONS INTACT."""
        a = _note(store, "Alpha", content="alpha body", tags=["x"])
        b = _note(store, "Beta")
        store.add_item_relation(a, b, "depends_on")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        page.write_text(
            page.read_text(encoding="utf-8").replace("alpha body", "EDITED"), encoding="utf-8"
        )
        _touch_later(page)
        v.sync_batch()
        block, body = split_page(page.read_text(encoding="utf-8"))
        fm = parse_frontmatter(block)
        assert fm["relations"] == [f"depends_on:{b}"]
        assert fm["tags"] == ["x"]
        assert "EDITED" in body
        assert store.inbound_references(a)["relations"], "the typed edge must still be in the db"

    def test_mirror_mode_overwrites_and_counts_it(self, store, home):
        """`mirror` means one direction. It says so in the config help and in the README.

        Counted rather than silent: `overwritten` is in the summary, so a user reading a log
        can tell "my edit was discarded because the mode is mirror" from "nothing happened".
        """
        a = _note(store, "Alpha", content="original body")
        v = _vault(store, home, "mirror")
        v.sync_batch()
        page = _page(v, store, a)
        page.write_text(
            page.read_text(encoding="utf-8").replace("original body", "OWNER TEXT"),
            encoding="utf-8",
        )
        _touch_later(page)
        result = v.sync_batch()
        assert result["overwritten"] == 1
        assert result["absorbed"] == 0
        assert store.get_item(a)["content"] == "original body"
        assert "original body" in page.read_text(encoding="utf-8")
        # And it settles: mirror must not re-detect the same edit forever.
        assert v.sync_batch()["units"] == 0

    def test_a_page_with_no_heading_is_refused_not_guessed(self, store, home):
        a = _note(store, "Alpha", content="original body")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        block, body = split_page(page.read_text(encoding="utf-8"))
        page.write_text(
            page.read_text(encoding="utf-8").replace("# Alpha", "Alpha (heading removed)"),
            encoding="utf-8",
        )
        _touch_later(page)
        result = v.sync_batch()
        assert result["conflicts"] == 1
        assert store.get_item(a)["content"] == "original body"


# ── the self-retrigger loop ──────────────────────────────────────────────────


class TestNoSelfRetrigger:
    def test_projection_does_not_retrigger_itself(self, store, home):
        """Run the pass twice; the second run must write NOTHING.

        This is the clause's proof, not its restatement. A hash that covered the front-matter
        field it writes would make every page look hand-edited on the pass after it was
        written, and the vault would rewrite the whole library on every tick forever.
        """
        for i in range(3):
            _note(store, f"Item {i}", tags=["t"])
        v = _vault(store, home)
        first = v.sync_batch()
        assert first["written"] == 3
        pages = sorted((v.path / "items").glob("*.md"))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in pages}

        second = v.sync_batch()

        assert second["units"] == 0
        assert second["written"] == 0
        assert second["absorbed"] == 0
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in pages}
        assert after == before, "the second pass rewrote a page it had just written"

    def test_the_body_hash_excludes_the_field_it_writes(self, store, home):
        """The mechanism behind the clause, asserted directly on the bytes.

        ``source_hash`` sits in the front-matter and hashes the BODY only, so the act of
        writing it cannot change its own input. Recomputing the hash from the page's body must
        reproduce exactly what the front-matter claims.
        """
        a = _note(store, "Alpha", content="alpha body")
        v = _vault(store, home)
        v.sync_batch()
        block, body = split_page(_page(v, store, a).read_text(encoding="utf-8"))
        fm = parse_frontmatter(block)
        assert fm[HASH_KEY] == body_hash(body)
        assert HASH_KEY not in body, "the hash must not be inside the bytes it hashes"

    def test_the_ledger_hash_excludes_the_frontmatter_it_writes(self, store, home):
        """The clause, asserted on the mechanism the pass actually compares.

        Measured because the behavioural test above turned out NOT to be sensitive to this:
        the pass has three independent reasons never to loop — `seen_mtime` (a stat), this
        hash, and the content-equality check in `_apply_page_edit` — and the first one alone
        keeps a settled vault settled. So a hash widened to cover the front-matter (which
        carries ``source_hash``, the field the projection writes) produces a page that reads as
        hand-edited on every pass while the outer behaviour still looks fine. This asserts the
        scope directly, which is the only way to catch it.
        """
        a = _note(store, "Alpha", content="alpha body")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        text = page.read_text(encoding="utf-8")
        block, body = split_page(text)
        row = store.vault_projection(a)
        assert row["projected_body_hash"] == body_hash(body)
        assert row["projected_body_hash"] != body_hash(text), (
            "the ledger hash covers the front-matter, which carries the source_hash the "
            "projection writes — every page would read as hand-edited on the next pass"
        )
        assert HASH_KEY in block

    def test_touching_a_page_without_changing_it_writes_nothing(self, store, home):
        """The second layer, exercised on its own: a stat change is not an edit.

        A save with no change, a `touch`, an editor rewriting identical bytes — none of these
        is an owner edit, and treating one as such would absorb the page back through the write
        path and bump ``updated_at``, which re-arms the projection: the loop, arriving through
        a file-manager rather than through the hash.
        """
        a = _note(store, "Alpha", content="alpha body")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        before = page.read_bytes()
        stamp = store.get_item(a)["updated_at"]
        _touch_later(page)

        result = v.sync_batch()

        assert result["units"] == 0
        assert result["conflicts"] == 0
        assert page.read_bytes() == before
        assert store.get_item(a)["updated_at"] == stamp
        assert store.vault_projection(a)["conflict"] == ""

    def test_a_conflict_does_not_busy_loop(self, store, home):
        """An unresolved page must contribute 0 next pass, or one conflict burns every tick."""
        a = _note(store, "Alpha", content="original body")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        page.write_text(
            page.read_text(encoding="utf-8").replace("# Alpha", "no heading"), encoding="utf-8"
        )
        _touch_later(page)
        assert v.sync_batch()["conflicts"] == 1
        assert v.sync_batch()["units"] == 0
        assert v.sync_batch()["units"] == 0


# ── the two-sided conflict ───────────────────────────────────────────────────


class TestTwoSidedConflict:
    def _both_sides_move(self, store, home):
        a = _note(store, "Alpha", content="original body")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        owner_bytes = page.read_text(encoding="utf-8").replace("original body", "OWNER TEXT")
        page.write_text(owner_bytes, encoding="utf-8")
        _touch_later(page)
        # …and the app changes the same item.
        store.update_item(a, content="APP TEXT")
        return a, v, page, owner_bytes

    def test_two_sided_change_surfaces_and_writes_nothing(self, store, home):
        """Neither side wins, and the disagreement is visible in three places.

        Falsified by resolving toward the database (apply the app's text over the file) or
        toward the file (apply the owner's edit anyway): either makes this red.
        """
        a, v, page, owner_bytes = self._both_sides_move(store, home)

        result = v.sync_batch()

        assert result["conflicts"] == 1
        assert result["absorbed"] == 0
        # 1. The store is untouched — the owner's edit did NOT silently win.
        assert store.get_item(a)["content"] == "APP TEXT"
        # 2. The owner's body is byte-for-byte what they wrote — the app did NOT silently win.
        _, body_now = split_page(page.read_text(encoding="utf-8"))
        _, body_owner = split_page(owner_bytes)
        assert body_now.rstrip() == body_owner.rstrip()
        # 3. It surfaces: in the file the owner was editing…
        fm = parse_frontmatter(split_page(page.read_text(encoding="utf-8"))[0])
        assert CONFLICT_KEY in fm
        assert "both" in str(fm[CONFLICT_KEY]).lower()
        # …in the verification flags…
        assert [f for f in v.lint_flags() if f[0] == "knowledge_vault_conflict"]
        # …and in the ledger the Doctor probe reads.
        assert [r for r in store.vault_projection_flags() if r["conflict"]]
        assert v.status()["conflicts"] == 1

    def test_the_conflict_stamp_does_not_make_the_page_look_clean(self, store, home):
        """Stamping front-matter must not recompute ``source_hash``.

        If it did, the page would read as untouched on the next pass and the projection would
        overwrite the owner's text — the conflict flag erasing the very disagreement it
        records.
        """
        a, v, page, _ = self._both_sides_move(store, home)
        v.sync_batch()
        block, body = split_page(page.read_text(encoding="utf-8"))
        fm = parse_frontmatter(block)
        assert fm[HASH_KEY] != body_hash(body)

    def test_a_conflicted_page_is_never_overwritten_by_a_later_pass(self, store, home):
        """The projection backlog must exclude a conflicted row, or the next tick undoes it."""
        a, v, page, owner_bytes = self._both_sides_move(store, home)
        v.sync_batch()
        for _ in range(3):
            v.sync_batch()
        assert "OWNER TEXT" in page.read_text(encoding="utf-8")
        assert "APP TEXT" not in page.read_text(encoding="utf-8")


# ── deletion, in both directions ─────────────────────────────────────────────


class TestDeletion:
    def test_deleted_page_is_not_recreated(self, store, home):
        """A page the owner removed stays removed — and the item is NOT deleted with it."""
        a = _note(store, "Alpha")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        page.unlink()

        result = v.sync_batch()

        assert result["tombstoned"] == 1
        assert not page.exists()
        for _ in range(3):
            v.sync_batch()
            assert not page.exists(), "the projection re-created a page the owner deleted"
        # And it survives a LATER change in the app, which is the case the tombstone actually
        # exists for. Without it, "not re-created" holds only by accident: the projection
        # backlog is keyed on `updated_at`, so a deleted file simply is not noticed — until any
        # edit in the app puts the item back in the backlog and the page reappears.
        store.update_item(a, title="Alpha renamed")
        v.sync_batch()
        v.sync_batch()
        assert not list(
            (v.path / "items").glob("*.md")
        ), "an app-side edit resurrected a page the owner deleted"
        # Deleting a file is an ambiguous signal (a moved directory, a half-restored backup),
        # so it must never delete the owner's knowledge.
        assert store.get_item(a) is not None
        assert [f for f in v.lint_flags() if f[0] == "knowledge_vault_page_deleted"]
        assert v.status()["owner_deleted"] == 1

    def test_deleted_item_leaves_no_file(self, store, home):
        """A removed item leaves no orphan file, and no ledger row either."""
        a = _note(store, "Alpha")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        assert page.exists()

        store.delete_item(a)
        result = v.sync_batch()

        assert result["deleted"] == 1
        assert not page.exists()
        assert store.vault_projection(a) is None
        assert v.sync_batch()["units"] == 0

    def test_a_deleted_item_leaves_no_broken_link_in_its_neighbour(self, store, home):
        """The other half of "leaves no orphan file": no orphan LINK either.

        Found by driving the real maintenance host, not by reading the code: deleting an item
        takes its typed edges with it and leaves every neighbour's `items.updated_at`
        untouched, so a projection keyed only on `updated_at` keeps serving a page that links
        a file which no longer exists. The broken-link check is what reported it.
        """
        a = _note(store, "Alpha")
        b = _note(store, "Beta")
        store.add_item_relation(a, b, "depends_on")
        v = _vault(store, home)
        v.sync_batch()
        assert f"[[{kv.page_basename(store.get_item(b))}]]" in _page(v, store, a).read_text(
            encoding="utf-8"
        )

        store.delete_item(b)
        v.sync_batch()

        text = _page(v, store, a).read_text(encoding="utf-8")
        assert "## Relations" not in text
        assert "depends_on" not in text
        assert not [f for f in v.lint_flags() if f[0] == "knowledge_vault_broken_link"]
        assert v.sync_batch()["units"] == 0

    def test_an_emptied_tag_hub_is_removed(self, store, home):
        """Hubs are pruned, not only written.

        Writing the hubs that have members and never removing the ones that do not is the
        one-sided-inventory shape: the hub for a tag whose last item is gone keeps linking a
        file that is gone. The broken-link check reported it while driving the real host.
        """
        a = _note(store, "Alpha", tags=["perf"])
        v = _vault(store, home)
        v.sync_batch()
        hub = v.path / "tags" / "tag-perf.md"
        assert hub.exists()

        store.delete_item(a)
        v.sync_batch()

        assert not hub.exists()
        assert not [f for f in v.lint_flags() if f[0] == "knowledge_vault_broken_link"]

    def test_the_ledger_row_outlives_the_item(self, store, home):
        """The reason the table carries no foreign key.

        With `ON DELETE CASCADE` the row would vanish with the item and nothing would remember
        that a file existed — "a removed item leaves no orphan file" would be unenforceable.
        """
        a = _note(store, "Alpha")
        v = _vault(store, home)
        v.sync_batch()
        store.delete_item(a)
        orphans = store.orphan_vault_projections()
        assert [r["item_id"] for r in orphans] == [a]
        assert orphans[0]["relpath"].endswith(".md")


# ── the host: KL-14's cadence, not a new one ─────────────────────────────────


class TestHostWiring:
    def test_registered_on_kl14s_host_as_resumable(self):
        """It runs on KL-14's host, and `batched=True` because it drains a real backlog."""
        maintenance.clear_passes()
        names = maintenance_passes.register_all()
        assert maintenance_passes.PASS_VAULT_PROJECTION in names
        assert maintenance_passes.PASS_VAULT_PROJECTION in maintenance.registered_passes()
        maintenance.clear_passes()

    def test_the_pass_is_a_no_op_when_the_gate_is_off(self, store, home):
        """The registered pass must cost nothing on the default install."""
        _note(store, "Alpha")
        assert maintenance_passes._vault_projection_pass(batch_size=5) == 0
        assert not kv.vault_path_from_config().exists()


# ── verification + config round trip ────────────────────────────────────────


class TestVerificationAndConfig:
    def test_an_orphan_page_is_reported_not_deleted(self, store, home):
        """A file the owner created is theirs. The lint names it; nothing removes it."""
        v = _vault(store, home)
        _note(store, "Alpha")
        v.sync_batch()
        mine = v.path / "items" / "my-own-notes.md"
        mine.write_text("# mine\n", encoding="utf-8")
        flags = v.lint_flags()
        assert [f for f in flags if f[0] == "knowledge_vault_orphan_page"]
        v.sync_batch()
        assert mine.exists()

    def test_config_round_trip(self, home):
        """dataclass → load() → to_dict(), plus the PATCH allowlist entry that writes it."""
        from personalclaw.config.loader import AppConfig
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        _write_config(home, "two_way")
        cfg = AppConfig.load()
        assert cfg.knowledge.vault_mode == "two_way"
        assert cfg.knowledge.vault_path == "knowledge-vault"
        assert cfg.to_dict()["knowledge"]["vault_mode"] == "two_way"
        assert cfg.to_dict()["knowledge"]["vault_path"] == "knowledge-vault"
        assert "knowledge.vault_mode" in _EDITABLE_CONFIG
        assert "knowledge.vault_path" in _EDITABLE_CONFIG
        assert _EDITABLE_CONFIG["knowledge.vault_mode"]["values"] == ["off", "mirror", "two_way"]

    def test_an_invalid_mode_in_the_file_resolves_to_off(self, home):
        from personalclaw.config.loader import AppConfig

        _write_config(home, "yolo")
        assert AppConfig.load().knowledge.vault_mode == "off"

    def test_the_doctor_probe_is_registered(self):
        from personalclaw.resilience.doctor import all_probes

        ids = {p.id for p in all_probes()}
        assert "knowledge.vault" in ids

    def test_the_doctor_probe_reports_a_waiting_page(self, store, home):
        """The probe is the reachable reader of the ledger — not a list nobody consults."""
        import asyncio

        from personalclaw.resilience.doctor import DoctorContext, all_probes

        a = _note(store, "Alpha", content="original body")
        v = _vault(store, home)
        v.sync_batch()
        page = _page(v, store, a)
        page.write_text(
            page.read_text(encoding="utf-8").replace("original body", "OWNER"), encoding="utf-8"
        )
        _touch_later(page)
        store.update_item(a, content="APP")
        v.sync_batch()
        store.close()

        probe = next(p for p in all_probes() if p.id == "knowledge.vault")
        result = asyncio.run(probe.run(DoctorContext(home=home)))
        assert result.ok, "a conflict is the projection working — never a failed capability"
        assert result.evidence["conflicts"] == 1
        assert "waiting on you" in result.detail


# ── isolation ────────────────────────────────────────────────────────────────


def test_the_real_home_is_never_touched(store, home):
    """Assert the isolation rather than trusting it.

    The subject under test deletes and rewrites files under `config_dir()`. If the env
    override failed to bind — the classic import-time-frozen-path failure — this suite would
    be writing a projection into the owner's real vault.
    """
    real = Path.home() / ".personalclaw"
    before = sorted(p.name for p in real.iterdir()) if real.is_dir() else None

    _note(store, "Alpha")
    v = _vault(store, home)
    v.sync_batch()

    assert str(kv.vault_path_from_config()).startswith(str(home))
    assert v.path.is_relative_to(home)
    after = sorted(p.name for p in real.iterdir()) if real.is_dir() else None
    assert after == before
    assert not (real / "knowledge-vault").exists()
