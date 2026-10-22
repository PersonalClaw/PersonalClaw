import type { CodeFinding } from '../../../lib/api'
import { ACTIVE_LOOP_STATUSES } from '../../../lib/loopStatus'
import { newSessionTarget, nextCycleTarget } from '../../../ui/content/commentTarget'
import type { ReviewComments } from './ReviewChanges'
import { resolveTouchedPath } from './cockpitShared'

// ── Where a comment on a code session's change goes ───────────────────────────────────────────
//
// While the loop is at work (running, or waiting on its owner, which a nudge re-arms), a comment
// is guidance for its next cycle, on the task whose work last touched that file. Once it has
// ended there is no next step to guide, so the comment opens a new chat about it, as a comment on
// a page with no live conversation does.

/** The task whose work last touched `rel`: the latest finding with a task that names the file. */
export function taskOfChange(findings: CodeFinding[] | undefined, root: string, rel: string): string | undefined {
  const base = root.replace(/\/$/, '')
  for (const f of [...(findings ?? [])].reverse()) {
    if (!f.task_id) continue
    if ((f.files_touched ?? []).some((raw) => resolveTouchedPath(raw, base)?.rel === rel)) return f.task_id
  }
  return undefined
}

export function reviewComments({ loopId, status, findings, root, navigate }: {
  loopId: string; status: string; findings?: CodeFinding[]; root: string
  navigate?: (path: string) => void
}): ReviewComments | undefined {
  if (ACTIVE_LOOP_STATUSES.has(status)) {
    return { loopId, targetFor: (rel) => nextCycleTarget(loopId, taskOfChange(findings, root, rel)) }
  }
  if (!navigate) return undefined
  return { loopId, targetFor: (rel) => newSessionTarget(navigate, { title: `Review of ${rel}` }) }
}
