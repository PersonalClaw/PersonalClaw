"""RET-3: the footprint probe, the pruner's first real caller, and bytes actually leaving the disk.

Three failures, each asserted rather than described, and each of the three would look wired while
being inert:

* **A size row with no rate.** "12 GB" is not a fact anyone can act on. So a report derived from
  ONE sample must report no rate at all rather than a fabricated zero — a fabricated zero is
  indistinguishable from a store that genuinely stopped growing, and that is the reading a
  footprint report exists to give. `test_a_single_sample_refuses_to_report_a_rate` is the vacuity
  floor under every growth assertion below.

* **A pruner nobody calls.** Before this atom `watchdog.prune_runs` had exactly zero non-test
  callers (`git grep -n prune_runs` on origin/main found one *comment* in `workflows/service.py`
  and six test lines) and `workflows.retention_per_def` had zero readers — declared, clamped,
  loaded and PATCH-writable, consumed by nothing. So the test here drives
  `DurabilityService._loop`, the real scheduler entry, and asserts the pruner is reached FROM it.
  Calling `_tick_footprint_maintenance` directly would pass on a helper nobody arms, which is the
  same bug in a new place.

* **A delete that frees no disk.** SQLite marks deleted pages free and reuses them later; the file
  never shrinks on its own, so a pruner without a reclaim moves zero bytes. Every assertion about
  reclaim is therefore an `st_size` comparison across a real maintenance run, never a row count.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3

import pytest

from personalclaw.durability import footprint


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. Everything here writes state and VACUUMs databases, so a test that
    reached the real `~/.personalclaw` would compact a live install."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _seed_bloated_db(path, *, rows: int = 2000, blob: int = 4096) -> int:
    """Write a database, then delete every row — leaving a big file of free pages.

    This is the shape retention produces: the rows are gone and the bytes are not. Returns the
    on-disk size, which is what any honest reclaim assertion has to move.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS bloat(id INTEGER PRIMARY KEY, payload BLOB)")
        conn.executemany(
            "INSERT INTO bloat(payload) VALUES(?)", [(os.urandom(blob),) for _ in range(rows)]
        )
        conn.commit()
        conn.execute("DELETE FROM bloat")
        conn.commit()
    finally:
        conn.close()
    return path.stat().st_size


# ── Clause 1: per-store bytes on disk ───────────────────────────────────────


def test_measure_reports_bytes_per_declared_store(home):
    (home / "memory.db").write_bytes(b"x" * 5000)
    (home / "workspace" / "knowledge").mkdir(parents=True)
    (home / "workspace" / "knowledge" / "knowledge.db").write_bytes(b"y" * 9000)

    rows = {r.id: r for r in footprint.measure(home)}
    assert rows["memory_db"].bytes == 5000
    assert rows["knowledge_db"].bytes == 9000
    assert rows["memory_db"].path == "memory.db", "the row must name the store on disk"
    assert rows["knowledge_db"].domain == "knowledge"


def test_an_absent_store_is_reported_as_absent_not_omitted(home):
    """A report that silently drops what is not there cannot be told apart from one that missed
    it, and "this store does not exist yet" is a real answer."""
    rows = {r.id: r for r in footprint.measure(home)}
    assert rows["memory_db"].present is False
    assert rows["memory_db"].bytes == 0


def test_a_sqlite_wal_counts_toward_its_stores_footprint(home):
    """A 200 MB `-wal` is 200 MB of disk. Ignoring it under-reports exactly the store that is
    growing, which is the one the report exists to find."""
    (home / "memory.db").write_bytes(b"x" * 1000)
    (home / "memory.db-wal").write_bytes(b"w" * 7000)
    (home / "memory.db-shm").write_bytes(b"s" * 32)

    rows = {r.id: r for r in footprint.measure(home)}
    assert rows["memory_db"].bytes == 8032


def test_a_tree_store_is_summed_recursively(home):
    nested = home / "workspace" / "knowledge" / "files" / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "doc.pdf").write_bytes(b"p" * 4096)
    (home / "workspace" / "knowledge" / "files" / "top.txt").write_bytes(b"t" * 100)

    rows = {r.id: r for r in footprint.measure(home)}
    assert rows["knowledge_files"].bytes == 4196


