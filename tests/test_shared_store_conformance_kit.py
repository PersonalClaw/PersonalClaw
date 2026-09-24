"""The shared-store conformance kit's OWN tests (TEAM-SHARED-HARNESS TSHR-1).

A kit whose failure path is untested is the "test exercises the mechanism, not its use"
trap: every clause could be inverted or silently vacuous and a green suite would still say
nothing. So this file does two things:

* Runs the kit against a CONFORMING provider of EACH shipped shared-store shape — a
  multi-tenant ``TaskProvider`` and a ``trigger`` store mirroring the bundled
  ``shared-automations`` app — and asserts it PASSES.
* Gives EACH clause a mutant: a provider that violates exactly one obligation, and pins
  which named clause the failure carries. The mutants ARE the non-cheatability guarantee
  the atom demands — a deliberately-unsafe provider MUST fail the suite.

The kit is imported the way an APP imports it (``personalclaw.sdk.shared_store``), so the
export path the apps depend on is the one core exercises. The two provider fixtures use the
REAL ``Task`` and ``Trigger`` models and the REAL shared owner predicates
(``Task.belongs_to``, ``triggers.ownership``) — the kit asserts the ecosystem's shipped
discipline, not a re-implementation of it.
"""

from __future__ import annotations

import pytest

from personalclaw.sdk.shared_store import (
    SharedStoreCase,
    SharedStoreContractError,
    WriteSafety,
    assert_shared_store_contract,
)
from personalclaw.security import fence_untrusted
from personalclaw.tasks.models import Task, TaskDependency
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.ownership import is_owner_authored, owner_authored

OWNER = "owner"


# ── a conforming multi-tenant TaskProvider shape ─────────────────────────────


class MultiTenantTaskStore:
    """An in-memory multi-tenant task store: the ``TaskProvider`` shared-store shape.

    Owner-scoping is the shipped ``Task.belongs_to``; a foreign task's title is surfaced to
    a prompt THROUGH ``security.fence_untrusted`` (a shared view legitimately shows a
    teammate's item, just never trusts it); writes are a sibling-preserving dict upsert, so
    a concurrently-committed record survives — a DESIGNATED_OWNER semantic.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Task] = {}

    def make(self, *, id: str, author: str, content: str = "", references=None) -> Task:
        return Task(
            id=id,
            title=content or id,
            author=author,
            dependencies=[TaskDependency(depends_on_task_id=r) for r in (references or [])],
        )

    def seed(self, records) -> None:
        self._by_id = {t.id: t for t in records}

    def all_records(self) -> list[Task]:
        return list(self._by_id.values())

    def upsert(self, task: Task) -> None:
        # Fresh read-modify-write on the shared dict: sibling-preserving, so a concurrent
        # writer's row is never clobbered.
        self._by_id[task.id] = task

    def commit_out_of_band(self, task: Task) -> None:
        # A concurrent writer (another machine) commits straight to the backing store.
        self._by_id[task.id] = task

    def content_for_prompt(self, task: Task) -> str:
        return fence_untrusted(
            task.title, source="task", source_type="shared-task", source_id=task.id
        )

    def reattribute(self, task_id: str, author: str) -> None:
        task = self._by_id.get(task_id)
        if task is not None:
            task.author = author  # keep the row visible; only its owner changes

    def case(self, **overrides) -> SharedStoreCase:
        base = dict(
            name="fixture-multitenant-tasks",
            owner=OWNER,
            write_safety=WriteSafety.DESIGNATED_OWNER,
            make_record=self.make,
            id_of=lambda t: t.id,
            belongs_to=lambda t, owner: t.belongs_to(owner),
            owner_view=lambda recs, owner: [t for t in recs if t.belongs_to(owner)],
            seed=self.seed,
            all_records=self.all_records,
            upsert=self.upsert,
            commit_out_of_band=self.commit_out_of_band,
            surfaces_foreign_content=True,
            content_for_prompt=self.content_for_prompt,
            references_of=lambda t: [d.depends_on_task_id for d in t.dependencies],
            reattribute=self.reattribute,
        )
        base.update(overrides)
        return SharedStoreCase(**base)


# ── a conforming trigger-store shape (the shared-automations app) ────────────


class SharedTriggerStore:
    """An in-memory ``trigger`` store mirroring the bundled ``shared-automations`` app.

    Owner-scoping is the shipped ``triggers.ownership`` (``owner_authored`` / the arm
    filter); foreign rows are structurally excluded from arming, so their content NEVER
    reaches a prompt (``surfaces_foreign_content=False``); a reference is a chain target
    carried in the row's ``spec``; a re-attributed row stays VISIBLE in the listing (as
    ``list_triggers``/``all_rows`` keep foreign rows). Writes are a sibling-preserving dict
    upsert — the app's fresh read-modify-write shape — a DESIGNATED_OWNER semantic.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Trigger] = {}

    def make(self, *, id: str, author: str, content: str = "", references=None) -> Trigger:
        refs = list(references or [])
        return Trigger(
            id=id,
            name=content or id,
            kind="run_completed" if refs else "clock",
            author=author,
            spec={"source_ids": refs} if refs else {},
        )

    def seed(self, records) -> None:
        self._by_id = {t.id: t for t in records}

    def all_records(self) -> list[Trigger]:
        return list(self._by_id.values())

    def upsert(self, trigger: Trigger) -> None:
        self._by_id[trigger.id] = trigger

    def commit_out_of_band(self, trigger: Trigger) -> None:
        self._by_id[trigger.id] = trigger

    def reattribute(self, trigger_id: str, author: str) -> None:
        trigger = self._by_id.get(trigger_id)
        if trigger is not None:
            trigger.author = author  # keep the row; only its owner changes

    def case(self, **overrides) -> SharedStoreCase:
        base = dict(
            name="fixture-shared-automations",
            owner=OWNER,
            write_safety=WriteSafety.DESIGNATED_OWNER,
            make_record=self.make,
            id_of=lambda t: t.id,
            belongs_to=lambda t, owner: is_owner_authored(t, owner=owner),
            owner_view=lambda recs, owner: owner_authored(recs, owner=owner),
            seed=self.seed,
            all_records=self.all_records,
            upsert=self.upsert,
            commit_out_of_band=self.commit_out_of_band,
            surfaces_foreign_content=False,
            references_of=lambda t: list(t.spec.get("source_ids", [])),
            reattribute=self.reattribute,
        )
        base.update(overrides)
        return SharedStoreCase(**base)


