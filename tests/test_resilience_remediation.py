"""Health-scored remediation engine tests (PLATFORM-RESILIENCE §4).

Pins the deficit→score math (reachable ceilings, unreachable-deficit exclusion), the
dependency-ordered plan, the three stop conditions (target/cost/exhausted), the
cooldown storm-guard, and the ledger.
"""

from __future__ import annotations

import pytest

from personalclaw.resilience import remediation as rem
from personalclaw.resilience.remediation import Deficit, RemediationJob


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolate the doctor/ ledger + jobs.json under tmp, and snapshot/restore the job
    registry so test jobs don't leak."""
    monkeypatch.setattr("personalclaw.resilience.remediation.config_dir", lambda: tmp_path)
    saved = dict(rem._JOBS)
    yield
    rem._JOBS.clear()
    rem._JOBS.update(saved)


# ── deficit → score ───────────────────────────────────────────────────────────


def test_penalty_is_capped_at_max_penalty():
    d = Deficit(key="k", count=1000, weight=1.0, max_penalty=10.0)
    assert d.penalty == 10.0  # capped, not 1000


def test_health_score_subtracts_reachable_penalties():
    ds = [
        Deficit(key="a", count=5, weight=1.0, max_penalty=20.0),  # penalty 5
        Deficit(key="b", count=10, weight=2.0, max_penalty=10.0),  # penalty 10 (capped)
    ]
    assert rem.health_score(ds) == 85.0  # 100 - 5 - 10


def test_unreachable_deficit_excluded_from_score():
    ds = [
        Deficit(key="a", count=10, weight=1.0, max_penalty=20.0, reachable=False),  # ignored
        Deficit(key="b", count=3, weight=1.0, max_penalty=20.0),  # penalty 3
    ]
    assert rem.health_score(ds) == 97.0  # only b counts (unfixable → not held against us)


def test_health_score_clamped():
    ds = [Deficit(key="a", count=999, weight=1.0, max_penalty=200.0)]
    assert rem.health_score(ds) == 0.0  # never negative


# ── dependency ordering ───────────────────────────────────────────────────────


def test_ordered_respects_after_edges():
    a = RemediationJob(id="a", title="a", run=lambda: "a", after=("b",))
    b = RemediationJob(id="b", title="b", run=lambda: "b")
    ordered = rem._ordered([a, b])
    assert [j.id for j in ordered].index("b") < [j.id for j in ordered].index("a")


def test_ordered_tolerates_cycle():
    a = RemediationJob(id="a", title="a", run=lambda: "a", after=("b",))
    b = RemediationJob(id="b", title="b", run=lambda: "b", after=("a",))
    ordered = rem._ordered([a, b])  # must not hang/raise
    assert {j.id for j in ordered} == {"a", "b"}


# ── run: stop conditions + execution ──────────────────────────────────────────


def _stub_deficits(monkeypatch, deficits):
    monkeypatch.setattr(rem, "measure_deficits", lambda: deficits)


def test_run_stops_when_already_healthy(monkeypatch):
    _stub_deficits(monkeypatch, [Deficit(key="a", count=0, weight=1.0, max_penalty=10.0)])
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert result.stopped_reason == "target_score already met"
    assert result.jobs == []


def test_run_executes_job_and_improves_score(monkeypatch):
    ran = {"n": 0}

    def _job():
        ran["n"] += 1
        return "fixed"

    rem.register_job(RemediationJob(id="fix.a", title="Fix A", run=_job, fixes_deficit="a"))
    # First measure: deficit present (score 80); after the job runs, healthy.
    calls = {"n": 0}

    def _measure():
        calls["n"] += 1
        if calls["n"] == 1:
            return [Deficit(key="a", count=20, weight=1.0, max_penalty=20.0, job_id="fix.a")]
        return [Deficit(key="a", count=0, weight=1.0, max_penalty=20.0, job_id="fix.a")]

    monkeypatch.setattr(rem, "measure_deficits", _measure)
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert ran["n"] == 1
    assert result.score_before == 80.0 and result.score_after == 100.0
    assert result.stopped_reason == "target_score reached"
    assert result.jobs[0]["status"] == "ok"


def test_run_skips_unreachable_deficit_job(monkeypatch):
    ran = {"n": 0}
    rem.register_job(
        RemediationJob(
            id="fix.b", title="Fix B", run=lambda: ran.__setitem__("n", 1) or "x", fixes_deficit="b"
        )
    )
    # deficit present but UNREACHABLE → job not a candidate, score unaffected by it.
    _stub_deficits(
        monkeypatch,
        [Deficit(key="b", count=50, weight=1.0, max_penalty=20.0, reachable=False, job_id="fix.b")],
    )
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert ran["n"] == 0  # never ran — unfixable now
    # Unreachable deficit doesn't count → already at target.
    assert result.stopped_reason == "target_score already met"


