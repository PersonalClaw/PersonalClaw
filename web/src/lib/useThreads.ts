import { useCallback, useEffect, useMemo, useRef } from 'react'
import { api, type ChatSessionSummary, type InboxItem, type Loop, type PendingApproval } from './api'
import { useQuery } from './data'
import { loopApprovalSession } from '../app/approvalDestination'
import { loopTitle, toLanes, type ActivityInput, type ApprovalInput, type AttentionInput, type Lane, type LaneCard, type LoopInput } from './attentionLanes'
import { sessionRecencyMs } from './epoch'
import { loopRoute } from './loopKind'
import { sessionTitle } from './sessionTitle'
import { refreshKinds, useChatSocket, type WsMessage } from './useChatSocket'

/** ── The threads the sidebar lists, under their projects (#3814) ──────────────────────────────
 *
 *  Every chat, goal loop and code session is a thread, and every thread sits under the project its
 *  row names (`project_id` on the chat list and on the loop list). The join is a pure function
 *  (`toThreads`) over those two lists and the two that say what waits on the owner, so it is tested
 *  against fixtures without a gateway; `useThreads` reads the four together and keeps them current.
 *
 *  · **A loop's workers are the loop.** Its stage worker, each task worker and its planner are chat
 *    sessions the list attributes to it (`origin: 'loop'`, its id as `source_id`), and one of them
 *    may be the loop's `session_key`. None is a thread of its own, whether or not its loop is in
 *    this snapshot: they would show one loop several times, titled by internal keys. This is the
 *    fold Home's lanes make (`toLanes`).
 *  · **A chat with no project goes under "No project"** (`NO_PROJECT`), the word the composer's
 *    project picker uses. It is not filed under a built-in project: that would read as though the
 *    chat were fed that project's context, which it is not.
 *  · **The pip is Home's lane, never a second derivation.** Waiting on you is a card in Needs
 *    approval or Your turn; running is a card in Working. `toLanes` decides both, over the same
 *    snapshot, and each card is traced back to the thread whose work it is (`threadOfCard`). Failed
 *    is the one pip no lane holds: a loop whose status is `failed`, shown only while nothing of it
 *    waits or runs. A chat's failed and unread pips need the gateway's seen record, and come with
 *    it: without it a chat would read failed until its next turn.
 *  · **Order is not decided here.** Groups come in the order their first thread does, and threads
 *    in their list's order, chats before loops. Each thread carries what the sidebar's named order
 *    reads (`pinned`, `activeAt`), and the sidebar sorts by it (`byRailOrder`, `app/threadRail.tsx`).
 */

/** The group a thread with no project goes under, shown as "No project". */
export const NO_PROJECT = ''

/** What a thread is: a chat, a goal loop (any loop kind but code) or a code session. Its kind
 *  changes the layout it opens in, never the project it sits under. */
export type ThreadKind = 'chat' | 'loop' | 'code'

/** The one static marker a row can show. `null` when nothing waits, runs or failed. */
export type ThreadPip = 'waiting' | 'running' | 'failed'

export interface Thread {
  /** Unique across kinds: `chat:<key>` or `loop:<id>`. */
  key: string
  kind: ThreadKind
  /** The chat's key or the loop's id. */
  id: string
  title: string
  /** The project its row names, or `NO_PROJECT`. */
  projectId: string
  /** The route that opens it, without the leading `#/`. */
  route: string
  pip: ThreadPip | null
  /** The owner pinned it. Only a chat can be pinned; a loop never is. */
  pinned: boolean
  /** When it last did something, in epoch ms, 0 when nothing says. A chat's is its list row's
   *  activity (`sessionRecencyMs`). A loop's is its newest cycle's, which its workers' latest activity
   *  is (each cycle is a worker's turn; the loop list carries no cycle time), else when it
   *  completed, started or was created: the newest of these. Its status never changes it. */
  activeAt: number
}

export interface ProjectThreads {
  projectId: string
  threads: Thread[]
}

