"""The harvested regression suite — real runs turned into scenario-library cases.

Every eval instrument in this package so far scores against AUTHORED inputs: the four shipped
scenarios in :mod:`personalclaw.evals.scenarios`, the judge fixture sets in
:mod:`personalclaw.evals.judge_bench`. That is fine for a smoke test and useless as a regression
suite, because the thing a template regresses on is the work the user actually asked for. A suite
that has to be hand-written is a suite nobody grows, and a study run over four authored scenarios
is measuring the authors.

This module is the missing direction: **ledger → scenario**. It reads terminal runs out of the Run
Ledger and emits one scenario per run, in the library's own shape, so everything the library
already does — canonical-JSON hashing (`scenarios.sha256_of_scenario_data`), the manifest
(`scenarios.read_manifest`), fixture-home resolution, `personalclaw eval` itself — applies to a
harvested case with no new machinery.

Four properties are load-bearing, and each is here because its absence is a specific failure this
repo has already shipped once:

**Inputs come from the LEDGER, never from the run row.** `WorkflowRun.inputs` is a SQLite column
written straight from the API request; `run_started.inputs` is the same dict after the ledger
writer's `redact()`. They are equal in content and NOT equal in safety, so a run with no
`run_started` record is reported unharvestable rather than harvested off the row. That single rule
is what makes "a harvested scenario cannot contain a credential" a property of the read path rather
than a hope. Each raw source is screened through :func:`_screen` exactly ONCE, at its point of
entry — a trailing pass over the composed case would garble its own output, which :func:`_screen`
explains and a test pins.

**`run_started`/`run_finished` are journal-only.** They are outside
:data:`~personalclaw.ledger.kinds.LEDGER_KINDS`, so the `events.jsonl` mirror never carries them
and :func:`~personalclaw.ledger.reader.read_events` returns `[]` for them — which reads exactly
like "the run had no inputs". :data:`JOURNAL_KINDS` and :data:`EVENT_KINDS` name the split, and
:func:`~personalclaw.ledger.reader.journal_only_kinds` is asserted against it, so the day a kind
moves between the two the test fails instead of the harvest silently going quiet.

**Provenance is mandatory.** Each case carries the run id, the workflow name and the `event_id` of
every record it was built from. A study that cannot name its population is the fabricated-evidence
shape — the same one ES-7's config-target refusal exists to prevent — and a case with no run id is
indistinguishable from an invented one.

**An empty population is a refusal, not a suite of zero.** :func:`load_harvested_suite` RAISES on an
empty suite rather than returning `[]`, because `[]` scored against a threshold reads as a pass.
:class:`HarvestReport` says `no replay population` in as many words, which is not the same statement
as a zero delta.

Writes are an idempotent, content-keyed backfill in the house style (`_init_schema`'s
`IF NOT EXISTS`, `vector_memory._MIGRATIONS`): a case's content is derived purely from the run, so
re-harvesting the same run produces byte-identical JSON and the write is skipped. Nothing here can
touch a packaged scenario — every harvested name carries :data:`HARVEST_PREFIX`, and
:func:`_target_path` refuses a collision with the shipped library outright.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import scenarios
from personalclaw.ledger import hash_value, redact
from personalclaw.ledger.kinds import (
    CONSULTED,
    RUN_FINISHED,
    RUN_STARTED,
    STEP_COMPLETED,
    STEP_FAILED,
)

logger = logging.getLogger(__name__)

#: Bumped when the SHAPE of the `harvest` provenance block changes. Distinct from a scenario's own
#: ``version`` (which the library's backfill compares) — this one tells a CONSUMER whether it
#: understands the block it is reading, and a consumer that finds a higher number should refuse
#: rather than guess at a field it has never seen.
HARVEST_VERSION = 1

#: Every harvested scenario's name starts with this. Two jobs: a reader can tell a harvested case
#: from a packaged or a user-authored one by name alone (the manifest's ``origin`` says the same
#: thing, but a name survives being copied out of the home), and no harvest can ever be written
#: over a shipped file, because nothing shipped is named this way.
HARVEST_PREFIX = "harvested_"

#: The manifest ``origin`` a harvested case reports. `install_library` derives it by INSPECTING the
#: scenario for its `harvest` block, not from a side list — a case whose provenance was stripped
#: stops claiming to be harvested in the same edit that makes the claim false.
ORIGIN_HARVESTED = "harvested"

#: The kinds that live ONLY in `journal.jsonl`. `run_started` is the sole carrier of a run's
#: post-redaction INPUTS; `run_finished` is the sole carrier of its final status and elapsed time.
#: Neither is in :data:`~personalclaw.ledger.kinds.LEDGER_KINDS`, so neither is ever mirrored.
JOURNAL_KINDS: frozenset[str] = frozenset({RUN_STARTED, RUN_FINISHED})

#: The kinds read from the `events.jsonl` mirror. `step_completed` carries the OUTPUTS (as
#: `output_ref` pointers — the bodies were spilled by the writer, not inlined); `step_failed` is
#: what makes a failed run harvestable as a regression case rather than discarded; `consulted` is
#: WHICH skill/template a run actually loaded, which is the population ES-7's skills bench gates on.
EVENT_KINDS: frozenset[str] = frozenset({STEP_COMPLETED, STEP_FAILED, CONSULTED})

#: Why a run was not harvested. Named as data so the CLI, a test and a consumer all say the same
#: word — a free-text reason is a reason nobody can filter on.
SKIP_NOT_TERMINAL = "not_terminal"
SKIP_NO_RUN_STARTED = "no_run_started"
SKIP_NO_WORKFLOW_NAME = "no_workflow_name"

SKIP_REASONS: frozenset[str] = frozenset(
    {SKIP_NOT_TERMINAL, SKIP_NO_RUN_STARTED, SKIP_NO_WORKFLOW_NAME}
)

#: The refusal sentence. A population of zero is a statement about the LEDGER, and saying it in
#: these words is what stops a caller from reading it as a measurement.
NO_POPULATION = (
    "no replay population: the Run Ledger holds no harvestable terminal run, "
    "which is not the same as a harvested suite of zero cases"
)

#: How many runs a harvest looks at when the caller does not say.
DEFAULT_LIMIT = 50


class EmptyHarvestError(RuntimeError):
    """The harvested suite is empty, and the caller asked for a suite.

    Raised — never returned as `[]` — because an empty case list scored against a threshold
    passes, so a study that silently harvested nothing reports a green it never measured.
    """


# ── the harvested case ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class HarvestedCase:
    """One run, as a scenario-library case.

    `scenario` is the library-shaped dict (`sha256` is its canonical-JSON hash via the library's
    own hasher, so a harvested row in `results.tsv` is pinned exactly like an authored one).
    `path` is where it was written, or `None` for a dry run.
    """

    name: str
    run_id: str
    workflow_name: str
    scenario: dict[str, Any]
    sha256: str
    path: Path | None = None
    written: bool = False

    @property
    def provenance(self) -> dict[str, Any]:
        """The `harvest` block — run id + the event ids the case was built from."""
        block = self.scenario.get("harvest")
        return dict(block) if isinstance(block, dict) else {}


@dataclass(frozen=True)
class HarvestReport:
    """What one harvest saw, kept and refused.

    Distinguishes three states a single integer could not: cases were harvested (`cases`),
    runs existed but none qualified (`considered` > 0, `cases` empty, `skipped` explains each),
    or there was nothing to look at at all (:attr:`is_refusal`).
    """

    cases: list[HarvestedCase] = field(default_factory=list)
    considered: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def population(self) -> int:
        return len(self.cases)

    @property
    def is_refusal(self) -> bool:
        """True when there was no population to measure — NOT when a measurement came out zero.

        `considered == 0` means the ledger had no terminal run to look at. A harvest that looked
        at runs and kept none of them is a different statement, and :attr:`skipped` names why for
        each one.
        """
        return self.considered == 0

    @property
    def refusal(self) -> str:
        """:data:`NO_POPULATION` when there was nothing to harvest, else `""`."""
        return NO_POPULATION if self.is_refusal else ""

    def skipped_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.skipped:
            reason = entry.get("reason", "")
            counts[reason] = counts.get(reason, 0) + 1
        return counts


# ── reading a run ────────────────────────────────────────────────────────────


def _screen(value: Any) -> Any:
    """Route one externally-sourced value through the ledger's redactor — EXACTLY ONCE.

    "Exactly once" is not fussiness, it is a measured property of
    :func:`~personalclaw.ledger.redaction.redact`. It is idempotent on its OWN output
    (`redact(redact("sk-…")) == redact("sk-…")`) but NOT on an already-screened value sitting in a
    `<key>: <value>` string: `redact_credentials` has a `key: value` pattern, so a second pass over
    `api_key: [REDACTED: credential]` matches `api_key: [REDACTED:` and rewrites it to
    `[REDACTED: credential] credential]` — garbled text AND the field name silently lost.

    So there is no "screen the whole composed scenario at the end" chokepoint here: composing a
    turn line out of already-screened inputs and then re-screening the line is exactly the shape
    that trips it. Instead every raw source is screened at the ONE point it enters, and everything
    downstream is composed from the screened values.
    """
    return redact(value)


def _journal_mod() -> Any:
    # Lazy: `evals` is imported by the CLI at parse time and the workflow engine is heavy.
    from personalclaw.workflows import journal  # noqa: PLC0415 - cycle-free, load-deferred

    return journal


def _run_store() -> Any:
    from personalclaw.workflows import store as run_store  # noqa: PLC0415 - cycle-free

    return run_store


def harvestable_runs(*, workflow_name: str = "", limit: int = DEFAULT_LIMIT) -> list[Any]:
    """Candidate runs, newest first. TERMINAL only — an in-flight run's ledger is incomplete.

    Returns candidates, not cases: whether a candidate qualifies is decided by
    :func:`case_from_run`, which needs its ledger. Splitting the two is what lets the report
    say "looked at 12, kept 3, and here is why the other 9 were dropped" instead of "3".
    """
    runs, _total = _run_store().list_runs(workflow_name=workflow_name, limit=max(1, limit))
    return list(runs)


def _skip(run_id: str, reason: str) -> dict[str, str]:
    return {"run_id": run_id, "reason": reason}


def _render_turn(workflow_name: str, inputs: dict[str, Any]) -> str:
    """The replayable prompt text, rendered deterministically from redacted inputs.

    Deterministic (sorted keys, canonical JSON for non-scalars) because the scenario's identity is
    the hash of its content: a render that depended on dict order would give the same run a new
    sha256 on every harvest, and the idempotent backfill would rewrite the file forever.

    Always non-empty — it names the workflow even when the run took no inputs. A harvested
    scenario with ZERO turns would be run by `personalclaw eval` and pass without asserting
    anything, which is the "a zero-case suite reads as a pass" failure at the level of a single
    case.
    """
    lines = [
        "Reproduce this recorded run's request.",
        "",
        f"workflow: {workflow_name}",
    ]
    if inputs:
        lines.append("inputs:")
        for key in sorted(inputs):
            value = inputs[key]
            rendered = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
            lines.append(f"- {key}: {rendered}")
    else:
        lines.append("inputs: (none recorded)")
    return "\n".join(lines)


def _judge_criteria(workflow_name: str, status: str) -> str:
    """What a judge is asked, stated as what the RUN did — not as an invented golden.

    Deliberately not derived from the run's output text. Turning one observed answer into a
    `contains`/`regex` assertion mints a golden nobody reviewed, and a suite of unreviewed goldens
    fails on correct answers that happen to be worded differently. The observed outcome is recorded
    as a `baseline` for pairing instead, which is what a paired A/B study actually consumes.
    """
    return (
        f"The response should carry out the recorded request for workflow '{workflow_name}' "
        f"at least as well as the harvested run did (that run ended '{status}'). "
        "Compare against the recorded baseline rather than an authored golden."
    )


def case_from_run(run: Any, *, journal: Any = None) -> tuple[dict[str, Any] | None, str]:
    """Build one run's scenario dict, or say why it cannot be built.

    Returns `(scenario, "")` on success and `(None, reason)` otherwise, where `reason` is a member
    of :data:`SKIP_REASONS`. Every externally-sourced value is screened through :func:`_screen`
    at the ONE point it enters — see that function for why a single trailing pass over the composed
    scenario would corrupt its own output instead of hardening it.
    """
    journal = journal or _journal_mod()
    run_id = str(getattr(run, "id", "") or "")
    if not getattr(run, "is_terminal", False):
        return None, SKIP_NOT_TERMINAL

    journal_recs = journal.journal_records(run_id, kinds=set(JOURNAL_KINDS))
    started = next((r for r in journal_recs if r.get("kind") == RUN_STARTED), None)
    if started is None:
        # No `run_started` means no redaction-guaranteed inputs. The run ROW has the same dict
        # unredacted, and reading it from there is exactly the shortcut this refusal exists to
        # forbid: it would put an un-screened API payload into a file the flywheel reads forever.
        return None, SKIP_NO_RUN_STARTED
    finished = next((r for r in journal_recs if r.get("kind") == RUN_FINISHED), None)

    workflow_name = str(_screen(started.get("workflow_name") or "") or "")
    if not workflow_name:
        return None, SKIP_NO_WORKFLOW_NAME

    # THE screen for the inputs — the one raw source that carries user content. Everything
    # downstream (the turn text, the `harvest.inputs` block) is composed from THIS value and is
    # never screened a second time; `_screen` explains why a second pass would corrupt it.
    raw_inputs = started.get("inputs")
    inputs = _screen(dict(raw_inputs)) if isinstance(raw_inputs, dict) else {}
    if not isinstance(inputs, dict):  # pragma: no cover - redact preserves dict shape
        inputs = {}

    events = journal.ledger(run_id, kinds=set(EVENT_KINDS))
    steps = [e for e in events if e.get("kind") == STEP_COMPLETED]
    failures = [e for e in events if e.get("kind") == STEP_FAILED]
    consulted = [e for e in events if e.get("kind") == CONSULTED]

    status = str(finished.get("status") if finished else "") or str(
        getattr(getattr(run, "status", None), "value", "") or ""
    )
    totals = journal.run_totals(run_id)

    scenario: dict[str, Any] = {
        "name": case_name(workflow_name, run_id),
        "version": 1,
        "fixture_home": scenarios.DEFAULT_FIXTURE_HOME,
        "description": (
            f"Harvested from run {run_id} of workflow '{workflow_name}' "
            f"({len(steps)} completed steps, {len(failures)} failed, ended '{status}')."
        ),
        "dimensions": ["harvested_run", workflow_name],
        "judge_criteria": _judge_criteria(workflow_name, status),
        "harvest": {
            "harvest_version": HARVEST_VERSION,
            "run_id": run_id,
            "workflow_name": workflow_name,
            "spec_version": int(started.get("spec_version", 0) or 0),
            "status": status,
            "run_started_at": str(started.get("ts") or ""),
            "resumed": bool(started.get("resumed", False)),
            "inputs": inputs,
            "baseline": {
                **totals,
                "status": status,
                "elapsed_secs": (
                    float(finished.get("elapsed_secs", 0.0) or 0.0) if finished else 0.0
                ),
            },
            "outputs": _output_refs(run_id, steps),
            "consulted_refs": sorted({str(_screen(e.get("ref") or "")) for e in consulted} - {""}),
            "provenance": {
                "run_started_event_id": str(started.get("event_id") or ""),
                "run_finished_event_id": str(finished.get("event_id") or "") if finished else "",
                "event_ids": [str(e.get("event_id") or "") for e in events],
            },
        },
        "sessions": [
            {
                "name": "harvested_run",
                "turns": [
                    {
                        "user": _render_turn(workflow_name, inputs),
                        "assertions": [
                            {"type": "judge", "value": _judge_criteria(workflow_name, status)}
                        ],
                    }
                ],
            }
        ],
    }
    # NO trailing whole-scenario `redact()` here, deliberately. Every raw source above went
    # through `_screen` at its point of entry; a second pass over the composed dict would hit the
    # turn line `- api_key: [REDACTED: credential]` and rewrite it to
    # `- [REDACTED: credential] credential]`, destroying the field name to harden a value that was
    # already hard. See `_screen`, and `test_redaction_is_not_idempotent_over_a_key_value_line`.
    return scenario, ""


def _output_refs(run_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each completed step's output as a `(node, ref, sha256)` triple — never the body.

    The body is deliberately not inlined: the writer already spilled anything oversize or binary
    to `artifacts/`, and re-inlining it here would undo that in a file the library hashes. The
    content hash is read back through the run store so the baseline OUTLIVES the run directory —
    retention reaps `runs/<id>/`, and a case whose only record of the old answer was a path into
    it would silently become unpairable.

    `resolved_prompt_ref` rides along because it is the ONLY pointer to what a node was actually
    asked — the fully-resolved post-binding prompt. A replay that reconstructs the prompt from the
    template plus `inputs` is replaying today's template, which is precisely the variable an A/B
    study is trying to hold still on the OLD arm.
    """
    run_store = _run_store()
    out: list[dict[str, Any]] = []
    for step in steps:
        node_path = str(step.get("instance_path") or "")
        ref = str(step.get("output_ref") or "")
        digest = ""
        if node_path:
            try:
                body = run_store.read_output(run_id, node_path)
            except (OSError, ValueError):  # pragma: no cover - defensive; read_output logs
                logger.debug("harvest: unreadable output for %s/%s", run_id, node_path)
                body = None
            if body is not None:
                digest = hash_value(_screen(body))
        out.append(
            {
                "node_id": str(step.get("node_id") or ""),
                "instance_path": node_path,
                "output_ref": ref,
                "output_sha256": digest,
                "resolved_prompt_ref": str(step.get("resolved_prompt_ref") or ""),
            }
        )
    return out


