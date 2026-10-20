"""The real-tree checks split from ``tests/test_structural_baseline.py``.

``scripts/gate_report.py`` in the rails-checks job holds each structural ratchet's vacuity check and
shrink-only half; these hold what it does not: the committed baseline byte-matches a fresh render
and is not stale on the shrink side, the census is the real population, and a walk whose every parse
fails still fires vacuity. They run once per CI run, in the rails job. The module named above says
what they hold
to, and its own tests prove each check works on trees they plant.
"""

from __future__ import annotations

import json

import pytest

from scripts import generate_structural_baseline as gen


def _committed() -> dict:
    path = gen.baseline_path()
    assert path.is_file(), (
        "structural-baseline.json is missing — generate it with "
        "`python scripts/generate_structural_baseline.py`"
    )
    return json.loads(path.read_text(encoding="utf-8"))


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


def test_every_ratchet_has_a_vacuity_assertion_and_it_holds_on_the_real_tree():
    """The three vacuity checks pass on the real tree, and the census is the real population —
    not a handful of files a broken walk happened to find."""
    assert gen.census_py_files() >= gen.MIN_CENSUS_PY_FILES
    assert len(gen.census_packages()) > 50, "the package census collapsed"
    assert gen.vacuity_failures() == []
    for ratchet in gen.RATCHETS:
        assert gen.vacuity_failures(ratchet) == []


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
