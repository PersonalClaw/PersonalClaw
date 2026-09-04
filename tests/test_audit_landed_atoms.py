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
    split_caveats,
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


def test_a_cross_reference_declares_no_verdict_at_all() -> None:
    """`DCU-3` inherited `DCU-2`'s "flip it when the PR lands" from a body mention.

    The FLIP half was already asymmetric. The GATED half used to hold too, on the reasoning that
    a cross-reference may keep an atom out of the flippable bucket even if it may not put it in.
    Measured cost of that asymmetry: ``DCU-3``, ``EI-2`` and ``LV-7`` all read LANDED-BUT-GATED
    — the *landed* half asserted on a sibling's paperwork — while ``macos_driver.py`` and a
    ``docker`` sandbox provider are simply not on the ref. Both halves now return ``NO_SIGNAL``.
    """
    hits = [LogHit(LogVerdict.FLIP, 5, "…DCU-2 COMPLETE… mentions DCU-3…", "P.md", headline=False)]
    assert decide_log(hits, own_plan_file="P.md")[0] == LogVerdict.NONE
    gated = [LogHit(LogVerdict.GATED, 5, "blocked", "P.md", headline=False)]
    assert decide_log(gated, own_plan_file="P.md")[0] == LogVerdict.NONE
    # VACUITY: the same GATED entry, this time with the atom as its subject, must still gate —
    # or this passes merely because decide_log stopped returning anything.
    own = [LogHit(LogVerdict.GATED, 5, "blocked", "P.md", headline=True)]
    assert decide_log(own, own_plan_file="P.md")[0] == LogVerdict.GATED


def test_subject_is_a_precondition_and_last_wins_only_orders_within_it() -> None:
    """The order between the two precedence rules, which used to be undefined.

    ``ES-7``'s ruling sat 146 lines below the entry that outranked it, so "headline beats body"
    and "last entry wins" could disagree by a whole section. They no longer can: a
    cross-reference is not a late ruling to be weighed, it is not a ruling.
    """
    hits = [
        LogHit(LogVerdict.PARTIAL, 100, "own entry", "P.md", headline=True),
        LogHit(LogVerdict.GATED, 9000, "a neighbour's entry, much later", "P.md", headline=False),
    ]
    verdict, hit = decide_log(hits, own_plan_file="P.md")
    assert verdict == LogVerdict.PARTIAL and hit is not None and hit.position == 100
    # VACUITY: make the late entry a subject entry and last-wins takes over immediately.
    later = [hits[0], replace(hits[1], headline=True)]
    assert decide_log(later, own_plan_file="P.md")[0] == LogVerdict.GATED


_SIBLING_ENTRY = (
    "## Execution log\n\n"
    "- **2026-08-24 — `ZZ-2` DONE (composition, tool surface, thin shim), except the one\n"
    "  clause that needs `ZZ-1`.** Shipped the chain; `ZZ-1`'s driver is the missing half and\n"
    "  the atom is therefore BLOCKED on it. `make_widget` is called from the dispatch.\n"
)