def case_name(workflow_name: str, run_id: str) -> str:
    """`harvested_<workflow>_<run-id>` — stable, so re-harvesting a run overwrites its own case.

    Keyed on the RUN ID, not on a counter or a timestamp: a name that moved between harvests would
    accumulate one file per pass over the same run, and the idempotent-backfill property would be
    lost. Non-identifier characters are folded so the name is a legal file stem on every platform.
    """
    slug = "".join(c if (c.isalnum() or c == "_") else "_" for c in workflow_name).strip("_")
    tail = "".join(c if (c.isalnum() or c == "_") else "_" for c in run_id).strip("_")
    return f"{HARVEST_PREFIX}{slug or 'workflow'}_{tail or 'run'}"


# ── writing (idempotent, content-keyed) ──────────────────────────────────────


def _target_path(name: str) -> Path:
    """Where a harvested case is written, refusing anything that could shadow the library.

    Two refusals, both about the packaged set: a name without :data:`HARVEST_PREFIX` (so a caller
    cannot pass `smoke_test`), and a name that matches a shipped file even WITH the prefix (so the
    guard survives someone shipping a `harvested_*` scenario one day). The shipped library is
    upgraded by a release and must never be a harvest's write target.
    """
    if not name.startswith(HARVEST_PREFIX):
        raise scenarios.ScenarioLibraryError(
            f"refusing to write harvested case {name!r}: "
            f"a harvested scenario name must start with {HARVEST_PREFIX!r}"
        )
    filename = f"{name}.json"
    if (scenarios.packaged_library_dir() / filename).exists():
        raise scenarios.ScenarioLibraryError(
            f"refusing to write harvested case {name!r}: it would shadow the shipped scenario "
            f"of the same name"
        )
    return scenarios.installed_dir() / filename


