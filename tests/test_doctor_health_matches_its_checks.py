"""Settings B16 — the Doctor must not report health it does not have.

What the validator saw on one page: two failed checks ("faiss index desync: 0 indexed vs 2
embedded rows", "4 unclaimed paths … in NO snapshot"), neither with a Fix — only "Investigate in
chat" — and directly below them Maintenance reading "Health score 100 / target 90", with Run now
answering "target_score already met". Memory → Rebuild links and Re-scan did not clear the desync
either, because neither touches the index.

Three defects, pinned here in that order:

1. ONE AUTHORITY FOR HEALTH. `health_score` read only the engine's own deficits, never a Doctor
   check, and counted only what the engine could fix. A failed check is now a deficit, and every
   deficit counts; what the engine can still do is a separate number (`fixable_penalty`).
2. EVERY FAILED CHECK NAMES ITS NEXT STEP — a registered Fix, or a plain sentence saying there is
   none and what to do instead. A static rail over every failure a probe can return, plus the
   failures a real home produces, driven.
3. THE DESYNC'S ROOT CAUSE. The store's index width was a constructor constant (384) nothing kept
   in step with the bound model, so every write skipped the index and a rebuild rebuilt at the
   same stale width; the saved index was also trusted on open however stale it was. The width now
   follows the stored vectors, an open reconciles the file, and the Fix (also a maintenance job)
   rebuilds the index recall actually reads.
"""

from __future__ import annotations

import ast
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.resilience import doctor, fixes, remediation
from personalclaw.resilience.doctor import DoctorContext
from personalclaw.vector_memory import VectorMemoryStore

_DOCTOR_SRC = Path(doctor.__file__)
#: The wire id of the index Fix and its maintenance job — pinned as the string a client sees.
MEMORY_INDEX_FIX = "memory.rebuild-faiss-index"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home that every resolver in the path reads."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.resilience.remediation.config_dir", lambda: home)
    return home


def _only_probes(monkeypatch, *ids: str) -> None:
    """Run only these REAL probes, so a check this environment happens to fail (an unbuilt SPA
    in a worktree, a host timezone) cannot decide a test about a different one."""
    chosen = [p for p in doctor.all_probes() if p.id in ids]
    assert {p.id for p in chosen} == set(ids), "a probe id this test names is not registered"
    monkeypatch.setattr(doctor, "all_probes", lambda: list(chosen))


def _vec(axis: int, dim: int = 8) -> list[float]:
    """A vector pointing mostly along one axis. Distinct axes are far apart, so two memories are
    never deduplicated into one by the write path's similarity check."""
    return [1.0 if i == axis % dim else 0.1 for i in range(dim)]


# ── 1. one authority for health ─────────────────────────────────────────────────────────


def test_a_failed_check_lowers_the_health_score(home, monkeypatch):
    """The validator's "4 unclaimed paths": a store the manifest does not claim fails the
    durability check, and the score must say so rather than read 100."""
    _only_probes(monkeypatch, "durability.inventory")
    (home / "a_store_nobody_declared").mkdir()
    (home / "a_store_nobody_declared" / "state.json").write_text("{}", encoding="utf-8")

    deficits = remediation.measure_deficits()
    (check,) = [d for d in deficits if d.key == "check:durability.inventory"]
    assert check.reachable is False and check.count == 1
    assert check.title == "Every state path is claimed by the manifest"
    assert "No automatic fix" in check.blocked_by, check.blocked_by
    assert remediation.health_score(deficits) <= 100 - check.penalty < 90


def test_run_now_does_not_claim_the_target_is_met_while_a_check_fails(home, monkeypatch):
    _only_probes(monkeypatch, "durability.inventory")
    (home / "a_store_nobody_declared").mkdir()

    result = remediation.run_remediation(target_score=90, now=time.time(), dry_run=True)
    assert result.score_before < 90
    assert result.stopped_reason != "target_score already met"
    # And it says why it did nothing, instead of implying it will get to it.
    assert result.stopped_reason == remediation.NOTHING_FIXABLE
    assert result.fixable_after == 0.0 and result.jobs == []


