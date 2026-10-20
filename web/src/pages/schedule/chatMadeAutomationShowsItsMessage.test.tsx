import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ScheduleJob } from '../../lib/api'

// ── An automation made in chat shows the instruction it runs ─────────────────────────────────────
//
// A chat-made automation (`automation_create`, `set_onetime_task`) is a Run Prompt action whose
// instruction rides in `message`, with no saved Prompt. The panel's Prompt box read only the saved
// Prompt, so it told the owner such a trigger would run "loop.md" — a file it never runs now that
// the action runs its message. What the box says is what the action runs, in its own order: the
// saved Prompt, else the message, else loop.md.

const { API } = vi.hoisted(() => ({
  API: {
    triggerHistory: vi.fn(() => Promise.resolve({ runs: [], total: 0, supported: true })),
    channels: vi.fn(() => Promise.resolve([])),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))

const { ScheduleDetail } = await import('./ScheduleDetail')

function job(config: Record<string, unknown>): ScheduleJob {
  return {
    id: 'clock:stretch', name: 'Stretch', message: String(config.message ?? ''), enabled: true,
    schedule: 'at 05:00 PM PDT', every_secs: null, cron_expr: null,
    action: { provider: 'run-prompt', config },
    last_run_ts: null, last_run_status: null, last_status: '', run_count: 0,
    next_run_ts: null, is_running: false, warnings: [], broken: [],
  } as unknown as ScheduleJob
}

function mount(j: ScheduleJob) {
  render(
    <ScheduleDetail job={j} providers={[]} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()}
      editing={false} onEditingChange={vi.fn()} />,
  )
}

describe('the Prompt box names what the Run Prompt action runs', () => {
  it('a chat-made automation shows its message, not loop.md', () => {
    mount(job({ message: 'Remind the owner to stretch.' }))
    expect(screen.getByText('Remind the owner to stretch.')).toBeTruthy()
    expect(screen.queryByText(/loop\.md/)).toBeNull()
  })

  it('a saved Prompt still wins over a message', () => {
    mount(job({ prompt_id: 'daily-standup', message: 'ignored' }))
    expect(screen.getByText('daily-standup')).toBeTruthy()
    expect(screen.queryByText('ignored')).toBeNull()
  })

  it('with neither, it is loop.md', () => {
    mount(job({}))
    expect(screen.getByText(/loop\.md \(default recurring prompt\)/)).toBeTruthy()
  })
})
