/**
 * A run paused at its budget says so beside what it spent, and its Resume raises the cap it
 * reached.
 *
 * The run page showed tokens alone, never a run's caps or its dollars, and a run paused at its
 * budget read as a fault in red with a Resume that paused it again at once: nothing on the page
 * could raise the cap. Its caps and its spend now sit in the run's caption, the pause reads as
 * information, and Resume asks for the cap the run reached before it resumes with it.
 *
 * Driven through the real page with the api mocked at its module boundary, asserting on the call
 * the control makes.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'
import { DialogHost } from '../../ui/dialog/DialogHost'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const resumeWorkflowRun = vi.fn()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
      resumeWorkflowRun: (...args: unknown[]) => resumeWorkflowRun(...args),
    },
  }
})

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

const PAUSED = 'Paused at its dollar budget: $0.12 of $0.10 spent.'
const UNPRICED =
  'Paused at its dollar budget: 1 step ran on a model with no price, so the $1.00 budget cannot count what it spent.'

function capped(over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData {
  return {
    run_id: 'run-1',
    workflow: 'three-calls',
    status: 'paused',
    spec_version: 1,
    error: PAUSED,
    tokens: 240,
    budget: { max_tokens: 0, max_cost: 0.1 },
    spend: { tokens: 240, dollars: 0.12, unpriced_steps: 0 },
    at_budget: true,
    nodes: [{ instance_path: 'root.children[0]', node_id: 'a0', state: 'done' }],
    ...over,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  resumeWorkflowRun.mockResolvedValue({ ok: true, resumed: true })
})

const mount = () =>
  render(
    <>
      <WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />
      <DialogHost />
    </>,
  )

describe('a run paused at its budget', () => {
  it('shows its dollar cap and what it spent beside its tokens', async () => {
    workflowRun.mockResolvedValue(capped())
    mount()
    expect(await screen.findByText('$0.12 of $0.10')).toBeTruthy()
    expect(screen.getByText('240 tokens')).toBeTruthy()
  })

  it('reads as information, not as a fault', async () => {
    workflowRun.mockResolvedValue(capped())
    mount()
    const line = await screen.findByText(PAUSED)
    expect(line.className).toContain('text-on-surface-var')
    expect(line.className).not.toContain('text-danger')
  })

  it('asks for the cap it reached, and resumes with it raised', async () => {
    workflowRun.mockResolvedValue(capped())
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /resume/i }))
    const dialog = await screen.findByRole('dialog')
    const field = within(dialog).getByLabelText('Dollar budget') as HTMLInputElement
    expect(field.value).toBe('0.1')
    fireEvent.change(field, { target: { value: '0.50' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume' }))
    await waitFor(() =>
      expect(resumeWorkflowRun).toHaveBeenCalledWith('run-1', { budget: { max_cost: 0.5 } }),
    )
  })

  it('will not resume with a cap it has already spent', async () => {
    workflowRun.mockResolvedValue(capped())
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /resume/i }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Dollar budget'), { target: { value: '0.11' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume' }))
    expect(await within(dialog).findByText(/It has spent \$0\.12/)).toBeTruthy()
    expect(resumeWorkflowRun).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('goes on past a step no price covered without asking for a cap', async () => {
    workflowRun.mockResolvedValue(capped({
      error: UNPRICED,
      budget: { max_tokens: 0, max_cost: 1 },
      spend: { tokens: 120, dollars: 0, unpriced_steps: 1 },
    }))
    mount()
    expect(await screen.findByText('not counting 1 step that had no price')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /resume/i }))
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-1', {}))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('a run with no budget', () => {
  it('shows its tokens as it did, and what it spent', async () => {
    workflowRun.mockResolvedValue(capped({
      status: 'running',
      error: '',
      at_budget: false,
      budget: { max_tokens: 0, max_cost: 0 },
      spend: { tokens: 900, dollars: 0.3, unpriced_steps: 0 },
    }))
    mount()
    expect(await screen.findByText('900 tokens')).toBeTruthy()
    expect(screen.getByText('$0.30 spent')).toBeTruthy()
  })
})
