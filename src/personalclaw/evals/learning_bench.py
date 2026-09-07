"""The skill-impact benchmark: the frozen task register, its reports, and V4 reproduction.

`docs/roadmap/research/learning-benchmark-protocol.md` is PROTOCOL v1 — owner-signed on
2026-08-16, before any run, including the commitment to publish a modest or negative result.
This module is the shipped half of executing it: the frozen ten-task register, the preflight
that says whether a paired run is runnable *before* a model is called, report persistence, and
the §8 reproduction predicate.

**What is deliberately NOT here: the verdict.** The protocol's §5 thresholds live in
`harness/fanout_measure.py`, and `harness` is a repo-root dev package that does not ship in the
wheel — so importing it from `src/` would strand an import at install time. The verdict is
computed by the runner (`scripts/learning_benchmark.py`, through `harness/learning_verdict.py`)
and **written into the report**. Everything downstream — this module, the gateway route, the
dashboard panel — only ever READS a verdict string.

That is not a workaround, it is the property worth having. A surface that cannot recompute a
verdict cannot invent one. A report with no verdict therefore renders as *"not measured"*, and
the one failure mode this benchmark most needs to avoid — drawing `0.000` where nothing was
measured, turning "we never asked" into "it scored nothing" — is unreachable by construction.

Isolation: every paired trial runs as a matrix cell in a spawned child whose
``PERSONALCLAW_HOME`` is a per-cell temp dir seeded from the scenario's declared
``fixture_home``. Nothing here reads or writes the operator's skills. The protocol's §3 forbids
`personalclaw eval` for benchmark runs for exactly that reason — it isolates the workspace but
not the home — and this module never reaches it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import provenance
from personalclaw.evals import scenarios as scenario_lib
from personalclaw.evals import store

#: Bump ONLY with the register below, and never for a code change. §2.3: adding, removing or
#: editing a task mints v2 and invalidates every v1 result *for comparison purposes* — v1 and v2
#: numbers are never plotted on one axis. The mechanical anchor is each scenario's ``sha256`` in
#: the library manifest, which is why :func:`task_set_fingerprint` reads the manifest rather than
#: trusting this integer.
#:
#: **v2 (ES-6):** all ten register scenarios joined the Loop-2 gate tier (``"tiers": ["gate"]``).
#: Their turns and assertions are untouched, so the tasks themselves are unchanged — but the tag
#: moved every one of their ``scenario_sha256`` values, which IS the mechanical anchor above, and
#: the library backfill is version-keyed, so an existing home only picks the tag up on a bump.
#: The integer has to follow the anchor rather than contradict it: leaving it at 1 would claim
#: these are the same subjects v1 measured while the anchor says otherwise. Nothing real is
#: invalidated — no baseline is keyed on this value (its only non-test reader stamps it on the
#: report payload) and the bench's ordinary state is "has never run".
TASK_SET_VERSION = 2

#: The report schema version. Separate from the task-set version because a schema change does
#: not invalidate a measurement, and conflating them would either freeze the schema or silently
#: retire results.
#:
#: **v2 (#2562):** ES-17 added ``provider_binding`` and left this at ``1``, so a consumer holding a
#: report with no ``provider_binding`` key could not tell "provenance was never recorded" from
#: "provenance was recorded and says nothing was bound" — the two states are different claims and
#: the panel had to branch on ``'provider_binding' in report`` to keep them apart, which left every
#: future consumer to rediscover the trick. Adding a field is a schema change; the report has
#: stated ``report_schema`` since LV-7, so the fix is for consumers to read the STATED schema
#: instead of inferring one from key presence. v2 also adds the run-level ``tokens_recorded`` /
#: ``unrecorded_spend_cells`` (#2540) and the pin's ``cell_model_fingerprint`` / ``cell_model_fp``
#: (#2561) — one bump for one schema change, not three.
REPORT_SCHEMA = 2

#: The schema at and above which a report RECORDS its provenance. Below it, an absent
#: ``provider_binding`` means :data:`~personalclaw.evals.provenance.UNRECORDED` and NOT "nothing
#: was bound". Named here (and mirrored in ``web/src/lib/unrecorded.ts``) so the answer lives in
#: one place rather than in each consumer's key-presence check.
PROVENANCE_SCHEMA = 2


def report_schema(report: dict | None) -> int | None:
    """The schema a report was written under, as the report itself states it.

    ``None`` when the report does not state one — :data:`~personalclaw.evals.provenance.UNRECORDED`
    — which no report this repo has ever written should be, because ``report_schema`` shipped with
    LV-7. A hand-edited or truncated artifact can be, and it must not read as v1 by default: that
    is the same absent-versus-declared collapse one level up.
    """
    if provenance.is_unrecorded(report, "report_schema"):
        return None
    try:
        return int((report or {})["report_schema"])
    except (TypeError, ValueError):
        return None


def provenance_recorded(report: dict | None) -> bool:
    """Did this report record what its cells could reach?

    ``True`` only at :data:`PROVENANCE_SCHEMA` or above. A report that states no schema at all is
    ``False``: an unreadable schema cannot certify that a field is present, and "we do not know"
    must never render as "nothing was bound".
    """
    schema = report_schema(report)
    return schema is not None and schema >= PROVENANCE_SCHEMA


PROTOCOL_DOC = "docs/roadmap/research/learning-benchmark-protocol.md"


@dataclass(frozen=True)
class BenchTask:
    """One row of the §2.2 frozen register.

    ``skill`` is the bundled skill under test — the name the child suppresses for the
    ``skills_off`` arm. ``observable`` restates in one line what the scenario's assertions
    actually check, so a reader of a results table does not have to open the scenario to know
    what "passed" meant.
    """

    task_id: str
    skill: str
    observable: str


#: **Task set v1, frozen.** One task per bundled skill family whose procedure has an
#: observable, non-judged outcome (§2.2's stated selection rule, so it can be checked rather
#: than trusted). The four skills excluded at freeze time and why: ``loop-worker`` fires only
#: inside a loop; ``pclaw-api`` and ``pclaw-features`` would test doc recall rather than a
#: procedure; ``infographic-syntax`` shares ``visual-output``'s observable.
BENCH_TASKS: tuple[BenchTask, ...] = (
    BenchTask("sk_check_work", "check-work", "an enumerated verification list with outcomes"),
    BenchTask("sk_task_project", "task-and-project", "each task title plus a status line"),
    BenchTask("sk_knowledge_grounding", "knowledge-grounding", "grounded fact, no distractor"),
    BenchTask("sk_memory_discipline", "memory-discipline", "persist decision, no over-capture"),
    BenchTask("sk_artifacts", "artifacts", "the /artifacts/<slug> reference form"),
    BenchTask("sk_editorial_document", "editorial-document", "sections in the requested order"),
    BenchTask("sk_delegation", "delegation", "both parts plus an enumerated split"),
    BenchTask("sk_grill", "grill", "no bare acceptance, two questions put back"),
    BenchTask("sk_best_of_n", "best-of-n", "three candidates plus a named criterion"),
    BenchTask("sk_visual_output", "visual-output", "the <widget> envelope"),
)

TASK_IDS: tuple[str, ...] = tuple(t.task_id for t in BENCH_TASKS)


def task_for(task_id: str) -> BenchTask | None:
    """The register row for ``task_id``, or ``None`` — the register is closed."""
    for task in BENCH_TASKS:
        if task.task_id == task_id:
            return task
    return None


# ── the task-set fingerprint (§2.3's mechanical anchor) ───────────────────────


def task_set_fingerprint() -> dict[str, str]:
    """``{task_id: scenario_sha256}`` for the register, read from the installed manifest.

    Reads the MANIFEST rather than hashing the packaged files, because the manifest describes
    what is actually installed — a locally edited scenario at an equal-or-higher version is left
    in place by ``install_library`` and must therefore be visible here. A task whose scenario is
    absent is reported as an empty string rather than omitted: a shorter dict would make a
    missing task look like a task set that was never supposed to include it.
    """
    manifest = scenario_lib.read_manifest() or scenario_lib.install_library()
    installed = manifest.get("scenarios") or {}
    out: dict[str, str] = {}
    for task_id in TASK_IDS:
        entry = installed.get(task_id)
        out[task_id] = str((entry or {}).get("sha256") or "")
    return out


# ── preflight: is a paired run runnable at all, before a model is called? ─────


@dataclass(frozen=True)
class TaskPreflight:
    """One task's readiness. ``runnable`` is an AND of the three checks below."""

    task_id: str
    skill: str
    scenario_present: bool = False
    scenario_sha256: str = ""
    fixture_home: str = ""
    skill_present: bool = False
    suppression_verified: bool = False
    suppression_reason: str = ""
    blockers: list[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return self.scenario_present and self.skill_present and self.suppression_verified

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "skill": self.skill,
            "scenario_present": self.scenario_present,
            "scenario_sha256": self.scenario_sha256,
            "fixture_home": self.fixture_home,
            "skill_present": self.skill_present,
            "suppression_verified": self.suppression_verified,
            "suppression_reason": self.suppression_reason,
            "runnable": self.runnable,
            "blockers": list(self.blockers),
        }


