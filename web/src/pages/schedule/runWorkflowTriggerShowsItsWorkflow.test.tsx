import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import type { ScheduleJob } from '../../lib/api'

// ── A Run workflow trigger's panel names its workflow and the inputs it starts it with ─────────
//
// Measured: a weekly research trigger saved with `{provider: 'run-workflow', config:
// {workflow: 'deep-research', inputs: {question: …}}}` showed "Workflow —" and none of the inputs.
// The panel read `workflow_id`, a key nothing writes; the action saves `workflow`.

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

const QUESTION = 'Which caching layers suit a small Python service, and what does each one cost?'

function job(config: Record<string, unknown>): ScheduleJob {
  return {
    id: 'clock:weekly-research', name: 'Weekly research', message: '', enabled: true,
    schedule: 'At 9:00 AM, only on Monday', every_secs: null, cron_expr: '0 9 * * 1',
    action: { provider: 'run-workflow', config },
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

describe('the Workflow box of a Run workflow trigger', () => {
  it('names the workflow the action saved, and its inputs under it', () => {
    mount(job({ workflow: 'deep-research', inputs: { question: QUESTION, source_budget: 12 } }))
    expect(screen.getByText('deep-research')).toBeTruthy()
    const inputs = screen.getByRole('group', { name: 'The inputs its run starts with' })
    expect(within(inputs).getByText('Question')).toBeTruthy()
    expect(within(inputs).getByText(QUESTION)).toBeTruthy()
    expect(within(inputs).getByText('Source budget')).toBeTruthy()
    expect(within(inputs).getByText('12')).toBeTruthy()
    expect(screen.queryByText('—'), 'the box still says no workflow').toBeNull()
  })

  it('with no inputs, names the workflow and lists nothing under it', () => {
    mount(job({ workflow: 'morning-triage' }))
    expect(screen.getByText('morning-triage')).toBeTruthy()
    expect(screen.queryByRole('group', { name: 'The inputs its run starts with' })).toBeNull()
  })
})
