#!/usr/bin/env python3
"""THE one command for the skill-impact benchmark (LEARNING-VISIBILITY T4.2).

    python scripts/learning_benchmark.py --preflight            # nothing is called
    python scripts/learning_benchmark.py --dry-run              # the paired cell plan
    python scripts/learning_benchmark.py --run                  # the paired runs
    python scripts/learning_benchmark.py --run --task sk_grill --trials 5
    python scripts/learning_benchmark.py --reproduce <baseline_run_id> --run

Protocol: `docs/roadmap/research/learning-benchmark-protocol.md` (PROTOCOL v1, owner-signed
before any run). This script implements §3's arms, §4's metrics, §5's verdict rule and §8's
publication rules; it does not restate any of them.

Why a script and not a `personalclaw` subcommand: the verdict thresholds live in
`harness/fanout_measure.py`, and `harness` is a repo-root dev package that is deliberately NOT in
the shipped wheel. A CLI subcommand would have to import it from `src/`, stranding an import at
install time. So the benchmark is dev tooling that imports both trees, the verdict is computed
HERE, and it is **written into the report** — the gateway and the dashboard only read it. That is
also what makes an unmeasured task render as "not measured" rather than as `0.000`: no surface
downstream is able to synthesise a verdict or a score it was not given.

Why `--run` is explicit and there is no default: every trial is a real model call against a real
provider. §3 pairs `k = 5` trials per arm over ten tasks — 100 cells — so the default must be the
one that costs nothing. `--preflight` and `--dry-run` both call zero models.

Isolation: nothing here touches `~/.personalclaw`'s skills. Each cell runs in a spawned child
whose `PERSONALCLAW_HOME` is a per-cell temp dir seeded from the scenario's declared
`fixture_home`, and the `skills_off` arm's suppression is an overlay applied only inside that
child. The REPORT, however, is written under the invoking home's `evals/learning_bench/`, so run
this against an isolated `PERSONALCLAW_HOME` unless you mean to keep the results.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from harness import learning_verdict as verdict_lib  # noqa: E402
from personalclaw.evals import learning_bench as bench  # noqa: E402
from personalclaw.evals import matrix as matrix_lib  # noqa: E402
from personalclaw.evals import overlay as overlay_lib  # noqa: E402
from personalclaw.evals import pinning  # noqa: E402
from personalclaw.evals import skills_bench  # noqa: E402
from personalclaw.evals import scenarios as scenario_lib  # noqa: E402

#: Arm coordinate → the report's arm name. The matrix axis carries the overlay vocabulary
#: (`on`/`off`, closed in `evals/overlay.py`); the report carries the benchmark's
#: (`skills_on`/`skills_off`, closed in `harness/learning_verdict.py`). One mapping, declared
#: once, so neither vocabulary has to grow a member for the other's sake.
ARM_NAMES = {
    skills_bench.ARM_SURFACED: verdict_lib.ARM_SKILLS_ON,
    skills_bench.ARM_SUPPRESSED: verdict_lib.ARM_SKILLS_OFF,
}


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _run_id(moment: datetime) -> str:
    return f"learnbench-{moment.strftime('%Y%m%dT%H%M%SZ')}"


def _default_k() -> int:
    """§3's `k`, read from `EvalsConfig.study_default_k` rather than hardcoded.

    G5 named this specifically: that field sits in the config PATCH allowlist and was read by
    nothing, so the protocol says read it rather than instruct an operator to flip a switch that
    changes nothing. Falls back to the §5 floor on any config failure — never to 1, which would
    silently produce `insufficient_trials` for every task.
    """
    try:
        from personalclaw.config.loader import AppConfig

        k = int(getattr(AppConfig.load().evals, "study_default_k", 0) or 0)
        return k if k > 0 else verdict_lib.MIN_TRIALS_PER_ARM
    except Exception:  # noqa: BLE001 - a config read must not decide the trial count silently
        return verdict_lib.MIN_TRIALS_PER_ARM


def _selected(task_ids: list[str]) -> list[bench.BenchTask]:
    if not task_ids:
        return list(bench.BENCH_TASKS)
    out = []
    for task_id in task_ids:
        task = bench.task_for(task_id)
        if task is None:
            raise SystemExit(
                f"{task_id!r} is not in the frozen register — §2.3: adding a task mints task set "
                f"v2. Known: {', '.join(bench.TASK_IDS)}"
            )
        out.append(task)
    return out


# ── the two zero-cost modes ──────────────────────────────────────────────────


def cmd_preflight(args: argparse.Namespace) -> int:
    """Every task's readiness. Calls no model. Exit 1 when any task is not runnable."""
    rows = bench.preflight()
    selected = {t.task_id for t in _selected(args.task)}
    rows = [r for r in rows if r.task_id in selected]
    print(f"task set v{bench.TASK_SET_VERSION} — {len(rows)} task(s)\n")
    for row in rows:
        mark = "ok " if row.runnable else "NOT"
        print(
            f"  [{mark}] {row.task_id:24s} skill={row.skill:20s} fixture={row.fixture_home or '?'}"
        )
        for blocker in row.blockers:
            print(f"          - {blocker}")
    blocked = [r for r in rows if not r.runnable]
    print()
    if blocked:
        print(
            f"{len(blocked)} of {len(rows)} task(s) cannot run a paired arm. "
            f"See {bench.PROTOCOL_DOC} §7."
        )
        return 1
    print(f"all {len(rows)} task(s) runnable. `--dry-run` next, then `--run`.")
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    """The paired cell plan and its cost shape. Calls no model."""
    trials = int(args.trials or _default_k())
    tasks = _selected(args.task)
    cells = len(tasks) * trials * 2
    print(f"task set v{bench.TASK_SET_VERSION}, k={trials} trials/arm, {len(tasks)} task(s)")
    print(
        f"arms: {verdict_lib.ARM_SKILLS_ON} / {verdict_lib.ARM_SKILLS_OFF} "
        f"on axis {overlay_lib.ARM_AXIS!r}"
    )
    print(f"{cells} cells, each a spawned child over a fresh seeded fixture home\n")
    for task in tasks:
        spec = skills_bench.build_spec(task.skill, subject=task.task_id, trials=trials)
        coords = sorted({str(c.get(overlay_lib.ARM_AXIS)) for c in matrix_lib.expand_cells(spec)})
        try:
            fixture = scenario_lib.resolve_fixture_home(
                scenario_lib.resolve_scenario_path(task.task_id)
            )
        except Exception as exc:  # noqa: BLE001 - report it as the blocker it is
            fixture = f"<unresolvable: {exc}>"
        print(f"  {task.task_id:24s} skill={task.skill:20s} arms={coords} fixture={fixture}")
    if trials < verdict_lib.MIN_TRIALS_PER_ARM:
        print(
            f"\nk={trials} is under the {verdict_lib.MIN_TRIALS_PER_ARM}-trial floor — every task "
            "would verdict `insufficient_trials` and no direction would be offered."
        )
    return 0