def test_a_clean_home_still_scores_100(home, monkeypatch):
    """The vacuity leg: the score is not simply lower now — a home with nothing wrong is 100."""
    _only_probes(monkeypatch, "durability.inventory", "memory.store")
    deficits = remediation.measure_deficits()
    assert not [d for d in deficits if d.count], deficits
    assert remediation.health_score(deficits) == 100.0


# ── 2. every failed check names a Fix or says there is none ─────────────────────────────


def _failure_constructions() -> list[tuple[str, int, dict[str, ast.expr]]]:
    """Every `ProbeResult(...)` in the probe module that can report a failure: `ok` is anything
    but the literal True. Returns ``(function, line, keywords)``."""
    tree = ast.parse(_DOCTOR_SRC.read_text(encoding="utf-8"))
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "ProbeResult"):
                continue
            kws = {k.arg: k.value for k in call.keywords if k.arg}
            ok = kws.get("ok")
            if isinstance(ok, ast.Constant) and ok.value is True:
                continue
            out.append((fn.name, call.lineno, kws))
    return out


def _fix_ids_named_in_the_module() -> set[str]:
    """Every Fix id a probe can hand out: the VALUES a `fix_id=` keyword or a `fix_id = …`
    assignment can take — never the strings in a condition that chooses between them."""
    tree = ast.parse(_DOCTOR_SRC.read_text(encoding="utf-8"))
    consts = {
        t.id: n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
        for t in n.targets
        if isinstance(t, ast.Name) and isinstance(n.value.value, str)
    }

    def values(expr: ast.expr) -> set[str]:
        if isinstance(expr, ast.Constant):
            return {expr.value} if isinstance(expr.value, str) and expr.value else set()
        if isinstance(expr, ast.Name):
            return {consts[expr.id]} if expr.id in consts else set()
        if isinstance(expr, ast.IfExp):
            return values(expr.body) | values(expr.orelse)
        if isinstance(expr, ast.BoolOp):
            return set().union(*(values(v) for v in expr.values))
        return set()

    named: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "fix_id":
            named |= values(node.value)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "fix_id" for t in node.targets
        ):
            named |= values(node.value)
    return named


def _always_names_a_fix(fix: ast.expr | None) -> bool:
    """A Fix id the construction carries on every path: a string literal or a module constant.
    A variable or a conditional does not count — it can be None on the very branch that failed
    (the SPA check's "not resolvable" branch had exactly that)."""
    if isinstance(fix, ast.Constant):
        return isinstance(fix.value, str) and bool(fix.value)
    return isinstance(fix, ast.Name) and fix.id.isupper()


def test_every_failure_a_probe_can_return_names_a_fix_or_a_remedy():
    """The static half: a failure is constructed with a Fix, or with a `remedy` that says there
    is none and what to do instead."""
    failures = _failure_constructions()
    assert len(failures) >= 20, f"the census found {len(failures)} failure sites — is it reading?"
    bare = [
        f"{fn}:{line}"
        for fn, line, kws in failures
        if not (_always_names_a_fix(kws.get("fix_id")) or "remedy" in kws)
    ]
    assert (
        not bare
    ), f"these failures name neither a Fix nor a remedy, so the row is a dead end: {bare}"


def test_every_fix_a_probe_names_is_registered():
    named = _fix_ids_named_in_the_module()
    assert MEMORY_INDEX_FIX in named and "serving-fs.symlink-repair" in named, named
    unregistered = sorted(f for f in named if fixes.get_fix(f) is None)
    assert not unregistered, f"a probe offers a Fix that does not exist: {unregistered}"


