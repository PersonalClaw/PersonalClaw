import type { UnifiedLoopClassification, Granularity } from '../../lib/api'

/** The in-memory hand-off from the unified Loop composer into Plan Review: the
 *  created (pre-run) loop + its classification + the pre-plan policy choices. Lives
 *  here (not in a composer) so it survives the composer's consolidation into the one
 *  LoopComposer — consumed by LoopsSection (resume + onReady) and LoopPlanReview. */
export interface LoopDraft {
  loopId: string
  classification: UnifiedLoopClassification
  rigor: string
  agent: string
  model: string
  provider?: string
  providerAgent?: string
  reasoningEffort?: string
  granularity: Granularity
  attended: boolean
}
