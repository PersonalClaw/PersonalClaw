import { useState } from 'react'
import { Check, ExternalLink, Play, RotateCcw } from 'lucide-react'
import { ApiError, api, type InboxItem, type Trigger, type TriggerRunResult } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { ConsentDeclined } from '../../lib/securityConsent'
import { workflowApprovalSession } from '../../app/approvalDestination'
import { Button } from '../../ui/Button'
import { FieldError } from '../../ui/forms'
import { InlineLoadError } from '../../ui/ListScaffold'
import { BUSY_REASON } from '../../ui/unavailable'
import { humanizeEvent } from '../triggers/triggerMeta'
import { isPrelaunch, isTerminal } from '../workflows/workflowMeta'
import { rewindNode } from '../workflows/reentry'
import { isOpen } from './inboxMeta'
import { InboxSection as Section } from './InboxSection'

/** What "Ask it to try again" sends to the chat whose approval ran out. */
function retryText(tool: string): string {
  return `The approval for ${tool} ran out before I answered, so it did not run. I'm here now — please try that again.`
}

const LABEL = 'Run it again'

/** Who answered a retry that settled this note (`refs.retry_by`, `approval_grants`): a person
 *  (`you`), or the standing grant that ran it without asking. A closed map from the backend's
 *  closed vocabulary; a name it does not know reads as a permission, never as a person. */
const RAN_WITHOUT_ASKING: Record<string, string> = {
  trust: 'this chat’s Trust',
  yolo: 'YOLO',
  trust_reads: 'Trust reads',
  agent_floor: 'the agent’s “Always allow”',
  parent_trust: 'the Trust of the chat that started it',
  approval_mode: 'its own approval mode',
  setting: 'the Auto-approve setting',
  hook_setting: 'the hook settings',
  hook_pattern: 'an auto-approve pattern in the hook settings',
  source: 'the hook settings’ auto-approved sources',
  cli: 'the gateway’s --approval flag',
  app_grant: 'the app’s grant',
  session_policy: 'the session’s approval policy',
  no_approval_surface: 'the gateway, which had nowhere to ask',
}

/** How a retry that settled this note ended: asked and answered, or run by a standing grant. */
export function settledSentence(retry: 'approved' | 'rejected', by: unknown, tool: string): string {
  const who = typeof by === 'string' ? by : ''
  if (who === '' || who === 'you') {
    return retry === 'approved' ? `Asked again: you allowed ${tool}.` : `Asked again: you denied ${tool}.`
  }
  return `Ran again without asking: ${RAN_WITHOUT_ASKING[who] ?? 'a standing permission'} allowed ${tool}.`
}

const LIFECYCLE = 'lifecycle:'

/** A call denied without an answer (`system/auto_denied`, `auto_denials.py`), and the
 *  one next step it really has, by where it was asked:
 *
 *  - a chat a person answers in (`refs.chat`): ask that chat to try again, so it asks for the
 *    approval again while someone is here;
 *  - a trigger's run (`refs.trigger`): run that trigger again. Run now refuses a trigger that is
 *    not allowed to run its action (#3702), and this goes through the same Allow its page asks;
 *  - a workflow step (`refs.session` is `workflow:<run>:<node>`): run that step again, while the
 *    run is live. A finished run never runs again (`models.RESUMABLE_ENDED_RUN_STATUSES`).
 *
 *  Each is offered only for a call that ASKED and got no answer (`expired`), because asked again
 *  it can be answered. A call an unattended run declined without asking would be declined the
 *  same way, so that note says so rather than offer a button that cannot help.
 *
 *  A lifecycle hook's run (`refs.trigger` is `lifecycle:<id>`) has no Run now; the note says when
 *  it runs again and opens it.
 *
 *  The note is handled once the same call is asked there again and answered, whichever way, or
 *  runs there again because a standing grant approved it (`auto_denials.settle_retried`), and then
 *  this says how it ended and who decided (`refs.retry_by`). */
export function DeniedCallRerun({ item, navigate, onChanged }: {
  item: InboxItem
  navigate: (path: string) => void
  onChanged: () => void
}) {
  const refs = item.refs ?? {}
  const denied = refs.auto_denied
  if (denied !== 'expired' && denied !== 'unattended') return null
  const tool = typeof refs.tool === 'string' && refs.tool ? refs.tool : 'that step'
  if (refs.retry === 'approved' || refs.retry === 'rejected') {
    return (
      <Section label={LABEL}>
        <p data-type="body-s" className="flex items-center gap-1.5 text-on-surface-var">
          <Check size={14} aria-hidden style={{ color: 'var(--color-ok)' }} />
          {settledSentence(refs.retry, refs.retry_by, tool)}
        </p>
      </Section>
    )
  }
  if (!isOpen(item.status)) return null
  const trigger = typeof refs.trigger === 'string' ? refs.trigger : ''
  const step = typeof refs.session === 'string' ? workflowApprovalSession(refs.session) : null
  if (denied === 'unattended') {
    if (!trigger && !step) return null
    return (
      <Section label={LABEL}>
        <p data-type="body-s" className="text-on-surface-var">
          {trigger ? 'It' : 'This step'} ran with nobody there to ask, so running it again would be
          declined the same way.
        </p>
      </Section>
    )
  }
  if (typeof refs.chat === 'string' && refs.chat) {
    return <AskChatAgain chat={refs.chat} tool={tool} navigate={navigate} />
  }
  if (trigger.startsWith(LIFECYCLE)) return <HookRunsAgain triggerId={trigger} tool={tool} />
  if (trigger) return <RunTriggerAgain triggerId={trigger} tool={tool} navigate={navigate} onChanged={onChanged} />
  if (step) return <RunStepAgain runId={step.runId} nodeId={step.nodeId} tool={tool} onChanged={onChanged} />
  return null
}

