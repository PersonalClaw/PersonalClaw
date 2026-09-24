"""KL-14's host: the due rule, the snapshot, bounded batches, and the tick that drives it.

The three failures this host exists to prevent, each asserted rather than described:

* **N passes for N writes.** A bulk import that ran graph work inline did one pass per item,
  each superseded by the next. The watermark collapses that to one.
* **Starvation.** A rule that waits for the ingest queue to drain never fires on a pipeline
  that never drains. So dirt older than `max_staleness` runs anyway — and the age is measured
  from the FIRST unprocessed write, not the latest, because the latest is always recent
  exactly when there is most to do.
* **A swallowed write.** Clearing the watermark to "now" after a pass discards everything that
  landed while it ran, and nothing downstream could tell. `execute` clears only up to the
  snapshot it took BEFORE running.

Plus the two wiring facts that would each make the whole thing inert while looking wired: the
tick must call maintenance with `durability.auto_backup` OFF, and the in-flight probe must
actually be installed by the gateway rather than defaulting to "drained".
"""

from __future__ import annotations

import pytest

from personalclaw.knowledge import maintenance as m


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home — this writes the maintenance state file."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    m.clear_passes()
    m.set_in_flight_probe(None)
    yield tmp_path
    m.clear_passes()
    m.set_in_flight_probe(None)


def _counting_pass(counter: list, *, returns: int = 0):
    def _run(*, batch_size: int = 0) -> int:
        counter.append(batch_size)
        return returns

    return _run


# ── The due rule ──────────────────────────────────────────────────────────


def test_a_clean_index_is_not_due(home):
    """Vacuity for everything below: with no write there is nothing to do."""
    due, why = m.is_due(now=1000.0, in_flight=0)
    assert due is False and why == "clean"


def test_a_write_with_a_drained_queue_is_due(home):
    m.mark_dirty(now=1000.0)
    due, why = m.is_due(now=1001.0, in_flight=0)
    assert due is True and "drained" in why


def test_a_busy_queue_DEFERS_so_a_bulk_import_coalesces(home):
    """The coalescing half: while the import is still running, do not run a pass per item."""
    m.mark_dirty(now=1000.0)
    due, why = m.is_due(now=1001.0, in_flight=7, staleness=900.0)
    assert due is False and "7 in flight" in why


def test_a_busy_queue_CANNOT_starve_maintenance(home):
    """The anti-starvation half. Same busy queue, older dirt — it runs anyway."""
    m.mark_dirty(now=1000.0)
    due, why = m.is_due(now=1000.0 + 901.0, in_flight=7, staleness=900.0)
    assert due is True and "old" in why


def test_staleness_is_measured_from_the_FIRST_write_not_the_latest(home):
    """The reason there are two stamps, and the thing one stamp cannot express.

    A pipeline writing continuously always has a recent LATEST write. A staleness rule read
    off that stamp would defer forever — precisely when the backlog is largest. So the age
    comes from `dirty_since`.
    """
    m.mark_dirty(now=1000.0)  # the first write: the age anchor
    for t in range(1, 20):
        m.mark_dirty(now=1000.0 + t * 50.0)  # a steady stream, latest is always fresh
    state = m.load_state()
    assert state["dirty_since"] == 1000.0, "the anchor moved with the stream"
    assert state["dirty_ts"] > 1900.0, "the snapshot boundary did not advance"
    due, why = m.is_due(now=1000.0 + 901.0, in_flight=5, staleness=900.0)
    assert due is True, f"a continuously-busy pipeline starved maintenance: {why}"


# ── The snapshot ──────────────────────────────────────────────────────────


def test_a_write_landing_MID_RUN_is_not_swallowed(home):
    """The snapshot's whole purpose, driven through `execute`.

    The pass itself marks dirty — which is exactly what a write landing during the pass looks
    like. `execute` cleared only up to the snapshot it took first, so the state must still be
    dirty when it returns.
    """
    m.mark_dirty(now=1000.0)

    def _writes_while_running(*, batch_size: int = 0) -> int:
        m.mark_dirty(now=2000.0)  # a write arrives mid-pass
        return 0

    m.register_pass("writer", _writes_while_running)
    result = m.execute(now=1500.0)

    assert result.ran is True
    assert result.snapshot == 1000.0, f"the snapshot was not the pre-run watermark: {result}"
    assert m.is_dirty() is True, "the mid-run write was swallowed by the clear"


