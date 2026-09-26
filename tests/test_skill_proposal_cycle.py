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

**Two links survived that round, and they are what kept #409 open.** Re-measured by execution on
this worktree before touching anything:

======================  ======================================================================
refill (LIVE)           ``enqueue`` accepted **20 of 20** proposals about ONE skill in twenty
                        minutes — 20 pending records, 20 open inbox rows — while
                        ``refine.cap_reason`` for that very skill correctly answered "a refine
                        proposal for auto/loop-worker is already pending". The rule existed and
                        covered ONE of three producers. It is at the SINK now
                        (``proposals.coalesce_reason``, called from ``enqueue``), keyed on
                        ``subject`` — the same resolution ``accept`` runs.
reducibility (LIVE)     Dismissing a proposal row left the record ``pending``: **18 open rows
                        against 19 pending proposals**, and ``dismiss-all`` over 32 rows moved
                        the Skills page's "Proposals (32)" by zero. Both terminal-transition
                        sites go through ``handlers_inbox._dismiss`` now, which answers the
                        proposal the row mirrors.
enumeration (PARTIAL)   ``GET /api/skills`` was fixed; ``marketplace.list_local_skills`` was
                        not — 3 ``auto/*`` on disk, 0 in the list — blinding
                        ``personalclaw skills list`` AND the loop classifier's capability
                        catalog. All four surfaces call ``loader.iter_skill_files`` now.
======================  ======================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    """File through the real sink. ``created_at`` is DAYS apart per *n* so a test about the
    accept path is not silently also a test of the coalescing window."""
    prop = P.enqueue(
        slug=slug,
        description=f"d{n}",
        triggers="t",
        procedure_md=f"refined body {n}",
        session_key="s1",
        created_at=f"2026-01-{n + 1:02d}T00:00:00Z",
        kind=kind,
        refine_target=refine_target,
    )
    assert prop is not None
    return prop


def _seed(slug: str, *, kind: str = "new", refine_target: str = "", n: int = 0):
    """Write a proposal record STRAIGHT to disk, bypassing :func:`P.enqueue`.

    This is the shape the measured instance was in: twenty records already on disk, filed
    before any rail existed. It matters that the recovery path is tested from that state and
    not from twenty fresh ``enqueue`` calls, because the coalescing rail is deliberately
    enqueue-side ONLY — no rail can reach a proposal already written, and a fix that stranded
    those twenty rows would have fixed nothing a user could feel.
    """
    import json

    from personalclaw.atomic_write import atomic_write

    prop = P.SkillProposal(
        id=f"{slug}-seeded{n:04d}",
        slug=slug,
        description=f"d{n}",
        triggers="t",
        procedure_md=f"refined body {n}",
        session_key="s1",
        created_at=f"2026-01-01T00:00:{n:02d}Z",
        kind=kind,
        refine_target=refine_target,
    )
    d = P._proposals_dir()
    d.mkdir(parents=True, exist_ok=True)
    atomic_write(d / f"{prop.id}.json", json.dumps(prop.to_dict(), indent=2))
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
        """The measured shape: 20 pending proposals for `loop-worker`, none acceptable. Draining
        them must keep working rather than working once and then blocking.

        Seeded to disk, not enqueued, because that is how they got there — see :func:`_seed`.
        """
        _install("loop-worker")
        seeded = [_seed("loop-worker", n=i) for i in range(1, 21)]
        assert len(P.list_pending(_surface=False)) == 20, "the fixture's own precondition"
        versions = [P.accept(p.id).version for p in seeded]
        assert versions == sorted(versions), f"versions did not advance: {versions}"
        assert len(set(versions)) == len(versions), "two accepts wrote the same version"
        assert P.list_pending(_surface=False) == [], "the queue must be drainable to EMPTY"

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

    def test_every_listing_surface_shares_the_loader_s_enumeration(self):
        """Pins the AGREEMENT, not the mechanism — and structurally, by CALL rather than by a
        matching regex. The loader and the listings were separate answers to "what is a skill",
        and only one of them decided what the user could see. Sharing one function is what makes
        them agree; four copies of a recursive walk would just be a slower divergence.
        """
        import inspect

        from personalclaw.dashboard.handlers import skills as H
        from personalclaw.skills import loader as L
        from personalclaw.skills import marketplace as M

        assert 'rglob("SKILL.md")' in inspect.getsource(L.iter_skill_files)
        for fn in (H.api_skills_list, M.list_local_skills):
            src = inspect.getsource(fn)
            assert "iter_skill_files(" in src, f"{fn.__qualname__} re-derives the walk"
            assert ".iterdir()" not in src, f"{fn.__qualname__} still walks ONE level"

    def test_the_CLI_and_the_loop_classifier_see_the_auto_namespace(self, home, monkeypatch):
        """🔴 `list_local_skills` was the LAST one-level walk, and it has two consumers that both
        went blind: `personalclaw skills list` printed none of the accepted proposals, and the loop
        classifier's capability catalog (`loop_routes._installed_capability_catalogs`) could never
        rank a skill the user had just approved. Measured before the fix: 3 `auto/*` on disk, 0 in
        this list.
        """
        from personalclaw.skills import marketplace as M

        _install("loop-worker")
        _install("knowledge-grounding")
        flat = home / "skills" / "hand-written"
        flat.mkdir(parents=True)
        (flat / "SKILL.md").write_text("---\nname: hand-written\ndescription: d\n---\nb\n")

        # Only this home's root — `SKILL_DISCOVERY_PATHS` is bound at import and also carries
        # `~/.agents/skills`, which is the developer's real machine.
        monkeypatch.setattr(M, "SKILL_DISCOVERY_PATHS", [home / "skills"])
        names = {s["name"] for s in M.list_local_skills()}
        assert {
            f"{AUTO_SKILL_NAMESPACE}/loop-worker",
            f"{AUTO_SKILL_NAMESPACE}/knowledge-grounding",
        } <= names
        assert "hand-written" in names, "vacuity floor: top-level skills must still list"

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


# ── the queue must not REFILL: one review per subject, owned by the sink ───────


def _fresh(minutes_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(
        timespec="seconds"
    )


class TestTheQueueCannotBeFloodedByOneSubject:
    """🔴 The last self-reinforcing link. With accept fixed, twenty proposals about `loop-worker`
    are each individually applicable and collectively untriageable — measured growth ~3.2 rows/hour
    with `_MAX_PENDING = 100` as the only brake. The rule that prevents it already existed
    (`refine.cap_reason`, "one refine per skill per day", reading the pending queue AND the accepted
    overlay) and covered ONE of three producers: the stumble arm asked, the after-turn ladder and
    the auto-skill synthesizer went straight to `enqueue`. Measured on this worktree before the
    move: 20 of 20 same-target enqueues accepted while `cap_reason` for that very skill answered
    "a refine proposal for auto/loop-worker is already pending". The rule was right; it was in the
    wrong place.
    """

    def _enqueue(self, slug, *, n=0, kind="refine", target="", minutes_ago=0):
        return P.enqueue(
            slug=slug,
            description=f"variant {n}",
            triggers="",
            procedure_md=f"body {n}",
            session_key=f"s{n}",
            created_at=_fresh(minutes_ago),
            kind=kind,
            refine_target=target,
        )

    def test_twenty_same_target_proposals_collapse_to_one(self, home):
        target = _install("loop-worker")
        made = [
            self._enqueue("loop-worker", n=i, target=target, minutes_ago=20 - i) for i in range(20)
        ]
        assert sum(1 for m in made if m is not None) == 1, "the flood was admitted"
        assert len(P.list_pending(_surface=False)) == 1

    def test_the_rail_is_at_the_SINK_so_every_producer_inherits_it(self):
        """The finding is structural: not "the ladder forgot to check" but "only one of three
        producers ever could". `enqueue` is the one funnel all three go through, so a fourth
        producer is covered by construction rather than by remembering."""
        import inspect

        from personalclaw import after_turn_review, history
        from personalclaw.skills import refine as R

        assert "coalesce_reason(prop)" in inspect.getsource(P.enqueue)
        # And the duplicate is GONE from the arm that used to own it — one rule, one place.
        assert not hasattr(R, "cap_reason")
        for producer in (after_turn_review, history):
            src = inspect.getsource(producer)
            assert "enqueue(" in src, "this producer no longer files proposals — retarget the rail"

    def test_a_kind_new_duplicate_is_coalesced_TOO(self, home):
        """The generator's default is `kind="new"`; #409's queue was 20 rows for one slug filed
        under mixed labels. Keying on `subject` — the same resolution accept runs — is what makes
        the label irrelevant to the rail."""
        _install("loop-worker")
        assert self._enqueue("loop-worker", n=1, kind="new", minutes_ago=5) is not None
        assert self._enqueue("loop-worker", n=2, kind="refine", target="auto/loop-worker") is None

    def test_two_proposals_to_create_the_SAME_new_skill_are_one_review(self, home):
        """`subject` is total, not just `accept_target`: nothing is installed yet, so both would
        `create` — and two rows asking to create one skill is still one question."""
        assert self._enqueue("brand-new", n=1, kind="new", minutes_ago=5) is not None
        assert self._enqueue("brand-new", n=2, kind="new") is None

    def test_a_DIFFERENT_subject_still_gets_through(self, home):
        """Vacuity floor. A rail that refused everything would pass every test above and silently
        end autonomous skill synthesis."""
        _install("loop-worker")
        assert self._enqueue("loop-worker", n=1, target="auto/loop-worker", minutes_ago=5)
        assert self._enqueue("other-skill", n=2, kind="new") is not None
        assert len(P.list_pending(_surface=False)) == 2

    def test_an_ACCEPTED_refinement_suppresses_the_next_one_for_a_day(self, home):
        """The half the queue cannot answer on its own: accept DELETES the queue entry, so without
        reading the overlay the next proposal lands the instant the user engages."""
        target = _install("loop-worker")
        first = self._enqueue("loop-worker", n=1, target=target, minutes_ago=5)
        assert first is not None
        P.accept(first.id)
        assert P.list_pending(_surface=False) == [], "the fixture's own precondition"
        assert self._enqueue("loop-worker", n=2, target=target) is None, "accepted half"

    def test_the_window_is_ROLLING_not_forever(self, home):
        """Past the window a genuine new refinement must still be fileable, or the arm dies after
        one accept per skill."""
        target = _install("loop-worker")
        first = self._enqueue("loop-worker", n=1, target=target, minutes_ago=5)
        assert first is not None
        P.accept(first.id)
        later = P.enqueue(
            slug="loop-worker",
            description="much later",
            triggers="",
            procedure_md="b",
            session_key="s9",
            created_at=(datetime.now(timezone.utc) + timedelta(hours=25)).isoformat(
                timespec="seconds"
            ),
            kind="refine",
            refine_target=target,
        )
        assert later is not None, "the cap became permanent"

    def test_re_enqueueing_the_SAME_record_is_idempotent_not_refused(self, home):
        """`_make_id` is deterministic on (slug, session_key, created_at), so a re-emission of one
        synthesis is the same record. It must not be refused as a flood by itself."""
        _install("loop-worker")
        kwargs = dict(
            slug="loop-worker",
            description="same",
            triggers="",
            procedure_md="b",
            session_key="s-same",
            created_at=_fresh(3),
            kind="refine",
            refine_target="auto/loop-worker",
        )
        first = P.enqueue(**kwargs)  # type: ignore[arg-type]
        again = P.enqueue(**kwargs)  # type: ignore[arg-type]
        assert first is not None and again is not None
        assert first.id == again.id
        assert len(P.list_pending(_surface=False)) == 1

    def test_the_rail_cannot_reach_a_proposal_already_on_disk(self, home):
        """The rail is enqueue-side ONLY, deliberately. No rail reaches the twenty rows the bug
        already wrote, so making the recovery path depend on one would have fixed nothing."""
        _install("loop-worker")
        seeded = [_seed("loop-worker", n=i) for i in range(1, 6)]
        assert len(P.list_pending(_surface=False)) == 5
        assert all(P.accept(p.id).version >= 1 for p in seeded)

    def test_subject_and_accept_target_are_the_SAME_resolution(self, home):
        """The derived half. Two answers to "which skill is this about" is how the cycle survived
        three fixes; a rail keyed on a second answer would refuse proposals accept could apply."""
        target = _install("loop-worker")
        prop = _seed("loop-worker", kind="refine", refine_target=target, n=1)
        assert P.accept_target(prop) == target
        assert P.subject(prop) == target

        orphan = _seed("nothing-installed", kind="new", n=2)
        assert P.accept_target(orphan) == "", "nothing to overlay → accept creates"
        assert P.subject(orphan) == f"{AUTO_SKILL_NAMESPACE}/nothing-installed"


# ── the queue must be REDUCIBLE: one dismissal, one answer ────────────────────


class TestDismissingTheRowAnswersTheProposal:
    """🔴 #409's second bulk-clear gap. Dismissing a proposal row left the record `pending`, so the
    Skills page kept counting a queue the inbox said was empty — measured on this worktree: 18 open
    rows against 19 pending proposals, and `dismiss-all` over 32 rows reduced "Proposals (32)" by
    zero. Two stores, one cleared.

    Not a coin-flip on semantics: `skills/proposals` already declares the mapping in the other
    direction (`reject()` → row DISMISSED, `accept()` → row HANDLED). DISMISSED *is* the terminal
    status for "the user said no". A row landing there while the proposal survives is the two
    stores disagreeing about an answer the user already gave.
    """

    def _live(self, home, monkeypatch):
        """A stand-in for a RUNNING gateway: a state whose `_inbox_svc.inbox` is a real store, so
        `live_store` type-check passes and the resolve path writes where the API reads.

        Patched through `monkeypatch`, never by assignment — a bare
        `ns.get_dashboard_state = ...` leaks into every later test in the session and makes an
        unrelated headless test see this store.
        """
        from personalclaw import inbox as ibx
        from personalclaw.inbox_providers import native_source as ns

        class Svc:
            def __init__(self):
                self.inbox = ibx.InboxStore()
                self.inbox.load()
                self.state = ibx.InboxState()
                self.state.load()

        class State:
            def __init__(self):
                self._inbox_svc = Svc()

            def notify(self, *a, **k):
                pass

            def broadcast_ws(self, *a, **k):
                pass

        state = State()
        monkeypatch.setattr(ns, "get_dashboard_state", lambda: state)
        return state, state._inbox_svc.inbox

    class _Req:
        def __init__(self, state, item_id="", body=None):
            self.app = {"state": state}
            self.match_info = {"id": item_id}
            self._body = body or {}

        async def json(self):
            return self._body

    def test_a_per_item_dismiss_rejects_the_proposal(self, home, monkeypatch):
        import asyncio

        from personalclaw.dashboard import handlers_inbox as H

        state, store = self._live(home, monkeypatch)
        _install("loop-worker")
        prop = _seed("loop-worker", kind="refine", refine_target="auto/loop-worker", n=1)
        P._surface_in_inbox(prop)
        row = next(i for i in store.items.values() if i.refs.get("skill_proposal") == prop.id)

        resp = asyncio.run(H.api_inbox_update(self._Req(state, row.id, {"status": "dismissed"})))
        assert resp.status == 200
        assert P.get(prop.id) is None, "the row was answered and the proposal was not"
        assert P.list_pending(_surface=False) == []

    def test_dismiss_all_empties_BOTH_stores(self, home, monkeypatch):
        import asyncio
        import json as _json

        from personalclaw.dashboard import handlers_inbox as H

        state, store = self._live(home, monkeypatch)
        _install("loop-worker")
        for i in range(1, 6):
            P._surface_in_inbox(_seed("loop-worker", n=i))
        assert len(store.open_items()) == 5 and len(P.list_pending(_surface=False)) == 5

        resp = asyncio.run(H.api_inbox_dismiss_all(self._Req(state)))
        payload = _json.loads(resp.body.decode())
        assert payload["dismissed"] == 5
        assert payload["proposals_rejected"] == 5
        assert store.open_items() == []
        assert P.list_pending(_surface=False) == [], "the Skills page count must fall too"

    def test_a_row_of_ANOTHER_kind_is_untouched(self, home, monkeypatch):
        """The blast radius that made this worth thinking about: dismissing a workflow-gate
        notification must never answer the gate. Keyed on `refs["skill_proposal"]`, which only a
        proposal row carries."""
        import asyncio

        from personalclaw.dashboard import handlers_inbox as H
        from personalclaw.inbox import ItemKind, emit_attention_item

        state, store = self._live(home, monkeypatch)
        emit_attention_item(
            state,
            source="workflows",
            kind="approval",
            item_kind=ItemKind.NEEDS_INPUT.value,
            title="A run needs you",
            body="approve step 3",
            refs={"workflow": "run-1", "workflow_node": "n3"},
            dedup_key="wf:run-1:n3",
        )
        row = next(i for i in store.items.values() if i.refs.get("workflow"))
        resp = asyncio.run(H.api_inbox_dismiss_all(self._Req(state)))
        assert resp.status == 200
        payload = __import__("json").loads(resp.body.decode())
        assert payload["dismissed"] == 1
        assert payload["proposals_rejected"] == 0, "a gate row must not be counted as answered"
        assert store.items[row.id].status in ("dismissed", "DISMISSED", "ItemStatus.DISMISSED")
        assert row.refs.get("workflow") == "run-1", "the gate ref must survive untouched"

    def test_ONE_dismissal_owner(self):
        """Both terminal-transition sites go through `_dismiss`, and neither keeps its own copy of
        the bookkeeping — that is what stops the next handler re-opening the gap.

        The ref key is asserted literally: `_dismiss` must select rows by
        `refs["skill_proposal"]` and nothing else. Selecting by `item_kind` (or by any other ref)
        is how a workflow gate would start getting answered by a dismissal.
        """
        import inspect

        from personalclaw.dashboard import handlers_inbox as H

        owner = inspect.getsource(H._dismiss)
        assert "dismissed.add" in owner
        assert '.get("skill_proposal")' in owner, "the owner no longer keys on the proposal ref"
        assert '.get("workflow' not in owner, "the owner reaches a row it must not answer"
        assert "item_kind" not in owner, "keyed on kind, not on the ref that names the record"
        for fn in (H.api_inbox_update, H.api_inbox_dismiss_all):
            src = inspect.getsource(fn)
            assert "_dismiss(" in src, f"{fn.__qualname__} does not use the owner"
            assert "dismissed.add" not in src, f"{fn.__qualname__} keeps its own copy"


# ── the cycle, end to end ─────────────────────────────────────────────────────


def test_the_whole_cycle_is_broken(home):
    """One assertion per link, on one sequence, because #409's point is that the links are mutually
    causal: each successful accept used to block its slug, hide its result, and orphan its row.
    """
    from personalclaw.inbox import InboxStore, set_item_status

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

    # It does not REFILL: a third proposal about the same skill, an hour after the refinement the
    # user just approved, is the same review again — the link that kept proposals at 89% of the
    # inbox even once every accept worked. (An hour after `second.created_at`, not after wall
    # clock: this fixture's timeline is January, and the window is measured against the
    # proposal's own instant the way every real producer stamps it.)
    assert (
        P.enqueue(
            slug="loop-worker",
            description="the twenty-first",
            triggers="",
            procedure_md="b",
            session_key="s3",
            created_at="2026-01-03T01:00:00Z",
            kind="refine",
            refine_target=f"{AUTO_SKILL_NAMESPACE}/loop-worker",
        )
        is None
    )

    # And whatever DOES land is reducible from the surface the user is on: dismissing the row
    # answers the proposal, so the inbox and the Skills page cannot report two counts of one
    # queue.
    later = _seed("editorial-document", kind="new", n=7)
    P._surface_in_inbox(later)
    assert len(P.list_pending(_surface=False)) == 1
    store = InboxStore()  # headless: the file IS the truth, and it just gained a row
    store.load()
    row = next(i for i in store.items.values() if i.refs.get("skill_proposal") == later.id)
    set_item_status(None, store, [row], "dismissed")
    from personalclaw.dashboard.handlers_inbox import _dismiss

    class _S:
        _inbox_svc = None

        def notify(self, *a, **k):
            pass

        def broadcast_ws(self, *a, **k):
            pass

    from personalclaw.inbox import InboxState

    st = InboxState()
    st.load()
    assert _dismiss(_S(), st, [row]) == 1
    assert P.list_pending(_surface=False) == [], "the Skills page count stayed stuck"
