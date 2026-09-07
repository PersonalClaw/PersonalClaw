import { useEffect, useState } from 'react'
import { Gavel, ListTree, Scale } from 'lucide-react'
import { Segmented } from '../../ui/Segmented'
import { Skeleton } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { api, type WorkflowLedgerRails } from '../../lib/api'
import { fmtElapsed } from './workflowMeta'

/** The run-side ledger rails (PP-16 seam 4): the findings rail and the verdict/ROI rail.
 *
 *  The loop cockpit has had both for a long time — `LoopCockpitPage`'s cycle nodes over
 *  `get_findings` and its `RoiRail` bar chart over `get_verdicts` — and a run detail had neither,
 *  even though the run engine writes the same PP-5 ledger kinds. This panel is the consumer of the
 *  projection that closes it. Nothing here computes: the arithmetic is
 *  `workflows/introspection.py`'s and the read is one GET.
 *
 *  **ABSENT IS NOT ZERO, and this panel is where that becomes visible.** Every measured cell
 *  arrives as `number | null`, and `null` means the ledger row did not carry the key. A
 *  loop-shaped step carries no cost or token keys at all (loop money lives in
 *  `usage/turns.jsonl`), so a `0` in the cost column would tell a user their work was free. Every
 *  such cell renders as an em dash, and `EM_DASH` is exported so the test asserts the rendering
 *  rather than trusting it.
 *
 *  **A kind with no producer says so.** The coverage strip reports `breaker_trip` as having no
 *  run-side writer instead of showing it as zero trips: "the breaker never tripped" and "nothing
 *  here can trip a breaker" are different claims, and only the first is an observation.
 *
 *  **A missing ROI axis is named, not implied.** The loop rail plots `marginal_value`; a run-side
 *  verdict row does not carry it, because the engine ledgers the verdict word and its evidence and
 *  keeps the rich `JudgeVerdict` in the node output. So the ROI chart plots the judge's `overall`
 *  score and states which axis is absent, rather than plotting zeros that would read as a run that
 *  earned no marginal value. */

/** What an absent cell renders as. Exported so the rail asserts the em dash and not a `0`. */
export const EM_DASH = '—'

/** A measured cell: the formatted value, or an em dash when the ledger carried nothing. */
function cell(value: number | null | undefined, fmt: (n: number) => string): string {
  return typeof value === 'number' ? fmt(value) : EM_DASH
}

/** A text cell. An empty string is a carried-but-blank value, which is still not an absence — but
 *  it has nothing to show, so it reads the same. `null` is the absence. */
function text(value: string | null | undefined): string {
  return value ? value : EM_DASH
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col rounded-lg bg-surface-high p-s">
      <dt data-type="caption" className="text-on-surface-low">{label}</dt>
      <dd className="text-on-surface tabular-nums">{value}</dd>
    </div>
  )
}

/** The ROI series as proportional bars. Deliberately not a zero-baseline chart with invented
 *  gridlines: the series is the judge's own aggregated score per verdict, and the only honest
 *  framing is relative to the largest one actually recorded. */
function RoiBars({ series }: { series: number[] }) {
  const peak = Math.max(...series)
  return (
    <div className="flex h-16 items-end gap-2xs" role="img" aria-label={`Judge scores across ${series.length} verdicts`}>
      {series.map((score, i) => (
        <div
          key={i}
          className="min-w-[6px] flex-1 rounded-t-sm bg-primary"
          style={{ height: `${peak > 0 ? Math.max(4, (score / peak) * 100) : 4}%` }}
          title={`Verdict ${i + 1}: ${score}`}
        />
      ))}
    </div>
  )
}

