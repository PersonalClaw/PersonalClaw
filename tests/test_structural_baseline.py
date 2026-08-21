"""Shrink-only STRUCTURAL ratchets over the committed baseline (PLATFORM-HARDENING-FLOORS PHF-14).

``structural-baseline.json`` is a GENERATED census (by
``scripts/generate_structural_baseline.py``) of three structural properties of production
``src/personalclaw`` — the shape of the tree rather than its behaviour:

  * **per-file size ceiling + a shrink-only band population** — no file may reach 6000 lines
    (one 1000-line step above the measured max of 5447), and the COUNT of files at or above the
    2800-line watch band (9) may only shrink. Growth WITHIN the band, below the ceiling, is
    deliberately not a violation: decay is a new 2,500-line module appearing, not three lines
    added to the file that by construction gets maintained most.
  * **module-boundary / import direction** — a declared layer order (the ledger is a leaf;
    core must not import the HTTP surface; core must not import its own published SDK facade).
    Each file's upward-edge count may only shrink.
  * **duplicate-implementation counter** — the families this codebase has repeatedly
    re-derived: the HTTP error envelope (PL-8 deleted 13 clones; 12 survive), verdict types
    (WF2LOO-16 reconciled four dialects; 24 verdict-shaped types remain outside the canonical
    module), and durable write (DAS-9's mkstemp+rename bypass; 5 sites).

None of those three defects fails a unit test — the code works. Only a census of the tree's
shape sees them, and only a ratchet makes the next one expensive.

This suite is the ratchet that keeps the census honest. It regenerates in-memory and asserts
every counter **may only shrink**:

  * A counter that ROSE — a new giant, a new upward import, a new re-derivation — reds CI,
    naming the file and the specific violation.
  * A DECREASE is welcome (a split, an inversion, a deletion). The ratchets do NOT demand
    equality and do NOT require the numbers to go down — only that they never go up. This is
    why they shipped at the MEASURED population and NOT at zero: a never-run gate given teeth
    at zero reds every pre-existing giant, upward import and clone at once (an outage). That
    is ``PHF-6``'s own ruling, and this atom exists partly to avoid repeating it.
  * And a ratchet that reds ORDINARY MAINTENANCE is an outage too, in slow motion — it teaches
    everyone to regenerate baselines, which kills every rail in the file. Hence
    ``test_an_ordinary_config_field_addition_to_the_largest_file_stays_green``: a real config
    round-trip edit to the repo's biggest module must stay PASS.

⚠️  FORBIDDEN-TO-RAISE RULE (the ``done_when`` doc line — do not weaken it): when a ratchet
    reds because a counter ROSE, the fix is to FIX THE CODE — split the file, invert the
    import, reuse the existing implementation — NEVER to regenerate
    ``structural-baseline.json`` to bless the higher number. Raising a committed count to make
    CI green re-hides exactly the decay this census exists to surface.

Every ratchet also carries a VACUITY assertion, because a rail that matches nothing looks
clean and is the most common way our gates die. All three are keyed on the census (file count
and package coverage), and the count comes from the ratchet's OWN scan rather than a parallel
re-walk — see ``vacuity_failures`` and the falsification tests at the bottom of this file.

# how to update
    Regenerate the committed baseline ONLY when a counter LEGITIMATELY SHRANK, and do it in
    that SAME commit::

        python scripts/generate_structural_baseline.py

    Each such commit should be able to point at the split, the inversion, or the deletion.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from scripts import gate_report
from scripts import generate_structural_baseline as gen

# The forbidden-to-raise sentence, asserted present in both the generator and this test so the
# ``done_when`` "forbidden-to-raise doc line is present" cannot silently be dropped.
_FORBIDDEN_TO_RAISE = "never to regenerate"


def _committed() -> dict:
    path = gen.baseline_path()
    assert path.is_file(), (
        "structural-baseline.json is missing — generate it with "
        "`python scripts/generate_structural_baseline.py`"
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ── The three ratchets ───────────────────────────────────────────────────────


@pytest.mark.parametrize("ratchet", gen.RATCHETS)
def test_no_structural_counter_rose_vs_committed_baseline(ratchet):
    """The ratchets. Parametrized so each of the three fails INDEPENDENTLY and by NAME — a
    single combined assertion would let the first red hide the other two, which is the exact
    ergonomic PHF-11 exists to prevent.

    FIX THE CODE: split the file, invert the import, reuse the canonical implementation. Do
    not raise the committed number to go green.
    """
    failures = gen.ratchet_failures(ratchet, _committed(), gen.build_inventory())
    assert not failures, (
        f"{ratchet} REGRESSED — the tree got structurally worse:\n  "
        + "\n  ".join(failures)
        + "\n\nFORBIDDEN: do NOT regenerate structural-baseline.json to bless the higher "
        "number. Fix the code named above (that is the whole point of this ratchet). "
        "Regenerate ONLY when a counter legitimately shrank, in that same commit."
    )


@pytest.mark.parametrize("ratchet", gen.RATCHETS)
def test_committed_baseline_is_not_stale_on_the_shrink_side(ratchet):
    """The complement of the rise check: a committed counter ABOVE the current one means a
    real improvement landed and the baseline was never regenerated, so the ratchet's floor is
    looser than reality. Not a code defect — regenerate (see ``# how to update``)."""
    stale = gen.stale_high(ratchet, _committed(), gen.build_inventory())
    assert not stale, (
        f"the committed {ratchet} baseline is stale-HIGH — the tree improved but "
        "structural-baseline.json was not regenerated:\n  "
        + "\n  ".join(stale)
        + "\n\nRun `python scripts/generate_structural_baseline.py` in that commit."
    )


def test_committed_baseline_byte_matches_a_fresh_render():
    """Belt-and-suspenders on both directions at once: while the tree is unchanged the
    committed file is byte-identical to a fresh render. This is what makes a legitimate shrink
    require a regeneration in the same commit."""
    assert gen.baseline_path().read_text(encoding="utf-8") == gen.build_baseline(), (
        "structural-baseline.json does not match a fresh render. If a counter legitimately "
        "SHRANK, regenerate it in the same commit. If a counter ROSE, do NOT regenerate — fix "
        "the code instead."
    )


def test_the_render_is_deterministic():
    """Two renders in the same process are byte-identical: sorted keys, sorted site lists, no
    timestamps, no absolute paths, no line numbers."""
    assert gen.build_baseline() == gen.build_baseline()


# ── Never at zero (PHF-6's ruling, restated as a rail) ───────────────────────


def test_every_threshold_shipped_at_the_measured_population_not_at_zero():
    """``done_when``: "enforced shrink-only, NEVER at zero, because a never-run gate given
    teeth at zero reds the whole tree at once".

    Asserted on the committed numbers themselves, so a future session cannot quietly "clean
    up" a baseline to zero and hand the next contributor a tree-wide red.
    """
    committed = _committed()
    size = committed[gen.RATCHET_SIZE]
    assert size["ceiling_lines"] > 0, "the size ceiling is 0 — that reds every file at once"
    assert size["totals"]["watched_files"] > 0, "the watch band is empty — nothing is ratcheted"
    assert committed[gen.RATCHET_IMPORT_DIRECTION]["totals"]["edges"] > 0, (
        "the import-direction baseline is 0 edges. If that is real, say so in the plan log — "
        "but check first that the walk did not break, because a broken walk also reports 0."
    )
    assert (
        committed[gen.RATCHET_DUPLICATION]["totals"]["sites"] > 0
    ), "the duplication baseline is 0 sites — same warning: a broken detector reports 0 too."


def test_the_watch_band_is_not_sitting_on_a_cliff():
    """The band was chosen at a real gap in the measured distribution, not at a round number.

    With POPULATION counting the band boundary is the whole trigger, so this matters more than it
    did when each member's length was pinned: a band placed 12 lines above the next-largest file
    would drag an innocent module in on an unrelated commit, and the first thing that teaches
    everyone is to regenerate the baseline — which kills every other ratchet in the file.
    Measured LIVE (217 lines at the 2800 band) rather than read from the baseline, because a
    stored headroom moves whenever any sub-band file grows and would itself demand a
    regeneration. This rail has already earned its keep once: ``builtin_tools.py`` grew ~233
    lines in three days to 2467, cut the original 2500 band's headroom from 206 to 33, and
    forced the boundary to move to 2800 (see ``SIZE_WATCH_BAND_LINES``).
    """
    headroom = gen.watch_band_headroom()
    assert headroom >= 100, (
        f"only {headroom} lines of headroom below the {gen.SIZE_WATCH_BAND_LINES}-line watch "
        "band — a file is about to be dragged in by an unrelated commit. Split that file now, "
        "or (if the band is genuinely mis-placed) move the band to a gap in the distribution and "
        "record why in the plan log. Do NOT silently widen it."
    )
    size = _committed()[gen.RATCHET_SIZE]
    assert (
        size["watch_band_lines"] < size["ceiling_lines"]
    ), "the watch band is at or above the ceiling, so the band ratchets nothing"


def test_the_ceiling_leaves_the_biggest_file_room_for_ordinary_maintenance():
    """The ceiling must sit ABOVE the current max, not on it.

    A ceiling pinned at the measured maximum gives the ceiling-HOLDER zero headroom, and the
    holder here is ``config/loader.py`` — the file the config round-trip contract touches on every
    new field (dataclass + ``_meta`` + ``load()`` all live there). Pinned at the max, adding one
    boolean toggle would red CI and demand a 5,427-line split as its price. This rail is what
    stops a future "tighten the ceiling down to the max" cleanup from shipping that outage.
    """
    counts = gen.scan(gen.RATCHET_SIZE).rows
    biggest_file = max(counts, key=lambda rel: counts[rel])
    headroom = gen.SIZE_CEILING_LINES - counts[biggest_file]
    assert headroom >= 100, (
        f"only {headroom} lines of headroom on {biggest_file} ({counts[biggest_file]} lines vs a "
        f"{gen.SIZE_CEILING_LINES}-line ceiling) — a routine config-field addition would red the "
        "gate. Read the SIZE_CEILING_LINES comment before tightening this."
    )
    assert gen.SIZE_CEILING_LINES % gen.SIZE_CEILING_STEP_LINES == 0, (
        "the ceiling must be a whole step, so its rendered value stays stable while the max "
        "drifts by a few lines — otherwise the byte-compare test demands a regeneration on "
        "routine commits"
    )


def test_every_threshold_records_its_rationale():
    """``done_when``: "each threshold records its RATIONALE — what defect it exists to catch —
    so a future session can tell a load-bearing limit from an arbitrary one".

    Asserted on the committed JSON, not on the generator's docstring: the rationale has to
    travel WITH the number to wherever the next reader lands (a CI failure sends them to the
    baseline file first).
    """
    committed = _committed()
    size_rationale = committed[gen.RATCHET_SIZE]["rationale"]
    assert len(size_rationale) > 200, "the size ceiling's rationale is a stub"
    for rule in committed[gen.RATCHET_IMPORT_DIRECTION]["rules"]:
        assert len(rule["rationale"]) > 200, f"{rule['name']} has no real rationale"
        assert rule["upper"], f"{rule['name']} names no upper layer — it ratchets nothing"
    for family in committed[gen.RATCHET_DUPLICATION]["families"]:
        assert len(family["rationale"]) > 200, f"{family['name']} has no real rationale"


def test_the_deliberate_non_ratchets_are_recorded_as_decisions():
    """``done_when``: "the atom states what it deliberately does NOT ratchet, so the omissions
    read as decisions rather than gaps". Pinned so a later reader cannot mistake a choice for
    an oversight — or delete the reasoning and leave the list."""
    doc = gen.__doc__ or ""
    assert "what this deliberately does NOT ratchet" in doc
    for omission in ("tests/", "complexity", "web/", "apps", "Total line count", "Import CYCLES"):
        assert omission in doc, f"the deliberate-omission list lost {omission!r}"


def test_forbidden_to_raise_doc_line_is_present():
    """``done_when``: "every ratchet carries the forbidden-to-raise rule … and that phrase is
    asserted present by the rail itself so it cannot be quietly dropped" — in BOTH the
    generator and this test, so neither can drop it unnoticed."""
    assert "FORBIDDEN-TO-RAISE" in (gen.__doc__ or "")
    assert _FORBIDDEN_TO_RAISE in (gen.__doc__ or "").lower()
    assert "FORBIDDEN-TO-RAISE" in (__doc__ or "")
    assert _FORBIDDEN_TO_RAISE in (__doc__ or "").lower()
    # And in the message a failing developer actually reads — a doc line nobody sees when the
    # gate reds is a doc line that will be dropped.
    source = Path(__file__).read_text(encoding="utf-8")
    assert "FORBIDDEN: do NOT regenerate structural-baseline.json" in source, (
        "the ratchet's own failure message lost the forbidden-to-raise instruction — a "
        "developer reading only the red would regenerate"
    )


# ── Reporting through PHF-11's aggregate ─────────────────────────────────────


def test_three_simultaneous_structural_violations_report_as_three(monkeypatch, tmp_path):
    """``done_when``: "the ratchets report THROUGH PHF-11's aggregate … so one red does not hide
    four" — proven, not asserted, and registration proven in the same breath (a ratchet that
    exists only as a pytest test is invisible to ``make gates``).

    Seed ONE violation per structural ratchet at the same time (a file over the ceiling, a new
    upward import, a new re-derivation) by pointing ``baseline_path`` at a synthetic committed
    baseline that under-counts all three. The real comparison functions do the work. All three
    gates must come back FAIL in a SINGLE ``run_all_gates()`` call, and the rendered table must
    carry all three.
    """
    current = gen.build_inventory()
    understated = json.loads(json.dumps(current))

    # 1. size: drop a member from the committed band population, so the real tree reads as
    # having gained a giant. A NEW BAND ENTRANT is the violation shape that survives population
    # counting — "the biggest file gained three lines" deliberately is not one any more.
    victim_size = sorted(current[gen.RATCHET_SIZE]["watch_band_members"])[0]
    understated[gen.RATCHET_SIZE]["watch_band_members"] = [
        m for m in current[gen.RATCHET_SIZE]["watch_band_members"] if m != victim_size
    ]
    # 2. import-direction: drop one file's edges to 0 so its real edges read as a rise.
    victim_import = sorted(current[gen.RATCHET_IMPORT_DIRECTION]["per_file"])[0]
    understated[gen.RATCHET_IMPORT_DIRECTION]["per_file"][victim_import] = {
        "edges": 0,
        "violations": [],
    }
    # 3. duplication: same shape on the duplicate counter.
    victim_dup = sorted(current[gen.RATCHET_DUPLICATION]["per_file"])[0]
    understated[gen.RATCHET_DUPLICATION]["per_file"][victim_dup] = {"count": 0, "sites": []}

    seeded = tmp_path / "structural-baseline.json"
    seeded.write_text(json.dumps(understated) + "\n", encoding="utf-8")
    monkeypatch.setattr(gen, "baseline_path", lambda: seeded)

    results = {r.name: r for r in gate_report.run_all_gates()}

    for ratchet in gen.RATCHETS:
        assert ratchet in results, f"{ratchet} is not registered in scripts/gate_report.py"
        assert results[ratchet].ok is False, f"{ratchet} did not fail"
        assert results[ratchet].failures, f"{ratchet} failed with no failure line"
        assert not any(
            "raised" in line for line in results[ratchet].failures
        ), f"{ratchet} failed by RAISING, not by ratcheting: {results[ratchet].failures}"
    assert any(victim_size in ln for ln in results[gen.RATCHET_SIZE].failures)
    assert any(victim_import in ln for ln in results[gen.RATCHET_IMPORT_DIRECTION].failures)
    assert any(victim_dup in ln for ln in results[gen.RATCHET_DUPLICATION].failures)

    # All three appear in the ONE rendered table — the point of reporting through the
    # aggregate. (``main()``'s exit code on a failing gate is covered by
    # ``tests/test_gate_report.py``; re-running the six gates here just to re-prove it would
    # push this test toward the 120s per-test cap under load.)
    report = gate_report.render_report(list(results.values()))
    for ratchet in gen.RATCHETS:
        assert f"{ratchet} FAIL (" in report
    assert "SUMMARY: 3 of 6 gate(s) FAILED" in report, report


# ── Vacuity: a rail that matches nothing looks clean ─────────────────────────


def test_every_ratchet_has_a_vacuity_assertion_and_it_holds_on_the_real_tree():
    """The three vacuity checks pass on the real tree, and the census is the real population —
    not a handful of files a broken walk happened to find."""
    assert gen.census_py_files() >= gen.MIN_CENSUS_PY_FILES
    assert len(gen.census_packages()) > 50, "the package census collapsed"
    assert gen.vacuity_failures() == []
    for ratchet in gen.RATCHETS:
        assert gen.vacuity_failures(ratchet) == []


def test_an_empty_walk_fires_the_vacuity_assertion_for_every_ratchet(monkeypatch):
    """FALSIFICATION: make the walk find NOTHING and confirm every ratchet reports VACUITY
    rather than reading clean.

    This is the failure mode that kills gates here: a wrong root, a glob that stopped matching,
    a rename. Without this check all three ratchets would report a spotless tree.
    """
    monkeypatch.setattr(gen, "_src_py_files", lambda: [])
    failures = gen.vacuity_failures()
    assert len(failures) == len(gen.RATCHETS), failures
    for ratchet in gen.RATCHETS:
        assert any(ratchet in line and "VACUITY" in line for line in failures), failures
    # And the ratchet reads CLEAN on that empty walk — which is exactly why vacuity must gate
    # it. Proven, so nobody "simplifies" the vacuity check away as redundant.
    empty = gen.build_inventory()
    assert gen.regressions_size(_committed()[gen.RATCHET_SIZE], empty[gen.RATCHET_SIZE]) == []
    # …and the gate as a whole still FAILS, because vacuity comes first.
    assert gen.ratchet_failures(gen.RATCHET_SIZE, _committed(), empty)


def test_a_ratchet_that_inspects_zero_files_fires_vacuity_even_when_the_census_is_intact(
    monkeypatch,
):
    """FALSIFICATION, second shape: the census is FINE (921 files) but two ratchets inspect
    ZERO of them, because every parse silently fails. A swallowed ``SyntaxError`` is a real
    narrowing — ``_parse`` returns ``None`` and the file is skipped — and the file count alone
    would not notice, since the count check is keyed on what the RATCHET saw, not on the glob.
    """
    monkeypatch.setattr(gen, "_parse", lambda path: None)
    assert gen.census_py_files() >= gen.MIN_CENSUS_PY_FILES  # census intact

    for ratchet in (gen.RATCHET_IMPORT_DIRECTION, gen.RATCHET_DUPLICATION):
        failures = gen.vacuity_failures(ratchet)
        assert failures, f"{ratchet} read clean on a walk that inspected 0 files"
        assert "VACUITY" in failures[0] and "inspected 0 of the" in failures[0], failures
    # The size ratchet needs no parse, so it is correctly still healthy — the vacuity checks
    # are per-ratchet, not one shared flag.
    assert gen.vacuity_failures(gen.RATCHET_SIZE) == []


def test_dropping_a_whole_subpackage_fires_vacuity_even_though_the_count_stays_plausible(
    monkeypatch,
):
    """FALSIFICATION, third shape: an exclusion rule that swallows an entire package.

    ``dashboard/`` is where most of the import-direction violations live. Dropping it leaves
    the file count well above the floor, so the count check alone reads clean — and the
    ratchet would report that core no longer imports the HTTP surface. The package-coverage
    check is what catches it.
    """
    real_files = gen._src_py_files()
    kept = [p for p in real_files if "/dashboard/" not in p.as_posix()]
    assert (
        gen.MIN_CENSUS_PY_FILES < len(kept) < len(real_files)
    ), f"the arithmetic this test depends on changed: {len(kept)} kept of {len(real_files)}"

    # census_packages reads the disk directly, so it still knows dashboard/ exists.
    monkeypatch.setattr(gen, "_src_py_files", lambda: kept)
    failures = gen.vacuity_failures(gen.RATCHET_IMPORT_DIRECTION)
    assert failures, "a whole package left the walk and the ratchet read clean"
    assert "dashboard" in failures[0] and "VACUITY" in failures[0], failures


def test_the_walk_cannot_wander_into_a_worktree_or_a_vendor_directory():
    """This repo is routinely checked out as ~200 concurrent worktrees. A census that counted
    another agent's tree is not a measurement of THIS repo, and its number would drift every
    run. Two guarantees: the walk is rooted at ``src/personalclaw`` (never the repo root), and
    the excluded-directory floor names every vendor/worktree dir explicitly."""
    for excluded in (".worktrees", "node_modules", ".venv", "build", "__pycache__", ".git"):
        assert excluded in gen._EXCLUDED_DIR_NAMES, f"{excluded} left the exclusion floor"
    root = gen._src_root().as_posix()
    assert root.endswith("/src/personalclaw")
    for path in gen._src_py_files():
        assert path.as_posix().startswith(root + "/"), f"{path} is outside the census root"
        assert not (gen._EXCLUDED_DIR_NAMES & set(path.parts)), path


# ── Detector rails: a false CLEAR passes the ratchet silently ────────────────
#
# A false RED is loud (someone investigates). A false CLEAR lowers a counter and sails through
# the shrink-only comparison, and the ratchet quietly stops watching. These synthetic-tree
# tests pin each detector's shape so a "simplification" cannot blind it.


def _module(tmp_path: Path, name: str, body: str):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return gen._parse(path)


def test_the_envelope_detector_is_keyed_on_the_SHAPE_not_the_helper_name(tmp_path):
    """A clone renamed from ``_err`` to something innocent is still a clone. Keying on the
    dict shape (``{"error": {"code": ...}}``) is what makes the counter un-dodgeable."""
    tree = _module(
        tmp_path,
        "handler.py",
        """
        def _oops(code, message, status):
            return web.json_response({"error": {"code": code, "message": message}}, status=status)

        def _unrelated(x):
            return {"data": x}
        """,
    )
    assert gen._envelope_helper_sites(tree) == ["_oops"]


def test_the_envelope_detector_ignores_a_full_route_handler(tmp_path):
    """Bounded to <= 3 statements on purpose. A 40-line handler that returns an envelope inline
    is a much larger population with no sanctioned alternative yet, and counting it would make
    the number un-actionable — see the family's rationale."""
    tree = _module(
        tmp_path,
        "route.py",
        """
        async def api_thing(request):
            body = await request.json()
            name = body.get("name")
            if not name:
                return web.json_response({"error": {"code": "bad", "message": "no name"}})
            result = do(name)
            log(result)
            return web.json_response({"ok": True})
        """,
    )
    assert gen._envelope_helper_sites(tree) == []


def test_the_durable_write_detector_does_not_count_a_delegating_wrapper(tmp_path):
    """Three helpers named ``_atomic_write*`` DELEGATE to ``atomic_write`` — that is the shape
    we want, not a re-derivation. Counting them would inflate the number and, worse, teach the
    next reader that wrapping the canonical helper is the defect."""
    tree = _module(
        tmp_path,
        "writers.py",
        """
        def _atomic_write(path, data):
            atomic_write(path, json.dumps(data), fsync=True)

        def _rolls_its_own(path, data):
            fd, tmp = tempfile.mkstemp(dir=str(path.parent))
            os.write(fd, data)
            os.close(fd)
            os.replace(tmp, path)
        """,
    )
    assert gen._durable_write_sites(tree) == ["_rolls_its_own"]


def test_the_verdict_detector_counts_the_named_family(tmp_path):
    """Name-based on purpose, and the rationale says why: a decision in a genuinely different
    domain is not destined to merge into the canonical algebra — but then it should not be
    NAMED a verdict, and the rename shrinks the counter too."""
    tree = _module(
        tmp_path,
        "d.py",
        """
        class Verdict: pass
        class TrustVerdict: pass
        class VerdictRecord: pass
        class Decision: pass
        """,
    )
    assert sorted(gen._verdict_type_sites(tree)) == ["TrustVerdict", "Verdict"]


def test_the_import_direction_rule_resolves_relative_imports(tmp_path, monkeypatch):
    """``from ..dashboard import x`` never contains the string ``personalclaw.dashboard``, so a
    grep-shaped rule would miss the most idiomatic way to write the violation. Resolving
    relative imports is what keeps the rule from being a rail that matches nothing.

    Driven against a SYNTHETIC src root, never by writing a probe into the real tree: a stray
    file under ``src/personalclaw`` would red the byte-compare gate for every other suite
    running concurrently, and a failed teardown would leave it there.
    """
    monkeypatch.setattr(gen, "_src_root", lambda: tmp_path)
    probe = tmp_path / "workflows" / "probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "from ..dashboard import state\nfrom .journal import x\nimport personalclaw.sdk.model\n",
        encoding="utf-8",
    )
    tree = gen._parse(probe)
    assert tree is not None
    modules = gen._imported_personalclaw_modules(probe, tree)
    assert "personalclaw.dashboard" in modules, modules
    assert "personalclaw.workflows.journal" in modules, modules
    assert "personalclaw.sdk.model" in modules, modules