# ── the conforming providers PASS ────────────────────────────────────────────


def test_conforming_multitenant_task_store_passes():
    assert assert_shared_store_contract(MultiTenantTaskStore().case()) is None


def test_conforming_shared_trigger_store_passes():
    assert assert_shared_store_contract(SharedTriggerStore().case()) is None


def test_a_last_writer_wins_store_that_documents_the_risk_passes():
    """An honest LWW store is conforming — the clause requires DOCUMENTING the risk, not
    avoiding LWW. Pinned so the write-safety clause is not read as "LWW is banned"."""
    case = SharedTriggerStore().case(
        write_safety=WriteSafety.LAST_WRITER_WINS,
        commit_out_of_band=None,
        lost_update_risk_doc="Whole-file rewrite: a concurrent write to another row can be lost.",
    )
    assert assert_shared_store_contract(case) is None


def test_a_store_with_no_cross_record_references_skips_the_orphan_clause():
    """A provider whose records never reference each other declares it and clause 4 is
    vacuously safe — the honest skip, not a silent pass."""
    case = SharedTriggerStore().case(
        references_of=None,
        reattribute=None,
        no_references_reason="trigger rows in this store never reference one another",
    )
    assert assert_shared_store_contract(case) is None


# ── clause 1: foreign records excluded from owner counters ───────────────────


def test_a_counter_that_counts_foreign_rows_fails():
    """The F3 failure: a 'my items' view that forgets to exclude a teammate's rows."""
    case = MultiTenantTaskStore().case(owner_view=lambda recs, owner: list(recs))
    with pytest.raises(SharedStoreContractError, match=r"\[owner-counter\].*MUST EXCLUDE"):
        assert_shared_store_contract(case)


def test_a_scope_that_diverges_from_belongs_to_on_empty_author_fails():
    """The subtler F3 bug: strict author-equality drops an UNATTRIBUTED row that
    belongs_to counts as the owner's — a parallel scope drifting from the shared predicate."""
    case = MultiTenantTaskStore().case(
        owner_view=lambda recs, owner: [
            t for t in recs if (t.author or "").strip().lower() == owner
        ]
    )
    with pytest.raises(SharedStoreContractError, match=r"\[owner-counter\]"):
        assert_shared_store_contract(case)


# ── clause 2: foreign content entering a prompt is fenced ────────────────────