def test_an_id_in_a_headline_is_not_automatically_that_entrys_subject(tmp_path: Path) -> None:
    """Measured: ``DCU-3`` was gated by ``DCU-4``'s DONE entry, which names it in its headline.

    "`DCU-4` DONE (…) except the one clause that needs `DCU-3`" is a ruling on DCU-4 and a
    statement of what DCU-4 is waiting for. Attributing it to both made ``DCU-3`` LANDED-BUT-
    GATED while ``computer_use/`` holds no ``macos_driver.py``, ``macos_ffi.py`` or ``types.py``.
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "ZZ-PLAN.md").write_text(_SIBLING_ENTRY)
    hits = scan_plan_logs(plans)

    assert [h.headline for h in hits["ZZ-2"]] == [True], "the first id IS the subject"
    assert hits["ZZ-1"] and not any(h.headline for h in hits["ZZ-1"]), "ZZ-1 is a cross-reference"
    assert _bucket_for_zz1(plans).bucket == UNKNOWN  # type: ignore[attr-defined]

    # VACUITY, two ways. Naming ZZ-1 FIRST must gate it, or the fixture never carried a gate;
    # and the sibling ZZ-2 must still be gated by its own entry, or subject resolution is
    # returning nothing at all.
    (plans / "ZZ-PLAN.md").write_text(_SIBLING_ENTRY.replace("`ZZ-2`", "`ZZ-1`", 1))
    swapped = _bucket_for_zz1(plans)
    assert swapped.bucket == GATED, swapped.why  # type: ignore[attr-defined]


def test_a_bare_subject_outranks_a_backticked_id_later_in_the_headline(tmp_path: Path) -> None:
    """``CA-7``'s own ruling opens with its id UNBACKTICKED, then backticks a sibling.

    "**CA-7 PARTIAL — the atom stays ``todo``.**" … "for the same reason ``CA-6``'s log already
    recorded against ``CA-7``/``CA-8``" — all within the 260-char headline. Reading only
    backticks handed CA-7's own PARTIAL to CA-6 and left CA-7 with no verdict, which is the
    mirror image of the defect this fix exists to close. Subject-first is the rule; backticking
    is typography.
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    entry = (
        "## Execution log\n\n"
        "- **{lead} PARTIAL — the atom stays `todo`.** The native-client half is built, tested\n"
        "  and gated; the two `done_when` clauses that need a real tunnel are not observed here,\n"
        "  for the same reason `ZZ-2`'s log already recorded against `ZZ-1`.\n"
    )
    (plans / "ZZ-PLAN.md").write_text(entry.format(lead="ZZ-1"))
    hits = scan_plan_logs(plans)
    assert [h.headline for h in hits["ZZ-1"]] == [True]
    assert not any(h.headline for h in hits["ZZ-2"]), "the backticked sibling is not the subject"
    gated = _bucket_for_zz1(plans)
    assert gated.bucket == GATED, gated.why  # type: ignore[attr-defined]

    # VACUITY: drop ZZ-1 from the headline entirely and it must lose the verdict again, or this
    # passes on the body mention rather than on the bare subject.
    (plans / "ZZ-PLAN.md").write_text(entry.format(lead="The native client"))
    assert not any(h.headline for h in scan_plan_logs(plans).get("ZZ-1", []))
    assert _bucket_for_zz1(plans).bucket == UNKNOWN  # type: ignore[attr-defined]


