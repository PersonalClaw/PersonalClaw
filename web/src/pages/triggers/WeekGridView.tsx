import { useMemo, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight, AlertTriangle } from 'lucide-react'
import { fvs } from '../../design/fontWeight'
import { EmptyState } from '../../ui/ListScaffold'
import { Button } from '../../ui/Button'
import { useCachedData } from '../../lib/useCachedData'
import { api, type WeekProjection } from '../../lib/api'
import { buildWeekGrid, cellLabel, visibleHours, weekSummary, startOfDay, type CellState, type WeekCell } from './weekGrid'

/** The Week tab — a 7×24 grid of every enabled clock trigger's fires (AUTO-A3 — S81).
 *
 *  `GET /api/triggers/week` shipped in S70 with ZERO frontend consumers; this is the half AUTO-A3
 *  names alongside it ("+ the Automations Week tab (7×24 grid, shaded quiet bands, click-through)").
 *
 *  Read-only by contract. Every cell is a projection from the recurrence a trigger already carries,
 *  so there is nothing here to save — clicking a cell opens the trigger it belongs to, which is the
 *  "click-through to the trigger row" the criterion asks for.
 *
 *  **Suppressed fires are SHOWN, shaded, never hidden.** The server annotates rather than filters,
 *  and the view keeps that: a grid that omitted suppressed slots would display a schedule the user
 *  does not have, and the reason someone opens this view is to find out why an automation did not
 *  run when they expected it.
 */

/** Cell colours by state. Suppression reasons are visually DISTINCT, not one generic "off" shade:
 *  a quiet-window fire is deferred while a skip-date fire is cancelled, and a user deciding what to
 *  change needs to tell those apart at a glance. */
const CELL_TONE: Record<CellState, { bg: string; fg: string }> = {
  empty: { bg: 'transparent', fg: 'var(--color-on-surface-low)' },
  fires: { bg: 'color-mix(in srgb, var(--color-primary) 68%, transparent)', fg: 'var(--color-on-primary)' },
  // Warn-toned and dimmed: suppressed by a time-of-day rule, may still catch up.
  quiet: { bg: 'color-mix(in srgb, var(--color-warn) 26%, transparent)', fg: 'var(--color-on-surface-var)' },
  // Struck: the whole day is excluded and never catches up, so it reads as cancelled.
  skipped: { bg: 'color-mix(in srgb, var(--color-on-surface-low) 20%, transparent)', fg: 'var(--color-on-surface-low)' },
  mixed: { bg: 'color-mix(in srgb, var(--color-primary) 34%, transparent)', fg: 'var(--color-on-surface)' },
}