def preflight(*, loader=None) -> list[TaskPreflight]:
    """Check every register task WITHOUT calling a model.

    Three checks, in the order that makes the later ones meaningful:

    1. the scenario is installed (so the pin has something to hash);
    2. the named skill exists in the loader's home (a register row naming a skill that no
       longer ships would otherwise "run" as two identical arms);
    3. suppression actually removes the skill body — reusing
       :func:`personalclaw.evals.skills_bench.verify_suppression`, the same check ES-7's
       skills bench refuses on. A suppression that does not suppress produces a 0.0 delta that
       reads as "this skill does not earn its place", which is the precise fabricated result the
       protocol exists to prevent.
    """
    from personalclaw.evals import skills_bench

    if loader is None:  # pragma: no cover - the default wiring
        from personalclaw.skills import SkillsLoader

        loader = SkillsLoader()

    fingerprint = task_set_fingerprint()
    installed = set(scenario_lib.list_installed())
    out: list[TaskPreflight] = []
    for task in BENCH_TASKS:
        blockers: list[str] = []
        present = task.task_id in installed
        if not present:
            blockers.append(
                f"scenario {task.task_id!r} is not installed — run the library backfill "
                "(personalclaw.evals.scenarios.install_library) in this home first"
            )
        fixture = ""
        if present:
            try:
                fixture = scenario_lib.resolve_fixture_home(
                    scenario_lib.resolve_scenario_path(task.task_id)
                )
            except Exception as exc:  # noqa: BLE001 - a bad fixture is a blocker, not a crash
                blockers.append(f"fixture_home unresolvable: {exc}")

        check = skills_bench.verify_suppression(loader, task.skill)
        # `probe_chars` is the loader's own "this skill has a loadable body" signal — the same
        # one `verify_suppression` refuses on. Asking the loader for a body is a stronger check
        # than asking the filesystem for a directory: a present-but-unloadable skill would
        # otherwise pass a name check and then run two identical arms.
        skill_present = bool(getattr(check, "probe_chars", 0))
        verified = bool(getattr(check, "verified", False))
        reason = str(getattr(check, "reason", "") or "")
        if not skill_present:
            blockers.append(f"skill {task.skill!r} has no loadable body in this home")
        elif not verified:
            blockers.append(f"suppression unverified for {task.skill!r}: {reason or 'no reason'}")

        out.append(
            TaskPreflight(
                task_id=task.task_id,
                skill=task.skill,
                scenario_present=present,
                scenario_sha256=fingerprint.get(task.task_id, ""),
                fixture_home=fixture,
                skill_present=skill_present,
                suppression_verified=verified,
                suppression_reason=reason,
                blockers=blockers,
            )
        )
    return out


