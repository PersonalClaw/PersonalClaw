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

The second half of this file covers the WIRE CHECK (``--check-wires``), whose subject is the
census's own biggest miss: ``APE-3`` scored LANDED-AND-CLEAN with three production call sites
that **no test asserted**, so deleting all three left 116 tests green. The wire check answers
"would deleting the caller be caught?" by actually deleting it, and that makes its own vacuity
problem sharper than the census's: a mutation that silently fails to land reports every wire
as RAILED, and a check that cannot detect a red is indistinguishable from a repo with no
unrailed wires. So the central rail here is a synthetic repo carrying **one railed and one
unrailed wire of the same shape**: the check must separate them. If the mutation degrades to a
no-op the railed case flips to UNRAILED; if it degrades to a parse break both become REFUSED.
Either way this file reds. ``tools/`` is linted by neither ``make lint`` nor CI, so these
assertions are the only enforcement the check has.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from tools.audit_landed_atoms import (
    CLEAN,
    GATED,
    KNOWN_LANDED,
    MUT_MARK,
    OPEN,
    RE_ANSI,
    UNKNOWN,
    WIRE_RAILED,
    WIRE_REFUSED,
    WIRE_UNRAILED,
    Atom,
    Corpus,
    LogHit,
    LogVerdict,
    RunResult,
    Snapshot,
    VacuityError,
    Wire,
    WireRefusal,
    _ref_exists,
    _test_index,
    annotated_modules,
    census,
    check_atom_wires,
    classify,
    decide_log,
    extract_keys,
    find_wires,
    load_atoms,
    mutate,
    probe,
    resolve_ref,
    scan_code_caveats,
    scan_plan_logs,
    score_evidence,
    select_tests,
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
    """The central false-positive trap: the scope text the keys came from lives in docs/.

    The probe phrase is READ FROM the docs tree, never written here. An earlier version
    hard-coded ``"Atom stays `todo` only because this code is unmerged"``, which put the
    phrase into ``tests/`` — and ``tests/`` IS in the corpus. That passed only because this
    very file did not yet exist on ``origin/main``; the assertion was scheduled to start
    failing the moment this test landed, whichever ref the corpus was built from. A test whose
    own source is inside the corpus it audits cannot use a literal as its needle.
    """
    assert not [p for p in real_corpus.blobs if p.startswith("docs/")]

    # A phrase that genuinely exists ONLY in roadmap prose, taken from the prose itself.
    doc = REPO_ROOT / "docs" / "roadmap" / "plans" / "MODEL-ROUTING-TELEMETRY.md"
    needle = next(
        line.strip()
        for line in doc.read_text().splitlines()
        if len(line.strip()) > 60 and "unmerged" in line
    )

    # Vacuity floor, both directions: the needle must be real docs prose, and must not have
    # crept into any corpus-eligible source file — otherwise a green says nothing about docs/.
    assert needle in doc.read_text(), needle
    assert needle not in Path(__file__).read_text(), "the needle must not be a literal here"

    assert real_corpus.find(needle) == ([], []), "docs/ leaked into the evidence corpus"


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


def test_a_clone_without_origin_main_is_audited_not_refused(tmp_path: Path) -> None:
    """``actions/checkout`` has no ``origin/main``, and the tool must survive that.

    This is the environment the test above CANNOT see: a developer clone always has the
    remote-tracking ref, so the tool passed locally for its author and exited ``2`` with
    ``INTERNAL ERROR: ... Not a valid object name origin/main`` on every CI run — the entire
    census lost to an environment assumption rather than to any roadmap fact.

    Reproduced the way CI produces it: a repo whose ONLY ref is a detached ``HEAD``. Deleting
    the local branch matters — the first draft of this fixture left ``git init``'s ``main``
    behind, so the resolver landed on ``main`` and the test asserted the wrong tier while
    still exercising a fallback. CI has no local branch either, so a fixture that keeps one
    is not the environment being fixed.
    """
    repo = tmp_path / "detached"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "f.txt").write_text("x\n")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "c"], check=True, env=env)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach", sha], check=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-qD", "main"], check=True)

    # Vacuity floor, both tiers: neither the remote-tracking ref NOR a local branch of that
    # name may exist, or the resolver never reaches the HEAD tier and this test measures a
    # different fallback than the one CI takes.
    assert not _ref_exists("origin/main", repo), "the fixture must lack origin/main"
    assert not _ref_exists("main", repo), "the fixture must lack a local main branch too"

    resolved, note = resolve_ref("origin/main", repo)
    assert resolved == "HEAD", resolved
    assert "does not exist here" in note and "HEAD" in note, note


