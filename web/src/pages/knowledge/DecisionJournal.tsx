import { Scale, Clock, CircleAlert, BellOff, Brain, MessageSquare } from 'lucide-react'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { api, type CalibrationBucket, type DecisionRow } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { fvs } from '../../design/fontWeight'
import {
  bucketLabel,
  bucketPlottable,
  calibrationCaption,
  calibrationState,
  confidenceLabel,
  gradeLabel,
  horizonLabel,
  pendingState,
} from './decisionMeta'

/** The Decision Journal (PROACTIVE-ASSISTANT §5.3) — a lens on the knowledge library, not a new
 *  destination, because a decision IS a knowledge item.
 *
 *  ONE fetch backs the whole surface. The strip is an aggregate of the rows beneath it, so a
 *  second fetch could put a rate computed from ten resolved decisions above a list of eleven —
 *  two answers to one question. And nothing here computes a rate: `decisions.calibration` is the
 *  only definition of how well-calibrated the user is, and this renders what it returns.
 *
 *  The tone rule for the whole panel: an unmeasured thing is SAID to be unmeasured. Never a 0%,
 *  never an empty chart, never a flat bar — each of those reads as "perfectly calibrated", the
 *  strongest possible claim, exactly where the truth is "nobody knows yet". See
 *  `decisionJournal.ts` for the three states and why they are three. */
export function DecisionJournal({ onOpenItem, onOpenChat }: { onOpenItem: (id: string) => void
  /** The way IN to the only surface that can create a decision. REQUIRED, not optional: a
   *  call site must not be able to ship the fact ("you have none") without the way to make
   *  one — the `ArtifactGrid` rule from PEP-2. */
  onOpenChat: () => void }) {
  const { data, loading, error, refresh } = useQuery('knowledge:decisions', () => api.decisionJournal())

  if (loading && !data) return <ListSkeleton rows={4} what="decision journal" />
  // A failed read is an ERROR, never an empty journal: "you have never decided anything" is the
  // most confident possible way to say the opposite of what is known.
  if (error) return <LoadError what="decision journal" error={error} onRetry={refresh} />
  if (!data) return null

  const pending = data.decisions.filter((d) => d.status === 'pending')
  const resolved = data.decisions.filter((d) => d.status === 'resolved')

  if (data.decisions.length === 0) {
    return (
      <EmptyState
        icon={Scale}
        title="No decisions logged yet"
        hint="Log a decision in chat — what you decided, what you expect to happen, and how confident you are. It comes back on its own when the horizon arrives."
        // Chat is the on-ramp because it is the ONLY place a decision can be made: logging one also
        // mints its one-shot review trigger, so `handlers/knowledge.py` deliberately refuses to
        // create a `decision` from the library's create picker (an item authored there would be a
        // decision that never comes back). Naming chat in prose and leaving the user to find it is
        // what PEP-2 flagged on Knowledge › Intents, so this carries the control.
        action={{ label: 'Open chat', onClick: onOpenChat, icon: MessageSquare }}
      />
    )
  }

  return (
    <div className="flex flex-col gap-xl">
      <CalibrationStrip view={data} />
      {pending.length > 0 && (
        <section aria-labelledby="decisions-pending" className="flex flex-col gap-s">
          <h2 id="decisions-pending" data-type="title-s" className="text-on-surface-low">Open ({pending.length})</h2>
          {pending.map((d, i) => <PendingRow key={d.id} d={d} index={i} onOpen={() => onOpenItem(d.id)} />)}
        </section>
      )}
      {resolved.length > 0 && (
        <section aria-labelledby="decisions-resolved" className="flex flex-col gap-s">
          <h2 id="decisions-resolved" data-type="title-s" className="text-on-surface-low">Resolved ({resolved.length})</h2>
          {resolved.map((d, i) => <ResolvedRow key={d.id} d={d} index={i} onOpen={() => onOpenItem(d.id)} />)}
        </section>
      )}
    </div>
  )
}

/** The decision's life domain. A plain tag, deliberately NOT the page's `FilterChip`: that is a
 *  button, and nothing here filters — a control that looks pressable and does nothing is worse
 *  than a label. */
function DomainTag({ domain }: { domain: string }) {
  return (
    <span className="shrink-0 rounded-full px-2 py-[1px] text-[0.75rem] text-on-surface-low"
      style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>{domain}</span>
  )
}

/** §2.5's strip. Three states, three sentences, and a bar ONLY where the backend called the
 *  bucket count-honest.
 *
 *  🔴 These section headings are h2, not h3 — caught by `discoverHeadingLevel`'s scope ratchet.
 *  `PageTitle` is this page's h1 and there is no h2 between it and these sections, so an h3 here
 *  was exactly the h1-to-h3 skip (WCAG 1.3.1) that test exists to prevent. `LibraryHome`, the peer
 *  lens on the same page, uses h2 for its shelf headings; this matches it.
 *
 *  🪤 The tag name is deliberately NOT written out in angle brackets anywhere in this file: that
 *  ratchet is a TEXT scan, so a heading tag named inside a comment counts as a use and put this
 *  file back on the list even after every real heading had been promoted.
 *  bucket count-honest — `bucketPlottable` is the single gate, so a proportional bar can never
 *  appear under a "too few to mean much" caption. */
