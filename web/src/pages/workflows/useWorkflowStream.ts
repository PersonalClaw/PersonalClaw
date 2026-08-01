import { useEffect, useRef } from 'react'
import { api, type WorkflowRunDetailData } from '../../lib/api'

// The COMPLETE set of events the per-run workflow SSE can emit after the initial
// `workflow_snapshot`. EventSource silently DROPS event types with no registered
// listener, so any omission here is a live update that never arrives — the same drift
// the loop hooks warned about, which is why this list is one exported union rather than
// per-component listeners.
//
// Sources (keep in sync with their `_publish(...)` sites):
//   • controller.py — workflow_run_update, workflow_node_started, workflow_node_done,
//     workflow_attention, workflow_needs_input, workflow_gate_resolved,
//     workflow_spec_updated, workflow_mutation_rejected, workflow_forked
//   • service.py blocking mode — workflow_progress (periodic node-state ticks)
//
// A component that doesn't care about a given event simply no-ops in its switch; that is
// harmless. Missing one from this list is not.
export const WORKFLOW_LIFECYCLE = [
  'workflow_run_update',
  'workflow_node_started',
  'workflow_node_done',
  'workflow_attention',
  'workflow_needs_input',
  'workflow_gate_resolved',
  'workflow_spec_updated',
  'workflow_mutation_rejected',
  'workflow_forked',
  'workflow_progress',
] as const

export type WorkflowLifecycleEvent = (typeof WORKFLOW_LIFECYCLE)[number]

// The coalesced frame (WF2-R11 batch-5). The backend batches one tick's per-node chatter
// into ONE frame so a 20-node fan-out is one write and one render instead of twenty. It is
// NOT a lifecycle event — it is an envelope AROUND them, so it gets its own listener that
// unwraps and replays the members in order. Every member keeps its own event envelope, so
// the fold sees exactly the sequence it would have seen unbatched.
export const WORKFLOW_BATCH_EVENT = 'workflow_batch'

interface BatchMember { event: string; payload: unknown }

/** Unwrap a batch frame into its ordered members, dropping anything malformed.
 *
 *  Exported for the fold tests: batching is a transport concern, and the property worth
 *  pinning is that unwrapping a batch yields the same event sequence the FE would have
 *  received as individual frames. */
export function unwrapBatch(data: unknown): Array<{ event: WorkflowLifecycleEvent; data: unknown }> {
  const raw = (data as { events?: unknown })?.events
  if (!Array.isArray(raw)) return []
  const known = new Set<string>(WORKFLOW_LIFECYCLE)
  return raw
    .filter((m): m is BatchMember => !!m && typeof (m as BatchMember).event === 'string')
    // An unrecognized member is dropped rather than passed through as an unknown event:
    // the fold's switch would ignore it anyway, and a cast would lie about the type.
    .filter((m) => known.has(m.event))
    .map((m) => ({ event: m.event as WorkflowLifecycleEvent, data: m.payload }))
}

/** Subscribe to one run's SSE (`/api/workflows/runs/{id}/events`).
 *
 *  Snapshot-then-subscribe: the backend writes the full status BEFORE the stream opens,
 *  so `onSnapshot` fires first and the view never renders an empty run that looks stalled.
 *  A terminal run's stream closes right after that snapshot — there are no further events
 *  to wait for, and holding the connection would leak one per finished run.
 *
 *  Handlers are held in a ref so a re-render with new closures does not tear down and
 *  re-establish the connection (which would drop events during the gap). */
export function useWorkflowStream(
  runId: string | null,
  enabled: boolean,
  handlers: {
    onSnapshot: (run: WorkflowRunDetailData) => void
    onLifecycle: (event: WorkflowLifecycleEvent, data: unknown) => void
  },
) {
  const ref = useRef(handlers)
  ref.current = handlers

  useEffect(() => {
    if (!enabled || !runId) return
    let es: EventSource | null = null
    try { es = new EventSource(api.workflowRunStreamUrl(runId)) } catch { return }

    es.addEventListener('workflow_snapshot', (e) => {
      try { ref.current.onSnapshot(JSON.parse((e as MessageEvent).data) as WorkflowRunDetailData) } catch { /* malformed */ }
    })
    for (const ev of WORKFLOW_LIFECYCLE) {
      es.addEventListener(ev, (e) => {
        let data: unknown = null
        try { data = JSON.parse((e as MessageEvent).data) } catch { /* may be empty */ }
        ref.current.onLifecycle(ev, data)
      })
    }
    // The coalesced frame: replayed member-by-member in order, so a consumer's fold is
    // identical whether the transport batched or not.
    es.addEventListener(WORKFLOW_BATCH_EVENT, (e) => {
      let data: unknown = null
      try { data = JSON.parse((e as MessageEvent).data) } catch { return }
      for (const m of unwrapBatch(data)) ref.current.onLifecycle(m.event, m.data)
    })
    es.onerror = () => { /* transient — EventSource retries automatically */ }
    return () => { es?.close() }
  }, [runId, enabled])
}