def test_surfacing_raw_foreign_content_to_a_prompt_fails():
    """The prompt-injection surface: a shared view that hands a teammate's raw text to the
    model instead of fencing it."""
    store = MultiTenantTaskStore()
    case = store.case(content_for_prompt=lambda t: t.title)  # RAW, unfenced
    with pytest.raises(SharedStoreContractError, match=r"\[foreign-content-fenced\].*is_fenced"):
        assert_shared_store_contract(case)


def test_declaring_surfaces_foreign_content_without_an_accessor_fails():
    case = SharedTriggerStore().case(surfaces_foreign_content=True, content_for_prompt=None)
    with pytest.raises(SharedStoreContractError, match=r"\[foreign-content-fenced\].*not callable"):
        assert_shared_store_contract(case)


# ── clause 3: append-only-safe or a documented last-writer-wins ──────────────


class _SnapshotLostUpdateStore(SharedTriggerStore):
    """A store whose ``upsert`` writes back a STALE snapshot, silently dropping a
    concurrently-committed row — Letta's shared-memory F4 failure. It (falsely) claims
    APPEND_ONLY, which is exactly the "claims a merge safety it lacks" the clause catches."""

    def __init__(self) -> None:
        super().__init__()
        self._snapshot: dict[str, Trigger] = {}

    def seed(self, records) -> None:
        super().seed(records)
        self._snapshot = dict(self._by_id)  # captured HERE; upsert writes this back

    def upsert(self, trigger: Trigger) -> None:
        # The bug: the writer holds a stale in-memory view and clobbers the file with it,
        # so a row a teammate committed after the snapshot is lost.
        self._snapshot[trigger.id] = trigger
        self._by_id = dict(self._snapshot)


def test_a_store_that_silently_loses_a_concurrent_write_fails_a_merge_safe_claim():
    store = _SnapshotLostUpdateStore()
    case = store.case(write_safety=WriteSafety.APPEND_ONLY)
    with pytest.raises(SharedStoreContractError, match=r"\[write-safety\].*was LOST"):
        assert_shared_store_contract(case)


def test_a_last_writer_wins_store_without_documentation_fails():
    case = SharedTriggerStore().case(
        write_safety=WriteSafety.LAST_WRITER_WINS,
        commit_out_of_band=None,
        lost_update_risk_doc="",
    )
    with pytest.raises(
        SharedStoreContractError, match=r"\[write-safety\].*document the lost-update"
    ):
        assert_shared_store_contract(case)


def test_a_merge_safe_claim_without_a_concurrency_hook_refuses_to_pass_vacuously():
    case = SharedTriggerStore().case(commit_out_of_band=None)
    with pytest.raises(SharedStoreContractError, match=r"\[write-safety\].*commit_out_of_band"):
        assert_shared_store_contract(case)


# ── clause 4: an ownership change cannot silently orphan a reference ─────────


def test_an_ownership_change_that_removes_the_record_orphans_it_and_fails():
    """n8n's failure (F6): a transfer/re-share that REVOKES visibility of a
    still-referenced record, silently orphaning everything pointing at it."""
    store = SharedTriggerStore()

    def revoking_reattribute(trigger_id: str, author: str) -> None:
        # The bug: the ownership change drops the row from the shared view entirely.
        store._by_id.pop(trigger_id, None)

    case = store.case(reattribute=revoking_reattribute)
    with pytest.raises(SharedStoreContractError, match=r"\[orphan-on-reattribute\].*VANISHED"):
        assert_shared_store_contract(case)


def test_clause_4_without_references_wiring_refuses_to_pass_vacuously():
    case = SharedTriggerStore().case(references_of=None, reattribute=None)
    with pytest.raises(SharedStoreContractError, match=r"\[orphan-on-reattribute\].*references_of"):
        assert_shared_store_contract(case)


# ── case validation ──────────────────────────────────────────────────────────


def test_a_case_missing_a_required_callable_fails_loudly():
    case = MultiTenantTaskStore().case()
    case.owner_view = None  # type: ignore[assignment]
    with pytest.raises(SharedStoreContractError, match=r"\[case\].*owner_view MUST be a callable"):
        assert_shared_store_contract(case)


def test_a_case_with_no_owner_fails():
    case = MultiTenantTaskStore().case(owner="")
    with pytest.raises(SharedStoreContractError, match=r"\[case\].*non-empty attribution"):
        assert_shared_store_contract(case)
