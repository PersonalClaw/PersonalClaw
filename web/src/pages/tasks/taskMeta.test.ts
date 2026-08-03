import { describe, it, expect } from 'vitest'
import { parseDueDate, dueMeta } from './taskMeta'

/** The LOCAL calendar day `offset` days from today, at midnight. */
const localDay = (offset = 0): Date => {
  const n = new Date()
  return new Date(n.getFullYear(), n.getMonth(), n.getDate() + offset)
}
/** …as the date-only string the backend stores in `Task.due`. */
const dateOnly = (offset = 0): string => {
  const d = localDay(offset)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
const shortLabel = (d: Date) => d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

describe('parseDueDate', () => {
  it('reads a date-only string as LOCAL midnight, not UTC midnight', () => {
    // Date.parse('2026-10-26') is UTC midnight per spec, which is the previous day for
    // every user west of UTC — the whole bug. Assert the local components round-trip.
    const t = parseDueDate('2026-10-26')
    const d = new Date(t)
    expect([d.getFullYear(), d.getMonth(), d.getDate()]).toEqual([2026, 9, 26])
    expect([d.getHours(), d.getMinutes()]).toEqual([0, 0])
  })

  it('passes a full ISO timestamp through to Date.parse', () => {
    expect(parseDueDate('2026-10-26T15:30:00Z')).toBe(Date.parse('2026-10-26T15:30:00Z'))
    expect(parseDueDate('2026-10-26T15:30:00')).toBe(Date.parse('2026-10-26T15:30:00'))
  })

  it('returns NaN for a non-date and for an impossible date-only value', () => {
    expect(Number.isNaN(parseDueDate('not a date'))).toBe(true)
    expect(Number.isNaN(parseDueDate('2026-13-01'))).toBe(true)
    expect(Number.isNaN(parseDueDate('2026-02-30'))).toBe(true)
  })
})

describe('dueMeta', () => {
  it('renders a far-future date-only value on the calendar day it NAMES', () => {
    // The regression: a task due 2026-10-26 showed "Oct 25" west of UTC. Compare against a
    // locally-constructed date so the expectation holds in any timezone.
    const meta = dueMeta('2026-10-26')
    expect(meta?.label).toBe(shortLabel(new Date(2026, 9, 26)))
  })

  it('says "Due today" for a date-only value naming the local today', () => {
    // Must hold for the whole local day, not flip once UTC rolls over.
    expect(dueMeta(dateOnly(0))?.label).toBe('Due today')
  })

  it('says "Due tomorrow" and "Due in Nd" inside the one-week window', () => {
    expect(dueMeta(dateOnly(1))?.label).toBe('Due tomorrow')
    expect(dueMeta(dateOnly(3))?.label).toBe('Due in 3d')
    expect(dueMeta(dateOnly(7))?.label).toBe('Due in 7d')
  })

  it('counts a past date-only value as overdue by whole local days', () => {
    expect(dueMeta(dateOnly(-1))?.label).toBe('1d overdue')
    expect(dueMeta(dateOnly(-5))?.label).toBe('5d overdue')
  })

  it('keeps the urgency tones', () => {
    expect(dueMeta(dateOnly(-1))?.tone).toBe('var(--color-danger)')
    expect(dueMeta(dateOnly(0))?.tone).toBe('var(--color-warn)')
    expect(dueMeta(dateOnly(1))?.tone).toBe('var(--color-warn)')
    expect(dueMeta(dateOnly(3))?.tone).toBe('var(--color-on-surface-var)')
    expect(dueMeta('2026-10-26')?.tone).toBe('var(--color-on-surface-low)')
  })

  it('still handles a full ISO timestamp', () => {
    const iso = new Date(Date.now() + 3 * 86400000).toISOString()
    expect(dueMeta(iso)?.label).toBe('Due in 3d')
  })

  it('falls back to the raw string for garbage, and to null for no due date', () => {
    expect(dueMeta('whenever')).toEqual({ label: 'whenever', tone: 'var(--color-on-surface-low)' })
    expect(dueMeta('')).toBeNull()
    expect(dueMeta(undefined)).toBeNull()
  })
})
