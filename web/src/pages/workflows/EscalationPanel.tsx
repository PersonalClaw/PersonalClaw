import { AlertTriangle } from 'lucide-react'
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
 *  Read-only, deliberately: see `attentionMeta.ts` on why the record's five `options` are not
 *  rendered as controls. */
export function EscalationPanel({ read, runError = '' }: { read: EscalationRead; runError?: string }) {
  const detail = read.detail.trim() && read.detail.trim() !== runError.trim() ? read.detail.trim() : ''

  return (
    <section
      aria-labelledby="escalation-heading"
      data-testid="escalation-panel"
      className="flex flex-col gap-s rounded-lg border border-outline-variant bg-surface-high p-m"
    >
      <div className="flex items-start gap-s">
        <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warning" />
        <div className="flex min-w-0 flex-col gap-xs">
          <h2 id="escalation-heading" data-type="label-s" className="text-on-surface">
            This run stopped and needs a decision
          </h2>
          <p data-type="body-s" className="text-on-surface-var">
            {read.headline}
            {read.nodeId && (
              <>
                {' at '}
                <span className="font-mono">{read.nodeId}</span>
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
    </section>
  )
}
