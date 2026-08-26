import { ArrowDown, ArrowUp, Trophy } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type RoutingPolicyRow, type RoutingProposal, type TelemetryRow } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Button } from '../../ui/Button'
import { Segmented } from '../../ui/Segmented'
import { Field, FieldError, Select } from '../../ui/forms'
import { unavailableWhen } from '../../ui/unavailable'
import { PanelHeader, Section } from './settingsUI'

/** Routing & Efficiency (MODEL-ROUTING-TELEMETRY, MRT-1e + MRT-4).
 *
 *  Two halves, and the distinction is the whole point of the page:
 *
 *  · The TELEMETRY table observes — per-model success rate, feedback, latency
 *    (p50/p95) and cost per call, one row per model that has handled this
 *    (use_case, query_class) bucket. A model is "on the frontier" when no other
 *    model beats it on all of quality, speed, and cost.
 *  · `RoutingPolicySection` below DECIDES — mode, pin and per-class order, each
 *    written through `api.setRoutingPolicy`.
 *
 *  Until MRT-4 this surface only visualized, and both this comment and the panel's
 *  own hint said so ("Observation only: this does not change routing — that's a
 *  later capability"). MRT-4 shipped that capability directly below the sentence
 *  denying it, so the page told a user its controls did nothing.
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

// 🔴 ONLY SOME OF THOSE AXES CAN EVER HAVE TELEMETRY, and the empty state used to promise all three
// would fill in. Traced: routing stats are folded in `ModelCallGuard._audit`, the guard is applied by
// `provider_bridge` only when `_guard_use_case` is set, and that happens for exactly
// `("reasoning", "background", "loops", "orchestration")`. Its own comment says why — "The interactive
// chat/code_tools stream stays OUT OF SCOPE … both human-watched".
//
// So on a fresh install a user lands on the DEFAULT tab (Chat), reads "it fills in as models handle
// this kind of request", and waits for data that cannot arrive. Two of the three tabs are structurally
// empty. The tabs are left as they are — mirroring the Models panel's axes is a deliberate choice, and
// removing two of them is the owner's call — but the copy now says which axes are measured.
const MEASURED_USE_CASES = ['reasoning', 'background', 'loops', 'orchestration'] as const

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
  const { data } = useQuery(
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
        hint="Real per-model efficiency for each kind of request — success rate, feedback, latency, and cost per call, measured as models handle work. A model is on the frontier when no other model beats it on all of quality, speed, and cost. Routing policy, below, turns that observation into a decision: which of your bound models this use case tries first." />

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

      {/* 🔴 Titled: the measured table was the unnamed group while "Routing policy" below it had a
          heading, so the outline read h1 → "Routing policy" and skipped the observation the policy
          is derived FROM. The title holds across all four branches (error / loading / empty / table)
          — a group that names itself only when its data arrives disappears exactly when the user is
          trying to work out what went wrong. */}
      <Section title="Model efficiency">
        {data === null ? (
          <div className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var text-[0.8125rem]" role="status">
            Couldn't read routing telemetry right now. It's a read-only view — try switching the bucket or reloading.
          </div>
        ) : data === undefined ? (
          <div className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low text-[0.8125rem]">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low text-[0.8125rem]">
            {(MEASURED_USE_CASES as readonly string[]).includes(useCase)
              ? 'No routing telemetry recorded for this yet — it fills in as models handle this kind of request.'
              : 'Nothing is measured for this axis. Routing telemetry comes from unattended work — reasoning, background, loops and orchestration — because interactive requests deliberately stay outside the model-call guard.'}
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

      <RoutingProposalsSection />

      <RoutingPolicySection useCase={useCase} queryClass={queryClass} />
    </div>
  )
}

/** Proposed routing changes (MODEL-ROUTING-TELEMETRY §6.3, MRT-5) — propose-don't-write.
 *
 *  The measured table above can show that one of your bound models clearly beats another
 *  for a kind of request. It must never act on that alone: `routing_policy.json` is YOUR
 *  table, and a telemetry fold quietly rewriting it would mean the machine changed which
 *  provider sees your content without anyone deciding to. So a measured gap lands here,
 *  with the evidence that justified it, and waits.
 *
 *  Deliberately NOT scoped to the two selectors above: a proposal is a decision waiting on
 *  the user, and hiding one because they happened to be looking at another bucket would
 *  make the queue unfindable. Each row names its own use case and request kind instead.
 *
 *  The section renders even when the queue is empty — one quiet line that says the machine
 *  proposes rather than rewrites. That sentence is the product property; a section that
 *  appeared only once there was something to accept would never teach it. */
