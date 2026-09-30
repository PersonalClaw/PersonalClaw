import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ScheduleJob } from '../../lib/api'
import { localDateTimeInput } from '../../lib/epoch'
import { draftToPayload, toDraft } from './ScheduleForm'
import { ScheduleDetail } from './ScheduleDetail'

// ── A one-shot's edit form opens on its time, and Save sends the time it shows ──────────────────────
//
// Triggers › Edit opened a One-shot with 'Run once at date and time' blank: the draft seeded `at` as
// '' whatever the row held, because the row carried no time for a one-shot at all. A time typed in was
// sent and the gateway threw it away. The row now carries `at_ts` and the draft opens on it; an
// untouched field sends the stored instant back exactly (the picker shows whole minutes), and a
// changed one sends the new time.

// 2026-10-05 13:50:30 UTC — seconds on purpose, the kind a chat-made one-time task carries.
const AT = 1791208230

const JOB: ScheduleJob = {
  id: 'clock:handoff-note', name: 'On-call handoff note', message: '', enabled: true,
  schedule: 'at 09:50 AM EDT, Oct 5', cron_expr: null, every_secs: null, at_ts: AT,
  action: { provider: 'notify', config: { title_template: 'On-call handoff at 10:00, write the note.' } },
  last_run_ts: null, last_run_status: '', last_status: 'ok', run_count: 0,
  next_run_ts: AT, is_running: false, revision: 'r1',
}

/** Off either way the shared Button says it: natively, or `aria-disabled` when it names why. */
function isOff(button: HTMLElement): boolean {
  return button.hasAttribute('disabled') || button.getAttribute('aria-disabled') === 'true'
}

describe("a one-shot's time in the edit form", () => {
  it('opens on the stored time, in the zone the picker shows', () => {
    const draft = toDraft(JOB)
    expect(draft.kind).toBe('at')
    expect(draft.at).toBe(localDateTimeInput(AT))
    // The picker's own reading of that value lands on the stored minute.
    expect(Math.floor(new Date(draft.at).getTime() / 60000)).toBe(Math.floor(AT / 60))
  })

  it('sends the stored instant back when the time is left alone', () => {
    expect(draftToPayload({ ...toDraft(JOB), name: 'Handoff note' }).at).toBe(AT)
  })

  it('sends the new time when it is changed', () => {
    const moved = '2026-09-30T05:25'
    const body = draftToPayload({ ...toDraft(JOB), at: moved })
    expect(body.at).toBe(new Date(moved).getTime() / 1000)
  })

  it('shows the time in the editor, and will not save a one-shot without one', () => {
    const { rerender } = render(
      <ScheduleDetail job={JOB} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()} editing onEditingChange={vi.fn()} />,
    )
    const field = screen.getByLabelText('Run once at date and time') as HTMLInputElement
    expect(field.value).toBe(localDateTimeInput(AT))
    expect(isOff(screen.getByRole('button', { name: /Save/ }))).toBe(false)

    rerender(
      <ScheduleDetail job={{ ...JOB, id: 'clock:no-time', at_ts: null }} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()} editing onEditingChange={vi.fn()} />,
    )
    expect((screen.getByLabelText('Run once at date and time') as HTMLInputElement).value).toBe('')
    expect(isOff(screen.getByRole('button', { name: /Save/ }))).toBe(true)
  })
})

describe('localDateTimeInput', () => {
  it('writes the datetime-local value for a stamp, and nothing for one it cannot read', () => {
    const value = localDateTimeInput(AT)
    expect(value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
    expect(localDateTimeInput(null)).toBe('')
    expect(localDateTimeInput('not a time')).toBe('')
  })
})
