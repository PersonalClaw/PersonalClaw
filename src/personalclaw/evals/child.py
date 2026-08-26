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

    Shape: ``{"matrix_id", "coords": {axis: value}, "subject", "scorer",
    "scenario_path", "fixture_home", "pin"}``. The workspace and the throwaway home
    are NOT in here — they arrive via ``PERSONALCLAW_WORKSPACE`` /
    ``PERSONALCLAW_HOME`` in this process's env (the §1.3 isolation seam)."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("descriptor must be a JSON object")
    return data


def tool_call_count(scenario_result: Any) -> int:
    """Total tool calls across every turn of every session.

    ``TurnResult.tool_calls`` is populated per turn (``eval/runner.py``) and was dropped by
    BOTH aggregation boundaries — ``ScenarioResult.summary()`` and
    :func:`result_from_scenario` — so the benchmark protocol's declared ``tool_calls``
    metric was unreachable from the matrix path (learning-benchmark-protocol.md §7 G3).
    Counting it HERE is the fix: a count, not the call texts, because the texts are prompt
    content and this payload crosses a process boundary into a retained artifact.

    Tolerant of a result object that has no sessions (a partially-built double in a test)
    because a missing count must read as 0 calls observed, never as a crashed cell."""
    total = 0
    for session in getattr(scenario_result, "sessions", None) or []:
        for turn in getattr(session, "turns", None) or []:
            total += len(getattr(turn, "tool_calls", None) or [])
    return total


def spend_from_home() -> dict:
    """This cell's model spend, read from its OWN throwaway home before it is destroyed.

    ``guardrails/audit.py`` writes one line per attempt into ``config_dir()/model_calls.jsonl``.
    That file lives in the cell's ``PERSONALCLAW_HOME`` — a ``TemporaryDirectory`` the parent
    deletes on exit — so the token denominator the honest verdict needs was being computed and
    then thrown away (protocol §7 G4). Folding it into the payload here is what makes it
    survive: the parent persists this dict verbatim into the cell artifact under the real home.

    ``estimated`` is carried through and never dropped. ``AttemptRecord.estimated`` exists to
    say "dollars/tokens are heuristic, not provider-reported", and §4 requires any published
    ratio to carry that word.

    ``observed`` is the load-bearing field: ``False`` means the audit file was absent or
    unreadable, which is NOT the same fact as zero spend. A reader that cannot tell those
    apart would publish "this arm was free" about a measurement that never happened."""
    tokens_in = tokens_out = attempts = 0
    dollars = 0.0
    estimated = False
    try:
        from personalclaw.config import config_dir

        path = Path(config_dir()) / "model_calls.jsonl"
        if not path.is_file():
            return {"observed": False, "reason": "no model_calls.jsonl in the cell home"}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            attempts += 1
            tokens_in += int(row.get("tokens_in") or 0)
            tokens_out += int(row.get("tokens_out") or 0)
            dollars += float(row.get("dollars_est") or 0.0)
            estimated = estimated or bool(row.get("estimated"))
    except Exception as exc:  # noqa: BLE001 - spend accounting never fails a measured cell
        return {"observed": False, "reason": f"spend read failed: {exc}"[:400]}
    return {
        "observed": True,
        "attempts": attempts,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens": tokens_in + tokens_out,
        "dollars_est": round(dollars, 6),
        "estimated": estimated,
    }


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
        "tool_calls": tool_call_count(scenario_result),
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


def resolve_scenario(descriptor: dict):
    """Load the :class:`Scenario` the PARENT already resolved for this cell.

    The parent writes an absolute ``scenario_path`` into the descriptor because it
    is the process that can see the real home's scenario library
    (:mod:`personalclaw.evals.scenarios`); this child runs with a throwaway
    ``PERSONALCLAW_HOME`` and must never re-resolve a bare name against it — that
    would silently run a different file than the one the pin hashed."""
    from personalclaw.eval.scenario import load_scenario

    raw = str(descriptor.get("scenario_path") or "")
    if not raw:
        raise FileNotFoundError("descriptor has no scenario_path (parent must resolve it)")
    path = Path(raw)
    if not path.is_file():
        raise FileNotFoundError(f"scenario file {raw!r} does not exist")
    return load_scenario(path)


def seed_fixture_home(fixture_home: str) -> None:
    """Seed the cell's ``PERSONALCLAW_HOME`` from the named ``tests_fixtures/`` seed.

    This is the "over named seeded fixture homes" half of ES-2: the scenario declares
    a fixture by name, and the run starts from that known state instead of from
    whatever the invoking user's home contains. ``seed()``'s own rails still apply —
    most importantly it refuses to write ``~/.personalclaw``, so a misconfigured cell
    can never clobber the real home. ``replace=True`` is safe here because the target
    is a per-cell temp dir the parent just created."""
    from personalclaw.seed import seed

    seed(fixture_home, replace=True)


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

    scenario = resolve_scenario(descriptor)

    # Seed BEFORE the first config read: everything below resolves against
    # PERSONALCLAW_HOME, and this call is what puts the declared fixture there.
    fixture_home = str(descriptor.get("fixture_home") or "")
    if fixture_home:
        seed_fixture_home(fixture_home)

    # ES-7 §3.1: the component overlay, applied AFTER the fixture seed (so it patches the
    # seeded throwaway home, not a home the seed is about to overwrite) and BEFORE the
    # first config read. It reads from THIS process's env; `apply_in_child` refuses outright
    # unless PERSONALCLAW_HOME is a throwaway, so a mis-spawned cell becomes an honest
    # VERIFIER_ABSENT rather than a measurement taken against live state.
    from personalclaw.evals import overlay as overlay_lib

    cell_overlay = overlay_lib.from_env()
    applied = overlay_lib.apply_in_child(cell_overlay)

    coords = descriptor.get("coords") or {}
    model = coords.get("model") if isinstance(coords, dict) else None

    base_factory = AppConfig.load().create_provider_factory()
    factory = wrap_factory_for_model(base_factory, model)

    # workspace_dir=ws so EvalRunner runs IN the env-provided workspace. Its own
    # env write (eval/runner.py) re-sets PERSONALCLAW_WORKSPACE to this same value
    # inside THIS process only — the parent's env is untouched (§1.3).
    runner = EvalRunner(provider_factory=factory, workspace_dir=ws, judge_enabled=False)
    scenario_result = await runner.run_scenario(scenario)
    result = result_from_scenario(scenario_result)
    # Read the spend BEFORE returning: the parent deletes this home the moment the child
    # exits, so this is the only process that can see the audit rows it just wrote (§7 G4).
    result["spend"] = spend_from_home()
    if cell_overlay is not None:
        # WHAT the overlay actually changed, reported back rather than assumed: an arm that
        # applied nothing is a delta of 0.0 that would otherwise read as "the component does
        # not matter", which is the exact wrong conclusion.
        result["overlay"] = {
            "component_id": cell_overlay.component_id,
            "arm": cell_overlay.arm,
            "applied": applied,
        }
    return result


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
