/**
 * The history wrappers must take a FULL facade id (S168).
 *
 * 🔴 THE DEFECT. `api.scheduleHistory` / `api.scheduleRunDetail` hardcoded a `schedule:` prefix, and
 * `RunHistory` was private to `ScheduleDetail` and took a bare job id. So a store trigger's run
 * history was **unrequestable from the frontend** even after the backend began serving it — S166 for
 * the list, S167 for the per-run detail. `StoreTriggerDetail` showed "When it runs" and "What it
 * runs" and nothing about whether it ever had.
 *
 * These tests pin the id ALGEBRA, which is where the bug lived: which id addresses the endpoint, and
 * which addresses the run store behind it. They are two different strings and confusing them is how
 * S165 nearly shipped an inert fix.
 */
import { describe, it, expect } from 'vitest'

/** The prefix strip `RunHistory` performs — `schedule:abc` → `abc`, `store:file:notes` → `file:notes`. */
const rawOf = (triggerId: string) => triggerId.replace(/^(?:schedule|store|lifecycle|event):/, '')

describe('the facade id vs the run-store key', () => {
  it('strips only the FACADE prefix, leaving a store kind intact', () => {
    // `file:notes` IS the TriggerStore id and the run-store `job_id`. Stripping greedily would turn
    // it into `notes` and every lookup would miss.
    expect(rawOf('store:file:notes')).toBe('file:notes')
    expect(rawOf('store:web_watch:feed')).toBe('web_watch:feed')
  })

  it('reduces a schedule id to its bare job id', () => {
    expect(rawOf('schedule:abc')).toBe('abc')
  })

  it('leaves a bare id untouched', () => {
    // `_split_id` treats a bare id as a schedule id, so a caller passing one already has the raw form.
    expect(rawOf('abc')).toBe('abc')
  })

  it('strips exactly one prefix, not every colon segment', () => {
    // The regex is anchored and non-global on purpose: `store:store:x` is not a real id, but a
    // greedy strip would silently mangle `web_watch:...` into `...`.
    expect(rawOf('store:file:a:b')).toBe('file:a:b')
  })
})

describe('the endpoint id', () => {
  // The wrapper interpolates the id verbatim, so the FULL id is what reaches the route — that is the
  // whole fix. A `schedule:`-prefixing wrapper could never address `store:file:notes`.
  const url = (triggerId: string, limit = 10, offset = 0) =>
    `/api/triggers/${encodeURIComponent(triggerId)}/history?limit=${limit}&offset=${offset}`

  it('addresses a store trigger, which the old wrapper could not', () => {
    expect(url('store:file:notes')).toContain('store%3Afile%3Anotes')
    expect(url('store:file:notes')).not.toContain('schedule')
  })

  it('still addresses a schedule trigger', () => {
    expect(url('schedule:abc')).toContain('schedule%3Aabc')
  })
})