/** The chat list's fields the join reads. */
export type ThreadChat = ActivityInput & Pick<ChatSessionSummary, 'project_id' | 'pinned'>
/** The loop list's fields the join reads. */
export type ThreadLoop = LoopInput & Pick<Loop, 'project_id'> & Partial<Pick<Loop, 'created_at' | 'completed_at'>>

export interface ThreadSources {
  sessions: ThreadChat[]
  loops: ThreadLoop[]
  /** The open Inbox rows (`GET /api/inbox/open`): an agent's question, a loop's ask. */
  items: AttentionInput[]
  /** The pending approvals (`GET /api/approvals`). */
  approvals: ApprovalInput[]
}

/** Which pip each of Home's lanes means. Idle is nothing a row marks. */
const PIP_OF_LANE: Record<Lane, ThreadPip | null> = {
  'needs-approval': 'waiting',
  'your-turn': 'waiting',
  working: 'running',
  idle: null,
}

/** Precedence when one thread's work sits in more than one place: waiting outranks running, which
 *  outranks failed. */
const RANK: Record<ThreadPip, number> = { waiting: 3, running: 2, failed: 1 }

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

/** An epoch-seconds field of a loop row as ms, 0 when it holds no number. */
function ms(secs: unknown): number {
  return typeof secs === 'number' && Number.isFinite(secs) ? secs * 1000 : 0
}

function rows<T>(list: T[] | unknown): T[] {
  return (Array.isArray(list) ? list : []).filter((r): r is T => r !== null && typeof r === 'object')
}

/** Every thread in *src*, grouped by project. Defended like `toLanes`: a non-array list or a
 *  malformed row is skipped, never thrown on, since this runs on a render path. */
export function toThreads(src: ThreadSources): ProjectThreads[] {
  const sessions = rows<ThreadChat>(src.sessions)
  const loops = rows<ThreadLoop>(src.loops)

  const loopThreads: Thread[] = []
  const loopBySession = new Map<string, string>()
  for (const l of loops) {
    const id = str(l.id)
    if (id === '') continue
    loopThreads.push({
      key: `loop:${id}`,
      kind: l.kind === 'code' ? 'code' : 'loop',
      id,
      title: loopTitle(l),
      projectId: str(l.project_id),
      route: loopRoute(l),
      pip: l.status === 'failed' ? 'failed' : null,
      pinned: false,
      activeAt: Math.max(ms(l.completed_at), ms(l.started_at), ms(l.created_at)),
    })
    if (str(l.session_key) !== '') loopBySession.set(str(l.session_key), `loop:${id}`)
  }

  /** The loop thread a listed session is a worker of, as the list says it (`origin`, `source_id`)
   *  or as a loop names its own session, else ''. A worker of a loop this snapshot does not hold
   *  answers a key no thread has. */
  const loopOfSession = (key: string, origin?: string, sourceId?: unknown): string => {
    const named = loopBySession.get(key)
    if (named) return named
    return origin === 'loop' ? `loop:${str(sourceId)}` : ''
  }

  const chatThreads: Thread[] = []
  const sessionThread = new Map<string, string>()
  /** The latest activity of each loop's listed workers, by the loop's thread key. */
  const workerActivity = new Map<string, number>()
  for (const s of sessions) {
    const key = str(s.key)
    if (key === '') continue
    const owner = loopOfSession(key, s.origin, s.source_id)
    if (owner !== '') {
      sessionThread.set(key, owner)
      workerActivity.set(owner, Math.max(workerActivity.get(owner) ?? 0, sessionRecencyMs(s)))
      continue
    }
    sessionThread.set(key, `chat:${key}`)
    chatThreads.push({
      key: `chat:${key}`,
      kind: 'chat',
      id: key,
      title: sessionTitle(s),
      projectId: str(s.project_id),
      route: `chat/${encodeURIComponent(key)}`,
      pip: null,
      pinned: s.pinned === true,
      activeAt: sessionRecencyMs(s),
    })
  }
  for (const t of loopThreads) t.activeAt = Math.max(t.activeAt, workerActivity.get(t.key) ?? 0)

  /** The thread whose work a session key is: a listed chat or loop worker, else a loop worker's
   *  key shape (`loopApprovalSession`), since an approval can name a worker the list has not
   *  caught up with. */
  const threadOfSession = (session: string): string => {
    if (session === '') return ''
    const listed = sessionThread.get(session) ?? loopBySession.get(session)
    if (listed) return listed
    const parsed = loopApprovalSession(session)
    return parsed ? `loop:${parsed.loopId}` : ''
  }

  const threadOfCard = (card: LaneCard): string => {
    switch (card.origin) {
      case 'approval': return threadOfSession(str(card.approval.session))
      case 'loop': return `loop:${card.id}`
      case 'session': return threadOfSession(card.id)
      case 'inbox': {
        const loop = str(card.item.refs?.loop)
        return loop !== '' ? `loop:${loop}` : threadOfSession(str(card.item.refs?.session))
      }
      default: return ''
    }
  }

  const all = [...chatThreads, ...loopThreads]
  const byKey = new Map(all.map((t) => [t.key, t]))
  const lanes = toLanes(src.items, src.approvals, sessions, loops)
  for (const card of Object.values(lanes).flat()) {
    const pip = PIP_OF_LANE[card.lane]
    const thread = pip ? byKey.get(threadOfCard(card)) : undefined
    if (thread && pip && (thread.pip === null || RANK[pip] > RANK[thread.pip])) thread.pip = pip
  }

  const groups = new Map<string, Thread[]>()
  for (const t of all) {
    const group = groups.get(t.projectId)
    if (group) group.push(t)
    else groups.set(t.projectId, [t])
  }
  return [...groups].map(([projectId, threads]) => ({ projectId, threads }))
}

