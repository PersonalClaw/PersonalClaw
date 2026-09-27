import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { HeartbeatTask, ScheduleJob } from '../../lib/api'

// ── A HEARTBEAT.md task the owner has not allowed waits, and the panel says which ───────────────
//
// The agent writes HEARTBEAT.md to keep checking on something, and the `system:heartbeat-tasks`
// trigger runs each task unattended. A task the owner has not allowed now waits for their Allow
// (`heartbeat.py`). Measured before this: the trigger's panel showed its cadence and runs and
// nothing about the tasks it ran — so there was no way to see what the agent had queued, and
// nothing to allow.

const { API } = vi.hoisted(() => ({
  API: {
    heartbeatTasks: vi.fn(),
    allowHeartbeatTask: vi.fn(),
    triggerHistory: vi.fn(() => Promise.resolve({ runs: [], total: 0, supported: true })),
    channels: vi.fn(() => Promise.resolve([])),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))

const { ScheduleDetail } = await import('../schedule/ScheduleDetail')

const AGENTS: HeartbeatTask = { text: 'Watch the deploy and tell me when it is green', deliver: 'dashboard:s1', allowed: false }
const OWNERS: HeartbeatTask = { text: 'Water the plants', deliver: '', allowed: true }

function job(provider: string): ScheduleJob {
  return {
    id: 'system:heartbeat-tasks', name: 'Heartbeat tasks', message: '', enabled: true,
    schedule: 'every 60s', every_secs: 60, cron_expr: null,
    action: { provider, config: {} }, created_by: 'system',
    last_run_ts: null, last_status: null, run_count: 0,
    next_run_ts: null, is_running: false, warnings: [], broken: [],
  } as ScheduleJob
}

const props = { providers: [], onSaved: vi.fn(), onDeleted: vi.fn(), onChanged: vi.fn(), editing: false, onEditingChange: vi.fn() }

beforeEach(() => {
  API.heartbeatTasks.mockReset().mockResolvedValue([AGENTS, OWNERS])
  API.allowHeartbeatTask.mockReset().mockResolvedValue({ ok: true })
})

describe('the heartbeat tasks panel', () => {
  it('lists each task with what it may do and where its result goes', async () => {
    render(<ScheduleDetail job={job('heartbeat-tasks')} {...props} />)

    expect(await screen.findByText(AGENTS.text)).toBeInTheDocument()
    expect(screen.getByText('Queued tasks · 2')).toBeInTheDocument()
    expect(screen.getByText(/Waiting for your Allow — it does not run until you allow it/)).toBeInTheDocument()
    expect(screen.getByText(/Allowed — runs with your agent’s tools/)).toBeInTheDocument()
    expect(screen.getByText('dashboard:s1')).toBeInTheDocument()
    // Allow only where there is something to allow.
    expect(screen.getAllByRole('button', { name: 'Allow' })).toHaveLength(1)
  })

  it('Allow sends the task as the panel listed it, and re-reads the queue', async () => {
    render(<ScheduleDetail job={job('heartbeat-tasks')} {...props} />)
    API.heartbeatTasks.mockResolvedValue([{ ...AGENTS, allowed: true }, OWNERS])

    fireEvent.click(await screen.findByRole('button', { name: 'Allow' }))

    await waitFor(() => expect(API.allowHeartbeatTask).toHaveBeenCalledWith(AGENTS.text))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Allow' })).toBeNull())
    expect(API.heartbeatTasks).toHaveBeenCalledTimes(2)
  })

  it('a refusal — the task finished or was edited, or the owner declined — is shown', async () => {
    API.allowHeartbeatTask.mockRejectedValue(new Error('That task is not in HEARTBEAT.md any more: it finished, or it was edited.'))
    render(<ScheduleDetail job={job('heartbeat-tasks')} {...props} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Allow' }))

    expect(await screen.findByText(/not in HEARTBEAT.md any more/)).toBeInTheDocument()
  })

  it('a queue that could not be read says so, rather than "No tasks queued"', async () => {
    API.heartbeatTasks.mockRejectedValue(new Error('gateway down'))
    render(<ScheduleDetail job={job('heartbeat-tasks')} {...props} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('gateway down')
    expect(screen.queryByText(/No tasks queued/)).toBeNull()
  })

  it('an empty queue says what puts a task there', async () => {
    API.heartbeatTasks.mockResolvedValue([])
    render(<ScheduleDetail job={job('heartbeat-tasks')} {...props} />)

    expect(await screen.findByText(/No tasks queued\. Your agent adds one/)).toBeInTheDocument()
  })

  it('is only on the heartbeat trigger', async () => {
    render(<ScheduleDetail job={job('notify')} {...props} />)

    await screen.findByText('No runs recorded yet.')
    expect(API.heartbeatTasks).not.toHaveBeenCalled()
    expect(screen.queryByText(/Queued tasks/)).toBeNull()
  })
})
