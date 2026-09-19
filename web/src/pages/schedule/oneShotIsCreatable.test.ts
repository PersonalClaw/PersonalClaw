/** A One-shot schedule must be CREATABLE from the form, not just offered by it.
 *
 * `#530`. The create handler has always accepted the `at` kind — `handlers/triggers.py` builds
 * `{kind:'at', at:float(at_ts), delete_after_run:True}` — but both create surfaces sent the raw
 * `datetime-local` string the picker produces (`"2026-09-20T14:30"`), and `float()` of that raises.
 * So every One-shot submit answered 400 `'at' must be a Unix timestamp in seconds`, for the whole
 * life of the control. The handler was correct and simply unreachable from any form-shaped value,
 * which is why this rail is on the PAYLOAD boundary rather than on the handler.
 *
 * 🔴 THE CONVERSION HAS TO HAPPEN IN THE BROWSER, AND THAT IS THE POINT OF THE ZONE TEST BELOW.
 * A `datetime-local` value carries no zone. Parsing it server-side resolves it in the GATEWAY's OS
 * zone, which is issue 497's live defect on the week-grid window — so "fix it in the handler with
 * `datetime.fromisoformat`" would have made One-shot reachable by minting 497 on a second path.
 * `epochSeconds` resolves it against the browser's own zone (`Date.parse` on a date-TIME form is
 * local), which is the only reading that matches the wall-clock the user typed into the picker.
 */

import { describe, expect, it } from 'vitest'
import { draftToPayload, emptyDraft } from './ScheduleForm'
import { scheduleWhenMet } from './scheduleMeta'

/** A One-shot draft as the form holds it: `at` is whatever `<input type="datetime-local">` set. */
function atDraft(at: string) {
  return { ...emptyDraft(), name: 'Ship the release note', kind: 'at' as const, at }
}

describe('the premise — a datetime-local string is not a timestamp', () => {
  // Asserted rather than assumed: this is the exact coercion the handler performs, and if it ever
  // starts succeeding then the rail below is testing something other than the reported defect.
  it('is what the handler refuses', () => {
    expect(Number.isNaN(Number('2026-09-20T14:30'))).toBe(true)
  })
})

describe('draftToPayload sends a One-shot as epoch seconds', () => {
  it('converts the picker value instead of forwarding it', () => {
    const body = draftToPayload(atDraft('2026-09-20T14:30'))
    expect(typeof body.at, 'the wire field must be a number, not the raw string').toBe('number')
    expect(body.at).not.toBe('2026-09-20T14:30')
  })

  it('resolves it in the BROWSER zone, so it means the wall-clock the user picked', () => {
    // Built from the same local fields the picker shows, so this holds under any TZ the suite
    // runs in — and would FAIL if the value were ever resolved as UTC (the server-side parse).
    const local = new Date(2026, 8, 20, 14, 30, 0, 0)
    const body = draftToPayload(atDraft('2026-09-20T14:30'))
    expect(body.at).toBe(local.getTime() / 1000)
    // The round trip a reader cares about: the instant we send renders back as 14:30 locally.
    const back = new Date((body.at as number) * 1000)
    expect(back.getHours()).toBe(14)
    expect(back.getMinutes()).toBe(30)
  })

  it('is seconds, not milliseconds — the unit the handler declares', () => {
    const body = draftToPayload(atDraft('2026-09-20T14:30'))
    const secs = body.at as number
    // A ms value would be ~1000x larger; pin the magnitude rather than an exact epoch.
    expect(secs).toBeGreaterThan(1_600_000_000)
    expect(secs).toBeLessThan(10_000_000_000)
  })

  it('OMITS `at` when the picker is empty, rather than sending NaN', () => {
    // `JSON.stringify(NaN)` is `null`, which the handler would read as "no `at`" anyway — but via
    // a field it cannot describe. Omitting earns the honest "every, cron, or at required" instead.
    expect('at' in draftToPayload(atDraft(''))).toBe(false)
    expect('at' in draftToPayload(atDraft('not a date'))).toBe(false)
  })

  it('leaves the other kinds alone', () => {
    const every = draftToPayload({ ...emptyDraft(), name: 'n', kind: 'every', intervalValue: 5, intervalUnit: 'm' })
    expect(every.every).toBe(300)
    expect('at' in every).toBe(false)
    const cron = draftToPayload({ ...emptyDraft(), name: 'n', kind: 'cron', cron: '0 9 * * *' })
    expect(cron.cron).toBe('0 9 * * *')
    expect('at' in cron).toBe(false)
  })
})

describe('scheduleWhenMet gates Save so the 400 is never the feedback', () => {
  it('refuses an empty or unreadable One-shot', () => {
    expect(scheduleWhenMet('at', '')).toBe(false)
    expect(scheduleWhenMet('at', 'tomorrow-ish')).toBe(false)
  })
  it('accepts a real one', () => {
    expect(scheduleWhenMet('at', '2026-09-20T14:30')).toBe(true)
  })
  it('never blocks the kinds that cannot be incomplete', () => {
    expect(scheduleWhenMet('every', '')).toBe(true)
    expect(scheduleWhenMet('cron', '')).toBe(true)
  })
})
