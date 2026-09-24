import { describe, it, expect } from 'vitest'
import {
  buildWeekGrid,
  cellLabel,
  dayIndex,
  startOfDay,
  visibleHours,
  weekDays,
  weekSummary,
  DAYS_IN_WEEK,
  HOURS_IN_DAY,
} from './weekGrid'
import type { WeekOccurrence } from '../../lib/api'

/** The week grid's placement logic (AUTO-A3 — S81).
 *
 *  Tested here rather than through the component because the hard part is arithmetic: which cell an
 *  epoch lands in, and what the cell says when some of its fires are suppressed. The same split
 *  `runDag.ts` uses.
 */

/** An occurrence at a given local wall-clock time, which is how the grid places them. */
function occ(iso: string, over: Partial<WeekOccurrence> = {}): WeekOccurrence {
  return {
    trigger_id: 'schedule:a',
    trigger_name: 'nightly build',
    at: new Date(iso).getTime() / 1000,
    suppressed_by: '',
    reason: '',
    ...over,
  }
}

const MONDAY = new Date('2026-08-03T00:00:00')

describe('weekDays / dayIndex', () => {
  it('produces seven local-midnight columns', () => {
    const days = weekDays(MONDAY)
    expect(days).toHaveLength(DAYS_IN_WEEK)
    for (const d of days) {
      expect(d.getHours()).toBe(0)
      expect(d.getMinutes()).toBe(0)
    }
    expect(days[0].getDate()).toBe(3)
    expect(days[6].getDate()).toBe(9)
  })

  it('advances by CALENDAR days, not by 86400 seconds', () => {
    // The DST reason the module documents: adding days keeps every column at local midnight, while
    // adding 86400s drifts an hour across a transition and pushes late-night fires into the wrong
    // column. Asserted structurally (all columns at hour 0) because the transition date is
    // zone-dependent and this suite runs in CI's zone.
    const days = weekDays(new Date('2026-03-06T00:00:00'))
    expect(days.every((d) => d.getHours() === 0)).toBe(true)
  })

  it('locates an epoch by calendar date', () => {
    const days = weekDays(MONDAY)
    expect(dayIndex(days, new Date('2026-08-05T13:00:00').getTime() / 1000)).toBe(2)
    expect(dayIndex(days, new Date('2026-08-03T00:00:00').getTime() / 1000)).toBe(0)
  })

  it('returns -1 for an epoch outside the week', () => {
    const days = weekDays(MONDAY)
    expect(dayIndex(days, new Date('2026-08-20T13:00:00').getTime() / 1000)).toBe(-1)
    expect(dayIndex(days, new Date('2026-07-01T13:00:00').getTime() / 1000)).toBe(-1)
  })

  it('startOfDay does not mutate its argument', () => {
    const src = new Date('2026-08-03T15:30:00')
    const out = startOfDay(src)
    expect(src.getHours()).toBe(15)
    expect(out.getHours()).toBe(0)
  })
})

