import { ArrowDown, ArrowUp, Trophy } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type RoutingPolicyRow, type RoutingProposal, type TelemetryRow } from '../../lib/api'
import { readableErrText } from '../../lib/errText'
import { notify } from '../../app/appSdk'
import { useQuery } from '../../lib/data'
import type { Rebase, Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Button } from '../../ui/Button'
import { StatusPill } from '../../ui/StatusPill'
import { Segmented } from '../../ui/Segmented'
import { Field, FieldError, Select } from '../../ui/forms'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { unavailableWhen } from '../../ui/unavailable'
import { PanelHeader, Section, RowGroup, ToggleRow, NumberRow } from './settingsUI'
import { HELD_CHANGE_REASON } from '../../lib/staleWrite'

/** Routing & Efficiency.
 *
 *  Two halves, and the distinction is the whole point of the page:
 *
 *  · The TELEMETRY table observes — per-model success rate, feedback, latency
 *    (p50/p95) and cost per call, one row per model that has handled this
 *    (use_case, query_class) bucket. A model is "on the frontier" when no other
 *    model beats it on all of quality, speed, and cost.
 *  · `RoutingPolicySection` below DECIDES — mode and pin written through
 *    `api.setRoutingPolicy`, and each class's order through `api.setRoutingOrder`.
 *
 *  This surface used to only visualize, and both this comment and the panel's
 *  own hint said so ("Observation only: this does not change routing — that's a
 *  later capability"). That capability then shipped directly below the sentence
 *  denying it, so the page told a user its controls did nothing.
 *
 *  Data comes from GET /api/models/telemetry (api.modelsTelemetry); the bucket is
 *  chosen by two selectors whose state round-trips to the URL so a reload restores
 *  the view. Empty- and error-tolerant: an empty bucket shows a friendly note, a
 *  read failure shows an inline message — neither throws or blanks the table. */

// The routed use-cases the classifier assigns a query_class for (routing/classifier.py
// use_case=code_tools → code, use_case=reasoning → long_reasoning). These mirror the
// Models panel's USE_CASE_META labels. 3 options → a Segmented.
const USE_CASES = [
  { key: 'chat', label: 'Chat' },
  { key: 'code_tools', label: 'Code & tools' },
  { key: 'reasoning', label: 'Reasoning' },
] as const

// 🔴 NOT EVERY CALL ON THOSE AXES HAS TELEMETRY, and the empty state used to promise all three would
// fill in. Routing stats are folded in `ModelCallGuard._audit`, and `provider_bridge` puts the guard on a
// call when `_guard_use_case` is set: for EVERY call on an axis in its `METERED_AXES`, and for a call
// automation makes on any other axis (`resolve_metered_model`: a loop's judge or gate, a knowledge step,
// a one-shot completion). The turns a person makes on Chat or Code & tools stay unguarded, "both
// human-watched" in the bridge's own words.
//
// So Reasoning fills in as models handle it, while Chat (the DEFAULT tab) and Code & tools fill in only
// from the calls automation makes there: a user who only chats sees nothing on them, and the copy says
// why rather than promising data their own turns never produce. The tabs are left as they are
// (mirroring the Models panel's axes is a deliberate choice; removing two is a separate design call).
// `routingTelemetryPromise.test.tsx` reads this list against the bridge's own.
const METERED_AXES = ['reasoning', 'background', 'loops', 'orchestration'] as const

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

/** Average cost per call. A model nothing has priced reads "unpriced": its 0 is no price, and
 *  "free" said a cloud model with no rate cost nothing. A priced model at 0 (one running on this
 *  machine) reads "free" (honest, not "$0.00"); otherwise 2dp for dollars, 4dp for sub-dollar so a
 *  fraction-of-a-cent shows. */