def test_a_run_with_no_concurrent_write_leaves_the_index_clean(home):
    """Vacuity for the test above: if `execute` never cleared anything, that test would pass
    for a host that simply never marks itself clean."""
    m.mark_dirty(now=1000.0)
    m.register_pass("noop", _counting_pass([]))
    m.execute(now=1500.0)
    assert m.is_dirty() is False, "a completed pass left the index dirty forever"


def test_the_clean_stamp_never_moves_backwards(home):
    """`clear_up_to` takes a max: an out-of-order clear must not un-clean the index."""
    m.mark_dirty(now=1000.0)
    m.clear_up_to(1000.0)
    m.clear_up_to(500.0)
    assert m.is_dirty() is False


# ── Bounded batches ───────────────────────────────────────────────────────


def test_a_batched_pass_is_reinvoked_until_it_reports_nothing_left(home):
    calls: list = []
    seq = [3, 2, 0]

    def _run(*, batch_size: int = 0) -> int:
        calls.append(batch_size)
        return seq[len(calls) - 1] if len(calls) <= len(seq) else 0

    m.mark_dirty(now=1000.0)
    m.register_pass("drain", _run)
    result = m.execute(batch_size=25)
    assert calls == [25, 25, 25], f"sub-batches were not bounded/looped as declared: {calls}"
    assert result.per_pass["drain"] == 5


def test_a_batched_pass_that_never_finishes_is_bounded(home):
    """A buggy job costs one tick, not the loop."""
    calls: list = []
    m.mark_dirty(now=1000.0)
    m.register_pass("greedy", _counting_pass(calls, returns=1))
    m.execute(max_batches=4)
    assert len(calls) == 4, f"an always-more pass ran {len(calls)} times, not the cap"


def test_a_SINGLE_SWEEP_pass_runs_exactly_once(home):
    """The distinction that stops a lint busy-looping.

    A whole-store sweep returns findings, not remaining work. Read as a backlog, a store with
    three standing findings would be re-linted once per allowed sub-batch, forever.
    """
    calls: list = []
    m.mark_dirty(now=1000.0)
    m.register_pass("lint", _counting_pass(calls, returns=3), batched=False)
    result = m.execute(max_batches=10)
    assert len(calls) == 1, f"a single-sweep pass ran {len(calls)} times"
    assert result.per_pass["lint"] == 3, "the sweep's report was dropped"


def test_one_failing_pass_does_not_cost_the_others_their_cadence(home):
    ran: list = []

    def _boom(*, batch_size: int = 0) -> int:
        raise RuntimeError("provider down")

    m.mark_dirty(now=1000.0)
    m.register_pass("a_boom", _boom)
    m.register_pass("b_fine", _counting_pass(ran))
    result = m.execute()
    assert "a_boom" in result.errors and "provider down" in result.errors["a_boom"]
    assert ran, "a failing pass stopped an independent one"


def test_registering_the_same_name_twice_does_not_double_the_pass(home):
    """ "ONE edge pass not N" has to survive a module being imported twice."""
    calls: list = []
    m.mark_dirty(now=1000.0)
    m.register_pass("dup", _counting_pass(calls))
    m.register_pass("dup", _counting_pass(calls))
    m.execute()
    assert len(calls) == 1, f"a re-registered pass ran {len(calls)} times"


# ── The in-flight probe ───────────────────────────────────────────────────


