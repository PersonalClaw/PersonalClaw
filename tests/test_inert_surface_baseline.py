"""Shrink-only ratchet for the committed inert-surface inventory (PLATFORM-HARDENING-FLOORS SH3.2).

``inert-surface-baseline.json`` is a GENERATED census (by
``scripts/generate_inert_surface_baseline.py``) of *declared-but-inert surfaces* across
six seam kinds — config keys, config readers, enum members, trigger kinds,
``_EDITABLE_CONFIG`` entries, and SDK exports — each being a place where something is
declared and nothing on the other side of the seam consumes or produces it. That defect
passes ordinary tests because they hand-build the state the missing writer should have
created; only a census of both ends catches it.

This suite is the ratchet that keeps the census honest. It regenerates in-memory and
asserts every per-file inert counter **may only shrink** versus the committed baseline:

  * A per-file counter that ROSE — a NEW declared-but-inert surface — reds CI, naming the
    file and the new surface. This is ``done_when``: "adding a declared-but-unread surface
    reds CI".
  * A DECREASE is welcome (a cleanup added the missing writer/reader). The ratchet does
    NOT demand exact equality and does NOT require the count to go down — only that it
    never goes up. This is why we shipped at the MEASURED population, not zero: a never-run
    gate given teeth at zero would red every pre-existing inert surface at once (an
    outage). See the generator's module docstring.

⚠️  FORBIDDEN-TO-RAISE RULE (the ``done_when`` doc line — do not weaken it): when this test
    reds because a counter ROSE, the fix is to ADD THE MISSING WRITER OR READER for the new
    surface — NEVER to regenerate ``inert-surface-baseline.json`` to bless the higher
    number. Raising a committed count to make CI green re-hides exactly the defect this
    census exists to surface.

# how to update
    Regenerate the committed baseline ONLY when a counter LEGITIMATELY SHRANK (a real
    cleanup landed that wired the missing writer/reader), and do it in that SAME commit::

        python scripts/generate_inert_surface_baseline.py

    Each such cleanup commit should be able to point at the writer/reader it added.

    ONE further case, added 2026-08-19: a counter may also rise because the CENSUS started
    seeing a population it was previously blind to — not because anything became inert.
    `sdk/channel.py` was the only `sdk/` module with no `__all__`, and the ``sdk_export``
    detector keys on `__all__`, so its 104 published re-exports were invisible to this
    ratchet for the whole life of the facade (it counted 0). Declaring the surface moved it
    to 104 without changing one line of what the module publishes.

    That case is NOT an escape hatch, and it does not soften the rule above. It is
    admissible only with the arithmetic attached: render the census against the PRE-change
    file with an ``__all__`` mechanically derived from its own imports, and show the count is
    identical. If the two numbers differ, the difference is a real new inert surface and the
    forbidden-to-raise rule applies to it in full. (Measured for the case above: 104 before,
    104 after. Of those 104, 97 are imported by a bundled channel app in the apps repo — the
    census cannot see across the repo boundary by construction, which the generator's own
    docstring says of every ``sdk_export``.)

    A SECOND such case, added 2026-09-24 and the mirror image of the first: a counter may
    also FALL because the census learned a reader shape it was previously blind to. The
    ``sdk_export`` detector knew only one reader — an in-repo ``ImportFrom`` — and an SDK
    export's real consumer is an app in a separate repository, so it could not tell a public
    API type apart from an unkept promise. It now also clears an export that ANOTHER export's
    field or method signature names (``scripts/sdk_surface_closure.py``), which dropped
    ``sdk_export`` 229 → 170 and every touched file's counter with it. That is a plain
    legitimate shrink under the rule above — regenerate in the same commit — but it is NOT a
    cleanup: nothing was wired, the census simply stopped scoring the API as inert. Recorded
    here because "a counter fell" normally means "a writer/reader landed", and reading this
    one that way would send someone looking for a commit that does not exist.

    That second case recurred once more, same day (#3511), when the same reader shape was
    widened from "another export's field or method" to "another export's field, method OR
    exported FUNCTION signature": ``sdk_export`` 170 → 159 across eight files, ROSE on none.
    Twenty-two types became newly owed; NINETEEN were exported in the same change and every one
    of them cleared on the signature that owed it — which is why a tranche of 19 new published
    names moved the counter DOWN rather than up. The other three are a declared
    ``personalclaw.dashboard.`` exemption (see ``CLOSURE_EXEMPT_PREFIX``) whose reason is the
    structural import-direction ratchet, not this one. Read that arithmetic before trusting the
    direction: a widening that clears more than it adds is legitimate, a widening that adds a
    surface nothing names is the defect the counter exists to catch.
"""

from __future__ import annotations

import json
import textwrap
import typing
from pathlib import Path

import pytest

from scripts.generate_inert_surface_baseline import (
    KIND_CONFIG_READER,
    _attribute_names_in_src,
    _ConfigFieldDeclaration,
    _enum_members,
    _inert_config_reader_paths,
    _inert_enum_members,
    _iterated_enum_classes,
    _parse,
    _src_py_files,
    baseline_path,
    build_baseline,
    build_inventory,
    regressions,
)

# The forbidden-to-raise sentence, asserted present in both the generator and this test so
# the ``done_when`` "forbidden-to-raise doc line is present" cannot silently be dropped.
_FORBIDDEN_TO_RAISE = "add the missing writer or reader"


