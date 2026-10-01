import { useCallback, useEffect, useState } from 'react'
import { api, type PendingApproval } from '../../lib/api'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { reportingWrite } from '../../app/reportingWrite'
import { workflowApprovalOf } from '../../app/approvalDestination'
import { PendingApprovalCard } from '../../app/PendingApprovalCard'

/** Pending TOOL approvals raised by this run, answerable here (#258).
 *
 *  A `stage` node that spawns a subagent raises an approval through the global approvals
 *  queue, not through the engine's own gate mechanism. The two look identical to a user and
 *  behaved completely differently: a gate renders `WorkflowAsk` on this page with Approve/Deny,
 *  while a tool approval rendered NOTHING here. The user who pressed Run, watching the run,
 *  had no control at all — the node's only affordances were "Re-run" — and the dashboard's
 *  Action Center was the sole surface that could resolve it, with nothing pointing there.
 *
 *  So this is the other half of the same fix as the nudge's destination: `approvalDestination`
 *  sends the toast to `#/workflows/runs/<run>?node=<node>`, and this is what makes that address
 *  a place where the question can be answered rather than a nicer dead end. Both read
 *  `workflowApprovalOf` — ONE parse of the `workflow:<run>:<node>` session key — so a link that
 *  arrives here and a row that appears here cannot disagree about which run owns an approval.
 *
 *  Each is the one approval card (`PendingApprovalCard`): the tool and its risk, what it can touch,
 *  the whole command or question a click away, and the step that asked. It used to say "This step
 *  needs your approval to run bash" with Approve and Reject and nothing of the command, while the
 *  Inbox showed it, so the run's watcher approved it unseen.
 */
export function RunToolApprovals({ runId }: { runId: string }) {
  const [pending, setPending] = useState<PendingApproval[]>([])
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const all = await api.approvals()
      setPending(all.filter((a) => workflowApprovalOf(a.session, runId)))
    } catch {
      /* A failed read keeps the last-good rows: blanking a pending approval would put the user
         back where this fix started — a blocked run with nothing on screen. */
    }
  }, [runId])

  useEffect(() => { load() }, [load])
  // The approvals queue is fanned out over the multiplexed WS, so this needs no poll: `state`
  // broadcasts `approval` on raise and `approval_resolved` when one ends — answered from Home or
  // chat, expired, or cancelled with its run. Listening to the first frame only is how a
  // cancelled run's page went on offering Approve/Reject for a step that would never run. And it
  // re-reads when the socket comes back: a restart resumes the run, and the approval its step
  // raises is broadcast while this page is still reconnecting.
  useChatSocket((m: WsMessage) => {
    if (m.type === 'approval' || m.type === 'approval_resolved') load()
  }, load)

  const decide = useCallback(async (a: PendingApproval, action: 'approve' | 'reject') => {
    setBusy(a.id)
    // `reportingWrite` so a refused decision says the server's own sentence instead of leaving
    // the row in place with nothing happening twice.
    await reportingWrite(`${action} this approval`, () => api.resolveApproval(a.id, action))
    setBusy(null)
    load()
  }, [load])

  if (pending.length === 0) return null

  return (
    <div className="flex flex-col gap-m">
      {pending.map((a) => (
        <PendingApprovalCard key={a.id} approval={a} busy={busy === a.id}
          onDecide={(action) => decide(a, action)} />
      ))}
    </div>
  )
}
