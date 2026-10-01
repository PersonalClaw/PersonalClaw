import { useCallback, useEffect, useState } from 'react'
import { api, type PendingApproval } from '../../lib/api'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { reportActionFailure } from '../../app/reportingWrite'
import { loopApprovalOf } from '../../app/approvalDestination'
import { ApprovalCard } from '../chat/ApprovalCard'
import { approvalSegmentOf } from '../chat/approvalSegment'

type Action = 'approved' | 'rejected' | 'trust' | 'trust_agent'

/** The tool calls this loop's workers are waiting on you for, answerable on the loop's own page.
 *
 *  An Attended loop's worker asks the way a chat does (the bell, the Inbox row, a phone push, the
 *  channel approvals go to), but its session is no chat anyone opened, so without this the one
 *  place you watch the loop from could not answer it. Every worker's asks land here, the stage
 *  worker's and each per-task worker's, claimed out of `/api/approvals` by the same parse the
 *  nudge links with (`loopApprovalOf`), and each is the chat's own card (`ApprovalCard`) with the
 *  loop's words for its scopes. Answered through the worker session's approve route, the one the
 *  chat card posts to, so the answer is the same answer from wherever it is given. */
export function LoopApprovals({ loopId, className = '' }: { loopId: string; className?: string }) {
  const [pending, setPending] = useState<PendingApproval[]>([])

  const load = useCallback(async () => {
    try {
      const all = await api.approvals()
      setPending(all.filter((a) => loopApprovalOf(a.session, loopId)))
    } catch {
      /* A failed read keeps the last-good cards: blanking one would leave a waiting worker with
         nothing on the page that watches it. */
    }
  }, [loopId])

  useEffect(() => { load() }, [load])
  // Raised and resolved over the one multiplexed socket: an ask answered from the bell, the
  // Inbox or a phone leaves this page as soon as it is answered. And read again when the socket
  // comes back: a worker's ask raised while it was down reached nobody here.
  useChatSocket((m: WsMessage) => {
    if (m.type === 'approval' || m.type === 'approval_resolved') load()
  }, load)

  const decide = useCallback((a: PendingApproval, action: Action) => {
    api.approve(a.session, action, a.request_id)
      .catch(reportActionFailure('record your decision'))
      .finally(() => { load() })
  }, [load])

  if (pending.length === 0) return null

  return (
    <div role="group" aria-label="Waiting on your approval" className={`flex flex-col gap-s ${className}`}>
      {pending.map((a) => (
        <ApprovalCard key={a.id} seg={approvalSegmentOf(a)} scopeWords="loop"
          onAct={(_id, action) => decide(a, action)} />
      ))}
    </div>
  )
}