def test_a_bracketed_entry_tag_declares_the_subject_without_backticks(tmp_path: Path) -> None:
    """``- [2026-08-23][ES-7]`` was unreadable: ``ENTRY_START`` splits on it, ``MENTION`` cannot.

    Measured on ``origin/main``: ``ES-7`` is written that way **fifteen** times in its own plan
    and is backticked exactly ONCE — inside the ``[harvest]`` entry that says of itself "Not an
    atom of its own". So its entire verdict came from a cross-reference in a neighbour, and
    every ruling it ever wrote about itself was invisible.
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    tagged = (
        "## Execution log\n\n"
        "- [2026-08-24][ZZ-1 §3.3] **The filter — the gap above is now closed in INPUTS. Atom\n"
        "  still `todo`** (§3.3's plural replay remains, below). `make_widget` returns a\n"
        "  `WidgetThing` and the end-to-end test drives every link.\n"
    )
    (plans / "ZZ-PLAN.md").write_text(tagged)
    hits = scan_plan_logs(plans)
    assert hits["ZZ-1"] and all(h.headline for h in hits["ZZ-1"]), "the tag is the subject"
    gated = _bucket_for_zz1(plans)
    assert gated.bucket == GATED, gated.why  # type: ignore[attr-defined]

    # VACUITY: strip the tag to an untagged date and the very same prose attributes to nobody,
    # which is the state this test exists to end.
    (plans / "ZZ-PLAN.md").write_text(tagged.replace("[2026-08-24][ZZ-1 §3.3]", "[2026-08-24]"))
    assert "ZZ-1" not in scan_plan_logs(plans)


def test_the_still_todo_marker_is_load_bearing_on_its_own(tmp_path: Path) -> None:
    """The one ``PARTIAL_PATTERNS`` member this fix adds, pinned by itself.

    Needed because ``ES-7``'s ruling entry states its verdict as "Atom still ``todo``" and
    nothing else — no "PARTIAL", no "unmet" — so without this marker the deciding entry carried
    no verdict and an earlier one that ES-7's own log supersedes ruled instead. Deliberately
    ``still`` and not ``stays``: "Atom stays ``todo`` **only because this code is unmerged**" is
    the canonical FLIP phrase, and PARTIAL is tested before FLIP from the headline.
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    entry = (
        "## Execution log\n\n"
        "- [2026-08-24][ZZ-1] **The filter — the gap above is now closed in INPUTS. Atom\n"
        "  {verdict}** `make_widget` returns a `WidgetThing`.\n"
    )
    (plans / "ZZ-PLAN.md").write_text(entry.format(verdict="still `todo`."))
    assert _bucket_for_zz1(plans).bucket == GATED  # type: ignore[attr-defined]

    # VACUITY: the same entry with no verdict phrase must classify as no signal at all.
    (plans / "ZZ-PLAN.md").write_text(entry.format(verdict="shipped in one commit."))
    assert "ZZ-1" not in scan_plan_logs(plans)

    # and the FLIP phrase it is deliberately narrower than must survive intact
    (plans / "ZZ-PLAN.md").write_text(
        entry.format(verdict="stays `todo` only because this code is unmerged.")
    )
    assert _bucket_for_zz1(plans).bucket == CLEAN  # type: ignore[attr-defined]


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
# verdict precedence — a flip phrase is not the last word
# ---------------------------------------------------------------------------

# One entry routinely carries a flip phrase AND a reason not to flip, because it QUOTES the
# entry it is overturning. `{ruling}` is the only thing that differs between the two runs
# below, so the assertion is about precedence and nothing else.
_REAUDIT_ENTRY = (
    "## Execution log\n\n"
    "- **2026-08-24 — `ZZ-1` re-audited against `origin/main`: two thirds are LANDED AND\n"
    "  LIVE; the last third is on main but INERT. {ruling}** The 2026-08-23 entry above says\n"
    '  "complete in code … atom stays `todo` only because this code is\n'
    '  unmerged"; the code is now merged and that reading does not survive the merge.\n'
)
_REFUSED = "The atom is PARTIALLY satisfied and must NOT be flipped `done` yet."
_ACCEPTED = "The atom is COMPLETE."


def _bucket_for_zz1(plans: Path) -> object:
    """Drive the whole path a human acts on: split -> classify -> attribute -> bucket.

    Asserting ``_verdict_for`` in isolation is necessary but not sufficient — the bucket is
    what the tool prints as a recommendation, so the bucket is what the test pins. Evidence is
    held STRONG and constant so the only free variable is the log.
    """
    atom = make_atom()
    keys = extract_keys(atom)
    probe(keys, make_corpus({"src/widgets/thing.py": "def make_widget(): return WidgetThing()"}))
    return classify(atom, keys, scan_plan_logs(plans).get("ZZ-1", []))


