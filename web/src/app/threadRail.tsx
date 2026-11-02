import { useCallback, useLayoutEffect, useMemo, useState } from 'react'
import { api, type ChatFolder, type ChatTag } from '../lib/api'
import { useQuery } from '../lib/data'
import { useGatewayConnection } from './gatewayConnection'
import { readThreads, toThreads, useThreads, type Thread, type ThreadSources } from '../lib/useThreads'
import { ThreadRail, railSections, readGroupsOpen, shownThreads, type ThreadRailGroup, type ThreadRailStatus } from '../ui/ThreadRail'
import { historyGroups, numberedRows, readGroupBy, writeGroupBy, type GroupBy } from './chatHistoryGroups'

/** ── The sidebar's chat history, and which chat is open (#3814, #5877) ───────────────────────────
 *
 *  `useThreads` joins the chats and loops; this groups them as the owner chose, by the folders Chat
 *  history keeps or by its tags (`chatHistoryGroups.ts`), and reads which row the address opens.
 *
 *  · **⌘1 to ⌘9 open the rows the rail shows**, top to bottom, a chat listed under two tags counted
 *    once (`threadNumberRoute`).
 *  · **The open chat is the address.** A row keeps the address of its kind (`#/chat/<key>`,
 *    `#/loops/<id>`, `#/code/<id>`, a run-backed loop's `#/workflows/runs/<run>`), so every address
 *    that opened one before still opens it and the rail keeps no selection of its own: reload,
 *    Back, Forward and a copied address light the row the page shows.
 */

function decoded(route: string): string {
  return route.split('/').map((s) => { try { return decodeURIComponent(s) } catch { return s } }).join('/')
}

/** The key of the listed row the address `#/<route>/<sub>` opens, else ''. `sub` arrives decoded,
 *  as `useHashRoute` gives it. A loop or code session is also open on its own sub-pages, and a code
 *  session at the loop address it had before; a chat only at its own address, so a chat whose key
 *  extends another's never lights the other. */
export function threadAt(route: string, sub: string, groups: readonly { threads: readonly Thread[] }[]): string {
  const all = groups.flatMap((g) => g.threads)
  const path = [route, sub].filter(Boolean).join('/')
  const head = sub.split('/')[0] ?? ''
  if ((route === 'loops' || route === 'code') && head !== '') {
    const loop = all.find((t) => t.kind !== 'chat' && t.id === head)
    if (loop) return loop.key
  }
  const exact = all.find((t) => decoded(t.route) === path)
  if (exact) return exact.key
  const within = all.find((t) => t.kind !== 'chat' && path.startsWith(`${decoded(t.route)}/`))
  return within ? within.key : ''
}

/** The rows ⌘1 to ⌘9 count while no rail shows them, as the expanded rail would with no search: the
 *  groups as *by* makes them, the rows of a group the owner closed (`open`, as stored) left out, a
 *  chat under two tags counted once. Pure, over the lists the rail reads. */
export function numberedThreads(
  src: ThreadSources, folders: readonly ChatFolder[] | undefined, tags: readonly ChatTag[] | undefined,
  by: GroupBy, open: Record<string, boolean>,
): Thread[] {
  return numberedRows(shownThreads(railSections(historyGroups(toThreads(src), by, folders, tags), open, '')))
}

/** The rows the mounted rail shows, top to bottom, or null while no rail shows any it has read. */
let shownByTheRail: readonly Thread[] | null = null

/** The route of row *n* (1 to 9) of the sidebar, or null when it shows fewer. The rows the rail
 *  shows when one is mounted (its closed groups and its search applied), so ⌘n opens the nth row the
 *  owner sees; else, with the rail collapsed to icons or not up, the same rows read fresh. A failed
 *  read rejects. */
export async function threadNumberRoute(n: number): Promise<string | null> {
  let rows = shownByTheRail
  if (rows === null) {
    const [lists, folders, tags] = await Promise.all([readThreads(), api.chatFolders(), api.chatTags()])
    rows = numberedThreads(lists, folders, tags, readGroupBy(), readGroupsOpen())
  }
  return rows[n - 1]?.route ?? null
}

