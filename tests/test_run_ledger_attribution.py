"""TSE2-1 (MULTI-TENANCY-ENTITY): run-ledger attribution — `owner_username` + `origin_harness`.

The audit reserved this at design time because the run store is cheap to touch now and expensive
to retrofit once it federates: a run record must be able to say WHO authored it and WHICH machine
minted it, so a shared/synced run store attributes a run rather than silently folding it into "the"
owner's. The field is added where the schema lives (`WorkflowRun` + the SQLite row), stamped from
the SHIPPED primitives — `identity.current_username()` and `durability.shards.machine_id` — never a
parallel mechanism, and carried onto the opening run-journal row and the mutation ledger row.

Two axes must not be conflated, and these tests hold the line: `actor` on a mutation row is the
KIND of change (`chat`/`engine`), untouched here; `owner_username`/`origin_harness` are WHO and
WHICH-machine. Every attribution field is optional and empty degrades to today's behaviour — a run
with no recorded owner is the owner's (`belongs_to` mirrors `Task.belongs_to`), so a solo install is
byte-identical but for two empty defaults.

The non-cheatable bar is the last test: a foreign-authored run stays VISIBLE in run history with its
own author, yet is EXCLUDED from the owner's "my runs" count. It asserts both a known-true (the
owner's run is counted) and a known-false (the foreign run is not) case, so neither "count
everything" nor "count nothing" satisfies it. On pre-plan code — no `owner_username`, no
`belongs_to` — it cannot even run.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.workflows import handlers
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import mutations
from personalclaw.workflows import store as st
from personalclaw.workflows.models import WorkflowRun

OWNER = "keyur-golani"
FOREIGN = "teammate-bob"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch) -> Path:
    """Point the store at an isolated home (the store imported `config_dir` by value, so both
    bindings are patched), exactly as `test_workflows_store.py` does."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    return tmp_path


def _run(**kw) -> WorkflowRun:
    base = {"id": "", "workflow_name": "research"}
    base.update(kw)
    return WorkflowRun(**base)  # type: ignore[arg-type]


# ── the serialization reserve: a pre-plan row round-trips but for the empty defaults ──


def test_a_pre_plan_run_round_trips_identically_but_for_the_new_empty_defaults() -> None:
    """A run dict as an OLD store wrote it — no attribution keys — reads back with the two new
    fields defaulted to "" and re-serializes to exactly the old dict plus those two empty keys.

    This is the invisible-single-user guarantee: a default that changed an existing run's
    serialization would be a silent break for solo users (plan §Risks)."""
    pre_plan = WorkflowRun(id="run00001", workflow_name="research").to_dict()
    pre_plan.pop("owner_username")
    pre_plan.pop("origin_harness")

    got = WorkflowRun.from_dict(pre_plan).to_dict()

    assert got == {**pre_plan, "owner_username": "", "origin_harness": ""}
    # An unattributed row is the owner's, so it is never wrongly excluded from "my runs".
    assert WorkflowRun.from_dict(pre_plan).belongs_to(OWNER) is True


# ── create() stamps the shipped primitives, once, at the creation seam ──


def test_create_stamps_the_owner_and_this_machine() -> None:
    with patch("personalclaw.identity.current_username", return_value=OWNER):
        run = st.create(_run())

    from personalclaw.durability.shards import machine_id

    assert run.owner_username == OWNER
    # origin_harness is the durability machine_id for THIS home — reused, not minted anew.
    assert run.origin_harness == machine_id(st.config_dir())
    assert run.origin_harness  # non-empty

    got = st.get(run.id)
    assert got is not None
    assert got.owner_username == OWNER
    assert got.origin_harness == run.origin_harness


def test_create_preserves_a_preset_foreign_author_rather_than_overwriting_it() -> None:
    """A caller that already knows whose row this is (a federated provider, a replay) keeps its
    attribution — the stamp only fills an EMPTY field, like `created_at`/`root_run_id`."""
    with patch("personalclaw.identity.current_username", return_value=OWNER):
        run = st.create(_run(owner_username=FOREIGN))
    assert st.get(run.id).owner_username == FOREIGN


# ── belongs_to mirrors Task ownership ──