def write_case(scenario: dict[str, Any]) -> tuple[Path, bool]:
    """Write one harvested case; return `(path, wrote)`.

    Idempotent and content-keyed, the same rule
    :func:`~personalclaw.evals.scenarios.install_library` uses: the case's content is derived
    purely from the run, so an existing file with the same
    :func:`~personalclaw.evals.scenarios.sha256_of_scenario_data` is already correct and `wrote`
    is `False`. A DIFFERENT hash means the run's ledger grew (a late `run_finished`, a resumed
    run's extra steps) and the case is refreshed — the newer read of the same run wins, because
    both describe one immutable run and the longer ledger is the more complete one.
    """
    name = str(scenario.get("name") or "")
    path = _target_path(name)
    digest = scenarios.sha256_of_scenario_data(scenario)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and scenarios.sha256_of_scenario_data(existing) == digest:
                return path, False
        except (OSError, ValueError):
            logger.warning("harvest: unreadable existing case %s, rewriting", path, exc_info=True)
    atomic_write(path, json.dumps(scenario, indent=2, sort_keys=True) + "\n")
    return path, True


# ── the harvest ──────────────────────────────────────────────────────────────


def harvest(
    *,
    workflow_name: str = "",
    limit: int = DEFAULT_LIMIT,
    write: bool = True,
    journal: Any = None,
) -> HarvestReport:
    """Turn the Run Ledger's terminal runs into scenario-library cases.

    `write=False` is the preflight: cases are built and hashed but nothing lands, so a caller can
    see the population and the skip reasons before touching the installed library. When anything
    IS written the library manifest is refreshed, so `read_manifest()` names the harvested cases
    (`origin: "harvested"`) in the same place it names the shipped ones — one inventory, not two.
    """
    journal = journal or _journal_mod()
    cases: list[HarvestedCase] = []
    skipped: list[dict[str, str]] = []
    candidates = harvestable_runs(workflow_name=workflow_name, limit=limit)

    for run in candidates:
        run_id = str(getattr(run, "id", "") or "")
        scenario, reason = case_from_run(run, journal=journal)
        if scenario is None:
            skipped.append(_skip(run_id, reason))
            continue
        path: Path | None = None
        wrote = False
        if write:
            try:
                path, wrote = write_case(scenario)
            except (scenarios.ScenarioLibraryError, OSError):
                logger.warning("harvest: could not write case for run %s", run_id, exc_info=True)
                skipped.append(_skip(run_id, SKIP_NO_WORKFLOW_NAME))
                continue
        cases.append(
            HarvestedCase(
                name=str(scenario.get("name") or ""),
                run_id=run_id,
                workflow_name=str((scenario.get("harvest") or {}).get("workflow_name") or ""),
                scenario=scenario,
                sha256=scenarios.sha256_of_scenario_data(scenario),
                path=path,
                written=wrote,
            )
        )

    if write and cases:
        # Refresh the ONE manifest so the harvested cases are inventoried beside the shipped ones.
        scenarios.install_library()

    return HarvestReport(cases=cases, considered=len(candidates), skipped=skipped)