def test_a_refusal_to_flip_keeps_an_atom_out_of_the_just_flip_it_bucket(tmp_path: Path) -> None:
    """Measured on ``origin/main`` at ``fc597af4``: FLIP tested first inverted the tool's own
    one recommendation. ``--bucket clean`` returned exactly one atom, ``MRT-5``, "log says
    complete-but-unmerged" — while the excerpt printed beside it read "The atom is PARTIALLY
    satisfied and **must NOT be flipped ``done`` yet**". The flip phrase it short-circuited on
    was 306 chars in, inside a quotation of the entry being overturned.

    Note that ``\\bpartial\\b`` does not rescue this: the entry writes "PARTIALLY", which the
    trailing word boundary rejects. The refusal needs its own pattern set.
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "ZZ-PLAN.md"

    plan.write_text(_REAUDIT_ENTRY.format(ruling=_REFUSED))
    refused = _bucket_for_zz1(plans)
    assert refused.bucket == GATED, refused.why  # type: ignore[attr-defined]
    assert refused.bucket != CLEAN  # type: ignore[attr-defined]

    # VACUITY: the fixture has to be *able* to reach CLEAN, or "not CLEAN" proves nothing — a
    # typo'd id, a missing `## Execution log`, or absent evidence would each read as clean-free
    # while measuring nothing. Same entry, same quoted flip phrase, refusal sentence dropped.
    plan.write_text(_REAUDIT_ENTRY.format(ruling=_ACCEPTED))
    assert _bucket_for_zz1(plans).bucket == CLEAN  # type: ignore[attr-defined]


# `PCS-7`'s real entry, reduced: a headline that unambiguously declares the flip, and 6810
# chars later the word "unmeasured" about a DIFFERENT surface it explicitly left alone.
_FLIP_HEADLINE = (
    "## Execution log\n\n"
    "- **2026-08-22 — `ZZ-1`: the numbers ship AND soul guardrail 4 is measured. {tail1}Atom\n"
    "  stays `todo` only because this code is unmerged**; flip it when the PR lands.\n"
    "  {tail2}\n"
)
_FILLER = (
    "The cache-hit ratio the dashboard reports is a different surface and it belongs to "
    "whoever owns the context-percent tile; it is recorded here rather than widened, "
    "because widening it now would pull an unrelated plan into this session's scope. "
)


def test_a_weak_keyword_in_the_body_does_not_overturn_a_headline_flip(tmp_path: Path) -> None:
    """Blanket inversion was measured wrong, so the inversion is scoped to the headline.

    ``GATED``/``PARTIAL`` are weak keyword sets. Letting them win from anywhere in a
    multi-thousand-character entry scored ``PCS-7`` — whose headline says "the numbers ship AND
    soul guardrail 4 is measured … flip it when the PR lands" — as PARTIAL, off the single word
    "unmeasured" in a body clause about someone else's surface. That tripped the
    ``KNOWN_LANDED`` ground-truth rail in ``self_check``, which is the correct outcome for a
    regression and the reason this narrower rule exists.
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "ZZ-PLAN.md"

    body = _FILLER * 3 + "see `G8`'s honest-unmeasured work, left alone."
    plan.write_text(_FLIP_HEADLINE.format(tail1="", tail2=body))
    assert len(_FLIP_HEADLINE.format(tail1="", tail2=body)) > 900, "filler must clear HEADLINE"
    kept = _bucket_for_zz1(plans)
    assert kept.bucket == CLEAN, kept.why  # type: ignore[attr-defined]

    # VACUITY: the same word in the HEADLINE must overturn the very same flip phrase, or the
    # test above is only measuring a pattern set that never matches anything.
    plan.write_text(_FLIP_HEADLINE.format(tail1="One clause is unmeasured. ", tail2=body))
    moved = _bucket_for_zz1(plans)
    assert moved.bucket == GATED, moved.why  # type: ignore[attr-defined]


# The same root cause as `MRT-5` pointing the other way: a phrase matched anywhere in the
# entry, with no notion of whether it qualifies THIS atom's completion or names work declined
# for now. `{clause}` is again the only free variable.
_DEFERRAL_ENTRY = (
    "## Execution log\n\n"
    "- **DONE — `ZZ-1` streams: the events + saved queries + digest handoff.** Three new\n"
    "  modules under `widgets/`; `make_widget` returns a `WidgetThing` and the end-to-end\n"
    "  test drives every link. {clause}\n"
)
_DEFERRED = (
    "The `app:` prefix on a core-contributed source is a naming wart worth an owner call "
    "later; it is not worth a fourth vocabulary now."
)
_STANDING = (
    "The `app:` prefix on a core-contributed source needs an owner call before this atom "
    "can close."
)