def _desync_home(home: Path) -> None:
    """Two embedded memories and a saved index that holds neither — the validator's state."""
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    store.write_episodic("The collector keeps its queue in SQLite with WAL", embedding=_vec(0))
    store.write_episodic("Release notes go out on Thursdays after the freeze", embedding=_vec(1))
    store.close()
    (home / "memory.ids.json").write_text("[]", encoding="utf-8")


def test_the_failures_a_real_home_shows_each_offer_a_fix_or_say_what_to_do(home, monkeypatch):
    """The dynamic half, on the validator's two failures plus a crash record: every failed row
    carries a Fix the registry has, or a remedy — never neither."""
    pytest.importorskip("faiss")  # an [embeddings] extra; `[dev]` installs it
    _only_probes(monkeypatch, "memory.store", "durability.inventory", "crashes.recent")
    _desync_home(home)
    (home / "a_store_nobody_declared").mkdir()
    (home / "crashes").mkdir()
    (home / "crashes" / "1758800000-turn.json").write_text(
        json.dumps({"ts": 1758800000, "kind": "turn", "exception": {"type": "KeyError"}}),
        encoding="utf-8",
    )

    report = asyncio.run(doctor.run_doctor(DoctorContext(home=home)))
    rows = [p for cap in report["capabilities"].values() for p in cap["probes"]]
    failed = {r["id"]: r for r in rows if not r["ok"]}
    assert set(failed) == {"memory.store", "durability.inventory", "crashes.recent"}, failed
    for row in failed.values():
        fix = row.get("fix_id")
        assert (fix and fixes.get_fix(fix)) or row.get("remedy"), row
    assert failed["memory.store"]["fix_id"] == MEMORY_INDEX_FIX
    assert "0 indexed vs 2 embedded" in failed["memory.store"]["detail"]
    assert failed["durability.inventory"]["remedy"].startswith("No automatic fix")
    assert str(home / "crashes") in failed["crashes.recent"]["remedy"]


# ── 3. the desync's root cause, and the rebuild that clears it ──────────────────────────


def test_a_store_opened_at_the_default_width_indexes_the_bound_models_vectors(tmp_path):
    """The root cause: the gateway builds its store without `embedding_dim`, so the index was
    384 wide whatever the model produced, and every write of an 8/768/1024-dim model skipped it."""
    pytest.importorskip("faiss")
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")  # the gateway's construction
    store.init()
    store.write_episodic("The collector keeps its queue in SQLite with WAL", embedding=_vec(0))
    store.write_episodic("Release notes go out on Thursdays after the freeze", embedding=_vec(1))

    assert store._faiss_index is not None and store._faiss_index.ntotal == 2
    hits = store.search_episodic(query_embedding=_vec(0), query_text="collector queue")
    assert hits and "collector" in hits[0]["text"], hits


def test_a_rebuild_indexes_at_the_width_the_vectors_have(tmp_path):
    """ "Re-index doesn't clear it": the rebuild used the same stale constant and skipped every
    row again. It now takes the width from the vectors."""
    pytest.importorskip("faiss")
    writer = VectorMemoryStore(db_path=tmp_path / "memory.db", embedding_dim=8)
    writer.init()
    writer.write_episodic("The collector keeps its queue in SQLite with WAL", embedding=_vec(0))
    writer.write_episodic("Release notes go out on Thursdays after the freeze", embedding=_vec(1))
    writer.close()

    reader = VectorMemoryStore(db_path=tmp_path / "memory.db")  # default width again
    reader.init()
    assert reader.build_faiss_index() == 2


