import { useEffect, useRef } from 'react'
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
] as const

export type RunLifecycleEvent = (typeof RUN_LIFECYCLE)[number]

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

  useEffect(() => {
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
    es.onerror = () => { /* transient — EventSource retries automatically */ }
    return () => { es?.close() }
  }, [id, enabled])
}
