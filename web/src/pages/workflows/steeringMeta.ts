import type { WorkflowNodeState } from '../../lib/api'

/** Judge-comment triage + interrupt-queue helpers (LOOPS-EVOLUTION R14 / criterion 8).
 *
 *  Pure so the interesting decisions — which node carries a judge comment worth acting on,
 *  and what text an "accept" sends to the worker — are unit-testable without a rendered run.
 *
 *  What the UI calls a "judge comment" on a workflow run is a node's verdict text: a
 *  `degraded` node carries the judge's `degraded_reason`, and a `failed` one carries the
 *  verification `failure.cause_plain` (+ its `remediation`, the actionable half). Accepting
 *  such a comment sends it to the worker as a steering instruction through `/steer`, which
 *  is consumed at the next iteration boundary — the channel that makes "an accepted judge
 *  comment reaches the worker session" literally true. */

/** The judge/verification comment on a node, or '' when there is none.
 *
 *  Prefers the remediation over the bare cause when both are present: the remediation is the
 *  next action, which is what a person accepting the comment wants the worker to DO — a cause
 *  alone re-sends the diagnosis without the fix. A degraded node's reason is a success-with-a-
 *  caveat, still worth steering on. */
export function judgeComment(node: Pick<WorkflowNodeState, 'degraded_reason' | 'failure'>): string {
  const remediation = node.failure?.remediation?.trim()
  const cause = node.failure?.cause_plain?.trim()
  const degraded = node.degraded_reason?.trim()
  return remediation || cause || degraded || ''
}

/** The steering text an accepted judge comment sends to the worker.
 *
 *  Prefixed so the worker reads it as feedback to act on, not as a fresh unrelated
 *  instruction, and labelled with the node so a multi-node run's steer log says which stage
 *  the comment came from. Empty in → empty out, so a caller never queues a blank steer. */
export function steerTextFromComment(nodeLabel: string, comment: string): string {
  const text = (comment ?? '').trim()
  if (!text) return ''
  const where = (nodeLabel ?? '').trim()
  return where ? `Address this feedback on "${where}": ${text}` : `Address this feedback: ${text}`
}

/** Whether a node's judge comment can be accepted-and-steered right now.
 *
 *  Only when the run can still act on it: steering a run that cannot move is refused by the
 *  backend anyway, and offering the action there teaches the user the UI lies. Gated on the
 *  presence of a comment too — an "accept" button over nothing has nothing to send. */
export function canSteerComment(node: Pick<WorkflowNodeState, 'degraded_reason' | 'failure'>, runIsLive: boolean): boolean {
  return runIsLive && judgeComment(node) !== ''
}