def test_an_absent_probe_is_LOUD_rather_than_silently_permissive(home, caplog):
    """The first version of this read a `knowledge.get_ingest_queue` that does not exist, so
    every lookup fell into an except and returned 0 — the coalescing clause would have been
    inert. An uninstalled probe and a drained queue are indistinguishable in the return value,
    so the difference is a log line."""
    import logging

    m.set_in_flight_probe(None)
    with caplog.at_level(logging.WARNING, logger="personalclaw.knowledge.maintenance"):
        assert m._in_flight_depth() == 0
    assert any(
        "no in-flight probe" in r.message for r in caplog.records
    ), f"an uninstalled probe was silent: {[r.message for r in caplog.records]}"


def test_an_installed_probe_is_consulted(home):
    m.set_in_flight_probe(lambda: 4)
    assert m._in_flight_depth() == 4
    assert m.has_in_flight_probe() is True


def test_the_gateway_installs_the_probe(home):
    """The wiring, asserted at the gateway rather than assumed from the line existing.

    Driven on a bare orchestrator instance: the installer must not need a running gateway,
    because it is called during startup before much else exists.
    """
    from personalclaw.gateway import GatewayOrchestrator

    gw = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gw.dashboard_state = None
    assert m.has_in_flight_probe() is False
    gw._install_graph_maintenance_probe()
    assert m.has_in_flight_probe() is True, "startup does not install the probe"
    assert m._in_flight_depth() == 0, "a stateless gateway should read as drained"


def test_the_probe_does_not_START_a_queue_to_measure_it(home):
    """`DashboardState.knowledge_ingest_queue()` CONSTRUCTS and starts a worker. Probing must
    read the private attribute instead — asking how busy the queue is must not create one."""
    from personalclaw.gateway import GatewayOrchestrator

    class _State:
        _knowledge_ingest_queue = None

        def knowledge_ingest_queue(self):  # pragma: no cover - must never be called
            raise AssertionError("the probe started a queue just to measure it")

    gw = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gw.dashboard_state = _State()
    gw._install_graph_maintenance_probe()
    assert m._in_flight_depth() == 0


# ── The tick ──────────────────────────────────────────────────────────────


def test_maintenance_runs_with_auto_backup_off(home, monkeypatch):
    """The coupling this atom must NOT create.

    The host rides the durability tick because that loop already exists. But that loop gates
    its jobs on `durability.auto_backup`, and a user turning off scheduled backups must not
    silently lose knowledge-graph maintenance — they mitigate unrelated failures. So the tick
    calls maintenance OUTSIDE that gate, and this is the proof.
    """
    import personalclaw.durability.service as ds

    monkeypatch.setattr(ds, "enabled", lambda: False)
    ran: list = []
    m.mark_dirty(now=1000.0)
    m.set_in_flight_probe(lambda: 0)
    m.register_pass("tick", _counting_pass(ran))

    ds._tick_graph_maintenance()

    assert ran, "maintenance did not run with durability.auto_backup off"


def test_the_tick_does_not_run_a_clean_index(home):
    """Vacuity for the tick: it consults due-ness rather than running unconditionally."""
    import personalclaw.durability.service as ds

    ran: list = []
    m.set_in_flight_probe(lambda: 0)
    m.register_pass("tick", _counting_pass(ran))
    ds._tick_graph_maintenance()
    assert ran == [], "the tick ran a pass over a clean index"


