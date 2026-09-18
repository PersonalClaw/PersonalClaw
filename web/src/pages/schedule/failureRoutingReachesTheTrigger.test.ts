/** The failure-routing controls have to reach the server, or not be offered (WF2AUT-15).
 *
 * 🔴 THE DEFECT. The whole failure-delivery contract is BUILT and WIRED on the backend:
 * `Trigger.failure_delivery` (models.py:664, `to_dict` :763, `from_dict` :1072), the
 * `automation_update` PATCH allowlist (triggers/tools.py), outcome-picks-the-route
 * (`delivery.route_for`, called from `gateway._deliver_fire_outcome`), and the opt-in repeat-failure
 * dedup gated on `failure_policy.dedupe_hash` (`gateway._dedupe_repeat_failure`). And NEITHER field
 * existed anywhere a user could see or set one: `git grep -c 'failure_delivery' -- web/src` was
 * **0**. A paid-for capability, unreachable — the config round-trip contract's last clause.
 *
 * These assertions are on the PAYLOAD, not on a rendered control, and the file next door says why:
 * `approvalModeReachesTheAction.test.ts` exists precisely because a field drawn beside the delivery
 * block was drawn, bound, and never sent. A switch that renders is not a switch that persists.
 *
 * 🪤 THE FAKE VERSION of this test asserts `draftToPayload` returns something truthy for
 * `failure_delivery`. That passes while the value is hardcoded, while the toggle is decorative, and
 * while the control cannot express the one route the atom names ('none' beside `delivery: none`). So
 * each case below pins a DISTINCT value that the form must carry through unchanged, including the
 * two falsy ones — 'inherit' (empty string, `route_for`'s fall-back-to-`delivery` branch) and
 * `failure_dedupe: false`.
 */

import { describe, expect, it } from 'vitest'
import { draftToPayload, emptyDraft, toDraft, FAILURE_ROUTES } from './ScheduleForm'
import type { ScheduleJob } from '../../lib/api'

const job = (over: Partial<ScheduleJob> = {}): ScheduleJob =>
  ({ id: 'j', name: 'n', message: '', enabled: true, schedule: 'every 1h', ...over }) as ScheduleJob

describe('the failure route rides the wire', () => {
  it.each(['inbox', 'none', ''] as const)('sends %o exactly as chosen', (route) => {
    const body = draftToPayload({ ...emptyDraft(), failure_delivery: route })
    expect('failure_delivery' in body).toBe(true)
    expect(body.failure_delivery).toBe(route)
  })

  it('rides EVERY exec mode, unlike approval_mode', () => {
    // `failure_delivery` is DELIVERY on the trigger entity, not `invoke-agent` action config, so it
    // has a home on the wire top level whatever the action is. That is the distinction issue 268
    // turned on: `approval_mode` had nowhere to go except inside an invoke-agent action, and sending
    // it anyway is what made it vanish. Getting this backwards for a trigger field would silently
    // strip failure routing from every notify/script/command automation.
    for (const mode of ['agent', 'script', 'command', 'other'] as const) {
      const body = draftToPayload({ ...emptyDraft(), mode, failure_delivery: 'none' })
      expect(body.failure_delivery, `mode=${mode}`).toBe('none')
    }
  })

  it('offers the three routes `delivery.route_for` can actually honour', () => {
    // 🪤 A fourth option would be a control that cannot persist: `route_for` reads
    // `failure_delivery` and falls back to `delivery`, and `delivery.is_muted` recognises exactly
    // 'none'. `channel:<id>` is deliberately absent — the Advanced block's Notify-channel field
    // owns a channel id, and a second place to type one is how two settings start disagreeing.
    expect(FAILURE_ROUTES.map((r) => r.value)).toEqual(['inbox', 'none', ''])
  })
})

describe('the dedupe toggle rides the wire', () => {
  it('sends true when the user opts in', () => {
    const body = draftToPayload({ ...emptyDraft(), failure_dedupe: true })
    expect(body.failure_dedupe).toBe(true)
  })

  it('sends an explicit false when the user opts back out, not nothing', () => {
    // Presence, not truthiness — the rule `approval_mode` established. Turning it OFF is an edit,
    // and omitting the key would leave the stored `dedupe_hash: true` in place, so the user's
    // automation would stay coalesced after they asked it to stop.
    const body = draftToPayload({ ...emptyDraft(), failure_dedupe: false })
    expect('failure_dedupe' in body).toBe(true)
    expect(body.failure_dedupe).toBe(false)
  })

  it('defaults OFF, matching the entity', () => {
    // `Trigger.failure_policy` defaults to `{}`, so dedup is opt-in. A form defaulting it ON would
    // silently mute repeat alerts for every automation created through the UI.
    expect(emptyDraft().failure_dedupe).toBe(false)
    expect(draftToPayload(emptyDraft()).failure_dedupe).toBe(false)
  })
})

describe('the round trip reads back what was written', () => {
  it('carries a served row into the draft', () => {
    const d = toDraft(job({ failure_delivery: 'none', failure_dedupe: true }))
    expect(d.failure_delivery).toBe('none')
    expect(d.failure_dedupe).toBe(true)
  })

  it('a row that inherits the result route reads as inherit, not as inbox', () => {
    // `route_for` treats an empty `failure_delivery` as "use `delivery`". Coercing that to 'inbox'
    // in the browser would show the user a route their trigger does not have, and saving the form
    // would then WRITE the route the UI invented.
    const d = toDraft(job({ failure_delivery: '' }))
    expect(d.failure_delivery).toBe('')
  })

  it('a served row with neither field falls back to the entity defaults', () => {
    const d = toDraft(job())
    expect(d.failure_delivery).toBe('inbox')
    expect(d.failure_dedupe).toBe(false)
  })

  it('survives draft -> payload -> draft without drifting', () => {
    for (const route of FAILURE_ROUTES.map((r) => r.value)) {
      for (const dedupe of [true, false]) {
        const body = draftToPayload({ ...emptyDraft(), failure_delivery: route, failure_dedupe: dedupe })
        const back = toDraft(job({
          failure_delivery: body.failure_delivery as string,
          failure_dedupe: body.failure_dedupe as boolean,
        }))
        expect(back.failure_delivery, `route=${route}`).toBe(route)
        expect(back.failure_dedupe, `dedupe=${dedupe}`).toBe(dedupe)
      }
    }
  })

  it('still sends the delivery fields it is drawn next to', () => {
    // 🪤 The same vacuity floor `approvalModeReachesTheAction.test.ts` carries, for the same reason:
    // "send two more fields" is one keystroke from "rewrite the Advanced block", and Silent / Strict
    // schedule / Skip dates / Timezone are the ones that worked all along.
    const body = draftToPayload({
      ...emptyDraft(),
      silent: true,
      strict_schedule: true,
      skip_dates: ['2027-12-25'],
      timezone: 'America/Los_Angeles',
      failure_delivery: 'inbox',
      failure_dedupe: true,
    })
    expect(body.silent).toBe(true)
    expect(body.strict_schedule).toBe(true)
    expect(body.skip_dates).toEqual(['2027-12-25'])
    expect(body.timezone).toBe('America/Los_Angeles')
  })
})