def test_the_ref_fallback_is_silent_when_the_requested_ref_exists() -> None:
    """The other half: an audit of the real ref must not announce a substitution.

    Without this, ``resolve_ref`` could return a note unconditionally and the test above
    would still pass — and every ordinary run would print a warning about a ref it did in
    fact read.
    """
    assert _ref_exists("HEAD", REPO_ROOT)
    resolved, note = resolve_ref("HEAD", REPO_ROOT)
    assert resolved == "HEAD" and note == "", (resolved, note)


def test_an_explicitly_named_missing_ref_raises_instead_of_falling_back() -> None:
    """The fallback must not become "audit whatever is lying around".

    This is the rail on the fix's own scope, and the fix failed it first: a chain ending in
    ``HEAD`` unconditionally means ``--ref origin/feature-x`` on a clone without that branch
    gets answered with a census of the working checkout — a report that reads entirely normal
    while describing the wrong tree. So the HEAD tier is reachable ONLY for ``DEFAULT_REF``.
    """
    assert not _ref_exists("origin/zzz-no-such-branch-9f3a", REPO_ROOT)
    assert _ref_exists("HEAD", REPO_ROOT), "HEAD must exist, or this proves nothing"
    with pytest.raises(RuntimeError, match="not a valid commit here"):
        resolve_ref("origin/zzz-no-such-branch-9f3a", REPO_ROOT)


def test_the_ref_is_resolved_where_it_is_CONSUMED_not_only_in_main() -> None:
    """A guard above the call only protects the callers that go through it.

    Measured: resolving in ``main`` alone left the CLI green and errored **ten** tests at
    setup, because ``real_census`` calls ``census(ref="origin/main")`` directly and never
    touches ``main``. So the resolution has to sit in ``load_corpus``, which is where both
    git invocations consume the ref.

    Pinned at the source level on purpose. A behavioural version would have to stream the
    real 45 MiB corpus, and it could not distinguish "resolved in load_corpus" from
    "resolved by the caller that happened to run first" — which is the exact confusion that
    produced the bug.
    """
    tree = ast.parse((REPO_ROOT / "tools" / "audit_landed_atoms.py").read_text())
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "load_corpus" in fns and "resolve_ref" in fns

    def calls(node: ast.AST) -> set[str]:
        return {
            c.func.id
            for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        }

    assert "resolve_ref" in calls(fns["load_corpus"]), (
        "load_corpus consumes the ref in two git calls and must resolve it itself; "
        "resolving only in main() is what errored ten tests at setup in CI"
    )
    # Vacuity floor: this must be a real containment test, not a whole-file substring match.
    assert "resolve_ref" not in calls(fns["extract_keys"]), "the AST scan is not per-function"


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


# ===========================================================================
# the wire check — "would deleting the caller be caught?"
# ===========================================================================


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


WIRED_PY = '''\
"""ZZ-9 deliverable: two side-effect helpers."""

CALLS: list[str] = []


def ping(n: int = 0) -> str:
    CALLS.append(f"ping{n}")
    return "ping"


def pong() -> str:
    CALLS.append("pong")
    return "pong"
'''

HOST_PY = '''\
"""ZZ-9: the boot block that wires the helpers in."""

from pkg.wired import ping, pong


def boot() -> None:
    ping()
    # A MULTI-LINE call site. Neutralising it by truncation instead of by whole-statement
    # replacement breaks the parse, and a parse break is a collection error — evidence about
    # the mutation, not about the wire.
    ping(
        n=2,
    )
    pong()
'''

