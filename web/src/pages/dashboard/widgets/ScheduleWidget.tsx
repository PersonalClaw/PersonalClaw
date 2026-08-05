import { CalendarClock } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { statusMeta } from '../../schedule/scheduleMeta'
import { SlotEmptyState, WidgetRow, StatusDot } from './kit'
import type { RouteProps } from '../../../app/useQueryState'

/** Compact "time ago / from now" for an epoch-seconds timestamp. */
function rel(secs?: number): string {
  if (!secs) return ''
  const now = Date.now() / 1000
  const d = Math.abs(now - secs)
  const past = secs <= now
  const unit = d < 60 ? `${Math.round(d)}s` : d < 3600 ? `${Math.round(d / 60)}m` : d < 86400 ? `${Math.round(d / 3600)}h` : `${Math.round(d / 86400)}d`
  return past ? `${unit} ago` : `in ${unit}`
}

/** Schedule Timeline — recent trigger run outcomes (cross-trigger history index).
 *  Each row shows the schedule name, outcome (success/error/launched), and when.
 *  The header jumps to Triggers. (Upcoming-run projection is layered in once the
 *  cross-trigger "next fire" index is surfaced; today the backend history endpoint
 *  is the runs index.) */
export function ScheduleWidget({ navigate }: RouteProps) {
  const { schedule, scheduleDidIds, scheduleSuppressed } = useDashboardLive()

  if (schedule.length === 0) {
    return <SlotEmptyState icon={CalendarClock}>No recent scheduled runs.</SlotEmptyState>
  }

  // 🔴 Uses the SHARED `statusMeta` (S137), not a local mapper (S163). This widget had its own
  // three-branch `outcome()` keyed on `status` — but `/api/triggers/history` returns FireRecord
  // rows, which carry no `status` at all: their typed value is `outcome`
  // (`ran | skipped_gate | blocked_injection | deferred | refused | …`). So every projected row hit
  // the default branch and a quiet-hours SUPPRESSION rendered as "ran" with an info dot — the feed
  // reporting that the machine did work it explicitly had not done. `statusMeta` already maps the
  // whole vocabulary (S132's skipped_*, S136's blocked_injection, T7's launched); a second local
  // copy is how two surfaces start disagreeing about what an outcome means.

  // 🔴 §1.3's ARCHIVE SPLIT, finally applied (S165). "Inert outcomes collapse to ledger rows and
  // archive out of the default inbox view — the runs inbox is for what the machine DID." The
  // backend has returned `did_ids` since S132 and this widget rendered the raw list, so a minutely
  // trigger inside quiet hours filled all six visible rows with identical "gate" entries while the
  // ONE fire that ran sat at index 7, invisible. Measured. A user reads that as "nothing has run".
  //
  // Ordered, not filtered: the suppressed rows still render BELOW the real ones, because §7
  // criterion 8 bans silent drops — "why did my automation not run" has to stay answerable. What
  // changes is that work outranks suppression for the scarce visible slots.
  //
  // Membership comes from the SERVER's `did_ids`, not from re-testing the outcome here: `is_inert`
  // is the backend's rule, and a second copy drifts the moment a new `skipped_*` outcome lands.
  //
  // 🔴 Keyed on `r.id`, and that was a bug in my own first draft: I used `r.run_id`, but a
  // FireRecord carries no `run_id` (measured: it serialises as `''`) — its identity IS `id`. The
  // split would have matched nothing and silently no-opped, which is how a fix becomes an inert
  // control. A row with no `id` is treated as NOT-suppressed: an unknown row is more likely real
  // work than a gate hit, and the fail direction has to be "shown" rather than "hidden".
  const did = new Set(scheduleDidIds)
  const isDid = (r: typeof schedule[number]) => !r.id || did.has(r.id)
  const ordered = [...schedule.filter(isDid), ...schedule.filter((r) => !isDid(r))]

  return (
    <div className="flex flex-col gap-xs pt-xs">
      {scheduleSuppressed > 0 && (
        <p data-type="body-m" className="px-xs text-on-surface-low">
          {scheduleSuppressed} suppressed by a gate
        </p>
      )}
      {ordered.slice(0, 6).map((r, i) => {
        const o = statusMeta(r.outcome ?? r.status)
        const when = r.finished_at ?? r.started_at
        return (
          <WidgetRow key={r.id ?? r.run_id ?? `${r.job_id}-${i}`} onClick={() => navigate('triggers')}>
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
      })}
    </div>
  )
}