def test_total_is_the_sum_of_the_stores(home):
    (home / "memory.db").write_bytes(b"x" * 1234)
    (home / "workspace" / "knowledge").mkdir(parents=True)
    (home / "workspace" / "knowledge" / "knowledge.db").write_bytes(b"y" * 4321)
    rows = footprint.measure(home)
    assert footprint.total_bytes(rows) == sum(r.bytes for r in rows) == 5555


def test_a_nested_store_is_not_billed_to_its_container_too(home):
    """Five manifest entries live INSIDE another: `workspace/` declares `knowledge.db`,
    `knowledge/files` and `lexicon.db`; `workflows/` declares `runs.db`; `loop/` declares
    `loops.db`. Counting both rows over-reports the total AND makes the container row absorb its
    child's growth, which points a "which store is growing?" report at the wrong store.

    This is the assertion that caught it: the naive sum read 9876 for 5555 bytes of files.
    """
    (home / "workspace" / "knowledge").mkdir(parents=True)
    (home / "workspace" / "knowledge" / "knowledge.db").write_bytes(b"y" * 4321)
    (home / "workspace" / "loose.txt").write_bytes(b"z" * 11)

    rows = {r.id: r for r in footprint.measure(home)}
    assert rows["knowledge_db"].bytes == 4321
    assert rows["workspace"].bytes == 11, "the container absorbed its child's bytes"
    assert footprint.total_bytes(list(rows.values())) == 4332


def test_a_nested_databases_sidecars_are_not_billed_to_the_container(home):
    """The `-wal` belongs to the row that owns the database, not to the tree it sits in."""
    (home / "workspace" / "knowledge").mkdir(parents=True)
    (home / "workspace" / "knowledge" / "knowledge.db").write_bytes(b"y" * 100)
    (home / "workspace" / "knowledge" / "knowledge.db-wal").write_bytes(b"w" * 900)

    rows = {r.id: r for r in footprint.measure(home)}
    assert rows["knowledge_db"].bytes == 1000
    assert rows["workspace"].bytes == 0


def test_every_manifest_entry_is_billed_to_exactly_one_row(home):
    """The rail behind the two tests above, over the WHOLE manifest rather than the three
    overlaps that exist today: a new nested entry must not silently reintroduce double counting.
    """
    from personalclaw.durability import inventory

    for entry in inventory.all_entries():
        for child in footprint.nested_entries(entry):
            assert child.path != entry.path
            assert (child.path + "/").startswith(entry.path.rstrip("/") + "/")

    # And the containment relation is what the exclusion is built from, so a container with
    # children must declare a directory kind — a FILE cannot contain another entry.
    for entry in inventory.all_entries():
        if footprint.nested_entries(entry):
            assert entry.kind in (
                inventory.KIND_TREE,
                inventory.KIND_JSON_ENTITY_DIR,
            ), f"{entry.id} declares nested entries but is a {entry.kind}"


# ── Clause 1: a growth rate from TWO samples ────────────────────────────────


def test_a_single_sample_refuses_to_report_a_rate(home):
    """The vacuity floor for the whole clause: a single-sample size row must FAIL to produce a
    rate, because 0 B/day from one reading is a fabricated fact."""
    (home / "memory.db").write_bytes(b"x" * 1000)
    footprint.record(home, now=1_000_000.0)

    assert len(footprint.load_samples(home)) == 1
    assert footprint.growth(footprint.load_samples(home)) is None


def test_two_samples_at_the_same_instant_also_refuse(home):
    """A rate needs elapsed time, not just two rows. Two readings one microsecond apart would
    divide by ~zero and print a preposterous number."""
    (home / "memory.db").write_bytes(b"x" * 1000)
    footprint.record(home, now=1_000_000.0)
    (home / "memory.db").write_bytes(b"x" * 2000)
    footprint.record(home, now=1_000_000.0)

    assert footprint.growth(footprint.load_samples(home)) is None


