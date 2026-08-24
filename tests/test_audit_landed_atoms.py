"""``tools/audit_landed_atoms.py`` must not be able to look clean while measuring nothing.

The tool answers one question — *which ``todo`` atoms are already satisfied by ``main``?* —
and its whole failure mode is silence. A census that matches nothing prints an empty
LANDED-AND-CLEAN bucket, exits ``0``, and is indistinguishable from a roadmap with no drift.
That is exactly how five atoms (``DCU-2``, ``PHF-7``, ``DFE-2``, ``PCS-7``, ``MRT-5``) sat
code-complete on ``main`` for days while reading ``todo``.

So every rail in ``self_check`` is exercised here by **planting the broken condition and
watching it fire**, not by reading the code and agreeing with it. Most tests build a tiny
synthetic ``Corpus``/catalog so they are hermetic and fast; two integration tests run the
real thing against the real repo, because the rails only mean something if they hold on the
data the tool actually reads.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tools.audit_landed_atoms import (
    CLEAN,
    GATED,
    KNOWN_LANDED,
    OPEN,
    UNKNOWN,
    Atom,
    Corpus,
    LogHit,
    LogVerdict,
    VacuityError,
    census,
    classify,
    decide_log,
    extract_keys,
    load_atoms,
    probe,
    scan_code_caveats,
    scan_plan_logs,
    score_evidence,
    self_check,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = "tools/audit_landed_atoms.py"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def make_atom(**over: object) -> Atom:
    base = dict(
        id="ZZ-1",
        title="a widget",
        status="todo",
        scope="adds widgets/thing.py with make_widget",
        done_when="calling make_widget returns a WidgetThing",
        deps=[],
        plan_code="ZZ",
        plan_name="ZZ-PLAN",
        plan_status="in_progress",
    )
    base.update(over)
    return Atom(**base)  # type: ignore[arg-type]


def make_corpus(blobs: dict[str, str]) -> Corpus:
    return Corpus(ref="test", paths=tuple(blobs), blobs=dict(blobs))


@pytest.fixture(scope="module")
def real_census() -> tuple[list, Corpus]:
    """One real census per worker.

    Each call costs ~13 s (a 45 MiB corpus read plus ~300 key probes), so calling it per test
    took the module to four minutes. Tests that need to mutate verdicts copy them.
    """
    return census(ref="origin/main")


@pytest.fixture(scope="module")
def real_corpus(real_census: tuple[list, Corpus]) -> Corpus:
    return real_census[1]


@pytest.fixture(scope="module")
def real_verdicts(real_census: tuple[list, Corpus]) -> list:
    return real_census[0]


@pytest.fixture(scope="module")
def real_log_hits() -> dict[str, list[LogHit]]:
    return scan_plan_logs()


# ---------------------------------------------------------------------------
# the catalog shape — the false-zero bug that started this
# ---------------------------------------------------------------------------


def test_atoms_live_under_plans_not_at_the_top_level() -> None:
    """A probe keyed on a top-level ``id``/``atoms`` finds nothing and reads as clean.

    This is not hypothetical: it cost a roadmap tick. The catalog is
    ``{"plans": [{"code", "atoms": [...]}], "dag": {...}}``, so the naive shape is empty.
    """
    data = json.loads((REPO_ROOT / "docs/roadmap/atomic/dag.json").read_text())
    assert "atoms" not in data and "id" not in data, "catalog shape changed; re-check the reader"
    naive = [a for a in data.get("atoms", [])]
    assert naive == [], "the naive top-level read must be the false zero this test describes"

    atoms = load_atoms()
    assert len(atoms) > 500, "the correct read must find the real population"
    assert any(a.status == "todo" for a in atoms)


def test_load_atoms_refuses_a_catalog_with_no_plans_key(tmp_path: Path) -> None:
    broken = tmp_path / "dag.json"
    broken.write_text(json.dumps({"atoms": [{"id": "X-1"}], "dag": {}}))
    with pytest.raises(VacuityError, match="no top-level 'plans' key"):
        load_atoms(broken)


def test_every_plan_name_resolves_to_a_plan_file() -> None:
    """Attribution is scoped to the owning plan file, so the mapping must be total."""
    missing = [
        a.plan_file
        for a in load_atoms()
        if not (REPO_ROOT / "docs/roadmap/plans" / a.plan_file).exists()
    ]
    assert missing == [], f"atoms whose plan markdown is unreachable: {sorted(set(missing))[:5]}"


# ---------------------------------------------------------------------------
# key extraction
# ---------------------------------------------------------------------------


def test_extract_keys_reads_paths_symbols_make_targets_and_env_vars() -> None:
    atom = make_atom(
        scope="touches routing/rates.py and web/src/lib/; adds make test-e2e",
        done_when="LearnedPolicy reads PERSONALCLAW_SCRIPTED_MODEL_SCRIPT and "
        "cloud_quality_margin; the widget exposes llmFriendlyMessage",
    )
    found = {(k.text, k.kind) for k in extract_keys(atom)}
    assert ("routing/rates.py", "path") in found
    assert ("web/src/lib/", "dir") in found
    assert ("test-e2e", "make") in found
    assert ("PERSONALCLAW_SCRIPTED_MODEL_SCRIPT", "env") in found
    assert ("LearnedPolicy", "symbol") in found
    assert ("cloud_quality_margin", "symbol") in found
    # lowerCamelCase is the frontend convention; omitting it blanked 37 of 129 atoms
    assert ("llmFriendlyMessage", "symbol") in found


def test_extract_keys_drops_doc_paths_and_prose_nouns() -> None:
    atom = make_atom(
        scope="see docs/guides/companion-apps.md; PersonalClaw and OpenAI are involved",
        done_when="the done_when is met",
    )
    keys = {k.text for k in extract_keys(atom)}
    assert "docs/guides/companion-apps.md" not in keys, "a doc path is never a deliverable"
    assert "PersonalClaw" not in keys and "OpenAI" not in keys
    assert "done_when" not in keys


def test_a_prose_fraction_is_not_a_directory() -> None:
    """``2/`` once matched a fixture path and manufactured evidence out of nothing."""
    atom = make_atom(scope="3 of 2/3 clauses hold", done_when="nothing")
    assert not [k for k in extract_keys(atom) if k.kind == "dir"]


def test_known_landed_atoms_are_key_rich() -> None:
    """The extractor rail's fixture. If these go thin, extraction has regressed."""
    by_id = {a.id: a for a in load_atoms()}
    for atom_id in KNOWN_LANDED:
        assert len(extract_keys(by_id[atom_id])) >= 3, f"{atom_id} yielded too few keys"


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------


