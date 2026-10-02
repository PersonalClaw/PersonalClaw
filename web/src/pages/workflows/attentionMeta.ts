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

import type { UsageBudget, WorkflowRunDetailData } from '../../lib/api'

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
  /** The instance that gave up — what tells two items of one `foreach` apart. `''` on the
   *  `run.attention` record, which never carried it; every ledger-read escalation has one. */
  instancePath: string
  /** The engine's raw reason token, kept so a reader can grep the source for it. */
  reason: string
  /** WHY it stopped: the record's own classification (`ending_sentence.CAUSES`, written by
   *  `resilience.escalation_artifact`), never re-derived here from the reason token — a budget it
   *  was given, a judge that would not decide, an ask that ran out of time, a start nobody
   *  approved, refused tools, a spend cap's refusal, or a step that failed at its work. `''` on a
   *  record written before causes were. */
  cause: string
  /** The stop as a headline fragment: by its cause where the cause says more than the token. */
  headline: string
  /** What happened, as one true sentence. */
  detail: string
  /** What to do about it, matching the cause — the record's own words. `''` when it carries
   *  none. */
  remedy: string
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
 *  escalation artifact, measured from the two `_escalate` call sites, in `controller.py` and
 *  `loop_convergence.py`:
 *
 *  • `retries_exhausted` / `not_retried` — the node path (`controller.py`): the retry budget was
 *    spent, or there was none to spend (no budget declared, or a failure class a retry cannot
 *    fix). They are two tokens because "every retry was spent" on a single attempt is false;
 *  • `iterations_failed` — the loop path (`loop_convergence.py`), from `surface_loop`;
 *  • the four `check_breaker` verdicts in `resilience.py`; and
 *  • the three `loop/tick.py` convergence reasons that reach `surface_loop`.
 *
 *  An unmapped token falls through to the token itself rather than to a friendly default —
 *  `ReviewTriagePanel`'s ANCHOR_REASON rule, for the same reason: a default sentence would
 *  report a NEW failure mode as one of these, which is worse than showing an unfamiliar word.
 */
export const ESCALATION_REASON: Record<string, string> = {
  retries_exhausted: 'every retry was spent and the step still failed',
  not_retried: 'the step failed on its only attempt',
  /** `max_iterations` is the loop spending its budget ON WORK. A loop that spent it FAILING gets
   *  `iterations_failed` instead — `surface_loop` re-derives the token, because the two are one
   *  token apart and miles apart to a reader: the first says "your task was too big", the second
   *  says "nothing ran". Measured on a `general-project` run where five of six iterations never
   *  called a model and the banner reported the ceiling (#3524). */
  max_iterations: 'the loop reached its iteration ceiling',
  iterations_failed: 'the loop spent its iterations failing rather than working',
  /** The judge would not decide on the cycle the loop ended on (`ending_sentence.loop_stop`). */
  judge_escalated: 'the judge could not decide whether the work is done',
  repeated_error: 'the same error came back on every attempt',
  identical_output: 'the work stopped changing between attempts',
  token_cap: 'the run reached its token budget',
  identical_call: 'the loop kept making the same call',
  hypothesis_exhausted: 'the loop ran out of things to try',
  no_progress: 'no measurable progress between attempts',
}

/** A cause's headline, for the causes that say more than any reason token: a stop for one of
 *  these is not a step failing at its work, and "the loop spent its iterations failing rather than
 *  working" read over a judge that handed over its decision, or an ask that waited too long, sent
 *  the reader to fix a step that was fine. Every cause the backend writes is here or in
 *  `REASON_HEADED_CAUSES` (railed by `tests/test_a_loops_ending_reads_why_it_stopped.py`). */
export const CAUSE_HEADLINE: Record<string, string> = {
  judge: 'the judge could not decide whether the work is done',
  approval_timeout: 'a step’s time limit ran out while it waited for your answer',
  approval: 'a step needed your approval to start, and nobody gave it',
  refusal: 'every call a step made was refused by the tools it was given',
  spend_cap: 'a spend cap refused the call a step needed',
}

/** The causes headed by their reason token, which is the more specific of the two: which budget
 *  (`max_iterations`, `token_cap`), and how a step gave up (`retries_exhausted`, `repeated_error`…). */
export const REASON_HEADED_CAUSES: ReadonlySet<string> = new Set(['budget', 'step'])

