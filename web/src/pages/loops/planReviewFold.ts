/** Pure fold of the WF2UNI plan-review SSE events into ONE view-model (WF2UNI-10).
 *
 *  The plan-review surface listens to four new lifecycle events — `plan_streaming`, `revision`,
 *  `confirmation`, `demotion` (all registered in `useRunStream`'s RUN_LIFECYCLE union). Deciding
 *  what each does to the review state is logic, not chrome, so it lives here as a pure reducer
 *  with `planReviewFold.test.ts` pinning it — the same split `runFold` uses for the cockpits.
 *
 *  Event contracts (the payload each carries; forgiving of a missing field):
 *   • plan_streaming `{chunk?|buffer?, names?, done?}` — a plan JSON chunk to APPEND (or a full
 *     buffer to replace), the streamed {title,description,labels}, and the terminal flag.
 *   • revision `{labels?}` — relabel ONLY changed steps (a revision merges by id; the plan says
 *     revisions relabel only what changed), merged over the running label set.
 *   • confirmation `{prompt?}` — the shared-understanding gate that precedes spec emission.
 *   • demotion `{reason?}` — an unattended run dropped to per-stage approval mid-plan. */

import type { PlanNames } from './planNaming'

export interface PlanReviewState {
  /** Accumulated plan JSON so far — grown by plan_streaming chunks. */
  buffer: string
  /** True once a plan_streaming event carried `done`. */
  complete: boolean
  /** The streamed naming call output, label-merged across revisions. Null until it arrives. */
  names: PlanNames | null
  /** The open shared-understanding confirmation prompt (null = none pending). */
  confirmation: string | null
  /** Set when the run demoted to per-stage approval; carries the reason for the banner. */
  demotedReason: string | null
}

export const emptyPlanReview = (): PlanReviewState => ({
  buffer: '', complete: false, names: null, confirmation: null, demotedReason: null,
})

export function planReviewReducer(
  state: PlanReviewState,
  event: string,
  data?: unknown,
): PlanReviewState {
  const d = (data ?? {}) as Record<string, unknown>
  switch (event) {
    case 'plan_streaming': {
      // A `buffer` replaces (a resync/snapshot); a `chunk` appends (the common case).
      const buffer = typeof d.buffer === 'string'
        ? d.buffer
        : typeof d.chunk === 'string'
          ? state.buffer + d.chunk
          : state.buffer
      const names = isNames(d.names) ? mergeNames(state.names, d.names) : state.names
      return { ...state, buffer, names, complete: state.complete || d.done === true }
    }
    case 'revision': {
      // Relabel only the changed steps — merge the revision's labels over the running set. Other
      // revision payloads (a re-streamed buffer) fall through to the same buffer/complete rules.
      const names = isNames(d.names) || d.labels
        ? mergeNames(state.names, { labels: asLabels(d.labels ?? (d.names as Record<string, unknown>)?.labels) })
        : state.names
      const buffer = typeof d.buffer === 'string' ? d.buffer : state.buffer
      // A revision re-opens the plan for review — a re-streamed buffer is no longer complete.
      const complete = typeof d.buffer === 'string' ? d.done === true : state.complete
      return { ...state, names, buffer, complete }
    }
    case 'confirmation':
      // A prompt opens the gate; an explicit resolve (done/resolved) closes it.
      return { ...state, confirmation: d.done === true || d.resolved === true ? null : String(d.prompt ?? 'Confirm the plan before it runs.') }
    case 'demotion':
      return { ...state, demotedReason: String(d.reason ?? 'Confidence dropped — switched to per-stage approval.') }
    default:
      return state
  }
}

/** Merge a naming payload over the prior one, per-field, so a revision that relabels one step
 *  keeps every other label. A later title/description wins; labels union with the new keys
 *  overriding. */
function mergeNames(prev: PlanNames | null, next: PlanNames): PlanNames {
  return {
    title: next.title ?? prev?.title,
    description: next.description ?? prev?.description,
    labels: { ...(prev?.labels ?? {}), ...(next.labels ?? {}) },
  }
}

function isNames(v: unknown): v is PlanNames {
  return !!v && typeof v === 'object'
}

function asLabels(v: unknown): Record<string, string> {
  if (!v || typeof v !== 'object') return {}
  const out: Record<string, string> = {}
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    if (typeof val === 'string') out[k] = val
  }
  return out
}
