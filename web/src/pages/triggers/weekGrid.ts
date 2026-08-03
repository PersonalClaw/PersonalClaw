import type { WeekOccurrence } from '../../lib/api'

/** The 7×24 week grid's PLACEMENT, as pure functions (AUTO-A3 — S81).
 *
 *  Split from the component for the same reason `runDag.ts` is: the grid's hard part is deciding
 *  which cell an epoch lands in and what that cell says, and none of that needs a DOM to test. The
 *  component draws what these functions return.
 *
 *  **The endpoint returns OCCURRENCES, not cells.** One trigger firing every minute produces 200
 *  rows (its own cap) that collapse into a handful of cells, and a naive one-row-per-fire render
 *  would paint 200 identical marks in the same hour. So the model here is a cell that COUNTS its
 *  fires and names its triggers.
 *
 *  **Local time is the display contract.** The server sends epoch seconds plus its own tz name; the
 *  browser places them with `getDay()`/`getHours()`, which is the viewer's zone. That is deliberate:
 *  the person reading the grid wants to know when their laptop will see the automation run. The
 *  server's zone is shown as a caption so a mismatch is legible rather than silent — a grid that
 *  quietly renumbered the user's hours would be the worst version of this.
 */

/** How a cell is drawn. Suppression reasons stay DISTINCT because they are different promises. */
export type CellState =
  | 'empty'
  /** At least one fire, none suppressed. */
  | 'fires'
  /** Every fire in the cell is inside a quiet window — suppressed, may catch up. */
  | 'quiet'
  /** Every fire in the cell is on a skip date — the whole day is struck, never catches up. */
  | 'skipped'
  /** Some fire, some are suppressed. Its own state so a partially-suppressed hour is not rounded
   *  into either lie. */
  | 'mixed'

export type WeekCell = {
  /** 0-6, Sunday-first, matching `Date.getDay()`. */
  day: number
  /** 0-23 local hour. */
  hour: number
  state: CellState
  /** Total fires in this cell, suppressed included — the number the tooltip shows. */
  count: number
  /** Fires the scheduler will actually run. */
  liveCount: number
  /** Distinct trigger names in this cell, in first-seen order. Names rather than ids: the cell's
   *  tooltip is read by a person, and `schedule:9f2a…` tells them nothing. */
  triggers: string[]
  /** The ids behind `triggers`, same order — what click-through routes on.
   *
   *  Carried alongside the names rather than recovered by the component: a lookup that re-scanned the
   *  occurrence list to turn a name back into an id would break for two triggers sharing a name,
   *  which nothing forbids. Parallel arrays keep "what the cell says" and "where the cell goes"
   *  derived from the same pass. */
  triggerIds: string[]
  /** Distinct suppression reasons, so a shaded cell explains itself. */
  reasons: string[]
}

export type WeekGrid = {
  cells: WeekCell[]
  /** The 7 day columns, as local dates at midnight — the header labels. */
  days: Date[]
  totalFires: number
  suppressedFires: number
  /** Hours that hold nothing all week. Used to COLLAPSE dead rows: a 24-row grid where 17 rows are
   *  empty makes the user scroll past nothing to find their 3am job. */
  emptyHours: number[]
}

export const DAYS_IN_WEEK = 7
export const HOURS_IN_DAY = 24

/** Local midnight of `d`, as a new Date. */
export function startOfDay(d: Date): Date {
  const out = new Date(d)
  out.setHours(0, 0, 0, 0)
  return out
}

/** The grid's 7 day columns, starting from `start`'s local midnight.
 *
 *  Built by adding DAYS to a date rather than 86400s to an epoch, so a DST transition inside the
 *  week does not shift every subsequent column by an hour. */
export function weekDays(start: Date): Date[] {
  const first = startOfDay(start)
  return Array.from({ length: DAYS_IN_WEEK }, (_, i) => {
    const d = new Date(first)
    d.setDate(d.getDate() + i)
    return d
  })
}

/** Which column an epoch belongs to, or -1 when it is outside the week.
 *
 *  Compares CALENDAR DATES rather than epoch arithmetic for the DST reason above: across a spring
 *  transition an "add i×86400s" bucket drifts an hour and a 00:30 fire lands on the previous day. */
