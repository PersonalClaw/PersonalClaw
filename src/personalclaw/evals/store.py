"""The evals store layout + helpers (EVALUATION-SUBSTRATE §1.1).

Everything lives under ``~/.personalclaw/evals/``. Whole-file JSON artifacts
(``experiment.json``/``aggregates.json``/``trials.json`` per matrix) go through
``atomic_write``; the cross-run ``results.tsv`` ledger is append-only — a plain
``open(..., "a")`` with the parent ensured, matching how the guardrails audit
appends its jsonl.

ES-1a physically supports the ``matrices/`` subtree and the ``results.tsv`` ledger;
ES-2 adds the ``scenarios/`` subtree (the versioned Loop-1 library — see
:mod:`personalclaw.evals.scenarios`). The ``studies/``/``benchmarks/``/``trust/``
subtrees named in §1.1 are owned by later atoms (ES-5/ES-8): this module does NOT
create them, because a dir with no writer is dead scaffolding. The single
``StateEntry`` for ``evals`` (in ``durability/inventory.py``) claims the whole tree
for backup regardless of which subtrees exist yet.

ES-2 also makes the ledger PINNED: :func:`append_result` takes a required ``pin``
and refuses a row whose pin is absent or incomplete. That refusal is the one
chokepoint every future writer (matrix, study, gate) passes through, so "a run
without a pin cannot be written to results.tsv" holds for writers that do not exist
yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

if TYPE_CHECKING:  # pragma: no cover - typing only; importing at runtime would cycle
    from personalclaw.evals.pinning import RunPin

# The append-only cross-run ledger's stable, ordered columns (§1.1). A new column
# must be appended, never inserted, so old rows stay parseable by position. The
# trailing five are the ES-2 pin columns; ``model_fp`` (declared by ES-1, written by
# nobody until now) is the pin's fingerprint digest rather than a sixth new column.
RESULTS_COLUMNS: tuple[str, ...] = (
    "study_id",
    "kind",
    "verdict",
    "score_old",
    "score_new",
    "k",
    "model_fp",
    "ts",
    "scenario_id",
    "scenario_sha256",
    "prompt_pack_sha256",
    "config_snapshot_ref",
    "fixture_home",
)


class PinRequiredError(ValueError):
    """Raised when a caller tries to write a ledger row without a complete RunPin."""


# ── path helpers ─────────────────────────────────────────────────────────────


def evals_root() -> Path:
    """``~/.personalclaw/evals/`` — created on first reference."""
    root = config_dir() / "evals"
    root.mkdir(parents=True, exist_ok=True)
    return root


def matrices_dir() -> Path:
    """``evals/matrices/`` — the experiment-matrix output tree."""
    d = evals_root() / "matrices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def matrix_dir(matrix_id: str) -> Path:
    """``evals/matrices/<matrix_id>/`` — one matrix's artifact directory."""
    d = matrices_dir() / matrix_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def scenarios_dir() -> Path:
    """``evals/scenarios/`` — the installed, versioned Loop-1 scenario library."""
    d = evals_root() / "scenarios"
    d.mkdir(parents=True, exist_ok=True)
    return d


def benchmarks_dir() -> Path:
    """``evals/benchmarks/`` — the fixture-set tree (§1.1).

    Created on reference now that ES-4 is a writer of it. The judge benchmark reads its
    shipped fixture sets out of the PACKAGE and only looks here for the user's own or
    locally edited sets (the ``prompt_pack`` resolution rule: the home wins when it has a
    file of that name), so this dir stays empty until the user puts something in it.
    """
    d = evals_root() / "benchmarks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def judge_benchmarks_dir() -> Path:
    """``evals/benchmarks/judge/`` — judge fixture sets (§6)."""
    d = benchmarks_dir() / "judge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def results_path() -> Path:
    """``evals/results.tsv`` — the append-only cross-run ledger."""
    return evals_root() / "results.tsv"


# ── the append-only results ledger ───────────────────────────────────────────


