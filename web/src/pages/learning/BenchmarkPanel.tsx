import { ExternalLink, FlaskConical, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../ui/ListScaffold'
import { fvs } from '../../design/fontWeight'
import { hasApiCode } from '../../lib/api'
import { EvalsOff } from './EvalsOff'
import type {
  BenchmarkArmAggregate, BenchmarkReport, BenchmarkTaskRow, BenchmarkView,
} from '../../lib/api'

/** Canonical blob root for repo docs, from `pyproject.toml`'s `[project.urls] Source`.
 *
 *  The PATH is not hardcoded beside it — it comes from the report's own `protocol_doc`, so the
 *  methodology link can never point at a different document than the one the runner cited. */
const DOC_BLOB_ROOT = 'https://github.com/PersonalClaw/PersonalClaw/blob/main/'

/** The skill-impact benchmark: does an approved skill make the next run better? (LV-7)
 *
 *  Two arms over identical work — `skills_on` (the skill is available to surfacing) and
 *  `skills_off` (it is suppressed, inside the spawned child only) — over a frozen ten-task
 *  register, each trial in a fresh seeded fixture home. The protocol was owner-signed BEFORE any
 *  run, including the commitment to publish a modest or negative result, which is why this panel
 *  gives `inconclusive` and a skills-off win the same prominence as a win.
 *
 *  **Nothing is decided here, and nothing CAN be.** The §5 thresholds live in
 *  `harness/fanout_measure.py`, a dev package outside the shipped wheel, so neither the gateway
 *  nor this page is able to compute a verdict — the runner writes it into the report and these
 *  surfaces read it. That is the structural version of the rule below.
 *
 *  **`null` renders as "not measured", never as a zero.** A task whose arms could not be
 *  assembled has no delta. Drawing 0.000 for it would turn "we never measured this" into "the
 *  skill scored nothing" — and for a benchmark whose whole question is whether skills help, a
 *  fabricated zero is the strongest possible case for the answer being no.
 *
 *  **A failed fetch is not an empty benchmark.** "No benchmark has run yet" is this panel's
 *  ORDINARY state for months, so it must be distinguishable from "we could not ask". The backend
 *  mints three distinct codes and this reads them. */
export function BenchmarkPanel({ view, error, onRetry }: {
  view: BenchmarkView | undefined
  error: unknown
  onRetry: () => void
}) {
  if (view === undefined && error) {
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="skillbench-heading">
          <Heading />
          {/* 🔑 `EvalsOff` OWNS THIS SENTENCE, and this panel was the one of five that never adopted it.
              Its four siblings — Judge tiers, Template studies, Retrieval arms, Component ablation —
              all render `<EvalsOff what="…" />`; this branch hand-rolled the copy the shared
              component's own docstring records as the FIXED-AND-WRONG version, and kept all three of
              its defects:

                · `<code>evals.enabled</code>` — the dotted path. "The right instruction for a terminal
                  and the wrong one for a link: `evals.enabled` appears nowhere on the destination."
                  `EvalsOff` names the CONTROL ("Evals enabled"), which is that field's own `_meta`
                  label — the words a user then looks for on the page.
                · `href="#/settings"` — the 34-card hub, which the docstring calls "the dead-end version".
                  `#/settings/evals` renders the actual switch.
                · a link whose text is just "Settings", so its accessible name was "Settings" rather
                  than the whole instruction. `EvalsOff` spans control AND destination so the name
                  carries the purpose out of context.

              The `learning_benchmark_absent` branch below keeps its OWN command on purpose — the
              docstring's rule is that turning a setting on and registering a component are two
              different places, so each panel's `*_absent` state owns its run command. */}
          <EvalsOff what="benchmark" />
        </section>
      )
    }
    if (hasApiCode(error, 'learning_benchmark_absent')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="skillbench-heading">
          <Heading />
          <p className="text-on-surface-low text-[0.8125rem]">
            No skill-impact benchmark has run yet. Start with{' '}
            <code className="text-on-surface-var">
              python scripts/learning_benchmark.py --preflight
            </code>{' '}
            — it calls no model — then <code className="text-on-surface-var">--run</code>. The full
            paired design is 100 real model calls, so nothing here starts one on a click.
          </p>
          <MethodologyLink doc="docs/roadmap/research/learning-benchmark-protocol.md" />
        </section>
      )
    }
    return <LoadError what="skill-impact benchmark" error={error} onRetry={onRetry} />
  }
  if (!view) return null

  const report = view.report
  const rows = orderedRows(view)

  return (
    <section className="flex flex-col gap-m" aria-labelledby="skillbench-heading">
      <Heading runId={report.run_id} />

      <p className="text-on-surface-low text-[0.8125rem]">
        Does a skill you approved make the next run better? Two arms over identical work —{' '}
        <span className="text-on-surface-var">skills_on</span> and{' '}
        <span className="text-on-surface-var">skills_off</span> — at{' '}
        {report.trials_per_arm} trial{report.trials_per_arm === 1 ? '' : 's'} per arm, each in a
        fresh seeded fixture home. Task set v{report.task_set_version}
        {report.created_at ? <> · {report.created_at}</> : null}.
      </p>

      <Coverage report={report} registerSize={view.register.length} />

      <div className="overflow-x-auto rounded-lg bg-surface-container">
        <table className="w-full text-[0.75rem]">
          <caption className="sr-only">
            Per-task skills-on versus skills-off results for task set v{report.task_set_version},
            with unmeasured tasks shown as not measured rather than as zero
          </caption>
          <thead>
            <tr className="text-on-surface-low">
              <th scope="col" className="px-m py-s text-left">Task</th>
              <th scope="col" className="px-m py-s text-left">Skill</th>
              <th scope="col" className="px-m py-s text-left">Verdict</th>
              <th scope="col" className="px-m py-s text-right">Delta (pts)</th>
              <th scope="col" className="px-m py-s text-right">skills_on</th>
              <th scope="col" className="px-m py-s text-right">skills_off</th>
              <th scope="col" className="px-m py-s text-right">Absent</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => <TaskRow key={row.task_id} row={row} />)}
          </tbody>
        </table>
      </div>

      <p className="text-on-surface-low text-[0.75rem]">
        A delta under {report.thresholds.inconclusive_band_points} points is reported as{' '}
        <span className="text-on-surface-var">inconclusive</span>, including in our favour, and a
        delta smaller than its own arm&apos;s spread is unresolved too. Arms whose spend differs by
        more than {pct(report.thresholds.token_match_tolerance)} yield{' '}
        <span className="text-on-surface-var">not_token_matched</span> — the measurement declining a
        question it did not ask. Below{' '}
        {report.thresholds.min_trials_per_arm} trials per arm, no verdict is offered at all.
        Thresholds come from <code className="text-on-surface-var">{report.thresholds.source}</code>,
        not from this page.
      </p>

      {report.reproduction && <Reproduction repro={report.reproduction} />}

      {report.skipped.length > 0 && <Skipped report={report} />}

      <MethodologyLink doc={report.protocol_doc || view.protocol_doc} />
    </section>
  )
}

