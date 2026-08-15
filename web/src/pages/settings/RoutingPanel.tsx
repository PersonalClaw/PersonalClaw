import { ArrowDown, ArrowUp, Trophy } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type RoutingPolicyRow, type TelemetryRow } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Segmented } from '../../ui/Segmented'
import { Field, FieldError, Select } from '../../ui/forms'
import { unavailableWhen } from '../../ui/unavailable'
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

      <RoutingPolicySection useCase={useCase} queryClass={queryClass} />
    </div>
  )
}

/** The routing POLICY table (MODEL-ROUTING-TELEMETRY §6.1-6.2, MRT-4).
 *
 *  The table above says which model is *efficient*; this one says which model routing
 *  actually tries FIRST, and lets the user overrule it. Three levers, in descending
 *  authority — a pin beats the policy, and a manual order beats the heuristic:
 *
 *    • mode  — off (resolve in the order you bound) | heuristic (prefer local) | learned
 *    • pin   — always local / always cloud / one exact model; skips ordering entirely
 *    • order — drag-free reorder buttons that record YOUR order for this request kind
 *
 *  The order is a RANKING, not a filter: a model missing from it is tried last, never
 *  dropped, which is why reordering can't accidentally unbind a provider. Every recorded
 *  order shows the basis that decided it, so the table always explains itself. */
function RoutingPolicySection({ useCase, queryClass }: { useCase: string; queryClass: string }) {
  const [rows, setRows] = useState<RoutingPolicyRow[] | null | undefined>(undefined)
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const load = useCallback(() => {
    api.routingPolicy()
      .then((d) => { setRows(d.use_cases); setEnabled(d.enabled) })
      .catch(() => setRows(null))
  }, [])
  useEffect(load, [load])

  const row = rows?.find((r) => r.use_case === useCase)

  // One write per interaction, then reload — the server is the authority on what the
  // table now says (a local guess could disagree with a floored/rejected value).
  const save = async (body: Parameters<typeof api.setRoutingPolicy>[0]) => {
    setBusy(true)
    setNote('')
    try {
      await api.setRoutingPolicy(body)
      load()
    } catch {
      setNote("Couldn't save that — nothing changed.")
    } finally {
      setBusy(false)
    }
  }

  if (rows === null) {
    return (
      <Section>
        <div className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var text-[0.8125rem]" role="status">
          Couldn't read the routing policy right now. Your bound models are unaffected — resolution
          falls back to the order you bound them in.
        </div>
      </Section>
    )
  }

  const recorded = row?.classes?.[queryClass]
  const order = recorded?.order ?? []
  const candidates = row?.candidates ?? []
  // The effective order shown: the recorded ranking first, then any newly-bound model.
  const shown = [
    ...order.filter((ref) => candidates.some((c) => c.ref === ref)),
    ...candidates.map((c) => c.ref).filter((ref) => !order.includes(ref)),
  ]

  const move = (index: number, delta: number) => {
    const next = [...shown]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    void save({ use_case: useCase, query_class: queryClass, order: next })
  }

  return (
    <Section title="Routing policy">
      <p className="mb-m text-on-surface-var text-[0.8125rem]">
        Which of your bound models this use case tries first. Routing only reorders the models you
        already bound — it never adds or removes one, and an unavailable model still reports an
        error rather than being quietly swapped.
        {!enabled && ' Routing is currently off globally, so this order is not applied yet.'}
      </p>

      {!row ? (
        <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low text-[0.8125rem]">
          Routing doesn't apply to this use case — it runs on background work (reasoning, loops,
          orchestration), not on interactive chat.
        </div>
      ) : (
        <>
          <div className="mb-l flex flex-wrap items-end gap-l">
            <div className="min-w-[13rem]">
              <Field label="Mode" hint="How the first model gets chosen.">
                <Select
                  value={row.mode}
                  disabled={busy}
                  onChange={(v) => void save({ use_case: useCase, mode: v as RoutingPolicyRow['mode'] })}
                  options={[
                    { value: 'off', label: 'Off — use my order' },
                    { value: 'heuristic', label: 'Prefer local' },
                    { value: 'learned', label: 'Learn from results' },
                  ]}
                />
              </Field>
            </div>
            <div className="min-w-[15rem]">
              <Field label="Pin" hint="Overrules the mode for this use case.">
                <Select
                  value={row.pin}
                  disabled={busy}
                  onChange={(v) => void save({ use_case: useCase, pin: v })}
                  options={[
                    { value: '', label: 'No pin' },
                    { value: 'local', label: 'Always local' },
                    { value: 'cloud', label: 'Always cloud' },
                    ...candidates.map((c) => ({ value: c.ref, label: `Always ${c.ref}` })),
                  ]}
                />
              </Field>
            </div>
          </div>

          {shown.length === 0 ? (
            <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low text-[0.8125rem]">
              No models bound to this use case yet. Bind two — one local, one cloud — to give routing
              a choice to make.
            </div>
          ) : (
            <ol className="flex flex-col gap-1.5">
              {shown.map((ref, i) => {
                const local = candidates.find((c) => c.ref === ref)?.local
                return (
                  <li key={ref} className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2 text-[0.8125rem]">
                    <span className="w-5 text-right tabular-nums text-on-surface-low">{i + 1}</span>
                    <span className="flex-1 truncate font-mono text-on-surface" title={ref}>{ref}</span>
                    <span className="text-on-surface-low text-[0.75rem]">{local ? 'local' : 'cloud'}</span>
                    <button type="button"
                      {...unavailableWhen(i === 0, 'Already tried first', { busy })}
                      onClick={() => move(i, -1)}
                      className="rounded-md p-1 text-on-surface-var hover:bg-surface-high aria-disabled:opacity-40 disabled:opacity-40"
                      aria-label={`Move ${ref} earlier`}>
                      <ArrowUp size={13} aria-hidden />
                    </button>
                    <button type="button"
                      {...unavailableWhen(i === shown.length - 1, 'Already tried last', { busy })}
                      onClick={() => move(i, 1)}
                      className="rounded-md p-1 text-on-surface-var hover:bg-surface-high aria-disabled:opacity-40 disabled:opacity-40"
                      aria-label={`Move ${ref} later`}>
                      <ArrowDown size={13} aria-hidden />
                    </button>
                  </li>
                )
              })}
            </ol>
          )}

          <p className="mt-m text-on-surface-low text-[0.75rem]">
            {row.pin
              ? `Pinned to ${row.pin} — the order below is recorded but not applied while the pin is set.`
              : recorded
                ? `Order recorded for ${queryClass} · decided by ${String(recorded.basis?.source ?? 'unknown')}.`
                : `No order recorded for ${queryClass} yet — ${row.mode === 'off' ? 'your bound order applies' : 'the prefer-local rule applies'}.`}
          </p>
          {/* A save that just failed is unrequested bad news, so it INTERRUPTS (FieldError
              carries role="alert"); the recorded-order line above it is normal status text. */}
          {note && <FieldError className="mt-s">{note}</FieldError>}
        </>
      )}
    </Section>
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
