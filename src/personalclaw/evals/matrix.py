"""Experiment-matrix TYPES (EVALUATION-SUBSTRATE §1.2).

One matrix runner is meant to serve five consumers (studies, ablation, retrieval,
judge benchmark, bake-off). ES-1a defines only the TYPES those consumers share —
the spec, the per-cell result, the whole-matrix result, and the three-state
aggregation. The ``run_matrix`` execution body (subprocess spawning, per-cell
timeouts, cost preflight) is ES-1b; nothing here spawns a process or calls a model.

The three-state outcome is load-bearing: an infra error / timeout / ``None`` reward
is ``VERIFIER_ABSENT``, never a zero score. :func:`aggregate` computes the mean over
``PASSED``/``FAILED`` cells ONLY and reports ``verifier_absent`` as a separate count,
so an unrunnable check can never be averaged in as a failure.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

# ── the three-state outcome (auto-harness semantics) ─────────────────────────
# A verifier that could not run (exit 127, timeout, infra error, None reward) is
# NEVER averaged as 0 into a recommendation — it is reported separately.
PASSED = "passed"
FAILED = "failed"
VERIFIER_ABSENT = "verifier_absent"
OUTCOMES = (PASSED, FAILED, VERIFIER_ABSENT)

# Outcomes that carry a real score and therefore feed the mean. VERIFIER_ABSENT
# is deliberately excluded.
_SCORED_OUTCOMES = (PASSED, FAILED)


@dataclass(frozen=True)
class MatrixSpec:
    """One experiment matrix: a subject evaluated across axes.

    ``axes`` is a dict of lists (``{model: [...], iterations: [...], ...}``) kept
    JSON-safe so a spec round-trips through :meth:`to_dict`/:meth:`from_dict`.
    """

    subject: str  # template id | retrieval-arm set | judge fixture set | use-case
    axes: dict[str, list] = field(default_factory=dict)
    trial_count: int = 3
    scorer: str = ""  # "judge" | "assertion" | "qrels" | "command"
    budget_usd: float = 0.0  # hard cap — the ES-1b runner refuses a cell it can't afford

    def to_dict(self) -> dict:
        # Copy the axes lists so the frozen spec's mapping can't be mutated through
        # the returned dict (and vice versa on from_dict).
        return {
            "subject": self.subject,
            "axes": {k: list(v) for k, v in self.axes.items()},
            "trial_count": self.trial_count,
            "scorer": self.scorer,
            "budget_usd": self.budget_usd,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MatrixSpec":
        raw_axes = data.get("axes") or {}
        axes = {str(k): list(v) for k, v in raw_axes.items()}
        return cls(
            subject=str(data.get("subject", "")),
            axes=axes,
            trial_count=int(data.get("trial_count", 3)),
            scorer=str(data.get("scorer", "")),
            budget_usd=float(data.get("budget_usd", 0.0) or 0.0),
        )


@dataclass(frozen=True)
class CellResult:
    """One matrix cell's outcome — the coordinates that produced it, its three-state
    outcome, the score (``None`` when the outcome is ``VERIFIER_ABSENT``), and a
    reference to the retained raw artifact under ``matrices/<id>/``."""

    coords: dict[str, object]  # the axis values that identify this cell
    outcome: str  # one of OUTCOMES
    score: float | None = None
    artifact_ref: str = ""  # path/ref to the retained raw run artifact

    def to_dict(self) -> dict:
        return {
            "coords": dict(self.coords),
            "outcome": self.outcome,
            "score": self.score,
            "artifact_ref": self.artifact_ref,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CellResult":
        return cls(
            coords=dict(data.get("coords") or {}),
            outcome=str(data.get("outcome", VERIFIER_ABSENT)),
            score=(None if data.get("score") is None else float(data["score"])),
            artifact_ref=str(data.get("artifact_ref", "")),
        )


@dataclass(frozen=True)
class MatrixResult:
    """A whole matrix run: the spec, every cell, and the aggregates."""

    spec: MatrixSpec
    cells: list[CellResult] = field(default_factory=list)
    aggregates: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "spec": self.spec.to_dict(),
            "cells": [c.to_dict() for c in self.cells],
            "aggregates": dict(self.aggregates),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MatrixResult":
        return cls(
            spec=MatrixSpec.from_dict(data.get("spec") or {}),
            cells=[CellResult.from_dict(c) for c in (data.get("cells") or [])],
            aggregates=dict(data.get("aggregates") or {}),
        )


#: The key :func:`expand_cells` adds to each coordinate dict to distinguish repeated trials
#: of the SAME axis point. Callers strip it before treating the dict as coordinates.
TRIAL_KEY = "_trial"


def expand_cells(spec: MatrixSpec) -> list[dict]:
    """Expand ``spec``'s axes into the cartesian product of coordinate dicts, repeated
    ``trial_count`` times (each trial is its own cell — a matrix of N points at k trials
    runs N×k cells).

    Lives HERE, beside the spec it reads and the :func:`aggregate` that closes the loop,
    because it is a pure function of the spec and BOTH matrix consumers need it: the
    scenario runner (:mod:`personalclaw.evals.runner`) and the judge benchmark
    (:mod:`personalclaw.evals.judge_bench`). A private copy in either one would be a
    second axis-expansion rule, and two rules over one product is how an axis silently
    stops being crossed.
    """
    axes = spec.axes or {}
    keys = list(axes.keys())
    value_lists = [list(axes[k]) for k in keys]
    trials = max(1, int(spec.trial_count))
    combos: list[dict] = []
    # itertools.product over an empty axis set yields one empty tuple → one cell,
    # which is correct: a subject with no axes is still one runnable point.
    for values in itertools.product(*value_lists) if keys else [()]:
        coords = dict(zip(keys, values))
        for trial in range(trials):
            combos.append({**coords, TRIAL_KEY: trial})
    return combos


def aggregate(cells: list[CellResult]) -> dict:
    """Aggregate cells into per-outcome counts and a mean score.

    The mean is computed over ``PASSED``/``FAILED`` cells ONLY — ``VERIFIER_ABSENT``
    cells (infra error / timeout / ``None`` reward) are counted separately and never
    averaged in as a zero. ``mean_score`` is ``None`` when no scored cell exists.
    """
    counts = {outcome: 0 for outcome in OUTCOMES}
    scored: list[float] = []
    for cell in cells:
        outcome = cell.outcome if cell.outcome in counts else VERIFIER_ABSENT
        counts[outcome] += 1
        if outcome in _SCORED_OUTCOMES and cell.score is not None:
            scored.append(float(cell.score))
    mean_score = (sum(scored) / len(scored)) if scored else None
    return {
        "counts": counts,
        "total": len(cells),
        "scored_count": len(scored),
        "mean_score": mean_score,
    }
