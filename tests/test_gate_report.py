"""Aggregate-gate report tests.

``scripts/gate_report.py`` runs the six drift/inert gates (config-baseline,
inert-surface, docs-lint, and the three structural ratchets) and reports EVERY failure in
one run — the "aggregate, don't short-circuit" ergonomic. These tests prove the contract:

  * three INDEPENDENT failures all surface in a single ``run_all_gates()`` call — none masked;
  * a gate that RAISES is captured as a failure and does NOT stop the others;
  * an all-pass run exits 0 and the table shows every gate PASS;
  * the one rendered table carries every failure (all gate names + FAIL markers) in one string.

All failure scenarios are SYNTHETIC — driven by monkeypatching each generator's
``baseline_path`` (the inert census's ``sites_path``) and ``build_inventory`` (mirroring how
the existing ratchet tests drive the shared comparisons with synthetic data). The real
committed ``config-baseline.json`` / ``docs-lint-baseline.json`` and the inert-surface site
list are never mutated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate_config_baseline as config_gen
from scripts import generate_docs_lint_baseline as docs_gen
from scripts import generate_inert_surface_baseline as inert_gen
from scripts import generate_structural_baseline as structural_gen
from scripts.gate_report import GateResult, main, render_report, run_all_gates

# The three drift/inert gates this module seeds failures for.
_GATE_NAMES = ["config-baseline", "inert-surface", "docs-lint"]
# Every gate the aggregate registers, in order. The three structural ratchets come last;
# they are asserted by NAME here so a future registration can neither drop one silently nor
# reorder the table.
_ALL_GATE_NAMES = _GATE_NAMES + [
    "structural-size",
    "structural-import-direction",
    "structural-duplication",
]


def _write_json(path: Path, obj: object) -> Path:
    path.write_text(json.dumps(obj) + "\n", encoding="utf-8")
    return path


def _seed_config_drift(monkeypatch, tmp_path: Path) -> None:
    """Make config-baseline drift: point ``baseline_path`` at a committed copy that does NOT
    match the (real) fresh render, so the byte-compare fails. ``build_baseline`` stays real —
    the truth — so this is exactly the "committed drifted from the schema" case."""
    stale = tmp_path / "config-baseline.json"
    stale.write_text("[]\n", encoding="utf-8")  # empty schema != the real render
    monkeypatch.setattr(config_gen, "baseline_path", lambda: stale)


#: The one surface the seeded inert census finds, and the site the gate must name.
_INERT_SITE = "src/personalclaw/synthetic.py::enum:SyntheticProbe.NEW_MEMBER"


def _write_sites(path: Path, *sites: str) -> Path:
    path.write_text(
        "".join(json.dumps({"site": site, "reason": "synthetic"}) + "\n" for site in sites),
        encoding="utf-8",
    )
    return path


def _seed_inert_drift(monkeypatch, tmp_path: Path) -> None:
    """Make inert-surface drift: a committed site list that holds another surface of the file
    and a current inventory with one it does not hold, so the REAL ``unlisted()`` names it."""
    held = _write_sites(
        tmp_path / "inert.sites.jsonl", "src/personalclaw/synthetic.py::enum:SyntheticProbe.OLD"
    )
    monkeypatch.setattr(inert_gen, "sites_path", lambda: held)
    monkeypatch.setattr(
        inert_gen,
        "build_inventory",
        lambda: {
            "per_file": {
                "src/personalclaw/synthetic.py": {
                    "inert": 1,
                    "surfaces": ["enum:SyntheticProbe.NEW_MEMBER"],
                }
            }
        },
    )


def _seed_docs_drift(monkeypatch, tmp_path: Path) -> None:
    """Make docs-lint drift: a committed baseline of 0 for a doc and a current inventory of 1
    for it, so the REAL shrink-only ``regressions()`` flags a rise."""
    baseline = _write_json(
        tmp_path / "docs-lint-baseline.json",
        {"per_file": {"docs/synthetic.md": {"total": 0, "findings": []}}},
    )
    monkeypatch.setattr(docs_gen, "baseline_path", lambda: baseline)
    monkeypatch.setattr(
        docs_gen,
        "build_inventory",
        lambda: {
            "per_file": {
                "docs/synthetic.md": {
                    "total": 1,
                    "findings": ["dead_link:docs/does-not-exist.md"],
                }
            }
        },
    )


#: A structural baseline every ratchet's real comparison reads as clean.
_STRUCTURAL_CLEAN = {
    structural_gen.RATCHET_SIZE: {
        "ceiling_lines": 1000,
        "watch_band_lines": 800,
        "watch_band_members": [],
        "over_ceiling": {},
    },
    structural_gen.RATCHET_IMPORT_DIRECTION: {"per_file": {}},
    structural_gen.RATCHET_DUPLICATION: {"per_file": {}},
}


@pytest.fixture
def gates(monkeypatch, tmp_path):
    """Every one of the six gates on stubs that pass: a committed baseline and a fresh render or
    inventory of the test's own, so no gate reads the real tree. The rails job runs the gates on
    the real tree once (``scripts/gate_report.py``); these tests prove how the aggregate reports
    them. A seed or a raise a test adds replaces one stub; the real comparisons do the rest."""
    # A render of the stub's own, so ``_seed_config_drift``'s committed ``[]`` differs from it.
    render = '[{"path": "stub.field"}]\n'
    config = tmp_path / "config-baseline.json"
    config.write_text(render, encoding="utf-8")
    monkeypatch.setattr(config_gen, "baseline_path", lambda: config)
    monkeypatch.setattr(config_gen, "build_baseline", lambda: render)
    empty = _write_json(tmp_path / "empty-baseline.json", {"per_file": {}})
    no_sites = _write_sites(tmp_path / "empty.sites.jsonl")
    monkeypatch.setattr(inert_gen, "sites_path", lambda: no_sites)
    monkeypatch.setattr(inert_gen, "build_inventory", lambda: {"per_file": {}})
    monkeypatch.setattr(docs_gen, "baseline_path", lambda: empty)
    monkeypatch.setattr(docs_gen, "build_inventory", lambda: {"per_file": {}})
    structural = _write_json(tmp_path / "structural-baseline.json", _STRUCTURAL_CLEAN)
    monkeypatch.setattr(structural_gen, "baseline_path", lambda: structural)
    monkeypatch.setattr(
        structural_gen, "build_inventory", lambda: json.loads(json.dumps(_STRUCTURAL_CLEAN))
    )
    monkeypatch.setattr(structural_gen, "vacuity_failures", lambda ratchet=None: [])


def _seed_structural(monkeypatch, ratchet: str) -> None:
    """Make one structural ratchet fail by its REAL comparison: the current inventory gains one
    band entrant, one upward edge or one re-derivation over the clean committed baseline."""
    current = json.loads(json.dumps(_STRUCTURAL_CLEAN))
    if ratchet == structural_gen.RATCHET_SIZE:
        current[ratchet]["watch_band_members"] = ["src/personalclaw/synthetic_giant.py"]
    elif ratchet == structural_gen.RATCHET_IMPORT_DIRECTION:
        current[ratchet]["per_file"] = {
            "src/personalclaw/synthetic_edge.py": {"edges": 1, "violations": ["up:synthetic"]}
        }
    else:
        current[ratchet]["per_file"] = {
            "src/personalclaw/synthetic_copy.py": {"count": 1, "sites": ["synthetic:site"]}
        }
    before = structural_gen.build_inventory

    def seeded():
        inventory = before()
        inventory[ratchet] = current[ratchet]
        return inventory

    monkeypatch.setattr(structural_gen, "build_inventory", seeded)


def test_three_independent_failures_all_surface_in_one_run(gates, monkeypatch, tmp_path):
    """The core property: a tree with three independent failures reports all three in one run.

    Each gate is seeded to fail independently; ``run_all_gates()`` must return all three as
    ``ok=False`` in a single call — none masked by another's failure — and the aggregate
    counts three failed gates, while the three it did not seed pass."""
    _seed_config_drift(monkeypatch, tmp_path)
    _seed_inert_drift(monkeypatch, tmp_path)
    _seed_docs_drift(monkeypatch, tmp_path)

    results = run_all_gates()

    assert [r.name for r in results] == _ALL_GATE_NAMES
    by_name = {r.name: r for r in results}
    seeded = [by_name[n] for n in _GATE_NAMES]
    assert all(not r.ok for r in seeded), results
    assert all(r.failures for r in seeded), results
    # Every seeded gate's failure is real and attributed (not the raise path).
    assert "config-baseline.json is stale" in by_name["config-baseline"].failures[0]
    assert by_name["inert-surface"].failures == [
        f"Site {_INERT_SITE} is a new declared-but-inert surface, not listed in "
        f"{inert_gen.SITES}: add the missing writer or reader"
    ]
    assert any("synthetic.md" in line for line in by_name["docs-lint"].failures)
    assert all(by_name[n].ok for n in structural_gen.RATCHETS), results

    assert main() == 1


def test_all_six_gates_fail_at_once_and_each_is_reported(gates, monkeypatch, tmp_path, capsys):
    """No gate masks another: all six seeded at once, all six FAIL in one run and one table."""
    _seed_config_drift(monkeypatch, tmp_path)
    _seed_inert_drift(monkeypatch, tmp_path)
    _seed_docs_drift(monkeypatch, tmp_path)
    for ratchet in structural_gen.RATCHETS:
        _seed_structural(monkeypatch, ratchet)

    results = run_all_gates()

    assert [r.name for r in results] == _ALL_GATE_NAMES
    assert all(not r.ok and r.failures for r in results), results
    assert not any("raised" in line for r in results for line in r.failures), results
    assert main() == 1
    assert "SUMMARY: 6 of 6 gate(s) FAILED" in capsys.readouterr().out


#: How each gate is made to raise: the call its body makes first, replaced by one that raises.
_RAISERS = {
    "config-baseline": lambda mp, boom: mp.setattr(config_gen, "build_baseline", boom),
    "inert-surface": lambda mp, boom: mp.setattr(inert_gen, "build_inventory", boom),
    "docs-lint": lambda mp, boom: mp.setattr(docs_gen, "build_inventory", boom),
    **{
        ratchet: (
            lambda mp, boom, ratchet=ratchet: mp.setattr(
                structural_gen,
                "vacuity_failures",
                lambda r=None: boom() if r == ratchet else [],
            )
        )
        for ratchet in structural_gen.RATCHETS
    },
}


@pytest.mark.parametrize("raiser", _ALL_GATE_NAMES)
def test_a_raising_gate_is_captured_and_does_not_stop_the_others(gates, monkeypatch, raiser):
    """A gate that RAISES is reported as a failure, and every OTHER gate still runs and passes —
    the non-short-circuit guarantee, for each of the six gates in turn."""

    def boom(*_args):
        raise RuntimeError("synthetic gate explosion")

    _RAISERS[raiser](monkeypatch, boom)

    results = run_all_gates()
    by_name = {r.name: r for r in results}

    assert [r.name for r in results] == _ALL_GATE_NAMES
    assert by_name[raiser].ok is False
    assert by_name[raiser].failures == ["gate raised RuntimeError: synthetic gate explosion"]
    for name in _ALL_GATE_NAMES:
        if name != raiser:
            assert by_name[name].ok, by_name[name]


def test_the_inert_surface_gate_names_an_unlisted_site_and_passes_a_listed_one(
    gates, monkeypatch, tmp_path
):
    """The inert-surface gate compares the census's sites with the committed site list: the
    seeded surface fails by its site while the list lacks it, and passes once a line holds it.
    A listed surface the census no longer finds is the rail's to name, not this gate's."""
    _seed_inert_drift(monkeypatch, tmp_path)
    assert not {r.name: r for r in run_all_gates()}["inert-surface"].ok
    held = _write_sites(
        tmp_path / "held.sites.jsonl", _INERT_SITE, "src/personalclaw/gone.py::enum:G.ONE"
    )
    monkeypatch.setattr(inert_gen, "sites_path", lambda: held)
    gate = {r.name: r for r in run_all_gates()}["inert-surface"]
    assert gate.ok and gate.failures == [], gate


def test_an_all_pass_run_exits_0_with_every_gate_pass(gates, capsys):
    """Every gate passes: ``main()`` exits 0, and the table shows one PASS row per gate."""
    assert main() == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == len(_ALL_GATE_NAMES)
    assert "FAIL" not in out
    assert f"SUMMARY: all {len(_ALL_GATE_NAMES)} gate(s) passed." in out


def test_report_renders_every_failure_in_one_table():
    """The rendered report is a SINGLE string carrying every gate name and, for each failing
    gate, its FAIL marker and failure lines — all three failures visible in one run."""
    results = [
        GateResult("config-baseline", False, ["config-baseline.json is stale — run ..."]),
        GateResult(
            "inert-surface",
            False,
            ["Site src/personalclaw/x.py::enum:X.NEW is a new declared-but-inert surface, ..."],
        ),
        GateResult("docs-lint", False, ["docs/y.md: docs-lint findings rose 0 -> 1; new ..."]),
    ]
    report = render_report(results)

    for name in _GATE_NAMES:
        assert name in report
        # Each failing gate gets a table row cell and a "<gate> FAIL (...)" detail header.
        assert f"{name} FAIL (" in report
    # Three FAIL cells in the table (one per gate row), independent of the summary wording.
    table = report.split("\n\n", 1)[0]
    assert table.count("FAIL") == 3
    assert "config-baseline.json is stale" in report
    assert "is a new declared-but-inert surface" in report
    assert "docs-lint findings rose" in report
    assert "SUMMARY: 3 of 3 gate(s) FAILED, 3 failure(s) total." in report


def test_report_is_deterministic(gates, monkeypatch, tmp_path):
    """Same results in → byte-identical report out (fixed columns, pre-sorted lines, no
    timestamps or absolute paths), a failing run's included."""
    _seed_inert_drift(monkeypatch, tmp_path)
    assert render_report(run_all_gates()) == render_report(run_all_gates())


def test_gateresult_ok_matches_emptiness_of_failures(gates, monkeypatch, tmp_path):
    """The one normalized shape: a passing gate carries no failures; a failing gate carries
    at least one. run_all_gates() always returns exactly the registered gates, in order."""
    _seed_docs_drift(monkeypatch, tmp_path)
    results = run_all_gates()
    assert [r.name for r in results] == _ALL_GATE_NAMES
    assert any(not r.ok for r in results) and any(r.ok for r in results)
    for r in results:
        assert isinstance(r, GateResult)
        assert r.ok == (not r.failures), r