function RoutingProposalsSection() {
  const [props_, setProps] = useState<RoutingProposal[] | null | undefined>(undefined)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [said, setSaid] = useState('')

  const load = useCallback(() => {
    api.routingProposals()
      .then((d) => setProps(d.proposals))
      .catch(() => setProps(null))
  }, [])
  useEffect(load, [load])

  // Accept can legitimately answer "not applied": the cell's order was set by hand, and a
  // user decision is never overwritten. That is not an error, so it lands in the polite
  // status line with the server's own reason — the backend owns that wording.
  const decide = async (p: RoutingProposal, accept: boolean) => {
    setBusy(p.id)
    setNote('')
    try {
      if (accept) {
        const r = await api.acceptRoutingProposal(p.id)
        setSaid(r.applied
          ? `Applied: ${p.use_case} / ${p.query_class} now tries ${p.proposed[0]} first.`
          : `Not applied — ${r.reason ?? 'this order was set by hand.'}`)
      } else {
        await api.rejectRoutingProposal(p.id)
        setSaid(`Dismissed. This suggestion won't come back for a while.`)
      }
      load()
    } catch {
      setNote("Couldn't record that — nothing changed.")
    } finally {
      setBusy('')
    }
  }

  return (
    <Section title="Proposed routing changes">
      {props_ === null ? (
        <div className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var text-[0.8125rem]" role="status">
          Couldn't read the proposal queue right now. Nothing is pending action — your routing
          table is unchanged either way.
        </div>
      ) : props_ === undefined ? (
        <div className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low text-[0.8125rem]">Loading…</div>
      ) : props_.length === 0 ? (
        <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low text-[0.8125rem]">
          Nothing proposed. When measurements show one of your models clearly beating another for a
          request kind, the change is proposed here — routing never rewrites your table on its own.
        </div>
      ) : (
        <>
        {/* §6.3's count, in a sentence rather than a bare badge: the number is only meaningful
            beside what it means, and "measured, not applied" is the property the queue exists to
            enforce. */}
        <p className="mb-m text-on-surface-var text-[0.8125rem]">
          {props_.length} proposed {props_.length === 1 ? 'change' : 'changes'} waiting on you.
          Routing measured these — it has not applied them.
        </p>
        <ul className="flex flex-col gap-2">
          {props_.map((p) => (
            <li key={p.id} className="rounded-lg bg-surface-container px-3 py-2.5">
              <p className="text-on-surface text-[0.8125rem]">
                For <span className="text-on-surface-var">{p.use_case} / {p.query_class}</span>, try{' '}
                <span className="font-mono">{p.proposed[0]}</span> before{' '}
                <span className="font-mono">{p.current[0]}</span>.
              </p>
              <ProposalEvidence evidence={p.evidence} promoted={p.proposed[0]} demoted={p.current[0]} />
              <div className="mt-s flex items-center gap-s">
                <Button size="xs" variant="primary" loading={busy === p.id}
                  onClick={() => void decide(p, true)}
                  ariaLabel={`Apply: try ${p.proposed[0]} first for ${p.use_case} ${p.query_class}`}>
                  Apply
                </Button>
                <Button size="xs" variant="ghost" disabled={busy === p.id}
                  onClick={() => void decide(p, false)}
                  ariaLabel={`Dismiss the proposal for ${p.use_case} ${p.query_class}`}>
                  Dismiss
                </Button>
              </div>
            </li>
          ))}
        </ul>
        </>
      )}
      {/* Requested outcome → polite status. ALWAYS MOUNTED and empty at rest: a live region created
          at the moment its text appears is not reliably announced. Deliberately NOT `sr-only` — the
          row it describes is gone after the reload, so this line is the ONLY confirmation any user
          gets, sighted or not. (It is also why the resting class is bare rather than `sr-only`: the
          policy section below owns the page's one visually-hidden status region, and a second one
          would shadow it for any reader that picks the first.) */}
      <p role="status" aria-live="polite"
        className={said ? 'mt-m text-on-surface-var text-[0.8125rem]' : ''}>{said}</p>
      {note && <FieldError className="mt-s">{note}</FieldError>}
    </Section>
  )
}

/** The evidence behind one proposal, in the same units the table above uses.
 *
 *  Only what is present is rendered — a proposal built with no `model_calls.jsonl` tail has
 *  no p50 to show, and an em-dash for a number that was never measured would read as zero.
 *  Deltas are promoted-minus-demoted, so a negative is an improvement; they are phrased as
 *  "faster"/"cheaper" rather than signed numbers because a bare "-780ms" needs a legend. */