# Asserts that boot() reaches `ping`. Deleting the ping() calls MUST red this.
TEST_RAILED = """\
import ast
import inspect
import textwrap

from pkg import host


def test_boot_reaches_ping() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(host.boot)))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "ping" in called, "boot no longer reaches the helper"
    assert called, "vacuity floor: the AST walk saw no calls at all"
"""

# Exercises the mechanism and never asserts its USE. Deleting the pong() call stays green —
# which is precisely the shape of APE-3's real defect.
TEST_UNRAILED = """\
from pkg import wired


def test_pong_returns_its_name() -> None:
    assert wired.pong() == "pong"
"""


@pytest.fixture
def wire_repo(tmp_path: Path) -> Path:
    """A committed two-file repo with one railed wire and one unrailed wire.

    Hermetic on purpose: the real APE-3 check costs minutes and mutates ``src/``, so it can
    never be a CI test. This fixture is the same machinery on four files.
    """
    root = tmp_path / "wirerepo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "wired.py").write_text(WIRED_PY)
    (root / "src" / "pkg" / "host.py").write_text(HOST_PY)
    (root / "tests" / "test_railed.py").write_text(TEST_RAILED)
    (root / "tests" / "test_unrailed.py").write_text(TEST_UNRAILED)
    # Its own ini, so the child pytest does not inherit this repo's addopts (-n auto, --cov,
    # --timeout) and does not resolve its rootdir to the real project.
    #
    # `--color=yes` is here DELIBERATELY, and it is the reason this fixture is trustworthy. A
    # plain ini disables colour into a pipe, and the first version of this fixture therefore
    # passed while the real APE-3 run silently mis-parsed every red: this project's own addopts
    # carry `--color=yes`, pytest emitted `\x1b[31mFAILED\x1b[0m tests/…`, the node-id parser
    # matched nothing and two railed wires came back UNRAILED. The fixture must reproduce the
    # configuration the tool actually meets, or it certifies the wrong thing.
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\naddopts = --color=yes\n")
    _run_git(root, "init", "-q")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-q", "-m", "base", "--no-verify")
    return root


ZZ9 = Atom(
    id="ZZ-9",
    title="two side-effect helpers wired into boot",
    status="todo",
    scope="src/pkg/wired.py",
    done_when="boot pings and pongs",
    deps=[],
    plan_code="ZZ",
    plan_name="ZZ-PLAN",
    plan_status="in_progress",
)


def test_the_check_separates_a_railed_wire_from_an_unrailed_one(wire_repo: Path) -> None:
    """THE rail. Both wires exist and are named; only one is asserted by a test.

    This is the whole thesis in four files. If the mutation silently stops landing, ``ping``
    flips to UNRAILED and this reds. If the mutation starts breaking the parse, both become
    REFUSED (collection errors) and this reds. If the selection stops reaching the asserting
    file, ``ping`` flips to UNRAILED and this reds. A check that cannot tell these two apart
    looks exactly like a repo with no unrailed wires, which is the defect being hunted.
    """
    report = check_atom_wires(wire_repo, ZZ9, max_wires=4, cap=8, timeout=180, with_cov=False)
    assert not report.refusal, report.refusal
    by_symbol = {c.wire.symbol: c for c in report.checks}
    assert set(by_symbol) == {"ping", "pong"}, sorted(by_symbol)

    ping = by_symbol["ping"]
    assert ping.status == WIRE_RAILED, f"{ping.status}: {ping.reason}"
    assert any("test_boot_reaches_ping" in node for node in ping.caught_by), ping.caught_by

    pong = by_symbol["pong"]
    assert pong.status == WIRE_UNRAILED, f"{pong.status}: {pong.reason}"
    assert not pong.caught_by

    # and the tree survives byte-identical
    assert (wire_repo / "src" / "pkg" / "host.py").read_text() == HOST_PY
    assert not subprocess.run(
        ["git", "status", "--porcelain"], cwd=wire_repo, capture_output=True, text=True
    ).stdout.strip()