def test_corpus_excludes_docs_so_an_atom_cannot_match_its_own_prose(real_corpus: Corpus) -> None:
    """The central false-positive trap: the scope text the keys came from lives in docs/."""
    assert not [p for p in real_corpus.blobs if p.startswith("docs/")]
    # a phrase that exists ONLY in the roadmap prose must not be findable
    phrase = "Atom stays `todo` only because this code is unmerged"
    assert real_corpus.find(phrase) == ([], []), "docs/ leaked into the evidence corpus"


def test_corpus_file_fence_stops_a_match_straddling_two_files() -> None:
    corpus = make_corpus({"src/a.py": "alpha", "src/b.py": "beta"})
    assert corpus.find("alpha")[0] == ["src/a.py"]
    assert corpus.find("alphabeta") == ([], []), "the NUL fence must break cross-file matches"


def test_dir_exists_is_anchored_at_a_path_boundary() -> None:
    corpus = make_corpus({"src/personalclaw/net/policy.py": "x", "loop/a17c3f92/status.json": "y"})
    assert corpus.dir_exists("net/") == ["src/personalclaw/net/policy.py"]
    assert corpus.dir_exists("2/") == [], "an unanchored substring test invents directories"


def test_impl_and_test_hits_are_counted_separately() -> None:
    corpus = make_corpus({"src/x.py": "make_widget", "tests/test_x.py": "make_widget"})
    keys = extract_keys(make_atom(done_when="make_widget exists"))
    probe(keys, corpus)
    key = next(k for k in keys if k.text == "make_widget")
    assert key.found_impl == ["src/x.py"]
    assert key.found_test == ["tests/test_x.py"]


# ---------------------------------------------------------------------------
# log attribution
# ---------------------------------------------------------------------------


def test_the_flip_phrase_is_matched_across_a_line_wrap(real_log_hits: dict) -> None:
    """The canonical phrase is written wrapped, so a line-oriented grep misses it entirely.

    This is why the classifier normalises whitespace first. Without it the tool finds zero
    flippable atoms and looks like a clean roadmap.
    """
    flips = {
        aid
        for aid, hits in real_log_hits.items()
        if any(h.verdict == LogVerdict.FLIP for h in hits)
    }
    assert flips, "no FLIP entry found at all — the phrasing or the normalisation broke"
    for atom_id in KNOWN_LANDED:
        assert atom_id in flips, f"{atom_id}'s complete-but-unmerged entry was not seen"