function Heading({ runId }: { runId?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-s">
      <FlaskConical size={16} className="text-on-surface-var" />
      <span id="skillbench-heading" data-type="title-m" className="text-on-surface">
        Skill impact benchmark
      </span>
      {runId && <span className="text-on-surface-low text-[0.75rem]">{runId}</span>}
    </div>
  )
}

/** How much of the frozen register this report actually covers.
 *
 *  Without this a report of one measured task reads as the whole benchmark. §6 also requires the
 *  `VERIFIER_ABSENT` count to be part of the result rather than a footnote, so it is here and not
 *  hidden in the table alone. */
function Coverage({ report, registerSize }: { report: BenchmarkReport; registerSize: number }) {
  const measured = report.measured_tasks
  const none = measured === 0
  return (
    <div className="flex flex-col gap-xs rounded-lg bg-surface-container px-l py-m">
      <span data-type="title-s" className="text-on-surface">
        {none
          ? 'Nothing was measured'
          : `${measured} of ${registerSize} task${registerSize === 1 ? '' : 's'} measured`}
      </span>
      {none && (
        <span
          className="inline-flex w-fit items-center gap-1.5 rounded-pill px-m h-6 text-[0.75rem]"
          style={{
            background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)',
            color: 'var(--color-warn)',
          }}
        >
          <ShieldAlert size={12} /> not measured
        </span>
      )}
      <p className="text-on-surface-low text-[0.8125rem]">
        {none
          ? 'No task assembled both arms, so there is no delta to read and none is drawn. This is '
            + 'published as an unmeasured run rather than as a zero: a fabricated 0.000 here would '
            + 'read as "the skills you approved do nothing".'
          : `${report.absent_cells} cell${report.absent_cells === 1 ? '' : 's'} came back with an `
            + 'absent verifier — a timeout, spawn fault or unparseable child. Those are counted, '
            + 'never folded into an arm, so an infrastructure failure can never register as a '
            + 'skills-off win.'}
      </p>
    </div>
  )
}

function TaskRow({ row }: { row: BenchmarkTaskRow }) {
  const on = row.arms.skills_on
  const off = row.arms.skills_off
  return (
    <tr className="border-t border-outline-variant/30 align-top">
      <td className="px-m py-s text-on-surface">{row.task_id}</td>
      <td className="px-m py-s text-on-surface-var">{row.skill}</td>
      <td className="px-m py-s">
        <span className={row.verdict === null ? 'text-on-surface-low' : 'text-on-surface-var'}>
          {verdictLabel(row.verdict)}
        </span>
        {row.reason && (
          <p className="text-on-surface-low">{row.reason}</p>
        )}
        {row.verdict !== null && !row.spend_observed && (
          <p className="text-on-surface-low">spend not observed — the token ratio is not a match</p>
        )}
        {row.spend_estimated && (
          <p className="text-on-surface-low">tokens estimated, not provider-reported</p>
        )}
      </td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtDelta(row.delta_points)}</td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtArm(on)}</td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtArm(off)}</td>
      <td className="px-m py-s text-right text-on-surface-var">{row.absent_cells}</td>
    </tr>
  )
}