def test_the_check_leaves_no_snapshot_directory_behind(wire_repo: Path) -> None:
    """ "Leaves nothing behind" has to mean the filesystem, not just the git tree.

    Measured: 22 ``wirecheck-snap-*`` directories accumulated in ``$TMPDIR`` over one session —
    several still holding a byte copy of a source file — because ``check_atom_wires`` created a
    Snapshot per atom and only the CLI ever disposed of one.
    """
    before = set(Path(tempfile.gettempdir()).glob("wirecheck-snap-*"))
    report = check_atom_wires(wire_repo, ZZ9, max_wires=1, cap=8, timeout=180, with_cov=False)
    assert report.checks, report.refusal
    leaked = set(Path(tempfile.gettempdir()).glob("wirecheck-snap-*")) - before
    assert leaked == set(), f"snapshot directories survived the run: {sorted(leaked)}"


def test_a_multi_line_call_site_is_replaced_whole_and_still_parses(wire_repo: Path) -> None:
    """Truncating a multi-line call yields a collection error, which proves nothing.

    ``ping`` has two sites, one of them three lines long. Both must become ``pass`` markers,
    the module must still parse, and the restore must be byte-exact.
    """
    host = wire_repo / "src" / "pkg" / "host.py"
    wires = {w.symbol: w for w in find_wires(ZZ9, wire_repo, ["src/pkg/host.py"])}
    # `ping` is imported into host from wired, so the DEFINING module has to be in scope too
    wires = {
        w.symbol: w for w in find_wires(ZZ9, wire_repo, ["src/pkg/host.py", "src/pkg/wired.py"])
    }
    ping = wires["ping"]
    assert len(ping.sites) == 2, [s.where for s in ping.sites]
    assert any(s.end_lineno > s.lineno for s in ping.sites), "the multi-line site was lost"

    snap = Snapshot(wire_repo)
    try:
        mutate(wire_repo, ping, snap)
        mutated = host.read_text()
        assert mutated.count(MUT_MARK) == 2, mutated
        ast.parse(mutated)  # a SyntaxError here is the exact failure being guarded
        assert "ping()" not in mutated and "n=2," not in mutated
    finally:
        assert snap.restore() == []
        snap.discard()
    assert host.read_text() == HOST_PY, "restore was not byte-exact"


def test_it_refuses_a_dirty_target_file_instead_of_mutating_over_the_edit(
    wire_repo: Path,
) -> None:
    """Someone else's uncommitted edit must stop the check, not be swept into the diff."""
    host = wire_repo / "src" / "pkg" / "host.py"
    host.write_text(HOST_PY + "\n# a sibling's work in progress\n")
    report = check_atom_wires(wire_repo, ZZ9, max_wires=4, cap=8, timeout=60, with_cov=False)
    assert report.checks, report.refusal
    assert {c.status for c in report.checks} == {WIRE_REFUSED}
    assert all("dirty" in c.reason for c in report.checks), [c.reason for c in report.checks]
    assert host.read_text().endswith("# a sibling's work in progress\n")


def test_it_refuses_when_the_baseline_is_not_green(wire_repo: Path) -> None:
    """A red before the mutation makes a red after it unattributable."""
    (wire_repo / "tests" / "test_railed.py").write_text(
        TEST_RAILED + "\n\ndef test_already_red() -> None:\n    assert False, 'pre-existing'\n"
    )
    _run_git(wire_repo, "add", "-A")
    _run_git(wire_repo, "commit", "-q", "-m", "red", "--no-verify")
    report = check_atom_wires(wire_repo, ZZ9, max_wires=1, cap=8, timeout=180, with_cov=False)
    (chk,) = report.checks
    assert chk.status == WIRE_REFUSED, chk.reason
    assert "baseline is not green" in chk.reason


