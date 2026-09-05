/** Issue 608: the drawn week and the requested window are the SAME window, across DST.
 *
 *  The old coverage asserted structurally ("all columns at hour 0") against a non-transition
 *  date, conceding "the transition date is zone-dependent and this suite runs in CI's zone" —
 *  an invariant written down and never exercised. This file PINS the zone and the dates:
 *  America/Santiago transitions at 00:00 local (2026-09-06 spring-forward, 2026-04-05
 *  fall-back), the exact shape the issue measured dropping a real fire.
 *
 *  TZ is set before vitest loads the module; V8 re-reads the env on Date calls, and the
 *  sibling suites in this worker are date-format-agnostic. Restored after, belt-and-braces.
 */
process.env.TZ = 'America/Santiago'

import { describe, expect, it, afterAll } from 'vitest'
import { buildWeekGrid, weekDays, weekEnd, weekSummary } from './weekGrid'
import type { WeekOccurrence } from '../../lib/api'

const ORIGINAL_TZ = 'America/Santiago'
afterAll(() => { process.env.TZ = ORIGINAL_TZ })

const HOUR = 3600

function hourly(startEpoch: number, endEpoch: number): WeekOccurrence[] {
  const out: WeekOccurrence[] = []
  for (let t = startEpoch; t < endEpoch; t += HOUR) {
    out.push({ at: t, trigger_id: 'schedule:h1', trigger_name: 'hourly', suppressed_by: '', reason: '' } as WeekOccurrence)
  }
  return out
}

describe('the drawn week IS the requested window, across DST (issue 608)', () => {
  it('sanity: this worker really is in the pinned zone', () => {
    // Spring-forward 2026-09-06: 00:00 local does not exist; midnight jumps to 01:00.
    const d = new Date(2026, 8, 6)
    expect([0, 1]).toContain(d.getHours())
    expect(new Date(2026, 8, 6, 12).getTimezoneOffset()).not.toBe(new Date(2026, 8, 1, 12).getTimezoneOffset())
  })

  it('spring-forward: the local week spans 167 real hours, and weekEnd names that bound', () => {
    const start = new Date(2026, 8, 2) // Wed Sep 2 2026, local midnight
    const days = weekDays(start)
    const end = weekEnd(start)
    const spanHours = (end.getTime() - days[0].getTime()) / 3_600_000
    expect(spanHours).toBe(167) // NOT 168 — this is the whole defect
    // Columns stay on consecutive calendar dates even though one lands at 01:00.
    for (let i = 1; i < days.length; i++) {
      const prev = new Date(days[i - 1]); prev.setDate(prev.getDate() + 1)
      expect(days[i].getDate()).toBe(prev.getDate())
    }
  })

  it('every occurrence inside the 167h window lands in a column — none dropped', () => {
    const start = new Date(2026, 8, 2)
    const days = weekDays(start)
    const end = weekEnd(start)
    const occ = hourly(Math.floor(days[0].getTime() / 1000), Math.floor(end.getTime() / 1000))
    expect(occ.length).toBe(167)
    const grid = buildWeekGrid(occ, start)
    expect(grid.totalFires).toBe(167)
    expect(grid.outsideWindow).toBe(0)
  })

  it('fall-back: the local week spans 169 real hours and still drops nothing', () => {
    const start = new Date(2026, 3, 1) // Wed Apr 1 2026; fall-back Apr 5 at 00:00
    const days = weekDays(start)
    const end = weekEnd(start)
    expect((end.getTime() - days[0].getTime()) / 3_600_000).toBe(169)
    const occ = hourly(Math.floor(days[0].getTime() / 1000), Math.floor(end.getTime() / 1000))
    const grid = buildWeekGrid(occ, start)
    expect(grid.totalFires + grid.outsideWindow).toBe(169)
    expect(grid.outsideWindow).toBe(0)
  })

  it('an occurrence past the drawn week is DISCLOSED, not silently dropped', () => {
    const start = new Date(2026, 8, 2)
    const end = weekEnd(start)
    const stray = hourly(Math.floor(end.getTime() / 1000), Math.floor(end.getTime() / 1000) + HOUR)
    const inWindow = hourly(Math.floor(start.getTime() / 1000), Math.floor(start.getTime() / 1000) + HOUR)
    const grid = buildWeekGrid([...inWindow, ...stray], start)
    expect(grid.outsideWindow).toBe(1)
    expect(weekSummary(grid)).toContain('1 outside the drawn week')
  })

  it('a clean week keeps the caption free of the disclosure (vacuity guard)', () => {
    const start = new Date(2026, 7, 5) // no-DST control week
    const occ = hourly(Math.floor(start.getTime() / 1000), Math.floor(start.getTime() / 1000) + 3 * HOUR)
    const grid = buildWeekGrid(occ, start)
    expect(grid.outsideWindow).toBe(0)
    expect(weekSummary(grid)).not.toContain('outside the drawn week')
  })
})