describe('buildWeekGrid', () => {
  it('always emits a full 7x24 grid, empty cells included', () => {
    const grid = buildWeekGrid([], MONDAY)
    expect(grid.cells).toHaveLength(DAYS_IN_WEEK * HOURS_IN_DAY)
    expect(grid.cells.every((c) => c.state === 'empty')).toBe(true)
    expect(grid.totalFires).toBe(0)
  })

  it('places a fire in its local day and hour', () => {
    const grid = buildWeekGrid([occ('2026-08-05T14:30:00')], MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)
    expect(cell).toBeDefined()
    expect(cell?.day).toBe(2)
    expect(cell?.hour).toBe(14)
    expect(cell?.state).toBe('fires')
    expect(cell?.liveCount).toBe(1)
  })

  it('COLLAPSES many fires in one hour into one counted cell', () => {
    // The measured shape of the endpoint: a minutely trigger returns 200 rows, and one mark per row
    // would paint 200 identical squares in the same hour.
    const rows = Array.from({ length: 60 }, (_, i) =>
      occ(`2026-08-05T09:${String(i).padStart(2, '0')}:00`))
    const grid = buildWeekGrid(rows, MONDAY)
    const filled = grid.cells.filter((c) => c.count > 0)
    expect(filled).toHaveLength(1)
    expect(filled[0].count).toBe(60)
    expect(grid.totalFires).toBe(60)
  })

  it('drops occurrences outside the rendered week rather than misplacing them', () => {
    const grid = buildWeekGrid([occ('2026-08-05T09:00:00'), occ('2026-09-01T09:00:00')], MONDAY)
    expect(grid.totalFires).toBe(1)
  })

  it('marks a quiet-suppressed cell distinctly from a skipped one', () => {
    const grid = buildWeekGrid([
      occ('2026-08-04T02:00:00', { suppressed_by: 'quiet', reason: 'quiet hours 22:00–06:00' }),
      occ('2026-08-06T02:00:00', { suppressed_by: 'skipped', reason: 'skip date 2026-08-06' }),
    ], MONDAY)
    const quiet = grid.cells.find((c) => c.day === 1 && c.hour === 2)
    const skipped = grid.cells.find((c) => c.day === 3 && c.hour === 2)
    expect(quiet?.state).toBe('quiet')
    expect(skipped?.state).toBe('skipped')
    expect(grid.suppressedFires).toBe(2)
  })

  it('reports a partially suppressed hour as MIXED, not rounded either way', () => {
    const grid = buildWeekGrid([
      occ('2026-08-05T09:00:00'),
      occ('2026-08-05T09:30:00', { suppressed_by: 'quiet', reason: 'quiet hours 09:15–09:45' }),
    ], MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)
    expect(cell?.state).toBe('mixed')
    expect(cell?.count).toBe(2)
    expect(cell?.liveCount).toBe(1)
  })

  it('prefers SKIPPED over quiet when both apply to the same cell', () => {
    // A struck day is a stronger statement than a quiet hour: reporting "quiet hours" for a date
    // that is skipped anyway sends the user to change the wrong setting.
    const grid = buildWeekGrid([
      occ('2026-08-05T02:00:00', { suppressed_by: 'quiet', reason: 'quiet hours 22:00–06:00' }),
      occ('2026-08-05T02:30:00', { suppressed_by: 'skipped', reason: 'skip date 2026-08-05' }),
    ], MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)
    expect(cell?.state).toBe('skipped')
  })

  it('dedupes triggers by ID, not by name', () => {
    // Nothing forbids two triggers sharing a name, and deduping on the label would collapse them
    // into one entry the user cannot open separately.
    const grid = buildWeekGrid([
      occ('2026-08-05T09:00:00', { trigger_id: 'schedule:a', trigger_name: 'sync' }),
      occ('2026-08-05T09:10:00', { trigger_id: 'schedule:b', trigger_name: 'sync' }),
      occ('2026-08-05T09:20:00', { trigger_id: 'schedule:a', trigger_name: 'sync' }),
    ], MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)
    expect(cell?.triggerIds).toEqual(['schedule:a', 'schedule:b'])
    expect(cell?.triggers).toHaveLength(2)
    expect(cell?.count).toBe(3)
  })

  it('keeps trigger names and ids index-aligned for click-through', () => {
    const grid = buildWeekGrid([
      occ('2026-08-05T09:00:00', { trigger_id: 'schedule:x', trigger_name: 'first' }),
      occ('2026-08-05T09:05:00', { trigger_id: 'schedule:y', trigger_name: 'second' }),
    ], MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)!
    expect(cell.triggers[0]).toBe('first')
    expect(cell.triggerIds[0]).toBe('schedule:x')
    expect(cell.triggers[1]).toBe('second')
    expect(cell.triggerIds[1]).toBe('schedule:y')
  })

  it('falls back to the id when a trigger has no name', () => {
    // A nameless row still has to be openable; rendering an empty label would make the cell a
    // dead end.
    const grid = buildWeekGrid([occ('2026-08-05T09:00:00', { trigger_name: '' })], MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)
    expect(cell?.triggers).toEqual(['schedule:a'])
  })

  it('dedupes suppression reasons', () => {
    const rows = Array.from({ length: 5 }, () =>
      occ('2026-08-04T02:00:00', { suppressed_by: 'quiet', reason: 'quiet hours 22:00–06:00' }))
    const grid = buildWeekGrid(rows, MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)
    expect(cell?.reasons).toEqual(['quiet hours 22:00–06:00'])
  })

  it('tolerates a null occurrence list', () => {
    // The endpoint is reachable before its first response; a grid that threw would blank the tab.
    expect(buildWeekGrid(undefined as unknown as WeekOccurrence[], MONDAY).totalFires).toBe(0)
  })
})