def _committed_inventory() -> dict:
    path = baseline_path()
    assert path.is_file(), (
        "inert-surface-baseline.json is missing — generate it with "
        "`python scripts/generate_inert_surface_baseline.py`"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.timeout(300)
def test_no_per_file_counter_rose_vs_committed_baseline():
    """The ratchet: no file's inert-surface count may exceed its committed count.

    A rise means a NEW declared-but-inert surface was added — a config key nothing reads,
    an enum member nobody references, a trigger kind nothing dispatches, an editable-config
    entry with no backing field, or an SDK export nothing imports. FIX IT BY ADDING THE
    MISSING WRITER OR READER — do not raise the committed number to go green.
    """
    committed = _committed_inventory()
    current = build_inventory()
    rose = regressions(committed["per_file"], current["per_file"])
    assert not rose, (
        "inert-surface count ROSE for one or more files — a new declared-but-inert "
        "surface was introduced:\n  "
        + "\n  ".join(rose)
        + "\n\nFORBIDDEN: do NOT regenerate inert-surface-baseline.json to bless the higher "
        "number. Add the missing writer or reader for the surface named above (that is the "
        "whole point of this ratchet). Regenerate the baseline ONLY when a count "
        "legitimately shrank, in that same commit."
    )


@pytest.mark.timeout(300)
def test_committed_baseline_is_not_stale_on_the_shrink_side():
    """Every committed per-file count must be >= the current count.

    The complement of the rise check: if a file's committed count is BELOW its current
    count that is the rise case (covered above); if it is ABOVE, a cleanup shrank the real
    population but the baseline was not regenerated, so the ratchet's floor is looser than
    reality. Neither is allowed to pass silently — a stale-high baseline should be
    regenerated (see this module's ``# how to update``)."""
    committed = _committed_inventory()
    current = build_inventory()
    stale_high = sorted(
        f"{rel}: committed {committed['per_file'][rel]['inert']} > current "
        f"{current['per_file'].get(rel, {}).get('inert', 0)}"
        for rel in committed["per_file"]
        if committed["per_file"][rel]["inert"] > current["per_file"].get(rel, {}).get("inert", 0)
    )
    assert not stale_high, (
        "the committed baseline is stale-HIGH — a cleanup shrank the inert population but "
        "inert-surface-baseline.json was not regenerated:\n  "
        + "\n  ".join(stale_high)
        + "\n\nRun `python scripts/generate_inert_surface_baseline.py` in the cleanup commit."
    )


@pytest.mark.timeout(300)
def test_committed_baseline_byte_matches_a_fresh_render():
    """Belt-and-suspenders on both directions at once: while the population is unchanged the
    committed file is byte-identical to a fresh render. This is what makes a legitimate
    shrink require a regeneration in the same commit (the two prior tests already forbid a
    silent rise or a stale-high floor)."""
    fresh = build_baseline()
    committed = baseline_path().read_text(encoding="utf-8")
    assert committed == fresh, (
        "inert-surface-baseline.json does not match a fresh render. If a cleanup "
        "legitimately shrank a counter, regenerate it with "
        "`python scripts/generate_inert_surface_baseline.py` in the same commit. If a "
        "counter ROSE, do NOT regenerate — add the missing writer or reader instead."
    )


@pytest.mark.timeout(300)
def test_render_is_deterministic():
    """Generating twice yields byte-identical output — no set-ordering, no timestamps, no
    absolute paths. Determinism is the whole contract; without it the ratchet is noise.

    🔴 The timeout is RAISED, not the work reduced, and the number is measured. Before the
    sixth census kind, two builds took **13.1s** on an idle machine (6.7s + 6.4s), but CI
    starved the CPU-bound whole-tree walk past 120s twice (#1205, #1222). With the sixth
    kind, one cold six-kind build measured **76.5s** on the shared host under the mandated
    three-worker cap. The parser cache removes duplicate parsing inside a process, but both
    calls still recollect and render every surface, so 300s remains measured headroom rather
    than a new cliff.

    Serializing this test instead is NOT available: the suite runs `--dist worksteal`
    (`pyproject.toml`), which ignores `xdist_group` — measured in PHF-9, where switching to
    `loadgroup` cost +58% wall time and was rejected.

    Building once and comparing a re-render would be cheaper and WRONG: the contract is that
    *collection* is order-stable too, so both builds have to be real.
    """
    assert build_baseline() == build_baseline()


def test_baseline_is_well_shaped_and_sorted():
    """The committed inventory has the declared shape and every list is sorted."""
    inv = _committed_inventory()
    assert set(inv) == {"generated_from", "per_file", "totals"}, inv.keys()
    assert inv["generated_from"] == "scripts/generate_inert_surface_baseline.py"
    total = 0
    for rel, bucket in inv["per_file"].items():
        assert set(bucket) == {"inert", "surfaces"}, bucket
        assert isinstance(rel, str) and rel and not rel.startswith("/"), rel
        surfaces = bucket["surfaces"]
        assert surfaces == sorted(surfaces), f"{rel} surfaces not sorted"
        assert len(surfaces) == len(set(surfaces)), f"{rel} has duplicate surfaces"
        assert bucket["inert"] == len(surfaces), f"{rel} inert count != len(surfaces)"
        for s in surfaces:
            kind = s.split(":", 1)[0]
            assert kind in {
                "config",
                "config_reader",
                "enum",
                "trigger_kind",
                "editable_config",
                "sdk_export",
            }, s
        total += bucket["inert"]
    assert inv["totals"]["inert"] == total
    assert inv["totals"]["inert"] == sum(inv["totals"]["by_kind"].values())


def test_baseline_ships_at_a_nonzero_measured_population():
    """The census must ship at the MEASURED population, not zero.

    A zeroed baseline would mean the ratchet was given teeth before the existing inert
    surfaces were driven down — the outage this atom explicitly avoids. A nonzero total is
    the evidence we measured first and committed the real number (SH3.3 owns driving it
    down)."""
    inv = _committed_inventory()
    assert inv["totals"]["inert"] > 0, (
        "inert-surface-baseline.json reports zero inert surfaces — ship at the MEASURED "
        "population, not zero (a never-run gate given teeth at zero is an outage)."
    )


def test_a_new_inert_surface_reds_the_ratchet():
    """done_when: "adding a declared-but-unread surface reds CI".

    We do NOT add dead code to the tree — we exercise the SHARED comparison the ratchet
    relies on against a synthetic ``current`` that carries one extra surface for a real
    file, and assert the comparison flags it (naming file + surface). This proves the gate
    would red on a genuine new inert surface without perturbing the actual census."""
    committed = _committed_inventory()
    per_file = committed["per_file"]
    assert per_file, "baseline is empty; cannot exercise the ratchet"

    victim = sorted(per_file)[0]
    synthetic = {
        rel: {"inert": bucket["inert"], "surfaces": list(bucket["surfaces"])}
        for rel, bucket in per_file.items()
    }
    synthetic[victim]["surfaces"].append("enum:SyntheticProbe.NEW_MEMBER")
    synthetic[victim]["inert"] += 1

    rose = regressions(per_file, synthetic)
    assert any(victim in line and "NEW_MEMBER" in line for line in rose), rose


def test_a_new_file_with_inert_surfaces_reds_the_ratchet():
    """A file absent from the baseline that acquires an inert surface counts as a rise from
    an implicit zero — covering the "brand new file introduces an inert surface" case."""
    committed = _committed_inventory()
    synthetic = {
        rel: {"inert": bucket["inert"], "surfaces": list(bucket["surfaces"])}
        for rel, bucket in committed["per_file"].items()
    }
    synthetic["src/personalclaw/brand_new_module.py"] = {
        "inert": 1,
        "surfaces": ["sdk_export:NeverImported"],
    }
    rose = regressions(committed["per_file"], synthetic)
    assert any("brand_new_module.py" in line for line in rose), rose


def test_a_cleanup_that_shrinks_a_counter_does_not_red_the_ratchet():
    """The other side of the contract: driving a counter DOWN (a real cleanup) is welcome —
    the rise-only comparison must return no regression for a shrink."""
    committed = _committed_inventory()
    per_file = committed["per_file"]
    victim = next((rel for rel, b in per_file.items() if b["inert"] > 0), None)
    assert victim is not None, "expected at least one file with inert surfaces"
    shrunk = {
        rel: {"inert": bucket["inert"], "surfaces": list(bucket["surfaces"])}
        for rel, bucket in per_file.items()
    }
    shrunk[victim]["surfaces"].pop()
    shrunk[victim]["inert"] -= 1
    assert regressions(per_file, shrunk) == []


# ── config-reader census: the #322/#364 calibration pair ─────────────────────


def test_config_reader_reproduces_the_two_paid_for_findings(tmp_path):
    """The sixth kind catches the historical Chat fields and ``agent.sandbox``.

    The fixture carries every false-clear trap from #364: an imported module with the same
    terminal word, a GitHub-label description, and a comment about the toggle. None is an
    AST field read. It also pins both supported clear paths: a config accessor called by
    production code, and a direct field read outside config.
    """
    config_file = tmp_path / "config_models.py"
    files = _fixture_tree(
        tmp_path,
        {
            "config_models.py": """
                class DashboardConfig:
                    send_on_enter = True
                    show_timestamps = False
                    show_thinking_inline = False
                    simplified_tool_names = False
                    confirm_close_session = True

                    def to_dict(self):
                        return {
                            "send_on_enter": self.send_on_enter,
                            "show_timestamps": self.show_timestamps,
                        }

                class AgentConfig:
                    sandbox = "auto"

                class WorkflowsConfig:
                    max_concurrent_llm_nodes = 4

                    def lane_caps(self):
                        return {"llm": self.max_concurrent_llm_nodes}

                class LearningConfig:
                    min_session_score = 0.0
                """,
            "runtime.py": """
                import personalclaw.sandbox

                GITHUB_LABEL_DESCRIPTION = "agent.sandbox controls process isolation"
                module_reference = personalclaw.sandbox

                # The agent.sandbox toggle used to promise a global sandbox policy.

                def limits(cfg):
                    return cfg.workflows.lane_caps()

                def unreachable_score_branch(cfg):
                    return cfg.learning.min_session_score
                """,
        },
    )
    declarations = [
        _ConfigFieldDeclaration(path, config_file, owner, field)
        for path, owner, field in [
            ("dashboard.send_on_enter", "DashboardConfig", "send_on_enter"),
            ("dashboard.show_timestamps", "DashboardConfig", "show_timestamps"),
            ("dashboard.show_thinking_inline", "DashboardConfig", "show_thinking_inline"),
            ("dashboard.simplified_tool_names", "DashboardConfig", "simplified_tool_names"),
            ("dashboard.confirm_close_session", "DashboardConfig", "confirm_close_session"),
            ("agent.sandbox", "AgentConfig", "sandbox"),
            (
                "workflows.max_concurrent_llm_nodes",
                "WorkflowsConfig",
                "max_concurrent_llm_nodes",
            ),
            ("learning.min_session_score", "LearningConfig", "min_session_score"),
        ]
    ]

    inert = set(
        _inert_config_reader_paths(
            files,
            declarations,
            editable_paths={decl.path for decl in declarations},
        )
    )
    assert inert == {
        "agent.sandbox",
        "dashboard.confirm_close_session",
        "dashboard.send_on_enter",
        "dashboard.show_thinking_inline",
        "dashboard.show_timestamps",
        "dashboard.simplified_tool_names",
    }


@pytest.mark.timeout(300)
def test_the_real_config_reader_census_fires_and_clears_an_accessor():
    """Vacuity + real-tree calibration in both directions."""
    inv = build_inventory()
    assert inv["totals"]["by_kind"][KIND_CONFIG_READER] > 0
    flagged = {surface for bucket in inv["per_file"].values() for surface in bucket["surfaces"]}
    assert f"{KIND_CONFIG_READER}:routing.energy_sampling" in flagged
    for wired in [
        "agent.self_qa.enabled",
        "external_access.a2a.enabled",
        "external_access.openai.enabled",
        "inbox.enabled",
        "knowledge.embed_batch_size",
        "knowledge.embed_retry_budget",
        "knowledge.max_mentions_per_claim",
        "knowledge.require_citations",
        "knowledge.similarity_degree_cap",
        "knowledge.synthesis_window",
        "learning.self_model_enabled",
        "resilience.remediation.enabled",
        "sources.enabled",
        "workflows.max_concurrent_llm_nodes",
        "workflows.retention_per_def",
    ]:
        assert f"{KIND_CONFIG_READER}:{wired}" not in flagged


# ── enum census: whole-enum iteration clears a class (PHF-12) ────────────────
#
# The census once reported an enum member inert whenever its NAME was never accessed as an
# attribute, which called every iteration-only enum dead: `workflows/publish.py:136`
# validates author-supplied lineage edges against `{e.value for e in Lineage}`, so
# `Lineage.INFORMED_BY` and `Lineage.RELATED` are reachable and were reported anyway. These
# tests pin both directions of the corrected rule against a FIXTURE tree — never by adding
# dead code to `src/` — plus a vacuity guard that the real census still finds a population.


def _fixture_tree(tmp_path: Path, modules: dict[str, str]) -> list[Path]:
    """Write ``{filename: source}`` into ``tmp_path`` and return the sorted file list."""
    for name, source in modules.items():
        (tmp_path / name).write_text(textwrap.dedent(source), encoding="utf-8")
    return sorted(tmp_path.glob("*.py"))


def _inert_in(tmp_path: Path, modules: dict[str, str]) -> set[str]:
    """``{"Class.MEMBER"}`` the enum detector reports for a fixture tree."""
    files = _fixture_tree(tmp_path, modules)
    return {surface for _, surface in _inert_enum_members(files, _attribute_names_in_src(files))}


def test_an_enum_consumed_only_by_iteration_is_not_flagged(tmp_path):
    """Every shape the detector claims to support clears its class, with no member of any of
    them touched as an attribute anywhere in the fixture tree."""
    inert = _inert_in(
        tmp_path,
        {
            "looped.py": """
                from enum import Enum

                class Looped(Enum):
                    ONE = "one"
                    TWO = "two"

                def kinds():
                    out = []
                    for member in Looped:
                        out.append(member.value)
                    return out
                """,
            "comprehended.py": """
                from enum import Enum

                class Comprehended(str, Enum):
                    ALPHA = "alpha"
                    BETA = "beta"

                ALLOWED = {e.value for e in Comprehended}
                """,
            "called.py": """
                from enum import Enum

                class Called(Enum):
                    RED = "red"
                    BLUE = "blue"

                ORDERED = tuple(sorted(Called, key=str))
                """,
        },
    )
    assert inert == set(), f"iteration-only enums were flagged (false red): {sorted(inert)}"


def test_an_enum_consumed_nowhere_is_still_flagged(tmp_path):
    """The rule can still fail: a genuinely unreferenced member is reported. Two members of
    the SAME class are declared and one is read as an attribute, so this also proves the
    per-member half survives — only iteration clears a whole class."""
    inert = _inert_in(
        tmp_path,
        {
            "orphan.py": """
                from enum import Enum

                class Orphan(Enum):
                    GHOST = "ghost"
                    PHANTOM = "phantom"
                """,
            "partial.py": """
                from enum import Enum

                class Partial(Enum):
                    USED = "used"
                    UNUSED = "unused"

                DEFAULT = Partial.USED
                """,
        },
    )
    assert inert == {"Orphan.GHOST", "Orphan.PHANTOM", "Partial.UNUSED"}, sorted(inert)


def test_iterating_one_enum_does_not_clear_a_same_named_enum_elsewhere(tmp_path):
    """Resolution is import-aware, not name-based. ``src/`` declares seven distinct
    ``Verdict`` enums; if iterating one cleared them all, a real inert member would go
    unreported the moment any namesake was iterated somewhere."""
    inert = _inert_in(
        tmp_path,
        {
            "shadow_a.py": """
                from enum import Enum

                class Shadow(Enum):
                    A_ONLY = "a"
                """,
            "shadow_b.py": """
                from enum import Enum

                class Shadow(Enum):
                    B_ONLY = "b"

                VALUES = [s.value for s in Shadow]
                """,
        },
    )
    assert inert == {"Shadow.A_ONLY"}, sorted(inert)


def test_the_lineage_false_red_is_gone_and_its_iteration_site_is_seen():
    """The finding itself, on the real tree: ``Lineage`` is recognised as iterated (its
    validation set in ``workflows/publish.py``) and no ``Lineage`` member is reported.

    If the validation that iterates ``Lineage`` is ever deleted, this test SHOULD red — the
    members would then genuinely be unreachable."""
    files = _src_py_files()
    enum_names: dict[Path, set[str]] = {}
    for f in files:
        tree = _parse(f)
        if tree is None:
            continue
        names = {cls for cls, _ in _enum_members(tree)}
        if names:
            enum_names[f.resolve()] = names
    iterated = _iterated_enum_classes(files, enum_names)
    publish = next(f for f in files if f.as_posix().endswith("workflows/publish.py"))
    assert (publish.resolve(), "Lineage") in iterated

    inert = {surface for _, surface in _inert_enum_members(files, _attribute_names_in_src(files))}
    assert not [s for s in inert if s.startswith("Lineage.")], sorted(inert)


@pytest.mark.timeout(300)
def test_the_enum_census_still_finds_a_nontrivial_population():
    """Vacuity guard. Clearing a whole class per iteration site is a broad clear, so a bug
    that over-cleared (or a detector that silently matched everything) would leave the enum
    census reporting nothing while every other kind still looked healthy. The census must
    still SEE many enum classes and still REPORT some inert members.

    🔴 The timeout is RAISED, not the work reduced, and the number is measured — the same
    ruling `test_render_is_deterministic` records above, for the same reason and at the same
    number. This is the file's second-heaviest test: it walks and parses every `src` file for
    the `>50` claim, then `build_inventory()` walks the tree AGAIN. Measured through pytest at
    load average 40: **19.2s** of call time alone, and **28.5s** running with the rest of this
    file — second only to the 42.8s of `test_render_is_deterministic`, which is already at 300
    for having crossed 120s in CI twice (#1205, #1222). CI starved this one past 120s the same
    way. Both assertions are untouched; only the wall-clock budget moves.

    Neither half of the work can be dropped without gutting the guard. The `>50` sweep is
    what proves the walk still SEES the population, and the `>= 5` half has to read the real
    census output — a stubbed or cached inventory would assert on the stub, which is exactly
    the suspiciously-clean census this test exists to catch. `_parse()` is deliberately
    uncached tree-wide (see `test_render_is_deterministic`: both builds have to be real), so
    there is no memoization to lean on here either.
    """
    files = _src_py_files()
    classes = {
        cls for f in files if (tree := _parse(f)) is not None for cls, _ in _enum_members(tree)
    }
    assert len(classes) > 50, f"only {len(classes)} enum classes seen — the walk is broken"
    assert build_inventory()["totals"]["by_kind"]["enum"] >= 5, (
        "the enum census reports (almost) nothing — whole-class clearing has over-reached; "
        "shrink the detected shapes rather than trusting a suspiciously clean census"
    )


# ── PHF-13: the value-lookup ruling (NOT a widening — a pinned decision) ─────────────────
#
# ``PHF-13`` audited every ``E(value)`` site behind the surviving enum surfaces and ruled
# AGAINST teaching the detector that shape: five of the six sites either never execute in
# production or read only values this codebase itself wrote, so a syntactic rule would FALSE-
# CLEAR them. A false clear passes the shrink-only ratchet silently (the count goes DOWN), so
# the ruling needs its own rail. These two tests are it.


def test_value_lookup_alone_does_not_clear_a_member(tmp_path):
    """A member reachable only through ``E(value)`` is STILL REPORTED — on purpose.

    ``E(value)`` does not prove reachability: the construction may never execute, and its value
    may come from state we wrote ourselves. Whoever wants to change this must first re-run
    ``PHF-13``'s per-site provenance audit (verdict table in the generator docstring and in
    ``PLATFORM-HARDENING-FLOORS``'s execution log) — not just make this test green.
    """
    inert = _inert_in(
        tmp_path,
        {
            "deserializer.py": """
                from enum import Enum

                class Coerced(str, Enum):
                    WRITTEN = "written"
                    NEVER_WRITTEN = "never_written"

                def load(row):
                    # Value lookup over a column WE wrote. Reaches NEVER_WRITTEN only if some
                    # writer ever produced it — and none does.
                    return Coerced(row["state"])

                def save(rec):
                    rec["state"] = Coerced.WRITTEN.value
                """,
        },
    )
    assert inert == {"Coerced.NEVER_WRITTEN"}, (
        "value-lookup construction cleared a member the census cannot prove is reachable; "
        f"got {sorted(inert)} — see PHF-13's verdict table before widening this rule"
    )


def test_the_audited_value_lookup_call_sites_are_wired_and_the_members_re_verdicted():
    """``PHF-13``'s ruling, RE-VERDICTED after ``WF2LOO-13`` wired the judge contract.

    PHF-13 reported ``Verdict.REPLAN``, ``Ratchet.RELAXED`` and ``Actor.WORKER`` inert even though
    each sits on a class with an ``E(value)`` construction, because the FUNCTIONS holding those
    constructions had no production caller — ``engine.py`` restated the judge aggregation rule
    instead of importing it. This test used to assert that dead-call-site premise and told the next
    reader to re-verdict if it ever changed. WF2LOO-13 changed it: ``validate_verdict``,
    ``hints_from_dict`` and ``resolve_transition`` are all called from the live judge path now.

    So the assertion is inverted rather than dropped, and it pins the re-verdict:

    * ``Verdict.REPLAN`` and ``Actor.WORKER`` left the baseline — the judge gate names both
      explicitly (``_judge_gate_outcome`` maps REPLAN; the actor ruling picks WORKER for a
      ``self_judge`` gate), so they are reachable by NAME, not merely by value lookup.
    * ``Ratchet.RELAXED`` stays inert, and that is the honest verdict: nothing names it, and no
      bundled template declares ``ratchet: relaxed``. Its only route in is a user template, which
      is exactly what "externally reachable, internal only" means in the detector's table.
    """
    owners = {
        "validate_verdict": "workflows/judge_contract.py",
        "hints_from_dict": "workflows/judge_contract.py",
        "resolve_transition": "workflows/judge_actors.py",
    }
    callers: dict[str, list[str]] = {name: [] for name in owners}
    for f in _src_py_files():
        rel = f.as_posix()
        text = f.read_text(encoding="utf-8")
        for name, owner in owners.items():
            if rel.endswith(owner):
                continue
            if f"{name}(" in text or f"import {name}" in text:
                callers[name].append(rel)
    stranded = [name for name, found in callers.items() if not found]
    assert not stranded, (
        f"{stranded} lost its production caller — the judge contract is authored-and-unrun again "
        "(the WF2LOO-12 defect). Re-verdict the members PHF-13 covers before touching the baseline."
    )

    baseline = _committed_inventory()
    flagged = {
        surface for entry in baseline["per_file"].values() for surface in entry.get("surfaces", [])
    }
    for cleared in ("enum:Verdict.REPLAN", "enum:Actor.WORKER"):
        assert cleared not in flagged, (
            f"{cleared} is flagged inert again while its call site is wired — either the wiring "
            "regressed or the detector's verdict did; read the code before regenerating"
        )
    assert "enum:Ratchet.RELAXED" in flagged, (
        "Ratchet.RELAXED left the baseline — if a template or a call site now names it, say so "
        "here; if not, this is a false clear and the ratchet just lost a member it was watching"
    )


def test_the_value_lookup_ruling_is_recorded_in_the_generator():
    """The verdicts are the deliverable, so they must live where the next reader lands: in the
    detector that produces the flags, not only in a plan log."""
    from scripts import generate_inert_surface_baseline as gen

    doc = (gen._inert_enum_members.__doc__ or "").lower()
    assert "deliberately not taught" in doc, "the PHF-13 ruling is missing from the detector"
    for marker in ("externally reachable", "internal only", "dead call site"):
        assert marker in doc, f"the per-site verdict vocabulary lost {marker!r}"
    assert "construction is the known remaining false-red shape" not in doc, (
        "PHF-12's superseded premise is asserted again in the detector docstring; "
        "judge_contract.py:342 has no production caller, so it does not make REPLAN reachable"
    )


# ── sdk_export census: a published signature IS the reader (#3496) ───────────────────────
#
# Every other kind this census measures has an in-repo consumer BY CONSTRUCTION — a config key
# is read by ``load()``, a trigger kind is dispatched under ``triggers/``. ``sdk_export`` is the
# exception: installable apps live in a SEPARATE repository, so "nothing in this repo imports
# it" cannot tell a public API type apart from an unkept promise. Closing the SDK surface under
# its own signatures exported 25 types that ALREADY-PUBLISHED fields and methods named
# (``ToolResult.agent_error``, ``Task.transition(to=)``, sixteen ``apps.manifest`` types), and
# this census scored all 26 surfaces as new declared-but-inert ones — which made "stop exporting
# the type apps need" the cheapest way to go green, i.e. the census pushing for the defect. A
# published signature is a reader, and unlike an out-of-repo app it is mechanically checkable,
# so it clears. These four tests pin both directions and the two rulings the rule rests on.
#
# #3511 widened "published signature" from a class's fields and methods to include an exported
# FUNCTION's parameters and return, in the SAME walk, so the census clear and the
# `test_sdk_surface_is_public` gap rail could not end up with two definitions of the phrase.
# That cleared 11 more and opened 22 new gaps, all 22 exported in that change.

#: The 26 surfaces the API-closure clear removed, each with the published field or signature
#: that requires it. Pinned by name rather than counted: the value of the finding is WHICH
#: types an app could not name, and a count would survive the list being silently rewritten.
_API_CLOSURE_CLEARED = frozenset(
    {
        "action.AgentError",  # ActionResult.agent_error / ToolResult.agent_error
        "tool.AgentError",
        "channel.AutoSkillProvenance",  # SkillsLoader.create_auto_skill(provenance)
        "channel.CapturedSession",  # CapturingState.get_linked_session() return
        "channel.ResourceRead",  # SkillsLoader.read_resource() return
        "channel.SkillResource",  # SkillsLoader.resources_for() return
        "channel.SubagentInfo",  # SubagentManager.get() return
        "channel.TaskState",  # Task.state / Task.transition(to=)
        "local_model.CapabilityMatrix",  # LocalModel.matrix
        "sandbox.ResourceCeilings",  # SandboxSpec.ceilings
        "manifest.AppSkill",  # AppManifest.skills
        "manifest.AutonomyConfig",  # ProviderConfig.autonomy
        "manifest.CliConfig",  # AppManifest.cli
        "manifest.ClientInstallConfig",  # PlatformConfig.clientInstall
        "manifest.CoreCompatibility",  # AppManifest.core_compatibility() return
        "manifest.CronEntry",  # AppManifest.crons
        "manifest.Dependencies",  # AppManifest.dependencies
        "manifest.MarketplaceDependencies",  # Dependencies.marketplace
        "manifest.PackSourceEntry",  # AppManifest.sources / pack_source() return
        "manifest.PlatformConfig",  # AppManifest.platform
        "manifest.ProposalKind",  # Permissions.proposals / proposal_kind() return
        "manifest.QualityDeclaration",  # AppManifest.quality
        "manifest.RouteEntry",  # BackendConfig.routes
        "manifest.UIConfig",  # AppManifest.ui
        "manifest.UIPage",  # UIConfig.pages
        "manifest.UISidebar",  # UIConfig.sidebar
    }
)

#: #3511's tranche, pinned the same way and for the same reason. Two groups in one set because
#: the census cannot tell them apart and neither may silently stop clearing:
#:
#: * the 11 ALREADY-EXPORTED surfaces that an exported function's signature cleared — 9 named
#:   by a function directly, plus ``model.ModelCatalog`` and ``skill.SkillsMarketplace``, which
#:   clear only because a function root drags a NEW class (``ProviderRegistry``,
#:   ``SkillsRegistry``) into the frontier whose own methods then name them. That indirection is
#:   the reason the count is 11 and not the 9 #3496 predicted, and it is worth a comment: the
#:   two root kinds compose, so admitting one can clear a surface neither reaches alone.
#: * the 19 NEWLY-EXPORTED types the widening owed an app (22 owed, minus the three the
#:   ``personalclaw.dashboard.`` closure exemption covers). Every one clears on the signature
#:   that owed it, which is why 19 new published names moved the counter DOWN.
_FUNCTION_ROOT_CLOSURE_CLEARED = frozenset(
    {
        # ── cleared by a function signature (the 11) ──
        "channel.ChannelTransportProvider",  # assert_channel_contract(provider)
        "channel.TrustVerdict",  # guard_inbound() return
        "feedback.FeedbackRecord",  # current_verdict() / record_feedback() return
        "image.ImageGenProvider",  # active_image_gen() return
        "model.MediaCatalog",  # register_media_catalog(catalog)
        "model.ModelCatalog",  # ProviderRegistry.build_catalog() return — via get_default_registry
        "net.EgressPolicy",  # egress_policy_for() / evaluate(policy) / fetch(policy)
        "net.GuardDecision",  # evaluate() return
        "skill.SkillsMarketplace",  # SkillsRegistry.get() return — via get_default_skills_registry
        "tts.TtsProvider",  # channel.voice_reply(provider)
        "video.VideoGenProvider",  # active_video_gen() return
        # ── newly exported because a published function owed them (19 of the 22) ──
        "channel.AuthConfig",  # resolve_bind_host(auth_cfg)
        "channel.AuthMode",  # AuthConfig.mode
        "channel.AutomationToolResult",  # delete_automation() / delete_all_automations() return
        "channel.McpServerInfo",  # list_servers() return
        "channel.ScheduleDefinition",  # format_schedule(schedule) / ScheduleJob.schedule
        "channel.ScheduleJob",  # compute_next_run_ts(job)
        "channel.SecurityEvent",  # SecurityEventLog.log(event)
        "channel.SecurityEventLog",  # sel() return
        "mcp.McpClientRegistry",  # get_mcp_client_registry() return
        "mcp.McpServerConn",  # McpClientRegistry.get() return
        "mcp.McpToolSpec",  # McpServerConn.list_tools() return
        "model.ProviderRegistry",  # get_default_registry() return
        "model.Segment",  # StreamingTagSplitter.feed() / flush() return
        "model.StreamingTagSplitter",  # make_think_splitter() return
        "net.ExtractOutcome",  # web_extract() return
        "net.FetchOutcome",  # web_fetch() return
        "net.FetchResponse",  # fetch() return
        "skill.InstallResult",  # SkillsRegistry.install_guarded() return
        "skill.SkillsRegistry",  # get_default_skills_registry() return
    }
)


def _reported_sdk_exports() -> set[str]:
    """``{"<sdk submodule>.<name>"}`` the census currently reports as inert."""
    from scripts.generate_inert_surface_baseline import _inert_sdk_export_surfaces

    return {
        f"{rel.rsplit('/', 1)[1].removesuffix('.py')}.{surface.split(':', 1)[1]}"
        for rel, surface in _inert_sdk_export_surfaces()
    }


@pytest.mark.timeout(300)
@pytest.mark.parametrize(
    "tranche,pinned",
    [
        ("#3496 fields+methods", _API_CLOSURE_CLEARED),
        ("#3511 functions", _FUNCTION_ROOT_CLOSURE_CLEARED),
    ],
)
def test_an_export_named_by_another_exports_signature_is_not_inert(tranche, pinned):
    """Direction one: the API closure clears, and it clears exactly the measured surfaces.

    Both halves are needed. ``consumed_exports()`` proves the closure SEES each one (a rule
    that resolved nothing would satisfy the census half trivially), and the census render
    proves the clear actually reaches the counter.

    Parametrized by tranche so a regression names WHICH widening broke: the #3496 set is
    reachable from class roots alone, the #3511 set needs exported functions seeded too. Merged
    into one set they would be indistinguishable, and un-seeding functions would read as a
    generic "the walk regressed" on 33 names instead of "the function half is gone".
    """
    from scripts.sdk_surface_closure import consumed_exports

    consumed = consumed_exports()
    missing = sorted(pinned - consumed)
    assert not missing, (
        f"[{tranche}] {missing} is exported so an app can fill a PUBLISHED field or call a "
        "PUBLISHED method or function, but the API-closure walk no longer finds the signature "
        "that requires it — either the signature moved or the walk regressed; read the code "
        "before regenerating"
    )
    still_reported = sorted(pinned & _reported_sdk_exports())
    assert not still_reported, (
        f"[{tranche}] {still_reported} is required by another export's signature yet the census "
        "still reports it inert — the clear is not reaching _inert_sdk_export_surfaces()"
    )


@pytest.mark.timeout(300)
def test_an_export_reachable_from_no_published_signature_is_still_inert():
    """Direction two — the teeth. A type exported for no reason is genuinely inert.

    This is the case worth keeping, and the rule must not have eaten it: the provider protocols
    an app subclasses, the service objects it is handed and the error classes it catches are
    named by NO published signature, so they stay reported. Measured when the class half landed:
    170 of 229 surfaces survive, 54 of them exported types. After #3511 admitted exported
    functions as roots: **159 of 248, 43 of them exported types**, leaving a headroom of THREE
    over the floor rather than fourteen — and the standing instruction was that a clear dropping
    it under 40 must RE-EXAMINE the floor against a fresh measurement rather than nudge the
    number, because the floor's whole job is that a rule which cleared every type would pass the
    test above and guard nothing.

    That re-examination has now happened once (OU-14), and the answer was that the clear rule is
    innocent: **153 ``sdk_export`` surfaces still reported, 39 of them exported types**. The
    four that left are TWO
    names, each exported from two facades, and each acquired a real in-repo importer rather than
    a new namer — so ``scripts/sdk_surface_closure.py`` is byte-identical and the clear did not
    widen by one line:

    * ``local_model.LocalModelProvider`` + ``diarization.LocalModelProvider`` — the bundled-chat
      app SUBCLASSES it (``class BundledChatProvider(ModelProvider, LocalModelProvider)``).
    * ``settings.ProviderSettings`` + ``channel.ProviderSettings`` — the same app CALLS it
      (``ProviderSettings.load(APP_NAME)``).

    Both are the census's primary reader shape (an ``ImportFrom`` in ``src/``), and the reason
    they moved now is that the first bundled app to consume these two facades landed in-tree;
    neither import is removable without deleting the feature. 39 still-reported types is not a
    suspiciously clean census, which is the only thing this floor is watching for.

    🔴 THE FLOOR IS NOW SET AT THE MEASUREMENT, WITH NO HEADROOM, DELIBERATELY. The previous
    three-wide gap is what let this departure arrive as a digit rather than as a decision. At 39
    the next export to leave reds here and has to be attributed by name, the same way
    ``_NO_FAMILY`` in ``test_audit_outcome_families.py`` replaced a ceiling with a named
    register. A named export that leaves this set has been wired, not excused: re-pin it above
    with the importer or signature that now names it, and move the floor only with that
    arithmetic attached.
    """
    import importlib

    reported = _reported_sdk_exports()
    types_only = set()
    for label in reported:
        mod_name, _, name = label.rpartition(".")
        obj = getattr(importlib.import_module(f"personalclaw.sdk.{mod_name}"), name, None)
        if isinstance(obj, type):
            types_only.add(label)
    assert len(types_only) >= 39, (
        f"only {len(types_only)} exported TYPES are still reported inert (54 when the "
        "API-closure clear landed, 43 after function roots, 39 after OU-14's app wired "
        "LocalModelProvider and ProviderSettings) — either the clear has over-reached, in which "
        "case narrow it rather than trusting a suspiciously clean census, or an export was "
        "genuinely wired, in which case name it and its new importer in this docstring. The "
        "floor sits ON the measurement so that choice cannot be skipped."
    )
    for orphan in (
        "action.ActionProvider",  # a provider protocol an app subclasses
        "channel.SessionManager",  # a service object an app is handed
        "manifest.AppManifest",  # cleared by nothing but its own self-edge
        "tool.ProjectionRule",  # a data type no published signature names
    ):
        assert orphan in types_only, f"{orphan} left the census — say what now names it"
    # `model.ModelCatalog` stood in the slot `tool.ProjectionRule` now holds until #3511, when
    # `get_default_registry() -> ProviderRegistry` made `ProviderRegistry.build_catalog()`
    # reachable and cleared it. `ProjectionRule` replaces it with a CONTROL the old pin lacked:
    # `project_and_retain` is the exported function that ought to name the rule, and its hints
    # RESOLVE — so "still reported" is a real finding about that signature (it returns a bare
    # `tuple[str, dict]`) and not a swallowed `get_type_hints` failure quietly faking a pin.
    #
    # 🔴 `getattr`, NOT `from personalclaw.sdk.tool import project_and_retain`. Measured while
    # writing this control: `_sdk_imported_names()` clears an export when an `ImportFrom` names
    # it ANYWHERE in `src/`, `tests/` or `apps/`, so spelling the import here cleared
    # `sdk_export:project_and_retain` and dropped the committed counter by one. A test that
    # imports a name in order to reason about whether it is inert makes it not-inert — the
    # control refutes itself, and it does so silently, as a plausible-looking extra shrink. The
    # same trap waits for any future assertion about a specific `sdk_export` surface.
    from scripts.sdk_surface_closure import signature_requirements

    project_and_retain = getattr(
        importlib.import_module("personalclaw.sdk.tool"), "project_and_retain"
    )
    assert typing.get_type_hints(project_and_retain), (
        "project_and_retain's hints no longer resolve, so `tool.ProjectionRule` being reported "
        "proves nothing — pick an orphan whose would-be namer still type-checks"
    )
    assert not signature_requirements(project_and_retain), (
        "project_and_retain now names a core type; if that type is ProjectionRule the pin above "
        "is wired and must move, with the signature recorded"
    )


@pytest.mark.timeout(300)
def test_a_type_that_names_only_itself_does_not_clear_itself():
    """Ruling one, UNCHANGED by #3511: a self-edge is not a reader.

    ``AppManifest.from_dict()`` returns ``AppManifest``; a self-referential constructor or
    ``with_overrides``-style method sits on most dataclasses in this tree, so counting it would
    clear nearly everything and leave the counter meaning nothing. The self-edge is asserted
    PRESENT first — without that control this test would pass just as well on a type with no
    methods at all, which is the shape of a rail that cannot fire.

    Thirty-four exports carry a self-edge; the ruling is LOAD-BEARING for the three below, i.e.
    the self-edge is the only thing that would have cleared them and they are all still reported.
    The other thirty-one clear on a real reader as well, so they would stay cleared whichever way
    this ruling went and prove nothing about it.

    ``net.EgressPolicy`` was the fourth until #3511 and is the reason this list is pinned rather
    than counted: admitting exported functions as roots gave it three genuine readers
    (``net.evaluate(policy)``, ``net.fetch(policy)``, ``net.egress_policy_for() -> EgressPolicy``),
    so it left the load-bearing set WITHOUT the ruling changing. A count would have read that as
    the ruling weakening. Two self-edge SHAPES are covered deliberately — a classmethod returning
    its own class (``AppManifest``, ``AppConfig``) and a singleton field typed as its own class
    (``Stats._instance``) — because the walk reaches them through different branches of
    ``types_an_app_must_name``.
    """
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.config.loader import AppConfig
    from personalclaw.stats import Stats
    from scripts.sdk_surface_closure import consumed_exports, types_an_app_must_name

    consumed = consumed_exports()
    reported = _reported_sdk_exports()
    for label, cls in (
        ("manifest.AppManifest", AppManifest),  # from_dict() / from_json_file() return
        ("channel.AppConfig", AppConfig),  # load() / load_with_migration_state() return
        ("channel.Stats", Stats),  # field _instance
    ):
        self_edges = [where for where, t in types_an_app_must_name(cls) if t is cls]
        assert self_edges, (
            f"{cls.__qualname__} no longer names itself in any field or signature, so this "
            "test's control is gone — pick another self-referential export"
        )
        assert label not in consumed, f"{label} cleared itself through {self_edges}"
        assert label in reported, (
            f"{label} left the census with only a self-edge to clear it. If a real reader now "
            "names it (as net.EgressPolicy gained one in #3511), move it out of this list and "
            "say which signature — do NOT relax the self-edge ruling to match"
        )


@pytest.mark.timeout(300)
def test_the_dashboard_closure_exemption_is_declared_load_bearing_and_still_justified():
    """The one exclusion from #3511's 22, and the three things that keep it from rotting.

    Admitting function roots made ``DashboardState`` owed (it types the first parameter of the
    published ``channel.save_session_to_history``) and ``SseRegistry``/``SseHub`` owed through it.
    Exporting them means ``sdk/channel.py`` importing ``personalclaw.dashboard.state`` and
    ``.sse`` — the FIFTH and SIXTH ``core-must-not-import-the-http-surface`` edges on a file the
    structural ratchet pins at four. That rule's own rationale is why the closure yields rather
    than the layering: *"Shrink-only GRANDFATHERS those instead of an exemption list — an
    allowlist is a thing that rots, a measured floor is not."*

    An exclusion with a reason in prose is a silent skip with extra words, so all three legs are
    asserted:

    1. **DECLARED** — the prefix is in ``CLOSURE_EXEMPT_PREFIX``, not dropped by an incidental
       filter somewhere in the walk.
    2. **LOAD-BEARING** — without it the gap rail really does red, and on exactly these three
       types. If a refactor moves them out of ``dashboard/``, this leg fails and the exemption
       should be deleted rather than kept for free.
    3. **STILL JUSTIFIED** — the structural baseline still pins ``sdk/channel.py`` at four
       HTTP-surface edges. If that file's floor ever rises for an unrelated reason, the
       "a fifth cannot be added" argument has changed and this exemption must be re-argued.
    """
    import scripts.sdk_surface_closure as closure

    assert "personalclaw.dashboard." in closure.CLOSURE_EXEMPT_PREFIX, (
        "the dashboard closure exemption is not declared in CLOSURE_EXEMPT_PREFIX — if the "
        "dashboard types are being skipped by something else, that is a silent skip"
    )

    # Leg 2: re-render the walk with ONLY this prefix removed.
    original = closure.CLOSURE_EXEMPT_PREFIX
    closure.surface_closure.cache_clear()
    try:
        closure.CLOSURE_EXEMPT_PREFIX = tuple(p for p in original if p != "personalclaw.dashboard.")
        would_gap = set(closure.surface_closure().gaps)
    finally:
        closure.CLOSURE_EXEMPT_PREFIX = original
        closure.surface_closure.cache_clear()
    assert would_gap == {
        "personalclaw.dashboard.sse.SseHub",
        "personalclaw.dashboard.sse.SseRegistry",
        "personalclaw.dashboard.state.DashboardState",
    }, (
        "without the dashboard exemption the gap rail reports "
        f"{sorted(would_gap)}. If that set is EMPTY the exemption is dead and must be deleted; "
        "if it names something outside dashboard/ the exemption is hiding an unrelated gap"
    )
    assert not closure.surface_closure().gaps, "the exemption must leave the gap rail closed"

    # Leg 3: the structural floor the argument rests on.
    structural = json.loads(
        (baseline_path().parent / "structural-baseline.json").read_text(encoding="utf-8")
    )
    entry = structural["structural-import-direction"]["per_file"]["src/personalclaw/sdk/channel.py"]
    assert entry["edges"] == 4 and all("dashboard" in v for v in entry["violations"]), (
        "sdk/channel.py's committed HTTP-surface edge count is no longer the four grandfathered "
        f"dashboard imports ({entry}) — the 'a fifth cannot be added' argument for the dashboard "
        "closure exemption has changed and must be re-argued, not inherited"
    )


@pytest.mark.timeout(300)
def test_an_exported_functions_signature_clears_an_export_in_both_directions():
    """Ruling two, INVERTED by #3511: an exported FUNCTION is an owner, in ONE walk.

    #3496 deliberately stopped at class roots and wrote the precondition for reversing itself:
    *"If that was intended, widen the gap rail in the same change and export the 22 types it then
    requires — do not widen one direction of the walk alone."* This test is that precondition,
    kept executable. The argument for widening is the argument that justified #3496 with one word
    changed: an app calling a published FUNCTION cannot name its parameter or return type either,
    so a typed integration is impossible and the failure surfaces as prose or ``Any``. A surface
    closed under fields and methods but not functions is closed against one caller and open
    against another, and nothing in the boundary's intent drew that line.

    What must hold is BOTH directions at once, because sharing one walk is the whole mechanism:

    1. the census half clears — ``net.evaluate() -> GuardDecision`` is a reader, so
       ``net.GuardDecision`` is no longer reported; and
    2. the gap half is closed — the 22 types the widening then owes an app ARE exported, so
       ``surface_closure().gaps`` is empty.

    Asserting (1) alone is what #3496 forbade: it would let the census go quiet while the facade
    still owed an app twenty-two unnameable types, which is the incoherence one walk prevents.

    Each half carries its own control, because both assertions are satisfiable by a walk that
    resolved nothing: the function really does name the type, and the walk really did seed
    functions (``function_roots`` non-zero) rather than reach ``GuardDecision`` some other way.
    """
    from personalclaw.net import GuardDecision
    from personalclaw.sdk import net as sdk_net
    from scripts.sdk_surface_closure import core_types_in, surface_closure

    named = [
        arg
        for arg, ann in typing.get_type_hints(sdk_net.evaluate).items()
        if GuardDecision in core_types_in(ann)
    ]
    assert named, (
        "sdk.net.evaluate no longer names GuardDecision, so this test's control is gone — "
        "pick another exported function whose signature names an exported type"
    )
    closure = surface_closure()
    assert closure.function_roots, (
        "the closure walk seeded ZERO exported functions, so nothing below measures the function "
        "half — GuardDecision clearing would prove only that some class names it"
    )
    assert "net.GuardDecision" not in _reported_sdk_exports(), (
        f"sdk.net.evaluate({named[0]}) names GuardDecision and functions are roots "
        f"({closure.function_roots} of them), yet the census still reports net.GuardDecision — "
        "the function half of the clear is not reaching _inert_sdk_export_surfaces()"
    )
    assert not closure.gaps, (
        "an exported function's signature now CLEARS for the census, but the gap rail is open: "
        f"{sorted(closure.gaps)} are named by a published signature and exported by no sdk "
        "module. One walk serves both directions — widening only the census half is the exact "
        "incoherence #3496 refused to ship"
    )


def test_forbidden_to_raise_doc_line_is_present():
    """done_when: "the forbidden-to-raise doc line is present" — in BOTH the generator and
    this test, so neither can drop it unnoticed."""
    from scripts import generate_inert_surface_baseline as gen

    assert _FORBIDDEN_TO_RAISE in (gen.__doc__ or "").lower()
    assert _FORBIDDEN_TO_RAISE in (__doc__ or "").lower()
