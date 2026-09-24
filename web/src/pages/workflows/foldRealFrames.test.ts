import { describe, expect, it } from 'vitest'
import { foldEvents, foldSnapshot } from './workflowFold'
import type { WorkflowRunDetailData } from '../../lib/api'

// ── The fold law against REAL captured frames ───────────────────────────────
// These are the exact SSE frames a live gateway emitted for run 5d106f6f (a gate answered
// mid-stream), captured off the wire. Folding them over the snapshot that preceded them
// must reproduce what the server reported afterwards — the fold law with real data rather
// than hand-written fixtures, which is what catches an envelope/fold mismatch.

const SNAPSHOT: WorkflowRunDetailData = {
  run_id: '5d106f6f',
  workflow: 'slow-gate',
  status: 'needs_input',
  spec_version: 1,
  error: '',
  attention: { kind: 'approval', prompt: 'Continue?' },
  tokens: 0,
  elapsed_secs: 0,
  nodes: [
    { instance_path: 'root.children[0]', node_id: 'prep', state: 'done' },
    { instance_path: 'root.children[1]', node_id: 'hold', state: 'waiting' },
  ],
}

const FRAMES = [
  { event: 'workflow_gate_resolved' as const, data: { run_id: '5d106f6f', event_id: '5d106f6f-evt-9', seq: 9, epoch: 0, instance_path: 'root.children[1]', node_id: 'hold', approved: true } },
  { event: 'workflow_run_update' as const, data: { run_id: '5d106f6f', event_id: '5d106f6f-evt-10', seq: 10, epoch: 0, status: 'running' } },
  { event: 'workflow_run_update' as const, data: { run_id: '5d106f6f', event_id: '5d106f6f-evt-11', seq: 11, epoch: 0, status: 'running' } },
  { event: 'workflow_node_started' as const, data: { run_id: '5d106f6f', event_id: '5d106f6f-evt-12', seq: 12, epoch: 0, node_epoch: 0, instance_path: 'root.children[2]', node_id: 'tail' } },
  { event: 'workflow_node_done' as const, data: { run_id: '5d106f6f', event_id: '5d106f6f-evt-13', seq: 13, epoch: 0, node_epoch: 0, instance_path: 'root.children[2]', node_id: 'tail', status: 'done' } },
  { event: 'workflow_run_update' as const, data: { run_id: '5d106f6f', event_id: '5d106f6f-evt-14', seq: 14, epoch: 0, status: 'complete' } },
]

describe('the fold law against real captured frames', () => {
  it('reconstructs the state the server reported after the same events', () => {
    // The server showed: complete, 3/3 done, no attention — verified in the browser.
    const vm = foldEvents(foldSnapshot(SNAPSHOT), FRAMES)
    expect(vm.status).toBe('complete')
    expect(vm.live).toBe(false)
    expect(vm.needsInput).toBe(false)
    expect(vm.attention).toBeNull()
    expect(vm.doneCount).toBe(3)
    expect(vm.totalCount).toBe(3)
    expect(vm.progress).toBe(1)
    expect(vm.nodes.map((n) => n.node_id)).toEqual(['prep', 'hold', 'tail'])
  })

  it('is idempotent under a full replay of the same frames', () => {
    // What a reconnect does. Every duplicate id must be dropped.
    const once = foldEvents(foldSnapshot(SNAPSHOT), FRAMES)
    const twice = foldEvents(once, FRAMES)
    expect(twice.doneCount).toBe(once.doneCount)
    expect(twice.nodes).toEqual(once.nodes)
    expect(twice.dropped).toBe(FRAMES.length)
  })

  it('converges regardless of node-frame delivery order', () => {
    const reordered = [FRAMES[4], FRAMES[3], FRAMES[0], FRAMES[5], FRAMES[1], FRAMES[2]]
    const inOrder = foldEvents(foldSnapshot(SNAPSHOT), FRAMES)
    const shuffled = foldEvents(foldSnapshot(SNAPSHOT), reordered)
    expect(shuffled.doneCount).toBe(inOrder.doneCount)
    expect(shuffled.nodes.map((n) => n.state)).toEqual(inOrder.nodes.map((n) => n.state))
  })
})
