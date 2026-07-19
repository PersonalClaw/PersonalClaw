import { CalendarClock, CheckCircle2, XCircle, PlayCircle } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { EmptyState, WidgetRow, StatusDot } from './kit'
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
  const { schedule } = useDashboardLive()

  if (schedule.length === 0) {
    return <EmptyState icon={CalendarClock}>No recent scheduled runs.</EmptyState>
  }

  const outcome = (status?: string) => {
    if (status === 'success') return { icon: CheckCircle2, tone: 'var(--color-ok)', label: 'succeeded' }
    if (status === 'error' || status === 'failure' || status === 'timeout') return { icon: XCircle, tone: 'var(--color-danger)', label: status }
    return { icon: PlayCircle, tone: 'var(--color-info)', label: status || 'ran' }
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      {schedule.slice(0, 6).map((r, i) => {
        const o = outcome(r.status)
        const when = r.finished_at ?? r.started_at
        return (
          <WidgetRow key={r.run_id ?? `${r.job_id}-${i}`} onClick={() => navigate('triggers')}>
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
