import { useEffect, useRef, useState } from 'react'
import { api, type Loop } from '../../lib/api'

// The COMPLETE set of lifecycle events the unified per-loop SSE (loop_sse) can emit
// after the initial `snapshot`, across ALL kinds (goal/general/research/design =
// "loop", and code = "sdlc") and all three publish sources. EventSource silently
// DROPS event types with no registered listener, so any omission is a missed live
// update — the C326/C367 drift both prior hooks (useLoopStream + useCodeStream)
// warned about. Collapsing to ONE union list is the fix: a cockpit that doesn't
// handle a given event simply no-ops on it in its onLifecycle switch (harmless),
// but no cockpit can ever silently miss an event again.
//
// Sources (keep in sync with their .publish(...) sites):
//   • sdlc kind on_new_cycle (loop/kinds/sdlc.py): stage_advance, rolled_back,
//     stage_stalled, gate_check, task_started, task_done, blocked, needs_input
//     (rolled_back = P6 metric regression → stepped back to the prior stage)
//   • goal/design kinds: cycle_score, phase_advance (design per-cycle step advance)
//   • unified watchdog (loop/watchdog.py): new_finding, cycle_verdict, judge_error,
//     complete, stagnant, needs_input, failed, ratchet_regression, judge_blind, ship_blocked
//   • loop_routes handler (PATCH/POST actions): autopilot, queued, plan_step, deleted
// judge_blind/ship_blocked are the P4 prove-the-instrument warnings (judge unreliable /
// completion unconfirmed → output not graduated).
export const RUN_LIFECYCLE = [
  'new_finding', 'cycle_verdict', 'cycle_score', 'judge_error', 'complete', 'stagnant',
  'needs_input', 'failed', 'ratchet_regression', 'plan_step', 'phase_advance', 'rolled_back',
  'queued', 'autopilot', 'deleted', 'judge_blind', 'ship_blocked',
  'stage_advance', 'stage_stalled', 'gate_check', 'task_started', 'task_done', 'blocked',
  // LOOPS-EVOLUTION R4/R14 middleware events. These MUST be listed here: EventSource
  // silently DROPS event types it has no listener for, so an unregistered event is not a
  // rendering bug you can see — it is an event that never arrives.
  'breaker_trip', 'steering', 'judge_verdict', 'judge_divergence',
  // UNIVERSAL-PLANNING (WF2UNI) plan-review lifecycle. Registered here for the SAME reason
  // as every event above — an unregistered type is silently dropped by EventSource, so the
  // plan-review surface would never see a plan chunk arrive, a step get relabeled, a
  // shared-understanding confirmation open, or an unattended run demote to per-stage
  // approval. The plan-review surface (LoopPlanReview) folds them; a cockpit that doesn't
  // handle them no-ops, per this hook's contract. Backed by the planner's publish sites once
  // the engine emit seam lands (WORKFLOWS-V2 §"New SSE events"); listed ahead of that emitter
  // deliberately, because the drop is invisible and the union is the only place to prevent it.
  'plan_streaming', 'revision', 'confirmation', 'demotion',
  // WORK-CONTAINERS §6.3 R10c (WF2WOR-7): the coexistence mirror. A legacy loop can now RUN as a
  // template, and `workflows/watchdog._publish_to_equivalent_loop_hub` mirrors that run's events
  // onto the equivalent `loop:<id>` hub — the backend half of `keys_equivalent`, which had no
  // caller before. So this hub now carries `workflow_*` events, and they MUST be registered here
  // for the same reason as every event above: EventSource silently drops an unregistered type, so
  // the mirror would connect, deliver, and be discarded with no error anywhere. The mirror only
  // fires when a loop cockpit is ALREADY subscribed (`peek`, never `hub`), so these arrive
  // precisely when something is listening.
  'workflow_run_update', 'workflow_node_started', 'workflow_node_done', 'workflow_attention',
  'workflow_needs_input', 'workflow_gate_resolved', 'workflow_gate_revised',
  'workflow_spec_updated', 'workflow_mutation_rejected', 'workflow_forked', 'workflow_progress',
  'workflow_task_materialized', 'workflow_confirmation_pending', 'workflow_confirmation_resolved',
  'workflow_task_verified', 'workflow_cascade_blocked', 'workflow_steering_consumed',
] as const

