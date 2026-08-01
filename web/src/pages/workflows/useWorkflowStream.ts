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
    es.onerror = () => { /* transient — EventSource retries automatically */ }
    return () => { es?.close() }
  }, [runId, enabled])
}
