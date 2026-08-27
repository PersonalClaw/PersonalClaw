import { useState } from 'react'
import { MoreRow } from '../../../ui/MoreRow'
import { CalendarClock, ChevronDown, ChevronRight, Plus } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { statusMeta, relPast, relFuture } from '../../schedule/scheduleMeta'
import { epochSeconds } from '../../../lib/epoch'
import { SlotEmptyState, SlotAction, WidgetRow, StatusDot } from './kit'
import { partitionRuns } from './scheduleFold'
import { Button } from '../../../ui/Button'
import type { RouteProps } from '../../../app/useQueryState'
import { rowSubject } from '../../../lib/rowSubject'

/** Compact "time ago / from now" for a timestamp in EITHER wire shape.
 *
 *  This used to own a fourth copy of the thresholds and do arithmetic on `secs` directly,
 *  typed `number`. `/api/triggers/history` — the endpoint that feeds this widget — sends
 *  ISO-8601 strings, so every comparison was false and the last branch rendered its unit
 *  with NaN in front of it: six rows of **"in NaNd"** on the home dashboard, the first
 *  thing a user sees. It now composes the shared pair, which coerces and has an honest
 *  empty value. */
function rel(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs == null) return ''
  return secs <= Date.now() / 1000 ? relPast(secs) : relFuture(secs)
}

/** Schedule Timeline — recent trigger run outcomes (cross-trigger history index).
 *  Each row shows the schedule name, outcome (success/error/launched), and when.
 *  The header jumps to Triggers. (Upcoming-run projection is layered in once the
 *  cross-trigger "next fire" index is surfaced; today the backend history endpoint
 *  is the runs index.) */
export function ScheduleWidget({ navigate }: RouteProps) {
  const { schedule, scheduleDidIds, scheduleSuppressed } = useDashboardLive()
  // Suppressed rows are archived out of the default view; the disclosure reveals them on demand.
  // Collapsed by default because §1.3 is explicit that "the runs inbox is for what the machine
  // DID" — the fold IS the archive, not a nicety layered over an already-crowded list.
  const [showSuppressed, setShowSuppressed] = useState(false)

  if (schedule.length === 0) {
    // 🔴 THE ONE SLOT ON THE FIRST SCREEN THAT TAUGHT NOTHING. Measured on a fresh install
    // (`#/dashboard`, 1440×1000, empty home past onboarding): seven slot-empty states render, and
    // six either name the mechanism that fills them — "Loops you launch appear here as they run",
    // "Pin one from its page to keep it here", "One loads on its first use", "they build from your
    // activity" — or are a finished verdict ("All clear — nothing waiting on you."). This one was a
    // bare fact with no mechanism and no step, on the surface a newcomer sees first.
    //
    // Both halves are borrowed rather than invented: the sentence takes ActiveWork's shape and the
    // product's own verb for a trigger run (`WeekGridView`'s "No fires this week", this file's
    // "suppressed by a gate"), and the on-ramp takes the label shipped by `TriggersListPage`'s top
    // bar and `TriggerCreatePage`'s title — "New trigger" — routed to the same `triggers/new` the
    // Triggers empty state's own "Start from scratch" opens. The widget already navigates to
    // `triggers`, so this adds no new destination.
    return (
      <SlotEmptyState
        icon={CalendarClock}
        action={<SlotAction icon={Plus} onClick={() => navigate('triggers/new')}>New trigger</SlotAction>}
      >No recent scheduled runs. Triggers you set up appear here as they fire.</SlotEmptyState>
    )
  }

  // 🔴 §1.3's ARCHIVE SPLIT as a FOLD (WF2AUT-10). The backend has returned `did_ids` since S132;
  // S165 got them as far as ordering work-first, but ordering only helps until the real fires run
  // out — a minutely trigger inside quiet hours still buried the ONE fire that ran under 11 gate
  // hits once the visible slots filled. So the suppressed rows now archive behind a disclosure:
  // the default view is work, and §7 criterion 8's "zero silent drops" holds because they are one
  // click away, not gone. `partitionRuns` is the single shared copy of the split — membership
  // comes from the SERVER's `did_ids`, never from re-testing `is_inert` here (a second copy drifts
  // the moment a new `skipped_*` outcome lands).
  //
  // Outcomes render via the SHARED `statusMeta` (S137/S163): `/api/triggers/history` returns
  // FireRecord rows whose typed value is `outcome` (`ran | skipped_gate | blocked_injection | …`),
  // not `status`, so a local mapper would render a quiet-hours suppression as "ran".
  const { did, suppressed } = partitionRuns(schedule, scheduleDidIds)
  // The count comes from the SERVER (`scheduleSuppressed`, the full-window tally) rather than
  // `suppressed.length`, which only sees the page fetched — the label must not shrink to the fold.
  const visible = showSuppressed ? [...did, ...suppressed] : did

  const row = (r: typeof schedule[number], i: number) => {
    const o = statusMeta(r.outcome ?? r.status)
    const when = r.finished_at ?? r.started_at
    return (
      <WidgetRow key={r.id ?? r.run_id ?? `${r.job_id}-${i}`} onClick={() => navigate('triggers')}
        label={rowSubject([r.job_name || r.job_id || 'Schedule', statusMeta(r.outcome ?? r.status).label])}>
        <div className="flex items-center gap-s">
          <StatusDot color={o.tone} />
          <div className="min-w-0 flex-1">
            <p data-type="title-m" className="truncate text-on-surface">{r.job_name || r.job_id || 'Schedule'}</p>
            <p data-type="body-m" className="truncate text-on-surface-low">
              <span style={{ color: o.tone }}>{o.label}</span>{r.trigger ? ` · ${r.trigger}` : ''}
            </p>
          </div>
          <span data-type="body-m" className="shrink-0 text-on-surface-low">{rel(when)}</span>
        </div>
      </WidgetRow>
    )
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      {did.length === 0 && !showSuppressed && (
        <p data-type="body-m" className="px-xs text-on-surface-low">
          Nothing ran recently — every recent fire was held by a gate.
        </p>
      )}
      {visible.slice(0, 6).map(row)}
      {/* 🔑 THIS FILE ALREADY SETS THE STANDARD IT WAS MISSING. Its disclosure below reveals the rows
          an archive fold hides, and its comment insists that count come from the SERVER's full-window
          tally because "the label must not shrink to the fold" — while a second, silent truncation sat
          right here, cutting the visible rows at six with nothing said. Same principle, applied to the
          cap: `visible` is what the fold decided to show, so the residue is measured against it. */}
      <MoreRow total={visible.length} shown={6} />
      {scheduleSuppressed > 0 && (
        <Button
          variant="ghost"
          size="xs"
          ariaExpanded={showSuppressed}
          onClick={() => setShowSuppressed((v) => !v)}
          className="self-start px-xs text-on-surface-low"
        >
          {showSuppressed ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {showSuppressed
            ? `Hide ${scheduleSuppressed} suppressed by a gate`
            : `Show ${scheduleSuppressed} suppressed by a gate`}
        </Button>
      )}
    </div>
  )
}
