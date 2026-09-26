import { AlertTriangle, RotateCcw } from 'lucide-react'
import { Button } from '../../ui/Button'
import type { EscalationRead } from './attentionMeta'

/** What the engine knew when it gave up (#565).
 *
 *  A run whose node exhausts its retries goes terminal through `_finish(status)` with **no**
 *  `error` argument, so `run.error_message` is empty on exactly the runs that carry an
 *  escalation. The record on `run.attention` was the only account of what happened, and no
 *  surface read it: the run page showed a failed run with nothing beside it.
 *
 *  What this panel adds over the error line above it is the part that was never anywhere else:
 *
 *  • **the reason** — retries spent, or which breaker tripped. "It failed" and "I tried four
 *    times and stopped" are different facts, and only the second tells the user whether to
 *    re-run or to change the workflow.
 *  • **the attempt history** — per attempt, the failure class and the engine's own derived
 *    `fix_instruction`. That is the actionable field in the whole record, and it is written
 *    once per attempt precisely so a reader does not have to re-derive it.
 *
 *  `detail` is shown only when it differs from the run's error line, which is where the same
 *  string appears when the run failed loudly. Two copies of one sentence reads as two problems.
 *
 *  The record's five `options` are still not rendered as controls — see `attentionMeta.ts`: no
 *  endpoint accepts one back. The ONE control here is `retry`, which the page passes only when
 *  EVERY escalated step failed in a way a fresh attempt can clear, and which is built from verbs
 *  that work on a finished run (fork + start). While a provider's circuit breaker is open it
 *  waits and says when it can run: pressed then, the new run was refused in microseconds.
 *
 *  Every escalation is shown, oldest first. `run.attention` holds one, and each escalation
 *  overwrote the last, so a run whose two steps both gave up used to explain only the second. */
export function EscalationPanel({ reads, runStatus, runError = '', retry }: {
  reads: EscalationRead[]
  runStatus: string
  runError?: string
  retry?: { onRetry: () => void; busy: boolean; waitSecs: number }
}) {
  return (
    <section
      aria-labelledby="escalation-heading"
      data-testid="escalation-panel"
      className="flex flex-col gap-s rounded-lg border border-outline-variant bg-surface-high p-m"
    >
      <h2 id="escalation-heading" data-type="label-s" className="flex items-center gap-s text-on-surface">
        <AlertTriangle size={14} className="shrink-0 text-warning" />
        {escalationHeading(runStatus, reads.length)}
      </h2>

      {reads.map((read, index) => (
        <EscalationEntry key={read.instancePath || `${read.nodeId}-${index}`} read={read} runError={runError} />
      ))}

      {retry && (
        <div className="flex flex-wrap items-center gap-s border-outline-variant border-t pt-s">
          <Button
            variant="ghost-accent"
            size="sm"
            onClick={retry.onRetry}
            loading={retry.busy}
            disabled={retry.waitSecs > 0}
            disabledReason={retry.waitSecs > 0 ? `Retry becomes available in ${retry.waitSecs}s` : undefined}
            title="Start a new run that keeps every finished step and re-runs the rest"
          >
            <RotateCcw size={13} /> Retry
          </Button>
          <p data-type="caption" className="text-on-surface-low">
            {retry.waitSecs > 0
              ? `Retry becomes available in ${retry.waitSecs}s. Calls to this provider are paused after repeated failures, and a retry before then is refused without being tried.`
              : 'A new run keeps every finished step and re-runs the rest. Retry once the cause above has cleared.'}
          </p>
        </div>
      )}
    </section>
  )
}

/** The panel's heading, true for the run's own status. An escalation is not always a stop: a
 *  `foreach` whose `on_item_error` is `skip` finishes with its failed items escalated, and a
 *  "stopped" heading over a Completed badge contradicted the page it sat on. */
export function escalationHeading(runStatus: string, count: number): string {
  if (runStatus === 'failed' || runStatus === 'escalated') return 'This run stopped and needs a decision'
  const steps = count === 1 ? '1 step' : `${count} steps`
  if (runStatus === 'complete') return `This run finished, but ${steps} failed`
  return `${steps} failed so far`
}

/** The item a `foreach` escalation belongs to (`…body#2` → `#2`), so two of them are told apart. */
function itemSuffix(instancePath: string): string {
  const match = /#(\d+)$/.exec(instancePath)
  return match ? ` #${match[1]}` : ''
}

function EscalationEntry({ read, runError }: { read: EscalationRead; runError: string }) {
  const detail = read.detail.trim() && read.detail.trim() !== runError.trim() ? read.detail.trim() : ''
  return (
    <div className="flex flex-col gap-s">
      <div className="flex min-w-0 flex-col gap-xs">
        <p data-type="body-s" className="text-on-surface-var">
          {read.headline}
          {read.nodeId && (
            <>
              {' at '}
              <span className="font-mono">{read.nodeId}{itemSuffix(read.instancePath)}</span>
            </>
          )}
          .
        </p>
        {detail && (
          <p data-type="body-s" className="whitespace-pre-wrap break-words text-on-surface-low">
            {detail}
          </p>
        )}
      </div>

      {read.attempts.length > 0 && (
        <ol className="flex flex-col gap-s border-outline-variant border-t pt-s">
          {read.attempts.map((a) => (
            <li key={`${a.attempt}-${a.signature}`} className="flex flex-col gap-xs">
              <div className="flex flex-wrap items-center gap-s">
                <span data-type="caption" className="text-on-surface">
                  Attempt {a.attempt}
                </span>
                {a.failureClass && (
                  <span data-type="caption" className="text-on-surface-low">
                    {a.failureClass} {a.severity === 'warning' ? 'warning' : 'error'}
                  </span>
                )}
                {a.signature && (
                  <span data-type="caption" className="font-mono text-on-surface-low">
                    {a.signature}
                  </span>
                )}
              </div>
              {a.error && (
                <p data-type="body-s" className="whitespace-pre-wrap break-words text-on-surface-var">
                  {a.error}
                </p>
              )}
              {/* The engine's own next move, labelled as such: unlabelled it reads as more
                  error text, and it is the one line here a user can act on. */}
              {a.fixInstruction && (
                <p data-type="body-s" className="text-on-surface">
                  <span className="text-on-surface-low">Suggested fix: </span>
                  {a.fixInstruction}
                </p>
              )}
              {(a.expected || a.actual) && (
                <p data-type="caption" className="whitespace-pre-wrap break-words text-on-surface-low">
                  {a.expected && <>expected {a.expected}</>}
                  {a.expected && a.actual && ' · '}
                  {a.actual && <>got {a.actual}</>}
                </p>
              )}
              {a.evidence && (
                <p data-type="caption" className="whitespace-pre-wrap break-words text-on-surface-low">
                  {a.evidence}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