def test_it_refuses_an_empty_selection_rather_than_calling_the_wire_railed(
    wire_repo: Path,
) -> None:
    """A selection that reaches nothing must never read as "the suite catches it".

    A vacuous run is the exact failure this whole tool exists to remove, so the safe-looking
    outcome (green ⇒ railed) is forbidden by construction.
    """
    for name in ("test_railed.py", "test_unrailed.py"):
        (wire_repo / "tests" / name).unlink()
    (wire_repo / "tests" / "test_elsewhere.py").write_text(
        "def test_nothing() -> None:\n    pass\n"
    )
    _run_git(wire_repo, "add", "-A")
    _run_git(wire_repo, "commit", "-q", "-m", "no relevant tests", "--no-verify")
    report = check_atom_wires(wire_repo, ZZ9, max_wires=2, cap=8, timeout=60, with_cov=False)
    assert report.checks
    for chk in report.checks:
        assert chk.status == WIRE_REFUSED, chk.reason
        assert "scored above zero" in chk.reason


def test_two_calls_on_one_line_are_refused_not_guessed(tmp_path: Path) -> None:
    """``f(); g()`` — deleting the line would take the sibling statement with it."""
    root = tmp_path / "r"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "m.py").write_text(
        "# ZZ-9\ndef a() -> None:\n    pass\n\n\ndef b() -> None:\n    pass\n\n\n"
        "def boot() -> None:\n    a(); b()\n"
    )
    wires = {w.symbol: w for w in find_wires(ZZ9, root, ["src/pkg/m.py"])}
    snap = Snapshot(root)
    before = (root / "src" / "pkg" / "m.py").read_text()
    try:
        with pytest.raises(WireRefusal, match="shares its line"):
            mutate(root, wires["a"], snap)
    finally:
        snap.restore()
        snap.discard()
    assert (root / "src" / "pkg" / "m.py").read_text() == before


def test_an_unattributable_red_refuses_instead_of_reading_as_unrailed() -> None:
    """The floor under red DETECTION — the bug this check shipped with for one run.

    A summary the parser cannot read is indistinguishable from a green, and "green" here means
    UNRAILED, which is the false-comfort answer. So a counts line that says a test failed while
    no node id was attributed must refuse. Both directions are asserted, because a floor that
    fires on everything is as useless as one that fires on nothing.
    """
    ansi = RunResult(
        returncode=1,
        counts={"failed": 2, "passed": 262},
        failed_ids=(),  # what the ANSI-blind parser produced on the real run
        error_ids=(),
        seconds=81.0,
        timed_out=False,
        tail="",
    )
    assert ansi.attribution_failed
    assert not ansi.green

    parsed = replace(ansi, failed_ids=("tests/test_x.py::test_y",))
    assert not parsed.attribution_failed
    clean = replace(ansi, returncode=0, counts={"passed": 264}, failed_ids=())
    assert not clean.attribution_failed and clean.green


def test_the_run_parser_reads_colourised_pytest_output() -> None:
    """Directly, on the bytes: colour must not hide a node id."""
    coloured = (
        "\x1b[31mFAILED\x1b[0m tests/test_app_worker_runtime.py::test_boot_starts_it"
        " - AssertionError: boom\n"
        "\x1b[31m== \x1b[31m\x1b[1m2 failed\x1b[0m, \x1b[32m262 passed\x1b[0m in 81s ==\x1b[0m\n"
    )
    stripped = RE_ANSI.sub("", coloured)
    ids = tuple(
        ln.split(" ", 1)[1].split(" - ")[0].strip()
        for ln in stripped.splitlines()
        if ln.startswith("FAILED ")
    )
    assert ids == ("tests/test_app_worker_runtime.py::test_boot_starts_it",), ids
    # vacuity floor: the UNSTRIPPED text is exactly what failed to parse
    assert not [ln for ln in coloured.splitlines() if ln.startswith("FAILED ")]


