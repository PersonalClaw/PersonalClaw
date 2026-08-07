import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ApiError, type NodeInspect, type WorkflowRunDetailData } from '../../lib/api'
import { NodeInspectorDrawer } from './NodeInspectorDrawer'
import { WorkflowRunDetail } from './WorkflowRunDetail'

// ── WV-10: the node-inspector drawer renders the §5 reconstructability set ────────
//
// The drawer fetches `api.workflowRunNodeInspect(runId, nodeId)` ON OPEN and renders the six
// fields WV-9 ships (resolved_prompt / resolved_inputs / output / attempts / ledger_events) plus a
// cached badge. These pins turn on the atom's done-when, from rendered DOM:
//   1. a full payload renders all six fields + the cached badge;
//   2. a `{ref}` prompt and an `{artifact_ref}` output render as monospace chips, NOT code blocks
//      (and never trigger a second fetch — the redaction pass spilled them on purpose);
//   3. a 409 (node not terminal) and a 404 (gone) render a calm inline message, never a crash;
//   4. a terminal node's row in the run view exposes an Inspect trigger that opens the drawer.
//
// The api module is mocked at the boundary — the four api methods the two components call are
// overridden while the REAL ApiError class is kept (the drawer branches on `instanceof ApiError`,
// so a fake class would make the 409/404 discrimination silently fall through to the generic path).

const workflowRunNodeInspect = vi.fn<(runId: string, nodeId: string) => Promise<NodeInspect>>()
const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const workflowContinuations = vi.fn<(id: string) => Promise<{ continuations: [] }>>()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRunNodeInspect: (r: string, n: string) => workflowRunNodeInspect(r, n),
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: (id: string) => workflowContinuations(id),
      workflowRunStreamUrl: () => 'http://localhost/stream',
    },
  }
})

function inspect(over: Partial<NodeInspect> = {}): NodeInspect {
  return {
    run_id: 'run-1', node_id: 'draft', instance_path: 'root', state: 'done',
    resolved_prompt: 'Write the intro section.',
    resolved_inputs: { topic: 'workflows', tone: 'plain' },
    output: 'The intro, in prose.',
    attempts: [{ status: 'ok' }],
    ledger_events: [{ kind: 'step_completed' }, { kind: 'output_written' }],
    cached: true,
    ...over,
  }
}

describe('NodeInspectorDrawer', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches on open and renders all six fields plus the cached badge', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect())
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)

    // the fetch fired for THIS node…
    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('run-1', 'draft'))
    // …and the six reconstructability fields + the badge all landed.
    expect(await screen.findByTestId('resolved-prompt')).toHaveTextContent('Write the intro section.')
    expect(screen.getByTestId('resolved-inputs')).toHaveTextContent('topic')
    expect(screen.getByTestId('output')).toHaveTextContent('The intro, in prose.')
    expect(within(screen.getByTestId('attempts')).getAllByRole('listitem')).toHaveLength(1)
    expect(within(screen.getByTestId('ledger-events')).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByTestId('cached-badge')).toHaveTextContent('cached')
  })

  it('renders "fresh" when the output was not cached', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect({ cached: false }))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)
    expect(await screen.findByTestId('cached-badge')).toHaveTextContent('fresh')
  })

  it('renders a ref chip for an offloaded prompt and an artifact chip for an offloaded output — no code block, no re-fetch', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect({
      resolved_prompt: { ref: 'root::prompt' },
      output: { artifact_ref: 'artifact://big-blob' },
    }))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)

    const chips = await screen.findAllByTestId('ref-chip')
    expect(chips).toHaveLength(2)
    expect(screen.getByText('root::prompt')).toBeInTheDocument()
    expect(screen.getByText('artifact://big-blob')).toBeInTheDocument()
    // the pointers are labels, not fetchers — the inline code blocks are absent for these fields…
    expect(screen.queryByTestId('resolved-prompt')).not.toBeInTheDocument()
    expect(screen.queryByTestId('output')).not.toBeInTheDocument()
    // …and only the single inspect call happened (no blob dereference).
    expect(workflowRunNodeInspect).toHaveBeenCalledTimes(1)
  })

  it('shows a not-terminal message on a 409 without throwing', async () => {
    workflowRunNodeInspect.mockRejectedValue(new ApiError('node not terminal', 409))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)
    expect(await screen.findByText(/has not finished yet/i)).toBeInTheDocument()
    // graceful: no field blocks rendered, but the panel (and its Close) is intact.
    expect(screen.queryByTestId('resolved-prompt')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Close')).toBeInTheDocument()
  })

  it('shows a not-found message on a 404', async () => {
    workflowRunNodeInspect.mockRejectedValue(new ApiError('gone', 404))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)
    expect(await screen.findByText(/could not be found/i)).toBeInTheDocument()
  })
})

describe('WorkflowRunDetail node rows expose the Inspect affordance', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowContinuations.mockResolvedValue({ continuations: [] })
    workflowRunNodeInspect.mockResolvedValue(inspect())
  })

  function runWith(nodeState: string): WorkflowRunDetailData {
    // A COMPLETE (terminal) run does not open an SSE stream, so no EventSource is needed in jsdom.
    return {
      run_id: 'run-1', workflow: 'demo', status: 'complete', spec_version: 1,
      nodes: [{ instance_path: 'root', node_id: 'draft', state: nodeState }],
    }
  }

  it('offers Inspect on a terminal node and clicking it opens the drawer (fetches that node)', async () => {
    workflowRun.mockResolvedValue(runWith('done'))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    const trigger = await screen.findByTitle(/Inspect this node/i)
    fireEvent.click(trigger)

    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('run-1', 'draft'))
    // the drawer's body is now on screen with the fetched fields.
    expect(await screen.findByTestId('node-inspector-body')).toBeInTheDocument()
    expect(await screen.findByTestId('resolved-prompt')).toBeInTheDocument()
  })

  it('does NOT offer Inspect on a non-terminal node (the endpoint would 409)', async () => {
    workflowRun.mockResolvedValue(runWith('running'))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    // the row rendered…
    await waitFor(() => expect(workflowRun).toHaveBeenCalled())
    await screen.findByText('draft')
    // …but a running node carries no Inspect trigger.
    expect(screen.queryByTitle(/Inspect this node/i)).not.toBeInTheDocument()
    expect(workflowRunNodeInspect).not.toHaveBeenCalled()
  })
})