def test_a_gate_declined_for_now_is_not_read_as_this_atoms_gate(tmp_path: Path) -> None:
    """``WS-7`` was reported "the log names an owner call / BLOCKED" off a deferred nicety.

    The sentence was "a naming wart worth an owner call **later**; it is **not worth** a fourth
    vocabulary now" — declined work, not a gate on the atom. Same defect family as
    ``ENTRY_START``'s bleed, one level finer: intra-entry instead of inter-entry. The bucket a
    false reason produces may still be the safe one, which is exactly why ``decide_log`` already
    records that a right bucket with a false reason is worse than no reason at all.
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "ZZ-PLAN.md"

    plan.write_text(_DEFERRAL_ENTRY.format(clause=_DEFERRED))
    deferred = _bucket_for_zz1(plans)
    assert deferred.bucket == UNKNOWN, deferred.why  # type: ignore[attr-defined]

    # VACUITY: strip the two deferral markers and the very same "owner call" must still gate
    # the atom, or this only proves GATED_PATTERNS never matches the fixture at all.
    plan.write_text(_DEFERRAL_ENTRY.format(clause=_STANDING))
    standing = _bucket_for_zz1(plans)
    assert standing.bucket == GATED, standing.why  # type: ignore[attr-defined]
    assert "owner call" in standing.why  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "marker",
    [
        "The `app:` prefix needs an owner call, tracked as a follow-up.",
        "The `app:` prefix needs an owner call and is worth an atom row of its own.",
        "The `app:` prefix needs an owner call and is worth a separate atom.",
        "The `app:` prefix needs an owner call, but it is not worth the churn today.",
    ],
    ids=["follow-up", "worth-an-atom-row", "worth-a-separate-atom", "not-worth"],
)
def test_every_deferral_marker_is_load_bearing_on_its_own(tmp_path: Path, marker: str) -> None:
    """Each ``DEFERRAL_PATTERNS`` entry must be able to suppress a gate BY ITSELF.

    Measured at integration: neutering four of the five markers left the whole suite green, so
    only ``\\blater\\b`` was pinned and the other four could have been typo'd without any test
    noticing — the "declared but never run" family, applied to the fix's own patterns. Each case
    below carries exactly ONE marker and no other, so the parametrized id names the pattern that
    fails.

    The vacuity floor is the second half: the identical sentence WITHOUT its marker must still
    gate the atom, or this would pass merely because the fixture never triggers
    ``GATED_PATTERNS`` in the first place.
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "ZZ-PLAN.md"

    # Two scope facts this fixture has to respect, both learned by writing it wrong first:
    # (1) the marker must sit in the SAME clause as the gate phrase, because the filter drops a
    #     CLAUSE, so a marker in a neighbouring sentence correctly leaves the gate standing; and
    # (2) the clause must sit BEYOND `HEADLINE` (260 chars), because GATED_PATTERNS is matched
    #     against the headline BEFORE the clause-scoped pass runs. The deferral filter is a
    #     body-only mechanism by construction — a gate named in the headline is never excused.
    pad = "  Background prose that carries no verdict keyword at all. " * 5

    plan.write_text(_DEFERRAL_ENTRY.format(clause=pad + marker))
    assert _bucket_for_zz1(plans).bucket == UNKNOWN  # type: ignore[attr-defined]

    plan.write_text(_DEFERRAL_ENTRY.format(clause=pad + "The `app:` prefix needs an owner call."))
    standing = _bucket_for_zz1(plans)
    assert standing.bucket == GATED, standing.why  # type: ignore[attr-defined]