function AskChatAgain({ chat, tool, navigate }: { chat: string; tool: string; navigate: (path: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const message = retryText(tool)
  async function ask() {
    setBusy(true); setErr('')
    try {
      await api.sendChat(message, chat)
      navigate(`chat/${encodeURIComponent(chat)}`)
    } catch (e) { setErr(`Couldn't ask the chat to try again: ${e instanceof Error ? e.message : 'unknown error'}`) }
    finally { setBusy(false) }
  }
  return (
    <Section label={LABEL}>
      <p data-type="body-s" className="text-on-surface-var">
        Asks the chat to try again, so it asks you for the approval while you are here. It sends: “{message}”
      </p>
      <div className="mt-s">
        <Button size="sm" onClick={ask} loading={busy} disabled={busy} disabledReason={BUSY_REASON}>
          <RotateCcw size={14} /> Ask it to try again
        </Button>
      </div>
      {err && <FieldError>{err}</FieldError>}
    </Section>
  )
}

/** A lifecycle hook (`lifecycle:<id>`) fires on the agent's own events and has no Run now: its
 *  Test is a rehearsal, not the fire that asked. So the note names the hook and says when it runs
 *  again, rather than offer a button that would not ask for the call again; the note's own link
 *  opens the hook (`inboxMeta.refRoute`). */
function HookRunsAgain({ triggerId, tool }: { triggerId: string; tool: string }) {
  const { data, error, refresh } = useQuery('triggers:all', () => api.triggers().then((d) => d.triggers))
  if (data === undefined) {
    return (
      <Section label={LABEL}>
        {error
          ? <InlineLoadError what="the trigger" error={error} onRetry={refresh} />
          : <p data-type="body-s" className="text-on-surface-low">Checking the trigger…</p>}
      </Section>
    )
  }
  const row = data.find((t) => t.kind === 'lifecycle' && t.id === triggerId) ?? null
  if (row === null) {
    return (
      <Section label={LABEL}>
        <p data-type="body-s" className="text-on-surface-var">The trigger that ran it no longer exists, so it will not run again.</p>
      </Section>
    )
  }
  const name = row.name || row.raw_id
  const when = row.event ? `on its next ${humanizeEvent(row.event).toLowerCase()} event` : 'the next time it fires'
  return (
    <Section label={LABEL}>
      <p data-type="body-s" className="text-on-surface-var">
        “{name}” runs again {when}{row.enabled ? '' : ' once it is switched on'}, and then it asks you
        for {tool} while you are here.
      </p>
    </Section>
  )
}

/** Run now, for the trigger's own kind of row: a schedule is addressed as `schedule:<id>`, any
 *  other store trigger as `store:<id>` — the two helpers the Triggers page's panels use. */
function runNow(row: Trigger): Promise<TriggerRunResult> {
  return row.kind === 'schedule' ? api.runSchedule(row.raw_id) : api.runStoreTrigger(row.raw_id)
}

/** Allow = the switch sent ON again, where the gateway asks for the grant the action needs. */
function allow(row: Trigger): Promise<unknown> {
  return row.kind === 'schedule' ? api.enableSchedule(row.raw_id, true) : api.toggleStoreTrigger(row.raw_id, true)
}

function names(labels: string[]): string {
  return labels.map((l) => `“${l}”`).join(', ')
}

function RunTriggerAgain({ triggerId, tool, navigate, onChanged }: {
  triggerId: string
  tool: string
  navigate: (path: string) => void
  onChanged: () => void
}) {
  const { data, error, refresh } = useQuery('triggers:all', () => api.triggers().then((d) => d.triggers))
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState('')
  const [err, setErr] = useState('')
  const open = () => navigate(`triggers?open=${encodeURIComponent(triggerId)}`)
  if (data === undefined) {
    return (
      <Section label={LABEL}>
        {error
          ? <InlineLoadError what="the trigger" error={error} onRetry={refresh} />
          : <p data-type="body-s" className="text-on-surface-low">Checking the trigger…</p>}
      </Section>
    )
  }
  const row = data.find((t) => t.kind !== 'lifecycle' && t.raw_id === triggerId) ?? null
  if (row === null) {
    return (
      <Section label={LABEL}>
        <p data-type="body-s" className="text-on-surface-var">The trigger that ran it no longer exists, so it cannot run again.</p>
      </Section>
    )
  }
  const name = row.name || row.raw_id
  const grants = row.needs_grant ?? []
  // Allowing a trigger that is off switches it on, and then it runs on its own again. That is the
  // owner's decision on its page, not a side effect of running it once from here.
  if ((grants.length > 0 && !row.enabled) || row.needs_review) {
    return (
      <Section label={LABEL}>
        <p data-type="body-s" className="text-on-surface-var">
          “{name}” is switched off{grants.length > 0 ? ` and is not allowed to use ${names(grants)}` : ''}.
          Switching it on asks you first and lets it run on its own again, so that is done on its page.
        </p>
        <div className="mt-s">
          <Button size="sm" variant="secondary" onClick={open}><ExternalLink size={14} /> Open the trigger</Button>
        </div>
      </Section>
    )
  }
  async function run(target: Trigger) {
    setBusy(true); setErr(''); setDone('')
    try {
      if ((target.needs_grant ?? []).length > 0) await allow(target)
      const res = await runNow(target)
      if (res.ok === false) {
        setErr(`Couldn't run it again: ${String(res.refused || res.result || 'it did not run')}`)
        return
      }
      setDone(`Started “${name}” again. When it asks for ${tool}, answer it, and this note is marked handled.`)
      refresh()
      onChanged()
    } catch (e) {
      setErr(e instanceof ConsentDeclined
        ? 'Not run: you did not allow it.'
        : `Couldn't run it again: ${e instanceof Error ? e.message : 'unknown error'}`)
    } finally { setBusy(false) }
  }
  return (
    <Section label={LABEL}>
      <p data-type="body-s" className="text-on-surface-var">
        {grants.length > 0
          ? `“${name}” is not allowed to use ${names(grants)} now. Allowing it asks you first; then it runs again and asks you for ${tool} while you are here.`
          : `Runs “${name}” again now, so it asks you for ${tool} while you are here.`}
      </p>
      {done ? (
        <p role="status" data-type="body-s" className="mt-s flex items-center gap-1.5 text-on-surface-var">
          <Check size={14} aria-hidden style={{ color: 'var(--color-ok)' }} /> {done}
        </p>
      ) : (
        <div className="mt-s">
          <Button size="sm" onClick={() => run(row)} loading={busy} disabled={busy} disabledReason={BUSY_REASON}>
            <Play size={14} /> {grants.length > 0 ? 'Allow and run it again' : 'Run it again'}
          </Button>
        </div>
      )}
      {err && <FieldError>{err}</FieldError>}
    </Section>
  )
}

function RunStepAgain({ runId, nodeId, tool, onChanged }: {
  runId: string
  nodeId: string
  tool: string
  onChanged: () => void
}) {
  const { data: run, error, refresh } = useQuery(`workflows:run:${runId}`, () => api.workflowRun(runId))
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState('')
  const [err, setErr] = useState('')
  if (run === undefined) {
    return (
      <Section label={LABEL}>
        {error instanceof ApiError && error.status === 404
          ? <p data-type="body-s" className="text-on-surface-var">The run no longer exists, so this step cannot run again.</p>
          : error
            ? <InlineLoadError what="the workflow run" error={error} onRetry={refresh} />
            : <p data-type="body-s" className="text-on-surface-low">Checking the run…</p>}
      </Section>
    )
  }
  if (isTerminal(run.status) || isPrelaunch(run.status)) {
    return (
      <Section label={LABEL}>
        <p data-type="body-s" className="text-on-surface-var">
          {isTerminal(run.status)
            ? 'The run has ended, so this step cannot run again in it.'
            : 'The run has not started, so there is no step to run again yet.'}
        </p>
      </Section>
    )
  }
  async function again() {
    setBusy(true); setErr(''); setDone('')
    try {
      if (!(await rewindNode(runId, nodeId))) return
      setDone(`The ${nodeId} step is running again. When it asks for ${tool}, answer it, and this note is marked handled.`)
      refresh()
      onChanged()
    } catch (e) { setErr(`Couldn't run the step again: ${e instanceof Error ? e.message : 'unknown error'}`) }
    finally { setBusy(false) }
  }
  return (
    <Section label={LABEL}>
      <p data-type="body-s" className="text-on-surface-var">
        Runs the {nodeId} step of this run again, so it asks you for {tool} while you are here. The steps that read
        its output run again after it.
      </p>
      {done ? (
        <p role="status" data-type="body-s" className="mt-s flex items-center gap-1.5 text-on-surface-var">
          <Check size={14} aria-hidden style={{ color: 'var(--color-ok)' }} /> {done}
        </p>
      ) : (
        <div className="mt-s">
          <Button size="sm" onClick={again} loading={busy} disabled={busy} disabledReason={BUSY_REASON}>
            <RotateCcw size={14} /> Run this step again
          </Button>
        </div>
      )}
      {err && <FieldError>{err}</FieldError>}
    </Section>
  )
}
