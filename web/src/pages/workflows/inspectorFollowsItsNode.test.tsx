import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { NodeInspect, WorkflowRunDetailData } from '../../lib/api'
import { NodeInspectorDrawer } from './NodeInspectorDrawer'
import { WorkflowRunDetail } from './WorkflowRunDetail'

// ── The node inspector follows its node to the end ───────────────────────────────────────────────
//
// A chat card deep-links its ACTIVE node into the run view (`?node=`), so the inspector opens on a
// running node as a matter of course. The endpoint answers a node still at work with its live state
// (what it was given, nothing produced yet), and the run view hands the drawer what it last saw of
// the node (its latest instance and that instance's state), so the drawer reads again when that
// changes and follows the node from its live state to its final one, without a reload. A failure
// that is not a missing node offers a retry, since a finished run changes no more and nothing else
// would read again.

const workflowRunNodeInspect = vi.fn<(runId: string, nodeId: string) => Promise<NodeInspect>>()
const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const stream: { onSnapshot?: (snap: WorkflowRunDetailData) => void } = {}

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRunNodeInspect: (r: string, n: string) => workflowRunNodeInspect(r, n),
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: async () => ({ continuations: [] }),
    },
  }
})
// The run view's live feed, driven by the test: `onSnapshot` is how a new state reaches the page.
vi.mock('./useWorkflowStream', () => ({
  useWorkflowStream: (_id: string, _live: boolean, handlers: { onSnapshot?: (s: WorkflowRunDetailData) => void }) => {
    stream.onSnapshot = handlers.onSnapshot
    return { connected: true }
  },
}))

function inspect(over: Partial<NodeInspect> = {}): NodeInspect {
  return {
    run_id: 'run-1', node_id: 'draft', instance_path: 'root.draft', state: 'done',
    resolved_prompt: 'Write the intro section.', resolved_inputs: {}, output: 'The intro, in prose.',
    attempts: [], ledger_events: [], cached: false, ...over,
  }
}

function run(draft: string, status = 'running'): WorkflowRunDetailData {
  return {
    run_id: 'run-1', workflow: 'demo', status, spec_version: 1,
    nodes: [
      { instance_path: 'root.plan', node_id: 'plan', state: 'done' },
      { instance_path: 'root.draft', node_id: 'draft', state: draft },
    ],
  } as WorkflowRunDetailData
}

// What the endpoint answers for the node while it is still at work: its live state, no output.
const atWork = () => inspect({ state: 'running', output: null })

beforeEach(() => {
  // Reset, not clear: an answer one test queued and never used must not reach the next one.
  workflowRunNodeInspect.mockReset()
  workflowRun.mockReset()
  stream.onSnapshot = undefined
})

describe('the drawer reads again when its node changes', () => {
  it('opened on a running node, it shows its live state, then its output once it is done', async () => {
    workflowRunNodeInspect.mockResolvedValueOnce(atWork()).mockResolvedValueOnce(inspect())
    const { rerender } = render(
      <NodeInspectorDrawer runId="run-1" nodeId="draft" nodeVersion="root.draft:running" onClose={() => {}} />,
    )
    // While it works: what it was given, and a plain note that its output comes when it finishes.
    expect(await screen.findByTestId('node-live-note')).toHaveTextContent(/still at work/i)
    expect(screen.getByTestId('resolved-prompt')).toHaveTextContent('Write the intro section.')
    expect(screen.getByText('No output yet.')).toBeInTheDocument()
    expect(screen.queryByTestId('output')).not.toBeInTheDocument()

    rerender(<NodeInspectorDrawer runId="run-1" nodeId="draft" nodeVersion="root.draft:done" onClose={() => {}} />)

    expect(await screen.findByTestId('output')).toHaveTextContent('The intro, in prose.')
    expect(screen.queryByTestId('node-live-note')).not.toBeInTheDocument()
    expect(workflowRunNodeInspect).toHaveBeenCalledTimes(2)
  })

  it('keeps what it shows while it reads again', async () => {
    workflowRunNodeInspect.mockResolvedValueOnce(inspect({ output: 'first cycle' }))
    const { rerender } = render(
      <NodeInspectorDrawer runId="run-1" nodeId="draft" nodeVersion="root.draft@0:done" onClose={() => {}} />,
    )
    expect(await screen.findByTestId('output')).toHaveTextContent('first cycle')

    let land: (d: NodeInspect) => void = () => {}
    workflowRunNodeInspect.mockReturnValueOnce(new Promise((res) => { land = res }))
    rerender(<NodeInspectorDrawer runId="run-1" nodeId="draft" nodeVersion="root.draft@1:done" onClose={() => {}} />)

    // No skeleton over content it already has: the first cycle stays until the second lands.
    expect(screen.getByTestId('output')).toHaveTextContent('first cycle')
    await act(async () => { land(inspect({ output: 'second cycle' })) })
    expect(screen.getByTestId('output')).toHaveTextContent('second cycle')
  })

  it('does not read again while the node is unchanged', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect())
    const { rerender } = render(
      <NodeInspectorDrawer runId="run-1" nodeId="draft" nodeVersion="root.draft:done" onClose={() => {}} />,
    )
    await screen.findByTestId('resolved-prompt')
    rerender(<NodeInspectorDrawer runId="run-1" nodeId="draft" nodeVersion="root.draft:done" onClose={() => {}} />)
    expect(workflowRunNodeInspect).toHaveBeenCalledTimes(1)
  })

  it('a failure that is not a missing node offers a retry, and the retry reads again', async () => {
    workflowRunNodeInspect.mockRejectedValueOnce(new Error('Failed to fetch')).mockResolvedValueOnce(inspect())
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" nodeVersion="root.draft:done" onClose={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }))

    expect(await screen.findByTestId('resolved-prompt')).toBeInTheDocument()
    expect(workflowRunNodeInspect).toHaveBeenCalledTimes(2)
  })

  it('a node still at work is no failure: no retry, it reads again by itself', async () => {
    workflowRunNodeInspect.mockResolvedValue(atWork())
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" nodeVersion="root.draft:running" onClose={() => {}} />)
    await screen.findByTestId('node-live-note')
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('the run view hands the drawer what it shows of the node', () => {
  it('a deep link to a running node shows it at work, then its output when the run finishes, without a reload', async () => {
    workflowRun.mockResolvedValue(run('running'))
    workflowRunNodeInspect.mockResolvedValueOnce(atWork()).mockResolvedValueOnce(inspect())
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} deepLinkNodeId="draft" />)
    expect(await screen.findByTestId('node-live-note')).toHaveTextContent(/still at work/i)
    await waitFor(() => expect(stream.onSnapshot).toBeTypeOf('function'))

    act(() => stream.onSnapshot?.(run('done', 'complete')))

    expect(await screen.findByTestId('output')).toHaveTextContent('The intro, in prose.')
    expect(screen.queryByTestId('node-live-note')).not.toBeInTheDocument()
    expect(workflowRunNodeInspect).toHaveBeenCalledTimes(2)
  })
})