def test_a_headline_subject_outranks_a_body_cross_reference() -> None:
    hits = [
        LogHit(LogVerdict.PARTIAL, 10, "own entry", "P.md", headline=True),
        LogHit(LogVerdict.FLIP, 99, "somebody else's entry", "P.md", headline=False),
    ]
    verdict, hit = decide_log(hits, own_plan_file="P.md")
    assert verdict == LogVerdict.PARTIAL and hit is not None and hit.headline


def test_a_body_only_mention_can_never_declare_an_atom_flippable() -> None:
    """`DCU-3` inherited `DCU-2`'s "flip it when the PR lands" from a body mention."""
    hits = [LogHit(LogVerdict.FLIP, 5, "…DCU-2 COMPLETE… mentions DCU-3…", "P.md", headline=False)]
    assert decide_log(hits, own_plan_file="P.md")[0] == LogVerdict.NONE
    # but a body-only GATE still holds — the asymmetry is deliberate
    gated = [LogHit(LogVerdict.GATED, 5, "blocked", "P.md", headline=False)]
    assert decide_log(gated, own_plan_file="P.md")[0] == LogVerdict.GATED


def test_only_the_owning_plan_adjudicates_an_atom() -> None:
    """A frontier survey in another plan listed CRE-7/LMMV-7 and was read as their gate."""
    hits = [LogHit(LogVerdict.GATED, 5, "frontier survey", "OTHER-PLAN.md", headline=True)]
    assert decide_log(hits, own_plan_file="MY-PLAN.md")[0] == LogVerdict.NONE


def test_the_last_entry_supersedes_an_earlier_one_on_the_same_day() -> None:
    """`PHF-7` is logged PARTIAL, then "all five clauses now MET" lower in the same file."""
    hits = [
        LogHit(LogVerdict.PARTIAL, 100, "PARTIAL", "P.md", headline=True),
        LogHit(LogVerdict.FLIP, 900, "all five clauses now MET", "P.md", headline=True),
    ]
    assert decide_log(hits, own_plan_file="P.md")[0] == LogVerdict.FLIP


def test_a_superseded_entry_is_ignored(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "P.md").write_text(
        "## Execution log\n\n"
        "- **2026-08-01 — `ZZ-1` COMPLETE. Atom stays `todo` only because this code is\n"
        "  unmerged**; flip it when the PR lands. *(SUPERSEDED 2026-08-02 — reopened.)*\n"
    )
    assert scan_plan_logs(plans).get("ZZ-1") is None


# ---------------------------------------------------------------------------
# bucketing
# ---------------------------------------------------------------------------


def test_a_flip_log_plus_evidence_is_the_only_route_into_the_clean_bucket() -> None:
    atom = make_atom()
    corpus = make_corpus({"src/widgets/thing.py": "def make_widget(): return WidgetThing()"})
    keys = extract_keys(atom)
    probe(keys, corpus)
    flip = [LogHit(LogVerdict.FLIP, 1, "…", "ZZ-PLAN.md", headline=True)]
    assert classify(atom, keys, flip).bucket == CLEAN

    # same evidence, no log entry -> honestly unknown, never clean
    assert classify(atom, keys, []).bucket == UNKNOWN
    # same log entry, no evidence -> the signals disagree, so also unknown
    empty = extract_keys(atom)
    probe(empty, make_corpus({"src/other.py": "unrelated"}))
    assert classify(atom, empty, flip).bucket == UNKNOWN


def test_an_inertness_note_in_code_demotes_an_otherwise_flippable_atom() -> None:
    """A finding recorded in a test, not in the plan log, must still gate the atom.

    ``DCU-2``'s log says "COMPLETE (all three clauses); flip it when the PR lands", and
    ``tests/test_computer_use_call_sites.py`` — committed to ``main`` *after* that entry —
    proves ``check_app``/``check_input_target``/``require_computer_use`` have zero production
    callers. Log-only adjudication calls that flippable when it is landed-but-inert.
    """
    atom = make_atom()
    corpus = make_corpus({"src/widgets/thing.py": "def make_widget(): return WidgetThing()"})
    keys = extract_keys(atom)
    probe(keys, corpus)
    flip = [LogHit(LogVerdict.FLIP, 1, "…", "ZZ-PLAN.md", headline=True)]
    caveat = [("tests/test_zz_call_sites.py", "ZZ-1's screens have zero production callers")]

    assert classify(atom, keys, flip).bucket == CLEAN, "baseline: flippable without the caveat"
    demoted = classify(atom, keys, flip, caveat)
    assert demoted.bucket == GATED
    assert "inertness gap" in demoted.why


