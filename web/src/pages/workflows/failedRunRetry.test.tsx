/**
 * A failed run offers a retry that WORKS, and a draft offers no control that 409s (day-5/6).
 *
 * Measured in the validation: a best-of-n run failed because its provider was down, and the only
 * ways forward the page offered were dead ends. Fork left the user on the parent with a toast
 * naming an id; on the fork's draft, every row offered Rewind / Run from / Edit, and each answered
 * 409 `run_not_live` "start the run before rewind" — re-entry is applied by a LIVE controller, and
 * a draft has none yet. The one path that worked was starting over from the template page.
 *
 * Driven through the real page with the api mocked at its module boundary, asserting on the calls
 * the controls make — a button that renders and calls nothing is the failure mode being fixed.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const forkWorkflowRun = vi.fn()
const startDraftWorkflowRun = vi.fn()
const notify = vi.fn()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
      forkWorkflowRun: (...args: unknown[]) => forkWorkflowRun(...args),
      startDraftWorkflowRun: (...args: unknown[]) => startDraftWorkflowRun(...args),
    },
  }
})

vi.mock('../../app/appSdk', async (importActual) => {
  const actual = await importActual<typeof import('../../app/appSdk')>()
  return { ...actual, notify: (...args: unknown[]) => notify(...args) }
})

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

/** The measured failure, after the blame fix: `sample` failed transient, `select` was skipped. */
function failedRun(retryable: boolean): WorkflowRunDetailData {
  return {
    run_id: 'run-1',
    workflow: 'best-of-n',
    status: 'failed',
    spec_version: 1,
    error: '',
    attention: {
      kind: 'escalation',
      node_id: 'sample',
      reason: 'not_retried',
      detail: 'no candidate: all 2 sampling calls failed — RemoteProtocolError: Server disconnected',
      attempts: [{ attempt: 1, failure_class: retryable ? 'transient' : 'user', error: 'down' }],
    },
    nodes: [
      {
        instance_path: 'root.children[0]',
        node_id: 'sample',
        state: 'failed',
        failure: {
          class: retryable ? 'transient' : 'user',
          cause_plain: 'no candidate: all 2 sampling calls failed',
          retryable,
        },
      },
      { instance_path: 'root.children[1]', node_id: 'select', state: 'skipped' },
    ],
  }
}

function withStatus(status: WorkflowRunDetailData['status']): WorkflowRunDetailData {
  return {
    run_id: 'run-1',
    workflow: 'best-of-n',
    status,
    spec_version: 1,
    nodes: [
      { instance_path: 'root.children[0]', node_id: 'sample', state: 'done' },
      { instance_path: 'root.children[1]', node_id: 'select', state: 'pending' },
    ],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  forkWorkflowRun.mockResolvedValue({ child_run_id: 'child-9', shared_axes: ['a', 'b', 'c'] })
  startDraftWorkflowRun.mockResolvedValue({ ok: true, run_id: 'child-9', started: true })
})

describe('a transient failure offers a retry that works', () => {
  it('Retry forks, starts the child, and lands on it', async () => {
    workflowRun.mockResolvedValue(failedRun(true))
    const onOpenRun = vi.fn()
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={onOpenRun} />)

    fireEvent.click(await screen.findByRole('button', { name: /retry/i }))

    await waitFor(() => expect(onOpenRun).toHaveBeenCalledWith('child-9'))
    expect(forkWorkflowRun).toHaveBeenCalledWith('run-1', expect.objectContaining({ note: expect.any(String) }))
    expect(startDraftWorkflowRun).toHaveBeenCalledWith('child-9')
  })

  it('a refused start still lands on the child, whose Start is the same call', async () => {
    workflowRun.mockResolvedValue(failedRun(true))
    startDraftWorkflowRun.mockRejectedValueOnce(new Error('the workflow supervisor is unavailable'))
    const onOpenRun = vi.fn()
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={onOpenRun} />)

    fireEvent.click(await screen.findByRole('button', { name: /retry/i }))

    await waitFor(() => expect(onOpenRun).toHaveBeenCalledWith('child-9'))
    await waitFor(() =>
      expect(notify).toHaveBeenCalledWith('the workflow supervisor is unavailable', 'error'),
    )
  })

  it('a failure a retry cannot fix offers no Retry', async () => {
    // A USER failure re-runs into the same wall; offering Retry there would promise a fix.
    workflowRun.mockResolvedValue(failedRun(false))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    await screen.findByTestId('escalation-panel')
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
  })

  it('Fork lands on the child draft rather than leaving only a toast', async () => {
    workflowRun.mockResolvedValue(failedRun(false))
    const onOpenRun = vi.fn()
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={onOpenRun} />)

    fireEvent.click(await screen.findByTitle(/Branch a new run from this one/i))

    await waitFor(() => expect(onOpenRun).toHaveBeenCalledWith('child-9'))
    expect(startDraftWorkflowRun).not.toHaveBeenCalled() // a fork is started by its author
  })
})

describe('re-entry controls exist only where a live controller can apply them', () => {
  it('a draft offers Start and no Rewind / Run from / Edit', async () => {
    workflowRun.mockResolvedValue(withStatus('draft'))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    await screen.findByTitle(/Start this run/i)
    expect(screen.queryByTitle(/Re-run this node and everything/i)).toBeNull()
    expect(screen.queryByTitle(/Re-run only what comes after/i)).toBeNull()
    expect(screen.queryByTitle(/Edit this stage's instruction/i)).toBeNull()
  })

  it('a running run still offers them (the control for the assertion above)', async () => {
    workflowRun.mockResolvedValue(withStatus('running'))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    expect((await screen.findAllByTitle(/Re-run this node and everything/i)).length).toBeGreaterThan(0)
  })
})