export function dayIndex(days: Date[], at: number): number {
  const m = new Date(at * 1000)
  const key = `${m.getFullYear()}-${m.getMonth()}-${m.getDate()}`
  return days.findIndex((d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` === key)
}

function cellState(count: number, live: number, suppressedKinds: Set<string>): CellState {
  if (count === 0) return 'empty'
  if (live === count) return 'fires'
  if (live > 0) return 'mixed'
  // Fully suppressed: report the single reason when there is one, and prefer `skipped` when both
  // apply. A struck day is a stronger statement than a quiet hour, and telling the user "quiet
  // hours" for a date that is skipped anyway sends them to change the wrong setting.
  if (suppressedKinds.has('skipped')) return 'skipped'
  if (suppressedKinds.has('quiet')) return 'quiet'
  return 'fires'
}

/** Fold occurrences into the 7×24 grid. Pure.
 *
 *  Every cell exists in the output, including empty ones: the component renders a fixed grid, and
 *  making it reconstruct absent cells would put placement logic back in the view. */
export function buildWeekGrid(occurrences: WeekOccurrence[], start: Date): WeekGrid {
  const days = weekDays(start)
  const cells: WeekCell[] = []
  const index = new Map<string, WeekCell>()
  const kinds = new Map<string, Set<string>>()
  for (let day = 0; day < DAYS_IN_WEEK; day++) {
    for (let hour = 0; hour < HOURS_IN_DAY; hour++) {
      const cell: WeekCell = { day, hour, state: 'empty', count: 0, liveCount: 0, triggers: [], triggerIds: [], reasons: [] }
      cells.push(cell)
      index.set(`${day}:${hour}`, cell)
      kinds.set(`${day}:${hour}`, new Set())
    }
  }

  let total = 0
  let suppressed = 0
  for (const o of occurrences ?? []) {
    const day = dayIndex(days, o.at)
    if (day < 0) continue  // outside the rendered week — the server may return a wider window
    const hour = new Date(o.at * 1000).getHours()
    const cell = index.get(`${day}:${hour}`)
    if (!cell) continue
    cell.count++
    total++
    if (o.suppressed_by) {
      suppressed++
      kinds.get(`${day}:${hour}`)?.add(o.suppressed_by)
      if (o.reason && !cell.reasons.includes(o.reason)) cell.reasons.push(o.reason)
    } else {
      cell.liveCount++
    }
    // Deduped on the ID, not the name: two triggers may legitimately share a name, and deduping on
    // the label would collapse them into one row the user cannot open separately.
    if (o.trigger_id && !cell.triggerIds.includes(o.trigger_id)) {
      cell.triggerIds.push(o.trigger_id)
      cell.triggers.push(o.trigger_name || o.trigger_id)
    }
  }

  for (const cell of cells) {
    cell.state = cellState(cell.count, cell.liveCount, kinds.get(`${cell.day}:${cell.hour}`) ?? new Set())
  }

  const emptyHours: number[] = []
  for (let hour = 0; hour < HOURS_IN_DAY; hour++) {
    if (cells.every((c) => c.hour !== hour || c.count === 0)) emptyHours.push(hour)
  }

  return { cells, days, totalFires: total, suppressedFires: suppressed, emptyHours }
}

/** The hour rows worth rendering.
 *
 *  Empty rows are collapsed ONLY when there is something to collapse around: for a week with no
 *  fires at all this returns every hour, because a grid with zero rows reads as broken rather than
 *  as empty. The caller shows its own empty state in that case.
 */
export function visibleHours(grid: WeekGrid): number[] {
  const all = Array.from({ length: HOURS_IN_DAY }, (_, h) => h)
  if (grid.totalFires === 0) return all
  return all.filter((h) => !grid.emptyHours.includes(h))
}

/** One cell's tooltip / screen-reader sentence.
 *
 *  Says the COUNT and the reason, because the two questions a shaded cell raises are "how many" and
 *  "why not". An `aria-label` that read only "3" would make the grid unusable without sight of the
 *  colour that carries the rest of the meaning.
 */
export function cellLabel(cell: WeekCell, day: Date): string {
  const when = `${day.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })} ${String(cell.hour).padStart(2, '0')}:00`
  if (cell.count === 0) return `${when} — no fires`
  const names = cell.triggers.join(', ')
  const fires = `${cell.count} fire${cell.count === 1 ? '' : 's'}`
  if (cell.state === 'fires') return `${when} — ${fires}: ${names}`
  const why = cell.reasons.join('; ')
  if (cell.state === 'mixed') return `${when} — ${fires}, ${cell.count - cell.liveCount} suppressed (${why}): ${names}`
  return `${when} — ${fires}, all suppressed (${why}): ${names}`
}

/** Human summary of the week, for the caption above the grid. */
export function weekSummary(grid: WeekGrid): string {
  if (grid.totalFires === 0) return 'No scheduled fires this week'
  const live = grid.totalFires - grid.suppressedFires
  const base = `${live} fire${live === 1 ? '' : 's'} this week`
  return grid.suppressedFires > 0 ? `${base} · ${grid.suppressedFires} suppressed` : base
}