def test_the_real_ws7_gate_is_its_own_partial_not_the_deferred_naming_wart(
    real_log_hits: dict,
) -> None:
    """``WS-7`` stays out of the clean bucket, but for the reason its own entry states.

    Worth being explicit, because the atom looks complete from the outside (three shipped
    modules, 13 passing tests, a ``**DONE —`` opener): the entry ALSO says, in bold, "**This
    atom is therefore PARTIAL in one respect worth recording: the digest is invocable and fully
    tested, but nothing in the shipped product calls it yet**". That is the module's
    shipped-but-inert shape, which may only move an atom OUT of the clean bucket. So the fix
    corrects the stated reason and must NOT free the atom.
    """
    own = "WATCHED-SOURCES.md"
    hits = [h for h in real_log_hits.get("WS-7", []) if h.plan_file == own]
    assert hits, f"WS-7 has no entry in {own}: the scan or the plan moved, and this test is mute"
    verdict, hit = decide_log(hits, own_plan_file=own)
    assert verdict == LogVerdict.PARTIAL, f"deciding entry: {hit.excerpt[:200] if hit else None}"
    assert verdict != LogVerdict.GATED


# ``test_the_real_inherited_verdicts_are_gone`` retired 2026-09-04 — its last subject graduated.
# It pinned atoms bucketed LANDED-BUT-GATED off a SIBLING's entry with nothing of their own on
# the ref (the bucket a human acts on). Atoms left it by growing a verdict of their own:
# ``DCU-3`` and ``LV-7`` graduated 2026-08-26 (each wrote its own dated entry), and ``EI-2``
# graduated 2026-09-04 — the ``docker`` sandbox provider landed on ``main`` (#2361), the atom
# flipped ``done`` with its own validated-done Execution-log entry, so its verdict is no longer
# inherited from ``EI-8``'s STOP POINT. With no remaining inherited-verdict atom the guard has
# no subject; the census's own ``self_check`` rails (exercised by ``test_self_check_*`` above)
# stay the standing protection against a verdict scored off nothing.


def test_the_real_es7_verdict_comes_from_its_own_tagged_entry(real_log_hits: dict) -> None:
    """``ES-7``'s ruling must come from one of ITS OWN tagged entries.

    The defect this pins: the only backticked mention used to be inside ``[harvest]``'s headline —
    an entry that says of itself "Not an atom of its own" — so ``[harvest]``'s text adjudicated
    ``ES-7``.

    Updated 2026-08-25: closing ES-7's flagged inert-route note shifted that section and handed the
    deciding position to a DIFFERENT ES-7 entry, so the excerpt check was widened to name both known
    rulings.

    **Rewritten 2026-08-26, because widening the enumeration was treating the symptom.** A third
    ES-7 entry landed and displaced both named ones, reddening this test again — the second
    expiry in two days. The enumeration cannot converge: the plan log is append-only, so *which*
    of ES-7's entries `decide_log` picks changes every time a legitimate entry is added, and the
    phrase list needs another member each time. Measured across this file, that is not a one-off:
    of the four real-data rails that call ``decide_log``, **three have now reddened a PR for
    exactly this reason** — ``MRT-5``'s (#2066), this one (#2098), and
    ``test_the_real_inherited_verdicts_are_gone`` (#2101).

    So the positive "which ruling is cited" clause is dropped rather than extended. It never carried
    the defect: the defect is that ``[harvest]``'s text — an entry that says of itself "Not an atom
    of its own" — used to adjudicate ``ES-7``, and that is pinned by the three assertions below,
    which are about the entry's PROVENANCE and do not care which of ES-7's own entries wins. The
    ``verdict == PARTIAL`` check still holds the adjudication itself.
    """
    own = "EVALUATION-SUBSTRATE.md"
    hits = [h for h in real_log_hits.get("ES-7", []) if h.plan_file == own]
    assert len(hits) > 1, f"only {len(hits)} ES-7 entries — the tag scan regressed"
    verdict, hit = decide_log(hits, own_plan_file=own)
    assert hit is not None
    # The durable property: the ruling comes from one of ES-7's OWN tagged entries, never from
    # `[harvest]`. Which of its own entries wins is not asserted — see the docstring.
    assert "[ES-7" in hit.excerpt, hit.excerpt[:200]
    assert "[harvest]" not in hit.excerpt, hit.excerpt[:200]
    assert "Not an atom of its own" not in hit.excerpt, hit.excerpt[:200]
    assert verdict == LogVerdict.PARTIAL