describe('visibleHours', () => {
  it('collapses hours that hold nothing all week', () => {
    const grid = buildWeekGrid([occ('2026-08-05T09:00:00'), occ('2026-08-06T21:00:00')], MONDAY)
    expect(visibleHours(grid)).toEqual([9, 21])
  })

  it('shows every hour when the week is empty', () => {
    // A grid with zero rows reads as broken rather than as empty; the caller renders its own empty
    // state in that case.
    const grid = buildWeekGrid([], MONDAY)
    expect(visibleHours(grid)).toHaveLength(HOURS_IN_DAY)
  })

  it('keeps an hour visible when only a SUPPRESSED fire lands in it', () => {
    // Collapsing it would hide the very thing the user opened the grid to find.
    const grid = buildWeekGrid([
      occ('2026-08-05T03:00:00', { suppressed_by: 'quiet', reason: 'quiet hours 22:00–06:00' }),
    ], MONDAY)
    expect(visibleHours(grid)).toEqual([3])
  })
})

describe('cellLabel', () => {
  const days = weekDays(MONDAY)

  it('names the count and the triggers for a live cell', () => {
    const grid = buildWeekGrid([occ('2026-08-05T09:00:00')], MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)!
    const label = cellLabel(cell, days[cell.day])
    expect(label).toContain('1 fire')
    expect(label).toContain('nightly build')
    expect(label).toContain('09:00')
  })

  it('says WHY for a suppressed cell', () => {
    // The two questions a shaded cell raises are "how many" and "why not"; a label with only a count
    // would make the grid unusable without sight of the colour that carries the rest.
    const grid = buildWeekGrid([
      occ('2026-08-05T02:00:00', { suppressed_by: 'skipped', reason: 'skip date 2026-08-05' }),
    ], MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)!
    const label = cellLabel(cell, days[cell.day])
    expect(label).toContain('all suppressed')
    expect(label).toContain('skip date 2026-08-05')
  })

  it('reports how many of a mixed cell were suppressed', () => {
    const grid = buildWeekGrid([
      occ('2026-08-05T09:00:00'),
      occ('2026-08-05T09:30:00', { suppressed_by: 'quiet', reason: 'quiet hours 09:15–09:45' }),
    ], MONDAY)
    const cell = grid.cells.find((c) => c.count > 0)!
    expect(cellLabel(cell, days[cell.day])).toContain('1 suppressed')
  })

  it('labels an empty cell honestly', () => {
    const grid = buildWeekGrid([], MONDAY)
    expect(cellLabel(grid.cells[0], days[0])).toContain('no fires')
  })

  it('singularizes one fire and pluralizes many', () => {
    const one = buildWeekGrid([occ('2026-08-05T09:00:00')], MONDAY)
    const many = buildWeekGrid([occ('2026-08-05T09:00:00'), occ('2026-08-05T09:10:00')], MONDAY)
    expect(cellLabel(one.cells.find((c) => c.count > 0)!, days[2])).toContain('1 fire:')
    expect(cellLabel(many.cells.find((c) => c.count > 0)!, days[2])).toContain('2 fires')
  })
})

describe('weekSummary', () => {
  it('reports LIVE fires, and suppressed ones separately', () => {
    const grid = buildWeekGrid([
      occ('2026-08-05T09:00:00'),
      occ('2026-08-05T02:00:00', { suppressed_by: 'quiet', reason: 'q' }),
    ], MONDAY)
    expect(weekSummary(grid)).toBe('1 fire this week · 1 suppressed')
  })

  it('omits the suppressed clause when there is none', () => {
    const grid = buildWeekGrid([occ('2026-08-05T09:00:00'), occ('2026-08-06T09:00:00')], MONDAY)
    expect(weekSummary(grid)).toBe('2 fires this week')
  })

  it('says so plainly when nothing is scheduled', () => {
    expect(weekSummary(buildWeekGrid([], MONDAY))).toBe('No scheduled fires this week')
  })
})