def test_two_samples_at_different_times_produce_a_growth_rate(home):
    """The clause: bytes on disk PLUS a rate derived from two samples taken at different times."""
    (home / "memory.db").write_bytes(b"x" * 1_000_000)
    footprint.record(home, now=1_000_000.0)
    (home / "memory.db").write_bytes(b"x" * 3_000_000)
    footprint.record(home, now=1_000_000.0 + 43200.0)  # +12h

    rate = footprint.growth(footprint.load_samples(home))
    assert rate is not None
    assert rate.total_delta == 2_000_000
    # +2 MB over half a day is +4 MB/day.
    assert rate.bytes_per_day == pytest.approx(4_000_000.0)
    assert rate.span_secs == pytest.approx(43200.0)


def test_the_growth_rate_names_the_store_that_grew(home):
    """ "Growing 400 MB/day" is only actionable with "...and it is workflows/runs.db"."""
    (home / "memory.db").write_bytes(b"x" * 1000)
    (home / "workspace" / "knowledge").mkdir(parents=True)
    (home / "workspace" / "knowledge" / "knowledge.db").write_bytes(b"y" * 1000)
    footprint.record(home, now=0.0)
    (home / "workspace" / "knowledge" / "knowledge.db").write_bytes(b"y" * 87400)
    footprint.record(home, now=86400.0)

    rate = footprint.growth(footprint.load_samples(home))
    assert rate is not None
    assert rate.per_store["knowledge_db"] == pytest.approx(86400.0)
    assert "memory_db" not in rate.per_store, "a store that did not move must not be listed"


def test_a_shrinking_store_reports_a_NEGATIVE_rate(home):
    """Reclaim exists, so the rate has to be able to go down. A magnitude-only rate would report
    a successful reclaim as growth."""
    (home / "memory.db").write_bytes(b"x" * 100_000)
    footprint.record(home, now=0.0)
    (home / "memory.db").write_bytes(b"x" * 10_000)
    footprint.record(home, now=86400.0)

    rate = footprint.growth(footprint.load_samples(home))
    assert rate is not None and rate.bytes_per_day == pytest.approx(-90_000.0)


def test_the_series_is_bounded(home):
    (home / "memory.db").write_bytes(b"x" * 10)
    for i in range(footprint.SAMPLE_KEEP + 10):
        footprint.record(home, now=float(i))
    assert len(footprint.load_samples(home)) == footprint.SAMPLE_KEEP


def test_a_corrupt_series_reads_as_empty_rather_than_wedging(home):
    footprint.state_path(home).write_text("{not json", encoding="utf-8")
    assert footprint.load_samples(home) == []
    footprint.record(home, now=1.0)  # must not raise
    assert len(footprint.load_samples(home)) == 1


# ── Clause 1: the command a user actually runs ───────────────────────────────


class _Args:
    def __init__(self, **kw):
        self.json = False
        self.reclaim = False
        for key, value in kw.items():
            setattr(self, key, value)


def test_the_command_prints_per_store_bytes_and_then_a_rate(home, capsys):
    """End to end through the CLI entry point, twice: the first run has one sample and says so,
    the second reports a real rate. This is clause 1's surface, not an internal."""
    (home / "memory.db").write_bytes(b"x" * 2_000_000)

    assert footprint.footprint_cmd(_Args()) == 0
    first = capsys.readouterr().out
    assert "memory.db" in first
    assert "1.9 MB" in first
    assert "not yet measurable" in first, "one sample must not print a rate"

    (home / "memory.db").write_bytes(b"x" * 4_000_000)
    assert footprint.footprint_cmd(_Args()) == 0
    second = capsys.readouterr().out
    assert "/day" in second, "the second run must report a growth rate"
    assert "memory.db" in second


def test_the_command_emits_the_same_answer_as_json(home, capsys):
    import json

    (home / "memory.db").write_bytes(b"x" * 1000)
    footprint.footprint_cmd(_Args(json=True))
    capsys.readouterr()
    (home / "memory.db").write_bytes(b"x" * 3000)
    footprint.footprint_cmd(_Args(json=True))
    data = json.loads(capsys.readouterr().out)

    assert data["total_bytes"] >= 3000
    assert data["growth"] is not None and data["growth"]["total_delta"] == 2000
    ids = {row["id"]: row for row in data["stores"]}
    assert ids["memory_db"]["bytes"] == 3000