def test_the_real_mrt5_refusal_entry_is_still_not_read_as_a_flip(real_log_hits: dict) -> None:
    """The defect, pinned on the data that produced it rather than on a synthetic string.

    **Re-read 2026-08-26, exactly as the previous revision of this docstring instructed.** It
    said: "if the owner later authors a genuine flip entry for ``MRT-5`` this test fails, and
    the fix is to re-read the new deciding entry — not to loosen the assertion." That happened —
    `MRT-5` was gated and flipped — so the deciding entry is now the flip, and the assertion is
    re-pointed rather than relaxed.

    What still needs pinning on real data is the thing the original defect was about:
    **precedence WITHIN an entry**. The 2026-08-24 re-audit entry quotes the reading it
    overturns, so it contains a flip phrase 306 chars in while its own ruling is a refusal. That
    entry is still in the file and must still classify as a non-flip; if the scanner ever
    short-circuits on the quoted phrase again, this test reds even though the deciding entry is
    now legitimately a flip. The synthetic fixture in
    :func:`test_a_refusal_to_flip_keeps_an_atom_out_of_the_just_flip_it_bucket` owns the
    bucket-level rail; this one owns the real corpus.
    """
    own = "MODEL-ROUTING-TELEMETRY.md"
    hits = [h for h in real_log_hits.get("MRT-5", []) if h.plan_file == own]
    assert hits, f"MRT-5 has no entry in {own}: the scan or the plan moved, and this test is mute"

    # The refusal entry is STILL classified as a non-flip. That is the whole durable property:
    # the entry quotes a flip phrase 306 chars into the reading it overturns, so a scanner that
    # tested FLIP first would mislabel it. Non-vacuous by construction — if the entry is ever
    # dropped from the plan, the first assertion fires instead of the test going quietly mute.
    refusals = [h for h in hits if "must NOT be flipped" in h.excerpt]
    assert refusals, (
        "the 2026-08-24 refusal entry is gone from the plan, so the precedence rail this test "
        "exists for is no longer measured by real data"
    )
    assert all(h.verdict != LogVerdict.FLIP for h in refusals), [
        h.excerpt[:120] for h in refusals if h.verdict == LogVerdict.FLIP
    ]

    # Deliberately NOT asserted: which entry `decide_log` picks. The previous revision pinned
    # the *deciding* entry, and that assertion had a built-in expiry — it holds only while
    # MRT-5's newest entry happens to be the refusal. Appending the flip entry moved the
    # decision and reddened a test whose subject had simply been resolved. A rail that a
    # legitimate later entry breaks is pinning the corpus's shape, not the tool's behaviour;
    # `test_a_refusal_to_flip_keeps_an_atom_out_of_the_just_flip_it_bucket` owns the
    # bucket-level precedence rail on a synthetic fixture, which is where that belongs.


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


def test_an_inertness_note_is_refuted_by_a_call_site_on_the_ref() -> None:
    """A note is a claim about the ref, so the ref gets to answer it.

    Measured: ``DCU-2`` stayed LANDED-BUT-GATED on three past-tense notes all written *after*
    ``DCU-4`` supplied the caller — including one in ``DCU-4``'s own module that says eight lines
    lower "and this module is that caller". The signal is one-directional and has no expiry, so a
    historical narrative pinned the fixed atom permanently.
    """
    atom = make_atom()
    keys = extract_keys(atom)
    caveat = [("src/widgets/notes.py", "ZZ-1 shipped make_widget as a provably inert function")]

    # nothing calls it -> the note stands, and the atom is gated. This is the founding case.
    probe(keys, make_corpus({"src/widgets/thing.py": "def make_widget(): return WidgetThing()"}))
    inert = make_corpus({"src/widgets/thing.py": "def make_widget(): return WidgetThing()"})
    live, refuted = split_caveats(keys, caveat, inert)
    assert (live, refuted) == (caveat, []), "a definition is not a call site"
    flip = [LogHit(LogVerdict.FLIP, 1, "…", "ZZ-PLAN.md", headline=True)]
    assert classify(atom, keys, flip, live, refuted).bucket == GATED

    # a real caller in impl -> the note is refuted and the atom is clean again
    wired = make_corpus(
        {
            "src/widgets/thing.py": "def make_widget(): return WidgetThing()",
            "src/widgets/service.py": "from . import thing\n\ndef go():\n    thing.make_widget()\n",
        }
    )
    keys2 = extract_keys(atom)
    probe(keys2, wired)
    live2, refuted2 = split_caveats(keys2, caveat, wired)
    assert live2 == [] and len(refuted2) == 1
    assert refuted2[0][2] == "src/widgets/service.py calls make_widget()"
    clean = classify(atom, keys2, flip, live2, refuted2)
    assert clean.bucket == CLEAN
    assert "REFUTED on the ref" in clean.why