def test_belongs_to_mirrors_task_ownership() -> None:
    assert _run(owner_username=OWNER).belongs_to(OWNER) is True
    assert _run(owner_username=FOREIGN).belongs_to(OWNER) is False
    # Empty attribution = the owner's (today's behaviour); no identity configured = everything mine.
    assert _run(owner_username="").belongs_to(OWNER) is True
    assert _run(owner_username=FOREIGN).belongs_to("") is True


# ── the opening run-journal row and the mutation ledger row both carry attribution ──


def test_run_started_journal_row_carries_attribution() -> None:
    with patch("personalclaw.identity.current_username", return_value=OWNER):
        run = st.create(_run())
    journal = journal_mod.Journal(run.id)
    journal.run_started(
        run.workflow_name,
        inputs={},
        spec_version=1,
        owner_username=run.owner_username,
        origin_harness=run.origin_harness,
    )
    rows = journal_mod.journal_records(run.id, kinds={journal_mod.RUN_STARTED})
    assert len(rows) == 1
    assert rows[0]["owner_username"] == OWNER
    assert rows[0]["origin_harness"] == run.origin_harness


def test_mutation_ledger_record_carries_attribution_without_touching_actor() -> None:
    rec = mutations.history_record(
        [],
        actor="chat",
        version=2,
        spec={"root": {"kind": "sequence"}},
        owner_username=OWNER,
        origin_harness="machine-xyz",
    )
    # The KIND axis is exactly what it was.
    assert rec["actor"] == "chat"
    # The WHO / WHICH-machine axis is added alongside it.
    assert rec["owner_username"] == OWNER
    assert rec["origin_harness"] == "machine-xyz"

    # …and defaults to empty (pre-plan-identical) when a caller does not wire it.
    default_rec = mutations.history_record([], actor="engine", version=1, spec={})
    assert default_rec["actor"] == "engine"
    assert default_rec["owner_username"] == ""
    assert default_rec["origin_harness"] == ""


# ── the non-cheatable bar: a foreign run renders in history but is excluded from "my runs" ──


def test_a_foreign_authored_run_is_excluded_from_the_owners_my_runs_count() -> None:
    with patch("personalclaw.identity.current_username", return_value=OWNER):
        mine = st.create(_run(intent="my run"))
    foreign = st.create(_run(owner_username=FOREIGN, intent="teammate's run"))

    all_runs, total = st.list_runs()
    assert total == 2  # both persisted

    # VISIBLE in unfiltered run history, and it renders ITS author — not the owner's.
    by_id = {r.id: r for r in all_runs}
    assert by_id[foreign.id].owner_username == FOREIGN

    # …but the owner's "my runs" set is exactly the owner's run — the foreign row is excluded.
    mine_only = [r for r in all_runs if r.belongs_to(OWNER)]
    assert [r.id for r in mine_only] == [mine.id]  # known-true: the owner's run is counted
    assert len(mine_only) == 1  # known-false: the foreign run is not


@pytest.mark.asyncio
async def test_runs_list_mine_filter_excludes_foreign_and_reports_the_owner() -> None:
    """The product surface: `GET /api/workflows/runs?mine=1` scopes to the owner and reports the
    owner handle, while the unfiltered list still shows the foreign run's author."""
    from aiohttp.test_utils import make_mocked_request

    with patch("personalclaw.identity.current_username", return_value=OWNER):
        mine = st.create(_run(intent="mine"))
    st.create(_run(owner_username=FOREIGN, intent="theirs"))

    with patch("personalclaw.workflows.handlers._owner_username", return_value=OWNER):
        resp = await handlers.api_runs_list(
            make_mocked_request("GET", "/api/workflows/runs?mine=1")
        )
        body = json.loads(resp.body)

        resp_all = await handlers.api_runs_list(make_mocked_request("GET", "/api/workflows/runs"))
        body_all = json.loads(resp_all.body)

    assert resp.status == 200
    assert body["owner"] == OWNER
    assert [r["id"] for r in body["runs"]] == [mine.id]
    assert body["total"] == 1  # foreign excluded from the count

    # Unfiltered history shows both, foreign author rendered.
    assert body_all["total"] == 2
    assert FOREIGN in {r["owner_username"] for r in body_all["runs"]}