def test_a_snapshot_restore_reports_a_hash_mismatch_instead_of_claiming_success(
    wire_repo: Path,
) -> None:
    """The restore is *verified*, not assumed — otherwise "restored" is just a hope."""
    host = wire_repo / "src" / "pkg" / "host.py"
    snap = Snapshot(wire_repo)
    try:
        snap.take("src/pkg/host.py")
        host.write_text("# mutated\n")
        # corrupt the backup so the copy-back cannot reproduce the recorded hash
        (snap.dir / "src__pkg__host.py").write_text("# not the original\n")
        broken = snap.restore()
        assert broken == ["src/pkg/host.py"], broken
        assert snap.failures == ["src/pkg/host.py"]
        # and the backup is deliberately KEPT so a human can recover by hand
        snap.discard()
        assert snap.dir.is_dir(), "a failed restore must not delete the only surviving copy"
    finally:
        snap.disarm()
        shutil.rmtree(snap.dir, ignore_errors=True)  # this test is the one that must clean up
        host.write_text(HOST_PY)


def test_arming_a_snapshot_does_not_leave_its_signal_handlers_installed(
    wire_repo: Path,
) -> None:
    """Arming is process-global; under pytest a leaked handler outlives the test."""
    import signal

    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    snap = Snapshot(wire_repo)
    snap.take("src/pkg/host.py")
    assert signal.getsignal(signal.SIGINT) is not before[signal.SIGINT]
    snap.restore()
    snap.discard()
    assert {s: signal.getsignal(s) for s in before} == before


# ---------------------------------------------------------------------------
# the locator, against the real repo (no mutation, no pytest — cheap)
# ---------------------------------------------------------------------------


def test_the_locator_reaches_ape3s_two_unrailed_wires_from_the_atom_alone() -> None:
    """The census's biggest miss must stay reachable from the atom id alone.

    ``APE-3``'s prose names neither ``start_worker_watchdog`` nor ``_stop_worker`` — its
    done_when says "survives a crash (watchdog)" and "uninstall leaves no orphan worker". So
    key extraction cannot reach either wire, and the derivation that can is the atom-id
    annotation in ``src/`` (``# APE-3: the same sweep shape for app background WORKERS``).
    Both must rank in the top few, or the check would need the answer handed to it.
    """
    atom = next(a for a in load_atoms() if a.id == "APE-3")
    assert "start_worker_watchdog" not in atom.prose
    assert "_stop_worker" not in atom.prose

    modules = annotated_modules("APE-3", REPO_ROOT)
    assert "src/personalclaw/apps/worker_runtime.py" in modules
    assert "src/personalclaw/providers/loader.py" in modules
    assert "src/personalclaw/apps/app_manager.py" in modules

    wires = find_wires(atom, REPO_ROOT, modules)
    ranked = [w.name for w in wires]
    assert "worker_runtime::start_worker_watchdog" in ranked, ranked[:8]
    assert "app_manager::_stop_worker" in ranked, ranked[:8]
    assert ranked.index("worker_runtime::start_worker_watchdog") < 4, ranked[:8]
    assert ranked.index("app_manager::_stop_worker") < 4, ranked[:8]

    # Vacuity floor: the locator is discriminating, not returning everything it parsed. A
    # resolver that matched `ast.Attribute` callees by bare name alone reported 572 sites for
    # this atom, including every `x.update()`, `t.start()` and `time.sleep()` in the tree.
    assert len(wires) < 60, len(wires)
    assert not any(w.symbol in {"update", "start", "stop", "sleep", "wait"} for w in wires)


def test_a_recursive_call_is_not_a_wire_into_the_symbol(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "m.py").write_text(
        "# ZZ-9\ndef walk(n: int) -> None:\n    if n:\n        walk(n - 1)\n"
    )
    assert find_wires(ZZ9, root, ["src/pkg/m.py"]) == []


