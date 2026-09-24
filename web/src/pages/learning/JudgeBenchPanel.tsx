import { Gavel, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../ui/ListScaffold'
import { fvs } from '../../design/fontWeight'
import { hasApiCode } from '../../lib/api'
import type { JudgeBenchRecommendation, JudgeBenchRow, JudgeBenchView } from '../../lib/api'
import { EvalsOff } from './EvalsOff'

/** The judge tier-recommendation table (EVALUATION-SUBSTRATE §6 / ES-4).
 *
 *  Every gate in the flywheel and the engine rests on a judge verdict, and the only way to
 *  know which model a rubric actually needs is to measure it. This panel publishes that
 *  measurement: per (rubric class x tier x judge_samples), agreement with the known
 *  verdict, strong-vs-null separation, position-swap flip rate, cost and wall time — with
 *  the harness's own failure-mode notes beside each row.
 *
 *  **Nothing is re-decided here.** `adequate` and `inadequate_reasons` arrive computed. A UI
 *  that re-derived adequacy from the numbers would eventually disagree with the harness, and
 *  the copy shipping the permissive answer would be this one.
 *
 *  **`null` renders as "not measured", never as a zero.** An unmeasured separation or flip
 *  rate is exactly WHY a row is inadequate; drawing 0.00 for it would read as a flawless
 *  score, which is the most confident possible way to say the opposite of what happened. */
export function JudgeBenchPanel({ bench, error, onRetry }: {
  bench: JudgeBenchView | undefined
  error: unknown
  onRetry: () => void
}) {
  // A 404 is the ordinary state here — the substrate is off, or no benchmark has run — so both
  // render as guidance, not as a failure. Any other error is surfaced: the panel's subject is
  // "can I trust the judge?", and a swallowed fetch would answer it with silence.
  if (bench === undefined && error) {
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="judge-bench-heading">
          <Heading />
          <EvalsOff what="judge benchmark" />
        </section>
      )
    }
    if (hasApiCode(error, 'judge_bench_absent')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="judge-bench-heading">
          <Heading />
          <p className="text-on-surface-low text-[0.8125rem]">
            No benchmark has run yet. Run <code className="text-on-surface-var">personalclaw judge-bench</code>{' '}
            to measure which model tier each rubric actually needs. It is a deliberate command,
            not a background job — the full matrix is hundreds of judge calls, so nothing here
            spends money on its own.
          </p>
        </section>
      )
    }
    return <LoadError what="judge benchmark" error={error} onRetry={onRetry} />
  }
  if (!bench) return null

  const classes = [...new Set(bench.rows.map((r) => r.rubric_class))]

  return (
    <section className="flex flex-col gap-m" aria-labelledby="judge-bench-heading">
      <Heading benchId={bench.bench_id} />

      <p className="text-on-surface-low text-[0.75rem]">
        Adequate means: agreement ≥ {fmt(bench.floors.agreement)} · strong-vs-null separation ≥{' '}
        {fmt(bench.floors.separation)} · position-swap flip rate ≤ {fmt(bench.floors.flip_rate)} ·
        and no forbidden-success-mode case passed. A floor is not a preference: one missed
        disqualifier rules a tier out on its own.
      </p>

      {bench.recommendations.map((rec) => (
        <RecommendationCard key={rec.rubric_class} rec={rec} />
      ))}

      {classes.map((rubricClass) => (
        <div key={rubricClass} className="flex flex-col gap-xs">
          <span data-type="title-s" className="text-on-surface">{rubricClass}</span>
          <div className="overflow-x-auto rounded-lg bg-surface-container">
            <table className="w-full text-[0.75rem]">
              <caption className="sr-only">
                Judge benchmark results for the {rubricClass} rubric class, by tier and sample count
              </caption>
              <thead>
                <tr className="text-on-surface-low">
                  <th scope="col" className="px-m py-s text-left">Tier</th>
                  <th scope="col" className="px-m py-s text-right">Samples</th>
                  <th scope="col" className="px-m py-s text-right">Agreement</th>
                  <th scope="col" className="px-m py-s text-right">Separation</th>
                  <th scope="col" className="px-m py-s text-right">Flip rate</th>
                  <th scope="col" className="px-m py-s text-right">Cost</th>
                  <th scope="col" className="px-m py-s text-right">Wall</th>
                  <th scope="col" className="px-m py-s text-left">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {bench.rows
                  .filter((r) => r.rubric_class === rubricClass)
                  .map((row) => <Row key={`${row.tier}-${row.samples}`} row={row} />)}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </section>
  )
}

function Heading({ benchId }: { benchId?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-s">
      <Gavel size={16} className="text-on-surface-var" />
      <span id="judge-bench-heading" data-type="title-m" className="text-on-surface">
        Judge tiers
      </span>
      {benchId && (
        <span className="text-on-surface-low text-[0.75rem]">{benchId}</span>
      )}
    </div>
  )
}

function Row({ row }: { row: JudgeBenchRow }) {
  const reasons = row.inadequate_reasons
  return (
    <tr className="border-t border-outline-variant/30 align-top">
      <td className="px-m py-s text-on-surface">{row.tier}</td>
      <td className="px-m py-s text-right text-on-surface-var">{row.samples}</td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtRate(row.agreement)}</td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtNum(row.separation)}</td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtRate(row.flip_rate)}</td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtCost(row.cost_usd)}</td>
      <td className="px-m py-s text-right text-on-surface-var">{row.wall_secs.toFixed(1)}s</td>
      <td className="px-m py-s">
        {row.adequate ? (
          <span className="text-on-surface" style={fvs(600)}>adequate</span>
        ) : (
          <div className="flex flex-col gap-1">
            <span
              className="inline-flex w-fit items-center gap-1.5 rounded-pill px-m h-6"
              style={{
                background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)',
                color: 'var(--color-warn)',
              }}
            >
              <ShieldAlert size={12} /> not adequate
            </span>
            {/* Every reason, not the first. A tier below the agreement floor AND missing a
                disqualifier has two different problems, and showing one sends the reader to
                fix the cheaper of them. */}
            <ul className="flex flex-col gap-0.5 text-on-surface-low">
              {reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          </div>
        )}
        {row.notes.length > 0 && (
          <ul className="mt-1 flex flex-col gap-0.5 text-on-surface-low">
            {row.notes.map((note) => <li key={note}>{note}</li>)}
          </ul>
        )}
      </td>
    </tr>
  )
}

