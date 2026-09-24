/** Renaming a schedule trigger must not replace its action.
 *
 * Reproduced end to end through the UI in issue 689: create a cron trigger with a **Dashboard
 * Notification** action, open it, change one character of the name, save. HTTP 200, and on disk
 * the action had become a blank `invoke-agent` with an empty `task_template` — the provider's
 * required field. The notification title was gone and the list row read "Invoke Agent".
 *
 * Three links, each individually reasonable:
 *
 *  1. `deriveMode` recognised only `script` and `command` and defaulted everything else to
 *     `'agent'`, ignoring `job.action.provider` — which the wire has always carried.
 *  2. The form therefore rendered agent fields, so `draftToPayload` emitted
 *     `message`/`agent`/`model`.
 *  3. `_scheduleBodyToWire`'s `else` was unconditional, so those fields became an
 *     `invoke-agent` action. The server applies any action it is sent — correctly — and the
 *     notify action was replaced.
 *
 * The server was never at fault: `_update_schedule` only touches the action when one is present
 * in the body (`if "action" in body and isinstance(...)`). So the fix is to stop describing an
 * action the form cannot edit, and the assertions below are on that boundary — the payload and
 * the wire body — because that is where the data was destroyed.
 */

import { describe, expect, it } from 'vitest'
import { deriveMode } from './scheduleMeta'
import { draftToPayload, toDraft } from './ScheduleForm'
import type { ScheduleJob } from '../../lib/api'

/** A cron trigger whose action is a dashboard notification — the issue's own repro. */
function notifyJob(overrides: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: 'sched-1',
    name: 'Photographer — nudge Kaur gallery delivery',
    message: '',
    enabled: true,
    schedule: 'At 10:00 on Wednesday',
    cron_expr: '0 10 * * 3',
    action: {
      provider: 'notify',
      config: { title_template: 'Kaur gallery due — $now', body_template: '', kind: 'info' },
    },
    ...overrides,
  } as ScheduleJob
}

describe('deriveMode reads the action provider', () => {
  it('reports a non-agent provider as other, not agent', () => {
    // The default-to-agent is the first link in the chain: it is what makes the form render a
    // prompt box for a trigger that has no prompt.
    expect(deriveMode(notifyJob())).toBe('other')
  })

  it.each([
    ['invoke-agent', 'agent'],
    ['run-script', 'script'],
    ['bash', 'command'],
  ])('maps %s to %s', (provider, mode) => {
    expect(deriveMode(notifyJob({ action: { provider, config: {} } }))).toBe(mode)
  })

  it('treats a row with no action at all as a legacy agent row', () => {
    // Rows written before the canonical `action` existed must keep resolving, or this fix would
    // strand them in a mode whose form refuses to edit them.
    const legacy = notifyJob()
    delete (legacy as { action?: unknown }).action
    expect(deriveMode(legacy)).toBe('agent')
  })

  it('still honours the legacy script/command fields first', () => {
    expect(deriveMode(notifyJob({ script: 'jobs/x.py:run' }))).toBe('script')
    expect(deriveMode(notifyJob({ command: 'echo hi' }))).toBe('command')
  })
})

describe('the edit payload describes no action it cannot edit', () => {
  it('omits message for a notify trigger, so nothing fabricates an action from it', () => {
    const draft = toDraft(notifyJob())
    expect(draft.mode).toBe('other')

    const body = draftToPayload({ ...draft, name: 'Photographer — nudge Kaur (renamed)' })

    expect('message' in body).toBe(false)
    expect('agent' in body).toBe(false)
    expect('model' in body).toBe(false)
    // The rename itself still rides along — that is the whole operation the user asked for.
    expect(body.name).toBe('Photographer — nudge Kaur (renamed)')
    expect(body.cron).toBe('0 10 * * 3')
  })

  it('still sends message and agent fields in agent mode', () => {
    // The vacuity floor. "Omit the action fields" is one line away from an edit form that can
    // never change an agent prompt again.
    const draft = toDraft(notifyJob({ action: { provider: 'invoke-agent', config: {} } }))
    expect(draft.mode).toBe('agent')

    const body = draftToPayload({ ...draft, message: 'Summarize my unread mail' })

    expect(body.message).toBe('Summarize my unread mail')
    expect('agent' in body).toBe(true)
  })

  it('keeps an EMPTY agent prompt as an agent edit', () => {
    // Presence, not truthiness. An agent trigger may legitimately have an empty prompt, and a
    // truthiness test would have skipped the action update for it — trading one silent
    // data-loss bug for another.
    const draft = toDraft(notifyJob({ action: { provider: 'invoke-agent', config: {} } }))
    const body = draftToPayload({ ...draft, message: '' })
    expect('message' in body).toBe(true)
    expect(body.message).toBe('')
  })
})