# ── report storage ───────────────────────────────────────────────────────────


def reports_dir() -> Path:
    """``evals/learning_bench/`` — one subdirectory per benchmark run."""
    d = store.evals_root() / "learning_bench"
    d.mkdir(parents=True, exist_ok=True)
    return d


def report_dir(run_id: str) -> Path:
    """``evals/learning_bench/<run_id>/`` — this run's report and raw logs."""
    d = reports_dir() / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def report_path(run_id: str) -> Path:
    return report_dir(run_id) / "report.json"


def write_report(run_id: str, report: dict) -> Path:
    """Persist one run's report atomically. Returns the path."""
    path = report_path(run_id)
    atomic_write(path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path


def read_report(run_id: str) -> dict | None:
    """One run's report, or ``None`` when it is absent or unreadable.

    ``None`` for BOTH is safe here only because every caller distinguishes them itself: the
    gateway route mints different codes for "nothing has run" and "the artifact is unreadable".
    """
    path = report_path(run_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_runs() -> list[str]:
    """Run ids, newest first. Run ids are timestamp-prefixed, so lexical order is temporal."""
    try:
        return sorted(
            (
                p.name
                for p in reports_dir().iterdir()
                if p.is_dir() and (p / "report.json").is_file()
            ),
            reverse=True,
        )
    except OSError:
        return []


def latest_report() -> dict | None:
    """The newest readable report, or ``None`` when none exists.

    Walks past an unreadable newest report to the next one rather than returning ``None``: one
    corrupt directory should not hide every earlier measurement. The report that is returned
    always carries its own ``run_id``, so a reader is never misled about which one it got.
    """
    for run_id in list_runs():
        data = read_report(run_id)
        if data is not None:
            return data
    return None


# ── V4 reproduction (§8) ─────────────────────────────────────────────────────

#: The four conditions §8 states, verbatim in spirit: same task-set version, same
#: ``scenario_sha256`` set, matching ``prompt_pack_sha256`` and ``config_snapshot_ref``, and a
#: verdict of the same class. This tuple exists so the report can PRINT the conditions it was
#: judged against — a tolerance a reader cannot see is not a stated one.
REPRODUCTION_CONDITIONS: tuple[str, ...] = (
    "same task_set_version",
    "same scenario_sha256 set",
    "same prompt_pack_sha256",
    "same config_snapshot_ref",
    "same verdict class per task",
)


@dataclass(frozen=True)
class ReproductionCheck:
    """Did a re-run reproduce the baseline within the STATED variance?

    The variance is not invented here and is not numeric. Protocol §8 states it as four
    equalities plus verdict-class agreement, and `reproduces` is exactly their conjunction.
    §8 also says the thing that keeps this honest: *"a re-run that changes the verdict class is
    a finding to publish, not a run to discard."* So a `False` here is a result, not an error.
    """

    baseline_run_id: str
    rerun_run_id: str
    reproduces: bool
    conditions: dict[str, bool] = field(default_factory=dict)
    verdict_changes: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: WHICH schema each side was written under (#2562), so a reader never has to infer one from
    #: key presence. `None` is UNRECORDED. Reported BESIDE the conditions and deliberately not AS
    #: one: §8 states four equalities plus verdict class, and a fifth condition invented here
    #: would be this code inventing variance the protocol did not state.
    baseline_report_schema: int | None = None
    rerun_report_schema: int | None = None

    def to_dict(self) -> dict:
        return {
            "baseline_run_id": self.baseline_run_id,
            "rerun_run_id": self.rerun_run_id,
            "reproduces": self.reproduces,
            "stated_variance": list(REPRODUCTION_CONDITIONS),
            "stated_variance_source": f"{PROTOCOL_DOC} §8 (Reproduction (V4))",
            "conditions": dict(self.conditions),
            "verdict_changes": [dict(c) for c in self.verdict_changes],
            "notes": list(self.notes),
            "baseline_report_schema": self.baseline_report_schema,
            "rerun_report_schema": self.rerun_report_schema,
        }


def _verdict_classes(report: dict) -> dict[str, str | None]:
    """``{task_id: verdict_class}`` as the RUNNER wrote it. Never recomputed here."""
    out: dict[str, str | None] = {}
    for row in report.get("tasks") or []:
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        cls = row.get("verdict_class")
        out[task_id] = None if cls is None else str(cls)
    return out


def reproduction_check(baseline: dict, rerun: dict) -> ReproductionCheck:
    """Judge a re-run against a baseline on §8's four conditions plus verdict class.

    Every condition is read off the two reports; nothing is recomputed. A task whose verdict
    class is ``None`` in EITHER report is a change, not a match: "not measured" reproducing
    "not measured" would let a benchmark that never ran certify itself.
    """
    notes: list[str] = []
    conditions: dict[str, bool] = {}

    b_ver = baseline.get("task_set_version")
    r_ver = rerun.get("task_set_version")
    conditions["same task_set_version"] = b_ver == r_ver
    if b_ver != r_ver:
        notes.append(
            f"task set v{b_ver} vs v{r_ver} — §2.3 forbids plotting these on one axis, so this "
            "is not a reproduction of the baseline at all"
        )

    b_fp = dict(baseline.get("task_set_fingerprint") or {})
    r_fp = dict(rerun.get("task_set_fingerprint") or {})
    conditions["same scenario_sha256 set"] = b_fp == r_fp and bool(b_fp)
    if not b_fp:
        notes.append("the baseline records no scenario_sha256 set, so there is nothing to match")
    elif b_fp != r_fp:
        drifted = sorted(k for k in set(b_fp) | set(r_fp) if b_fp.get(k) != r_fp.get(k))
        notes.append(f"scenario_sha256 differs for: {', '.join(drifted)}")

    for key, label in (
        ("prompt_pack_sha256", "same prompt_pack_sha256"),
        ("config_snapshot_ref", "same config_snapshot_ref"),
    ):
        b_val = str((baseline.get("pin") or {}).get(key) or "")
        r_val = str((rerun.get("pin") or {}).get(key) or "")
        conditions[label] = bool(b_val) and b_val == r_val
        if not b_val:
            notes.append(f"the baseline records no {key}, so it cannot be matched")
        elif b_val != r_val:
            notes.append(f"{key} moved between runs")

    b_cls = _verdict_classes(baseline)
    r_cls = _verdict_classes(rerun)
    changes: list[dict] = []
    for task_id in sorted(set(b_cls) | set(r_cls)):
        before = b_cls.get(task_id)
        after = r_cls.get(task_id)
        if before is None or after is None or before != after:
            changes.append({"task_id": task_id, "baseline": before, "rerun": after})
    conditions["same verdict class per task"] = not changes and bool(b_cls)
    if not b_cls:
        notes.append("the baseline verdicts no tasks, so there is no verdict class to reproduce")
    elif changes:
        notes.append(
            f"{len(changes)} task(s) changed verdict class — §8: a changed class is a finding to "
            "publish, not a run to discard"
        )

    # #2562: say which schema each side was written under rather than leaving a reader to infer
    # it. A v1 baseline recorded no provenance at all, so "reproduces" over it cannot also mean
    # the two runs reached the same models — and the issue's interaction with #2540 is the same
    # shape: two runs can satisfy all four equalities while one of them had unrecorded spend.
    b_schema = report_schema(baseline)
    r_schema = report_schema(rerun)
    for label, schema in (("baseline", b_schema), ("re-run", r_schema)):
        if schema is None:
            notes.append(
                f"the {label} states no report_schema, so which fields it recorded is "
                f"{provenance.UNRECORDED} — not the same claim as a report that recorded them "
                "and found nothing"
            )
        elif schema < PROVENANCE_SCHEMA:
            notes.append(
                f"the {label} was written under report_schema {schema}, before provenance and "
                f"spend recording, so what its cells could reach is {provenance.UNRECORDED}"
            )
    if b_schema is not None and r_schema is not None and b_schema != r_schema:
        notes.append(
            f"the two reports were written under different schemas ({b_schema} vs {r_schema}) — "
            "the §8 conditions above still hold or do not, but the two carry different facts"
        )

    return ReproductionCheck(
        baseline_run_id=str(baseline.get("run_id") or ""),
        rerun_run_id=str(rerun.get("run_id") or ""),
        reproduces=all(conditions.values()),
        conditions=conditions,
        verdict_changes=changes,
        notes=notes,
        baseline_report_schema=b_schema,
        rerun_report_schema=r_schema,
    )
