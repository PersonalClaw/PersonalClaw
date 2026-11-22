/**
 * The run page hands its Document panel what changes when a step settles and when the run ends, so
 * the panel reads the run's document again then (`DeliverablePanel`'s `refreshKey`), and says when the
 * run has ended (`ended`).
 *
 * 🔴 The page mounted the panel with the run id alone, and the panel read once: a loop's note a step
 * kept after the page opened did not show until a reload, and an ended run went on saying its worker
 * had not written it yet. Driven through the real page with the api and the stream mocked.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const handed: { refreshKey?: string; ended?: boolean }[] = []
let lifecycle: (() => void) | undefined

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

vi.mock('./useWorkflowStream', () => ({
  useWorkflowStream: (_id: string, _live: boolean, opts?: { onLifecycle?: () => void }) => {
    lifecycle = opts?.onLifecycle
    return { connected: true }
  },
}))
vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({
  DeliverablePanel: (props: { refreshKey?: string; ended?: boolean }) => {
    handed.push({ refreshKey: props.refreshKey, ended: props.ended })
    return null
  },
}))

function run(status: WorkflowRunDetailData['status'], states: string[]): WorkflowRunDetailData {
  return {
    run_id: 'run-1',
    workflow: 'general-project',
    status,
    spec_version: 1,
    loop_kind: 'general',
    title: 'Week plan',
    nodes: states.map((state, i) => ({ instance_path: `root.children[${i}]`, node_id: `s${i}`, state })),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  handed.length = 0
  lifecycle = undefined
})

describe('the run page tells its Document panel when to read again', () => {
  it('🔴 a step settling and the run ending each hand it a new key', async () => {
    workflowRun.mockResolvedValueOnce(run('running', ['running', 'pending']))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    await waitFor(() => expect(handed.length).toBeGreaterThan(0))
    const first = handed[handed.length - 1]
    expect(first.ended).toBe(false)

    workflowRun.mockResolvedValueOnce(run('running', ['done', 'running']))
    lifecycle?.()
    await waitFor(() => expect(handed[handed.length - 1].refreshKey).not.toBe(first.refreshKey))
    const settled = handed[handed.length - 1]

    workflowRun.mockResolvedValueOnce(run('complete', ['done', 'done']))
    lifecycle?.()
    await waitFor(() => expect(handed[handed.length - 1].ended).toBe(true))
    expect(handed[handed.length - 1].refreshKey).not.toBe(settled.refreshKey)
  })

  it('a refetch that settled nothing hands it the same key', async () => {
    workflowRun.mockResolvedValue(run('running', ['running']))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    await waitFor(() => expect(handed.length).toBeGreaterThan(0))
    const first = handed[handed.length - 1].refreshKey
    lifecycle?.()
    await new Promise((r) => setTimeout(r, 400))
    expect(new Set(handed.map((h) => h.refreshKey))).toEqual(new Set([first]))
  })
})
