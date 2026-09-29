import { useCallback, useEffect, useState } from 'react'
import { Check, X } from 'lucide-react'
import { Button } from '../ui/Button'
import { InlineLoadError } from '../ui/ListScaffold'
import { BUSY_REASON } from '../ui/unavailable'
import { api, type PendingApproval } from '../lib/api'
import { useChatSocket, type WsMessage } from '../lib/useChatSocket'
import { reportingWrite } from './reportingWrite'

/** Approve or deny ONE pending approval, on a surface that announced it.
 *
 *  An approval was announced in more places than it could be answered. Its Inbox row and that
 *  row's notification (the bell, `#/notifications`) said "Approval needed: … is waiting for your
 *  decision" and offered Mark handled, Dismiss, Mark read and Delete — none of which answers it —
 *  and named no place that would. A trigger's run asks with no chat behind it, so there was not
 *  even a chat to open: only Home › To triage carried the verbs, and the run sat blocked while its
 *  owner read about it twice. So every surface that announces an approval carries this, and it
 *  answers through the one decision path every surface outside a chat uses
 *  (`POST /api/approvals/{id}/{action}`).
 *
 *  The approval is read LIVE from the registry (`GET /api/approvals`), never inferred from the text
 *  that announced it: a row and its notification outlive the approval they name — answered
 *  elsewhere, out of time, or the work that asked has stopped — and offering Approve then would
 *  promise a decision nothing is waiting on. */
export function ApprovalDecision({ approvalId, onDecided }: {
  /** The registry id: an Inbox row's `refs.approval`, which its notification carries as `approval`. */
  approvalId: string
  /** After an answer the registry took. */
  onDecided?: () => void
}) {
  // undefined: not read yet · null: nothing is waiting under this id.
  const [pending, setPending] = useState<PendingApproval | null | undefined>(undefined)
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [answered, setAnswered] = useState<{ action: 'approve' | 'reject'; tool: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const all = await api.approvals()
      setPending(all.find((a) => a.id === approvalId) ?? null)
      setLoadErr(null)
    } catch (e) {
      // A failed read keeps what was last read: blanking a pending approval would hide the one
      // control this exists to show.
      setLoadErr(e)
    }
  }, [approvalId])

  useEffect(() => { void load() }, [load])
  // An approval can end anywhere — answered in a chat or on Home, run out of time, cancelled with
  // its work — and the registry says so on these two frames, or on none this page heard when it
  // ended while the socket was down.
  useChatSocket((m: WsMessage) => {
    if (m.type === 'approval' || m.type === 'approval_resolved') void load()
  }, () => { void load() })

  async function decide(p: PendingApproval, action: 'approve' | 'reject') {
    setBusy(action)
    const ok = await reportingWrite(`${action === 'approve' ? 'approve' : 'deny'} ${p.tool}`,
      () => api.resolveApproval(p.id, action))
    setBusy(null)
    if (ok) {
      setAnswered({ action, tool: p.tool })
      onDecided?.()
    }
    void load()
  }

  if (answered) {
    return (
      <p data-type="body-s" className="text-on-surface-var">
        {answered.action === 'approve'
          ? <>Approved. <span className="font-mono">{answered.tool}</span> runs now.</>
          : <>Denied. <span className="font-mono">{answered.tool}</span> does not run.</>}
      </p>
    )
  }
  if (pending === undefined) {
    return loadErr
      ? <InlineLoadError what="this approval" error={loadErr} onRetry={() => void load()} />
      : <p data-type="body-s" className="text-on-surface-low">Checking whether it is still waiting…</p>
  }
  if (pending === null) {
    return (
      <p data-type="body-s" className="text-on-surface-low">
        Nothing is waiting on this any more: it was answered, it ran out of time, or the work that
        asked for it stopped.
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-s">
      <p data-type="body-s" className="text-on-surface-var">
        Approve lets <span className="font-mono">{pending.tool}</span> run. Deny refuses it.
      </p>
      <div className="flex flex-wrap items-center gap-s">
        <Button size="sm" onClick={() => decide(pending, 'approve')}
          loading={busy === 'approve'} loadingLabel="Approving…"
          disabled={busy !== null} disabledReason={BUSY_REASON}
          ariaLabel={`Approve: ${pending.tool}`}>
          <Check size={14} /> Approve
        </Button>
        {/* Quiet, not danger-styled: refusing a call is an ordinary answer, and red would read as
            the work having broken — the reasoning the workflow run view's Deny gives. */}
        <Button size="sm" variant="ghost" onClick={() => decide(pending, 'reject')}
          loading={busy === 'reject'} loadingLabel="Denying…"
          disabled={busy !== null} disabledReason={BUSY_REASON}
          ariaLabel={`Deny: ${pending.tool}`}>
          <X size={14} /> Deny
        </Button>
      </div>
    </div>
  )
}
