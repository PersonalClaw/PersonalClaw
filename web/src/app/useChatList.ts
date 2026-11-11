import { createElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type ChatFolder, type ChatSessionSummary, type ChatTag } from '../lib/api'
import { invalidateKeys, useQuery } from '../lib/data'
import { toThreads, useThreads, type Thread } from '../lib/useThreads'
import { refreshKinds, useChatSocket, type WsMessage } from '../lib/useChatSocket'
import type { FilterSectionDef } from '../ui/FilterMenu'
import type { ThreadRailStatus } from '../ui/ThreadRail'
import { useGatewayConnection } from './gatewayConnection'
import {
  DEFAULT_VIEW, applyView, clearedFilters, filtersSet, readRailView, viewSections, writeRailView,
  type ChatListView,
} from './chatListView'

/** ── The chat list's reads, status and view, for the sidebar and the Chat history panel ──────────
 *
 *  Both hosts call `useChatList`, so the two lists on one page read the same rows and never say
 *  different things about them.
 *
 *  · **Rows:** `dashboard:threads` (`useThreads`), kept live by the socket's frames.
 *  · **The archive:** `chat:sessions:archived`, the key Chat history's archive reads, read only while
 *    the view's Status asks for it (`archive`, an element the host mounts), and read again on the
 *    frames Chat history relists on. Under All the two are joined by key, the active row winning.
 *  · **Names:** the folders and tags, read under the keys Chat history reads them by. A failed names
 *    read never fails the list: its section shows ids and a Retry row.
 *  · **Status:** offline first, then failed, loading, ready.
 *  · **The view:** the sidebar's is kept on this device (`chat-list-view`); the panel's is its own,
 *    opening at the defaults.
 */

export type ChatListHost = 'rail' | 'panel'

/** The key the archive is read under, shared with Chat history's archive. */
export const ARCHIVE_KEY = 'chat:sessions:archived'

/** What the archive's reader last said. */
interface ArchiveRead {
  sessions: ChatSessionSummary[] | undefined
  error: unknown
  loading: boolean
  refresh: () => void
}

/** The frames Chat history relists on: a `sessions` frame (the gateway lists a chat it changed) and
 *  its `history` hint (a turn ended, chats deleted or archived). */
function relists(m: WsMessage): boolean {
  return m.type === 'sessions' || refreshKinds(m).includes('history')
}

/** The archive's read, mounted only while a view asks for it: a mount always fetches. */
function ArchivedChatsRead({ onRead }: { onRead: (read: ArchiveRead | null) => void }) {
  // The key written out, as every reader of the chat list writes it (`sessionListStaleness.test.ts`).
  const { data, error, loading, refresh } = useQuery<ChatSessionSummary[]>('chat:sessions:archived', () => api.chatSessions(true))
  const debounce = useRef<number | undefined>(undefined)
  const onMessage = useCallback((m: WsMessage) => {
    if (!relists(m)) return
    if (debounce.current) clearTimeout(debounce.current)
    debounce.current = window.setTimeout(() => { debounce.current = undefined; invalidateKeys(ARCHIVE_KEY) }, 300)
  }, [])
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current) }, [])
  useChatSocket(onMessage, refresh)
  useLayoutEffect(() => { onRead({ sessions: data, error, loading, refresh }) }, [data, error, loading, refresh, onRead])
  useLayoutEffect(() => () => onRead(null), [onRead])
  return null
}

/** The archive's rows as threads, each marked archived. */
function archivedThreads(sessions: readonly ChatSessionSummary[] | undefined): Thread[] {
  if (!sessions) return []
  return toThreads({ sessions: [...sessions], loops: [], items: [], approvals: [] })
    .flatMap((g) => g.threads)
    .map((t) => ({ ...t, archived: true }))
}

