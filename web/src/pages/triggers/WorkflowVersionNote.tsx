import { useState } from 'react'
import { History } from 'lucide-react'
import { api, ApiError, type AutomationWorkflowStep, type AutomationWorkflowVersion } from '../../lib/api'
import { ConsentDeclined } from '../../lib/securityConsent'
import { Button } from '../../ui/Button'
import { FieldError } from '../../ui/forms'
import { BUSY_REASON } from '../../ui/unavailable'
import { savedByWords } from '../workflows/savedBy'

/** Who saved each version after the one an automation runs, as one line: "v6 by an agent". */
export function savedSince(since: AutomationWorkflowVersion['since']): string {
  return since.map((row) => `v${row.version} by ${savedByWords(row.saved_by)}`).join('; ')
}

/** The steps whose workflow has a version the automation does not run, or that would not run. */
function waitingSteps(version: AutomationWorkflowVersion): AutomationWorkflowStep[] {
  return version.steps.filter((step) => step.newer > 0 || step.problem)
}

/** One workflow the automation runs as a step, when what that step runs is not its newest. */
function stepLine(step: AutomationWorkflowStep): string {
  if (step.problem) return `“${step.workflow}”, which “${step.via}” runs as a step: ${step.problem}`
  return `It runs “${step.workflow}” as a step of “${step.via}” at v${step.runs}; saved since: ${savedSince(step.since)}.`
}

function heading(version: AutomationWorkflowVersion, waiting: AutomationWorkflowStep[]): string {
  if (version.newer) return `“${version.workflow}” has a newer version, v${version.newer}`
  if (version.problem) return `“${version.workflow}” cannot run`
  if (waiting.length === 1) return `“${waiting[0].workflow}”, which it runs as a step, has a newer version`
  return 'Workflows it runs as steps have newer versions'
}

/** Which version of its workflow a "Run workflow" automation runs, when it is not the workflow's
 *  newest (`workflow_version`, the server's verdict), and the same of each workflow its steps
 *  start. A newer version the owner saves in a workflow's editor is followed on its own, so this
 *  shows for one anything else saved — an agent, a sync, an import, an app — or when a fire or a
 *  step would run nothing (`problem`), and offers Use vN: what the row's `use` says it moves. It
 *  asks first: the gateway's question names who saved each version since, and nothing changes
 *  until the owner allows it. */
export function WorkflowVersionNote({ triggerId, version, onChanged, readOnly = false, busy = false }: {
  /** The Triggers page's namespaced id (`store:…`, `schedule:…`, `lifecycle:…`). */
  triggerId: string
  version: AutomationWorkflowVersion
  /** Read the row again: after Use, and after a Use refused because what it offered changed
   *  since the row was read, so the owner sees what it offers now. */
  onChanged: () => void
  /** Someone else's automation: which version it runs is theirs to say. */
  readOnly?: boolean
  busy?: boolean
}) {
  const [using, setUsing] = useState(false)
  const [err, setErr] = useState('')
  const waiting = waitingSteps(version)
  const use = version.use
  if (!use && !version.problem && waiting.length === 0) return null
  async function onUse() {
    if (!use) return
    setUsing(true)
    setErr('')
    try {
      await api.allowWorkflowVersion(triggerId, use)
      onChanged()
    } catch (e) {
      if (e instanceof ConsentDeclined) return
      setErr(e instanceof Error ? e.message : 'Could not change it')
      if (e instanceof ApiError && e.code === 'stale_write') onChanged()
    } finally {
      setUsing(false)
    }
  }
  return (
    <div role="note" className="flex items-start gap-s text-warn">
      <History size={14} aria-hidden className="shrink-0" />
      <div data-type="body-s" className="flex min-w-0 flex-1 flex-col gap-xs">
        <p data-type="label-m">{heading(version, waiting)}</p>
        {version.problem ? (
          <p className="break-words text-on-surface-var">{version.problem}</p>
        ) : version.newer ? (
          <p className="break-words text-on-surface-var">
            This automation runs v{version.runs},{' '}
            {version.runs === version.allowed ? 'the version you allowed' : 'the newest version you saved in the workflow’s editor'}.
            Saved since: {savedSince(version.since)}.
          </p>
        ) : null}
        {waiting.map((step) => (
          <p key={step.workflow} className="break-words text-on-surface-var">{stepLine(step)}</p>
        ))}
        <p className="break-words text-on-surface-var">
          A version you save in a workflow’s editor is used without asking; any other waits for you.
        </p>
        {use && !readOnly && (
          <div>
            <Button variant="secondary" size="sm" onClick={onUse} loading={using} disabled={busy} disabledReason={BUSY_REASON}>
              {version.newer || version.problem ? `Use v${use.version}` : 'Use the newest versions'}
            </Button>
          </div>
        )}
        {err && <FieldError>{err}</FieldError>}
      </div>
    </div>
  )
}