function CalibrationStrip({ view }: { view: Parameters<typeof calibrationCaption>[0] }) {
  const state = calibrationState(view.calibration)
  const domains = Object.entries(view.calibration).sort((a, b) => b[1].n - a[1].n)
  return (
    <section aria-labelledby="decisions-calibration" className="rounded-xl border border-outline-variant p-l">
      <div className="flex items-center gap-s">
        <Scale size={16} className="text-primary" aria-hidden />
        <h2 id="decisions-calibration" data-type="title-s" className="text-on-surface">Calibration</h2>
      </div>
      {/* The caption is the claim. It is rendered in every state including the two with no
          number, because a strip that goes silent when it has nothing to say leaves the reader
          to assume the chart above it means something. */}
      <p className="mt-1 text-on-surface-low text-[0.9375rem]" data-calibration-state={state}>
        {calibrationCaption(view)}
      </p>
      {domains.length > 0 && (
        <ul className="mt-l flex flex-col gap-s">
          {domains.map(([domain, b]) => (
            <li key={domain} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between gap-s">
                <span className="text-on-surface text-[0.9375rem]" style={fvs(500)}>{domain}</span>
                <span className="text-on-surface-low text-[0.8125rem] tabular-nums">{bucketLabel(b, view.calibration_min_n)}</span>
              </div>
              <Bar b={b} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** The bar, or the honest absence of one.
 *
 *  🪤 A 0%-width bar was the first shape here and it is a lie in the shape of a chart: an
 *  under-threshold domain drew an empty track, which is visually identical to "0% as expected"
 *  and adjacent to "flawless". Below the threshold there is no bar at all — the count in the
 *  label is the whole truth. */
function Bar({ b }: { b: CalibrationBucket }) {
  if (!bucketPlottable(b)) {
    return (
      <p className="text-on-surface-low text-[0.8125rem] opacity-80">
        Not enough resolved decisions in this domain to draw a rate.
      </p>
    )
  }
  const pct = Math.round((b.as_expected_rate ?? 0) * 100)
  return (
    <div className="flex h-[6px] overflow-hidden rounded-full" role="img"
      aria-label={`${b.better} better than expected, ${b.as_expected} as expected, ${b.worse} worse than expected`}>
      <span className="h-full" style={{ width: `${(b.better / b.n) * 100}%`, background: 'var(--color-ok)' }} />
      <span className="h-full" style={{ width: `${pct}%`, background: 'var(--color-primary)' }} />
      <span className="h-full" style={{ width: `${(b.worse / b.n) * 100}%`, background: 'var(--color-warn)' }} />
    </div>
  )
}

/** An open decision: what you expect, how sure you were, and when it comes back.
 *
 *  Three pending states get three different icons AND three different sentences, because
 *  "stale-pending" means no reminder is coming and rendering it as merely overdue would promise
 *  a review card that will never arrive. */
function PendingRow({ d, index, onOpen }: { d: DecisionRow; index: number; onOpen: () => void }) {
  const state = pendingState(d)
  const Icon = state === 'stale' ? BellOff : state === 'overdue' ? CircleAlert : Clock
  const tone = state === 'stale' ? 'var(--color-warn)' : state === 'overdue' ? 'var(--color-danger)' : 'var(--color-on-surface-low)'
  return (
    <ListRow index={index} onClick={onOpen} label={d.summary} accent={state === 'counting' ? undefined : tone}>
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-s">
          <span className="truncate text-on-surface" style={fvs(500)}>{d.summary}</span>
          <DomainTag domain={d.domain} />
        </div>
        <p className="line-clamp-2 text-on-surface-low text-[0.9375rem]">
          <span className="text-on-surface-low opacity-80">Expected: </span>{d.expectation}
        </p>
        <div className="flex flex-wrap items-center gap-s text-[0.8125rem]" style={{ color: tone }}>
          <Icon size={13} aria-hidden />
          <span data-pending-state={state}>{horizonLabel(d)}</span>
          <span className="text-on-surface-low opacity-80">· {confidenceLabel(d)}</span>
        </div>
      </div>
    </ListRow>
  )
}

/** A resolved decision, expectation and outcome SIDE BY SIDE (§5.3). Never one collapsed into a
 *  verdict: the whole value of the record is that the two are readable against each other. */
function ResolvedRow({ d, index, onOpen }: { d: DecisionRow; index: number; onOpen: () => void }) {
  const grade = d.outcome_grade || ''
  const tone = grade === 'better' ? 'var(--color-ok)' : grade === 'worse' ? 'var(--color-warn)' : 'var(--color-primary)'
  return (
    <ListRow index={index} onClick={onOpen} label={d.summary} accent={tone}>
      <div className="flex min-w-0 flex-col gap-s">
        <div className="flex min-w-0 items-center gap-s">
          <span className="truncate text-on-surface" style={fvs(500)}>{d.summary}</span>
          <DomainTag domain={d.domain} />
          {/* Words, not the wire enum — and an empty grade renders `ungraded`, never the middle
              grade: a decision the user resolved without grading is not one that came out as
              expected. `gradeLabel` owns both halves. */}
          <span className="shrink-0 text-[0.8125rem]" style={{ color: tone }}>{gradeLabel(grade)}</span>
        </div>
        <dl className="grid gap-s sm:grid-cols-2">
          <div className="min-w-0">
            <dt className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Expected</dt>
            <dd className="mt-1 text-on-surface text-[0.9375rem]">{d.expectation}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">What happened</dt>
            <dd className="mt-1 text-on-surface text-[0.9375rem]">{d.outcome || '—'}</dd>
          </div>
        </dl>
        <div className="flex flex-wrap items-center gap-s text-[0.8125rem] text-on-surface-low">
          <span>{confidenceLabel(d)}</span>
          {/* The lesson chip is a SOFT reference into the memory store, so its absence is a real
              state (the lesson write was refused) and is said rather than hidden. */}
          {d.lesson_memory_key
            ? <span className="inline-flex items-center gap-1"><Brain size={12} aria-hidden /> lesson recorded</span>
            : <span className="opacity-80">no lesson recorded</span>}
        </div>
      </div>
    </ListRow>
  )
}
