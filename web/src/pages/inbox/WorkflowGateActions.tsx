import { useCallback, useEffect, useState } from 'react'
import { api, type WorkflowContinuation } from '../../lib/api'
import { WorkflowAsk } from '../workflows/WorkflowAsk'

/** Answer a workflow's human-input gate from the inbox (WF2-R7).
 *
 *  A `needs_input` row whose only action is "go to the workflow" is a notification with extra
 *  steps: the user came to the inbox to clear it, and being sent elsewhere to do that is the
 *  friction the inbox exists to remove. So the gate is answerable HERE.
 *
 *  It renders `WorkflowAsk` — the same component the run view uses — rather than a
 *  second form. One typed-ask renderer was the whole point of the typed payload; a private
 *  inbox copy would drift the moment a new ask kind lands, and the drift would be silent
 *  (the payload arrives fine, the inbox just cannot show it).
 *
 *  A gate answered elsewhere (the run view, another tab, an auto-approve policy) leaves this
 *  row stale. That is not an error worth shouting about: the component says so plainly and
 *  offers the run, which is the only thing left to look at.
 *
 *  Renders bare content, not its own section: the caller owns the heading, matching how
 *  `ProposalActions` sits inside the detail view's own layout. */
export function WorkflowGateActions({ runId, nodeId, onChanged, navigate }: {
  runId: string
  nodeId?: string
  onChanged: () => void
  navigate: (path: string) => void
}) {
  const [conts, setConts] = useState<WorkflowContinuation[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await api.workflowContinuations(runId)
      // Scoped to the node this row is about: a run with two concurrent gates raises two
      // rows, and showing both asks under each would make it impossible to tell which row
      // you just answered.
      const all = res.continuations ?? []
      setConts(nodeId ? all.filter((c) => c.node_id === nodeId) : all)
    } catch {
      // A deleted (or unreadable) run reads as "nothing pending" — the row is stale either
      // way, and the same message covers both.
      setConts([])
    }
  }, [runId, nodeId])

  useEffect(() => { load() }, [load])

  const answer = useCallback(async (
    cont: WorkflowContinuation, value: unknown, alwaysAllow: boolean,
  ) => {
    setBusy(true); setErr('')
    try {
      await api.resumeWorkflowRun(runId, {
        answer: value, resume_token: cont.resume_token, always_allow: alwaysAllow,
      })
      // The backend closes the inbox row on `gate_resolved`, so a refresh is what makes this
      // row disappear rather than the component hiding itself and lying about the store.
      onChanged()
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not answer this gate')
    } finally {
      setBusy(false)
    }
  }, [runId, onChanged, load])

  if (conts === null) {
    return <p className="text-on-surface-low text-[0.8125rem]">Loading the request…</p>
  }

  if (conts.length === 0) {
    return (
      <p className="text-on-surface-low text-[0.8125rem]">
        This request was already answered.{' '}
        <button type="button" onClick={() => navigate(`workflows/${runId}`)} className="text-primary hover:underline">
          Open the run
        </button>
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-m">
      {conts.map((c) => (
        <WorkflowAsk key={c.resume_token} continuation={c} busy={busy} onAnswer={answer} />
      ))}
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
    </div>
  )
}
