import { describe, expect, it } from 'vitest'
import { fmtElapsed, isTerminal, itemProgress, nodeDepth, nodeLabel, nodeLook, runLook } from './workflowMeta'
import { WORKFLOW_LIFECYCLE } from './useWorkflowStream'

// ── The engine's outcome vocabulary must survive into the UI ────────────────
// The backend deliberately keeps outcomes wider than done|failed (degraded, no_change,
// scope_violation, blocked, escalated). Flattening them here would throw away exactly the
// distinction it went to the trouble of preserving — a `degraded` run DID succeed, and a
// user who reads it as a failure "fixes" something that worked.

describe('runLook', () => {
  it('needs_input is the only status toned as actionable', () => {
    // It is the one row a user can act on; toning others the same way would make the
    // actionable case invisible in a list.
    expect(runLook('needs_input').tone).toBe('text-warning')
    expect(runLook('running').tone).toBe('text-on-surface')
    expect(runLook('complete').tone).toBe('text-success')
  })

  it('failed and escalated are both danger-toned', () => {
    expect(runLook('failed').tone).toBe('text-danger')
    expect(runLook('escalated').tone).toBe('text-danger')
  })

  it('only running spins', () => {
    expect(runLook('running').spin).toBe(true)
    expect(runLook('complete').spin).toBeUndefined()
  })

  it('an unknown status degrades to a readable label, never throws', () => {
    // Tolerant reader: a status added backend-first must not break the list.
    const look = runLook('some_future_status')
    expect(look.label).toBe('some_future_status')
    expect(look.tone).toBe('text-on-surface-low')
  })

  it('every documented run status has a look', () => {
    for (const s of ['draft', 'running', 'paused', 'needs_input', 'complete', 'failed', 'cancelled', 'escalated']) {
      expect(runLook(s).label).not.toBe(s === 'needs_input' ? 'needs_input' : '')
    }
  })
})

describe('nodeLook', () => {
  it('degraded reads as a WARNING, not a failure — it is a success with a reason', () => {
    expect(nodeLook('degraded').tone).toBe('text-warning')
    expect(nodeLook('failed').tone).toBe('text-danger')
  })

  it('the wider outcome states are all distinctly rendered', () => {
    const states = ['no_change', 'scope_violation', 'blocked', 'escalated', 'skipped']
    const labels = states.map((s) => nodeLook(s).label)
    expect(new Set(labels).size).toBe(states.length)
    expect(labels).not.toContain('Unknown')
  })

  it('waiting is toned as actionable (a human is needed)', () => {
    expect(nodeLook('waiting').tone).toBe('text-warning')
  })

  it('an unknown node state degrades safely', () => {
    expect(nodeLook('brand_new_state').label).toBe('brand_new_state')
  })
})

describe('isTerminal', () => {
  it('a terminal run will not move on its own', () => {
    for (const s of ['complete', 'failed', 'cancelled', 'escalated']) expect(isTerminal(s)).toBe(true)
  })

  it('needs_input is NOT terminal — it is waiting, and the view must keep streaming', () => {
    // Treating it as terminal would close the SSE connection and freeze the view at the
    // moment the user answers.
    expect(isTerminal('needs_input')).toBe(false)
    expect(isTerminal('running')).toBe(false)
    expect(isTerminal('paused')).toBe(false)
  })
})

describe('nodeLabel', () => {
  it('prefers the node id', () => {
    expect(nodeLabel({ node_id: 'gather', instance_path: 'root.children[0]' })).toBe('gather')
  })

  it('keeps the foreach/loop instance suffix — many instances share one node id', () => {
    // Without the suffix, a 10-item fan-out renders ten identical rows.
    expect(nodeLabel({ node_id: 'item', instance_path: 'root.body#3' })).toBe('item #3')
    expect(nodeLabel({ node_id: 'step', instance_path: 'root.body@2' })).toBe('step @2')
  })

  it('falls back to the path when a node has no id', () => {
    expect(nodeLabel({ node_id: '', instance_path: 'root.children[1]' })).toBe('root.children[1]')
  })
})