def test_a_test_only_caller_cannot_refute_an_inertness_note() -> None:
    """The whole point of the signal is "shipped, tested, and nothing in production calls it".

    ``tests/`` is outside ``IMPL_PREFIXES``, so a ratchet that drives the symbol directly — which
    is exactly how these three screens were tested — must not read as the caller it complains
    about being missing.
    """
    atom = make_atom()
    keys = extract_keys(atom)
    corpus = make_corpus(
        {
            "src/widgets/thing.py": "def make_widget(): return WidgetThing()",
            "tests/test_widgets.py": "from widgets.thing import make_widget\n\nmake_widget()\n",
        }
    )
    probe(keys, corpus)
    caveat = [("tests/test_widget_call_sites.py", "ZZ-1's screens have zero production callers")]
    live, refuted = split_caveats(keys, caveat, corpus)
    assert (live, refuted) == (caveat, [])


def test_the_real_dcu2_notes_are_all_refuted_by_dcu4s_dispatch(real_corpus: Corpus) -> None:
    """The defect pinned on the data that produced it, both halves.

    If the owner later removes the dispatch's calls this test fails and the fix is to re-read the
    ref, not to loosen the assertion. ``DCU-2`` is one of the five ``KNOWN_LANDED`` atoms, so a
    regression here also shows up in ``self_check``.
    """
    atoms = {a.id: a for a in load_atoms()}
    keys = extract_keys(atoms["DCU-2"])
    probe(keys, real_corpus)
    notes = scan_code_caveats(real_corpus).get("DCU-2", [])
    assert notes, "no DCU-2 inertness note on the ref: this test is mute"
    live, refuted = split_caveats(keys, notes, real_corpus)
    assert live == [], f"still-live notes: {[w for w, _ in live]}"
    assert any("service.py calls" in proof for _, _, proof in refuted), refuted


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
    if not any(v.atom.id in KNOWN_LANDED for v in verdicts):
        # Every hand-verified atom can flip to done and leave the census selection;
        # re-badge one verdict so the per-atom message path is still exercised.
        verdicts[0] = replace(verdicts[0], atom=replace(verdicts[0].atom, id=KNOWN_LANDED[0]))
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


def test_the_check_leaves_no_snapshot_directory_behind(
    wire_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Leaves nothing behind" has to mean the filesystem, not just the git tree.

    Measured: 22 ``wirecheck-snap-*`` directories accumulated in ``$TMPDIR`` over one session —
    several still holding a byte copy of a source file — because ``check_atom_wires`` created a
    Snapshot per atom and only the CLI ever disposed of one.

    ``$TMPDIR`` is redirected into this test's own directory first, because it is SHARED across
    xdist workers: a sibling worker's in-flight snapshot appeared in the after-minus-before set
    and read as this call's leak. Measured when a change to this file reshuffled the sharding so
    two wire-check tests ran concurrently. Redirecting makes the assertion stronger, not looser —
    the only ``wirecheck-snap-*`` that can appear here is one this call created.
    """
    sandbox = tmp_path / "tmpdir"
    sandbox.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(sandbox))
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
