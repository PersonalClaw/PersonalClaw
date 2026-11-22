/**
 * A run paused because its earlier token use went unrecorded says why in words, and its Resume goes
 * on with no token budget.
 *
 * Its pause read in red, as a fault, in the engine's own words ("token spend unrecorded; cannot
 * pre-charge token budget"), and its Resume ("the step the pause stopped runs again") paused it
 * again at once: the raise-the-budget form came only for a cap a run had reached, and none had.
 * The gateway now names this pause (`tokens_unrecorded`) and says it in words, in the tone of a
 * budget pause, and Resume asks once before it goes on with no token budget, the one resume that
 * does not pause it again (`run_budget.resume_refusal`).
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

const UNRECORDED = "Its earlier steps didn't record the tokens they used, so its token budget can't be checked. Resume it with no token budget to go on."

function paused(): WorkflowRunDetailData {
  return {
    run_id: 'run-1',
    workflow: 'nightly-digest',
    status: 'paused',
    spec_version: 1,
    error: UNRECORDED,
    tokens: 0,
    budget: { max_tokens: 10000, max_cost: 0 },
    spend: { tokens: 0, dollars: 0, unpriced_steps: 0 },
    at_budget: false,
    tokens_unrecorded: true,
    nodes: [{ instance_path: 'root.children[0]', node_id: 'a0', state: 'done' }],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  resumeWorkflowRun.mockResolvedValue({ ok: true, resumed: true })
  workflowRun.mockResolvedValue(paused())
})

const mount = () =>
  render(
    <>
      <WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />
      <DialogHost />
    </>,
  )

describe('a run paused because its earlier token use went unrecorded', () => {
  it('🔴 reads as information, not as a fault', async () => {
    mount()
    const line = await screen.findByText(UNRECORDED)
    expect(line.className).toContain('text-on-surface-var')
    expect(line.className).not.toContain('text-danger')
  })

  it('🔴 its Resume says it goes on with no token budget, and asks once before it does', async () => {
    mount()
    const resume = await screen.findByRole('button', { name: /resume/i })
    expect(resume.getAttribute('title') ?? '').toMatch(/no token budget/)
    fireEvent.click(resume)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Resume with no token budget?')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume with no token budget' }))
    await waitFor(() =>
      expect(resumeWorkflowRun).toHaveBeenCalledWith('run-1', { budget: { max_tokens: 0 } }),
    )
  })

  it('goes on with nothing when the ask is cancelled', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /resume/i }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(resumeWorkflowRun).not.toHaveBeenCalled()
  })
})