export interface ChatListRead {
  /** The rows the view lists, in its order, before the search. */
  rows: Thread[]
  /** Every row the reads hold, the archive's included while it is read. */
  all: Thread[]
  view: ChatListView
  setView: (view: ChatListView) => void
  /** The Sort and filter menu's sections. */
  sections: FilterSectionDef[]
  /** Whether a filter is off its default (the sort aside). */
  filtered: boolean
  clearFilters: () => void
  status: ThreadRailStatus
  /** What failed, as the list says it, when `status` is 'failed'. */
  failure: string
  /** The failed read's error, or null. */
  error: unknown
  /** The chats read's own error, or null: a failed archive read is not one. The panel's LoadError
   *  stands only for this, so the archive's reader stays mounted and says its own failure. */
  chatsError: unknown
  /** The folders read's error, or null. The Folder section says it, with Retry. */
  foldersError: unknown
  /** The tags read's error, or null. The Tag section says it, with Retry. */
  tagsError: unknown
  retry: () => void
  /** The archive's reader, for the host to mount; null while the view needs no archive. */
  archive: ReactNode
}

/** The chat list's rows, view and status for *host*. */
export function useChatList(host: ChatListHost): ChatListRead {
  const threads = useThreads()
  const [view, setViewState] = useState<ChatListView>(() => (host === 'rail' ? readRailView() : { ...DEFAULT_VIEW }))
  const setView = useCallback((next: ChatListView) => {
    setViewState(host === 'rail' ? writeRailView(next) : next)
  }, [host])
  const { data: folders, error: foldersError, refresh: refreshFolders } = useQuery<ChatFolder[]>(
    'chat:folders', () => api.chatFolders(), { persist: true })
  const { data: tags, error: tagsError, refresh: refreshTags } = useQuery<ChatTag[]>(
    'chat:tags', () => api.chatTags(), { persist: true })

  const wantsArchive = view.status !== 'active'
  const [archiveRead, setArchiveRead] = useState<ArchiveRead | null>(null)
  const onArchiveRead = useCallback((read: ArchiveRead | null) => { setArchiveRead(read) }, [])
  const archive = wantsArchive ? createElement(ArchivedChatsRead, { onRead: onArchiveRead }) : null
  const archived = wantsArchive ? archiveRead : null

  const all = useMemo(() => {
    const active = threads.groups.flatMap((g) => g.threads)
    if (!archived?.sessions) return active
    const keys = new Set(active.map((t) => t.key))
    return [...active, ...archivedThreads(archived.sessions).filter((t) => !keys.has(t.key))]
  }, [threads.groups, archived?.sessions])

  const rows = useMemo(() => applyView(all, view, folders, tags), [all, view, folders, tags])
  const retryNames = useCallback(() => { refreshFolders(); refreshTags() }, [refreshFolders, refreshTags])
  const sections = useMemo(() => viewSections(all, view, {
    folders, tags, foldersFailed: !!foldersError && !folders, tagsFailed: !!tagsError && !tags, onRetryNames: retryNames,
  }, setView), [all, view, folders, tags, foldersError, tagsError, retryNames, setView])

  // The gateway not answering at all is said as itself: a read that fails because the gateway is
  // away is not a fault of that read, and the chats it last listed stay on screen.
  const { state: connection, check: checkConnection } = useGatewayConnection()
  const offline = connection === 'disconnected'
  const archiveFailed = !!archived?.error
  const failure = threads.error ? "Couldn't load your chats." : archiveFailed ? "Couldn't load your archived chats." : ''
  // Under All the active rows stay on screen while the archive is read.
  const archiveLoading = wantsArchive && (!archived || (archived.loading && !archived.sessions))
    && (view.status === 'archived' || all.length === 0)
  const status: ThreadRailStatus = offline ? 'offline' : failure ? 'failed'
    : threads.loading || archiveLoading ? 'loading' : 'ready'
  const { refresh: refreshThreads } = threads
  const refreshArchive = archived?.refresh
  const retry = useCallback(() => {
    checkConnection(); refreshThreads(); refreshArchive?.()
  }, [checkConnection, refreshThreads, refreshArchive])
  const clearFilters = useCallback(() => { setView(clearedFilters(view)) }, [setView, view])
  return {
    rows, all, view, setView, sections, filtered: filtersSet(view), clearFilters,
    status, failure, error: threads.error || archived?.error || null, chatsError: threads.error || null,
    foldersError: foldersError || null, tagsError: tagsError || null,
    retry, archive,
  }
}
