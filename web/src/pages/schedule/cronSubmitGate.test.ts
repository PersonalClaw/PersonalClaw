/** An invalid cron expression cannot be SUBMITTED from either surface (#687).
 *
 *  The validator has its own tests (`cronExpr.test.ts`). This module pins the WIRING, which is
 *  where the defect actually lived: the check existed (as a token count) and gated nothing.
 *
 *  * `TriggerCreatePage`'s `canSave` ANDed name + provider + required-config + event-matcher, with
 *    no cron term at all, and `create()` posted `body.cron = sched.cron.trim()` regardless.
 *  * `ScheduleDetail`'s Save gated only on a non-empty name, so an existing 9am automation could be
 *    edited into one that never fires again.
 *
 *  Asserted against the SOURCE for the two `disabled`/`canSave` expressions, because there is no
 *  cheaper way to prove a button is gated on a term than to read the term — and a behavioural
 *  render test of either page needs the whole provider-catalog fetch, which is what made this gap
 *  survive: every existing test of these surfaces exercises the happy path.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { emptyDraft, scheduleDraftInvalidReason, draftToPayload } from './ScheduleForm'

const read = (rel: string) => readFileSync(join(__dirname, rel), 'utf8')

describe('scheduleDraftInvalidReason', () => {
  it('refuses a cron draft croniter would reject', () => {
    expect(scheduleDraftInvalidReason({ ...emptyDraft(), kind: 'cron', cron: '99 99 * * *' }))
      .toMatch(/minute/)
  })

  it('passes a cron draft croniter accepts, macros included', () => {
    // The vacuity partner: an implementation that returned a reason for everything would satisfy
    // the assertion above and disable Save on every schedule.
    for (const cron of ['0 9 * * *', '@daily', '0 9 * * 1-5', '*/15 * * * *']) {
      expect(scheduleDraftInvalidReason({ ...emptyDraft(), kind: 'cron', cron }), cron).toBeNull()
    }
  })

  it('leaves an every/at draft alone even when its cron field holds junk', () => {
    // 🔴 The over-tightening this must not do. `emptyDraft()` seeds `cron: '0 9 * * *'` and the
    // field keeps whatever was last typed when the user switches cadence — so gating on the cron
    // unconditionally would refuse a perfectly good interval schedule.
    const junk = { ...emptyDraft(), cron: 'not a cron at all' }
    expect(scheduleDraftInvalidReason({ ...junk, kind: 'every' })).toBeNull()
    expect(scheduleDraftInvalidReason({ ...junk, kind: 'at', at: '2027-01-01T09:00' })).toBeNull()
    // And the payload for those cadences carries no `cron` key at all, so nothing invalid ships.
    expect(draftToPayload({ ...junk, kind: 'every' })).not.toHaveProperty('cron')
    expect(draftToPayload({ ...junk, kind: 'at', at: '2027-01-01T09:00' })).not.toHaveProperty('cron')
  })
})

/** The whole statement `needle` opens, not the line it sits on.
 *
 *  A line-scoped read would call the gate missing the moment the expression wraps — which is how a
 *  rail ends up forbidding the formatting rather than pinning the behaviour. A `&&` chain can break
 *  on either side of the operator, so continue while THIS line ends in one or the NEXT line opens
 *  with one.
 */
function statement(src: string, needle: string): string {
  const lines = src.split('\n')
  const start = lines.findIndex((l) => l.includes(needle))
  expect(start, `no line opens ${needle}`).toBeGreaterThan(-1)
  const opens = /^\s*(&&|\|\||\?\?|[?:+])/
  const ends = /(&&|\|\||\?\?|[=+([,])\s*$/
  const out = [lines[start]]
  for (let i = start; i + 1 < lines.length; i++) {
    if (!ends.test(lines[i]) && !opens.test(lines[i + 1])) break
    out.push(lines[i + 1])
  }
  return out.join('\n')
}

describe('both submit paths are gated on it', () => {
  it('the create page ANDs it into canSave and names it as the disabled reason', () => {
    const src = read('../triggers/TriggerCreatePage.tsx')
    const canSave = statement(src, 'const canSave =')
    expect(canSave, 'the cron term the defect was missing').toContain('!scheduleReason')
    // The four terms it must not have dropped on the way in.
    for (const term of ['!!name.trim()', '!!provider', 'requiredConfigMet', 'scheduleWhenOk']) {
      expect(canSave, `canSave lost ${term}`).toContain(term)
    }
    expect(src).toContain('scheduleDraftInvalidReason(sched)')
    // The button must SAY why, not just sit dead — the reason is the field's own sentence.
    expect(src).toMatch(/disabledReason[\s\S]{0,600}scheduleReason \? scheduleReason/)
    // And the handler refuses even if something reached it with the button enabled.
    expect(src).toMatch(/if \(scheduleReason\) \{ setErr\(scheduleReason\); return \}/)
  })

  it('the edit panel gates Save on it too, not only on a non-empty name', () => {
    const src = read('./ScheduleDetail.tsx')
    expect(src).toContain('scheduleDraftInvalidReason(draft)')
    // The whole ELEMENT: its props wrap across lines, and the gate is a prop, not a line.
    const open = src.indexOf('onClick={save}')
    expect(open, 'the Save button').toBeGreaterThan(-1)
    const save = src.slice(open, src.indexOf('</Button>', open))
    expect(save, 'the cron term the defect was missing').toContain('!!scheduleReason')
    expect(save, 'the name term it must not have dropped').toContain('!draft.name.trim()')
    expect(src).toMatch(/if \(scheduleReason\) \{ setErr\(scheduleReason\); return \}/)
  })

  it('the cron FIELD renders the same sentence the buttons cite', () => {
    const src = read('./ScheduleForm.tsx')
    expect(src).toContain('cronExprInvalidReason(value)')
    // The token count is gone, not merely bypassed — a dual path here is how the field and the
    // button end up disagreeing about the same expression. Scanned over CODE lines only: the
    // comment above the replacement quotes the old expression to say what it got wrong, and a
    // scanner that reads comments would forbid explaining the defect it exists to prevent.
    const code = src
      .split('\n')
      .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l))
      .join('\n')
    expect(code, 'the old token count').not.toContain('split(/\\s+/).length === 5')
    expect(src, 'the field marks itself invalid for a11y').toContain('aria-invalid={reason')
    expect(src).toContain("aria-describedby={reason ? 'cron-expression-error' : undefined}")
  })
})
