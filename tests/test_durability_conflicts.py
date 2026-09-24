"""DAS-7 — sync conflict records, the review queue, and the propose-only merge pass.

Covers the three rules §4.2 turns on:
  * both-sides-edited is the trigger (a one-sided divergence is a fast-forward, not noise);
  * the local version stays authoritative (byte-identical after a conflicting pull);
  * a missing/failing model loses the PROPOSAL, never the CONFLICT (fail-open),
plus a source-level rail that the dangerous direction — applying a proposal — is unreachable.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from personalclaw.durability import (
    conflict_merge,
    conflicts,
)
from personalclaw.durability import inventory as inv
from personalclaw.durability import (
    reconcile,
    writeback,
)
from personalclaw.durability.registry import Registry

_ENTRY = inv.StateEntry(
    id="tasks_test",
    kind=inv.KIND_JSON_ENTITY_DIR,
    path="tasks",
    domain=inv.DOMAIN_WORK,
    merge=inv.MERGE_LWW,
    help="test entity dir",
)


def _row(rid: str, text: str, updated: str = "2026-01-01T00:00:00Z") -> dict:
    """An entity-dir exporter row: {"id": stem, "data": the file's JSON}."""
    return {"id": rid, "data": {"id": rid, "text": text, "updated_at": updated}}


def _write_local(home: pathlib.Path, rows: list[dict]) -> None:
    """Seed the local store through the SAME writer the reconcile uses, so a later
    byte-comparison measures content and not formatting drift."""
    writeback.apply_rows(_ENTRY.kind, home / _ENTRY.path, rows)


# ── detection: the both-sides-edited rule ────────────────────────────────────


class TestDetection:
    def test_both_sides_edited_since_ancestor_is_a_conflict(self):
        base = _row("t1", "base")
        local = _row("t1", "local edit", "2026-02-01T00:00:00Z")
        remote = _row("t1", "remote edit", "2026-02-02T00:00:00Z")
        ancestors = {"t1": conflicts.row_sha(base)}
        found = conflicts.detect_conflicts(_ENTRY, [local], [remote], ancestors, now="NOW")
        assert [c.entity_id for c in found] == ["t1"]
        rec = found[0]
        assert rec.ancestor_sha == ancestors["t1"]
        assert rec.local_sha == conflicts.row_sha(local)
        assert rec.remote_sha == conflicts.row_sha(remote)
        # Both versions are IN the record — neither is destroyed by detection.
        assert rec.local_row == local and rec.remote_row == remote
        assert rec.status == conflicts.STATUS_NEEDS_REVIEW and rec.proposal is None

    def test_one_sided_divergence_is_a_fast_forward_not_a_conflict(self):
        """Only the remote moved since the ancestor → the deterministic merge owns it.
        Otherwise every ordinary sync would manufacture review noise."""
        base = _row("t1", "base")
        remote = _row("t1", "remote edit", "2026-02-02T00:00:00Z")
        ancestors = {"t1": conflicts.row_sha(base)}
        assert conflicts.detect_conflicts(_ENTRY, [base], [remote], ancestors) == []
        # …and symmetrically when only the local side moved.
        local = _row("t1", "local edit", "2026-02-01T00:00:00Z")
        assert conflicts.detect_conflicts(_ENTRY, [local], [base], ancestors) == []

    def test_no_ancestor_or_converged_rows_are_not_conflicts(self):
        local = _row("t1", "a")
        remote = _row("t1", "b")
        assert conflicts.detect_conflicts(_ENTRY, [local], [remote], {}) == []
        anc = {"t1": conflicts.row_sha(_row("t1", "base"))}
        assert conflicts.detect_conflicts(_ENTRY, [local], [local], anc) == []

    def test_append_dedup_streams_never_conflict(self):
        """A stable event id means "the same append" — a re-import is a no-op, not a
        divergence, so an append stream can't produce a conflict record."""
        entry = inv.StateEntry(
            id="events_test",
            kind=inv.KIND_JSONL_APPEND,
            path="events.jsonl",
            domain=inv.DOMAIN_PLATFORM,
            merge=inv.MERGE_APPEND_DEDUP,
            help="test stream",
        )
        anc = {"e1": conflicts.row_sha({"id": "e1", "v": 0})}
        found = conflicts.detect_conflicts(
            entry, [{"id": "e1", "v": 1}], [{"id": "e1", "v": 2}], anc
        )
        assert found == []

    def test_record_id_is_deterministic_per_divergence(self):
        base, local, remote = _row("t1", "b"), _row("t1", "l"), _row("t1", "r")
        anc = {"t1": conflicts.row_sha(base)}
        a = conflicts.detect_conflicts(_ENTRY, [local], [remote], anc)[0]
        b = conflicts.detect_conflicts(_ENTRY, [local], [remote], anc)[0]
        assert a.id == b.id  # re-detection dedups instead of piling up

    def test_domain_routes_to_the_review_surface(self):
        assert conflicts.surface_for_domain(inv.DOMAIN_MEMORY) == conflicts.SURFACE_MEMORY
        assert conflicts.surface_for_domain(inv.DOMAIN_KNOWLEDGE) == conflicts.SURFACE_KNOWLEDGE
        assert conflicts.surface_for_domain(inv.DOMAIN_WORK) == conflicts.SURFACE_DURABILITY


# ── the queue ────────────────────────────────────────────────────────────────


class TestQueue:
    def _conflict(self, entry_id="tasks_test", entity_id="t1", surface="durability"):
        return conflicts.ConflictRecord(
            entry_id=entry_id,
            entity_id=entity_id,
            domain=inv.DOMAIN_WORK,
            surface=surface,
            ancestor_sha="a",
            local_sha="l",
            remote_sha="r",
            local_row={"id": entity_id},
            remote_row={"id": entity_id},
            detected_at="NOW",
        )

    def test_record_dedups_and_round_trips(self, tmp_path):
        q = conflicts.ConflictQueue(tmp_path)
        rec = self._conflict()
        assert q.record(rec) is True
        assert q.record(rec) is False  # same divergence → one row
        items = q.items()
        assert len(items) == 1 and items[0].id == rec.id
        assert items[0].local_row == rec.local_row

    def test_surface_filter_separates_memory_from_knowledge(self, tmp_path):
        q = conflicts.ConflictQueue(tmp_path)
        q.record(self._conflict(entity_id="m1", surface=conflicts.SURFACE_MEMORY))
        q.record(self._conflict(entity_id="k1", surface=conflicts.SURFACE_KNOWLEDGE))
        mem = q.items(surface=conflicts.SURFACE_MEMORY)
        kno = q.items(surface=conflicts.SURFACE_KNOWLEDGE)
        assert [r.entity_id for r in mem] == ["m1"]
        assert [r.entity_id for r in kno] == ["k1"]

    def test_update_never_creates_a_record(self, tmp_path):
        q = conflicts.ConflictQueue(tmp_path)
        rec = self._conflict()
        assert q.update(rec) is False and q.items() == []

    def test_held_ids_are_the_unresolved_ones(self, tmp_path):
        q = conflicts.ConflictQueue(tmp_path)
        q.record(self._conflict(entity_id="t1"))
        assert q.held_ids("tasks_test") == {"t1"}
        assert q.held_ids("other_entry") == set()

    def test_a_corrupt_line_does_not_hide_the_queue(self, tmp_path):
        q = conflicts.ConflictQueue(tmp_path)
        q.record(self._conflict())
        with q.path.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        assert len(q.items()) == 1


# ── the registry's ancestor map ──────────────────────────────────────────────


class TestAncestorRegistry:
    def test_ancestors_round_trip_through_the_shared_registry(self):
        r = Registry.empty()
        r.record_ancestors("tasks_test", {"t1": "sha1"})
        reloaded = Registry.loads(r.to_bytes())
        assert reloaded.ancestors_for("tasks_test") == {"t1": "sha1"}
        assert reloaded.sha() == r.sha()  # canonical bytes, so CAS still works

    def test_corrupt_ancestors_degrade_to_no_ancestry(self):
        raw = json.dumps({"machines": {}, "ancestors": {"tasks_test": "nope"}}).encode()
        assert Registry.loads(raw).ancestors_for("tasks_test") == {}


# ── reconcile: local stays authoritative ─────────────────────────────────────


class TestReconcileHoldsLocal:
    def _setup(self, tmp_path):
        base = _row("t1", "base")
        local = _row("t1", "LOCAL EDIT", "2026-02-01T00:00:00Z")
        # The remote's updated_at is NEWER, so plain LWW would overwrite the local row —
        # which is exactly what the conflict hold must prevent.
        remote = _row("t1", "REMOTE EDIT", "2026-09-09T00:00:00Z")
        _write_local(tmp_path, [local])
        return base, local, remote

    def test_conflict_leaves_local_bytes_identical(self, tmp_path):
        base, local, remote = self._setup(tmp_path)
        path = tmp_path / _ENTRY.path / "t1.json"
        before = path.read_bytes()
        q = conflicts.ConflictQueue(tmp_path)
        res = reconcile.reconcile_entry(
            tmp_path,
            _ENTRY,
            [remote],
            ancestors={"t1": conflicts.row_sha(base)},
            queue=q,
            now="NOW",
        )
        assert res.conflicts == 1
        assert res.verdict == "consumed"  # recorded, so re-pulling forever would add nothing
        assert path.read_bytes() == before, "the local version must stay authoritative"
        assert json.loads(path.read_text())["text"] == "LOCAL EDIT"
        queued = q.items()
        assert len(queued) == 1 and queued[0].remote_row == remote
        # A held id keeps its OLD ancestor, so the conflict re-detects next cycle.
        assert "t1" not in res.new_ancestors

    def test_a_conflict_stays_held_on_a_later_cycle(self, tmp_path):
        base, local, remote = self._setup(tmp_path)
        q = conflicts.ConflictQueue(tmp_path)
        anc = {"t1": conflicts.row_sha(base)}
        reconcile.reconcile_entry(tmp_path, _ENTRY, [remote], ancestors=anc, queue=q, now="NOW")
        path = tmp_path / _ENTRY.path / "t1.json"
        before = path.read_bytes()
        # Second cycle, same divergence: nothing new recorded, local still authoritative.
        res = reconcile.reconcile_entry(
            tmp_path, _ENTRY, [remote], ancestors=anc, queue=q, now="N2"
        )
        assert res.conflicts == 0 and len(q.items()) == 1
        assert path.read_bytes() == before

    def test_fast_forward_still_merges_and_records_an_ancestor(self, tmp_path):
        """The non-conflict path must be untouched: a one-sided remote edit applies."""
        base = _row("t1", "base")
        remote = _row("t1", "remote edit", "2026-09-09T00:00:00Z")
        _write_local(tmp_path, [base])
        q = conflicts.ConflictQueue(tmp_path)
        res = reconcile.reconcile_entry(
            tmp_path,
            _ENTRY,
            [remote],
            ancestors={"t1": conflicts.row_sha(base)},
            queue=q,
            now="NOW",
        )
        assert res.conflicts == 0 and q.items() == []
        text = json.loads((tmp_path / _ENTRY.path / "t1.json").read_text())["text"]
        assert text == "remote edit"
        assert res.new_ancestors["t1"] == conflicts.row_sha(remote)

    def test_new_remote_rows_still_arrive_during_a_conflict(self, tmp_path):
        """A conflict on one id must not hold the whole entry hostage."""
        base, local, remote = self._setup(tmp_path)
        fresh = _row("t2", "brand new")
        res = reconcile.reconcile_entry(
            tmp_path,
            _ENTRY,
            [remote, fresh],
            ancestors={"t1": conflicts.row_sha(base)},
            queue=conflicts.ConflictQueue(tmp_path),
            now="NOW",
        )
        assert res.conflicts == 1 and res.added == 1
        assert (tmp_path / _ENTRY.path / "t2.json").is_file()


# ── the propose-only pass ────────────────────────────────────────────────────


class TestProposeOnly:
    def _queued(self, tmp_path):
        q = conflicts.ConflictQueue(tmp_path)
        q.record(
            conflicts.ConflictRecord(
                entry_id="tasks_test",
                entity_id="t1",
                domain=inv.DOMAIN_WORK,
                surface=conflicts.SURFACE_DURABILITY,
                ancestor_sha="a",
                local_sha="l",
                remote_sha="r",
                local_row={"id": "t1", "text": "LOCAL EDIT"},
                remote_row={"id": "t1", "text": "REMOTE EDIT"},
                detected_at="NOW",
            )
        )
        return q

    @pytest.mark.asyncio
    async def test_draft_is_a_proposal_never_an_application(self, tmp_path, monkeypatch):
        q = self._queued(tmp_path)
        _write_local(tmp_path, [_row("t1", "LOCAL EDIT")])
        before = (tmp_path / _ENTRY.path / "t1.json").read_bytes()
        seen: dict = {}

        async def fake(prompt, **kw):
            seen["use_case"] = kw.get("use_case")
            return json.dumps({"merged": {"id": "t1", "text": "MERGED"}, "rationale": "why"})

        monkeypatch.setattr(conflict_merge, "one_shot_completion", fake)
        report = await conflict_merge.draft_proposals(tmp_path, now="T1")
        assert report.drafted == 1 and report.failed == 0
        assert seen["use_case"] == "background"  # the reasoning axis, per §4.2
        rec = q.items()[0]
        assert rec.proposal == {"id": "t1", "text": "MERGED"}
        assert rec.rationale == "why" and rec.proposed_at == "T1"
        assert rec.status == conflicts.STATUS_NEEDS_REVIEW  # never auto-applied
        assert (tmp_path / _ENTRY.path / "t1.json").read_bytes() == before

    @pytest.mark.asyncio
    async def test_no_model_keeps_the_conflict_without_a_proposal(self, tmp_path, monkeypatch):
        """Fail-open: a missing model must never lose the conflict and never resolve it."""
        q = self._queued(tmp_path)

        async def boom(prompt, **kw):
            raise RuntimeError("no model configured")

        monkeypatch.setattr(conflict_merge, "one_shot_completion", boom)
        report = await conflict_merge.draft_proposals(tmp_path, now="T1")
        assert report.failed == 1 and report.drafted == 0
        rec = q.items()[0]
        assert rec.status == conflicts.STATUS_NEEDS_REVIEW
        assert rec.proposal is None
        assert "no model configured" in rec.proposal_error
        assert len(q.items()) == 1  # the conflict survived

    @pytest.mark.asyncio
    async def test_unparseable_answer_is_also_fail_open(self, tmp_path, monkeypatch):
        q = self._queued(tmp_path)

        async def junk(prompt, **kw):
            return "sure! here's a merge, roughly"

        monkeypatch.setattr(conflict_merge, "one_shot_completion", junk)
        report = await conflict_merge.draft_proposals(tmp_path, now="T1")
        assert report.failed == 1
        rec = q.items()[0]
        assert rec.proposal is None and rec.status == conflicts.STATUS_NEEDS_REVIEW
        assert rec.proposal_error

    @pytest.mark.asyncio
    async def test_a_failed_draft_is_not_retried_every_pass(self, tmp_path, monkeypatch):
        self._queued(tmp_path)
        calls = {"n": 0}

        async def boom(prompt, **kw):
            calls["n"] += 1
            raise RuntimeError("down")

        monkeypatch.setattr(conflict_merge, "one_shot_completion", boom)
        await conflict_merge.draft_proposals(tmp_path)
        second = await conflict_merge.draft_proposals(tmp_path)
        assert calls["n"] == 1 and second.considered == 0

    @pytest.mark.asyncio
    async def test_fenced_json_still_parses(self, tmp_path, monkeypatch):
        q = self._queued(tmp_path)

        async def fenced(prompt, **kw):
            body = json.dumps({"merged": {"id": "t1"}, "rationale": "r"})
            return f"```json\n{body}\n```"

        monkeypatch.setattr(conflict_merge, "one_shot_completion", fenced)
        assert (await conflict_merge.draft_proposals(tmp_path)).drafted == 1
        assert q.items()[0].proposal == {"id": "t1"}


# ── source-level rail: the dangerous direction is unreachable ────────────────


def test_conflict_modules_cannot_write_the_live_store():
    """A propose-only surface must be structurally unable to apply. Not "doesn't today" —
    unreachable: neither conflict module may import the writeback/reconcile path or call any
    store-writing helper. A later edit that wires one up trips this."""
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "durability"
    banned_modules = {"personalclaw.durability.writeback", "personalclaw.durability.reconcile"}
    banned_calls = {"apply_rows", "reconcile_entry", "atomic_write", "atomic_write_bytes"}
    checked = 0
    for name in ("conflict_merge.py", "conflicts.py"):
        path = src / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in banned_modules, f"{name} imports {node.module}"
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    assert full not in banned_modules, f"{name} imports {full}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in banned_modules, f"{name} imports {alias.name}"
            if isinstance(node, ast.Call):
                fn = node.func
                fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name == "conflict_merge.py":
                    assert fname not in banned_calls, f"{name} calls {fname}()"
        checked += 1
    assert checked == 2, "the rail must actually have scanned both modules (vacuity floor)"


def test_the_queue_never_lands_in_an_entrys_store_path():
    """The queue's own path must not collide with any inventory entry — a conflict record
    can never be mistaken for state the sync cycle rewrites."""
    assert conflicts.CONFLICTS_PATH.startswith("sync/")
    assert inv.is_ignored("sync"), "the sync root must be an ignored machine-local dir"
    assert not any(
        e.path == conflicts.CONFLICTS_PATH or e.path.startswith("sync/") for e in inv.INVENTORY
    )


# ── criterion 5, end to end through the real cycle ───────────────────────────


class TestCriterionFive:
    """Two machines, one shared store: the same task edited on both while offline yields a
    conflict-review item and applies NOTHING (DAS-7 done_when / plan criterion 5).

    Also the wiring proof for this atom's new seams: the shared registry's ancestor map is
    written by the pull and published by the CAS bump, and the queue is fed by the cycle —
    a call site that merely exists would not produce any of this.
    """

    def _write_task(self, home, tid, title, updated):
        # Written through the cycle's own writer, so a later byte-comparison measures content
        # rather than JSON formatting the reconcile normalizes.
        writeback.apply_rows(
            inv.KIND_JSON_ENTITY_DIR,
            home / "tasks",
            [{"id": tid, "data": {"id": tid, "title": title, "updated_at": updated}}],
        )

    def test_offline_same_task_edit_yields_a_review_item_and_applies_nothing(self, tmp_path):
        from personalclaw.durability.sync_cycle import read_registry, run_sync_cycle
        from tests.test_durability_sync_cycle import SharedStore

        store = SharedStore()
        a, b = tmp_path / "A", tmp_path / "B"
        # 1. A creates the task and publishes; B pulls it; A pulls B's echo. Both agree now,
        #    so the shared registry carries a common ancestor sha for t1.
        self._write_task(a, "t1", "base", "2026-01-01T00:00:00Z")
        assert run_sync_cycle(store, a, self_id="A", now="t1").ok
        assert run_sync_cycle(store, b, self_id="B", now="t2").ok
        assert run_sync_cycle(store, a, self_id="A", now="t3").ok
        ancestor = read_registry(store).ancestors_for("tasks").get("t1")
        assert ancestor, "the pull must publish the agreed ancestor sha into the registry"

        # 2. Both edit the same task offline. B's timestamp is NEWER, so plain LWW would
        #    overwrite A's edit — the conflict hold is what prevents that.
        self._write_task(a, "t1", "A edit", "2026-02-01T00:00:00Z")
        self._write_task(b, "t1", "B edit", "2026-03-01T00:00:00Z")
        assert run_sync_cycle(store, a, self_id="A", now="t4").ok

        # 3. B syncs and sees A's divergent version.
        before = (b / "tasks" / "t1.json").read_bytes()
        report = run_sync_cycle(store, b, self_id="B", now="t5")
        assert report.ok and report.conflicts == 1
        assert "conflict" in report.detail

        # Applies nothing: B's local version is byte-identical and still authoritative.
        assert (b / "tasks" / "t1.json").read_bytes() == before
        assert json.loads((b / "tasks" / "t1.json").read_text())["title"] == "B edit"

        # And it is a review item carrying BOTH versions and all three shas.
        queued = conflicts.ConflictQueue(b).items(status=conflicts.STATUS_NEEDS_REVIEW)
        assert len(queued) == 1
        rec = queued[0]
        assert rec.entry_id == "tasks" and rec.entity_id == "t1"
        assert rec.ancestor_sha == ancestor
        assert rec.local_row["data"]["title"] == "B edit"
        assert rec.remote_row["data"]["title"] == "A edit"
        assert rec.proposal is None  # no model in this test → needs-review without a draft

        # The ancestor is NOT advanced for a held id, so the conflict re-detects rather than
        # self-resolving on the next cycle.
        assert read_registry(store).ancestors_for("tasks").get("t1") == ancestor
        assert run_sync_cycle(store, b, self_id="B", now="t6").ok
        assert len(conflicts.ConflictQueue(b).items()) == 1
        assert (b / "tasks" / "t1.json").read_bytes() == before
