/** The "Auto-approve tools" switch has to reach the server, or not be offered.
 *
 * Issue 268, found by driving the UI: switch it on, create the trigger, re-open it — off. No error
 * anywhere, and the two switches beside it (Silent, Strict schedule) round-tripped fine, which is
 * what made it look like a per-field omission rather than a broken panel.
 *
 * It was never a backend omission. `approval_mode` is `invoke-agent` action config: `schedule.py`'s
 * `approval_mode` property returns '' for every other provider, and `invoke-agent-action/app.json`
 * declares the field with its own label. The server reads it from `action.config`, and reads it
 * correctly. The loss happened in the browser, in `_scheduleBodyToWire`:
 *
 *   const { message, agent, model, approval_mode, … , action, ...rest } = body
 *   if (action) return { ...rest, action }          // ← everything above is gone
 *
 * `draftToPayload` put `approval_mode` in its unconditional literal, so it went out in every mode,
 * and only the invoke-agent branch had anywhere to put it. The create page was worse: it supplies
 * its own `action`, so it took the early return and the field never left the tab.
 *
 * These assertions are on the payload rather than on a rendered switch because the payload is where
 * the value was destroyed. `tests/test_trigger_wire_field_census.py` holds the structural rule that
 * stops a future field going the same way.
 */

import { describe, expect, it } from 'vitest'
import { draftToPayload, emptyDraft } from './ScheduleForm'

describe('approval_mode rides only the mode that can carry it', () => {
  it('is sent in agent mode, where the wire builds an invoke-agent action', () => {
    const body = draftToPayload({ ...emptyDraft(), mode: 'agent', approval_mode: 'auto' })
    expect(body.approval_mode).toBe('auto')
  })

  it('sends an explicit empty string when the switch is off, not nothing', () => {
    // Presence, not truthiness — the same rule issue 689 established for `message`. Turning the
    // switch OFF is an edit, and omitting the key would leave the stored 'auto' in place.
    const body = draftToPayload({ ...emptyDraft(), mode: 'agent', approval_mode: '' })
    expect('approval_mode' in body).toBe(true)
    expect(body.approval_mode).toBe('')
  })

  it.each(['other', 'script', 'command'] as const)(
    'omits it in %s mode rather than sending a field the wire discards',
    (mode) => {
      const body = draftToPayload({ ...emptyDraft(), mode, approval_mode: 'auto' })
      expect('approval_mode' in body).toBe(false)
    },
  )

  it('still sends the delivery fields it is drawn next to', () => {
    // 🪤 The vacuity floor. "Stop sending approval_mode" is one keystroke from "stop sending the
    // whole Advanced block", and Silent / Strict schedule / Skip dates are the fields that DID
    // round-trip correctly all along. Breaking them to fix their neighbour would be a net loss.
    const body = draftToPayload({
      ...emptyDraft(),
      mode: 'other',
      silent: true,
      strict_schedule: true,
      skip_dates: ['2027-12-25'],
      timezone: 'America/Los_Angeles',
    })
    expect(body.silent).toBe(true)
    expect(body.strict_schedule).toBe(true)
    expect(body.skip_dates).toEqual(['2027-12-25'])
    expect(body.timezone).toBe('America/Los_Angeles')
  })
})
