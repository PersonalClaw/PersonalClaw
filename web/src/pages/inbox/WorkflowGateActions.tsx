import { useCallback, useEffect, useState } from 'react'
import { FieldError } from '../../ui/forms'
import { api, type WorkflowContinuation } from '../../lib/api'
import { WorkflowAsk } from '../workflows/WorkflowAsk'
import { TextLink } from '../../ui/TextLink'

/** Run statuses a run cannot leave — `workflows/models.TERMINAL_RUN_STATUSES`, whose Python
 *  definition is `ENDED - RESUMABLE_ENDED` with the resumable set deliberately EMPTY ("a run is
 *  one attempt, so every way it can stop is a way it stops for good"). Mirrored rather than
 *  derived because the wire carries a bare string; the rail in `terminalRunHasNoGate.test.tsx`
 *  compares this set against the Python source so the two cannot drift. */
const TERMINAL_RUN_STATUSES = new Set(['complete', 'failed', 'cancelled', 'escalated'])

/** How to say each ending in a sentence. `complete` is included for completeness of the map, not
 *  because a completed run's gate is a common sight — a run that finished having left a gate open
 *  is exactly as unanswerable as one that failed. */
const ENDED_VERB: Record<string, string> = {
  complete: 'finished', failed: 'failed', cancelled: 'was cancelled', escalated: 'was escalated',
}

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
  const [runStatus, setRunStatus] = useState('')
  // 🪤 A FAILED LOOKUP USED TO READ AS "nothing pending", which then printed "This request was
  // already answered." for a run nobody could reach. Three different facts (a dead run, an
  // answered gate, an unreadable one) had one sentence between them; each has its own now.
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await api.workflowContinuations(runId)
      // Scoped to the node this row is about: a run with two concurrent gates raises two
      // rows, and showing both asks under each would make it impossible to tell which row
      // you just answered.
      const all = res.continuations ?? []
      setRunStatus(res.run_status ?? '')
      setLoadFailed(false)
      setConts(nodeId ? all.filter((c) => c.node_id === nodeId) : all)
    } catch {
      setLoadFailed(true)
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
    return <p data-type="body-s" className="text-on-surface-low">Loading the request…</p>
  }

  if (conts.length === 0) {
    // Three reasons the list is empty, and they are not interchangeable. The run's own status
    // decides, which is why the route sends it: telling someone their question was "already
    // answered" when the run died holding it sends them looking for their own answer.
    const ended = TERMINAL_RUN_STATUSES.has(runStatus)
    const reason = loadFailed
      ? "Couldn't check this request."
      : ended
        ? `This run ${ENDED_VERB[runStatus] ?? 'ended'}, so the request can no longer be answered.`
        : 'This request was already answered.'
    return (
      <p data-type="body-s" className="text-on-surface-low">
        {reason}{' '}
        <TextLink onClick={() => navigate(`workflows/${runId}`)}>
          Open the run
        </TextLink>
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-m">
      {conts.map((c) => (
        <WorkflowAsk key={c.resume_token} continuation={c} runId={runId} busy={busy} onAnswer={answer} />
      ))}
      {err && <FieldError>{err}</FieldError>}
    </div>
  )
}