def test_the_upper_layer_may_import_itself():
    """``dashboard/`` importing ``dashboard/`` is not a violation, and neither is ``sdk/``
    importing ``sdk/``. A rule that flagged intra-layer imports would report hundreds of false
    reds and be deleted within a day."""
    rule = next(r for r in gen.DIRECTION_RULES if r.upper == ("dashboard",))
    assert rule.applies_to("workflows/handlers.py") is True
    assert rule.applies_to("dashboard/handlers/core.py") is False
    assert rule.applies_to("gateway.py") is True, "top-level modules are part of core"


def _real_counts() -> dict[str, int]:
    """The real per-file line counts, as a mutable copy. Perturbing this copy lets a test drive
    the REAL derivation over a hypothetical tree without writing to ``src/``."""
    return dict(gen.scan(gen.RATCHET_SIZE).rows)


def test_an_ordinary_config_field_addition_to_the_largest_file_stays_green():
    """THE rail that keeps this gate a ratchet instead of an outage.

    Simulates the repo's documented config round-trip on its most-touched file: six lines added to
    ``config/loader.py`` in the shape of a real field (a dataclass field, a ``_meta`` row, a
    ``load()`` mapping, a ``to_dict()`` line). Three of the contract's five points live in that
    file, and it is also the largest module in the repo — so if the size ratchet cannot absorb
    this, every config field addition in the project reds CI and the gate gets deleted.

    What it proves: ORDINARY MAINTENANCE OF AN EXISTING LARGE FILE IS NOT A VIOLATION. The
    companion test below proves the other half — a NEW large file is.
    """
    counts = _real_counts()
    biggest = max(counts, key=lambda rel: counts[rel])
    assert biggest == "src/personalclaw/config/loader.py", (
        f"the premise moved: the largest file is now {biggest}. Re-point this test — it has to "
        "exercise the ceiling HOLDER, which is where headroom is scarcest."
    )
    assert (
        biggest in _committed()[gen.RATCHET_SIZE]["watch_band_members"]
    ), "the largest file is not a band member, so this test no longer exercises a giant"
    counts[biggest] += 6

    failures = gen.regressions_size(_committed()[gen.RATCHET_SIZE], gen.size_block_from(counts))
    assert failures == [], (
        "a six-line config-field addition to the repo's largest file REDS the size ratchet. That "
        "is an outage, not a gate: it prices a boolean toggle at a 5,427-line split. Re-read the "
        "SIZE_CEILING_LINES comment.\n  " + "\n  ".join(failures)
    )
    # And the render is unchanged, so the byte-compare gate does not demand a regeneration
    # either — the other half of "ordinary maintenance is untaxed".
    assert gen.size_block_from(counts) == gen.size_block_from(_real_counts())


