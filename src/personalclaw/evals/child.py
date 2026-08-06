"""The experiment-matrix CHILD entrypoint (EVALUATION-SUBSTRATE §1.2/§1.3).

``run_matrix`` (:mod:`personalclaw.evals.runner`) spawns one of these per cell:

    python -m personalclaw.evals.child <descriptor.json>

The §1.3 isolation fix lives HERE by construction: the parent sets
``PERSONALCLAW_WORKSPACE`` in *this child's* spawn env only, so this process is
already pointed at the per-cell workspace before any code runs. We reuse
:class:`~personalclaw.eval.runner.EvalRunner` unchanged (``workspace_dir=`` reads
the same path), run ONE cell's scenario, and emit its raw result as a
sentinel-prefixed JSON line on stdout for the parent to read back. The parent
process's ``os.environ`` is never mutated — that is the whole point of §1.3.

Crash / infra-error contract: any failure emits an ``{"ok": false}`` result AND
exits non-zero, so the parent maps the cell to ``VERIFIER_ABSENT`` (never a false
``FAILED``). A clean scenario run emits ``{"ok": true, "passed": ..., "score": ...}``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# The parent scans child stdout (reversed) for this sentinel, so ordinary logging
# on stdout never confuses result parsing (the ``schedule_script`` launcher pattern).
CELL_RESULT_SENTINEL = "PCLAW_CELL_RESULT:"


# ── pure helpers (unit-tested directly; no process/LLM) ──────────────────────


def parse_descriptor(text: str) -> dict:
    """Parse the cell descriptor JSON the parent handed us.

    Shape: ``{"matrix_id", "coords": {axis: value}, "subject", "scorer"}``. The
    workspace is NOT in here — it arrives via ``PERSONALCLAW_WORKSPACE`` in this
    process's env (the §1.3 isolation seam)."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("descriptor must be a JSON object")
    return data


def result_from_scenario(scenario_result: Any) -> dict:
    """Map a completed ``ScenarioResult`` to the raw cell-result dict.

    ``score`` is the assertion pass rate (``passed_assertions/total_assertions``),
    falling back to 1.0/0.0 for a scenario with no assertions. The parent turns
    ``passed`` into ``PASSED``/``FAILED``; ``ok=True`` means the verifier RAN (so it
    is never ``VERIFIER_ABSENT``)."""
    total = int(getattr(scenario_result, "total_assertions", 0) or 0)
    passed_assertions = int(getattr(scenario_result, "passed_assertions", 0) or 0)
    passed = bool(getattr(scenario_result, "passed", False))
    if total > 0:
        score = passed_assertions / total
    else:
        score = 1.0 if passed else 0.0
    return {
        "ok": True,
        "passed": passed,
        "score": score,
        "summary": {
            "name": getattr(scenario_result, "name", ""),
            "assertions": f"{passed_assertions}/{total}",
            "elapsed_secs": round(float(getattr(scenario_result, "elapsed_secs", 0.0) or 0.0), 3),
        },
    }


def error_result(message: str) -> dict:
    """The infra-error payload — the parent maps ``ok=False`` to ``VERIFIER_ABSENT``."""
    return {"ok": False, "error": message[:4000]}


def render_result_line(result: dict) -> str:
    """Render the sentinel-prefixed stdout line the parent parses back."""
    return CELL_RESULT_SENTINEL + json.dumps(result, separators=(",", ":"))


# ── scenario resolution + model binding ──────────────────────────────────────


def _scenarios_dir() -> Path:
    """The packaged ``eval/scenarios/`` dir (same set the ``eval`` CLI resolves)."""
    from personalclaw import eval as _eval_pkg

    return Path(_eval_pkg.__file__).resolve().parent / "scenarios"


def resolve_scenario(subject: str):
    """Resolve a matrix ``subject`` to a :class:`Scenario`.

    A path to a scenario file wins; otherwise ``subject`` is a bare scenario name
    under the packaged ``eval/scenarios/`` dir (``.json``/``.yaml``/``.yml``)."""
    from personalclaw.eval.scenario import load_scenario

    as_path = Path(subject)
    if as_path.is_file():
        return load_scenario(as_path)
    for ext in (".json", ".yaml", ".yml"):
        candidate = _scenarios_dir() / f"{subject}{ext}"
        if candidate.is_file():
            return load_scenario(candidate)
    raise FileNotFoundError(f"scenario {subject!r} not found (as a path or a packaged scenario)")


def wrap_factory_for_model(base_factory, model: str | None):
    """Bind the cell's model-axis value (an ``active_models.json`` ``Provider:model``
    ref) as the ``model_override`` on every provider the runner builds.

    Provider fidelity: the override flows through the existing bridge factory
    (``factory(session_key, model_override=...)``); the matrix never hardcodes a
    provider. No model coord ⇒ the base factory is returned unwrapped (the bound
    default resolves)."""
    if not model:
        return base_factory

    def _factory(session_key: str, **kwargs: Any):
        kwargs.setdefault("model_override", model)
        return base_factory(session_key, **kwargs)

    return _factory


# ── entrypoint ────────────────────────────────────────────────────────────────


async def _run(descriptor: dict) -> dict:
    from personalclaw.config.loader import AppConfig
    from personalclaw.eval.runner import EvalRunner

    ws_raw = os.environ.get("PERSONALCLAW_WORKSPACE", "")
    if not ws_raw:
        return error_result("PERSONALCLAW_WORKSPACE not set in child env")
    ws = Path(ws_raw)

    subject = str(descriptor.get("subject", ""))
    scenario = resolve_scenario(subject)

    coords = descriptor.get("coords") or {}
    model = coords.get("model") if isinstance(coords, dict) else None

    base_factory = AppConfig.load().create_provider_factory()
    factory = wrap_factory_for_model(base_factory, model)

    # workspace_dir=ws so EvalRunner runs IN the env-provided workspace. Its own
    # env write (eval/runner.py) re-sets PERSONALCLAW_WORKSPACE to this same value
    # inside THIS process only — the parent's env is untouched (§1.3).
    runner = EvalRunner(provider_factory=factory, workspace_dir=ws, judge_enabled=False)
    scenario_result = await runner.run_scenario(scenario)
    return result_from_scenario(scenario_result)


def main(argv: list[str]) -> int:
    """Parse the descriptor argv, run one cell, emit the sentinel result line.

    Returns the process exit code: 0 on a clean run (``ok=True``), 1 on any infra
    error (``ok=False``) so the parent's non-zero-exit path also fires."""
    if len(argv) < 1:
        print(render_result_line(error_result("no descriptor path argument")))
        return 1
    try:
        descriptor = parse_descriptor(Path(argv[0]).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure is an infra error
        print(render_result_line(error_result(f"descriptor parse failed: {exc}")))
        return 1
    try:
        result = asyncio.run(_run(descriptor))
    except Exception:  # noqa: BLE001 - a crashed cell must be VERIFIER_ABSENT, never FAILED
        print(render_result_line(error_result(traceback.format_exc())))
        return 1
    print(render_result_line(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess spawn
    sys.exit(main(sys.argv[1:]))
