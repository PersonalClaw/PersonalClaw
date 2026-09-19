import { useCallback, useEffect, useState } from 'react'
import { Check, ShieldQuestion, X } from 'lucide-react'
import { Button } from '../../ui/Button'
import { BUSY_REASON } from '../../ui/unavailable'
import { api, type PendingApproval } from '../../lib/api'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { reportingWrite } from '../../app/reportingWrite'
import { workflowApprovalOf } from '../../app/approvalDestination'

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
 *  Deliberately NOT a second approval renderer with risk chips and argument inspection: that is
 *  `pages/chat/ApprovalCard`, reached by the chat path. What a blocked run owes its watcher is
 *  the question and the two verbs.
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
  // The approvals queue is fanned out over the multiplexed WS as its own frame, so this needs no
  // poll: `state` broadcasts on raise AND on resolve, which is also what clears a row answered
  // from the dashboard or from chat while this page is open.
  useChatSocket((m: WsMessage) => { if (m.type === 'approval') load() })

  // Keyed by `<id>:<action>`, not by id alone, so the in-flight state names WHICH verb is running.
  // That is what lets each button publish `aria-busy` for its OWN operation (`loading`) while the
  // other reads as merely blocked (`disabledReason`) — gating both on one row-level flag would
  // announce two concurrent operations, which is the lie `busyIsNotAnnounced` exists to catch.
  const decide = useCallback(async (a: PendingApproval, action: 'approve' | 'reject') => {
    setBusy(`${a.id}:${action}`)
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
        <div key={a.id} className="flex flex-col gap-s rounded-xl border border-outline-variant p-l">
          <span data-type="body-s" className="inline-flex items-center gap-s text-on-surface">
            {/* Decorative: the sentence beside it carries the fact, so it is `aria-hidden` rather
                than named — the form `IntrospectPanel` uses for the same warn-toned glyph, and the
                branch `approvalShieldNamed`'s pages sweep exempts. Naming it would make a
                screen reader read the warning twice. */}
            <ShieldQuestion size={14} className="shrink-0 text-warning" aria-hidden />
            This step needs your approval to run <span className="font-mono">{a.tool}</span>
          </span>
          {a.tool_purpose && (
            <p data-type="caption" className="text-on-surface-low">{a.tool_purpose}</p>
          )}
          <div className="flex items-center gap-s">
            <Button onClick={() => decide(a, 'approve')}
              loading={busy === `${a.id}:approve`} loadingLabel="Approving…"
              disabled={busy !== null} disabledReason={BUSY_REASON}
              ariaLabel={`Approve: ${a.tool}`}>
              <Check size={14} /> Approve
            </Button>
            {/* Ghost, not danger-styled, for the same reason `WorkflowAsk`'s Deny is quiet:
                refusing a call is a normal answer and red would imply the run broke. Both verbs
                are `Button` rather than the quieter sibling because both need `ariaLabel` — a run
                can hold two approvals, and "Approve"/"Reject" twice over is two pairs of controls
                with one name each. */}
            <Button variant="ghost" onClick={() => decide(a, 'reject')}
              loading={busy === `${a.id}:reject`} loadingLabel="Rejecting…"
              disabled={busy !== null} disabledReason={BUSY_REASON}
              ariaLabel={`Reject: ${a.tool}`}>
              <X size={13} /> Reject
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}