describe('nodeDepth', () => {
  it('the root sits at depth 0', () => {
    expect(nodeDepth('root')).toBe(0)
  })

  it('depth grows with the engine path grammar, not with string length', () => {
    expect(nodeDepth('root.children[0]')).toBe(0)
    expect(nodeDepth('root.children[0].children[1]')).toBe(1)
    expect(nodeDepth('root.children[0].body')).toBe(1)
  })

  it('handles cases and defaults', () => {
    expect(nodeDepth('root.cases[hit].children[0]')).toBe(1)
    expect(nodeDepth('root.default')).toBe(0)
  })

  it('never returns a negative indent', () => {
    expect(nodeDepth('')).toBe(0)
    expect(nodeDepth('nonsense')).toBe(0)
  })
})

describe('fmtElapsed', () => {
  it('renders nothing for a run that has not started', () => {
    expect(fmtElapsed(undefined)).toBe('')
    expect(fmtElapsed(0)).toBe('')
  })

  it('scales its unit with the magnitude', () => {
    expect(fmtElapsed(9)).toBe('9s')
    expect(fmtElapsed(90)).toBe('1m 30s')
    expect(fmtElapsed(3720)).toBe('1h 2m')
  })
})

// ── The EventSource drift guard ─────────────────────────────────────────────
// EventSource SILENTLY DROPS event types with no registered listener, so an event the
// backend publishes but this union omits is a live update that never arrives — invisible
// in every test that doesn't assert the list itself.

describe('WORKFLOW_LIFECYCLE', () => {
  it('covers every event the engine publishes', () => {
    // Kept in sync with the `_publish(...)` sites in controller.py + service.py. Adding a
    // backend event without adding it here is the failure this test exists to catch.
    const published = [
      'workflow_run_update',
      'workflow_node_started',
      'workflow_node_done',
      'workflow_attention',
      'workflow_needs_input',
      'workflow_gate_resolved',
      'workflow_gate_revised',
      'workflow_spec_updated',
      'workflow_mutation_rejected',
      'workflow_forked',
      'workflow_progress',
      // TASKS-SOPS §7 (S61e): the task-projection events. Each has a real `_publish` site
      // (`RunController.publish_task_materialized` and its four siblings), so they belong in this
      // mirror list — and the backend test `test_workflows_projection_events.py` asserts the
      // reverse direction, that every emitted kind appears in the union.
      'workflow_task_materialized',
      'workflow_confirmation_pending',
      'workflow_confirmation_resolved',
      'workflow_task_verified',
      'workflow_cascade_blocked',
      // LOOPS-EVOLUTION R14: emitted by RunController._consume_steering when a mid-run steer is
      // consumed at the iteration boundary.
      'workflow_steering_consumed',
      // PP-15: emitted by RunController._converge_loop for every convergence decision a tripped
      // loop gets — the rung, a replan, or a recoverable wait. Without it a run that quietly
      // switched to a fresh session looks to the user like a run doing nothing.
      'workflow_loop_converged',
    ]
    for (const ev of published) expect(WORKFLOW_LIFECYCLE).toContain(ev)
    // And no extras: a listener for an event nobody publishes is dead code that reads as
    // coverage.
    expect(WORKFLOW_LIFECYCLE.length).toBe(published.length)
  })

  it('does not include the snapshot event — that has its own dedicated listener', () => {
    expect(WORKFLOW_LIFECYCLE).not.toContain('workflow_snapshot')
  })
})

// ── Per-item foreach progress (WF2-R5, Slice 8c) ────────────────────────────
//
// A twelve-item fan-out renders as twelve rows whose only difference is an index suffix —
// technically correct and useless for answering "which item is stuck?". This is what makes
// them distinguishable.
describe('itemProgress', () => {
  it('renders a 1-based counter with the total', () => {
    // 1-based deliberately: the engine's item_index is a 0-based array position, and "[0/12]"
    // reads to a human as "none done yet" rather than "the first one".
    expect(itemProgress({ item_index: 0, item_total: 12, item_label: 'auth.py' }))
      .toBe('[1/12] auth.py')
    expect(itemProgress({ item_index: 11, item_total: 12 })).toBe('[12/12]')
  })

  it('drops the denominator when no total is known', () => {
    expect(itemProgress({ item_index: 2 })).toBe('[3]')
  })

  it('renders a label with no index', () => {
    expect(itemProgress({ item_label: 'auth.py' })).toBe('auth.py')
  })

  it('is empty for a non-iterated node', () => {
    // So the caller renders nothing rather than an empty bracket.
    expect(itemProgress({})).toBe('')
    expect(itemProgress({ item_label: '' })).toBe('')
  })
})
