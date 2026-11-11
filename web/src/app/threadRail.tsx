import { useCallback, useLayoutEffect } from 'react'
import { api, type ChatFolder, type ChatSessionSummary, type ChatTag } from '../lib/api'
import { readThreads, toThreads, type Thread, type ThreadSources } from '../lib/useThreads'
import { ThreadRail } from '../ui/ThreadRail'
import { applyView, readRailView, type ChatListView } from './chatListView'
import { useChatList } from './useChatList'

/** ── The sidebar's chat history, and which chat is open (#3814, #5877) ───────────────────────────
 *
 *  `useChatList` reads the chats and loops and lists them in the sidebar's view (its order and
 *  filters, `chatListView.ts`); this reads which row the address opens.
 *
 *  · **⌘1 to ⌘9 open the rows the rail shows**, top to bottom, after its view and its search
 *    (`threadNumberRoute`).
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
export function threadAt(route: string, sub: string, rows: readonly Thread[]): string {
  const path = [route, sub].filter(Boolean).join('/')
  const head = sub.split('/')[0] ?? ''
  if ((route === 'loops' || route === 'code') && head !== '') {
    const loop = rows.find((t) => t.kind !== 'chat' && t.id === head)
    if (loop) return loop.key
  }
  const exact = rows.find((t) => decoded(t.route) === path)
  if (exact) return exact.key
  const within = rows.find((t) => t.kind !== 'chat' && path.startsWith(`${decoded(t.route)}/`))
  return within ? within.key : ''
}

/** The rows ⌘1 to ⌘9 count while no rail shows them, as the expanded rail would with no search: the
 *  sidebar's view (`view`) over the rows, the archive's among them while its Status asks for it.
 *  Pure, over the lists the rail reads. */
export function numberedThreads(
  src: ThreadSources, archived: readonly ChatSessionSummary[] | undefined,
  folders: readonly ChatFolder[] | undefined, tags: readonly ChatTag[] | undefined, view: ChatListView,
): Thread[] {
  const active = toThreads(src).flatMap((g) => g.threads)
  const keys = new Set(active.map((t) => t.key))
  const old = archived
    ? toThreads({ sessions: [...archived], loops: [], items: [], approvals: [] }).flatMap((g) => g.threads)
      .filter((t) => !keys.has(t.key)).map((t) => ({ ...t, archived: true }))
    : []
  return applyView([...active, ...old], view, folders, tags)
}

/** The rows the mounted rail shows, top to bottom, or null while no rail shows any it has read. */
let shownByTheRail: readonly Thread[] | null = null

/** The route of row *n* (1 to 9) of the sidebar, or null when it shows fewer. The rows the rail
 *  shows when one is mounted (its view and its search applied), so ⌘n opens the nth row the owner
 *  sees; else, with the rail collapsed to icons or not up, the same rows read fresh, reading the
 *  folders or tags only when the view filters by them and the archive only when its Status asks. A
 *  failed read rejects. */
export async function threadNumberRoute(n: number): Promise<string | null> {
  let rows = shownByTheRail
  if (rows === null) {
    const view = readRailView()
    const [lists, archived, folders, tags] = await Promise.all([
      readThreads(),
      view.status === 'active' ? undefined : api.chatSessions(true),
      view.folders.length > 0 ? api.chatFolders() : undefined,
      view.tags.length > 0 ? api.chatTags() : undefined,
    ])
    rows = numberedThreads(lists, archived, folders, tags, view)
  }
  return rows[n - 1]?.route ?? null
}

/** The rail's chat history as the shell mounts it, in the `threads` slot of `NavRail`. A component
 *  of its own, so the lists are read only while a rail that shows them is mounted: not in the
 *  collapsed rail, an embedded page, the companion or setup. `onLit` tells the shell whether a row,
 *  or Open Chat history, is lit, so the destination its route belongs to is not lit beside it. */
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
  const list = useChatList('rail')
  const selectedKey = threadAt(route, sub, list.rows)
  const historyLit = route === 'chat' && sub === 'history'
  const lit = selectedKey !== '' || historyLit
  useLayoutEffect(() => { onLit(lit) }, [lit, onLit])
  useLayoutEffect(() => () => onLit(false), [onLit])
  const onShown = useCallback((rows: Thread[] | null) => { shownByTheRail = rows }, [])
  useLayoutEffect(() => () => { shownByTheRail = null }, [])
  return (
    <>
      {list.archive}
      <ThreadRail rows={list.rows} selectedKey={selectedKey} status={list.status}
        failure={list.failure} onOpen={onOpen} onRetry={list.retry}
        top={{ onNewChat: () => onOpen('chat/new'), onOpenHistory: () => onOpen('chat/history'), historyLit }}
        onShown={onShown} search={search} onSearch={onSearch}
        menu={list.sections} filtered={list.filtered} onClearFilters={list.clearFilters}
        markArchived={list.view.status === 'all'} />
    </>
  )
}