def test_run_respects_cooldown(monkeypatch):
    ran = {"n": 0}
    rem.register_job(
        RemediationJob(
            id="fix.c",
            title="Fix C",
            run=lambda: ran.__setitem__("n", ran["n"] + 1) or "x",
            fixes_deficit="c",
            cooldown_hours=24.0,
        )
    )
    _stub_deficits(
        monkeypatch,
        [Deficit(key="c", count=20, weight=1.0, max_penalty=20.0, job_id="fix.c")],
    )
    # First run executes it.
    rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert ran["n"] == 1
    # A run 1 hour later → within the 24h cooldown → skipped.
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0 + 3600)
    assert ran["n"] == 1  # not re-run
    assert any(j["status"] == "skipped_cooldown" for j in result.jobs)


def test_dry_run_does_not_execute_or_change_score(monkeypatch):
    ran = {"n": 0}
    rem.register_job(
        RemediationJob(
            id="fix.d", title="Fix D", run=lambda: ran.__setitem__("n", 1) or "x", fixes_deficit="d"
        )
    )
    _stub_deficits(
        monkeypatch,
        [Deficit(key="d", count=20, weight=1.0, max_penalty=20.0, job_id="fix.d")],
    )
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0, dry_run=True)
    assert ran["n"] == 0  # dry-run never executes
    assert result.score_after == result.score_before
    assert all(j["status"] == "would_run" for j in result.jobs)


# ── ledger ────────────────────────────────────────────────────────────────────


def test_ledger_written_and_read_back(monkeypatch):
    rem.register_job(
        RemediationJob(id="fix.e", title="Fix E", run=lambda: "done", fixes_deficit="e")
    )
    calls = {"n": 0}

    def _measure():
        calls["n"] += 1
        return [
            Deficit(
                key="e",
                count=(20 if calls["n"] == 1 else 0),
                weight=1.0,
                max_penalty=20.0,
                job_id="fix.e",
            )
        ]

    monkeypatch.setattr(rem, "measure_deficits", _measure)
    rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1234.0)
    runs = rem.recent_runs()
    assert len(runs) == 1
    assert runs[0]["ts"] == 1234.0
    assert runs[0]["stopped_reason"] in ("target_score reached", "plan exhausted")


def test_builtin_deterministic_jobs_registered():
    ids = {j.id for j in rem.all_jobs()}
    assert {"serving-fs.prune-orphans", "skills.age", "knowledge.reindex-embeddings"} <= ids
    # all built-ins are the deterministic ($0) lane
    for j in rem.all_jobs():
        if j.id in ("serving-fs.prune-orphans", "skills.age", "knowledge.reindex-embeddings"):
            assert j.lane == "deterministic"


# ── absorbed heartbeat maintenance (§4.4, PR2-11) ─────────────────────────────


_ABSORBED = {
    "memory.rebuild-fts": "memory_fts_desync",
    "memory.prune-history": "history_over_retention",
    "sel.prune": "sel_prunable_entries",
    "skills.age": "skill_aging_due",
}


def test_absorbed_maintenance_jobs_registered():
    """Every maintenance pass retired from the heartbeat has a registered engine job in
    the deterministic ($0) lane.

    Load-bearing since PR2-8 deleted `_legacy_maintenance`: the heartbeat no longer keeps a
    duplicate copy of these passes, so if one disappears from this registry there is nothing
    left running it and the loss is silent (an absent prune is invisible by nature)."""
    jobs = {j.id: j for j in rem.all_jobs()}
    for job_id, deficit_key in _ABSORBED.items():
        assert job_id in jobs, f"{job_id} is not registered"
        assert jobs[job_id].lane == "deterministic"
        assert jobs[job_id].fixes_deficit == deficit_key