def test_the_command_is_registered_on_the_cli(home):
    """A probe nobody can invoke is the inert case one level up. Parse the real argv."""
    from personalclaw.cli import build_parser

    args = build_parser().parse_args(["footprint", "--json"])
    assert args.command == "footprint" and args.json is True


# ── Clause 2: the pruner's caller is the SCHEDULED path ─────────────────────


async def _one_scheduled_tick(monkeypatch, *, stop_on) -> None:
    """Drive ONE iteration of the real `DurabilityService._loop`.

    The loop is the scheduler entry — `start()` only wraps it in a task — so this exercises the
    same body the gateway arms. `stop_on` is invoked from inside the tick to set the shutdown
    event, which makes the `while` check at the top of iteration two end the loop.
    """
    import personalclaw.durability.service as ds
    from personalclaw import shutdown_event

    # `run_due_jobs` takes snapshots and tars the home; the point here is the tick ABOVE that
    # gate, and turning the gate off is also what proves the decoupling.
    monkeypatch.setattr(ds, "enabled", lambda: False)
    svc = ds.DurabilityService(tick_secs=0.01)
    try:
        await asyncio.wait_for(svc._loop(), timeout=60)
    finally:
        shutdown_event.clear()
    assert stop_on(), "the loop never reached the footprint tick"


@pytest.mark.asyncio
async def test_the_scheduled_loop_invokes_the_run_retention_pruner(home, monkeypatch):
    """Clause 2. NOT "prune_runs works when called directly" — that passed on main with zero
    callers. This asserts the real loop reaches it."""
    import personalclaw.workflows.store as wf_store
    import personalclaw.workflows.watchdog as watchdog
    from personalclaw import shutdown_event

    calls: list[tuple[str, int]] = []

    async def _spy(workflow_name, *, keep=100):
        calls.append((workflow_name, keep))
        shutdown_event.set()
        return 0

    monkeypatch.setattr(watchdog, "prune_runs", _spy)
    monkeypatch.setattr(wf_store, "def_names", lambda: ["nightly-digest"])

    await _one_scheduled_tick(monkeypatch, stop_on=lambda: bool(calls))

    assert calls == [("nightly-digest", 100)], "the scheduled tick did not reach the pruner"


@pytest.mark.asyncio
async def test_the_scheduled_tick_passes_retention_per_def_to_the_pruner(home, monkeypatch):
    """`workflows.retention_per_def` had zero readers before this atom. A knob wired to nothing
    and a pruner called by nothing are one bug from two ends, so both halves are asserted."""
    import json

    import personalclaw.workflows.store as wf_store
    import personalclaw.workflows.watchdog as watchdog
    from personalclaw import shutdown_event
    from personalclaw.config.loader import AppConfig

    (home / "config.json").write_text(
        json.dumps({"workflows": {"retention_per_def": 7}}), encoding="utf-8"
    )
    assert AppConfig.load().workflows.retention_per_def == 7, "the fixture did not take"

    seen: list[int] = []

    async def _spy(workflow_name, *, keep=100):
        seen.append(keep)
        shutdown_event.set()
        return 0

    monkeypatch.setattr(watchdog, "prune_runs", _spy)
    monkeypatch.setattr(wf_store, "def_names", lambda: ["d"])

    await _one_scheduled_tick(monkeypatch, stop_on=lambda: bool(seen))

    assert seen == [7], f"the tick ignored workflows.retention_per_def (saw {seen})"


