import { useState } from 'react'
import { FieldError } from '../../ui/forms'
import { api, type InboxItem, type WorkflowContinuation } from '../../lib/api'
import { WorkflowAsk } from '../workflows/WorkflowAsk'

/** Answer a control-bridge action from the inbox.
 *
 *  A local agent on the control bridge asked to run an action that needs your confirmation. Its
 *  row names the action and what it was asked with, and it is answered HERE, where you are, by
 *  you: Approve runs it once, Deny drops it. The agent that asked cannot answer it
 *  (`approval_answer`). The same card renderer as a trigger's park, so the cards read alike. */
export function BridgeConfirmActions({ item, onChanged }: { item: InboxItem; onChanged: () => void }) {
  const refs = (item.refs ?? {}) as Record<string, unknown>
  const confirmation = String(refs.confirmation ?? '')
  const continuation: WorkflowContinuation = {
    resume_token: confirmation,
    node_id: '',
    instance_path: '',
    ask: { kind: 'approval', prompt: String(item.message ?? ''), rerun: true },
    handoff: {},
    expires_at: 0,
    expired: false,
  }
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState('')

  async function answer(_c: WorkflowContinuation, value: unknown) {
    const confirm = value === true
    setBusy(true)
    setErr('')
    try {
      const res = await api.answerBridgeConfirmation(confirmation, confirm)
      setDone(
        res.status === 'declined'
          ? 'Declined. Nothing ran; the agent has to ask again.'
          : `Ran ${res.action ?? 'it'} once.`,
      )
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not answer this')
    } finally {
      setBusy(false)
    }
  }

  if (done) return <p data-type="body-s" className="text-on-surface-var">{done}</p>
  return (
    <div className="flex flex-col gap-m">
      <WorkflowAsk
        continuation={continuation}
        runId=""
        busy={busy}
        onAnswer={answer}
        rerunCaption="Approve runs it once, now. Deny drops it."
      />
      {err && <FieldError>{err}</FieldError>}
    </div>
  )
}