export function fmtCost(usd: number, priced: boolean): string {
  if (!priced) return 'unpriced'
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

      <RouterConfigSection />

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
          <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var" role="status">
            Couldn't read routing telemetry right now. It's a read-only view — try switching the bucket or reloading.
          </div>
        ) : data === undefined ? (
          <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low">Loading…</div>
        ) : rows.length === 0 ? (
          <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
            {(METERED_AXES as readonly string[]).includes(useCase)
              ? 'No routing telemetry recorded for this yet — it fills in as models handle this kind of request.'
              : 'No routing telemetry recorded for this yet. On this axis only the calls automation makes are measured, such as a loop’s judge or a knowledge step, so it fills in as they run. The turns you type here stay outside the model-call guard.'}
          </div>
        ) : (
          <>
            <TelemetryTable rows={rows} />
            <p data-type="caption" className="mt-m text-on-surface-low">
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

/** The `routing.*` config section — the adaptive router's master switch and its tuning numbers.
 *
 *  🔑 THIS IS A DIFFERENT STORE FROM EVERYTHING ELSE ON THE PAGE, and that is why the section had
 *  to exist. `RoutingPolicySection` below writes `routing_policy.json` through
 *  `api.setRoutingPolicy` (mode, pin) and `api.setRoutingOrder` (per-class order); these six
 *  write `config.json`'s `routing.*` through the PATCH allowlist. All six were allowlisted and read by `routing/policy.py` with NO
 *  control anywhere in `web/` — so a panel that looked like it configured routing could not reach
 *  the master switch that turns routing on.
 *
 *  🔴 `routing.energy_sampling` IS DELIBERATELY ABSENT. It is allowlisted and has `_meta` help
 *  promising "record a rough energy estimate for local calls", but it has ZERO readers outside the
 *  plumbing — the same inert-path defect issue #465 catalogues. An inert knob needs its reader
 *  wired or its allowlist row dropped; a control would only make a promise the code ignores more
 *  convincing. */
function RouterConfigSection() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  const { data, error: loadErr, refresh } = useQuery('settings:routing-config', () =>
    api.personalclawConfig().then((c) => (c.routing ?? {}) as Record<string, unknown>),
    { persist: true },
  )
  useEffect(() => { if (data) setCfg(data) }, [data])

  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = (cfg ?? {})[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`routing.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <Section title="Router" hint="Whether PersonalClaw may reorder your bound models at all, and how much evidence it needs before it does.">
      {!data && loadErr
        ? <LoadError what="routing settings" error={loadErr} onRetry={refresh} />
        : !cfg
          ? <FormSkeleton sections={1} what="routing settings" />
          : (
            <RowGroup>
              <ToggleRow label="Adaptive routing" cfg={cfg} field="enabled" patch={patch}
                hint="Master switch. Off means every use case resolves in the exact order you bound its models. On lets PersonalClaw prefer a local model for work it handles well and fall back to a cloud model when it can't." />
              <NumberRow label="Local attempt timeout (seconds)" cfg={cfg} field="local_timeout_secs" min={0} max={600} step={1} patch={patch}
                hint="How long a local model gets before the call falls back to the next model you bound. Keeps a slow local model from stalling background work." />
              <NumberRow label="Minimum samples" cfg={cfg} field="min_samples" min={1} max={10000} patch={patch}
                hint="How many recorded calls a model needs for a kind of request before its measured score may influence order. Below this, the simple local-first rule stands." />
              <NumberRow label="Hysteresis margin" cfg={cfg} field="hysteresis" min={0} max={1} step={0.01} patch={patch}
                hint="How much better a model's score must be before the order actually changes. Prevents flip-flopping between two near-equal models." />
              <NumberRow label="Cloud quality margin" cfg={cfg} field="cloud_quality_margin" min={0} max={1} step={0.01} patch={patch}
                hint="How much better a cloud model must score than a local one to be tried first. Free and private wins ties." />
              <NumberRow label="Re-proposal cooldown (days)" cfg={cfg} field="reproposal_cooldown_days" min={0} max={365} patch={patch}
                hint="After you reject a suggestion below, how long before the same change may be suggested again." />
            </RowGroup>
          )}
    </Section>
  )
}

/** Proposed routing changes — propose-don't-write.
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
  // Why the queue could not be read, in the gateway's words. `null` above is "not read", and the
  // gateway answers an unreadable store as a failure rather than as an empty queue.
  const [readErr, setReadErr] = useState('')
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [said, setSaid] = useState('')

  const load = useCallback(() => {
    api.routingProposals()
      .then((d) => { setProps(d.proposals); setReadErr('') })
      .catch((e: unknown) => { setProps(null); setReadErr(readableErrText(e)) })
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
        <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var" role="status">
          <p>
            Couldn't read the proposal queue, so proposals may be waiting that are not shown here.
            Your routing table is unchanged either way.
          </p>
          {readErr && <p data-type="caption" className="mt-xs text-on-surface-low">{readErr}</p>}
          <Button size="xs" variant="ghost" className="mt-xs" onClick={load}>Try again</Button>
        </div>
      ) : props_ === undefined ? (
        <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low">Loading…</div>
      ) : props_.length === 0 ? (
        <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
          Nothing proposed. When measurements show one of your models clearly beating another for a
          request kind, the change is proposed here — routing never rewrites your table on its own.
        </div>
      ) : (
        <>
        {/* The count, in a sentence rather than a bare badge: the number is only meaningful
            beside what it means, and "measured, not applied" is the property the queue exists to
            enforce. */}
        <p data-type="body-s" className="mb-m text-on-surface-var">
          {props_.length} proposed {props_.length === 1 ? 'change' : 'changes'} waiting on you.
          Routing measured these — it has not applied them.
        </p>
        <ul className="flex flex-col gap-2">
          {props_.map((p) => (
            <li key={p.id} className="rounded-lg bg-surface-container px-3 py-2.5">
              <p data-type="body-s" className="text-on-surface">
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
                <Button size="xs" variant="ghost" loading={busy === p.id}
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
      <p role="status" aria-live="polite" data-type={said ? 'body-s' : undefined}
        className={said ? 'mt-m text-on-surface-var' : ''}>{said}</p>
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
  // Present only when both models are priced, so the difference is a price.
  if (cost !== undefined && cost !== 0) {
    bits.push(`${fmtCost(Math.abs(cost), true)} ${cost < 0 ? 'cheaper' : 'dearer'} per call`)
  }
  if (bits.length === 0) return null
  return <p data-type="caption" className="mt-1 text-on-surface-low">{bits.join(' · ')}.</p>
}

/** The routing POLICY table.
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
  // Why the table could not be read, in the gateway's words: an unreadable routing_policy.json is
  // named there, with what that means for routing.
  const [readErr, setReadErr] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  // What the last successful reorder did, for the live region below. Empty until one happens.
  const [moved, setMoved] = useState('')

  const load = useCallback(() => {
    api.routingPolicy()
      .then((d) => { setRows(d.use_cases); setEnabled(d.enabled); setReadErr('') })
      .catch((e: unknown) => { setRows(null); setReadErr(readableErrText(e)) })
  }, [])
  useEffect(load, [load])

  // 🔴 A CLASS'S ORDER IS SAVED WHOLE, over the revision this table read it at. A reorder swapped
  // two entries of the order on screen and saved the result — so after another tab's reorder, or
  // an accepted routing proposal, it put this table's old order back over theirs. A stale copy is
  // now refused and the move — an operation, never the order — is re-applied on top of what is
  // stored (`ui/StaleWriteNotice`).
  const guard = useStaleWriteGuard<string[]>({
    read: () => api.routingPolicy().then((d) => paintedOrder(d.use_cases.find((r) => r.use_case === useCase), queryClass)),
    write: (next, base) => api.setRoutingOrder(useCase, queryClass, next, base),
    onSaved: load,
    onDiscard: load,
  })
  const conflicted = guard.conflict !== null

  const row = rows?.find((r) => r.use_case === useCase)

  // One write per interaction, then reload — the server is the authority on what the
  // table now says (a local guess could disagree with a floored/rejected value). For the two
  // single-valued levers; a reorder goes through the guard above.
  const save = async (body: Parameters<typeof api.setRoutingPolicy>[0]) => {
    setBusy(true)
    setNote('')
    try {
      await api.setRoutingPolicy(body)
      load()
    } catch (e) {
      // The gateway's sentence when it gave one: a refusal says why, and what to do about it.
      setNote(readableErrText(e) || "Couldn't save that — nothing changed.")
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
        <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var" role="status">
          <p>Couldn't read the routing table, so it is not shown here, and nothing about routing changed.</p>
          {readErr && <p data-type="caption" className="mt-xs text-on-surface-low">{readErr}</p>}
          <Button size="xs" variant="ghost" className="mt-xs" onClick={load}>Try again</Button>
        </div>
      </Section>
    )
  }

  const recorded = row?.classes?.[queryClass]
  const candidates = row?.candidates ?? []
  const painted = paintedOrder(row, queryClass)
  const shown = painted.value

  // A reorder is a status message (WCAG 4.1.3): the only feedback is that the row visually
  // swapped, and the ranking numbers beside each row are not in any focused control's
  // accessible name — so a user who cannot see the list gets nothing back from pressing the
  // button. Announce the ref AND its new position, because "moved earlier" alone does not say
  // where it landed or when the end of the list has been reached.
  const move = (index: number, delta: number) => {
    const ref = shown[index]
    const target = index + delta
    if (ref === undefined || target < 0 || target >= shown.length) return
    setBusy(true)
    setNote('')
    // `false` is a refused stale copy — the notice below holds the move. Only a move that landed
    // is announced, because announcing one that did not would be worse than announcing nothing.
    void guard.apply(painted, moveEntry(ref, delta))
      .then((ok) => { if (ok) setMoved(`${ref} moved to position ${target + 1} of ${shown.length}`) })
      .catch((e: unknown) => setNote(readableErrText(e) || "Couldn't save that — nothing changed."))
      .finally(() => setBusy(false))
  }

  return (
    <Section title="Routing policy">
      <p data-type="body-s" className="mb-m text-on-surface-var">
        Which of your bound models this use case tries first. Routing only reorders the models you
        already bound — it never adds or removes one, and an unavailable model still reports an
        error rather than being quietly swapped.
        {!enabled && ' Routing is currently off globally, so this order is not applied yet.'}
      </p>

      {!row ? (
        <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
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
            <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
              No models bound to this use case yet. Bind two — one local, one cloud — to give routing
              a choice to make.
            </div>
          ) : (
            <ol className="flex flex-col gap-1.5">
              {shown.map((ref, i) => {
                const local = candidates.find((c) => c.ref === ref)?.local
                return (
                  <li key={ref} data-type="body-s" className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
                    <span className="w-5 text-right tabular-nums text-on-surface-low">{i + 1}</span>
                    <span className="flex-1 truncate font-mono text-on-surface" title={ref}>{ref}</span>
                    <span data-type="caption" className="text-on-surface-low">{local ? 'local' : 'cloud'}</span>
                    {/* `size-7` (28px), not `p-1` (21px): an icon-only control needs 24px of target.
                        These keep `unavailableWhen` rather than adopting `SquareIconButton`, and the
                        reason recorded here USED TO BE FACTUALLY WRONG — worth correcting rather than
                        deleting, because it was steering future work away from the primitive on a
                        premise that does not hold. It said the primitive "never" sets the native
                        attribute so an in-flight save could be fired twice. It cannot: `IconButton`
                        guards with `off = !!disabled || loading` and drops `onClick` entirely, which
                        its own comment states exists so that "`loading` would [not] trade a false
                        'unavailable' for a double-fire". So the primitive refuses the second click too.
                        What actually keeps these on `unavailableWhen` is the missing-input REASON: a
                        soft-off with a title ("Already tried first") on a control that stays tabbable,
                        which is this helper's whole purpose. Its busy branch now also carries
                        `aria-busy`, so the two paths no longer disagree about what in-flight means. */}
                    <button type="button"
                      {...unavailableWhen(i === 0 || conflicted, conflicted ? HELD_CHANGE_REASON : 'Already tried first', { busy })}
                      onClick={() => move(i, -1)}
                      className="grid size-7 place-items-center rounded-md text-on-surface-var hover:bg-surface-high aria-disabled:opacity-40 disabled:opacity-40"
                      aria-label={`Move ${ref} earlier`}>
                      <ArrowUp size={13} aria-hidden />
                    </button>
                    <button type="button"
                      {...unavailableWhen(i === shown.length - 1 || conflicted, conflicted ? HELD_CHANGE_REASON : 'Already tried last', { busy })}
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

          <p data-type="caption" className="mt-m text-on-surface-low">
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
          <StaleWriteNotice guard={guard} what="This routing order" className="mt-s" />
        </>
      )}
    </Section>
  )
}

/** Why a reorder control is unavailable while a refused move waits in the notice below it. */

/** The order a class's table shows — the recorded ranking first, then any newly-bound model — with
 *  the revision of the class's order from the SAME read, which a reorder names. */
function paintedOrder(row: RoutingPolicyRow | undefined, queryClass: string): Revisioned<string[]> {
  const order = row?.classes?.[queryClass]?.order ?? []
  const candidates = row?.candidates ?? []
  return {
    value: [
      ...order.filter((ref) => candidates.some((c) => c.ref === ref)),
      ...candidates.map((c) => c.ref).filter((ref) => !order.includes(ref)),
    ],
    revision: row?.order_revisions?.[queryClass] ?? '',
  }
}

/** Move `ref` one place earlier (`-1`) or later (`1`) in whatever order is stored — found by name,
 *  so it is the same move wherever another tab put it; `null` when there is no such move left. */
function moveEntry(ref: string, delta: number): Rebase<string[]> {
  return (theirs) => {
    const i = theirs.indexOf(ref)
    const j = i + delta
    if (i < 0 || j < 0 || j >= theirs.length) return null
    const next = [...theirs]
    ;[next[i], next[j]] = [next[j], next[i]]
    return next
  }
}

/** The per-model efficiency table. Frontier rows are marked with a labeled badge
 *  (not color alone) and floated to the top. Numbers are right-aligned and
 *  tabular; headers carry `scope="col"` for screen-reader column association. */
function TelemetryTable({ rows }: { rows: TelemetryRow[] }) {
  const th = 'border-b border-outline-variant/40 px-2 py-1.5 font-normal'
  const td = 'border-b border-outline-variant/25 px-2 py-1.5'
  return (
    <div className="overflow-x-auto">
      <table data-type="body-s" className="w-full border-collapse">
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
              <td className={`${td} text-right tabular-nums`}>{fmtCost(r.avg_cost_usd, r.priced)}</td>
              <td className={`${td} text-right`}>
                {r.on_frontier ? (
                  <StatusPill tone="ok" sized={false} data-type="caption" className="gap-1 py-0.5"
                    title="On the Pareto frontier — no other model beats this one on all of quality, speed, and cost.">
                    <Trophy size={9} aria-hidden /> frontier
                  </StatusPill>
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