export function escalationHeadline(reason: string, cause = ''): string {
  return CAUSE_HEADLINE[cause] ?? ESCALATION_REASON[reason] ?? reason
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
    const cause = str(record, 'cause')
    const attempts = Array.isArray(record.attempts) ? record.attempts : []
    return {
      kind: 'escalation',
      nodeId: str(record, 'node_id'),
      instancePath: str(record, 'instance_path'),
      reason,
      cause,
      headline: escalationHeadline(reason, cause),
      detail: str(record, 'detail'),
      remedy: str(record, 'remedy'),
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

/** Did this run end because a loop stopped at the budget it was given? Read off the escalation
 *  that ended it (`attention`, which every run surface carries: the run page, the runs list, the
 *  chat card's fold), so each of them says "Stopped at its budget" where the run's status alone
 *  reads `escalated`. */
export function stoppedAtBudget(status: string, attention: unknown): boolean {
  if (status !== 'escalated') return false
  const read = readAttention(attention)
  return read?.kind === 'escalation' && read.cause === 'budget'
}

/** Every escalation in a run's `escalations` list (oldest first), skipping anything unreadable.
 *  The list, not `run.attention`: that is ONE slot, and each escalation overwrote the last, so a
 *  run whose two steps both gave up used to show only the second. */
export function readEscalations(raw: unknown): EscalationRead[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map(readAttention)
    .filter((read): read is EscalationRead => read?.kind === 'escalation')
}

/** Can a fresh attempt clear what stopped the run, and from when? `null` when Retry must not be
 *  offered at all.
 *
 *  Each escalated step's own verdict (`failure.retryable`, from `models.RETRYABLE_CLASSES`), read
 *  off its node rather than a class list re-derived here. ALL of them: a Retry re-runs every one,
 *  so a single failure a retry cannot fix (a rejected key, a missing model) fails the new run the
 *  same way. `retryAt` is when the attempt can run, in epoch seconds, `0` for now: the server sets
 *  it while a provider's circuit breaker is open, when a Retry is refused without a call. */
export function retryWindow(
  run: Pick<WorkflowRunDetailData, 'status' | 'escalations' | 'nodes'> | null | undefined,
): { retryAt: number } | null {
  const failures = escalatedFailures(run)
  if (!failures || !failures.every((f) => f?.retryable === true)) return null
  return { retryAt: Math.max(0, ...failures.map((f) => f?.retry_at ?? 0)) }
}

type RunFailures = Pick<WorkflowRunDetailData, 'status' | 'escalations' | 'nodes'>

/** The failure of each step that stopped a failed run, read off its node; `null` for a run that
 *  did not fail on an escalation. */
function escalatedFailures(run: RunFailures | null | undefined) {
  if (!run || run.status !== 'failed') return null
  const escalations = readEscalations(run.escalations)
  if (escalations.length === 0) return null
  return escalations.map((e) =>
    (run.nodes ?? []).find(
      (n) =>
        n.state === 'failed' &&
        (e.instancePath ? n.instance_path === e.instancePath : n.node_id === e.nodeId),
    )?.failure,
  )
}

/** A run a spend cap stopped: every step that stopped it failed because a spend ceiling refused
 *  its model call (class `budget`, which only that refusal is). Nothing in the workflow needs
 *  changing, and the engine never retries it: once the cap has room the same steps can run.
 *  Returns the step's own way out (`fix`), the words the gateway says every cap refusal in, or
 *  `null` for any other run. */
export function spendCapStop(run: RunFailures | null | undefined): { fix: string } | null {
  const failures = escalatedFailures(run)
  if (!failures || !failures.every((f) => f?.class === 'budget')) return null
  return { fix: failures.find((f) => f?.remediation)?.remediation ?? '' }
}

/** When the day's spend caps reset (`UsageBudget.resets_at`, epoch seconds) on the reader's own
 *  clock, as "12:00 AM"; empty while it is not known. */
export function capResetTime(resetsAt: number): string {
  if (!(resetsAt > 0)) return ''
  return new Date(resetsAt * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

/** Whether the daily caps have room for a new run: each one set has less spent than it allows —
 *  it was raised past what was spent, removed (0), or reset at midnight. An unreadable cap has no
 *  room anyone can vouch for. */
export function capsHaveRoom(budget: UsageBudget | null | undefined): boolean {
  if (!budget || budget.cap_unreadable) return false
  const fits = (cap: number | null, spent: number) => (cap ?? 0) <= 0 || spent < (cap ?? 0)
  return fits(budget.max_dollars_per_day, budget.spent_dollars) && fits(budget.max_tokens_per_day, budget.spent_tokens)
}

/** The one-line form for a glance surface (the chat card). Never empty: a run that is
 *  holding something must say so even when the record carries no words of its own. */
export function attentionLine(raw: unknown, fallback = 'Waiting on you'): string {
  const read = readAttention(raw)
  if (read === null) return fallback
  if (read.kind === 'escalation') return `Stopped: ${read.headline}`
  return read.prompt || fallback
}