# ── the paired run ───────────────────────────────────────────────────────────


def _cell_payload(cell) -> dict:
    """The child's own result dict for one cell, read back off the retained artifact.

    `CellResult` carries only coords/outcome/score, so the two metrics G3 and G4 fold into the
    child payload (`tool_calls`, `spend`) reach us through `<artifact_ref>/result.json`, which the
    parent writes under the REAL home and never deletes. Returning `{}` on any read failure is
    correct and is not the same as returning zeros: the caller counts an unobserved cell.
    """
    ref = getattr(cell, "artifact_ref", "") or ""
    if not ref:
        return {}
    try:
        raw = json.loads((Path(ref) / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    parsed = raw.get("parsed") if isinstance(raw, dict) else None
    return parsed if isinstance(parsed, dict) else {}


def _verdict_for_task(task: bench.BenchTask, cells: list) -> verdict_lib.TaskVerdict:
    """Assemble the two arms from one task's cells and verdict them.

    A `VERIFIER_ABSENT` cell contributes to `absent_cells` and to NOTHING else (§6: an absent
    cell is reported as a count, never counted as a skills-off win). A scored cell whose spend
    was not observed flips `spend_observed` off for the whole task, because a token ratio over
    partially observed spend is not a token match.
    """
    trials: dict[str, list] = {verdict_lib.ARM_SKILLS_ON: [], verdict_lib.ARM_SKILLS_OFF: []}
    tool_calls: dict[str, int] = {verdict_lib.ARM_SKILLS_ON: 0, verdict_lib.ARM_SKILLS_OFF: 0}
    absent = 0
    spend_observed = True
    spend_estimated = False
    scored = 0

    for cell in cells:
        arm = ARM_NAMES.get(str((cell.coords or {}).get(overlay_lib.ARM_AXIS)))
        if cell.outcome == matrix_lib.VERIFIER_ABSENT or cell.score is None or arm is None:
            absent += 1
            continue
        payload = _cell_payload(cell)
        spend = payload.get("spend") if isinstance(payload.get("spend"), dict) else {}
        if not spend.get("observed"):
            spend_observed = False
        spend_estimated = spend_estimated or bool(spend.get("estimated"))
        # Scores are assertion pass RATES in 0..1; §5's band is in POINTS, so scale once, here,
        # at the boundary between the matrix's unit and the verdict rule's.
        trials[arm].append(
            verdict_lib.Trial(score=float(cell.score) * 100.0, tokens=int(spend.get("tokens") or 0))
        )
        tool_calls[arm] += int(payload.get("tool_calls") or 0)
        scored += 1

    if not scored:
        spend_observed = False
    return verdict_lib.verdict_task(
        task_id=task.task_id,
        skill=task.skill,
        on_trials=trials[verdict_lib.ARM_SKILLS_ON],
        off_trials=trials[verdict_lib.ARM_SKILLS_OFF],
        absent_cells=absent,
        tool_calls=tool_calls,
        spend_observed=spend_observed,
        spend_estimated=spend_estimated,
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Run the paired arms and write the report. THIS calls models."""
    from personalclaw.evals import ablation, store
    from personalclaw.evals.runner import run_matrix

    home = os.environ.get("PERSONALCLAW_HOME", "")
    trials = int(args.trials or _default_k())
    tasks = _selected(args.task)
    moment = _now()
    run_id = _run_id(moment)

    ready = {r.task_id: r for r in bench.preflight()}
    verdicts: list[verdict_lib.TaskVerdict] = []
    skipped: list[dict] = []
    pin_seen: dict[str, str] = {}

    for task in tasks:
        pre = ready.get(task.task_id)
        if pre is None or not pre.runnable:
            blockers = list(pre.blockers) if pre else ["task absent from preflight"]
            print(f"SKIP {task.task_id}: {'; '.join(blockers)}")
            skipped.append({"task_id": task.task_id, "skill": task.skill, "blockers": blockers})
            continue
        matrix_id = f"{run_id}-{task.task_id}"
        spec = skills_bench.build_spec(task.skill, subject=task.task_id, trials=trials)
        print(f"RUN  {task.task_id} ({task.skill}) — {trials * 2} cells")
        try:
            with ablation.live_state_unchanged():
                result = run_matrix(spec, matrix_id=matrix_id)
        except store.PinRequiredError as exc:
            # §3: "the pin is the comparability claim", and `run_matrix` refuses an incomplete
            # one BEFORE spawning. That refusal is correct behaviour, not an error to work
            # around — an unbound home genuinely cannot record a benchmark result. Report it as
            # a skipped task carrying the store's OWN sentence, because a traceback here would
            # abort the remaining tasks and lose the reason.
            print(f"SKIP {task.task_id}: {exc}")
            skipped.append({"task_id": task.task_id, "skill": task.skill, "blockers": [str(exc)]})
            continue
        tv = _verdict_for_task(task, list(result.cells))
        verdicts.append(tv)
        try:
            pin = pinning.compute_pin(task.task_id)
            pin_seen.setdefault("prompt_pack_sha256", pin.prompt_pack_sha256)
            pin_seen.setdefault("config_snapshot_ref", pin.config_snapshot_ref)
            pin_seen.setdefault("model_fp", pin.model_fp())
        except Exception:  # noqa: BLE001 - an unpinnable task is reported, not fatal
            pass
        print(
            f"     verdict={tv.verdict or 'not measured'} "
            f"delta={tv.delta_points} reason={tv.reason}"
        )

    report = {
        "run_id": run_id,
        "report_schema": bench.REPORT_SCHEMA,
        "created_at": moment.isoformat(),
        "protocol_doc": bench.PROTOCOL_DOC,
        "task_set_version": bench.TASK_SET_VERSION,
        "task_set_fingerprint": bench.task_set_fingerprint(),
        "trials_per_arm": trials,
        "arms": [verdict_lib.ARM_SKILLS_ON, verdict_lib.ARM_SKILLS_OFF],
        "thresholds": {
            "inconclusive_band_points": verdict_lib.INCONCLUSIVE_BAND_POINTS,
            "token_match_tolerance": verdict_lib.TOKEN_MATCH_TOLERANCE,
            "min_trials_per_arm": verdict_lib.MIN_TRIALS_PER_ARM,
            "source": "harness/fanout_measure.py",
        },
        "pin": pin_seen,
        "home": home,
        "tasks": [tv.to_dict() for tv in verdicts],
        "skipped": skipped,
        "measured_tasks": sum(1 for tv in verdicts if tv.verdict is not None),
        "absent_cells": sum(tv.absent_cells for tv in verdicts),
    }
    if args.reproduce:
        baseline = bench.read_report(args.reproduce)
        if baseline is None:
            print(f"no readable baseline report {args.reproduce!r} — reproduction NOT recorded")
        else:
            report["reproduction"] = bench.reproduction_check(baseline, report).to_dict()

    path = bench.write_report(run_id, report)
    print(f"\nreport: {path}")
    print(
        f"measured {report['measured_tasks']} of {len(tasks)} task(s); "
        f"{report['absent_cells']} absent cell(s)"
    )
    if report["measured_tasks"] == 0:
        print("NOTHING was measured. §8: this is published as such, not drawn as a zero.")
    return 0


def cmd_reproduce_only(args: argparse.Namespace) -> int:
    """Judge an existing re-run against a baseline. Calls nothing."""
    baseline = bench.read_report(args.reproduce)
    rerun = bench.read_report(args.against) if args.against else bench.latest_report()
    if baseline is None:
        print(f"no readable baseline report {args.reproduce!r}")
        return 1
    if rerun is None:
        print("no readable re-run report to compare")
        return 1
    check = bench.reproduction_check(baseline, rerun)
    print(json.dumps(check.to_dict(), indent=2, sort_keys=True))
    return 0 if check.reproduces else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="learning_benchmark",
        description=(
            "The skill-impact paired benchmark. Protocol: "
            "docs/roadmap/research/learning-benchmark-protocol.md"
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="readiness only; calls no model")
    mode.add_argument("--dry-run", action="store_true", help="the paired cell plan; calls no model")
    mode.add_argument("--run", action="store_true", help="run the paired arms (calls models)")
    mode.add_argument(
        "--check-reproduction",
        action="store_true",
        help="judge an existing re-run against --reproduce; calls nothing",
    )
    p.add_argument("--task", action="append", default=[], help="restrict to a register task id")
    p.add_argument(
        "--trials", type=int, default=0, help="trials per arm (default: study_default_k)"
    )
    p.add_argument("--reproduce", default="", help="baseline run id to judge this run against (V4)")
    p.add_argument("--against", default="", help="with --check-reproduction: the re-run's run id")
    args = p.parse_args(argv)

    if args.preflight:
        return cmd_preflight(args)
    if args.dry_run:
        return cmd_dry_run(args)
    if args.check_reproduction:
        if not args.reproduce:
            p.error("--check-reproduction needs --reproduce <baseline_run_id>")
        return cmd_reproduce_only(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
