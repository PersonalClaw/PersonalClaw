import { useCallback, useEffect, useRef } from 'react'
import { invalidateKeys } from '../../lib/data'
import { refreshKinds, useChatSocket, type WsMessage } from '../../lib/useChatSocket'

/** ── A companion lane stays current while the page is open ──────────────────────────────────────
 *
 *  Each lane of `#/companion` read its list once, when the page mounted, and only the approvals
 *  queue followed its frames. A phone left open on the page for days showed a loop that had long
 *  finished as running, with Pause and Stop, a completed task as open and an answered approval's
 *  Inbox row, until a reload. A lane now reads its list again on three things:
 *
 *    · a frame about its list, the same frames the desktop surfaces of that collection follow
 *      (`FOLLOWS`);
 *    · the socket coming back after a drop, because every frame sent while it was down is lost;
 *    · the page being shown again, because a phone suspends a hidden page, and what changed
 *      meanwhile came as frames nobody heard.
 *
 *  🪤 THE READ IS A NEW REQUEST, NEVER A JOIN. A query's `refresh()` joins a read already on the
 *  wire (`fetchKey` dedups by key), and a read that started before the change answers with the list
 *  as it was, with no frame left to correct it. A frame is the gateway saying the list changed,
 *  which is what an invalidation means, so the lane's key is invalidated and its reader asks again.
 *
 *  A burst of frames is ONE read, shortly after the first: "Dismiss all" moves every row and says
 *  so once per row, and a loop that ends sends its hint more than once. Home coalesces the same way.
 */

/** How long a lane waits after a frame for the rest of its burst. */
export const LANE_BURST_MS = 150

const isApprovalFrame = (m: WsMessage) => m.type === 'approval' || m.type === 'approval_resolved'

/** The frames that move each lane. */
export const FOLLOWS = {
  /** The approval registry's two frames: one raised, one answered or ended. */
  approvals: isApprovalFrame,
  /** The listing hint: a loop was created, moved status, finished a cycle or was deleted (`loops`),
   *  or the run behind a run-backed loop started, moved or ended (`workflow_runs`). */
  loops: (m: WsMessage) => refreshKinds(m).some((k) => k === 'loops' || k === 'workflow_runs'),
  /** The task store's listing hint: a task was created, edited, commented on or deleted. */
  tasks: (m: WsMessage) => refreshKinds(m).includes('tasks'),
  /** A row raised or moved, and the approval frames too: an approval's row is raised and closed
   *  with the approval itself, as the Inbox page and Home read them. */
  inbox: (m: WsMessage) => m.type.startsWith('inbox') || isApprovalFrame(m),
  /** Every frame about the notification log: a note, and those naming notes by `ts`. */
  notifications: (m: WsMessage) => m.type.startsWith('notification'),
} as const

/** Keep the lane read under *key* current: see the header. *follows* is its entry in `FOLLOWS`. */
export function useLiveLane(key: string, follows: (m: WsMessage) => boolean): void {
  const timer = useRef<number | undefined>(undefined)
  const reread = useCallback(() => {
    if (timer.current !== undefined) { window.clearTimeout(timer.current); timer.current = undefined }
    invalidateKeys(key)
  }, [key])
  const onFrame = useCallback((m: WsMessage) => {
    if (!follows(m) || timer.current !== undefined) return
    timer.current = window.setTimeout(reread, LANE_BURST_MS)
  }, [follows, reread])
  useChatSocket(onFrame, reread)
  useEffect(() => {
    const onShown = () => { if (!document.hidden) reread() }
    document.addEventListener('visibilitychange', onShown)
    return () => {
      document.removeEventListener('visibilitychange', onShown)
      if (timer.current !== undefined) { window.clearTimeout(timer.current); timer.current = undefined }
    }
  }, [reread])
}
