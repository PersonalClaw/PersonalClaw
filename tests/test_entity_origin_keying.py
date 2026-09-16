"""Entity-id origin keying (MULTI-TENANCY-ENTITY TSE2-2).

A locally-minted task / project / trigger now carries an ``origin_harness`` sidecar — this
home's ``durability`` ``machine_id`` — so two harnesses' stores merge *attributably*: every row
knows WHICH machine minted it, even when a ``t-<hex8>``-class id collides by chance. The field is
attribution, never authority (the plan's soul guardrail): it is optional, defaults to ``""`` (=
"this harness's"), and changes no behavior — a single-home install is byte-unchanged.

Four falsifiable claims, mirroring the atom's ``done_when``:

1. A NEW record's ``origin_harness`` is the local ``machine_id`` — the SAME string
   ``durability.shards.machine_id`` returns for the home, REUSED not minted here. (create-path)
2. Id generation is UNCHANGED — still ``t-<hex8>`` / ``p-<hex8>`` / the trigger slug. Origin is a
   sidecar, not an id-format change.
3. Merging two stores minted under DIFFERENT ``machine_id``s keeps every row attributable to its
   origin — and a same-id collision across the two does NOT silently collapse.
4. A pre-plan store (rows with no ``origin_harness`` key) round-trips BYTE-IDENTICAL but for the
   new field, which reads back as its ``""`` default.

Each create-path claim is paired with the independent ``machine_id`` value rather than "non-empty"
alone, because a hard-coded placeholder would pass a non-empty check while breaking attribution.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from personalclaw.durability.shards import machine_id
from personalclaw.tasks.hierarchy import HierarchyStore
from personalclaw.tasks.models import Project, Task
from personalclaw.tasks.native import NativeTaskProvider
from personalclaw.triggers import tools as T
from personalclaw.triggers.models import parse_trigger
from personalclaw.triggers.store import TriggerStore

TASK_ID_RE = re.compile(r"^t-[0-9a-f]{8}$")
PROJECT_ID_RE = re.compile(r"^p-[0-9a-f]{8}$")


# ── 1 + 2: a NEW record is stamped with THIS home's machine_id; the id format is unchanged ──


@pytest.mark.asyncio
async def test_a_created_task_is_stamped_with_this_homes_machine_id(tmp_path):
    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path):
        task = await NativeTaskProvider().create_task(title="local work")
    # The stamped origin IS durability's machine_id for this home — reused, not a fresh mint.
    assert task.origin_harness == machine_id(tmp_path)
    assert task.origin_harness != ""
    # Sidecar, not an id-format change.
    assert TASK_ID_RE.match(task.id)


@pytest.mark.asyncio
async def test_a_created_tasks_origin_is_server_owned_not_caller_set(tmp_path):
    """Origin is provenance, like ``id``/``created_at``: a caller-supplied value is IGNORED, so a
    client cannot forge which harness minted a row."""
    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path):
        task = await NativeTaskProvider().create_task(
            title="t", origin_harness="somebody-elses-machine"
        )
    assert task.origin_harness == machine_id(tmp_path)


@pytest.mark.asyncio
async def test_an_update_cannot_rewrite_a_tasks_origin(tmp_path):
    """Write-once. Re-stamping a merged foreign row with the local origin would collapse the very
    attribution this field exists to preserve — so update leaves it intact (like ``author``)."""
    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path):
        provider = NativeTaskProvider()
        task = await provider.create_task(title="before")
        original = task.origin_harness
        updated = await provider.update_task(
            task.id, title="after", origin_harness="in-process-forge"
        )
    assert updated is not None
    assert updated.title == "after"  # the edit applied — not a dead update path
    assert updated.origin_harness == original


def test_a_created_project_is_stamped_with_this_homes_machine_id(tmp_path):
    with patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
        project = HierarchyStore().create_project(name="Launch")
    assert project.origin_harness == machine_id(tmp_path)
    assert project.origin_harness != ""
    assert PROJECT_ID_RE.match(project.id)


def test_seeded_builtin_projects_are_stamped_too(tmp_path):
    """``ensure_defaults`` mints Personal/Repeatable locally, so they carry the local origin."""
    with patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
        store = HierarchyStore()
        store.ensure_defaults()
        projects = store.list_projects()
    assert projects
    assert all(p.origin_harness == machine_id(tmp_path) for p in projects)


def test_a_created_trigger_is_stamped_with_the_stores_machine_id(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    result = T.create(
        store,
        name="Daily digest",
        kind="clock",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        message="digest the day",
    )
    assert result.ok, result.text
    saved = store.get("clock:daily-digest")
    assert saved is not None
    assert saved.trigger.origin_harness == machine_id(tmp_path)
    assert saved.trigger.origin_harness != ""
    # Id format (the recognizable ``kind:slug``) is untouched — origin is a sidecar.
    assert saved.trigger.id == "clock:daily-digest"


# ── 3: two stores under different machine_ids merge attributably — no silent collision-collapse ──


@pytest.mark.asyncio
async def test_two_task_stores_under_different_homes_stay_attributable(tmp_path):
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    home_a.mkdir()
    home_b.mkdir()
    with patch("personalclaw.tasks.native.config_dir", return_value=home_a):
        task_a = await NativeTaskProvider().create_task(title="on A")
    with patch("personalclaw.tasks.native.config_dir", return_value=home_b):
        task_b = await NativeTaskProvider().create_task(title="on B")

    # Distinct homes → distinct machine_ids → each row attributable to its own origin.
    assert machine_id(home_a) != machine_id(home_b)
    assert task_a.origin_harness == machine_id(home_a)
    assert task_b.origin_harness == machine_id(home_b)

    merged = [task_a, task_b]
    by_origin: dict[str, list[str]] = {}
    for t in merged:
        by_origin.setdefault(t.origin_harness, []).append(t.id)
    assert set(by_origin) == {machine_id(home_a), machine_id(home_b)}


def test_an_id_collision_across_harnesses_does_not_silently_collapse(tmp_path):
    """The non-cheatable core. Two harnesses can independently mint the SAME ``t-<hex8>``; keyed by
    id alone a merge collapses one silently. With origin as a sidecar, the two rows are
    DISTINGUISHABLE — this is exactly what a shared-store consumer merges on."""
    collided = "t-deadbeef"
    row_a = Task.from_dict({"id": collided, "title": "from A", "origin_harness": "machine-A"})
    row_b = Task.from_dict({"id": collided, "title": "from B", "origin_harness": "machine-B"})

    # Keyed by id ALONE, a naive merge keeps one — the silent collapse the field prevents.
    naive = {row.id: row for row in (row_a, row_b)}
    assert len(naive) == 1

    # Keyed by (id, origin) — the attributable merge — keeps BOTH, each pointing at its harness.
    attributable = {(row.id, row.origin_harness): row for row in (row_a, row_b)}
    assert len(attributable) == 2
    assert {r.origin_harness for r in attributable.values()} == {"machine-A", "machine-B"}
    assert attributable[(collided, "machine-A")].title == "from A"
    assert attributable[(collided, "machine-B")].title == "from B"


def test_a_trigger_provider_served_origin_is_preserved_not_overwritten(tmp_path):
    """A row minted by ANOTHER harness (served through a shared trigger store) keeps its origin on
    read — the read path never re-stamps it as local."""
    trigger, issues = parse_trigger(
        {
            "id": "clock:foreign",
            "name": "foreign",
            "kind": "clock",
            "spec": {"kind": "cron", "expr": "0 9 * * *"},
            "origin_harness": "alices-laptop",
        }
    )
    assert not [i for i in issues if i.severity == "error"]
    assert trigger.origin_harness == "alices-laptop"
    assert trigger.to_dict()["origin_harness"] == "alices-laptop"


# ── 4: a pre-plan store round-trips byte-identical but for the new default field ──


def test_a_pre_plan_task_round_trips_byte_identical_but_for_the_default(tmp_path):
    full = Task(id="t-abc12345", title="legacy").to_dict()
    assert full["origin_harness"] == ""  # the field's default IS the empty string
    pre_plan = {
        k: v for k, v in full.items() if k != "origin_harness"
    }  # a row written before TSE2-2
    revived = Task.from_dict(pre_plan).to_dict()
    # The ONLY difference from a pre-plan row is the new field, defaulted to "".
    assert revived == full
    assert revived["origin_harness"] == ""


def test_a_pre_plan_project_round_trips_byte_identical_but_for_the_default(tmp_path):
    full = Project(id="p-abc12345", name="legacy").to_dict()
    assert full["origin_harness"] == ""
    pre_plan = {k: v for k, v in full.items() if k != "origin_harness"}
    revived = Project.from_dict(pre_plan).to_dict()
    assert revived == full
    assert revived["origin_harness"] == ""


def test_a_pre_plan_trigger_round_trips_byte_identical_but_for_the_default(tmp_path):
    base = {
        "id": "clock:legacy",
        "name": "legacy",
        "kind": "clock",
        "spec": {"kind": "cron", "expr": "0 9 * * *"},
    }
    full = parse_trigger(base)[0].to_dict()
    assert full["origin_harness"] == ""
    pre_plan = {k: v for k, v in full.items() if k != "origin_harness"}
    revived = parse_trigger(pre_plan)[0].to_dict()
    assert revived == full
    assert revived["origin_harness"] == ""
