import { AlertTriangle, Pencil, RotateCcw } from 'lucide-react'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import type { EscalationRead } from './attentionMeta'

/** What the engine knew when it gave up (#565).
 *
 *  A run whose node exhausts its retries ends with a line naming that step and its cause
 *  ("“consume” failed: ConnectionError: network down."). That line used to be empty on exactly
 *  the runs that carry an escalation, and the record on `run.attention` was the only account of
 *  what happened — with no surface reading it, so the run page showed a failed run with nothing
 *  beside it. A run recorded before the line existed still reads that way, which is why this panel
 *  never depends on it.
 *
 *  What this panel adds over the error line above it is the part that is nowhere else:
 *
 *  • **the reason** — retries spent, or which breaker tripped. "It failed" and "I tried four
 *    times and stopped" are different facts, and only the second tells the user whether to
 *    re-run or to change the workflow.
 *  • **the attempt history** — per attempt, the failure class and the engine's own derived
 *    `fix_instruction`. That is the actionable field in the whole record, and it is written
 *    once per attempt precisely so a reader does not have to re-derive it.
 *
 *  `detail` is shown only when the run's error line does not already say it — whole, or quoted
 *  inside the sentence that names the failed step. Two copies of one sentence reads as two
 *  problems.
 *
 *  The record's five `options` are still not rendered as controls — see `attentionMeta.ts`: no
 *  endpoint accepts one back. A stopped run is not waiting on anyone, so the panel does not say
 *  it needs a decision; it offers the two ways forward that work on a finished run. `retry` is
 *  passed only when EVERY escalated step failed in a way a fresh attempt can clear, and is built
 *  from fork + start; while a provider's circuit breaker is open it waits and says when it can
 *  run (pressed then, the new run was refused in microseconds). `editHref` opens the workflow's
 *  editor, for the failure a fresh attempt repeats until the step itself changes.
 *
 *  A step is named by its label (`nameOf`), as the run's ending and failure lines name it.
 *
 *  Every escalation is shown, oldest first. `run.attention` holds one, and each escalation
 *  overwrote the last, so a run whose two steps both gave up used to explain only the second. */
export function EscalationPanel({ reads, runStatus, runError = '', retry, editHref, nameOf }: {
  reads: EscalationRead[]
  runStatus: string
  runError?: string
  retry?: { onRetry: () => void; busy: boolean; waitSecs: number }
  /** The editor of the workflow this run ran, offered on a run that stopped. */
  editHref?: string
  /** What a step is called, by its id. */
  nameOf?: (nodeId: string) => string
}) {
  const edit = stopped(runStatus) ? editHref : undefined
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
        <EscalationEntry key={read.instancePath || `${read.nodeId}-${index}`} read={read} runError={runError} nameOf={nameOf} />
      ))}

      {(retry || edit) && (
        <div className="flex flex-wrap items-center gap-s border-outline-variant border-t pt-s">
          {retry && (
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
          )}
          {edit && (
            <TextLink href={edit} size="sm" ink="emphasis" icon={Pencil} iconSize={13}>
              Change the workflow
            </TextLink>
          )}
          <p data-type="caption" className="text-on-surface-low">{waysForward(retry, !!edit)}</p>
        </div>
      )}
    </section>
  )
}

/** Whether the run is over because a step gave up — the runs this panel offers a way forward on. */
function stopped(runStatus: string): boolean {
  return runStatus === 'failed' || runStatus === 'escalated'
}

/** What each offered way forward does, for the ways offered. */
function waysForward(retry: { waitSecs: number } | undefined, edit: boolean): string {
  const change = 'change the step that gave up, then run the workflow again'
  if (retry && retry.waitSecs > 0) {
    return `Retry becomes available in ${retry.waitSecs}s. Calls to this provider are paused after repeated failures, and a retry before then is refused without being tried.${edit ? ` Or ${change}.` : ''}`
  }
  if (retry) {
    return `A new run keeps every finished step and re-runs the rest. Retry once the cause above has cleared${edit ? `, or ${change}` : ''}.`
  }
  return `A new run of this workflow fails the same way until the step changes: ${change}.`
}

/** The panel's heading, true for the run's own status. An escalation is not always a stop: a
 *  `foreach` whose `on_item_error` is `skip` finishes with its failed items escalated, and a
 *  "stopped" heading over a Completed badge contradicted the page it sat on. */
export function escalationHeading(runStatus: string, count: number): string {
  if (stopped(runStatus)) return 'This run stopped'
  const steps = count === 1 ? '1 step' : `${count} steps`
  if (runStatus === 'complete') return `This run finished, but ${steps} failed`
  return `${steps} failed so far`
}

/** The item a `foreach` escalation belongs to (`…body#2` → `#2`), so two of them are told apart. */
function itemSuffix(instancePath: string): string {
  const match = /#(\d+)$/.exec(instancePath)
  return match ? ` #${match[1]}` : ''
}

/** `text` on one line with no closing stop, so a cause compares with the sentence that quotes it. */
function oneLine(text: string): string {
  return text.split(/\s+/).join(' ').trim().replace(/[\s.]+$/, '')
}

function EscalationEntry({ read, runError, nameOf }: {
  read: EscalationRead
  runError: string
  nameOf?: (nodeId: string) => string
}) {
  const own = oneLine(read.detail)
  const detail = own && !oneLine(runError).includes(own) ? read.detail.trim() : ''
  const name = read.nodeId ? (nameOf?.(read.nodeId) || read.nodeId) : ''
  return (
    <div className="flex flex-col gap-s">
      <div className="flex min-w-0 flex-col gap-xs">
        <p data-type="body-s" className="text-on-surface-var">
          {/* The headline is a fragment (the chat card writes "Stopped: …" before it); here it
              opens the sentence. */}
          {read.headline.charAt(0).toUpperCase() + read.headline.slice(1)}
          {name && (
            <>
              {' at '}
              {/* Named as the run's own ending names it: the label, quoted; an id stays code. */}
              <span className={name === read.nodeId ? 'font-mono' : undefined}>
                {name === read.nodeId ? name : `“${name}”`}{itemSuffix(read.instancePath)}
              </span>
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
