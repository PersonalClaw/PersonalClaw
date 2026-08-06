import { Trophy } from 'lucide-react'
import { api, type TelemetryRow } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Segmented } from '../../ui/Segmented'
import { Field, Select } from '../../ui/forms'
import { PanelHeader, Section } from './settingsUI'

/** Routing & Efficiency (MODEL-ROUTING-TELEMETRY, MRT-1e).
 *
 *  A read-only visualization of the per-model efficiency the router observes for
 *  each kind of request: success rate, feedback, latency (p50/p95) and cost per
 *  call, one row per model that has handled this (use_case, query_class) bucket.
 *  This surface ONLY visualizes — it never changes routing (that is a later
 *  capability); it shows which model is efficient for which work. A model is "on
 *  the frontier" when no other model beats it on all of quality, speed, and cost.
 *
 *  Data comes from GET /api/models/telemetry (api.modelsTelemetry); the bucket is
 *  chosen by two selectors whose state round-trips to the URL so a reload restores
 *  the view. Empty- and error-tolerant: an empty bucket shows a friendly note, a
 *  read failure shows an inline message — neither throws or blanks the table. */

// The routed use-cases the classifier assigns a query_class for (routing/classifier.py
// §2: use_case=code_tools → code, use_case=reasoning → long_reasoning). These mirror the
// Models panel's USE_CASE_META labels. 3 options → a Segmented.
const USE_CASES = [
  { key: 'chat', label: 'Chat' },
  { key: 'code_tools', label: 'Code & tools' },
  { key: 'reasoning', label: 'Reasoning' },
] as const

// The fixed query-class vocabulary (routing/classifier.py QUERY_CLASSES), in its
// stable order. 5 options (>4) → a Select from the ui/ form family, not a Segmented.
const QUERY_CLASSES = [
  { value: 'short_chat', label: 'Short chat' },
  { value: 'code', label: 'Code' },
  { value: 'summarize', label: 'Summarize' },
  { value: 'extract_structured', label: 'Extract structured' },
  { value: 'long_reasoning', label: 'Long reasoning' },
] as const

const DEFAULT_USE_CASE = USE_CASES[0].key
const DEFAULT_QUERY_CLASS = QUERY_CLASSES[0].value

