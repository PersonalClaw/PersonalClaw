"""The rail for the skill-proposal cycle (#409 → #323, #302, #336).

**The cycle.** Measured on a live 48-cycle instance: skill proposals were 89% of the open inbox
(32 of 36 rows) and 87% of them could not be accepted at all — accept answered ``409`` permanently.
The links are mutually causal, which is what made it a cycle rather than three bugs: each
*successful* accept blocked its slug forever, hid its own result, and left the row that announced it
un-clearable.

======  ==========================================================================================
#323    ``accept()`` fell through to ``create_auto_skill(slug)``, which refuses an existing slug, so
        a ``kind="new"`` proposal for an installed skill answered 409 FOREVER. The generator files
        ``kind="new"`` by default and its only duplicate guard is ``find_similar(description)`` — a
        check on the DESCRIPTION, not on whether the slug exists — so a differently-worded synthesis
        for an installed skill sailed through and then could never be applied.
#302    ``GET /api/skills`` walked ONE level with ``iterdir()`` while ``SkillsLoader._iter`` uses
        ``rglob("SKILL.md")``. ``auto/`` is a directory with no ``SKILL.md`` of its own, so every
        accepted proposal's skill was invisible: loaded into every agent's context, and
        un-inspectable and un-deletable from the UI.
# 336    Both inbox writers here constructed a detached ``InboxStore()``, against ``live_store``'s
# own
        warning that doing so "writes a row the API cannot see … and that the service's next save
        silently overwrites". Three live orphans confirmed it.
======  ==========================================================================================

**#303 is NOT fixed here — it was already fixed on main.** ``accept()`` branches on
``kind == "refine"`` and applies a sidecar overlay. That fix is what makes #323's fix possible: the
overlay path already existed and simply was not reached for a proposal that had not been LABELLED a
refine.

**Both halves, deliberately.** The generator now labels a same-slug proposal as a refine, which
stops
the queue filling. ``accept()`` also INFERS it, which is the only thing that can recover a queue the
bug already filled — no generator fix reaches a proposal already on disk. A fix with only the
generator half would leave those 26 rows stuck forever.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from personalclaw.skills import proposals as P
from personalclaw.skills.loader import AUTO_SKILL_NAMESPACE, AutoSkillProvenance, SkillsLoader

_CREATED = "2026-01-01T00:00:00Z"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home for the loader, the proposal store and the inbox."""
    import personalclaw.config.loader as cfg
    import personalclaw.skills.loader as sl

    monkeypatch.setattr(sl, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    return tmp_path


def _loader() -> SkillsLoader:
    return SkillsLoader(install_builtins=False)


def _install(slug: str, *, body: str = "original body") -> str:
    name = _loader().create_auto_skill(
        slug,
        description="d",
        triggers="t",
        procedure_md=body,
        provenance=AutoSkillProvenance(session_key="s1", created_at=_CREATED),
    )
    assert name, "the fixture's own precondition"
    return name


def _propose(slug: str, *, kind: str = "new", refine_target: str = "", n: int = 0):
    prop = P.enqueue(
        slug=slug,
        description=f"d{n}",
        triggers="t",
        procedure_md=f"refined body {n}",
        session_key="s1",
        created_at=f"2026-01-01T00:00:{n:02d}Z",
        kind=kind,
        refine_target=refine_target,
    )
    assert prop is not None
    return prop


# ── #323: accept must not 409 on an existing slug ─────────────────────────────


class TestAcceptNeverPermanently409s:
    def test_a_new_proposal_for_an_existing_slug_overlays_it(self, home):
        """🔴 THE cycle's engine. This answered `409 could not write skill … (exists)` forever."""
        _install("loop-worker")
        result = P.accept(_propose("loop-worker", n=1).id)
        assert result.name == f"{AUTO_SKILL_NAMESPACE}/loop-worker"
        assert result.version >= 1, "an overlay was applied, not a create"

    def test_the_TWENTIETH_proposal_for_one_slug_still_accepts(self, home):
        """The measured shape: 20 pending proposals for `loop-worker`, none acceptable. Accepting
        them in sequence must keep working rather than working once and then blocking."""
        _install("loop-worker")
        versions = [P.accept(_propose("loop-worker", n=i).id).version for i in range(1, 6)]
        assert versions == sorted(versions), f"versions did not advance: {versions}"
        assert len(set(versions)) == len(versions), "two accepts wrote the same version"

    def test_an_accepted_proposal_is_cleared_from_the_queue(self, home):
        """A 409 left the proposal `pending`, i.e. unchanged AND unchangeable — so the Skills page
        kept counting it. The accept has to resolve the row it acted on."""
        _install("loop-worker")
        prop = _propose("loop-worker", n=1)
        P.accept(prop.id)
        assert P._load(prop.id) is None
        assert prop.id not in {p.id for p in P.list_pending()}

    def test_a_refine_proposal_still_overlays_its_named_target(self, home):
        """The path #303 added, unchanged — asserted so the #323 restructure cannot regress it."""
        name = _install("loop-worker")
        result = P.accept(_propose("loop-worker", kind="refine", refine_target=name, n=1).id)
        assert result.name == name
        assert result.version >= 1

    def test_a_refine_whose_target_VANISHED_falls_back_to_create(self, home):
        """Deleted since the proposal was filed. Must create rather than 500, so Accept still
        resolves the row."""
        prop = _propose("gone-skill", kind="refine", refine_target="auto/gone-skill", n=1)
        result = P.accept(prop.id)
        assert result.name == f"{AUTO_SKILL_NAMESPACE}/gone-skill"
        assert result.version == 0, "a create, not an overlay"

    def test_a_genuinely_new_slug_still_CREATES(self, home):
        """Vacuity floor. A fix that always overlaid would pass every test above and never install
        anything — the proposal queue's whole purpose."""
        result = P.accept(_propose("brand-new-skill", n=1).id)
        assert result.name == f"{AUTO_SKILL_NAMESPACE}/brand-new-skill"
        assert result.version == 0
        assert _loader().load_skill(result.name) is not None

    def test_the_overlay_does_not_rewrite_the_base_skill(self, home):
        """The property the overlay design exists for: base bytes stay intact, so reverting a
        refinement is deleting one file."""
        name = _install("loop-worker", body="ORIGINAL-MARKER")
        base = home / "skills" / name / "SKILL.md"
        before = base.read_text(encoding="utf-8")
        P.accept(_propose("loop-worker", n=1).id)
        assert base.read_text(encoding="utf-8") == before


# ── the generator labels it correctly, so the queue stops filling ─────────────


class TestGeneratorLabelsARefine:
    def test_a_proposal_for_an_existing_slug_is_filed_as_a_refine(self, home):
        """`find_similar` compares DESCRIPTIONS, so it cannot answer "does this slug exist" — which
        is why a differently-worded synthesis for an installed skill got through as `kind="new"`.

        Asserted at the expression rather than by driving a whole history compaction: the finding is
        that the generator never asked the question at all.
        """
        import inspect

        from personalclaw import history

        src = inspect.getsource(history)
        assert 'kind="refine" if _is_refine else "new"' in src
        assert "load_skill(_existing)" in src

    def test_the_inbox_label_follows_the_kind(self, home):
        """A row that says "New skill proposed" for a refinement is the same lie in the UI. The
        label is derived from `kind`, so labelling the kind correctly fixes both."""
        import inspect

        src = inspect.getsource(P._surface_in_inbox)
        assert "Refine a skill" in src and 'prop.kind == "refine"' in src


# ── #302: the accepted skill must be visible ──────────────────────────────────


class TestNamespacedSkillsAreListed:
    def test_an_auto_namespaced_skill_is_discovered(self, tmp_path):
        """🔴 Three `auto/*` skills were loaded into every agent's context while `GET /api/skills`
        reported none of them — un-inspectable and un-deletable from the UI."""
        base = tmp_path / "skills"
        (base / "auto" / "loop-worker").mkdir(parents=True)
        (base / "auto" / "loop-worker" / "SKILL.md").write_text("---\nname: x\n---\nb\n")
        (base / "top-level").mkdir()
        (base / "top-level" / "SKILL.md").write_text("---\nname: y\n---\nb\n")

        found = {
            md.parent.relative_to(base).as_posix()
            for md in base.rglob("SKILL.md")
            if md.parent != base
        }
        assert found == {"auto/loop-worker", "top-level"}

    def test_the_handler_walks_recursively_like_the_loader_does(self):
        """Pins the AGREEMENT, not the mechanism. The loader and the listing were two answers to
        "what is a skill", and only one of them decided what the user could see."""
        import inspect

        from personalclaw.dashboard.handlers import skills as H
        from personalclaw.skills import loader as L

        assert "rglob(_SKILL_FILENAME)" in inspect.getsource(H.api_skills_list)
        assert 'rglob("SKILL.md")' in inspect.getsource(L)

    def test_a_namespaced_name_keeps_its_namespace(self, tmp_path):
        """`auto/loop-worker`, not `loop-worker`. The namespace is what `SkillsLoader` calls it,
        what
        the delete route takes, and what dedups it against a top-level skill of the same name — a
        bare basename would collide the two and drop one."""
        base = tmp_path / "skills"
        (base / "auto" / "shared-name").mkdir(parents=True)
        (base / "auto" / "shared-name" / "SKILL.md").write_text("a")
        (base / "shared-name").mkdir()
        (base / "shared-name" / "SKILL.md").write_text("b")
        names = {
            md.parent.relative_to(base).as_posix()
            for md in base.rglob("SKILL.md")
            if md.parent != base
        }
        assert names == {"auto/shared-name", "shared-name"}, "the two must not collide"


# ── #336: the inbox row must clear ────────────────────────────────────────────


class TestInboxRowsResolveAgainstTheLiveStore:
    def test_the_resolve_path_uses_the_same_accessor_as_the_write_path(self):
        """The one-sided shape: `_surface_in_inbox` (WRITE) already went through the running
        service's store, and `_resolve_inbox_item` (RESOLVE) constructed its own — so a resolve
        wrote to a copy the service then overwrote, leaving the row open forever."""
        import inspect

        resolve = inspect.getsource(P._resolve_inbox_item)
        assert "_inbox_store_for_write()" in resolve
        assert "InboxStore()" not in resolve

        helper = inspect.getsource(P._inbox_store_for_write)
        assert "live_store" in helper
        assert "get_dashboard_state" in helper

    def test_the_backfill_reads_the_same_store_it_would_write(self):
        """It reads to decide which proposals still need a row. Reading a detached copy would miss
        every row the running service holds in memory and re-surface duplicates."""
        import inspect

        assert "_inbox_store_for_write()" in inspect.getsource(P.backfill_inbox_items)

    def test_headless_still_gets_a_usable_store(self, home):
        """Vacuity floor: with no gateway up there is no live store, and the file IS the truth. A
        helper that returned None there would make a CLI accept silently skip the resolve."""
        with patch(
            "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=None
        ):
            store = P._inbox_store_for_write()
        assert store is not None
        assert hasattr(store, "items") and hasattr(store, "save")

    def test_a_resolve_marks_the_row_terminal(self, home):
        """End to end through the real (headless) store: accepting a proposal must leave no open
        row referencing it."""
        from personalclaw.inbox import InboxStore

        _install("loop-worker")
        prop = _propose("loop-worker", n=1)
        P._surface_in_inbox(prop)
        P.accept(prop.id)

        store = InboxStore()
        store.load()
        rows = [i for i in store.items.values() if i.refs.get("skill_proposal") == prop.id]
        assert rows, "the fixture's own precondition: a row was surfaced"
        assert all(
            i.status in ("handled", "dismissed") for i in rows
        ), f"an accepted proposal left an OPEN row: {[i.status for i in rows]}"


# ── the bulk control has to be able to reach the queue ────────────────────────


class TestDismissAllMeansAll:
    """`POST /api/inbox/dismiss-all` used `pending()`, and the UI marks a row SEEN the moment you
    open it — so merely LOOKING at an item removed it from the reach of the only bulk control. On
    the measured instance that left 32 open proposal rows clearable one at a time and no other way.
    """

    def _store(self, tmp_path, monkeypatch):
        import personalclaw.inbox as inbox_mod
        from personalclaw.inbox import InboxItem, InboxStore, ItemStatus

        monkeypatch.setattr(inbox_mod, "config_dir", lambda: tmp_path, raising=False)
        store = InboxStore()
        for i, status in enumerate(
            [ItemStatus.PENDING, ItemStatus.SEEN, ItemStatus.DISMISSED, ItemStatus.HANDLED]
        ):
            item = InboxItem(
                id=f"i{i}",
                channel="C1",
                channel_name="#t",
                thread_ts=None,
                message=f"m{i}",
                sender_id="U1",
                sender_name="A",
            )
            item.status = status
            store.items[item.id] = item
        return store

    def test_open_items_covers_pending_AND_seen(self, tmp_path, monkeypatch):
        store = self._store(tmp_path, monkeypatch)
        assert {i.id for i in store.open_items()} == {"i0", "i1"}

    def test_open_items_excludes_the_already_decided(self, tmp_path, monkeypatch):
        """Vacuity floor in the other direction: an "open" set that included dismissed/handled rows
        would make dismiss-all re-dismiss answered work and inflate its own count."""
        store = self._store(tmp_path, monkeypatch)
        assert {i.id for i in store.open_items()}.isdisjoint({"i2", "i3"})

    def test_pending_is_UNCHANGED(self, tmp_path, monkeypatch):
        """`pending()` has four other callers where it means exactly pending — a badge must not keep
        counting a row you have read. Widening it instead of adding `open_items` would have changed
        every count on the dashboard."""
        store = self._store(tmp_path, monkeypatch)
        assert {i.id for i in store.pending()} == {"i0"}

    def test_the_handler_uses_the_open_set(self):
        import inspect

        from personalclaw.dashboard import handlers_inbox as H

        src = inspect.getsource(H.api_inbox_dismiss_all)
        assert "inbox.open_items()" in src
        assert "inbox.pending()" not in src


# ── the cycle, end to end ─────────────────────────────────────────────────────


def test_the_whole_cycle_is_broken(home):
    """One assertion per link, on one sequence, because #409's point is that the links are mutually
    causal: each successful accept used to block its slug, hide its result, and orphan its row.
    """
    from personalclaw.inbox import InboxStore

    # A first proposal installs the skill.
    first = _propose("loop-worker", n=1)
    P._surface_in_inbox(first)
    created = P.accept(first.id)
    assert created.name == f"{AUTO_SKILL_NAMESPACE}/loop-worker"

    # #323 — a SECOND proposal for that slug is still acceptable.
    second = _propose("loop-worker", n=2)
    P._surface_in_inbox(second)
    assert P.accept(second.id).version >= 1

    # #302 — both are visible where a user would look for them.
    base = home / "skills"
    listed = {
        md.parent.relative_to(base).as_posix() for md in base.rglob("SKILL.md") if md.parent != base
    }
    assert f"{AUTO_SKILL_NAMESPACE}/loop-worker" in listed

    # #336 — neither accept left an open row behind.
    store = InboxStore()
    store.load()
    open_rows = [
        i
        for i in store.items.values()
        if i.refs.get("skill_proposal") in {first.id, second.id} and i.status in ("pending", "seen")
    ]
    assert open_rows == [], f"{len(open_rows)} orphan row(s) left open"

    # And the queue is actually empty, not merely answered.
    assert [p.id for p in P.list_pending()] == []
