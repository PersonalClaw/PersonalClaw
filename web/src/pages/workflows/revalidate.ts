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

function committedEffectsSentence(preview: WorkflowCascadePreview | null | undefined): string {
  const committed = preview?.committed_effects ?? []
  if (committed.length === 0) return ''
  return ` ${committed.length} step${committed.length === 1 ? '' : 's'} already committed ` +
    `effects (${committed.join(', ')}); re-running may fire tools again.`
}

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
  return `${head}${committedEffectsSentence(preview)} Re-validate this template’s judge calibration.`
}

/** The confirmation copy for the two in-place re-entry verbs.
 *
 *  Both verbs receive the same computed cascade preview; the verb only changes whether the
 *  selected node itself is reset. The committed-effect list is named, not reduced to a boolean,
 *  so the user can identify the steps whose tools may fire again before consenting. */
export function reentrySummary(
  verb: 'rewind' | 'run-from',
  preview: WorkflowCascadePreview | null | undefined,
): string {
  const rerun = preview?.rerun?.length ?? 0
  const lead = verb === 'rewind' ? 'Re-running this node' : 'Running from this node'
  const impact = rerun > 0
    ? `${lead} will reset ${rerun} step${rerun === 1 ? '' : 's'}.`
    : `${lead} will not reset any completed steps.`
  return `${impact}${committedEffectsSentence(preview)}`
}
