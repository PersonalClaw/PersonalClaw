import type { CodeClassification } from '../../lib/api'

/** The in-memory hand-off into Code's Plan Review: the created (pre-run) code loop +
 *  its classification + the pre-plan policy choices. Lives here (not in a composer) so
 *  it survives the composer's consolidation into the unified LoopComposer — consumed
 *  by CodeSection (resume + onReady) and CodePlanReview. */
export interface CodeDraft {
  projectId: string
  classification: CodeClassification
  rigor: string
  attended: boolean
}
