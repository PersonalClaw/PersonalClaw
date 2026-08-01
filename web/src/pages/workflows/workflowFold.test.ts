import { describe, expect, it } from 'vitest'
import { dedupKey, foldEvent, foldEvents, foldSnapshot } from './workflowFold'
import { workflowRefFromTool } from '../chat/WorkflowProgressCard'
import type { WorkflowRunDetailData } from '../../lib/api'

// ── The fold law (WF2-R11) ──────────────────────────────────────────────────
// Folding a run's events over its snapshot must reconstruct exactly the state the server
// would report. That is what makes the live widget trustworthy: if the law holds, a
// reconnect that replays events converges on the same view as a fresh snapshot fetch.
//
// Three guards make the law survive rewind and fork — the operations that break naive live
// widgets. Each is asserted directly, because a guard that silently stops firing looks
// exactly like a guard that was never needed.

const snap = (over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData => ({
  run_id: 'a1b2c3d4',
  workflow: 'triage',
  status: 'running',
  spec_version: 1,
  error: '',
  attention: null,
  tokens: 0,
  elapsed_secs: 0,
  nodes: [
    { instance_path: 'root.children[0]', node_id: 'gather', state: 'done' },
    { instance_path: 'root.children[1]', node_id: 'analyze', state: 'pending' },
  ],
  ...over,
})

const ev = (over: Record<string, unknown> = {}) => ({
  run_id: 'a1b2c3d4', event_id: `evt-${Math.random()}`, seq: 1, epoch: 0, ...over,
})

describe('foldSnapshot', () => {
  it('derives progress from terminal node states', () => {
    const vm = foldSnapshot(snap())
    expect(vm.doneCount).toBe(1)
    expect(vm.totalCount).toBe(2)
    expect(vm.progress).toBe(0.5)
  })

  it('counts every terminal outcome as progress, not just done', () => {
    // The engine's outcomes are wider than done|failed; a `degraded` or `skipped` node has
    // finished, and excluding it would leave a completed run showing 60%.
    const vm = foldSnapshot(snap({
      nodes: [
        { instance_path: 'a', node_id: 'a', state: 'degraded' },
        { instance_path: 'b', node_id: 'b', state: 'skipped' },
        { instance_path: 'c', node_id: 'c', state: 'no_change' },
        { instance_path: 'd', node_id: 'd', state: 'scope_violation' },
      ],
    }))
    expect(vm.doneCount).toBe(4)
    expect(vm.progress).toBe(1)
  })

  it('sorts nodes into instance-path order', () => {
    const vm = foldSnapshot(snap({
      nodes: [
        { instance_path: 'root.children[1]', node_id: 'b', state: 'pending' },
        { instance_path: 'root.children[0]', node_id: 'a', state: 'pending' },
      ],
    }))
    expect(vm.nodes.map((n) => n.node_id)).toEqual(['a', 'b'])
  })

  it('marks a terminal run not-live so nothing subscribes to a closed stream', () => {
    expect(foldSnapshot(snap({ status: 'complete' })).live).toBe(false)
    expect(foldSnapshot(snap({ status: 'running' })).live).toBe(true)
    // needs_input is WAITING, not finished — the stream must stay open for the answer.
    expect(foldSnapshot(snap({ status: 'needs_input' })).live).toBe(true)
  })

  it('a snapshot RESETS dedup and epoch state', () => {
    // Merging into prior state would let a pre-snapshot epoch keep suppressing fresh
    // events — the widget would go permanently silent after a rewind.
    const vm = foldSnapshot(snap())
    expect(vm.epoch).toBe(0)
    expect(vm.seen.size).toBe(0)
    expect(vm.dropped).toBe(0)
  })
})

describe('guard 1 — dedup by deterministic event id', () => {
  it('applying the same event twice changes nothing the second time', () => {
    const vm = foldSnapshot(snap())
    const e = ev({ event_id: 'a1b2c3d4-evt-7', instance_path: 'root.children[1]', node_id: 'analyze', status: 'done' })
    const once = foldEvent(vm, 'workflow_node_done', e)
    const twice = foldEvent(once, 'workflow_node_done', e)
    expect(once.doneCount).toBe(2)
    expect(twice.doneCount).toBe(2)
    expect(twice.dropped).toBe(1)
  })

  it('a reconnect replay of many events is idempotent', () => {
    // The acceptance criterion: sequence-numbered replay after a drop must not double-count.
    const vm = foldSnapshot(snap())
    const events = [
      { event: 'workflow_node_started' as const, data: ev({ event_id: 'e1', instance_path: 'root.children[1]', node_id: 'analyze' }) },
      { event: 'workflow_node_done' as const, data: ev({ event_id: 'e2', instance_path: 'root.children[1]', node_id: 'analyze', status: 'done' }) },
    ]
    const first = foldEvents(vm, events)
    const replayed = foldEvents(first, events)
    expect(replayed.nodes).toEqual(first.nodes)
    expect(replayed.doneCount).toBe(first.doneCount)
  })

  it('an event with no id is still applied — absence is not a duplicate', () => {
    const vm = foldSnapshot(snap())
    const out = foldEvent(vm, 'workflow_node_done', { run_id: 'a1b2c3d4', instance_path: 'root.children[1]', status: 'done' })
    expect(out.doneCount).toBe(2)
  })
})

describe('guard 2 — epoch supersede-drop', () => {
  it('an event from a superseded epoch is DROPPED', () => {
    // The rewind case: this event was in flight when the user reset the region. Applying it
    // would resurrect state they just discarded.
    let vm = foldSnapshot(snap())
    vm = foldEvent(vm, 'workflow_run_update', ev({ event_id: 'e1', epoch: 2, status: 'running' }))
    expect(vm.epoch).toBe(2)

    const stale = foldEvent(vm, 'workflow_node_done', ev({ event_id: 'e2', epoch: 1, instance_path: 'root.children[1]', status: 'done' }))
    expect(stale.doneCount).toBe(1) // unchanged
    expect(stale.dropped).toBe(1)
  })

  it('an event AT the current epoch is applied', () => {
    let vm = foldSnapshot(snap())
    vm = foldEvent(vm, 'workflow_run_update', ev({ event_id: 'e1', epoch: 3, status: 'running' }))
    const out = foldEvent(vm, 'workflow_node_done', ev({ event_id: 'e2', epoch: 3, instance_path: 'root.children[1]', status: 'done' }))
    expect(out.doneCount).toBe(2)
  })

  it('the epoch only ratchets upward', () => {
    let vm = foldSnapshot(snap())
    vm = foldEvent(vm, 'workflow_run_update', ev({ event_id: 'e1', epoch: 5, status: 'running' }))
    vm = foldEvent(vm, 'workflow_run_update', ev({ event_id: 'e2', epoch: 2, status: 'running' }))
    expect(vm.epoch).toBe(5)
  })

  it('an event with no epoch inherits the current one rather than being dropped', () => {
    // A payload that predates the envelope must still work — dropping it would make the
    // widget go silent against an older gateway.
    let vm = foldSnapshot(snap())
    vm = foldEvent(vm, 'workflow_run_update', ev({ event_id: 'e1', epoch: 4, status: 'running' }))
    const out = foldEvent(vm, 'workflow_node_done', { run_id: 'a1b2c3d4', event_id: 'e2', instance_path: 'root.children[1]', status: 'done' })
    expect(out.doneCount).toBe(2)
  })
})

describe('guard 3 — node-keyed patches', () => {
  it('a node event patches ONE node, leaving siblings untouched', () => {
    const vm = foldSnapshot(snap())
    const out = foldEvent(vm, 'workflow_node_done', ev({ event_id: 'e1', instance_path: 'root.children[1]', node_id: 'analyze', status: 'failed' }))
    expect(out.nodes.find((n) => n.node_id === 'analyze')?.state).toBe('failed')
    expect(out.nodes.find((n) => n.node_id === 'gather')?.state).toBe('done')
  })

  it('two concurrent completions do not clobber each other', () => {
    // The fan-out case: a whole-list rebroadcast would make the second event's payload win
    // and erase the first node's result.
    const vm = foldSnapshot(snap({
      nodes: [
        { instance_path: 'root.children[0]#0', node_id: 'item', state: 'running' },
        { instance_path: 'root.children[0]#1', node_id: 'item', state: 'running' },
      ],
    }))
    let out = foldEvent(vm, 'workflow_node_done', ev({ event_id: 'e1', instance_path: 'root.children[0]#0', node_id: 'item', status: 'done' }))
    out = foldEvent(out, 'workflow_node_done', ev({ event_id: 'e2', instance_path: 'root.children[0]#1', node_id: 'item', status: 'done' }))
    expect(out.doneCount).toBe(2)
  })

  it('a node the snapshot never had is appended in path order', () => {
    // A foreach that expands mid-run produces instances the snapshot could not contain.
    const vm = foldSnapshot(snap())
    const out = foldEvent(vm, 'workflow_node_started', ev({ event_id: 'e1', instance_path: 'root.children[0]#5', node_id: 'item' }))
    expect(out.totalCount).toBe(3)
    expect(out.nodes.map((n) => n.instance_path)).toEqual([
      'root.children[0]', 'root.children[0]#5', 'root.children[1]',
    ])
  })

  it('an event with no instance path is a no-op, not a corruption', () => {
    const vm = foldSnapshot(snap())
    const out = foldEvent(vm, 'workflow_node_done', ev({ event_id: 'e1', status: 'done' }))
    expect(out.nodes).toEqual(vm.nodes)
  })

  it('a progress tick preserves richer per-node detail', () => {
    // The tick carries only path+state; letting it overwrite would erase a failure reason
    // the snapshot had.
    const vm = foldSnapshot(snap({
      nodes: [{ instance_path: 'a', node_id: 'a', state: 'failed', failure: { cause_plain: 'boom' } }],
    }))
    const out = foldEvent(vm, 'workflow_progress', ev({ event_id: 'e1', nodes: [{ instance_path: 'a', node_id: 'a', state: 'failed' }] }))
    expect(out.nodes[0].failure?.cause_plain).toBe('boom')
  })
})

describe('guard 4 — per-node seq ordering', () => {
  // Found by folding REAL captured frames out of order: SSE delivery order is not guaranteed
  // across a reconnect, and a late `node_started` arriving after that node's `node_done`
  // regressed the widget from Done back to Running. `seq` was in the envelope but unused.

  it('a late event for a node that already advanced is dropped', () => {
    const vm = foldSnapshot(snap())
    const done = foldEvent(vm, 'workflow_node_done', ev({ event_id: 'e2', seq: 12, instance_path: 'root.children[1]', node_id: 'analyze', status: 'done' }))
    const late = foldEvent(done, 'workflow_node_started', ev({ event_id: 'e1', seq: 11, instance_path: 'root.children[1]', node_id: 'analyze' }))
    expect(late.nodes.find((n) => n.node_id === 'analyze')?.state).toBe('done')
    expect(late.dropped).toBe(1)
  })

  it('the guard is PER NODE, so a sibling arriving second still applies', () => {
    // A global seq floor would drop a legitimate sibling event that merely arrived later —
    // two different nodes' events are genuinely independent.
    const vm = foldSnapshot(snap({
      nodes: [
        { instance_path: 'a', node_id: 'a', state: 'running' },
        { instance_path: 'b', node_id: 'b', state: 'running' },
      ],
    }))
    const first = foldEvent(vm, 'workflow_node_done', ev({ event_id: 'e1', seq: 20, instance_path: 'a', node_id: 'a', status: 'done' }))
    const second = foldEvent(first, 'workflow_node_done', ev({ event_id: 'e2', seq: 19, instance_path: 'b', node_id: 'b', status: 'done' }))
    expect(second.doneCount).toBe(2)
  })

  it('an event with no seq is still applied', () => {
    const vm = foldSnapshot(snap())
    const out = foldEvent(vm, 'workflow_node_done', { run_id: 'a1b2c3d4', event_id: 'e1', instance_path: 'root.children[1]', status: 'done' })
    expect(out.doneCount).toBe(2)
  })
})

describe('run-level folding', () => {
  it('a run update flips live and needsInput together', () => {
    const vm = foldSnapshot(snap())
    const blocked = foldEvent(vm, 'workflow_run_update', ev({ event_id: 'e1', status: 'needs_input' }))
    expect(blocked.needsInput).toBe(true)
    expect(blocked.live).toBe(true) // waiting, not finished
    const done = foldEvent(blocked, 'workflow_run_update', ev({ event_id: 'e2', status: 'complete' }))
    expect(done.needsInput).toBe(false)
    expect(done.live).toBe(false)
  })

  it('a terminal run clears attention so no dead ask card renders', () => {
    let vm = foldSnapshot(snap())
    vm = foldEvent(vm, 'workflow_attention', ev({ event_id: 'e1', ask: { prompt: 'ok?' } }))
    expect(vm.attention).toEqual({ prompt: 'ok?' })
    vm = foldEvent(vm, 'workflow_run_update', ev({ event_id: 'e2', status: 'complete' }))
    expect(vm.attention).toBeNull()
  })

  it('gate_resolved clears the ask immediately', () => {
    // Waiting for the next snapshot would leave the answered card on screen.
    let vm = foldSnapshot(snap())
    vm = foldEvent(vm, 'workflow_needs_input', ev({ event_id: 'e1', ask: { prompt: 'ship?' } }))
    vm = foldEvent(vm, 'workflow_gate_resolved', ev({ event_id: 'e2', instance_path: 'root.children[1]', approved: true }))
    expect(vm.attention).toBeNull()
    expect(vm.nodes.find((n) => n.instance_path === 'root.children[1]')?.state).toBe('done')
  })

  it('an event for a DIFFERENT run is dropped', () => {
    const vm = foldSnapshot(snap())
    const out = foldEvent(vm, 'workflow_node_done', ev({ event_id: 'e1', run_id: 'other', instance_path: 'root.children[1]', status: 'done' }))
    expect(out.doneCount).toBe(1)
    expect(out.dropped).toBe(1)
  })

  it('fork and mutation_rejected leave THIS run alone', () => {
    // A fork creates a sibling; a rejected mutation by definition applied nothing.
    const vm = foldSnapshot(snap())
    const forked = foldEvent(vm, 'workflow_forked', ev({ event_id: 'e1', child_run_id: 'zz' }))
    expect(forked.nodes).toEqual(vm.nodes)
    expect(forked.status).toBe(vm.status)
    const rejected = foldEvent(vm, 'workflow_mutation_rejected', ev({ event_id: 'e2', issues: [] }))
    expect(rejected.nodes).toEqual(vm.nodes)
  })

  it('spec_updated tracks the version for expect_version', () => {
    const vm = foldSnapshot(snap())
    const out = foldEvent(vm, 'workflow_spec_updated', ev({ event_id: 'e1', spec_version: 4 }))
    expect(out.specVersion).toBe(4)
  })
})

describe('the fold is pure', () => {
  it('never mutates its input', () => {
    const vm = foldSnapshot(snap())
    const before = JSON.stringify({ ...vm, seen: [...vm.seen] })
    foldEvent(vm, 'workflow_node_done', ev({ event_id: 'e1', instance_path: 'root.children[1]', status: 'done' }))
    expect(JSON.stringify({ ...vm, seen: [...vm.seen] })).toBe(before)
  })

  it('is order-independent for independent node patches', () => {
    // Two different nodes finishing in either order must converge — otherwise SSE delivery
    // order would change what the user sees.
    const base = foldSnapshot(snap({
      nodes: [
        { instance_path: 'a', node_id: 'a', state: 'running' },
        { instance_path: 'b', node_id: 'b', state: 'running' },
      ],
    }))
    const ea = { event: 'workflow_node_done' as const, data: ev({ event_id: 'ea', instance_path: 'a', node_id: 'a', status: 'done' }) }
    const eb = { event: 'workflow_node_done' as const, data: ev({ event_id: 'eb', instance_path: 'b', node_id: 'b', status: 'failed' }) }
    const ab = foldEvents(base, [ea, eb])
    const ba = foldEvents(base, [eb, ea])
    expect(ab.nodes).toEqual(ba.nodes)
    expect(ab.doneCount).toBe(ba.doneCount)
  })
})

describe('dedupKey', () => {
  it('is the documented run|node|epoch|seq|state shape', () => {
    expect(dedupKey({ run_id: 'r1', node_id: 'n1', epoch: 2, seq: 9, status: 'done' }))
      .toBe('r1|n1|2|9|done')
  })

  it('prefers a node epoch over the run epoch when present', () => {
    // A node's own epoch is the finer key: it can lag the run's after a partial rewind.
    expect(dedupKey({ run_id: 'r1', node_id: 'n1', epoch: 5, node_epoch: 3, seq: 1, status: 'done' }))
      .toBe('r1|n1|3|1|done')
  })

  it('falls back to the instance path when a node has no id', () => {
    expect(dedupKey({ run_id: 'r1', instance_path: 'root.body#2', epoch: 0, seq: 4, status: 'running' }))
      .toBe('r1|root.body#2|0|4|running')
  })

  it('distinguishes two states of the same node in the same epoch', () => {
    const a = dedupKey({ run_id: 'r', node_id: 'n', epoch: 0, seq: 1, status: 'running' })
    const b = dedupKey({ run_id: 'r', node_id: 'n', epoch: 0, seq: 2, status: 'done' })
    expect(a).not.toBe(b)
  })
})

// ── The chat card's tool detection ──────────────────────────────────────────

describe('workflowRefFromTool', () => {
  it('recognizes a started run from the tool output', () => {
    expect(workflowRefFromTool('workflow_start', '{\n  "run_id": "a1b2c3d4",\n  "status": "running"\n}'))
      .toEqual({ runId: 'a1b2c3d4', created: true })
  })

  it('recognizes an inspected run, marked not-created', () => {
    // A status check should still render the live card — the user wants the run, not the
    // frozen text — but `created` distinguishes "the agent made this" from "it looked".
    expect(workflowRefFromTool('workflow_status', '{"run_id": "deadbeef"}'))
      .toEqual({ runId: 'deadbeef', created: false })
  })

  it('ignores unrelated tools', () => {
    expect(workflowRefFromTool('workflow_manifest', '{"run_id": "a1b2c3d4"}')).toBeNull()
    expect(workflowRefFromTool('bash', '{"run_id": "a1b2c3d4"}')).toBeNull()
  })

  it('returns null when there is no run id to open', () => {
    expect(workflowRefFromTool('workflow_start', 'Error [WF_DEF_NOT_FOUND]: no such workflow')).toBeNull()
    expect(workflowRefFromTool('workflow_start', undefined)).toBeNull()
    expect(workflowRefFromTool(undefined, '{"run_id": "a1b2c3d4"}')).toBeNull()
  })
})