export function LedgerRailsPanel({ runId }: { runId: string }) {
  const [data, setData] = useState<WorkflowLedgerRails | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'findings' | 'verdicts'>('findings')

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    api
      .workflowRunLedgerRails(runId)
      .then((body) => { if (live) setData(body) })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : 'could not read this run’s ledger') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  if (loading) return <Skeleton />
  if (error) return <InlineError>{error}</InlineError>
  if (!data) return <p data-type="body-s" className="text-on-surface-low">This run has no ledger to project.</p>

  const { findings, verdicts, totals, coverage } = data
  const absent = coverage.filter((c) => c.producer === 'none')

  return (
    <div className="flex flex-col gap-l">
      {/* The rail totals. Each measured cell can be absent, and the em dash is the honest render:
          this run's ledger carried no such key, which is not the same as a zero. */}
      <section className="flex flex-col gap-xs">
        <h3 data-type="label-s" className="flex items-center gap-2xs text-on-surface fw-500">
          <Scale size={13} aria-hidden /> Rail totals
        </h3>
        <dl data-type="caption" className="grid grid-cols-2 gap-xs sm:grid-cols-3">
          <Stat label="Steps completed" value={String(totals.steps_completed)} />
          <Stat label="Verdicts" value={String(totals.verdicts)} />
          <Stat label="Cost (est.)" value={cell(totals.cost_usd, (n) => `~$${n.toFixed(4)}`)} />
          <Stat label="Tokens" value={cell(totals.tokens, (n) => n.toLocaleString())} />
          <Stat label="Step time" value={cell(totals.duration_secs, fmtElapsed)} />
          <Stat
            label="Judge outcomes"
            value={Object.entries(totals.verdicts_by_word).map(([w, n]) => `${w} ${n}`).join(' · ') || EM_DASH}
          />
        </dl>
      </section>

      {/* Which rail kinds have a writer on this side. A kind with none is REPORTED, because a
          surface that silently omits it lets a reader conclude the event never happened. */}
      {absent.length > 0 && (
        <p data-type="caption" className="text-on-surface-low">
          No run-side producer for {absent.map((c) => c.kind).join(', ')} — these rails show{' '}
          {EM_DASH} rather than zero, because nothing here writes that event.
        </p>
      )}

      <Segmented
        ariaLabel="Ledger rail"
        value={tab}
        onChange={(v) => setTab(v as 'findings' | 'verdicts')}
        options={[
          { key: 'findings', label: `Findings (${findings.length})` },
          { key: 'verdicts', label: `Verdict / ROI (${verdicts.length})` },
        ]}
      />

      {tab === 'findings' && (
        <section className="flex flex-col gap-xs">
          <h3 data-type="label-s" className="flex items-center gap-2xs text-on-surface fw-500">
            <ListTree size={13} aria-hidden /> Findings rail
          </h3>
          {findings.length === 0 ? (
            // An empty rail is an answer: this run has completed no step yet. Saying so beats
            // blank space, which reads as a broken panel on a run that is merely young.
            <p data-type="caption" className="text-on-surface-low">
              No step has completed yet — the rail fills as the run works.
            </p>
          ) : (
            <ul className="flex flex-col gap-xs">
              {findings.map((f, i) => (
                <li key={`${f.node_id ?? ''}-${f.epoch ?? i}-${i}`} className="flex flex-col gap-2xs rounded-lg bg-surface-high p-s">
                  <div className="flex items-baseline justify-between gap-s">
                    <span data-type="label-s" className="text-on-surface">
                      {text(f.node_id)}
                      {f.cycle !== null && <span className="text-on-surface-low"> · cycle {f.cycle}</span>}
                    </span>
                    <span data-type="caption" className="text-on-surface-low tabular-nums">{text(f.state)}</span>
                  </div>
                  <dl data-type="caption" className="grid grid-cols-2 gap-2xs text-on-surface-low sm:grid-cols-4">
                    <div><dt className="inline">Cost </dt><dd className="inline tabular-nums">{cell(f.cost_usd, (n) => `~$${n.toFixed(4)}`)}</dd></div>
                    <div><dt className="inline">Tokens </dt><dd className="inline tabular-nums">{cell(f.tokens, (n) => n.toLocaleString())}</dd></div>
                    <div><dt className="inline">Took </dt><dd className="inline tabular-nums">{cell(f.duration_secs, fmtElapsed)}</dd></div>
                    <div><dt className="inline">Retries </dt><dd className="inline tabular-nums">{cell(f.retries, String)}</dd></div>
                    <div className="col-span-2"><dt className="inline">Model </dt><dd className="inline">{text(f.model)}</dd></div>
                    <div className="col-span-2"><dt className="inline">Produced </dt><dd className="inline break-all">{text(f.output_ref)}</dd></div>
                  </dl>
                  {f.degraded_reason && (
                    <p data-type="caption" className="text-warning">Degraded: {f.degraded_reason}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {tab === 'verdicts' && (
        <section className="flex flex-col gap-xs">
          <h3 data-type="label-s" className="flex items-center gap-2xs text-on-surface fw-500">
            <Gavel size={13} aria-hidden /> Verdict / ROI rail
          </h3>
          {/* The absent axis, named. Without this the chart below would look like the whole ROI
              story, and a reader would take a missing marginal value for a zero one. */}
          <p data-type="caption" className="text-on-surface-low">
            Plotted from the judge’s aggregated score. This run’s ledger carries no{' '}
            {totals.absent_scores.join(' or ')} — the loop rail’s axes live in a node’s output, not
            on the ledger row, so they read as {EM_DASH} here rather than as zero.
          </p>
          {totals.overall_series === null ? (
            <p data-type="caption" className="text-on-surface-low">
              {verdicts.length === 0
                ? 'No judge has run on this run yet.'
                : 'A judge ran, but no verdict carried a score to plot.'}
            </p>
          ) : (
            <RoiBars series={totals.overall_series} />
          )}
          {verdicts.length > 0 && (
            <ul className="flex flex-col gap-xs">
              {verdicts.map((v, i) => (
                <li key={`${v.node_id ?? ''}-${v.epoch ?? i}-${i}`} className="flex flex-col gap-2xs rounded-lg bg-surface-high p-s">
                  <div className="flex items-baseline justify-between gap-s">
                    <span data-type="label-s" className="text-on-surface">{text(v.node_id)}</span>
                    <span data-type="caption" className="text-on-surface-low">{text(v.verdict)}</span>
                  </div>
                  <dl data-type="caption" className="grid grid-cols-2 gap-2xs text-on-surface-low sm:grid-cols-4">
                    <div><dt className="inline">Score </dt><dd className="inline tabular-nums">{cell(v.overall, (n) => n.toFixed(2))}</dd></div>
                    <div><dt className="inline">Samples </dt><dd className="inline tabular-nums">{cell(v.sample_count, String)}</dd></div>
                    <div><dt className="inline">Marginal </dt><dd className="inline tabular-nums">{cell(v.marginal_value, (n) => n.toFixed(2))}</dd></div>
                    <div><dt className="inline">Quality </dt><dd className="inline tabular-nums">{cell(v.quality_score, (n) => n.toFixed(2))}</dd></div>
                  </dl>
                  {v.shortfalls && v.shortfalls.length > 0 && (
                    <p data-type="caption" className="text-on-surface-low">
                      Shortfalls: {v.shortfalls.join('; ')}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}