def test_the_selection_reaches_the_atoms_own_suite_inside_a_tight_cap() -> None:
    """The suite that drives the seam must survive the cap, or the check measures neighbours.

    Note WHICH tier carries it here, because it is the finding. When this test was written **no
    rail on ``main`` named ``start_worker_watchdog``**, so the symbol tier scored zero and
    ``test_app_worker_runtime`` was reached by the atom-id tier alone. ``APE-3`` then landed the
    missing rail, so the symbol tier is live now and BOTH tiers reach the suite — the assertion
    below is the one that forced this docstring to be rewritten instead of quietly drifting.

    Either way the property is the same: an atom with neither a symbol mention nor an id mention
    in its tests is one this selection cannot aim.

    (This file names the symbol too — as the locator's ground truth, not as a rail on the wire
    — which is exactly why ``SELECTION_EXCLUDE`` drops it from every selection.)
    """
    atom = next(a for a in load_atoms() if a.id == "APE-3")
    modules = annotated_modules("APE-3", REPO_ROOT)
    wire = next(
        w for w in find_wires(atom, REPO_ROOT, modules) if w.symbol == "start_worker_watchdog"
    )
    index = _test_index(REPO_ROOT)
    assert len(index) > 200, len(index)
    assert "tests/test_audit_landed_atoms.py" not in index, "the check must not select itself"
    # The symbol tier is live: APE-3's rail names the symbol, and it is the atom's own suite that
    # does so. Asserting WHICH file carries it keeps this a premise rail rather than a tautology —
    # if the rail is ever deleted the symbol tier silently reverts to scoring zero, and the
    # selection would then depend entirely on the atom-id tier again.
    naming = [rel for rel, (text, _) in index.items() if wire.symbol in text]
    assert naming == ["tests/test_app_worker_runtime.py"], (
        "the symbol tier's only rail moved or vanished — this test's premise (APE-3's rail makes "
        f"the symbol tier live) is stale and the docstring must change: {naming}"
    )
    selection = select_tests(wire, atom, index, cap=6)
    assert "tests/test_app_worker_runtime.py" in selection.files, selection.files
    assert selection.total_test_files == len(index)
    # and it says what it left out, because a green over 6 of 1000 files is not a green
    assert selection.total_test_files - len(selection.files) > 100


def test_a_symbol_naming_file_outranks_the_module_importing_crowd() -> None:
    """Weighting is the difference between finding the rail and burying it under the cap.

    ``app_manager`` appears in ~40 real test files. Scoring "imports the module" as highly as
    "names the symbol" pushes the one file that could hold a textual rail below three dozen
    neighbours, and a cap of 6 then cuts it — measured while building this, on APE-3.

    Synthetic on purpose: this needs a symbol appearing in ~40 files to have anything to
    outrank, and no real wire has that shape. (When this was written the real APE-3 wires had
    no symbol-naming test on ``main`` either, so the tier could not be exercised against the
    repo at all; ``APE-3`` has since landed that rail, but it is one file, not forty.)
    """
    wire = Wire(
        module="pkg.wired",
        symbol="zzq_unique_symbol",
        def_path="src/pkg/wired.py",
        sites=(),
        relevance=0.0,
        cross_module=True,
        annotated=False,
    )
    index: dict[str, tuple[str, set[str]]] = {
        "tests/test_the_only_rail.py": ("asserts zzq_unique_symbol is reached", set()),
    }
    for i in range(40):  # the crowd: each merely imports the module
        index[f"tests/test_crowd_{i:02d}.py"] = ("nothing relevant", {"pkg.wired"})

    selection = select_tests(wire, ZZ9, index, cap=6)
    assert selection.files[0] == "tests/test_the_only_rail.py", selection.files
    assert len(selection.cut) == 35, len(selection.cut)


def test_the_destructive_path_is_off_unless_the_flag_is_typed() -> None:
    """``--check-wires`` mutates source. It must never be reachable from a default run."""
    proc = subprocess.run(
        [sys.executable, TOOL, "--help"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert "--check-wires" in proc.stdout
    assert "DESTRUCTIVE" in proc.stdout
    tracked = subprocess.run(
        ["git", "grep", "-l", MUT_MARK, "--", "src/", "tests/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout.strip() == "", f"a mutation marker was committed: {tracked.stdout}"
