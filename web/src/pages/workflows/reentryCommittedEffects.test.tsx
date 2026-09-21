import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ApiError, type WorkflowCascadePreview, type WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const workflowContinuations = vi.fn<() => Promise<{ continuations: [] }>>()
const rewindWorkflowRun = vi.fn()
const workflowRunFrom = vi.fn()
const confirmDialog = vi.fn()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => workflowContinuations(),
      rewindWorkflowRun: (...args: unknown[]) => rewindWorkflowRun(...args),
      workflowRunFrom: (...args: unknown[]) => workflowRunFrom(...args),
    },
  }
})

vi.mock('../../ui/dialog', () => ({
  confirm: (...args: unknown[]) => confirmDialog(...args),
  promptForm: vi.fn(),
}))

vi.mock('./useWorkflowStream', () => ({
  useWorkflowStream: () => ({ connected: true }),
}))

vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

const preview: WorkflowCascadePreview = {
  rerun: ['seed', 'work'],
  stale: [],
  skipped: [],
  committed_effects: ['work'],
  needs_confirmation: true,
}

const run: WorkflowRunDetailData = {
  run_id: 'run-1',
  workflow: 'stage-work',
  status: 'running',
  spec_version: 1,
  nodes: [{ instance_path: 'root.children[0]', node_id: 'work', state: 'done' }],
}

function confirmationRequired(): ApiError {
  return new ApiError(
    'cascade confirmation required',
    409,
    'confirmation_required',
    { preview, needs_confirmation: true },
  )
}

describe('workflow re-entry committed-effect previews', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowRun.mockResolvedValue(run)
    workflowContinuations.mockResolvedValue({ continuations: [] })
    confirmDialog.mockResolvedValue(true)
  })

  it('rewind shows the committed stage before resubmitting with confirmation', async () => {
    rewindWorkflowRun
      .mockRejectedValueOnce(confirmationRequired())
      .mockResolvedValueOnce({ ok: true, preview })
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    fireEvent.click(await screen.findByTitle(/Re-run this node and everything/i))

    await waitFor(() => expect(rewindWorkflowRun).toHaveBeenCalledTimes(2))
    expect(rewindWorkflowRun).toHaveBeenNthCalledWith(1, 'run-1', { node_id: 'work' })
    expect(rewindWorkflowRun).toHaveBeenNthCalledWith(
      2,
      'run-1',
      { node_id: 'work', confirm_cascade: true },
    )
    expect(confirmDialog.mock.calls[0][0].body).toMatch(/committed effects \(work\)/)
  })

  it('run-from shows the committed stage before resubmitting with confirmation', async () => {
    workflowRunFrom
      .mockRejectedValueOnce(confirmationRequired())
      .mockResolvedValueOnce({ ok: true, preview })
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    fireEvent.click(await screen.findByTitle(/Re-run only what comes after/i))

    await waitFor(() => expect(workflowRunFrom).toHaveBeenCalledTimes(2))
    expect(workflowRunFrom).toHaveBeenNthCalledWith(1, 'run-1', { node_id: 'work' })
    expect(workflowRunFrom).toHaveBeenNthCalledWith(
      2,
      'run-1',
      { node_id: 'work', confirm_cascade: true },
    )
    expect(confirmDialog.mock.calls[0][0].body).toMatch(/committed effects \(work\)/)
  })
})
