/** Renaming an interval schedule must not rewrite its cadence.
 *
 * The third instance of one defect on one form, and the only one that was still open. Its two
 * siblings already have rails beside this file: #689 (a rename replaced the ACTION —
 * `renameKeepsItsAction.test.ts`) and #268 (`approval_mode` silently DISCARDED). All three are
 * the same sentence: `draftToPayload` sent a field the user never touched.
 *
 * Here the vehicle is a pair of converters that clamp in OPPOSITE directions and are not
 * invertible (`scheduleMeta.ts`):
 *
 *     secsToInterval:  return { value: Math.max(1, Math.round(s / 60)), unit: 'm' }   // 5s   -> 1m
 *     intervalToSecs:  return Math.max(60, Math.round(value) * u.secs)                // 1m   -> 60s
 *
 * `toDraft` opens every existing job through the first and `draftToPayload` re-derived `every`
 * through the second on EVERY save, unconditionally. So editing only the name round-tripped the
 * cadence through both and wrote the result back: **5s became 60s, 3601s became 3600s** (#531).
 * Silent, and caused by an edit that had nothing to do with the schedule.
 *
 * The fix is the narrow one the issue asked for — do not send an untouched field — not making the
 * converters invertible, which would be a larger change and would STILL leave `every` rewritten
 * on every save. So these assertions are on the payload boundary, where the value was lost.
 */

import { describe, expect, it } from 'vitest'
import { draftToPayload, toDraft } from './ScheduleForm'
import { secsToInterval, intervalToSecs } from './scheduleMeta'
import type { ScheduleJob } from '../../lib/api'

/** An interval trigger, cadence in seconds. `every_secs` is what the wire carries. */
function everyJob(secs: number, overrides: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: 'sched-1',
    name: 'Poll the deploy queue',
    message: 'check the queue',
    enabled: true,
    schedule: `every ${secs}s`,
    every_secs: secs,
    action: { provider: 'invoke-agent', config: {} },
    ...overrides,
  } as ScheduleJob
}

describe('the converters really are lossy — the premise of this rail', () => {
  // Asserted rather than assumed: if someone later makes the round trip invertible, this fails
  // and tells them the guard below may be redundant, instead of leaving a rail whose reason
  // silently evaporated.
  //
  // The drift goes in BOTH directions, which is why the issue's own examples are one of each:
  // `5s -> 60s` is the `Math.max(1, …)`/`Math.max(60, …)` floor pushing UP, `3601s -> 3600s`
  // is `Math.round` truncating DOWN, and `90s -> 120s` is `Math.round(1.5) === 2` rounding UP
  // past the value (measured — my first draft of this table guessed 60s and was wrong, so the
  // numbers here are the ones the converters actually produce, not the ones they ought to).
  it.each([
    [5, 60],
    [3601, 3600],
    [90, 120],
  ])('round-trips %is to %is through the display pair', (secs, mangled) => {
    const iv = secsToInterval(secs)
    expect(intervalToSecs(iv.value, iv.unit)).toBe(mangled)
    expect(intervalToSecs(iv.value, iv.unit)).not.toBe(secs)
  })

  it('is faithful for the round cadences the form CAN express', () => {
    for (const secs of [60, 300, 3600, 7200, 86400]) {
      const iv = secsToInterval(secs)
      expect(intervalToSecs(iv.value, iv.unit), `${secs}s survives`).toBe(secs)
    }
  })
})

describe('a name-only edit leaves the cadence exactly as it was', () => {
  it.each([5, 90, 3601, 45])('preserves %is when only the name changed', (secs) => {
    const draft = toDraft(everyJob(secs))
    const body = draftToPayload({ ...draft, name: 'Poll the deploy queue (renamed)' })

    // 🔑 The whole issue in one assertion: the cadence that ships is the one that was there.
    expect(body.every, 'the untouched cadence is sent verbatim').toBe(secs)
    expect(body.name).toBe('Poll the deploy queue (renamed)')
  })

  it('does not depend on the name being what changed — any other field is the same case', () => {
    // The defect was never about names; it was about `every` being re-derived regardless. A
    // channel edit must be just as safe.
    const draft = toDraft(everyJob(5))
    expect(draftToPayload({ ...draft, channel: 'ops' }).every).toBe(5)
    expect(draftToPayload({ ...draft, silent: true }).every).toBe(5)
  })
})

describe('a real cadence edit still writes the user’s new value', () => {
  // The vacuity floor. "Send the original" is one line away from an interval that can never be
  // changed again — which would trade a silent rewrite for a silent refusal.
  it('sends the edited interval when the user changes the value', () => {
    const draft = toDraft(everyJob(3600))          // opens as 1h
    const body = draftToPayload({ ...draft, intervalValue: 6, intervalUnit: 'h' })
    expect(body.every).toBe(6 * 3600)
  })

  it('sends the edited interval when the user changes only the unit', () => {
    const draft = toDraft(everyJob(3600))          // opens as { 1, 'h' }
    const body = draftToPayload({ ...draft, intervalUnit: 'd' })
    expect(body.every).toBe(86400)
  })

  it('clamps a NEW sub-minute value, because that clamp is the backend floor', () => {
    // The 60s floor is right for a value the user just typed; it was only wrong when applied to
    // a value they never touched. Both halves are pinned so neither can be "simplified" away.
    const draft = toDraft(everyJob(3600))
    expect(draftToPayload({ ...draft, intervalValue: 1, intervalUnit: 'm' }).every).toBe(60)
  })

  it('still sends a cadence for a BRAND-NEW schedule, which has no original', () => {
    // `everySecsOriginal` is undefined on a create, so the guard must fall through to the
    // conversion rather than omitting `every` and creating a schedule with no cadence at all.
    const draft = toDraft(everyJob(3600))
    const created = draftToPayload({ ...draft, id: undefined, everySecsOriginal: undefined })
    expect('every' in created, 'a create still carries its cadence').toBe(true)
    expect(created.every).toBe(3600)
  })
})

describe('the other kinds are untouched by this guard', () => {
  it('a cron trigger still sends cron and no every', () => {
    const draft = toDraft(everyJob(3600, { every_secs: undefined, cron_expr: '0 9 * * *' }))
    const body = draftToPayload({ ...draft, kind: 'cron', cron: '0 9 * * *' })
    expect(body.cron).toBe('0 9 * * *')
    expect('every' in body).toBe(false)
  })
})