function ProposalEvidence({ evidence, promoted, demoted }: {
  evidence: RoutingProposal['evidence']
  promoted: string
  demoted: string
}) {
  const scores = evidence.scores ?? {}
  const counts = evidence.n ?? {}
  const p50 = evidence.p50_delta_ms
  const cost = evidence.cost_delta_usd
  const bits: string[] = []
  if (scores[promoted] !== undefined && scores[demoted] !== undefined) {
    bits.push(`scored ${fmtPct(scores[promoted])} vs ${fmtPct(scores[demoted])}`)
  }
  if (counts[promoted] !== undefined && counts[demoted] !== undefined) {
    bits.push(`over ${counts[promoted]} and ${counts[demoted]} calls`)
  }
  if (p50 !== undefined && p50 !== 0) {
    bits.push(`${fmtMs(Math.abs(p50))}ms ${p50 < 0 ? 'faster' : 'slower'}`)
  }
  if (cost !== undefined && cost !== 0) {
    bits.push(`${fmtCost(Math.abs(cost))} ${cost < 0 ? 'cheaper' : 'dearer'} per call`)
  }
  if (bits.length === 0) return null
  return <p className="mt-1 text-on-surface-low text-[0.75rem]">{bits.join(' · ')}.</p>
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
  // What the last successful reorder did, for the live region below. Empty until one happens.
  const [moved, setMoved] = useState('')

  const load = useCallback(() => {
    api.routingPolicy()
      .then((d) => { setRows(d.use_cases); setEnabled(d.enabled) })
      .catch(() => setRows(null))
  }, [])
  useEffect(load, [load])

  const row = rows?.find((r) => r.use_case === useCase)

  // One write per interaction, then reload — the server is the authority on what the
  // table now says (a local guess could disagree with a floored/rejected value).
  // Returns whether the write actually landed. It swallows the error to render `note` instead of
  // rejecting, so a caller cannot infer success from the promise settling — `move` below needs to
  // know, because announcing a reorder that failed would be worse than announcing nothing.
  const save = async (body: Parameters<typeof api.setRoutingPolicy>[0]): Promise<boolean> => {
    setBusy(true)
    setNote('')
    try {
      await api.setRoutingPolicy(body)
      load()
      return true
    } catch {
      setNote("Couldn't save that — nothing changed.")
      return false
    } finally {
      setBusy(false)
    }
  }

  if (rows === null) {
    // 🔴 The SAME title as the success branch below. This early return dropped it, so the section
    //    lost its own heading precisely when it had bad news to deliver — the reader gets an
    //    unattributed "Couldn't read…" with no way to tell which part of the page failed.
    return (
      <Section title="Routing policy">
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

  // A reorder is a status message (WCAG 4.1.3): the only feedback is that the row visually
  // swapped, and the ranking numbers beside each row are not in any focused control's
  // accessible name — so a user who cannot see the list gets nothing back from pressing the
  // button. Announce the ref AND its new position, because "moved earlier" alone does not say
  // where it landed or when the end of the list has been reached.
  const move = (index: number, delta: number) => {
    const next = [...shown]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    void save({ use_case: useCase, query_class: queryClass, order: next })
      .then((ok) => { if (ok) setMoved(`${next[target]} moved to position ${target + 1} of ${next.length}`) })
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
                    {/* `size-7` (28px), not `p-1` (21px): an icon-only control needs 24px of target.
                        These deliberately keep `unavailableWhen` rather than adopting
                        `SquareIconButton` — the primitive maps `disabled` to `aria-disabled` and never
                        to the native attribute, but `unavailableWhen` goes NATIVELY disabled while
                        `busy` on purpose, so an in-flight save cannot be fired twice. Borrowing the
                        primitive's geometry keeps that semantic while matching how every other
                        icon-button in the app measures. */}
                    <button type="button"
                      {...unavailableWhen(i === 0, 'Already tried first', { busy })}
                      onClick={() => move(i, -1)}
                      className="grid size-7 place-items-center rounded-md text-on-surface-var hover:bg-surface-high aria-disabled:opacity-40 disabled:opacity-40"
                      aria-label={`Move ${ref} earlier`}>
                      <ArrowUp size={13} aria-hidden />
                    </button>
                    <button type="button"
                      {...unavailableWhen(i === shown.length - 1, 'Already tried last', { busy })}
                      onClick={() => move(i, 1)}
                      className="grid size-7 place-items-center rounded-md text-on-surface-var hover:bg-surface-high aria-disabled:opacity-40 disabled:opacity-40"
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
          {/* A successful reorder is a status message, not an alert: it was requested, so it must
              not interrupt. ALWAYS MOUNTED and empty at rest — a live region created at the same
              moment its text appears is not reliably observed (the reasoning `ResultAnnouncement`
              records). Visually hidden because the list already shows the new order and its
              position numbers; this is the same fact for a user who cannot see them. */}
          <p role="status" aria-live="polite" className="sr-only">{moved}</p>
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