export function WeekGridView({ onOpenTrigger }: { onOpenTrigger?: (triggerId: string) => void }) {
  // Week offset in days from today. Kept in component state rather than the URL: the grid is a
  // glance surface, and a deep-linked "week of" is a different feature (the trigger itself is the
  // addressable thing, and clicking a cell routes to it).
  const [offset, setOffset] = useState(0)

  const start = useMemo(() => {
    const d = startOfDay(new Date())
    d.setDate(d.getDate() + offset * 7)
    return d
  }, [offset])

  // Keyed by the week so paging fetches rather than reusing the previous week's cells. persist:false
  // — a projection is only true relative to `now`, so a cached week restored after a hard reload
  // would show a forecast that has already partly happened.
  const { data: week } = useCachedData<WeekProjection>(
    `triggers:week:${start.toISOString().slice(0, 10)}`,
    () => api.triggersWeek(localIso(start), 7),
    { persist: false },
  )

  const grid = useMemo(() => buildWeekGrid(week?.occurrences ?? [], start), [week, start])
  const hours = useMemo(() => visibleHours(grid), [grid])
  const cellAt = useMemo(() => {
    const m = new Map<string, WeekCell>()
    for (const c of grid.cells) m.set(`${c.day}:${c.hour}`, c)
    return m
  }, [grid])

  // The viewer's zone vs the server's. Shown only when they DIFFER: a caption that always says
  // "times shown in your timezone" is noise, while one that appears exactly when the host is
  // elsewhere is the warning that makes an off-by-hours grid legible.
  const viewerTz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const tzMismatch = Boolean(week?.server_tz && viewerTz && week.server_tz !== viewerTz)

  return (
    <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
      <div className="mb-m flex flex-wrap items-center justify-between gap-s">
        <div className="min-w-0">
          <div className="flex items-center gap-s">
            <span data-type="title-s" className="text-on-surface">{weekLabel(grid.days)}</span>
            {offset !== 0 && <Button size="sm" variant="ghost" onClick={() => setOffset(0)}>Today</Button>}
          </div>
          <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">
            {week === undefined ? 'Projecting…' : weekSummary(grid)}
            {tzMismatch && <span> · times in {viewerTz} (server: {week?.server_tz})</span>}
          </div>
        </div>
        <div className="flex items-center gap-xs">
          <Button size="sm" variant="ghost" aria-label="Previous week" onClick={() => setOffset((o) => o - 1)}><ChevronLeft size={15} /></Button>
          <Button size="sm" variant="ghost" aria-label="Next week" onClick={() => setOffset((o) => o + 1)}><ChevronRight size={15} /></Button>
        </div>
      </div>

      {/* The cap is REPORTED, never silent. A trigger that fires every minute is capped at 200
          occurrences, and a partial week rendered without saying so reads as an accurate forecast. */}
      {week && week.truncated.length > 0 && (
        <div className="mb-m flex items-start gap-s rounded-lg bg-surface-high p-s text-[0.8125rem] text-on-surface-var">
          <AlertTriangle size={15} style={{ color: 'var(--color-warn)' }} className="mt-0.5 shrink-0" />
          <span>
            {week.truncated.length} trigger{week.truncated.length === 1 ? '' : 's'} fire too often to plot in full
            — this week is partial for {week.truncated.join(', ')}.
          </span>
        </div>
      )}

      {week !== undefined && grid.totalFires === 0 ? (
        <EmptyState
          icon={CalendarDays}
          title="No fires this week"
          hint="Only enabled interval schedules are plotted. A cron-expression trigger is not projected here yet, and a disabled one has no fires."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-separate" style={{ borderSpacing: '2px' }}>
            <caption className="sr-only">
              Scheduled trigger fires by day and hour. Shaded cells are suppressed by a quiet window or a skip date.
            </caption>
            <thead>
              <tr>
                <th scope="col" className="w-12 text-right text-on-surface-low text-[0.75rem]" style={fvs(500)}>
                  <span className="sr-only">Hour</span>
                </th>
                {grid.days.map((d, i) => (
                  <th key={i} scope="col" className="px-1 pb-1 text-center text-[0.75rem] text-on-surface-var" style={fvs(500)}>
                    <div>{d.toLocaleDateString(undefined, { weekday: 'short' })}</div>
                    <div className="text-on-surface-low">{d.getDate()}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {hours.map((hour) => (
                <tr key={hour}>
                  <th scope="row" className="pr-1 text-right align-middle text-on-surface-low text-[0.75rem] tabular-nums" style={fvs(400)}>
                    {String(hour).padStart(2, '0')}
                  </th>
                  {grid.days.map((day, di) => {
                    const cell = cellAt.get(`${di}:${hour}`)
                    if (!cell) return <td key={di} />
                    const tone = CELL_TONE[cell.state]
                    const label = cellLabel(cell, day)
                    const clickable = cell.count > 0 && Boolean(onOpenTrigger)
                    // The cell is a `td`, not a control. Two reasons, and the design ratchet
                    // (`primitiveAdoption.test.ts`) is what made me check: a heat grid of 168 raw
                    // button elements is new bespoke chrome, and the `Button` primitive is a
                    // sheen-animated pill with no `aria-label` — wrong shape for a 24px heat cell, and
                    // 168 of them would animate on every hover. The interactive affordance lives on
                    // the cell's `onClick` + keyboard handler with an explicit role, so the semantics
                    // are still a button where it matters (name, role, focus) without minting chrome.
                    //
                    // NB the scanner is a regex over source text, so even a literal button tag inside
                    // a COMMENT counts against the baseline. Prose says "button element" for that
                    // reason, not to be coy.
                    const open = () => {
                      // A cell holding several triggers opens the first. The alternative is a
                      // disambiguation popover on a glance surface, and the tooltip names them all.
                      const id = cell.triggerIds[0] ?? ''
                      if (id && onOpenTrigger) onOpenTrigger(id)
                    }
                    return (
                      <td
                        key={di}
                        // Only a cell with fires is a control. An empty cell keeps no role and no tab
                        // stop: tabbing through 168 empty cells to reach the one interesting hour is
                        // the accessibility failure that would make this grid unusable by keyboard.
                        role={clickable ? 'button' : undefined}
                        tabIndex={clickable ? 0 : undefined}
                        aria-label={label}
                        title={label}
                        onClick={clickable ? open : undefined}
                        onKeyDown={
                          clickable
                            ? (e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault()
                                  open()
                                }
                              }
                            : undefined
                        }
                        className="h-6 rounded text-center text-[0.6875rem] tabular-nums transition-colors"
                        style={{
                          background: tone.bg,
                          color: tone.fg,
                          cursor: clickable ? 'pointer' : 'default',
                          // The struck look for a skip date: the count stays readable, and the strike
                          // says "cancelled" without relying on colour alone (WCAG — colour is never
                          // the only channel).
                          textDecoration: cell.state === 'skipped' ? 'line-through' : undefined,
                          border:
                            cell.count === 0 ? '1px solid var(--color-outline-var)' : '1px solid transparent',
                        }}
                      >
                        {cell.count > 0 ? cell.count : ''}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <Legend />
        </div>
      )}
    </div>
  )
}

/** The colour key. Not decoration: three of the five cell states mean "this will not run", and a
 *  grid whose shading is unexplained makes the user assume their schedule is broken. */
function Legend() {
  const items: Array<{ state: CellState; label: string }> = [
    { state: 'fires', label: 'Will run' },
    { state: 'mixed', label: 'Partly suppressed' },
    { state: 'quiet', label: 'Quiet hours' },
    { state: 'skipped', label: 'Skip date' },
  ]
  return (
    <div className="mt-m flex flex-wrap items-center gap-m text-on-surface-low text-[0.75rem]">
      {items.map((it) => (
        <span key={it.state} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block size-3 rounded"
            style={{
              background: CELL_TONE[it.state].bg,
              border: '1px solid transparent',
              textDecoration: it.state === 'skipped' ? 'line-through' : undefined,
            }}
          />
          {it.label}
        </span>
      ))}
    </div>
  )
}

/** A local (not UTC) ISO datetime, which is what the endpoint's `start=` expects.
 *
 *  `toISOString()` would send UTC and shift the week by the viewer's offset — for a user west of
 *  Greenwich the grid would start on the previous day. */
function localIso(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`
}

function weekLabel(days: Date[]): string {
  if (days.length === 0) return ''
  const a = days[0], b = days[days.length - 1]
  const sameMonth = a.getMonth() === b.getMonth()
  const fmt = (d: Date, withMonth: boolean) =>
    d.toLocaleDateString(undefined, withMonth ? { month: 'short', day: 'numeric' } : { day: 'numeric' })
  return `${fmt(a, true)} – ${fmt(b, !sameMonth)}`
}