export interface ThreadRailRead {
  groups: ThreadRailGroup[]
  /** The listed row the address opens, else ''. */
  selectedKey: string
  status: ThreadRailStatus
  /** What failed, as the rail says it, when `status` is 'failed'. */
  failure: string
  /** The failed read's error (the chats', else the folders' or tags'), or null. */
  error: unknown
  retry: () => void
  groupBy: GroupBy
  setGroupBy: (by: GroupBy) => void
}

/** The rail's groups and its open row, for the shell at `#/<route>/<sub>`. The folders and tags are
 *  read under the keys Chat history reads them by, so the two share one read. */
export function useThreadRail(route: string, sub: string): ThreadRailRead {
  const threads = useThreads()
  const [groupBy, setGroupByState] = useState<GroupBy>(readGroupBy)
  const setGroupBy = useCallback((by: GroupBy) => { setGroupByState(writeGroupBy(by)) }, [])
  const { data: folders, loading: foldersLoading, error: foldersError, refresh: refreshFolders } = useQuery<ChatFolder[]>(
    'chat:folders', () => api.chatFolders(), { persist: true })
  const { data: tags, loading: tagsLoading, error: tagsError, refresh: refreshTags } = useQuery<ChatTag[]>(
    'chat:tags', () => api.chatTags(), { persist: true })
  // Only the list the groups are made by can fail them, or hold them loading.
  const listError = groupBy === 'folder' ? foldersError : tagsError
  const listLoading = groupBy === 'folder' ? foldersLoading : tagsLoading
  const groups = useMemo(
    () => historyGroups(threads.groups, groupBy, folders, tags),
    [threads.groups, groupBy, folders, tags],
  )
  const selectedKey = threadAt(route, sub, groups)
  // The gateway not answering at all is said as itself: a read that fails because the gateway is
  // away is not a fault of that read, and the chats it last listed stay on screen.
  const { state: connection, check: checkConnection } = useGatewayConnection()
  const offline = connection === 'disconnected'
  const failure = threads.error ? "Couldn't load your chats."
    : listError ? (groupBy === 'folder' ? "Couldn't load your folders." : "Couldn't load your tags.") : ''
  const status: ThreadRailStatus = offline ? 'offline' : failure ? 'failed' : threads.loading || listLoading ? 'loading' : 'ready'
  const { refresh: refreshThreads } = threads
  const retry = useCallback(() => { checkConnection(); refreshThreads(); refreshFolders(); refreshTags() }, [checkConnection, refreshThreads, refreshFolders, refreshTags])
  return {
    groups, selectedKey, status, failure, retry, groupBy, setGroupBy,
    error: threads.error || (groupBy === 'folder' ? foldersError : tagsError) || null,
  }
}

/** The rail's chat history as the shell mounts it, in the `threads` slot of `NavRail`. A component
 *  of its own, so the lists are read only while a rail that shows them is mounted: not in the
 *  collapsed rail, an embedded page, the companion or setup. `onLit` tells the shell whether a row is
 *  lit, so the destination its route belongs to is not lit beside it. */
export function ShellThreadRail({ route, sub, onOpen, onLit, search, onSearch }: {
  route: string
  sub: string
  onOpen: (route: string) => void
  /** The rail's search, kept by the shell so a collapse gives it back (`ThreadRail`'s `search`). */
  search: string
  onSearch: (query: string) => void
  /** Called with whether a row is lit; with false when the rail unmounts. Keep it stable. */
  onLit: (lit: boolean) => void
}) {
  const rail = useThreadRail(route, sub)
  const lit = rail.selectedKey !== ''
  useLayoutEffect(() => { onLit(lit) }, [lit, onLit])
  useLayoutEffect(() => () => onLit(false), [onLit])
  const onShown = useCallback((rows: Thread[] | null) => { shownByTheRail = rows && numberedRows(rows) }, [])
  useLayoutEffect(() => () => { shownByTheRail = null }, [])
  return (
    <ThreadRail groups={rail.groups} selectedKey={rail.selectedKey} status={rail.status}
      failure={rail.failure} onOpen={onOpen} onRetry={rail.retry}
      onNewThread={() => onOpen('chat/new')} onShown={onShown} search={search} onSearch={onSearch}
      groupBy={rail.groupBy} onGroupBy={rail.setGroupBy} />
  )
}