// One `dashboard:`-namespaced key and one fetch for all four lists: `toThreads` reads them
// together, so reading them under separate keys could pair one instant's chats with another's
// approvals.
const THREADS_KEY = 'dashboard:threads'

/** What `readThreads` reads: the four lists `toThreads` joins. */
export interface ThreadLists {
  sessions: ChatSessionSummary[]
  loops: Loop[]
  items: InboxItem[]
  approvals: PendingApproval[]
}

/** The four lists, read together. Deliberately no `.catch(() => [])` on any leg: a swallowed failure
 *  would list no threads, which reads as "you have none". The error reaches the caller instead. */
export async function readThreads(): Promise<ThreadLists> {
  const [sessions, loops, items, approvals] = await Promise.all([
    api.chatSessions(), api.uLoops(), api.inboxOpen(), api.approvals(),
  ])
  return { sessions, loops, items, approvals }
}

/** The frames that change a thread or its pip: a chat's run state, an approval, an Inbox row, and
 *  the gateway's listing hint naming the loops. */
function changesThreads(m: WsMessage): boolean {
  const type = m.type
  if (type === 'refresh') return refreshKinds(m).includes('loops')
  return type === 'sessions' || type === 'work_phase' || type === 'approval' || type === 'approval_resolved'
    || type.startsWith('inbox')
}

export interface ThreadsRead {
  groups: ProjectThreads[]
  loading: boolean
  /** Set when the last read failed. `groups` are then what the last good read said, and empty
   *  when none has succeeded, so the caller must say the read failed rather than show them as all. */
  error: unknown
  refresh: () => void
}

/** Every thread under its project, kept current by the socket's frames (debounced like Home's,
 *  since a running turn sends one every few seconds). */
export function useThreads(): ThreadsRead {
  const { data, error, loading, refresh } = useQuery<ThreadLists>(THREADS_KEY, readThreads)

  const debounce = useRef<number | undefined>(undefined)
  const onMessage = useCallback((m: WsMessage) => {
    if (!changesThreads(m)) return
    if (debounce.current) clearTimeout(debounce.current)
    debounce.current = window.setTimeout(refresh, 300)
  }, [refresh])
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current) }, [])
  useChatSocket(onMessage, refresh)

  const groups = useMemo(() => (data ? toThreads(data) : []), [data])
  return { groups, loading, error, refresh }
}