/** The §8 (V4) reproduction judgement.
 *
 *  The conditions are printed, not summarised to a boolean, because "within stated variance" is
 *  only meaningful if the reader can see the variance. `stated_variance_source` cites where the
 *  protocol states it, so a tolerance this code invented would be visibly missing a citation. */
function Reproduction({ repro }: { repro: NonNullable<BenchmarkReport['reproduction']> }) {
  const entries = Object.entries(repro.conditions)
  return (
    <div className="flex flex-col gap-xs">
      <span data-type="title-s" className="text-on-surface">
        {repro.reproduces
          ? 'Reproduced within the stated variance'
          : 'Did NOT reproduce within the stated variance'}
      </span>
      <p className="text-on-surface-low text-[0.75rem]">
        Baseline <span className="text-on-surface-var">{repro.baseline_run_id || '—'}</span> vs
        re-run <span className="text-on-surface-var">{repro.rerun_run_id || '—'}</span>. The
        variance is stated in <code className="text-on-surface-var">
          {repro.stated_variance_source}
        </code> — it is a set of equalities plus verdict-class agreement, not a number this page
        chose. A changed verdict class is a finding to publish, not a run to discard.
      </p>
      <ul className="flex flex-col gap-xs">
        {entries.map(([label, ok]) => (
          <li key={label} className="text-[0.75rem]">
            <span className={ok ? 'text-on-surface-var' : 'text-on-surface-low'}>
              {ok ? 'met' : 'not met'}
            </span>
            <span className="text-on-surface-low"> · {label}</span>
          </li>
        ))}
      </ul>
      {repro.verdict_changes.length > 0 && (
        <p className="text-on-surface-low text-[0.75rem]">
          {repro.verdict_changes.length} task
          {repro.verdict_changes.length === 1 ? '' : 's'} changed verdict class:{' '}
          {repro.verdict_changes.map((c) => c.task_id).join(', ')}.
        </p>
      )}
    </div>
  )
}

/** Tasks the runner refused to run, with the refusal's own words.
 *
 *  Shown rather than dropped: an unbound home cannot record a benchmark result at all (the run
 *  pin is the comparability claim and an incomplete pin is refused before a cell spawns), and a
 *  table that silently omitted those tasks would make a two-task report look like the register. */
function Skipped({ report }: { report: BenchmarkReport }) {
  return (
    <div className="flex flex-col gap-xs">
      <span data-type="title-s" className="text-on-surface">
        Not run ({report.skipped.length})
      </span>
      <ul className="flex flex-col gap-xs">
        {report.skipped.map((row) => (
          <li key={row.task_id} className="rounded-lg bg-surface-container px-l py-m">
            <span className="text-on-surface text-[0.8125rem]" style={fvs(600)}>{row.task_id}</span>
            <span className="text-on-surface-low text-[0.75rem]"> · {row.skill}</span>
            {row.blockers.map((blocker) => (
              <p key={blocker} className="text-on-surface-low text-[0.75rem]">{blocker}</p>
            ))}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** The methodology link — the `done_when`'s second half, and the reason a number here is
 *  readable at all. The path comes from the report, so the link cannot drift from the document
 *  the runner cited. */
function MethodologyLink({ doc }: { doc: string }) {
  return (
    <a
      className="inline-flex w-fit items-center gap-1.5 text-on-surface-var text-[0.75rem] underline"
      href={`${DOC_BLOB_ROOT}${doc}`}
      target="_blank"
      rel="noreferrer"
    >
      Methodology: the benchmark protocol <ExternalLink size={12} aria-hidden="true" />
    </a>
  )
}

/** Report rows in REGISTER order, with any row the report carries but the register does not
 *  appended rather than dropped. A row the panel silently discarded would be a task that ran and
 *  is not in the published table. */
function orderedRows(view: BenchmarkView): BenchmarkTaskRow[] {
  const byId = new Map(view.report.tasks.map((t) => [t.task_id, t]))
  const ordered = view.register
    .map((r) => byId.get(r.task_id))
    .filter((t): t is BenchmarkTaskRow => t !== undefined)
  const known = new Set(view.register.map((r) => r.task_id))
  return [...ordered, ...view.report.tasks.filter((t) => !known.has(t.task_id))]
}

/** `null` is UNMEASURED. It must never render as a verdict string, and never as a zero. */
function verdictLabel(verdict: string | null): string {
  return verdict === null ? 'not measured' : verdict
}

/** A signed delta in points. `null` means the arms could not be assembled — the one value that
 *  must never render as 0.000, because a zero delta is the case for "skills do not help". */
function fmtDelta(value: number | null): string {
  if (value === null) return 'not measured'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`
}

/** An arm's mean with its own spread beside it. An absent arm is "not measured", not 0.00 —
 *  §5's rule that within-arm spread beats the delta only reads if the spread is visible. */
function fmtArm(agg: BenchmarkArmAggregate | undefined): string {
  if (!agg) return 'not measured'
  return `${agg.mean_score.toFixed(2)} ±${agg.spread.toFixed(2)}`
}

function pct(fraction: number): string {
  return `${(fraction * 100).toFixed(0)}%`
}
