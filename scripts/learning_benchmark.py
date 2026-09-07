#!/usr/bin/env python3
"""THE one command for the skill-impact benchmark (LEARNING-VISIBILITY T4.2).

    python scripts/learning_benchmark.py --preflight            # nothing is called
    python scripts/learning_benchmark.py --dry-run              # the paired cell plan
    python scripts/learning_benchmark.py --run                  # the paired runs
    python scripts/learning_benchmark.py --run --bind-provider LocalOllama:gemma4:12b
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

Why `--bind-provider` and not "use whatever this home has": a cell's environment is BUILT from a
name allowlist, not inherited, so no provider or credential reaches it by accident. `--run` on its
own therefore measures nothing — every cell resolves the offline scripted fixture — and the flag
names the ONE `Provider:model` ref the cells may call. The ref it names lands in the report's
`provider_binding`, so a published table can never be mistaken for the other kind of run.
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
from personalclaw.evals import provenance  # noqa: E402
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

    A scored cell whose PROVIDER did not report its usage flips `tokens_recorded` off, which is a
    different fact and was #2540's silent zero: `spend.get("tokens") or 0` fed the token
    denominator a `0` for a cell that genuinely spent, while the cell still reported
    `observed: true`. The cell now reports `tokens: None`
    (`provenance.UNRECORDED`) and this is where the count is assembled; `verdict_task` refuses the
    ratio rather than averaging over it.
    """
    trials: dict[str, list] = {verdict_lib.ARM_SKILLS_ON: [], verdict_lib.ARM_SKILLS_OFF: []}
    tool_calls: dict[str, int] = {verdict_lib.ARM_SKILLS_ON: 0, verdict_lib.ARM_SKILLS_OFF: 0}
    absent = 0
    spend_observed = True
    spend_estimated = False
    tokens_recorded = True
    unrecorded_cells = 0
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
        # The guard, before the number is used. An ABSENT `tokens_recorded` is unrecorded too: a
        # cell artifact written before #2540 never recorded whether its provider reported usage.
        if provenance.is_unrecorded(spend, "tokens_recorded") or not spend.get("tokens_recorded"):
            tokens_recorded = False
            unrecorded_cells += 1
        # Scores are assertion pass RATES in 0..1; §5's band is in POINTS, so scale once, here,
        # at the boundary between the matrix's unit and the verdict rule's.
        #
        # `Trial.tokens` is an `int` by construction, so an unrecorded count arrives as a
        # PLACEHOLDER 0. That is safe only because `tokens_recorded=False` below makes
        # `verdict_task` refuse before any code reads it — and it raises
        # `IncommensurableSpendError` if it is ever reached with the flag off.
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
        tokens_recorded=tokens_recorded,
        unrecorded_spend_cells=unrecorded_cells,
    )


def run_spend_facts(verdicts: list) -> dict:
    """The RUN-level spend facts the report publishes above its task table.

    #2540 asked for this as a property of the run and not only of a row: "if a provider cannot
    report usage, that is a property of the run worth surfacing in the report". A reader deciding
    whether to believe any token ratio in the table needs it before reading a single row.

    A pure function rather than an expression inlined into the report literal, because inline it was
    unreachable from a test — and a run-level claim nothing exercises is the inert-control shape
    this project keeps finding. A run with NO verdicts records `True`: nothing failed to report,
    because nothing reported at all, and `measured_tasks: 0` beside it is what says so.
    """
    return {
        "tokens_recorded": all(tv.tokens_recorded for tv in verdicts) if verdicts else True,
        "unrecorded_spend_cells": sum(tv.unrecorded_spend_cells for tv in verdicts),
    }


