import { describe, it, expect, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowNodeState, WorkflowRunDetailData } from '../../lib/api'
import { foldEvent, foldSnapshot } from './workflowFold'

// ── A step a fallback served says "ran on X instead of Y" on its own row ──────────────────────
//
// A step whose bound model could not serve completed on the second entry of the
// user's model chain, and the row and Introspect named only the model that answered — the run read
// as having run on the model it asked for. The backend now records the substitution on the call,
// the ledger row, the node row and the `node_done` event; this is the half a user reads.
//
// Differential with a vacuity floor, for the reason the schema notice's test gives: a line rendered
// unconditionally would pass the one-sided arm and warn on every row of every normal run.

const NODES: WorkflowNodeState[] = [
  { instance_path: 'root.ask', node_id: 'ask', state: 'done' },
  { instance_path: 'root.summarize', node_id: 'summarize', state: 'done' },
]

// The exact wording `ModelSubstitution.sentence` produces, so the fixture is the real copy.
const LINE =
  "ran on fake-oai:fake-model-1 instead of broken-app:model-x: provider 'broken-app' declares type " +
  "'brokenapp', and no installed app registers that type. Install an app that provides 'brokenapp' " +
  "in the App Store, or change 'broken-app''s type in Settings → Providers"

function snapshot(over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData {
  return {
    run_id: 'r1', workflow: 'named-model', status: 'running', spec_version: 1,
    nodes: NODES.map((n) => ({ ...n })), ...over,
  } as WorkflowRunDetailData
}

describe('the fold carries the substitution into the node a fallback served', () => {
  it('marks that node and leaves its sibling bare', () => {
    const vm = foldEvent(foldSnapshot(snapshot()), 'workflow_node_done', {
      run_id: 'r1', event_id: 'r1-evt-1', seq: 1, epoch: 1,
      instance_path: 'root.ask', node_id: 'ask', status: 'done', model_substituted: [LINE],
    })
    expect(vm.nodes.find((n) => n.instance_path === 'root.ask')?.model_substituted).toEqual([LINE])
    expect(vm.nodes.find((n) => n.instance_path === 'root.summarize')?.model_substituted).toBeUndefined()
  })

  it('a re-run on the model it asked for clears the line rather than inheriting it', () => {
    const first = foldEvent(foldSnapshot(snapshot()), 'workflow_node_done', {
      run_id: 'r1', event_id: 'r1-evt-1', seq: 1, epoch: 1,
      instance_path: 'root.ask', node_id: 'ask', status: 'done', model_substituted: [LINE],
    })
    const second = foldEvent(first, 'workflow_node_done', {
      run_id: 'r1', event_id: 'r1-evt-2', seq: 2, epoch: 1,
      instance_path: 'root.ask', node_id: 'ask', status: 'done',
    })
    expect(second.nodes.find((n) => n.instance_path === 'root.ask')?.model_substituted).toBeUndefined()
  })
})

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))

async function mountRunDetail(nodes: WorkflowNodeState[]) {
  vi.resetModules()
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../lib/api')>()
    return {
      ...real,
      api: {
        ...real.api,
        workflowRun: async () => snapshot({ status: 'complete', nodes }),
        workflowContinuations: async () => ({ continuations: [] }),
      },
    }
  })
  const { WorkflowRunDetail } = await import('./WorkflowRunDetail')
  await act(async () => {
    render(<WorkflowRunDetail runId="r1" onBack={() => {}} onOpenRun={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

describe('the run view says it on the row a fallback served', () => {
  it('names the model that answered and the one that was asked for, as a warning', async () => {
    await mountRunDetail([{ ...NODES[0], model_substituted: [LINE] }, { ...NODES[1] }])
    await waitFor(() => expect(screen.queryAllByTestId('node-model-substituted').length).toBe(1))
    const line = screen.getByTestId('node-model-substituted')
    expect(line).toHaveTextContent('ran on fake-oai:fake-model-1 instead of broken-app:model-x')
    expect(line).toHaveAttribute('title', LINE)
    expect(line.className).toContain('text-warning')
  })

  it('a run whose steps got the model they asked for shows no line', async () => {
    await mountRunDetail([{ ...NODES[0] }, { ...NODES[1] }])
    await waitFor(() => expect(screen.queryByText('named-model')).not.toBeNull())
    expect(screen.queryByTestId('node-model-substituted')).toBeNull()
  })
})
