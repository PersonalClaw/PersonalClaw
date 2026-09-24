"""Aggregate-gate report tests (PLATFORM-HARDENING-FLOORS §6 — PHF-11).

``scripts/gate_report.py`` runs the six §3/§6 drift/inert gates (config-baseline,
inert-surface, docs-lint, and PHF-14's three structural ratchets) and reports EVERY failure in
one run — the "aggregate, don't short-circuit" ergonomic. These tests prove the contract:

  * three INDEPENDENT failures all surface in a single ``run_all_gates()`` call — none masked;
  * a gate that RAISES is captured as a failure and does NOT stop the others;
  * an all-pass run exits 0 and the table shows every gate PASS;
  * the one rendered table carries every failure (all gate names + FAIL markers) in one string.

All failure scenarios are SYNTHETIC — driven by monkeypatching each generator's
``baseline_path``/``build_inventory`` (mirroring how the existing ratchet tests drive the
shared ``regressions()`` with synthetic dicts). The real committed
``config-baseline.json`` / ``inert-surface-baseline.json`` / ``docs-lint-baseline.json`` are
never mutated.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import generate_config_baseline as config_gen
from scripts import generate_docs_lint_baseline as docs_gen
from scripts import generate_inert_surface_baseline as inert_gen
from scripts.gate_report import GateResult, main, render_report, run_all_gates

# The three drift/inert gates this module seeds failures for.
_GATE_NAMES = ["config-baseline", "inert-surface", "docs-lint"]
# Every gate the aggregate registers, in order. PHF-14 appended the three structural ratchets;
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


def _seed_inert_drift(monkeypatch, tmp_path: Path) -> None:
    """Make inert-surface drift: a committed baseline of 0 for a file and a current inventory
    of 1 for it, so the REAL shrink-only ``regressions()`` flags a rise."""
    baseline = _write_json(
        tmp_path / "inert-surface-baseline.json",
        {"per_file": {"src/personalclaw/synthetic.py": {"inert": 0, "surfaces": []}}},
    )
    monkeypatch.setattr(inert_gen, "baseline_path", lambda: baseline)
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


def test_three_independent_failures_all_surface_in_one_run(monkeypatch, tmp_path):
    """done_when core: a tree with three independent failures reports all three in one run.

    Each gate is seeded to fail independently; ``run_all_gates()`` must return all three as
    ``ok=False`` in a single call — none masked by another's failure — and the aggregate
    counts three failed gates."""
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
    assert any("synthetic.py" in line for line in by_name["inert-surface"].failures)
    assert any("synthetic.md" in line for line in by_name["docs-lint"].failures)

    assert main() == 1


def test_a_raising_gate_is_captured_and_does_not_stop_the_others(monkeypatch):
    """A gate that RAISES is reported as a failure, and every OTHER gate still runs — the
    non-short-circuit guarantee, proven in BOTH directions by raising the MIDDLE gate.

    On a clean tree (Step 4 guarantees the committed baselines are current) the two
    unpatched gates pass; the assertions below only require that they RAN (present, and their
    failures, if any, are not the raise-capture text) so the proof does not depend on tree
    cleanliness."""

    def boom():
        raise RuntimeError("synthetic gate explosion")

    monkeypatch.setattr(inert_gen, "build_inventory", boom)

    results = run_all_gates()
    by_name = {r.name: r for r in results}

    # The raiser (middle gate) is captured as a FAIL carrying the exception text.
    assert by_name["inert-surface"].ok is False
    assert any("raised RuntimeError" in line for line in by_name["inert-surface"].failures)

    # The gate BEFORE it and the gate AFTER it both still ran (present, and not the raiser).
    assert by_name["config-baseline"].name == "config-baseline"
    assert by_name["docs-lint"].name == "docs-lint"
    for name in _ALL_GATE_NAMES:
        if name == "inert-surface":
            continue
        assert not any("raised" in line for line in by_name[name].failures), by_name[name]
    assert [r.name for r in results] == _ALL_GATE_NAMES


def test_all_gates_pass_on_a_clean_tree(capsys):
    """On the real (clean) tree every gate passes, ``main()`` exits 0, and the table shows one
    PASS row per registered gate."""
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
            ["src/personalclaw/x.py: inert surfaces rose 0 -> 1; new ..."],
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
    assert "inert surfaces rose" in report
    assert "docs-lint findings rose" in report
    assert "SUMMARY: 3 of 3 gate(s) FAILED, 3 failure(s) total." in report


def test_report_is_deterministic():
    """Same results in → byte-identical report out (fixed columns, pre-sorted lines, no
    timestamps or absolute paths)."""
    results = run_all_gates()
    assert render_report(results) == render_report(results)


def test_gateresult_ok_matches_emptiness_of_failures():
    """The one normalized shape: a passing gate carries no failures; a failing gate carries
    at least one. run_all_gates() always returns exactly the registered gates, in order."""
    results = run_all_gates()
    assert [r.name for r in results] == _ALL_GATE_NAMES
    for r in results:
        assert isinstance(r, GateResult)
        assert r.ok == (not r.failures), r
