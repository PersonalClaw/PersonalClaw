import { useCallback, useEffect, useMemo, useRef } from 'react'
import { api, type InboxItem, type SkillProposal, type WaitingList } from './api'
import { useQuery } from './data'
import { needsYou, type NeedsYouRow } from './needsYou'
import { refreshKinds, useChatSocket, type WsMessage } from './useChatSocket'

/** The one read of what waits on you, shared by Home's Needs You and the toolbar's badge, so the
 *  two count the same rows from the same instant. */
export const NEEDS_YOU_KEY = 'dashboard:needs-you'

export interface NeedsYouRead {
  /** `GET /api/waiting`: every source's asks, in the one order. */
  waiting: WaitingList
  /** The open Inbox, for your notes and the proposals' own rows. */
  items: InboxItem[]
  /** The skill proposals waiting on a yes or no. */
  proposals: SkillProposal[]
}

/** Deliberately not `.catch(() => [])` on any leg: a swallowed failure reads as nothing waiting,
 *  which is the one thing this read must never say when it does not know. The error reaches
 *  `useQuery`, and the reader says it could not read. */
export async function readNeedsYou(): Promise<NeedsYouRead> {
  const [waiting, items, proposals] = await Promise.all([api.waiting(), api.inboxOpen(), api.skillProposals()])
  return { waiting, items, proposals: proposals.proposals }
}

/** The frames that can change what waits on you. Each is a signal: the reader reads the list again
 *  and never a field of the frame. The list's own `waiting_changed`, rung at every source's write;
 *  and, for a gateway from before it, an approval and its answer, the Inbox (an agent's question, a
 *  run's gate and a trigger's park each raise a row there), the running list moving (a loop that
 *  starts or stops asking), a browser grant, and the listings a loop's or a run's change refreshes. */
export function refreshesNeedsYou(m: WsMessage): boolean {
  const type = m.type
  if (type === 'waiting_changed') return true
  if (type === 'approval' || type === 'approval_resolved' || type.startsWith('inbox')) return true
  if (type === 'running_changed' || type === 'browse_grant') return true
  return refreshKinds(m).some((k) => k === 'loops' || k === 'workflow_runs' || k === 'crons')
}

/** What waits on you: the rows of Needs You, the sources the gateway could not read, and the read's
 *  own state. Re-read on the frames above, a burst of them read once, and after a reconnect, whose
 *  dropped frames are lost. */
export function useNeedsYou() {
  const { data, error, loading, refresh } = useQuery<NeedsYouRead>(NEEDS_YOU_KEY, readNeedsYou)
  const debounce = useRef<number | undefined>(undefined)
  const onMessage = useCallback((m: WsMessage) => {
    if (!refreshesNeedsYou(m)) return
    if (debounce.current) clearTimeout(debounce.current)
    debounce.current = window.setTimeout(refresh, 300)
  }, [refresh])
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current) }, [])
  useChatSocket(onMessage, refresh)
  const rows: NeedsYouRow[] = useMemo(
    () => (data ? needsYou(data.waiting, data.items, data.proposals) : []),
    [data],
  )
  const unreadable = useMemo(() => {
    // Read as unknown: a malformed answer names its sources however it likes.
    const raw: unknown = data?.waiting?.unreadable
    return Array.isArray(raw) ? raw.filter((k): k is string => typeof k === 'string') : []
  }, [data])
  // `error` stays in the shape: a reader that cannot tell a failed read from an empty one says
  // nothing waits when it does not know.
  return { data, error, loading, refresh, rows, unreadable }
}
