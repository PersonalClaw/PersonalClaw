import { Cog, Server } from 'lucide-react'
import type { AppProcessExit, AppSummary, AppWorkerStatus } from '../../lib/api'
import { expiryStamp, fullStamp, isoStamp } from '../../lib/epoch'

/** The processes an app runs, on its panel: its backend and its background workers — running or
 *  not, and for one that is not, how its last run ended and the last lines it printed. The gateway
 *  masks those lines before they leave it (`child_output`), and every line also reached the log
 *  as it was printed (Settings → Diagnostics → Live logs). */
export function BackendStatus({ app }: { app: AppSummary }) {
  return (
    <section role="region" aria-label="Backend" className="rounded-md border border-outline-variant bg-surface-high p-m" data-type="body-s">
      <div className="flex items-center gap-s text-on-surface"><Server size={14} aria-hidden="true" /> Backend</div>
      <div className="mt-xs text-on-surface-low">
        {app.backendRunning ? `running on port ${app.backendPort}` : 'not running'}
      </div>
      {!app.backendRunning && app.backendExit && <ProcessExit exit={app.backendExit} />}
    </section>
  )
}

/** A background worker's state in a few words: what its supervisor says about it, and why. */
function workerState(worker: AppWorkerStatus): string {
  if (worker.state === 'paused') return worker.reason ? `paused: ${worker.reason}` : 'paused'
  if (worker.state === 'failed') return worker.reason ? `stopped: ${worker.reason}` : 'stopped'
  return worker.running ? 'running' : 'not running'
}

export function WorkerStatus({ worker }: { worker: AppWorkerStatus }) {
  return (
    <section role="region" aria-label="Background worker" className="rounded-md border border-outline-variant bg-surface-high p-m" data-type="body-s">
      <div className="flex items-center gap-s text-on-surface"><Cog size={14} aria-hidden="true" /> Background worker</div>
      <div className="mt-xs text-on-surface-low">{workerState(worker)}</div>
      {!worker.running && worker.exit && <ProcessExit exit={worker.exit} />}
    </section>
  )
}

/** How a process's last run ended and when (to the minute, with the day when that is not today),
 *  the line it printed that says why, and the last lines it printed, folded away until asked for (a
 *  traceback is a detail, not a reason). */
function ProcessExit({ exit }: { exit: AppProcessExit }) {
  const at = expiryStamp(exit.endedAt)
  const count = exit.lines.length
  return (
    <div className="mt-xs flex flex-col gap-xs text-on-surface-low">
      <div>
        Its last run {exit.ended}
        {at && <> at <time dateTime={isoStamp(exit.endedAt)} title={fullStamp(exit.endedAt)}>{at}</time></>}.
      </div>
      {exit.cause && <div data-type="label-s" className="break-words font-mono text-on-surface">{exit.cause}</div>}
      {count > 0 && (
        <details>
          <summary data-type="label-s" className="cursor-pointer text-on-surface-var">
            {count === 1 ? 'The last line it printed' : `The last ${count} lines it printed`}
          </summary>
          <pre aria-label="The last lines it printed" data-type="caption"
            className="mt-xs max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface-container p-s font-mono text-on-surface-low">
            {exit.lines.join('\n')}
          </pre>
        </details>
      )}
    </div>
  )
}
