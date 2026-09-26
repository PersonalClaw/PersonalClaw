/** Reading the run's polymorphic `attention` record (#565).
 *
 *  `run.attention` is ONE field carrying two different things the engine writes to it:
 *
 *  • a **gate ask** — `{kind: 'approval'|'choice'|'text'|'form', prompt, …}`, a question with an
 *    answer the user can give; and
 *  • an **escalation** — `{kind: 'escalation', node_id, reason, detail, options, attempts}`, the
 *    record `resilience.escalation_artifact` writes when retries are spent or the breaker trips.
 *    That is a **diagnosis**, not a question: nothing about it is answerable today.
 *
 *  Every reader before this module hard-coded the gate shape and reached for `attention.prompt`
 *  (`chat/WorkflowProgressCard.tsx`, and `workflows/context_block.py` on the Python side). An
 *  escalation has no `prompt`, so it read as blank at every surface — while the producer's own
 *  docstring promised the run "parks on a real decision rather than dying silently".
 *
 *  So the discrimination lives here, once. A surface asks what the record IS and gets back
 *  something it can render, rather than guessing a field name and falling back to a generic
 *  sentence when it guesses wrong.
 *
 *  **The five `options` are deliberately not read.** `ESCALATION_OPTIONS` is a module constant —
 *  the same five strings on every escalation ever produced — and no endpoint accepts one back.
 *  Rendering them would add five controls that cannot succeed, or five words that carry no
 *  information about *this* run. The decision surface that consumes them is engine work
 *  (a supervisor policy); until it exists, the honest thing to show is the diagnosis.
 */

/** One try at one node, as `resilience.Attempt.to_dict` writes it. Camel-cased at this
 *  boundary so no page has to know the wire's snake_case. */
export interface EscalationAttempt {
  attempt: number
  failureClass: string
  severity: string
  error: string
  expected: string
  actual: string
  evidence: string
  /** The engine's derived next move — the single most actionable field in the record. */
  fixInstruction: string
  signature: string
}

export interface EscalationRead {
  kind: 'escalation'
  nodeId: string
  /** The engine's raw reason token, kept so a reader can grep the source for it. */
  reason: string
  /** That token as a sentence. */
  headline: string
  detail: string
  attempts: EscalationAttempt[]
}

export interface AskRead {
  kind: 'ask'
  /** The ask's own kind (`approval`, `choice`, …), '' when it did not say. */
  askKind: string
  /** The question. '' when the ask carries none — the caller supplies its own fallback. */
  prompt: string
}

export type AttentionRead = EscalationRead | AskRead | null

/** Why the engine gave up, in words. Every key is a token that can actually reach an
 *  escalation artifact, measured from the two `_escalate` call sites in `controller.py`:
 *
 *  • `retries_exhausted` / `not_retried` — the node path (`controller.py`): the retry budget was
 *    spent, or there was none to spend (no budget declared, or a failure class a retry cannot
 *    fix). They are two tokens because "every retry was spent" on a single attempt is false;
 *  • `iterations_failed` — also `controller.py`, from `_surface_loop`;
 *  • the four `check_breaker` verdicts in `resilience.py`; and
 *  • the three `loop/tick.py` convergence reasons that reach `_surface_loop`.
 *
 *  An unmapped token falls through to the token itself rather than to a friendly default —
 *  `ReviewTriagePanel`'s ANCHOR_REASON rule, for the same reason: a default sentence would
 *  report a NEW failure mode as one of these, which is worse than showing an unfamiliar word.
 */
export const ESCALATION_REASON: Record<string, string> = {
  retries_exhausted: 'every retry was spent and the step still failed',
  not_retried: 'the step failed on its only attempt',
  /** `max_iterations` is the loop spending its budget ON WORK. A loop that spent it FAILING gets
   *  `iterations_failed` instead — `_surface_loop` re-derives the token, because the two are one
   *  token apart and miles apart to a reader: the first says "your task was too big", the second
   *  says "nothing ran". Measured on a `general-project` run where five of six iterations never
   *  called a model and the banner reported the ceiling (#3524). */
  max_iterations: 'the loop reached its iteration ceiling',
  iterations_failed: 'the loop spent its iterations failing rather than working',
  repeated_error: 'the same error came back on every attempt',
  identical_output: 'the work stopped changing between attempts',
  token_cap: 'the run reached its token budget',
  identical_call: 'the loop kept making the same call',
  hypothesis_exhausted: 'the loop ran out of things to try',
  no_progress: 'no measurable progress between attempts',
}

export function escalationHeadline(reason: string): string {
  return ESCALATION_REASON[reason] ?? reason
}

function str(source: Record<string, unknown>, key: string): string {
  const value = source[key]
  return typeof value === 'string' ? value : ''
}

function readAttempt(raw: unknown, index: number): EscalationAttempt {
  const a = (raw ?? {}) as Record<string, unknown>
  return {
    // 1-based fallback: an attempt row labelled "Attempt 0" reads as a bug in the panel
    // rather than as the first try.
    attempt: typeof a.attempt === 'number' ? a.attempt : index + 1,
    failureClass: str(a, 'failure_class'),
    severity: str(a, 'severity'),
    error: str(a, 'error'),
    expected: str(a, 'expected'),
    actual: str(a, 'actual'),
    evidence: str(a, 'evidence'),
    fixInstruction: str(a, 'fix_instruction'),
    signature: str(a, 'error_signature'),
  }
}

/** Discriminate one attention record. `null` for absent or unreadable input — a surface
 *  renders nothing rather than an empty card. */
export function readAttention(raw: unknown): AttentionRead {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const record = raw as Record<string, unknown>
  const kind = str(record, 'kind')

  if (kind === 'escalation') {
    const reason = str(record, 'reason')
    const attempts = Array.isArray(record.attempts) ? record.attempts : []
    return {
      kind: 'escalation',
      nodeId: str(record, 'node_id'),
      reason,
      headline: escalationHeadline(reason),
      detail: str(record, 'detail'),
      attempts: attempts.map(readAttempt),
    }
  }

  // Anything else is a gate ask. Deliberately NOT an allow-list of the four known ask kinds:
  // a fifth kind carrying a prompt is still a question, and refusing to read it would repeat
  // exactly the failure this module exists to end.
  const prompt = str(record, 'prompt').trim()
  if (!prompt && !kind) return null
  return { kind: 'ask', askKind: kind, prompt }
}

/** The one-line form for a glance surface (the chat card). Never empty: a run that is
 *  holding something must say so even when the record carries no words of its own. */
export function attentionLine(raw: unknown, fallback = 'Waiting on you'): string {
  const read = readAttention(raw)
  if (read === null) return fallback
  if (read.kind === 'escalation') return `Stopped: ${read.headline}`
  return read.prompt || fallback
}