/** The recommendation, and the two refusals.
 *
 *  §6's posture: the harness recommends, the human rebinds. So this names the use case and
 *  the exact `Provider:model` ref and points at Settings -> Models — it does not offer a
 *  button that rebinds a judge from a measurement page. */
function RecommendationCard({ rec }: { rec: JudgeBenchRecommendation }) {
  const recommended = rec.verdict === 'recommended'
  return (
    <div className="flex flex-col gap-xs rounded-lg bg-surface-container px-l py-m">
      <span data-type="title-s" className="text-on-surface">
        {rec.rubric_class}:{' '}
        {recommended
          ? `${rec.tier} at ${rec.samples} sample${rec.samples === 1 ? '' : 's'}`
          : rec.verdict === 'cost_unknown'
            ? 'cheapest tier unknown'
            : 'no adequate tier'}
      </span>
      {recommended && (
        <p className="text-on-surface-low text-[0.8125rem]">
          Bind <code className="text-on-surface-var">{rec.use_case}</code>
          {rec.model_ref ? <> to <code className="text-on-surface-var">{rec.model_ref}</code></> : null}{' '}
          on <a className="underline" href="#/settings/models">Settings → Models</a>. The harness
          recommends; you rebind.
        </p>
      )}
      <ul className="flex flex-col gap-0.5 text-on-surface-low text-[0.75rem]">
        {rec.notes.map((note) => <li key={note}>{note}</li>)}
      </ul>
    </div>
  )
}

function fmt(value: number | undefined): string {
  return value === undefined ? '—' : String(value)
}

/** A rate. `null` is UNMEASURED — the one value that must never render as 0.00. */
function fmtRate(value: number | null): string {
  return value === null ? 'not measured' : `${(value * 100).toFixed(0)}%`
}

function fmtNum(value: number | null): string {
  return value === null ? 'not measured' : value.toFixed(2)
}

/** Cost is `null` when nothing priced the calls. Rendering it as $0.00 would make an
 *  unpriced tier look like the cheapest one, which is the ranking the harness refuses to
 *  invent — so the UI must not invent it either. */
function fmtCost(value: number | null): string {
  return value === null ? 'unknown' : `$${value.toFixed(4)}`
}
