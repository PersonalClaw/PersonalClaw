import type { WorkflowCascadePreview } from '../../lib/api'

/** The mid-flight-edit re-validate warning (LOOPS-EVOLUTION R10b / criterion 9).
 *
 *  A bundled template carries a typed doc block whose judge calibration is tuned to the
 *  prompts it shipped with. Editing a stage's prompt on a live run is a legitimate mutation,
 *  but it can silently invalidate that calibration — the judge keeps grading against a rubric
 *  the run no longer matches. So an edit to a bundled template SURFACES this rather than
 *  applying quietly; the user confirms the trade instead of discovering later that the judge
 *  is calibrated to a prompt that no longer exists.
 *
 *  Pure text + a pure predicate, so the wording is one reviewable place and the "does this
 *  edit warrant the warning" decision is unit-testable without a dialog. */
export const revalidateNotice =
  'Editing this stage changes the template. Its judge calibration was tuned to the ' +
  'shipped prompts, so re-validate the template after resuming — the judge may otherwise ' +
  'grade against a rubric this run no longer matches.'

/** A one-line re-validate summary for AFTER an edit lands, tuned to what it cost.
 *
 *  Names the re-run count because the size of the cascade is what tells the user how much of
 *  the run the edit invalidated: "3 steps will re-run" is a different decision from "nothing
 *  re-runs". Always ends on the re-validate ask, since that is the calibration point the whole
 *  warning exists for. */
export function revalidateSummary(preview: WorkflowCascadePreview | null | undefined): string {
  const rerun = preview?.rerun?.length ?? 0
  const head =
    rerun > 0
      ? `Edit applied — ${rerun} step${rerun === 1 ? '' : 's'} will re-run.`
      : 'Edit applied.'
  return `${head} Re-validate this template’s judge calibration.`
}