def test_the_tick_never_raises(home, monkeypatch):
    """A maintenance failure must not break the backup loop it rides."""
    import personalclaw.durability.service as ds

    monkeypatch.setattr(m, "run_maintenance", lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    ds._tick_graph_maintenance()  # must not raise


# ── Config round-trip ─────────────────────────────────────────────────────


def test_max_staleness_round_trips_through_config(home, monkeypatch):
    """The clause says it round-trips; this asserts all the way to the reader."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    assert cfg.knowledge.maintenance_max_staleness_secs == 900
    assert "maintenance_max_staleness_secs" in cfg.to_dict()["knowledge"]

    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    spec = _EDITABLE_CONFIG.get("knowledge.maintenance_max_staleness_secs")
    assert spec and spec["type"] == "int", "the field is not PATCH-writable"

    class _K:
        maintenance_max_staleness_secs = 120

    class _C:
        knowledge = _K()

    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: _C()))
    assert m.max_staleness_secs() == 120.0, "the host does not read the configured value"


def test_a_zero_staleness_cannot_disable_coalescing(home, monkeypatch):
    """The file is hand-editable, so the floor is enforced in the reader too — a 0 would make
    every tick "stale" and defeat the coalescing the watermark exists for."""
    from personalclaw.config.loader import AppConfig

    class _K:
        maintenance_max_staleness_secs = 0

    class _C:
        knowledge = _K()

    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: _C()))
    assert m.max_staleness_secs() == m.DEFAULT_MAX_STALENESS_SECS


# ── Registration ──────────────────────────────────────────────────────────


def test_the_standing_passes_register(home):
    from personalclaw.knowledge import maintenance_passes

    names = maintenance_passes.register_all()
    assert maintenance_passes.PASS_MEMORY_LINT in names
    assert maintenance_passes.PASS_CONSOLIDATION in names
    assert maintenance_passes.PASS_LINK_BACKFILL in names
    assert set(names) <= set(m.registered_passes())


def test_the_standing_passes_are_single_sweep_not_batched(home):
    """A lint returning "3 findings" must not be read as "3 units of remaining work"."""
    from personalclaw.knowledge import maintenance_passes

    maintenance_passes.register_all()
    for name in (maintenance_passes.PASS_MEMORY_LINT, maintenance_passes.PASS_CONSOLIDATION):
        assert m._PASSES[name].batched is False, f"{name} would busy-loop on its own report"


def test_the_linker_backfill_is_registered_as_RESUMABLE_not_a_sweep(home):
    """The third named job, and the one pass whose return value IS a backlog.

    Registered `batched=True` on purpose: it returns items processed and 0 when drained, so the
    host's sub-batch loop is what finishes the library. Marked `False` it would still work and
    still look wired while only ever draining ONE batch per tick — a library larger than a batch
    would never finish, and nothing in the result would say so.
    """
    from personalclaw.knowledge import maintenance_passes

    maintenance_passes.register_all()
    assert maintenance_passes.PASS_LINK_BACKFILL in m.registered_passes()
    assert m._PASSES[maintenance_passes.PASS_LINK_BACKFILL].batched is True


def test_the_registered_linker_pass_CALLS_the_real_backfill(home, monkeypatch):
    """The call site, not the registration.

    A registered name proves a dict entry; it does not prove the callable reaches the module
    that does the work. This drives `execute` and asserts `link_backfill.link_backfill_pass`
    was the thing invoked, with the host's batch size passed through.
    """
    from personalclaw.knowledge import link_backfill, maintenance_passes

    seen: list[int] = []
    monkeypatch.setattr(
        link_backfill,
        "link_backfill_pass",
        lambda *, batch_size: (seen.append(batch_size), 0)[1],
    )
    m.mark_dirty(now=1000.0)
    maintenance_passes.register_all()
    m.execute(batch_size=7)
    assert seen == [7], f"the registered pass did not reach link_backfill_pass: {seen}"


def test_the_linker_pass_DRAINS_across_sub_batches(home, monkeypatch):
    """What `batched=True` buys, asserted through the host rather than assumed.

    Three sub-batches then empty — the drain loop must keep claiming while the pass reports
    work, and stop when it reports none.
    """
    from personalclaw.knowledge import link_backfill, maintenance_passes

    returns = [5, 5, 2, 0]
    calls: list[int] = []

    def _fake(*, batch_size: int) -> int:
        calls.append(batch_size)
        return returns[len(calls) - 1] if len(calls) <= len(returns) else 0

    monkeypatch.setattr(link_backfill, "link_backfill_pass", _fake)
    m.mark_dirty(now=1000.0)
    maintenance_passes.register_all()
    result = m.execute(batch_size=5)
    assert len(calls) == 4, f"the backlog was not drained across sub-batches: {calls}"
    assert result.per_pass[maintenance_passes.PASS_LINK_BACKFILL] == 12