/** A 0..1 fraction as a whole-percent string ("0.93" → "93%"). */
export function fmtPct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`
}

/** Feedback is optional signal: render it as a percent, or an em-dash when none
 *  has landed yet (0/absent) so a blank never reads as a real "0%". */
export function fmtFeedback(fraction: number): string {
  return fraction > 0 ? fmtPct(fraction) : '—'
}

/** A latency sample in ms — rounded and grouped, or an em-dash when there are no
 *  samples yet (the backend reports 0 for an un-sampled ref). */
export function fmtMs(ms: number): string {
  return ms > 0 ? Math.round(ms).toLocaleString() : '—'
}

/** Average cost per call. A local model reports 0 → "free" (honest, not "$0.00");
 *  otherwise 2dp for dollars, 4dp for sub-dollar so a fraction-of-a-cent shows. */
export function fmtCost(usd: number): string {
  if (usd <= 0) return 'free'
  return usd >= 1 ? `$${usd.toFixed(2)}` : `$${usd.toFixed(4)}`
}

/** Frontier rows first, otherwise stable (the backend already id-sorts). The
 *  Pareto frontier is the whole point of the view, so the un-dominated models sit
 *  at the top. Pure + exported for unit testing. */
export function sortByFrontier(rows: TelemetryRow[]): TelemetryRow[] {
  return [...rows].sort((a, b) => Number(b.on_frontier) - Number(a.on_frontier))
}

export function RoutingPanel({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [useCase, setUseCase] = useQueryParam(query, setQuery, 'uc', DEFAULT_USE_CASE, { replace: true })
  const [queryClass, setQueryClass] = useQueryParam(query, setQuery, 'qc', DEFAULT_QUERY_CLASS, { replace: true })

  // Keyed by both params so switching bucket revalidates against the right view;
  // persist:false (live telemetry, not slow config). A read failure resolves to
  // null (distinct from undefined=loading and an empty rows array=no telemetry).
  const { data } = useCachedData(
    `settings:routing-telemetry:${useCase}:${queryClass}`,
    () => api.modelsTelemetry({ use_case: useCase, query_class: queryClass })
      .then((d) => ({ rows: d.rows }))
      .catch(() => null),
    { persist: false },
  )

  const rows = data ? sortByFrontier(data.rows) : []
  const frontierCount = rows.filter((r) => r.on_frontier).length

  return (
    <div className="flex flex-col" style={{ minHeight: 0 }}>
      <PanelHeader title="Routing & Efficiency"
        hint="Real per-model efficiency for each kind of request — success rate, feedback, latency, and cost per call, measured as models handle work. Observation only: this does not change routing (that's a later capability); it shows which model is efficient for which work. A model is on the frontier when no other model beats it on all of quality, speed, and cost." />

      <div className="mb-l flex flex-wrap items-end gap-l">
        <Field label="Use case">
          <Segmented
            ariaLabel="Routing use case"
            options={USE_CASES.map((u) => ({ key: u.key, label: u.label }))}
            value={useCase}
            onChange={setUseCase}
          />
        </Field>
        <div className="min-w-[13rem]">
          <Field label="Request kind">
            <Select
              value={queryClass}
              onChange={setQueryClass}
              options={QUERY_CLASSES.map((q) => ({ value: q.value, label: q.label }))}
            />
          </Field>
        </div>
      </div>

      <Section>
        {data === null ? (
          <div className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var text-[0.8125rem]" role="status">
            Couldn't read routing telemetry right now. It's a read-only view — try switching the bucket or reloading.
          </div>
        ) : data === undefined ? (
          <div className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low text-[0.8125rem]">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low text-[0.8125rem]">
            No routing telemetry recorded for this yet — it fills in as models handle this kind of request.
          </div>
        ) : (
          <>
            <TelemetryTable rows={rows} />
            <p className="mt-m text-on-surface-low text-[0.75rem]">
              <Trophy size={11} className="mr-1 inline text-ok" aria-hidden />
              {frontierCount} of {rows.length} {rows.length === 1 ? 'model is' : 'models are'} on the frontier
              — not beaten by another on all of quality, speed, and cost.
            </p>
          </>
        )}
      </Section>
    </div>
  )
}

/** The per-model efficiency table. Frontier rows are marked with a labeled badge
 *  (not color alone) and floated to the top. Numbers are right-aligned and
 *  tabular; headers carry `scope="col"` for screen-reader column association. */
function TelemetryTable({ rows }: { rows: TelemetryRow[] }) {
  const th = 'border-b border-outline-variant/40 px-2 py-1.5 font-normal'
  const td = 'border-b border-outline-variant/25 px-2 py-1.5'
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[0.8125rem]">
        <thead>
          <tr className="text-on-surface-low">
            <th scope="col" className={`${th} text-left`}>Model</th>
            <th scope="col" className={`${th} text-right`}>Calls</th>
            <th scope="col" className={`${th} text-right`}>Success</th>
            <th scope="col" className={`${th} text-right`}>Feedback</th>
            <th scope="col" className={`${th} text-right`}>p50 ms</th>
            <th scope="col" className={`${th} text-right`}>p95 ms</th>
            <th scope="col" className={`${th} text-right`}>Cost/call</th>
            <th scope="col" className={`${th} text-right`}>Frontier</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.ref} className="text-on-surface-var">
              <td className={`${td} font-mono text-on-surface`}>{r.ref}</td>
              <td className={`${td} text-right tabular-nums`}>{r.n.toLocaleString()}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtPct(r.success)}</td>
              <td className={`${td} text-right tabular-nums text-on-surface-low`}>{fmtFeedback(r.feedback)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtMs(r.p50_ms)}</td>
              <td className={`${td} text-right tabular-nums text-on-surface-low`}>{fmtMs(r.p95_ms)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtCost(r.avg_cost_usd)}</td>
              <td className={`${td} text-right`}>
                {r.on_frontier ? (
                  <span className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5 text-[0.6875rem]"
                    style={{ background: 'color-mix(in srgb, var(--color-ok) 16%, transparent)', color: 'var(--color-ok)' }}
                    title="On the Pareto frontier — no other model beats this one on all of quality, speed, and cost.">
                    <Trophy size={9} aria-hidden /> frontier
                  </span>
                ) : (
                  <span className="text-on-surface-low" title="Dominated — another model beats this one on quality, speed, and cost.">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
