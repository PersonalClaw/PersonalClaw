/** A run that started a workflow opens the workflow run's own tile, beside the list (#3816).
 *
 *  The Oct 18 amendment: selecting a running or finished workflow from Automations opens the same
 *  workflow-detail tile the thread's inline card and the session inspector's Background tasks open
 *  (#3820/#3824), `WorkflowProgressCard`, rather than a run view of its own. A "Run workflow"
 *  automation's history row names the run it started (`work_id: workflow:<id>`); opening that row
 *  (`?run=`) shows the tile with the run's live state, and a run that finishes while it is open
 *  keeps the selection and shows its new state.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import type { ScheduleJob, WorkflowRunDetailData } from '../../lib/api'

const { STATE } = vi.hoisted(() => ({
  STATE: { status: 'running', onSnapshot: null as null | ((snap: unknown) => void) },
}))

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
// The run's stream: the test hands the tile the run's next snapshot, as the gateway would.
vi.mock('../workflows/useWorkflowStream', () => ({
  useWorkflowStream: (_id: string, _live: boolean, on: { onSnapshot: (snap: unknown) => void }) => {
    STATE.onSnapshot = on.onSnapshot
    return { connected: true }
  },
}))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<Record<string, unknown>>()
  const run = {
    run_id: 'r-9', job_id: 'weekly', status: 'launched', summary: 'Started the weekly report',
    started_at: Math.floor(Date.now() / 1000) - 120, work_id: 'workflow:wf-42',
  }
  return {
    ...real,
    api: {
      automations: () => Promise.resolve({
        jobs: [{
          id: 'weekly', name: 'Weekly report', message: '', enabled: true, schedule: 'every Monday', state: 'active',
          action: { provider: 'run-workflow', config: { workflow: 'weekly-report' } }, group: 'next_to_run', rank: 0,
          next_run_ts: Math.floor(Date.now() / 1000) + 3600,
        } as unknown as ScheduleJob],
        hooks: [], stores: [], callbacks: [], server_tz: 'UTC', unreadable: [],
      }),
      triggerReview: () => Promise.resolve([]),
      actionProviders: () => Promise.resolve([]),
      autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
      triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
      triggerHistory: () => Promise.resolve({ runs: [run], total: 1 }),
      triggerRunDetail: () => Promise.resolve({ ...run, trace: 'Started wf-42' }),
      channels: () => Promise.resolve([]),
      channelRuntimes: () => Promise.resolve([]),
      workflow: () => new Promise(() => {}),
      workflowRun: () => Promise.resolve(runSnapshot(STATE.status)),
      approvals: () => Promise.resolve([]),
    },
  }
})

function runSnapshot(status: string): WorkflowRunDetailData {
  return {
    run_id: 'wf-42', workflow: 'weekly-report', status, spec_version: 1,
    nodes: [{ instance_path: 'root.draft', node_id: 'draft', state: status === 'running' ? 'running' : 'done' }],
  } as unknown as WorkflowRunDetailData
}

const { TriggersSection } = await import('../triggers/TriggersSection')
const { invalidateKeys } = await import('../../lib/data')

const mount = () => render(
  <TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={{ open: 'schedule:weekly', run: 'r-9' }} setQuery={() => {}} />,
)

beforeEach(() => {
  sessionStorage.clear()
  invalidateKeys('automations:list')
  STATE.status = 'running'
  STATE.onSnapshot = null
})
afterEach(() => cleanup())

describe('a run that started a workflow', () => {
  it('opens the workflow run\'s own tile, the one the thread and the inspector open', async () => {
    mount()
    const detail = await screen.findByRole('region', { name: 'Weekly report' })
    // The tile is the run's live card: its workflow, its state and the way to the run's own page.
    const open = await within(detail).findByRole('link', { name: /Open/ })
    const card = open.closest('div.rounded-xl') as HTMLElement
    expect(card, 'the run card').not.toBeNull()
    // The card draws before the run's read lands, then names the run's workflow.
    expect(await within(card).findByText('weekly-report')).toBeInTheDocument()
    expect(await within(card).findByText(/Running/)).toBeInTheDocument()
    expect(open.getAttribute('href')).toBe('#/workflows/runs/wf-42')
  })

  it('keeps the run open, and shows its new state, when it finishes', async () => {
    mount()
    const detail = await screen.findByRole('region', { name: 'Weekly report' })
    await within(detail).findByText(/Running/)
    await waitFor(() => expect(STATE.onSnapshot).not.toBeNull())
    // The run finishes while its tile is open: its stream brings the ending.
    act(() => STATE.onSnapshot!(runSnapshot('completed')))
    await waitFor(() => expect(within(detail).queryByText(/Running/)).toBeNull())
    expect(within(detail).getByRole('link', { name: /Open/ }).getAttribute('href')).toBe('#/workflows/runs/wf-42')
    expect(within(detail).getByRole('button', { expanded: true, name: /Started the weekly report/ }), 'the run stays open').toBeInTheDocument()
  })
})