@pytest.mark.asyncio
async def test_every_def_with_runs_is_pruned_not_just_the_first(home, monkeypatch):
    """Retention is per def, so a sweep that stopped at one def would leave every other def
    unbounded — the exact shape of the bug this atom is about."""
    import personalclaw.workflows.store as wf_store
    import personalclaw.workflows.watchdog as watchdog
    from personalclaw import shutdown_event

    names: list[str] = []

    async def _spy(workflow_name, *, keep=100):
        names.append(workflow_name)
        if len(names) == 3:
            shutdown_event.set()
        return 0

    monkeypatch.setattr(watchdog, "prune_runs", _spy)
    monkeypatch.setattr(wf_store, "def_names", lambda: ["a", "b", "c"])

    await _one_scheduled_tick(monkeypatch, stop_on=lambda: len(names) == 3)

    assert names == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_a_pruner_that_raises_does_not_break_the_backup_loop(home, monkeypatch):
    """The tick rides the durability loop. A def that will not prune must cost a cadence, never
    the loop that also takes the snapshots."""
    import personalclaw.durability.service as ds
    import personalclaw.workflows.store as wf_store
    import personalclaw.workflows.watchdog as watchdog
    from personalclaw import shutdown_event

    async def _boom(workflow_name, *, keep=100):
        shutdown_event.set()
        raise RuntimeError("locked")

    monkeypatch.setattr(watchdog, "prune_runs", _boom)
    monkeypatch.setattr(wf_store, "def_names", lambda: ["d"])
    monkeypatch.setattr(ds, "enabled", lambda: False)

    svc = ds.DurabilityService(tick_secs=0.01)
    try:
        await asyncio.wait_for(svc._loop(), timeout=60)  # must not raise
    finally:
        shutdown_event.clear()


def test_the_retention_sweep_does_not_create_the_store_it_reads(home):
    """The sweep runs on every tick. `store._connect` creates the directory AND the full schema,
    so an unguarded "which defs have runs?" would mint an empty `runs.db` on a gateway that has
    never run a workflow — a probe creating the thing it probes."""
    from personalclaw.workflows import store as wf_store

    assert wf_store.def_names() == []
    assert not (home / "workflows" / "runs.db").exists(), "the probe created the store"


def test_the_pruner_is_reached_from_a_non_test_module():
    """A grep-shaped rail, because "zero non-test callers" is the state this atom found and the
    state it must never return to. The comment in `workflows/service.py` is not a caller.

    Parsed rather than grepped: a substring search would be satisfied by the docstring above the
    call, which is exactly the false positive that let the inert state survive this long.
    """
    import ast
    import pathlib

    import personalclaw.durability.service as ds

    src = pathlib.Path(ds.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "personalclaw.workflows.watchdog"
        and any(alias.name == "prune_runs" for alias in node.names)
    ]
    assert imported, "durability/service.py no longer imports the run-retention pruner"
    called = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "prune_runs"
    ]
    assert called, "the pruner is imported but never called — inert again"


# ── Clause 3: bytes actually leave the disk ─────────────────────────────────


def test_reclaim_shrinks_a_store_whose_rows_were_deleted(home):
    """Deleting rows does not shrink a SQLite file. This is the measurement that proves the
    reclaim half exists at all: st_size before vs after, never a row count."""
    db = home / "memory.db"
    before = _seed_bloated_db(db)
    assert before > 1_000_000, "the fixture did not produce an oversized store"

    result = footprint.reclaim(home)

    after = db.stat().st_size
    assert after < before, f"reclaim moved no bytes ({before} → {after})"
    assert result.freed_bytes > 0
    assert result.per_store["memory_db"] == before - after


def test_reclaim_leaves_an_already_compact_store_alone(home):
    """Vacuity for the clause above: a store with no free pages must not be reported as freed,
    or "freed N bytes" would be true of every run and mean nothing."""
    db = home / "memory.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t(a TEXT)")
    conn.execute("INSERT INTO t VALUES('x')")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(str(db))
    conn.isolation_level = None
    conn.execute("VACUUM")
    conn.close()

    result = footprint.reclaim(home)
    assert result.per_store.get("memory_db", 0) == 0


def test_reclaim_only_touches_manifest_declared_databases(home):
    """A `*.db` glob would compact whatever a user dropped in their home. Not this job's
    business, and VACUUMing a stranger's file is a way to corrupt one."""
    stray = home / "someone-elses.db"
    _seed_bloated_db(stray, rows=400)
    before = stray.stat().st_size

    footprint.reclaim(home)

    assert stray.stat().st_size == before


