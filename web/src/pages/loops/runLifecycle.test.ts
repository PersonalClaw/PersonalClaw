import { describe, it, expect } from 'vitest'
import { RUN_LIFECYCLE, unwrapRunBatch } from './useRunStream'

// EventSource silently DROPS event types with no registered listener, and `useRunStream` builds
// its listeners by iterating THIS const. So a member missing from the union is not a bug you can
// see — it is a live update that never arrives. This test pins the membership the plan-review
// surface depends on, so a refactor of the array can't silently drop one.
describe('RUN_LIFECYCLE union membership', () => {
  it('carries the UNIVERSAL-PLANNING plan-review events (WF2UNI-10)', () => {
    for (const ev of ['plan_streaming', 'revision', 'confirmation', 'demotion'] as const) {
      expect(RUN_LIFECYCLE).toContain(ev)
    }
  })

  it('has no duplicate members (a dup double-registers a listener)', () => {
    expect(new Set(RUN_LIFECYCLE).size).toBe(RUN_LIFECYCLE.length)
  })
})

// ── WORK-CONTAINERS §6.3 R10c (WF2WOR-7): the coexistence mirror ──
//
// A legacy loop can now RUN as a template, and the backend mirrors that run's events onto the
// equivalent `loop:<id>` hub (`workflows/watchdog._publish_to_equivalent_loop_hub`). So this hub
// carries `workflow_*` frames now. An unregistered type is dropped by EventSource with no error,
// which for a mirror means: connects, delivers, discarded — indistinguishable from never arriving.
describe('the mirrored workflow-run events are registered', () => {
  it('carries every event the workflow engine publishes', () => {
    // Kept in step with `WORKFLOW_LIFECYCLE` in useWorkflowStream.ts: the mirror forwards whatever
    // the engine published, so anything that union lists can land on this hub too.
    for (const ev of [
      'workflow_run_update', 'workflow_node_started', 'workflow_node_done', 'workflow_attention',
      'workflow_needs_input', 'workflow_gate_resolved', 'workflow_gate_revised',
      'workflow_spec_updated', 'workflow_mutation_rejected', 'workflow_forked',
      'workflow_progress', 'workflow_task_materialized', 'workflow_confirmation_pending',
      'workflow_confirmation_resolved', 'workflow_task_verified', 'workflow_cascade_blocked',
      'workflow_steering_consumed',
      'workflow_loop_converged',
    ] as const) {
      expect(RUN_LIFECYCLE).toContain(ev)
    }
  })

  it('stays in step with the workflow hook it mirrors', async () => {
    // The stronger form of the test above: derived from the other union rather than a hand-copied
    // list, so a NEW engine event added there cannot be silently missing here.
    const { WORKFLOW_LIFECYCLE } = await import('../workflows/useWorkflowStream')
    const missing = WORKFLOW_LIFECYCLE.filter((e) => !RUN_LIFECYCLE.includes(e as never))
    expect(missing).toEqual([])
  })
})

describe('the coalesced batch frame is unwrapped on this hook too', () => {
  it('replays members in order, so a fold is identical batched or not', () => {
    const out = unwrapRunBatch({
      events: [
        { event: 'workflow_node_started', payload: { n: 1 } },
        { event: 'workflow_node_done', payload: { n: 2 } },
      ],
    })
    expect(out.map((m) => m.event)).toEqual(['workflow_node_started', 'workflow_node_done'])
    expect(out[1].data).toEqual({ n: 2 })
  })

  it('drops an unrecognized member rather than casting it', () => {
    // The switch would ignore it anyway; a cast would lie about the type.
    const out = unwrapRunBatch({ events: [{ event: 'not_a_real_event', payload: {} }] })
    expect(out).toEqual([])
  })

  it('survives a malformed frame instead of throwing into the listener', () => {
    expect(unwrapRunBatch(null)).toEqual([])
    expect(unwrapRunBatch({ events: 'nope' })).toEqual([])
  })
})
