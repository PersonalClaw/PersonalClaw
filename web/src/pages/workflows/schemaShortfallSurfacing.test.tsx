import { describe, it, expect, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowNodeState, WorkflowRunDetailData } from '../../lib/api'
import { foldEvent, foldSnapshot } from './workflowFold'

// ── issue 3545: a step whose output ignored its declared `schema` has to SAY so ────────────────
//
// The backend half names it (`engine.apply_schema_notice` → `NodeResult.schema_shortfall` → the
// `step_completed` row and the node's REST row). This file is the half that decides whether a user
// ever learns it: a notice that reaches the API and stops there is the same silence one layer down.
//
// The run in the issue shows **12 of 12 steps Done** and escalates on its iteration ceiling, having
// spent six rounds on a worker that never honoured its schema. So the row beside the notice says
// "Done" and the run really did complete — which is exactly why the notice may not be styled as
// dimmed secondary text like the `degraded_reason` line above it, and may not be a failed badge
// either. It is a warning on a successful row.
//
// 🪤 THE FALSE FIX IS TO CARRY THE NOTICE FORWARD. Only the settle knows it, so a re-run after a
// rewind — one that now honours the schema — emits `node_done` with an empty value. Inheriting the
// previous value the way `item_label` deliberately is inherited would leave the row accusing a
// worker that has since complied.
//
// 🪤 THE FOLD READING IT IS NOT A USER SEEING IT. Both rendered assertions below are differential,
// with a vacuity floor on the conforming case: a line rendered unconditionally would satisfy a
// one-sided assertion and would put a warning on every row of every normal run.

const NODES: WorkflowNodeState[] = [
  { instance_path: 'root.triage', node_id: 'triage', state: 'done' },
  { instance_path: 'root.draft', node_id: 'draft', state: 'done' },
]

// The exact text `engine.schema_shortfall` produces for this case, so the fixture is the real copy.
const NOTICE = 'the output ignored its declared schema: 2 of 2 declared keys are missing (tier, why); got answer'

function snapshot(over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData {
  return {
    run_id: 'r1', workflow: 'deep-research', status: 'running', spec_version: 2,
    nodes: NODES.map((n) => ({ ...n })), ...over,
  } as WorkflowRunDetailData
}

describe('the fold carries the schema notice into the node that produced it', () => {
  it('a node_done carrying a shortfall marks that node and leaves its sibling bare', () => {
    const vm = foldEvent(foldSnapshot(snapshot()), 'workflow_node_done', {
      run_id: 'r1', event_id: 'r1-evt-1', seq: 1, epoch: 1,
      instance_path: 'root.triage', node_id: 'triage', status: 'done', schema_shortfall: NOTICE,
    })
    expect(vm.nodes.find((n) => n.instance_path === 'root.triage')?.schema_shortfall).toBe(NOTICE)
    expect(vm.nodes.find((n) => n.instance_path === 'root.draft')?.schema_shortfall).toBeFalsy()
  })

  it('the step is still done — the notice never restates itself as a failure', () => {
    const vm = foldEvent(foldSnapshot(snapshot()), 'workflow_node_done', {
      run_id: 'r1', event_id: 'r1-evt-1', seq: 1, epoch: 1,
      instance_path: 'root.triage', node_id: 'triage', status: 'done', schema_shortfall: NOTICE,
    })
    const node = vm.nodes.find((n) => n.instance_path === 'root.triage')
    expect(node?.state).toBe('done')
    expect(node?.degraded_reason).toBe('')
    expect(node?.failure).toBeNull()
  })

  it('a re-run that now honours the schema CLEARS the notice rather than inheriting it', () => {
    // The false fix, pinned. `item_label` is deliberately carried forward; this must not be.
    const first = foldEvent(foldSnapshot(snapshot()), 'workflow_node_done', {
      run_id: 'r1', event_id: 'r1-evt-1', seq: 1, epoch: 1,
      instance_path: 'root.triage', node_id: 'triage', status: 'done', schema_shortfall: NOTICE,
    })
    expect(first.nodes.find((n) => n.instance_path === 'root.triage')?.schema_shortfall).toBe(NOTICE)
    const second = foldEvent(first, 'workflow_node_done', {
      run_id: 'r1', event_id: 'r1-evt-2', seq: 2, epoch: 1,
      instance_path: 'root.triage', node_id: 'triage', status: 'done',
    })
    expect(second.nodes.find((n) => n.instance_path === 'root.triage')?.schema_shortfall).toBe('')
  })
})

// ── the rendered surface ──────────────────────────────────────────────────────────────────────

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
    render(<WorkflowRunDetail runId="r1" onBack={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

describe('the run view names the ignored schema on the row that produced it', () => {
  it('shows the notice, naming both what was declared and what arrived', async () => {
    await mountRunDetail([{ ...NODES[0], schema_shortfall: NOTICE }, { ...NODES[1] }])
    await waitFor(() => expect(screen.queryAllByTestId('node-schema-shortfall').length).toBe(1))
    const line = screen.getByTestId('node-schema-shortfall')
    // BOTH facts have to be legible without opening anything: the keys the schema asked for and
    // the keys that actually came back. Naming only one sends an author hunting.
    expect(line).toHaveTextContent('tier')
    expect(line).toHaveTextContent('why')
    expect(line).toHaveTextContent('answer')
    // The row is truncated, so the untruncated text has to be reachable.
    expect(line).toHaveAttribute('title', NOTICE)
    // A WARNING on a row that says Done — not the dimmed secondary tone the lines above it use,
    // because the whole defect is that a successful-looking row said nothing.
    expect(line.className).toContain('text-warning')
  })

  it('a run whose output honoured every schema shows no notice at all', async () => {
    // The vacuity floor. This is the arm that decides whether the check is worth having: a line
    // rendered unconditionally would satisfy the assertion above and warn on every normal run.
    await mountRunDetail([{ ...NODES[0] }, { ...NODES[1] }])
    await waitFor(() => expect(screen.queryByText('deep-research')).not.toBeNull())
    expect(screen.queryByTestId('node-schema-shortfall')).toBeNull()
  })
})