def test_a_stale_index_file_is_reconciled_when_the_store_opens(tmp_path):
    """The saved index is a cache that goes stale — it was saved only every 100 writes, and a
    process that stopped between saves left rows out of it. An open used to trust it anyway."""
    pytest.importorskip("faiss")
    first = VectorMemoryStore(db_path=tmp_path / "memory.db")
    first.init()
    first.write_episodic("The collector keeps its queue in SQLite with WAL", embedding=_vec(0))
    first.save_faiss_index()
    first.write_episodic("Release notes go out on Thursdays after the freeze", embedding=_vec(1))
    # The process stops here without closing: the file holds one of the two.
    assert json.loads((tmp_path / "memory.ids.json").read_text()) != first._faiss_id_map

    reopened = VectorMemoryStore(db_path=tmp_path / "memory.db")
    reopened.init()
    assert reopened._faiss_index.ntotal == 2
    assert (
        len(json.loads((tmp_path / "memory.ids.json").read_text())) == 2
    ), "the reconciled index must be saved, so the next open — and the Doctor — read it"


def test_the_doctor_fix_rebuilds_the_index_recall_reads(home):
    """The Fix acts on the LIVE store — the one a turn searches — not on a copy of it."""
    pytest.importorskip("faiss")
    import faiss

    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    store.serve_recall()
    store.write_episodic("The collector keeps its queue in SQLite with WAL", embedding=_vec(0))
    store.write_episodic("Release notes go out on Thursdays after the freeze", embedding=_vec(1))
    # What the validator's gateway held: an index with none of the embedded rows in it.
    store._faiss_index = faiss.IndexFlatIP(8)
    store._faiss_id_map = []

    before = asyncio.run(doctor._probe_memory(DoctorContext(home=home)))
    assert before.ok is False and before.fix_id == MEMORY_INDEX_FIX, before
    assert before.detail == "faiss index desync: 0 indexed vs 2 embedded rows"
    assert "rebuild the search index" in fixes.get_fix(MEMORY_INDEX_FIX).dry_preview()

    applied = fixes.apply_fix(MEMORY_INDEX_FIX)
    assert applied["ok"], applied
    assert "2 of 2 embedded memories indexed" in applied["result"]

    after = asyncio.run(doctor._probe_memory(DoctorContext(home=home)))
    assert after.ok is True, after
    hits = store.search_episodic(query_embedding=_vec(1), query_text="release notes")
    assert hits and "Release notes" in hits[0]["text"], hits
    store.close()


def _embedder(dim: int, calls: list[str]):
    """A stand-in model: deterministic, one direction per text, and it records every call."""

    def embed(text: str) -> list[float]:
        calls.append(text)
        return _vec(sum(map(ord, text)), dim)

    return embed


def _mixed_width_store(home: Path, calls: list[str]) -> VectorMemoryStore:
    """Two memories an old 4-dim model wrote, then one from the 8-dim model bound now."""
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    store.serve_recall()
    store.write_episodic("An early note from the old embedding model", embedding=_vec(0, 4))
    store.write_episodic("A second note from before the model switch", embedding=_vec(1, 4))
    store.embed_fn = _embedder(8, calls)
    store.write_episodic("The first memory the new model embedded")
    calls.clear()
    return store


def test_the_fix_reembeds_memories_another_model_wrote(home, monkeypatch):
    """A rebuild alone can never index a vector of another model's width, so the Fix (which the
    user confirms) re-embeds exactly those rows with the model bound now, then rebuilds."""
    pytest.importorskip("faiss")
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda use_case: True
    )
    calls: list[str] = []
    store = _mixed_width_store(home, calls)

    before = asyncio.run(doctor._probe_memory(DoctorContext(home=home)))
    assert before.ok is False and before.fix_id == MEMORY_INDEX_FIX, before
    assert "re-embed the 2 memories" in fixes.get_fix(MEMORY_INDEX_FIX).dry_preview()

    applied = fixes.apply_fix(MEMORY_INDEX_FIX)
    assert applied["ok"], applied
    assert applied["result"].startswith("Re-embedded 2 memories"), applied
    # One probe embedding for the width, then one per stale row — never one per memory.
    assert len(calls) == 3, calls
    assert asyncio.run(doctor._probe_memory(DoctorContext(home=home))).ok is True
    assert store._faiss_index.ntotal == 3
    store.close()