def test_every_job_bearing_deficit_can_be_scheduled_alone(tmp_path, monkeypatch):
    """🔴 The rail that catches a registered-but-unschedulable job.

    ``run_remediation`` bails out while ``score_before >= target_score``, so a deficit whose
    whole penalty fits inside ``100 − target`` can NEVER schedule its job — at any backlog.
    Two shipped deficits sat exactly on that line (`orphan_locks`, `skill_aging_due`, both
    ``max_penalty=10.0`` against the default target 90: 1000 stale skills still scored 90.0
    and stopped with "target_score already met"). Raise the ceiling for a new deficit; never
    lower this floor."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    by_key = {d.key: d for d in rem.measure_deficits()}
    job_deficits = {j.fixes_deficit for j in rem.all_jobs() if j.fixes_deficit}
    # NON-VACUITY: measure the whole set, don't skip what this env can't see. Every
    # job-bearing deficit is observable in a bare home (each ``measure_deficits`` branch
    # swallows its own exception, so an unmeasurable one vanishes silently — and a rail
    # that silently checks nothing reads exactly like a passing one).
    assert job_deficits <= set(by_key), (
        f"deficit(s) {sorted(job_deficits - set(by_key))} are declared by a job but were not "
        f"measured at all — their measure branch is swallowing an exception"
    )
    for key in sorted(job_deficits):
        d = by_key[key]
        assert d.max_penalty > rem._MIN_SCHEDULABLE_PENALTY, (
            f"deficit {key!r} caps at {d.max_penalty} penalty points, which never drops the "
            f"score below the default target — its job can never be scheduled by it alone"
        )


def test_skills_tampered_deficit_is_a_detector_not_a_job(tmp_path, monkeypatch):
    """`verify_skill_integrity` is finally SCHEDULED — as a measured deficit on every engine
    pass and Doctor read — but deliberately job-less and unreachable: no job can un-tamper a
    skill, and re-baselining a mutated one would launder the tamper. So it must never burn
    budget nor depress a score the engine cannot improve."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    tampered = [d for d in rem.measure_deficits() if d.key == "skills_tampered"]
    assert tampered, "skill-integrity is not measured — verify_skill_integrity is unscheduled"
    d = tampered[0]
    assert d.reachable is False and d.job_id == ""
    assert rem.health_score([d]) == 100.0  # unreachable → excluded from the score
    assert not [j for j in rem.all_jobs() if j.fixes_deficit == "skills_tampered"]


def test_history_prune_job_deletes_expired_files_and_their_index_rows(tmp_path, monkeypatch):
    """The job does the WORK: expired daily-history files are gone, and so are the FTS rows
    that would otherwise keep returning snippets for deleted files."""
    from datetime import datetime, timedelta

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.memory import MemoryStore, workspace_dir

    # No explicit workspace: the JOB builds MemoryStore() from config_dir(), and passing one
    # here would point the test at a different memory_index.db than the job writes.
    mem = MemoryStore()
    mem.init()
    hist = workspace_dir() / "memory" / "history"
    old = hist / f"{datetime.now().date() - timedelta(days=400)}.md"
    fresh = hist / f"{datetime.now().date()}.md"
    for p in (old, fresh):
        p.write_text("# day\n\n#### 09:00\nstuff\n", encoding="utf-8")
        mem._index_file(p, p.read_text(encoding="utf-8"))

    assert mem.count_history_over_retention(365) == 1
    detail = rem._job_prune_history()

    assert "1 history file" in detail
    assert not old.exists() and fresh.exists()
    assert mem.count_history_over_retention(365) == 0
    # the deleted file left no orphan search row behind
    assert str(old) not in dict(_indexed_rows(mem))
    assert str(fresh) in dict(_indexed_rows(mem))


def _indexed_rows(mem):
    conn = mem._get_db()
    try:
        return list(conn.execute("SELECT path, content FROM memory_fts"))
    finally:
        conn.close()