def test_the_code_caveat_signal_is_one_directional() -> None:
    """It may demote out of CLEAN; it may never promote anything into it."""
    atom = make_atom()
    keys = extract_keys(atom)
    probe(keys, make_corpus({"src/unrelated.py": "nothing"}))
    caveat = [("tests/t.py", "zero production callers")]
    # no evidence, no log, plus a caveat -> still NOT-LANDED, not upgraded
    assert classify(atom, keys, [], caveat).bucket == OPEN


def test_the_real_dcu2_caveat_is_actually_found_on_main(real_corpus: Corpus) -> None:
    """Vacuity floor for the caveat scan: it must see the case it was built for."""
    caveats = scan_code_caveats(real_corpus)
    assert "DCU-2" in caveats, "the DCU-2 zero-caller census on main was not detected"
    assert any("call_sites" in path for path, _ in caveats["DCU-2"])


def test_a_gate_in_the_log_keeps_a_landed_atom_out_of_the_clean_bucket() -> None:
    atom = make_atom()
    corpus = make_corpus({"src/widgets/thing.py": "def make_widget(): return WidgetThing()"})
    keys = extract_keys(atom)
    probe(keys, corpus)
    for verdict in (LogVerdict.GATED, LogVerdict.PARTIAL):
        hits = [LogHit(verdict, 1, "…", "ZZ-PLAN.md", headline=True)]
        assert classify(atom, keys, hits).bucket == GATED


def test_no_evidence_and_no_log_is_not_landed() -> None:
    atom = make_atom()
    keys = extract_keys(atom)
    probe(keys, make_corpus({"src/unrelated.py": "nothing here"}))
    assert classify(atom, keys, []).bucket == OPEN


def test_a_retirement_atom_is_scored_with_the_sign_the_right_way_round() -> None:
    """Finding the named file proves a *deletion* atom is NOT done.

    ``SV-11`` ("retire the interim commit-watcher cron script") names
    ``selfqa/scripts/selfqa_commit_watch.py``. That file is on ``main``, which is exactly why
    the atom is open — presence-evidence has the wrong sign here.
    """
    atom = make_atom(
        title="Retire the interim commit-watcher cron script",
        scope="delete the selfqa/scripts/selfqa_commit_watch.py shim",
        done_when="the script no longer exists",
    )
    assert atom.is_retirement
    keys = extract_keys(atom)
    probe(keys, make_corpus({"src/personalclaw/selfqa/scripts/selfqa_commit_watch.py": "x"}))
    verdict = classify(atom, keys, [])
    assert verdict.bucket == UNKNOWN
    assert "evidence AGAINST completion" in verdict.why

    # a normal build atom keeps the ordinary reading
    plain = make_atom()
    plain_keys = extract_keys(plain)
    probe(
        plain_keys, make_corpus({"src/widgets/thing.py": "def make_widget(): return WidgetThing()"})
    )
    assert not plain.is_retirement
    assert "evidence AGAINST" not in classify(plain, plain_keys, []).why


def test_an_atom_with_no_extractable_key_is_unknown_not_open() -> None:
    """Prose-only atoms exist ("public launch announced after the gate is met")."""
    atom = make_atom(scope="Session 1; owner call", done_when="the launch is announced")
    keys = extract_keys(atom)
    assert score_evidence(keys)[0] == "NO-KEYS"
    assert classify(atom, keys, []).bucket == UNKNOWN


# ---------------------------------------------------------------------------
# vacuity rails: plant the break, watch it fire
# ---------------------------------------------------------------------------


def test_self_check_passes_on_the_real_repo(
    real_verdicts: list, real_corpus: Corpus, real_log_hits: dict
) -> None:
    assert self_check(real_verdicts, real_corpus, real_log_hits) == []
    assert real_verdicts, "vacuity floor: the census must select something"


def test_self_check_fires_on_an_empty_selection(real_corpus: Corpus, real_log_hits: dict) -> None:
    problems = self_check([], real_corpus, real_log_hits)
    assert any("zero atoms selected" in p for p in problems)


def test_self_check_fires_on_a_truncated_corpus(real_verdicts: list, real_log_hits: dict) -> None:
    verdicts = real_verdicts
    tiny = make_corpus({"src/a.py": "x"})
    problems = self_check(verdicts, tiny, real_log_hits)
    assert any("corpus holds only" in p for p in problems)
    assert any("content search is dead" in p for p in problems)
    assert any("make-target probe is dead" in p for p in problems)


