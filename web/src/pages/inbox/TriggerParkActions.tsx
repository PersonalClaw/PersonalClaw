import { useState } from 'react'
import { FieldError } from '../../ui/forms'
import { api, type InboxItem, type WorkflowContinuation } from '../../lib/api'
import { WorkflowAsk } from '../workflows/WorkflowAsk'

/** Answer a trigger's parked action from the inbox.
 *
 *  A trigger's action can stop for a person — browse at a sign-in page. Its row carries the
 *  action's own card (`refs.needs_input`: the question and what it tried) and the park's token, and
 *  is answered HERE with the same renderer an in-run park uses, so the two cards read alike: Approve
 *  runs the action again now, with your answer on that one run; Deny closes the question until the
 *  trigger next runs. The answer goes to the trigger (`/api/triggers/{id}/answer`), not a run — a
 *  trigger's action has none. */
export function TriggerParkActions({ item, onChanged }: { item: InboxItem; onChanged: () => void }) {
  const refs = (item.refs ?? {}) as Record<string, unknown>
  const card = (refs.needs_input && typeof refs.needs_input === 'object' ? refs.needs_input : {}) as Record<string, unknown>
  const triggerId = String(refs.trigger ?? refs.trigger_park ?? '')
  const token = String(card.resume_token ?? refs.resume_token ?? '')
  const attempted = Array.isArray(card.attempted) ? card.attempted.map((a) => String(a)) : []
  const continuation: WorkflowContinuation = {
    resume_token: token,
    node_id: '',
    instance_path: '',
    ask: { kind: 'approval', prompt: String(card.blocker ?? item.message ?? ''), rerun: true },
    handoff: { attempted },
    expires_at: 0,
    expired: false,
  }
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState('')

  async function answer(_c: WorkflowContinuation, value: unknown) {
    const approved = value === true
    setBusy(true)
    setErr('')
    try {
      const res = await api.answerTriggerPark(triggerId, { resume_token: token, answer: approved })
      if (res.refused) {
        setErr(res.refused)
        return
      }
      setDone(
        !approved
          ? 'Declined. It asks again the next time it stops.'
          : res.waiting
            ? 'Ran it again, and it stopped for you again — its new question is in your Inbox.'
            : res.ok
              ? 'Ran it again — see its history for what it did.'
              : `Ran it again, and it ${res.result ?? 'did not finish'}.`,
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
        rerunCaption="Approve runs it again now. Deny leaves it until it next runs."
      />
      {err && <FieldError>{err}</FieldError>}
    </div>
  )
}