# ── consuming (the strict loader) ────────────────────────────────────────────


def installed_harvested_cases(*, workflow_name: str = "") -> list[dict[str, Any]]:
    """Every harvested case already in the installed library, oldest run first.

    Reads the library dir rather than re-harvesting, so a consumer scores the suite that was
    reviewed rather than whatever the ledger looks like this second. Selection is by the `harvest`
    block's presence — the same inspection `install_library` uses for `origin` — so a case whose
    provenance was stripped drops out of the suite instead of being scored anonymously.
    """
    out: list[dict[str, Any]] = []
    directory = scenarios.installed_dir()
    if not directory.is_dir():
        return out
    for path in sorted(directory.iterdir()):
        if path.suffix not in scenarios.SCENARIO_SUFFIXES:
            continue
        if not path.stem.startswith(HARVEST_PREFIX):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("harvest: skipping unreadable case %s", path, exc_info=True)
            continue
        block = data.get("harvest") if isinstance(data, dict) else None
        if not isinstance(block, dict) or not block.get("run_id"):
            continue
        if workflow_name and str(block.get("workflow_name") or "") != workflow_name:
            continue
        out.append(data)
    out.sort(key=lambda d: str((d.get("harvest") or {}).get("run_started_at") or ""))
    return out


def load_harvested_suite(*, workflow_name: str = "") -> list[dict[str, Any]]:
    """The harvested suite, or :class:`EmptyHarvestError` — never an empty list.

    This is the entry point a study or a bench should call. Raising is the whole point: a caller
    that got `[]` and compared it to a threshold would report a pass it never measured, and "the
    suite is empty" and "the suite scored zero" are the two statements a harvested suite exists to
    keep apart.
    """
    cases = installed_harvested_cases(workflow_name=workflow_name)
    if not cases:
        scope = f" for workflow {workflow_name!r}" if workflow_name else ""
        raise EmptyHarvestError(
            f"{NO_POPULATION}{scope}. Run `personalclaw eval-harvest` after some runs have "
            f"finished; a suite of zero cases must not be scored."
        )
    return cases