def _tsv_cell(value: object) -> str:
    """Render one cell, neutralizing the tab/newline that would break the row."""
    text = "" if value is None else str(value)
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def append_result(row: dict, *, pin: "RunPin") -> None:
    """Append one tab-separated line to ``evals/results.tsv``.

    The ledger is append-only (every attempt logged, including failures) — so this
    is a plain ``open(..., "a")`` rather than ``atomic_write`` (which rewrites whole
    files). The file is created with a header row when absent; columns are the
    stable, ordered :data:`RESULTS_COLUMNS`, and unknown keys in ``row`` are ignored
    so a caller cannot silently widen the ledger.

    ``pin`` is REQUIRED (ES-2). A missing or incomplete
    :class:`~personalclaw.evals.pinning.RunPin` raises :class:`PinRequiredError`
    BEFORE the file is touched — an unattributable score never lands in the ledger,
    and the pin's own columns are written from the pin, not from ``row``, so a caller
    cannot pass a score with someone else's pin values.
    """
    if pin is None or not pin.is_complete():
        missing = pin.missing_parts() if pin is not None else ["<no pin>"]
        raise PinRequiredError(
            "refusing to write results.tsv without a complete RunPin "
            f"(missing: {', '.join(missing)})"
        )
    row = {**row, **pin.to_row()}
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    need_header = not path.exists()
    line = "\t".join(_tsv_cell(row.get(col)) for col in RESULTS_COLUMNS)
    with path.open("a", encoding="utf-8") as fh:
        if need_header:
            fh.write("\t".join(RESULTS_COLUMNS) + "\n")
        fh.write(line + "\n")


def read_results() -> list[dict]:
    """Read the ledger back as a list of column→value dicts (header skipped).

    Empty/missing ledger → ``[]``. Rows are split on tab and zipped to
    :data:`RESULTS_COLUMNS`; a short/long row is tolerated by ``zip`` truncation so
    a malformed line never aborts the read.
    """
    path = results_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for line in lines[1:]:  # skip header
        if not line:
            continue
        cells = line.split("\t")
        rows.append(dict(zip(RESULTS_COLUMNS, cells)))
    return rows


# ── per-matrix JSON artifacts (retention sinks ES-1b fills) ──────────────────


def write_matrix_experiment(matrix_id: str, spec_dict: dict) -> Path:
    """Persist a matrix's ``experiment.json`` (its spec) via ``atomic_write``."""
    path = matrix_dir(matrix_id) / "experiment.json"
    atomic_write(path, json.dumps(spec_dict, indent=2, sort_keys=True))
    return path


def read_matrix_experiment(matrix_id: str) -> dict | None:
    """Read a matrix's ``experiment.json``, or ``None`` if it doesn't exist."""
    path = matrix_dir(matrix_id) / "experiment.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_matrix_aggregates(matrix_id: str, agg_dict: dict) -> Path:
    """Persist a matrix's ``aggregates.json`` via ``atomic_write``."""
    path = matrix_dir(matrix_id) / "aggregates.json"
    atomic_write(path, json.dumps(agg_dict, indent=2, sort_keys=True))
    return path


def read_matrix_aggregates(matrix_id: str) -> dict | None:
    """Read a matrix's ``aggregates.json``, or ``None`` if it doesn't exist."""
    path = matrix_dir(matrix_id) / "aggregates.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_matrix_trials(matrix_id: str, cells: list) -> Path:
    """Persist a matrix's per-cell ``trials.json`` via ``atomic_write``.

    Each entry is one cell's coords/outcome/score/artifact_ref — the drill-down
    behind the ``aggregates.json`` summary (§1.2's "a surprising aggregate is always
    drillable to the run that produced it"). ``cells`` are :class:`CellResult`-shaped
    objects; they are serialized through their ``to_dict`` so this store stays
    dataclass-agnostic (no import of the matrix TYPES, no cycle).
    """
    path = matrix_dir(matrix_id) / "trials.json"
    rows = [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in cells]
    atomic_write(path, json.dumps(rows, indent=2, sort_keys=True))
    return path


def read_matrix_trials(matrix_id: str) -> list | None:
    """Read a matrix's ``trials.json``, or ``None`` if it doesn't exist."""
    path = matrix_dir(matrix_id) / "trials.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