def test_a_new_giant_file_reds_by_naming_the_band_population():
    """The other half: a NEW module over the band is exactly the decay this ratchet exists to
    catch, and the failure must name the population going N -> N+1 so the fix is obvious.

    The new file's size is derived from ``SIZE_WATCH_BAND_LINES``, never hard-coded. A literal here
    silently stops testing anything the moment the band moves — which is not hypothetical: it
    happened on the 2500 -> 2800 move, where a hard-coded 2,600-line probe fell BELOW the new band
    and this test went green while asserting nothing.
    """
    counts = _real_counts()
    committed = _committed()[gen.RATCHET_SIZE]
    before = len(committed["watch_band_members"])
    counts["src/personalclaw/brand_new_giant.py"] = gen.SIZE_WATCH_BAND_LINES + 100

    failures = gen.regressions_size(committed, gen.size_block_from(counts))
    assert len(failures) == 1, failures
    assert "watch-band population ROSE" in failures[0]
    assert f"{before} -> {before + 1}" in failures[0], failures[0]
    assert "brand_new_giant.py" in failures[0], failures[0]


def test_a_file_pushed_past_the_ceiling_reds_on_the_ceiling():
    """The absolute backstop: a band member (or anything else) reaching the ceiling is a step
    change, and reds by name with its length."""
    counts = _real_counts()
    biggest = max(counts, key=lambda rel: counts[rel])
    counts[biggest] = gen.SIZE_CEILING_LINES + 1

    failures = gen.regressions_size(_committed()[gen.RATCHET_SIZE], gen.size_block_from(counts))
    assert any(
        biggest in ln and "EXCEEDS the committed per-file ceiling" in ln for ln in failures
    ), failures
    assert any(str(gen.SIZE_CEILING_LINES) in ln for ln in failures), failures


def test_a_split_is_never_a_regression_and_asks_for_a_regeneration():
    """Removing a giant is the outcome the ratchet wants: never a failure, and the stale-high
    check asks for the baseline to be regenerated in that same commit."""
    counts = _real_counts()
    committed = _committed()[gen.RATCHET_SIZE]
    departing = sorted(committed["watch_band_members"])[0]
    counts[departing] = 400  # split into smaller modules

    current_block = gen.size_block_from(counts)
    assert gen.regressions_size(committed, current_block) == []
    stale = gen.stale_high(
        gen.RATCHET_SIZE, {gen.RATCHET_SIZE: committed}, {gen.RATCHET_SIZE: current_block}
    )
    assert any(departing in ln and "left the band" in ln for ln in stale), stale


def test_an_unknown_ratchet_name_is_rejected_rather_than_silently_passing():
    """A typo'd ratchet name must raise, not return an empty (clean) failure list. An
    always-green gate is the worst outcome available here."""
    with pytest.raises(ValueError, match="unknown structural ratchet"):
        gen.ratchet_failures("structural-typo", {}, {})
    with pytest.raises(ValueError, match="unknown structural ratchet"):
        gen.scan("structural-typo")
