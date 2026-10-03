import { useCallback, useEffect, useState } from 'react'
import { api, type PendingApproval } from '../../lib/api'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { reportingWrite } from '../../app/reportingWrite'
import { roomApprovalOf } from '../../app/approvalDestination'
import { PendingApprovalCard } from '../../app/PendingApprovalCard'

/** The calls this room's members are waiting on you for, answerable in the room itself.
 *
 *  A member's call that asks is listed where every approval is (Home, the Inbox, the phone, the
 *  channel approvals go to), but its session is no chat anyone opened, so without this the one place
 *  you are watching the member from could not answer it, and the room read "Answering: <member>"
 *  with nothing to answer. Every member's asks land here, claimed out of `/api/approvals` by the same
 *  parse the nudge links with (`roomApprovalOf`), each as the one approval card the queue surfaces
 *  render (`PendingApprovalCard`): the tool and its risk, what it can touch, its whole input, and the
 *  member and room that asked. Answered through the queue (`POST /api/approvals/{id}/{action}`), for
 *  this call alone: no standing answer covers a room member's call.
 *
 *  *onCount* hears how many are waiting, so the room can keep the newest of them in view. */
export function RoomApprovals({ roomId, onCount }: { roomId: string; onCount?: (n: number) => void }) {
  const [pending, setPending] = useState<PendingApproval[]>([])
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const all = await api.approvals()
      setPending(all.filter((a) => roomApprovalOf(a.session, roomId)))
    } catch {
      /* A failed read keeps the last-good cards: blanking one would leave a member waiting on you
         with nothing in the room to answer it. */
    }
  }, [roomId])

  useEffect(() => { load() }, [load])
  useEffect(() => { onCount?.(pending.length) }, [pending.length, onCount])
  // Raised and resolved over the one multiplexed socket: an ask answered from the bell, the Inbox or
  // a phone leaves the room as soon as it is answered. Read again when the socket comes back: an
  // ask raised while it was down reached nobody here.
  useChatSocket((m: WsMessage) => {
    if (m.type === 'approval' || m.type === 'approval_resolved') load()
  }, load)

  const decide = useCallback(async (a: PendingApproval, action: 'approve' | 'reject') => {
    setBusy(a.id)
    // `reportingWrite` so a refused answer (the room was archived meanwhile) says the server's own
    // sentence instead of leaving the card in place with nothing happening.
    await reportingWrite(`${action} this approval`, () => api.resolveApproval(a.id, action))
    setBusy(null)
    load()
  }, [load])

  if (pending.length === 0) return null

  return (
    <div role="group" aria-label="Waiting on your approval" className="flex flex-col gap-m">
      {pending.map((a) => (
        <PendingApprovalCard key={a.id} approval={a} busy={busy === a.id}
          onDecide={(action) => decide(a, action)} />
      ))}
    </div>
  )
}