export type RunLifecycleEvent = (typeof RUN_LIFECYCLE)[number]

/** The coalesced frame the workflow engine batches high-frequency node chatter into
 *  (`coalescer.BATCH_EVENT`). It is NOT a lifecycle event — it is an envelope AROUND them.
 *
 *  Registered on THIS hook because the R10c mirror forwards whatever the engine published,
 *  batches included. Without an unwrapper here, a mirrored run's node events would arrive inside
 *  an envelope nobody opened — delivered, then discarded, which is indistinguishable from never
 *  arriving. Matches `useWorkflowStream`'s handling so a mirrored cockpit sees the same sequence
 *  an unbatched one would. */
export const RUN_BATCH_EVENT = 'workflow_batch'

interface BatchMember { event: string; payload: unknown }

/** Unwrap a batch frame into its ordered members, dropping anything unrecognized.
 *
 *  Exported for the test: the property worth pinning is that unwrapping yields the same event
 *  sequence the cockpit would have received as individual frames. An unknown member is dropped
 *  rather than cast — the switch would ignore it anyway, and a cast would lie about the type. */
export function unwrapRunBatch(data: unknown): Array<{ event: RunLifecycleEvent; data: unknown }> {
  const raw = (data as { events?: unknown })?.events
  if (!Array.isArray(raw)) return []
  const known = new Set<string>(RUN_LIFECYCLE)
  return raw
    .filter((m): m is BatchMember => !!m && typeof (m as BatchMember).event === 'string')
    .filter((m) => known.has(m.event))
    .map((m) => ({ event: m.event as RunLifecycleEvent, data: m.payload }))
}

/** Subscribe to a run's per-resource SSE (/api/loops/{id}/stream) — the ONE stream
 *  hook for every loop kind (goal/general/research/design/code). `onSnapshot` fires
 *  with the full Loop on connect + whenever a snapshot is re-pushed; `onLifecycle`
 *  fires on each lifecycle event (the cue to refetch report/findings/tasks). Every
 *  kind listens to the full RUN_LIFECYCLE union — an event a given cockpit doesn't
 *  handle is simply a no-op in its switch, so no kind can silently miss one.
 *  EventSource carries the auth cookie same-origin and auto-reconnects on drops. */
export function useRunStream(id: string | null, enabled: boolean, handlers: {
  onSnapshot: (c: Loop) => void
  onLifecycle: (event: RunLifecycleEvent, data: unknown) => void
}) {
  const ref = useRef(handlers)
  ref.current = handlers

  // Is the FEED alive? Same reasoning as `workflows/useWorkflowStream`: EventSource retries forever, so
  // `onerror` is not a failure — it is the transport saying it is between attempts. Unsurfaced, a dead
  // feed and a quiet loop are identical: the cockpit stops updating and nothing says why.
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    setConnected(false)
    if (!enabled || !id) return
    let es: EventSource | null = null
    try { es = new EventSource(api.uLoopStreamUrl(id)) } catch { return }

    es.addEventListener('snapshot', (e) => {
      try { ref.current.onSnapshot(JSON.parse((e as MessageEvent).data) as Loop) } catch { /* malformed */ }
    })
    for (const ev of RUN_LIFECYCLE) {
      es.addEventListener(ev, (e) => {
        let data: unknown = null
        try { data = JSON.parse((e as MessageEvent).data) } catch { /* may be empty */ }
        ref.current.onLifecycle(ev, data)
      })
    }
    // The coalesced frame, replayed member-by-member in order, so a cockpit's fold is identical
    // whether the transport batched or not — and whether the events came from a loop or from a
    // mirrored template run.
    es.addEventListener(RUN_BATCH_EVENT, (e) => {
      let data: unknown = null
      try { data = JSON.parse((e as MessageEvent).data) } catch { return }
      for (const m of unwrapRunBatch(data)) ref.current.onLifecycle(m.event, m.data)
    })
    es.onopen = () => setConnected(true)
    // Still transient — the browser reconnects on its own. We only record that the feed is down
    // so the cockpit can say so; we never close or resubscribe here.
    es.onerror = () => setConnected(false)
    return () => { es?.close(); setConnected(false) }
  }, [id, enabled])

  return { connected }
}