def test_a_locked_store_is_skipped_not_raised(home, monkeypatch):
    """One busy database must not cost every other store its reclaim."""
    _seed_bloated_db(home / "memory.db", rows=400)
    (home / "workspace" / "knowledge").mkdir(parents=True)
    _seed_bloated_db(home / "workspace" / "knowledge" / "knowledge.db", rows=400)

    real = footprint.reclaim_store

    def _flaky(path):
        if path.name == "memory.db":
            raise sqlite3.OperationalError("database is locked")
        return real(path)

    monkeypatch.setattr(footprint, "reclaim_store", _flaky)
    result = footprint.reclaim(home)

    assert "memory_db" in result.skipped
    assert result.per_store.get("knowledge_db", 0) > 0, "one locked store stopped the whole pass"


@pytest.mark.asyncio
async def test_the_scheduled_maintenance_job_shrinks_an_oversized_store(home, monkeypatch):
    """CLAUSE 3, through the real scheduler entry.

    An oversized store is seeded, one iteration of `DurabilityService._loop` runs, and the file
    is smaller afterwards. This is the assertion that must fail on today's main: `git grep -iE
    "VACUUM|PRAGMA optimize" -- src/` returned zero hits there, so no scheduled path could move
    a byte no matter how many rows retention deleted.
    """
    import personalclaw.workflows.store as wf_store
    import personalclaw.workflows.watchdog as watchdog
    from personalclaw import shutdown_event

    db = home / "memory.db"
    before = _seed_bloated_db(db)

    async def _spy(workflow_name, *, keep=100):
        return 0

    monkeypatch.setattr(watchdog, "prune_runs", _spy)
    monkeypatch.setattr(wf_store, "def_names", lambda: [])

    import personalclaw.durability.service as ds

    real_reclaim = footprint.reclaim

    def _reclaim_then_stop(h):
        try:
            return real_reclaim(h)
        finally:
            shutdown_event.set()

    monkeypatch.setattr(footprint, "reclaim", _reclaim_then_stop)
    monkeypatch.setattr(ds, "enabled", lambda: False)

    svc = ds.DurabilityService(tick_secs=0.01)
    try:
        await asyncio.wait_for(svc._loop(), timeout=120)
    finally:
        shutdown_event.clear()

    after = db.stat().st_size
    assert after < before, f"the scheduled maintenance job freed no disk ({before} → {after})"
    # And the sample the tick recorded reflects the POST-reclaim size, so the trend line measures
    # the steady state rather than the pre-compaction peak.
    samples = footprint.load_samples(home)
    assert samples, "the tick recorded no footprint sample"
    assert samples[-1].stores.get("memory_db", 0) == after


@pytest.mark.asyncio
async def test_the_reclaim_half_is_rate_limited(home, monkeypatch):
    """VACUUM rewrites every store. Doing that on the 5-minute tick would be a permanent disk
    load, so the reclaim half is due-gated even though the prune half is not."""
    import time

    import personalclaw.durability.service as ds
    import personalclaw.workflows.store as wf_store
    import personalclaw.workflows.watchdog as watchdog
    from personalclaw import shutdown_event

    footprint.stamp_reclaim(home, now=time.time())
    assert footprint.reclaim_due(home) is False

    ran: list[int] = []

    async def _spy(workflow_name, *, keep=100):
        shutdown_event.set()
        return 0

    monkeypatch.setattr(watchdog, "prune_runs", _spy)
    monkeypatch.setattr(wf_store, "def_names", lambda: ["d"])
    monkeypatch.setattr(footprint, "reclaim", lambda h: ran.append(1))
    monkeypatch.setattr(ds, "enabled", lambda: False)

    svc = ds.DurabilityService(tick_secs=0.01)
    try:
        await asyncio.wait_for(svc._loop(), timeout=60)
    finally:
        shutdown_event.clear()

    assert ran == [], "the tick reclaimed inside its own rate limit"


def test_the_command_reclaim_flag_frees_measured_bytes(home, capsys):
    """The user-facing half of clause 3: `personalclaw footprint --reclaim` reports the bytes it
    actually returned, not the bytes it hoped to."""
    db = home / "memory.db"
    before = _seed_bloated_db(db)

    assert footprint.footprint_cmd(_Args(reclaim=True)) == 0
    out = capsys.readouterr().out

    assert db.stat().st_size < before
    assert "Reclaimed" in out