def cmd_run(args: argparse.Namespace) -> int:
    """Run the paired arms and write the report. THIS calls models."""
    from personalclaw.evals import ablation, cell_provider, store
    from personalclaw.evals.runner import run_matrix

    home = os.environ.get("PERSONALCLAW_HOME", "")
    trials = int(args.trials or _default_k())
    tasks = _selected(args.task)
    moment = _now()
    run_id = _run_id(moment)

    # `--bind-provider` is what makes a cell able to call a real model. It names ONE
    # `Provider:model` ref; everything the cell needs is resolved from that one entry, and a
    # run without the flag keeps the offline scripted default. Resolved BEFORE the first task
    # so a typo'd ref fails now rather than after burning half a matrix.
    binding = None
    if args.bind_provider:
        try:
            binding = cell_provider.resolve_binding(args.bind_provider)
        except cell_provider.CellBindingError as exc:
            print(f"cannot bind {args.bind_provider!r}: {exc}")
            return 1
        print(f"bound {binding.model_ref()} for {binding.use_case!r} — cells call a real model")
    else:
        print("no --bind-provider: every cell resolves the offline scripted fixture")

    ready = {r.task_id: r for r in bench.preflight()}
    verdicts: list[verdict_lib.TaskVerdict] = []
    skipped: list[dict] = []
    pin_seen: dict = {}

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
                result = run_matrix(spec, matrix_id=matrix_id, provider_binding=binding)
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
            # READ BACK the pin `run_matrix` actually persisted, rather than recomputing one here.
            # A second computation is a second answer: this one would not carry the cell binding
            # `run_matrix` recorded (#2561), so the report would state the home's model beside a
            # cells' field that was silently unrecorded.
            pin = pinning.matrix_pin(matrix_id)
            if pin is None:
                raise RuntimeError(f"matrix {matrix_id} persisted no pin.json")
            pin_seen.setdefault("prompt_pack_sha256", pin.prompt_pack_sha256)
            pin_seen.setdefault("config_snapshot_ref", pin.config_snapshot_ref)
            pin_seen.setdefault("model_fp", pin.model_fp())
            # The per-use-case refs behind `model_fp`, spelled out. `model_fp` is a digest, so a
            # reader of a published table could not tell a real model from the offline replay
            # from it — and §8 forbids publishing a number whose provenance is unreadable.
            pin_seen.setdefault("model_fingerprint", dict(pin.model_fingerprint))
            # The SECOND fact, under its own name (#2561): what the CELLS could reach. `model_fp`
            # above is the invoking home's and was identical for a bound run and a run where every
            # cell failed to resolve any provider at all.
            pin_seen.setdefault("cell_model_fp", pin.cell_model_fp())
            pin_seen.setdefault(
                "cell_model_fingerprint",
                None if pin.cell_model_fingerprint is None else dict(pin.cell_model_fingerprint),
            )
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
        # WHAT the cells were actually allowed to call. `null` is the honest reading of an
        # offline run and must stay distinguishable from a real one at a glance: the two
        # produce identically-shaped score tables and only one of them is a measurement.
        #
        # And a THIRD state a consumer must not fold into `null`: a report that never recorded
        # this at all. That is what `report_schema` above answers (#2562) — every report at
        # `bench.PROVENANCE_SCHEMA` or above carries this key, so a consumer reads the STATED
        # schema and never `'provider_binding' in report`.
        "provider_binding": (binding.to_dict() if binding is not None else None),
        "home": home,
        "tasks": [tv.to_dict() for tv in verdicts],
        "skipped": skipped,
        "measured_tasks": sum(1 for tv in verdicts if tv.verdict is not None),
        "absent_cells": sum(tv.absent_cells for tv in verdicts),
        # The RUN-level spend facts (#2540). See `run_spend_facts` for why they are a function.
        **run_spend_facts(verdicts),
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
    if not report["tokens_recorded"]:
        print(
            f"{report['unrecorded_spend_cells']} cell(s) reported NO token usage — their provider "
            f"omitted it. Every token ratio in this report is {provenance.UNRECORDED}, not zero."
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
    p.add_argument(
        "--bind-provider",
        default="",
        metavar="Provider:model",
        help=(
            "bind ONE real model for this run's cells (e.g. LocalOllama:gemma4:12b). Without "
            "it every cell resolves the offline scripted fixture and measures nothing"
        ),
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