def test_fts_rebuild_job_reconciles_out_of_band_edits(tmp_path, monkeypatch):
    """The job does the WORK: a memory file edited outside the store API is measured as
    desync and the index matches disk afterwards."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.memory import MemoryStore, workspace_dir

    mem = MemoryStore()  # same resolution the job uses (see the sibling test)
    mem.init()
    mem.write_preferences("# User Preferences\n\nlikes tea\n")
    mem.rebuild_index()
    assert mem.fts_desync_count() == 0  # converged

    prefs = workspace_dir() / "memory" / "preferences.md"
    prefs.write_text("# User Preferences\n\nedited by hand\n", encoding="utf-8")
    assert mem.fts_desync_count() == 1

    detail = rem._job_rebuild_memory_fts()

    assert "FTS index rebuilt" in detail
    assert mem.fts_desync_count() == 0
    assert dict(_indexed_rows(mem))[str(prefs)] == prefs.read_text(encoding="utf-8")


def test_sel_prune_job_removes_exactly_what_it_measured(tmp_path, monkeypatch):
    """The job does the WORK: aged entries are gone, fresh ones survive, and the measured
    deficit equals what the prune removed (shared plan, so they can never disagree)."""
    import json
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.sel import sel

    log = sel()
    log.log_api_access(caller="t", operation="fresh", outcome="ok")
    aged = (datetime.now(tz=timezone.utc) - timedelta(days=400)).isoformat()
    with log._path.open("a", encoding="utf-8") as fh:
        for i in range(5):
            fh.write(json.dumps({"timestamp": aged, "event": "old", "n": i}) + "\n")

    assert log.count_prunable() == 5
    detail = rem._job_prune_sel()

    assert "pruned 5" in detail
    assert log.count_prunable() == 0
    body = log._path.read_text(encoding="utf-8")
    assert "fresh" in body and '"event": "old"' not in body


# ── knowledge.reindex-embeddings: the job could not work at all (#1782) ────────


def _seeded_store(tmp_path, n=3):
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(tmp_path / "k.db")
    for i in range(n):
        store.create_typed_item(item_type="note", title=f"N{i}", content=f"body {i}")
    assert store.count_items_missing_embedding() == n
    return store


def _bind(monkeypatch, store, embed):
    """Bind what the job resolves: the store, and an embedder OBJECT.

    The bug was that the job passed a bare `Callable[[str], vector]`, which `reembed_all`
    cannot use — so these fakes are deliberately the real `KnowledgeStore` and a real
    `UnifiedEmbedder`, not stubs. A stub accepting anything would have passed against the
    broken code too.
    """
    from personalclaw.knowledge.embedder import UnifiedEmbedder

    monkeypatch.setattr("personalclaw.knowledge.get_knowledge_store", lambda: store)
    monkeypatch.setattr(
        "personalclaw.knowledge.get_knowledge_embedder", lambda: UnifiedEmbedder(embed)
    )


def test_reindex_embeddings_actually_drains_the_backlog(tmp_path, monkeypatch):
    """It reported "re-embedded 0 item(s)" in every install: the embedder it passed was
    unusable AND the count it read was a key `reembed_all` does not return, so a job that
    embedded nothing was indistinguishable from a library with nothing to embed."""
    store = _seeded_store(tmp_path)
    _bind(monkeypatch, store, lambda text: [0.1, 0.2, 0.3])

    detail = rem._job_reindex_embeddings()

    assert detail == "re-embedded 3 item(s)"
    assert store.count_items_missing_embedding() == 0


def test_reindex_embeddings_only_touches_items_missing_a_vector(tmp_path, monkeypatch):
    """The deficit is `knowledge_missing_embeddings` and the job is titled "Backfill
    missing knowledge embeddings", so a whole-library re-embed is the wrong scope — it
    re-embeds every item the owner has on a 6-hourly cadence. The count in the message is
    what proves the scope."""
    store = _seeded_store(tmp_path)
    _bind(monkeypatch, store, lambda text: [0.1, 0.2, 0.3])
    assert rem._job_reindex_embeddings() == "re-embedded 3 item(s)"

    store.create_typed_item(item_type="note", title="fresh", content="new body")

    assert rem._job_reindex_embeddings() == "re-embedded 1 item(s)"


def test_a_total_reindex_failure_raises_instead_of_reporting_zero(tmp_path, monkeypatch):
    """Raising is the only way this job can say "it did not work": `run_remediation`
    writes `last_success_ts` on any non-raising return, so a clean zero would take the
    job's 6h cooldown while the deficit it claims to fix stayed exactly where it was."""
    store = _seeded_store(tmp_path)
    _bind(monkeypatch, store, lambda text: [])  # embeds nothing, corrupts nothing

    with pytest.raises(RuntimeError, match="embedded none of"):
        rem._job_reindex_embeddings()

    assert store.count_items_missing_embedding() == 3


def test_a_partial_reindex_reports_the_remainder_and_keeps_its_progress(tmp_path, monkeypatch):
    """A partial pass DID reduce the backlog, so it must not raise — that would discard
    the progress from the ledger and redo the same work next tick. `reembed_all` leaves a
    failed item vector-less rather than corrupt, so the remainder is simply still in the
    backlog, which the message says out loud rather than rounding to success."""
    store = _seeded_store(tmp_path)
    _bind(monkeypatch, store, lambda text: [] if "N1" in text else [0.1, 0.2, 0.3])

    detail = rem._job_reindex_embeddings()

    assert detail == "re-embedded 2 item(s); 1 still without a vector"
    assert store.count_items_missing_embedding() == 1


def test_reindex_embeddings_skips_cleanly_with_no_embedder(tmp_path, monkeypatch):
    store = _seeded_store(tmp_path)
    monkeypatch.setattr("personalclaw.knowledge.get_knowledge_store", lambda: store)
    monkeypatch.setattr("personalclaw.knowledge.get_knowledge_embedder", lambda: None)

    assert rem._job_reindex_embeddings() == "no embedder bound — skipped"
    assert store.count_items_missing_embedding() == 3