def test_self_check_fires_when_content_search_matches_anything(
    real_verdicts: list, real_corpus: Corpus, real_log_hits: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdicts = real_verdicts
    monkeypatch.setattr(Corpus, "find", lambda self, needle: (["src/everything.py"], []))
    problems = self_check(verdicts, real_corpus, real_log_hits)
    assert any("WAS found" in p for p in problems), "an always-true search must be caught"


def test_self_check_fires_when_the_flip_phrasing_rots(
    real_verdicts: list, real_corpus: Corpus
) -> None:
    verdicts = real_verdicts
    no_flips = {
        aid: [h for h in hits if h.verdict != LogVerdict.FLIP]
        for aid, hits in scan_plan_logs().items()
    }
    problems = self_check(verdicts, real_corpus, no_flips)
    assert any("FLIP pattern" in p for p in problems)


def test_self_check_fires_when_one_known_case_stops_being_detected(
    real_verdicts: list, real_corpus: Corpus
) -> None:
    """ "At least one FLIP" is not a rail.

    Measured: replacing the whitespace normaliser with a no-op silently dropped exactly one
    of the five hand-verified atoms (``PHF-7``) and the >=1 assertion stayed green — the
    census printed 6 flippable atoms instead of 7 and exited 0. The fixture has to be the
    five, individually.
    """
    verdicts = real_verdicts
    hits = scan_plan_logs()
    hits["PHF-7"] = [h for h in hits["PHF-7"] if h.verdict != LogVerdict.FLIP]
    problems = self_check(verdicts, real_corpus, hits)
    assert any(
        "PHF-7's complete-but-unmerged log entry is no longer detected" in p for p in problems
    )


def test_self_check_fires_when_gate_detection_dies(
    real_verdicts: list, real_corpus: Corpus
) -> None:
    verdicts = real_verdicts
    no_gates = {
        aid: [h for h in hits if h.verdict != LogVerdict.GATED]
        for aid, hits in scan_plan_logs().items()
    }
    problems = self_check(verdicts, real_corpus, no_gates)
    assert any("GATED pattern" in p for p in problems)


def test_self_check_fires_when_key_extraction_regresses(
    real_verdicts: list, real_corpus: Corpus, real_log_hits: dict
) -> None:
    # copied: the shared fixture must not be poisoned for other tests
    verdicts = [replace(v, keys=[]) for v in real_verdicts]
    problems = self_check(verdicts, real_corpus, real_log_hits)
    assert any("key extractor found only 0 keys" in p for p in problems)
    assert any(">60%" in p for p in problems)


# ---------------------------------------------------------------------------
# the CLI contract
# ---------------------------------------------------------------------------


def test_the_tool_exits_zero_on_an_imperfect_roadmap() -> None:
    """A roadmap with drift is the tool's *subject*, never its error."""
    proc = subprocess.run(
        [sys.executable, TOOL, "--verify-known"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "LANDED-AND-CLEAN" in proc.stdout
    assert "miss-rate=0%" in proc.stdout, "the five known-landed atoms must all be detected"


def test_the_tool_exits_non_zero_on_its_own_broken_input(tmp_path: Path) -> None:
    scratch = tmp_path / "dag.json"
    scratch.write_text(json.dumps({"nope": []}))
    proc = subprocess.run(
        [sys.executable, TOOL, "--dag", str(scratch)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, proc.stdout
    assert "INTERNAL ERROR" in proc.stderr


def test_a_module_level_rebind_of_dag_path_does_not_reach_census(tmp_path: Path) -> None:
    """Why ``--dag`` exists at all.

    ``census(dag_path=DAG_PATH)`` binds the default at import, so patching the module global
    is a silent no-op — the run reads the real catalog and looks like it worked. Asserting
    the no-op keeps anyone from "simplifying" the flag away and re-introducing it.
    """
    import tools.audit_landed_atoms as mod

    scratch = tmp_path / "dag.json"
    scratch.write_text(
        json.dumps({"plans": [{"plan": "P", "code": "P", "status": "todo", "atoms": []}]})
    )
    original = mod.DAG_PATH
    try:
        mod.DAG_PATH = scratch
        assert load_atoms() != [], "the rebind must NOT take effect; that is the whole point"
    finally:
        mod.DAG_PATH = original


def test_the_tool_never_writes_to_the_roadmap() -> None:
    """``dag.json`` is owner-maintained. The census is read-only, asserted on the bytes."""
    dag = REPO_ROOT / "docs/roadmap/atomic/dag.json"
    before = dag.read_bytes()
    subprocess.run([sys.executable, TOOL], cwd=REPO_ROOT, capture_output=True, text=True)
    assert dag.read_bytes() == before
