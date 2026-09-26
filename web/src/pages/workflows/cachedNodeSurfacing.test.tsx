import { describe, it, expect, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { WorkflowNodeState, WorkflowRunDetailData } from '../../lib/api'
import { foldEvent, foldEvents, foldSnapshot } from './workflowFold'

// ── issue 2769 / WV-10: a cached node has to LOOK different from a re-run one ─────────────────
//
// `WF2-A1` emits `cached` on `workflow_node_done` for one stated reason (`workflows/journal.py`):
// *"did my edit actually re-run anything?" is the first question a user asks after a mid-flight
// edit, and the answer has to come from the ledger, not from reading logs.* The flag reached the
// frontend and stopped: `workflowFold.ts` DECLARED `cached?` on the event envelope, the fold
// dropped it, `WorkflowNodeState` had no such field, and the one badge that existed
// (`NodeInspectorDrawer`) read a different source — the `/inspect` endpoint — from behind a
// per-node drawer. So answering a run-level question meant opening every node in turn.
//
// `WV-10`'s completion clause names the missing half explicitly: *"cached nodes render a visually
// distinct badge (`workflowFold.ts` `cached?` finally read)"*.
//
// 🪤 THE FALSE FIX IS TO CARRY THE FLAG FORWARD. Only a cache HIT publishes `cached`
// (`controller.py` — one of seven `workflow_node_done` sites carries it), so a node that re-runs
// after a mid-flight edit emits `node_done` WITHOUT it. Inheriting the previous value the way
// `item_label` deliberately is inherited would leave the row claiming a cache hit the edit had
// just invalidated — a wrong answer to the only question the flag exists for, and one that reads
// as correct because the badge renders.
//
// 🪤 A FLAG THAT ONLY LIVES IN THE SSE STREAM VANISHES ON RELOAD. The run view does not fold at
// all (it treats a lifecycle event as a refetch cue), so the snapshot payload has to carry
// `cached` too — pinned on the backend in `tests/test_workflow_cached_node_surfacing.py`.
//
// 🪤 THE FOLD READING THE FLAG IS NOT A USER SEEING IT. Two of the four tests below drive the
// rendered surfaces, differentially, with a vacuity floor on the not-cached case — a badge that
// rendered unconditionally would satisfy a one-sided assertion.

// The suite is run both from `web/` (`npm test --workspace web`) and from the repo root, so a
// single cwd-relative path ENOENTs in one of the two. `import.meta.url` is not an option: Vite
// rewrites it to a non-file URL, which `readFileSync` refuses.
const REL = 'src/pages/workflows/workflowFold.ts'
const FOLD_SRC = [join(process.cwd(), REL), join(process.cwd(), 'web', REL)].find(existsSync) ?? REL

const NODES: WorkflowNodeState[] = [
  { instance_path: 'root.plan', node_id: 'plan', state: 'done' },
  { instance_path: 'root.draft', node_id: 'draft', state: 'done' },
]

function snapshot(over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData {
  return {
    run_id: 'r1', workflow: 'deep-research', status: 'running', spec_version: 2,
    nodes: NODES.map((n) => ({ ...n })), ...over,
  } as WorkflowRunDetailData
}

describe('the fold carries cache-origin into the node it belongs to', () => {
  it('a cache-hit node_done marks the node, and the count follows', () => {
    const vm = foldEvent(foldSnapshot(snapshot()), 'workflow_node_done', {
      run_id: 'r1', event_id: 'r1-evt-1', seq: 1, epoch: 1,
      instance_path: 'root.plan', node_id: 'plan', status: 'done', cached: true,
    })
    expect(vm.nodes.find((n) => n.instance_path === 'root.plan')?.cached).toBe(true)
    // The sibling is untouched — a node-keyed patch, not a whole-list rebroadcast.
    expect(vm.nodes.find((n) => n.instance_path === 'root.draft')?.cached).toBeFalsy()
    expect(vm.cachedCount).toBe(1)
  })

  it('a FRESH node_done leaves it unmarked — absence is the answer, not a missing field', () => {
    const vm = foldEvent(foldSnapshot(snapshot()), 'workflow_node_done', {
      run_id: 'r1', event_id: 'r1-evt-1', seq: 1, epoch: 1,
      instance_path: 'root.plan', node_id: 'plan', status: 'done',
    })
    expect(vm.nodes.find((n) => n.instance_path === 'root.plan')?.cached).toBe(false)
    expect(vm.cachedCount).toBe(0)
  })

  it('a re-run CLEARS it — the mid-flight-edit case the flag exists for', () => {
    // The real sequence after editing a late node and resuming: the early node hits the cache,
    // then a rewind bumps its epoch and it genuinely re-runs. The second `node_done` carries no
    // `cached`, because only a hit publishes one.
    const vm = foldEvents(foldSnapshot(snapshot()), [
      { event: 'workflow_node_done', data: { run_id: 'r1', event_id: 'e1', seq: 1, epoch: 1, instance_path: 'root.plan', node_id: 'plan', status: 'done', cached: true } },
      { event: 'workflow_node_started', data: { run_id: 'r1', event_id: 'e2', seq: 2, epoch: 2, instance_path: 'root.plan', node_id: 'plan' } },
      { event: 'workflow_node_done', data: { run_id: 'r1', event_id: 'e3', seq: 3, epoch: 2, instance_path: 'root.plan', node_id: 'plan', status: 'done' } },
    ])
    expect(vm.nodes.find((n) => n.instance_path === 'root.plan')?.cached).toBe(false)
    expect(vm.cachedCount).toBe(0)
  })

  it('a snapshot carries it too, so a page load is not blind to it', () => {
    const vm = foldSnapshot(snapshot({
      nodes: [{ ...NODES[0], cached: true }, { ...NODES[1] }],
    }))
    expect(vm.cachedCount).toBe(1)
    expect(vm.nodes.find((n) => n.instance_path === 'root.plan')?.cached).toBe(true)
  })
})

describe('every field the event envelope declares is read by the fold', () => {
  // The rail this defect class needs, and the shape that would have caught it the moment
  // `cached?` was declared: a field on `WorkflowEventEnvelope` that nothing reads is a value
  // travelling the whole way from the engine to the browser to be discarded. The precedent is
  // `tests/test_workflows_autonomy.py::test_every_interrupt_member_is_produced`, which reads
  // enum names out of the producing function rather than trusting a comment.
  it('declares no field the fold never touches', () => {
    const code = readFileSync(FOLD_SRC, 'utf8')
    const body = code.match(/export interface WorkflowEventEnvelope \{([\s\S]*?)\n\}/)?.[1]
    expect(body, 'the envelope interface must be findable — a rename makes this rail vacuous').toBeTruthy()

    const declared = [...(body ?? '').matchAll(/^ {2}(\w+)\??:/gm)].map((m) => m[1])
    // VACUITY FLOOR: the parse has to have found real fields, including the one this issue is
    // about. A regex that silently matched nothing would make the loop below trivially pass.
    expect(declared.length).toBeGreaterThan(8)
    expect(declared).toContain('cached')
    expect(declared).toContain('degraded_reason')

    for (const field of declared) {
      expect(code, `WorkflowEventEnvelope.${field} is declared but never read — either read it or delete it`)
        .toMatch(new RegExp(`env\\.${field}\\b`))
    }
  })
})

// ── the two rendered surfaces ────────────────────────────────────────────────────────────────

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

describe('the run view marks cached rows', () => {
  it('badges the cached node and leaves the freshly-produced one bare', async () => {
    await mountRunDetail([{ ...NODES[0], cached: true }, { ...NODES[1] }])
    await waitFor(() => expect(screen.queryAllByTestId('node-cached-badge').length).toBe(1))
    const badge = screen.getByTestId('node-cached-badge')
    // The WORD carries the state; the tint only confirms it. Same word and same title text as
    // `NodeInspectorDrawer`'s badge — one vocabulary for one fact.
    expect(badge).toHaveTextContent('cached')
    expect(badge).toHaveAttribute('title', 'Output served from the resume cache')
  })

  it('a run with nothing cached shows no badge at all', async () => {
    // The vacuity floor for the assertion above: a badge rendered unconditionally would have
    // satisfied it, and would put a chip on every row of every normal run.
    await mountRunDetail([{ ...NODES[0] }, { ...NODES[1] }])
    await waitFor(() => expect(screen.queryByText('deep-research')).not.toBeNull())
    expect(screen.queryByTestId('node-cached-badge')).toBeNull()
  })
})

async function mountCard(nodes: WorkflowNodeState[]) {
  vi.resetModules()
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../lib/api')>()
    return {
      ...real,
      api: { ...real.api, workflowRun: async () => snapshot({ status: 'complete', nodes }) },
    }
  })
  const { WorkflowProgressCard } = await import('../chat/WorkflowProgressCard')
  await act(async () => {
    render(<WorkflowProgressCard refObj={{ runId: 'r1', created: true }} />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

describe('the chat card answers the question at the run level', () => {
  it('reports how many steps came from the cache', async () => {
    // A COUNT rather than a per-row chip, because the card renders only the currently-active
    // node and an active node is never a cache hit — a per-row badge here would be dead code.
    await mountCard([{ ...NODES[0], cached: true }, { ...NODES[1], cached: true }])
    await waitFor(() => expect(screen.queryByTestId('run-cached-count')).not.toBeNull())
    expect(screen.getByTestId('run-cached-count')).toHaveTextContent('2 cached')
  })

  it('says nothing when nothing was cached', async () => {
    await mountCard([{ ...NODES[0] }, { ...NODES[1] }])
    await waitFor(() => expect(screen.queryByText('2/2')).not.toBeNull())
    expect(screen.queryByTestId('run-cached-count')).toBeNull()
  })
})
