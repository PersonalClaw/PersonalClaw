/**
 * A run incident mode holds says so on its page.
 *
 * While the switch is on, a running run starts no step and makes no model call, and it carries on
 * by itself once the switch is off. Its status stays `running` through the hold, so the page read
 * as working while nothing ran. The run's status carries the sentence (`held`), and the page shows
 * it, as the loop pages already do for a held loop.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
    },
  }
})

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

const HELD =
  'Held: incident mode is on, so this run starts no step and makes no model calls. ' +
  'It carries on by itself once incident mode is turned off.'

function run(over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData {
  return {
    run_id: 'run-1', workflow: 'general-project', status: 'running', spec_version: 1,
    loop_kind: 'general', title: 'Weekly checklist', pause_requested: false,
    nodes: [{ instance_path: 'root.children[0]', node_id: 'work', state: 'pending' }],
    ...over,
  }
}

beforeEach(() => vi.clearAllMocks())

const mount = () => render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)

describe('a held run on its page', () => {
  it('says it is held and why', async () => {
    workflowRun.mockResolvedValue(run({ held: HELD }))
    mount()
    expect(await screen.findByText(HELD)).toBeTruthy()
    expect(screen.getByText(HELD).getAttribute('role')).toBe('status')
  })

  it('its status reads Held, not Running, above the sentence', async () => {
    workflowRun.mockResolvedValue(run({ held: HELD }))
    const { container } = mount()
    await screen.findByText(HELD)
    const pill = container.querySelector('[data-run-status]')
    expect(pill?.textContent?.trim()).toBe('Held')
    // Nothing runs while it is held, so its icon does not spin as a working run's does.
    expect(pill?.querySelector('.animate-spin')).toBeNull()
  })

  it('a run that is not held says nothing of the kind', async () => {
    workflowRun.mockResolvedValue(run())
    const { container } = mount()
    await screen.findByRole('button', { name: /^pause/i })
    expect(screen.queryByText(/incident mode/)).toBeNull()
    expect(container.querySelector('[data-run-status]')?.textContent?.trim()).toBe('Running')
  })
})
