"""The experiment-matrix RUNNER (EVALUATION-SUBSTRATE §1.2/§1.3).

``run_matrix`` expands a :class:`~personalclaw.evals.matrix.MatrixSpec` into the
cartesian product of its axes × ``trial_count``, and executes each cell
**sequentially** in a **spawned child process** (:mod:`personalclaw.evals.child`)
whose ``PERSONALCLAW_WORKSPACE`` points at a fresh per-cell temp workspace. The
parent gateway's ``os.environ`` is NEVER mutated — that is the §1.3 isolation fix,
achieved by env-copy-at-spawn (precedent: ``schedule_script.run_script_sandboxed``).

Three-state outcome mapping (§1.2), the load-bearing contract:

* the child ran and reported ``passed`` ⇒ ``PASSED`` / ``FAILED`` with its score;
* the SpendMeter preflight says ``EXCEEDED`` ⇒ ``VERIFIER_ABSENT`` (couldn't run
  within budget), and NO child is spawned;
* the child timed out / exited non-zero / emitted unparseable output ⇒
  ``VERIFIER_ABSENT`` — an infra failure is never averaged in as a ``0`` score.

The runner never raises out: a cell's infra failure becomes a ``VERIFIER_ABSENT``
cell, not an exception that aborts the whole matrix. Every cell's raw artifact is
retained under ``matrices/<id>/`` so a surprising aggregate is always drillable.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.evals import pinning
from personalclaw.evals import scenarios as scenario_lib
from personalclaw.evals import store
from personalclaw.evals.child import CELL_RESULT_SENTINEL
from personalclaw.evals.matrix import (
    FAILED,
    PASSED,
    TRIAL_KEY,
    VERIFIER_ABSENT,
    CellResult,
    MatrixResult,
    MatrixSpec,
    aggregate,
    expand_cells,
)
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

# Default per-cell wall-clock ceiling. A single-user machine runs cells one at a
# time, so this bounds one scenario, not a fleet.
DEFAULT_CELL_TIMEOUT_SECS = 600.0


def _budget_blocks_cell(spec: MatrixSpec) -> bool:
    """SpendMeter preflight: does the spec's ``budget_usd`` cap forbid this cell?

    Returns ``True`` only when a meter is present AND the day total is already at/over
    the dollar cap (``BudgetVerdict.EXCEEDED``). No meter / no budget ⇒ unlimited is
    the safe default and the cell proceeds. Best-effort: any meter error fails OPEN
    (proceed), because the meter is a guardrail, not a hard gate here."""
    if not spec.budget_usd or spec.budget_usd <= 0.0:
        return False
    try:
        from personalclaw.guardrails.budgets import Budget, BudgetVerdict, get_meter

        meter = get_meter()
        budget = Budget(max_dollars=float(spec.budget_usd))
        verdict, _reason = meter.check_day(budget)
        return verdict == BudgetVerdict.EXCEEDED
    except Exception:
        logger.debug("budget preflight failed open", exc_info=True)
        return False


def _parse_child_stdout(stdout: str) -> dict | None:
    """Extract the sentinel-prefixed JSON result line from child stdout (scanned
    in reverse, the ``schedule_script`` pattern). ``None`` on absent/garbage."""
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(CELL_RESULT_SENTINEL):
            try:
                data = json.loads(line[len(CELL_RESULT_SENTINEL) :])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _spawn_cell(
    spec: MatrixSpec,
    coords: dict,
    *,
    matrix_id: str,
    cell_index: int,
    timeout_secs: float,
    pin: pinning.RunPin,
) -> CellResult:
    """Run ONE cell in a child process and map its outcome to a ``CellResult``.

    Env construction is the load-bearing §1.3 isolation code: we ``os.environ.copy()``
    (a COPY — the parent's env is never mutated) and set ``PERSONALCLAW_WORKSPACE``
    and ``PERSONALCLAW_HOME`` on the COPY only, so both overrides exist in the child
    and nowhere else. ``PERSONALCLAW_HOME`` points at an empty per-cell dir the child
    seeds from the scenario's named ``fixture_home`` (ES-2): the run executes over a
    known clean state, and everything the child writes lands in the throwaway home
    rather than in the user's.

    The cell's own pin (the matrix pin with the model-axis override applied) is
    persisted beside the cell artifact, so a surprising cell is attributable without
    re-deriving which model produced it."""
    cell_dir = store.matrix_dir(matrix_id) / f"cell-{cell_index:04d}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cell_pin = pin.with_model_override(coords.get("model") if isinstance(coords, dict) else None)
    pinning.write_pin(cell_dir, cell_pin)
    descriptor_path = cell_dir / "descriptor.json"
    descriptor = {
        "matrix_id": matrix_id,
        "coords": coords,
        "subject": spec.subject,
        "scorer": spec.scorer,
        # Resolved in the PARENT (which can see the real home's scenario library);
        # the child runs with a throwaway home and could not resolve a bare name.
        "scenario_path": str(scenario_lib.resolve_scenario_path(spec.subject)),
        "fixture_home": cell_pin.fixture_home,
        "pin": cell_pin.to_dict(),
    }
    descriptor_path.write_text(json.dumps(descriptor, indent=2, sort_keys=True), encoding="utf-8")
    artifact_ref = str(cell_dir)

    with tempfile.TemporaryDirectory(prefix="pclaw_matrix_cell_") as cell_tmp:
        ws = Path(cell_tmp) / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        # The child seeds this path itself; ``seed()`` refuses a non-empty target
        # without ``replace=True``, so we deliberately do NOT create it here.
        cell_home = Path(cell_tmp) / "home"
        # ── §1.3 isolation: env override on a COPY, parent env never touched ──
        env = os.environ.copy()
        env["PERSONALCLAW_WORKSPACE"] = str(ws)
        env["PERSONALCLAW_HOME"] = str(cell_home)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "personalclaw.evals.child", str(descriptor_path)],
                env=env,
                timeout=timeout_secs,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            _write_cell_artifact(cell_dir, {"outcome": VERIFIER_ABSENT, "reason": "timeout"})
            return CellResult(coords=coords, outcome=VERIFIER_ABSENT, artifact_ref=artifact_ref)
        except Exception as exc:  # noqa: BLE001 - a spawn fault is one absent cell
            logger.warning("cell spawn failed (mapped to verifier_absent)", exc_info=True)
            _write_cell_artifact(
                cell_dir, {"outcome": VERIFIER_ABSENT, "reason": f"spawn_error: {exc}"}
            )
            return CellResult(coords=coords, outcome=VERIFIER_ABSENT, artifact_ref=artifact_ref)

        parsed = _parse_child_stdout(proc.stdout)
        _write_cell_artifact(
            cell_dir,
            {
                "returncode": proc.returncode,
                "parsed": parsed,
                "stdout_tail": (proc.stdout or "")[-2000:],
                "stderr_tail": (proc.stderr or "")[-2000:],
            },
        )

        # Non-zero exit, unparseable output, or an explicit infra error → absent.
        if proc.returncode != 0 or parsed is None or not parsed.get("ok"):
            return CellResult(coords=coords, outcome=VERIFIER_ABSENT, artifact_ref=artifact_ref)

        outcome = PASSED if parsed.get("passed") else FAILED
        raw_score = parsed.get("score")
        score = None if raw_score is None else float(raw_score)
        return CellResult(coords=coords, outcome=outcome, score=score, artifact_ref=artifact_ref)


def _write_cell_artifact(cell_dir: Path, payload: dict) -> None:
    """Retain a cell's raw run artifact (best-effort — never breaks the run)."""
    try:
        (cell_dir / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        logger.debug("cell artifact write failed for %s", cell_dir, exc_info=True)


def run_matrix(
    spec: MatrixSpec,
    *,
    matrix_id: str,
    timeout_secs: float = DEFAULT_CELL_TIMEOUT_SECS,
) -> MatrixResult:
    """Execute ``spec`` cell-by-cell in child processes; persist + return the result.

    Sequential by design (single-user machine; also sidesteps any residual
    env-mutation concern). Writes ``experiment.json`` up front, then for each cell:
    a SpendMeter preflight (``EXCEEDED`` → ``VERIFIER_ABSENT``, no spawn), else a
    child spawn with the per-cell workspace in the child's env only. Persists
    ``aggregates.json`` + ``trials.json``, appends a ``results.tsv`` row, and
    SEL-logs matrix-run start + completion (best-effort).

    ES-2: the pin is computed FIRST, before any cell runs, and persisted as
    ``matrices/<id>/pin.json``. A scenario that cannot be resolved — or that names a
    fixture home that does not ship — raises
    :class:`~personalclaw.evals.scenarios.ScenarioLibraryError` out of here, on
    purpose: the "never raises out" contract covers a CELL's infra failure (mapped to
    ``VERIFIER_ABSENT``), not a whole run that could only produce unattributable
    scores."""
    pin = pinning.compute_pin(spec.subject)
    if not pin.is_complete():
        # Fail fast, BEFORE any cell burns a model call: an incomplete pin means the
        # row could not be written at the end anyway, and a run whose result cannot
        # enter the ledger is wasted spend, not evidence.
        raise store.PinRequiredError(
            f"refusing to run matrix {matrix_id}: incomplete RunPin "
            f"(missing: {', '.join(pin.missing_parts())})"
        )
    store.write_matrix_experiment(matrix_id, spec.to_dict())
    pinning.write_pin(store.matrix_dir(matrix_id), pin)
    _sel_log(matrix_id, spec, outcome="started")

    cells: list[CellResult] = []
    combos = expand_cells(spec)
    for cell_index, combo in enumerate(combos):
        coords = {k: v for k, v in combo.items() if k != TRIAL_KEY}
        if _budget_blocks_cell(spec):
            cells.append(CellResult(coords=coords, outcome=VERIFIER_ABSENT))
            continue
        cells.append(
            _spawn_cell(
                spec,
                coords,
                matrix_id=matrix_id,
                cell_index=cell_index,
                timeout_secs=timeout_secs,
                pin=pin,
            )
        )

    aggregates = aggregate(cells)
    store.write_matrix_aggregates(matrix_id, aggregates)
    store.write_matrix_trials(matrix_id, cells)
    store.append_result(
        {
            "study_id": matrix_id,
            "kind": "matrix",
            "verdict": _matrix_verdict(aggregates),
            "score_new": aggregates.get("mean_score"),
            "k": spec.trial_count,
            "ts": datetime.now(tz=timezone.utc).isoformat(),
        },
        pin=pin,
    )
    result = MatrixResult(spec=spec, cells=cells, aggregates=aggregates)
    _sel_log(matrix_id, spec, outcome="completed")
    return result


def _matrix_verdict(aggregates: dict) -> str:
    """One-word ledger verdict from the three-state counts: ``pass`` if any scored
    cell passed and none failed, ``fail`` if any failed, ``verifier_absent`` if no
    cell scored at all."""
    counts = aggregates.get("counts") or {}
    if counts.get(FAILED):
        return "fail"
    if counts.get(PASSED):
        return "pass"
    return VERIFIER_ABSENT


def _sel_log(matrix_id: str, spec: MatrixSpec, *, outcome: str) -> None:
    """SEL-log a matrix-run lifecycle event (§10). Best-effort — never breaks a run."""
    try:
        sel().log_api_access(
            caller=f"matrix:{matrix_id}",
            operation="evals_matrix",
            outcome=outcome,
            source="evals",
            resources=f"subject={spec.subject} scorer={spec.scorer}",
        )
    except Exception:
        logger.debug("SEL matrix log failed", exc_info=True)