def test_other_model_memories_with_no_embedder_bound_say_what_to_do(home, monkeypatch):
    pytest.importorskip("faiss")
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda use_case: False
    )
    calls: list[str] = []
    store = _mixed_width_store(home, calls)
    store.build_faiss_index()  # nothing missing at the current width; only the other model's rows

    res = asyncio.run(doctor._probe_memory(DoctorContext(home=home)))
    assert res.ok is False and res.fix_id is None, res
    assert "different model" in res.detail
    assert "Settings → Models" in res.remedy and res.remedy.startswith("No automatic fix")
    store.close()


def test_the_maintenance_job_rebuilds_without_spending_an_embedding(home, monkeypatch):
    """The engine runs unattended, so its job only rebuilds the derived index; re-embedding is
    the confirmed Fix's."""
    pytest.importorskip("faiss")
    import faiss

    calls: list[str] = []
    store = _mixed_width_store(home, calls)
    store._faiss_index = faiss.IndexFlatIP(8)
    store._faiss_id_map = []

    message = remediation._JOBS[MEMORY_INDEX_FIX].run()
    assert calls == [], "an unattended pass called the embedding model"
    assert "1 of 3 embedded memories indexed" in message, message
    assert "the Doctor's Fix re-embeds them" in message
    store.close()


def test_a_desynced_index_is_scored_and_repaired_by_maintenance(home, monkeypatch):
    """The engine sees the desync as its own deficit — `memory_index_desync`, counted by the
    check's measurement — and Run now's job clears it, so the Doctor and the score move together.
    """
    pytest.importorskip("faiss")
    _only_probes(monkeypatch, "memory.store")
    _desync_home(home)

    deficits = {d.key: d for d in remediation.measure_deficits()}
    desync = deficits["memory_index_desync"]
    assert desync.count == 2 and desync.reachable and desync.job_id == MEMORY_INDEX_FIX
    assert "check:memory.store" not in deficits, "one fault, charged twice"
    assert remediation.health_score(list(deficits.values())) < 90

    result = remediation.run_remediation(target_score=90, now=time.time())
    assert {"id": MEMORY_INDEX_FIX, "status": "ok"}.items() <= result.jobs[0].items(), result.jobs
    assert result.score_after == 100.0, result
    assert asyncio.run(doctor._probe_memory(DoctorContext(home=home))).ok is True


# ── the page reads what the Fix did ─────────────────────────────────────────────────────


def test_a_fix_is_not_hidden_behind_the_report_cache(home, monkeypatch):
    """The report is cached 30s for the dashboard poll. A Fix and the page's own Re-run are not
    polls: serving them the cached pre-repair report is how a Fix that worked read as one that
    did nothing."""
    from personalclaw.dashboard.handlers import doctor as handlers

    runs: list[int] = []

    async def _run_doctor(ctx=None, **_kw):
        runs.append(1)
        return {"ok": True, "capabilities": {}, "generation": len(runs)}

    monkeypatch.setattr(handlers, "run_doctor", _run_doctor)
    monkeypatch.setattr(handlers, "_doctor_cache", None)

    def _get(query: str = ""):
        req = MagicMock()
        req.query = {"fresh": "1"} if query else {}
        req.app = {}
        return asyncio.run(handlers.api_doctor(req))

    _get()
    _get()
    assert len(runs) == 1, "the poll path is cached"
    _get(query="fresh")
    assert len(runs) == 2, "Re-run asks for a fresh report and must get one"

    async def _body():
        return {"confirm": True}

    req = MagicMock()
    req.match_info = {"fix_id": "serving-fs.orphan-prune"}
    req.json = _body
    resp = asyncio.run(handlers.api_doctor_fix_apply(req))
    assert resp.status == 200, resp.text
    _get()
    assert len(runs) == 3, "the first read after a Fix must re-probe"
