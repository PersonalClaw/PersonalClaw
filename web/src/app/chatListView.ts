import type { ChatFolder, ChatTag } from '../lib/api'
import type { Thread, ThreadKind } from '../lib/useThreads'
import type { FilterSectionDef } from '../ui/FilterMenu'
import { NO_FOLDER_LABEL, NO_TAG_LABEL, byRailOrder } from './chatHistoryGroups'

/** ── The chat list's view: its order and its filters, for the sidebar and the Chat history panel ──
 *
 *  One flat list of every chat, goal loop and code session (`useThreads`), in the order and under
 *  the filters the owner picks from the list's Sort and filter menu. Pure: the reads and the state
 *  are `useChatList`'s, the drawing `ui/ThreadRail`'s.
 *
 *  · **Order:** pinned first under every sort, then the chosen key, ties by the row's key. Recent
 *    activity, the default, is the sidebar's order before the menu (`byRailOrder`), and a row's state
 *    never moves it. Date created: newest first, rows with none last. Title: the locale's order,
 *    numbers in numeric order, case ignored.
 *  · **Status:** Active (the default: the active chats and every goal loop and code session, which
 *    have no archive), Archived, or All.
 *  · **Kind, Folder and Tag** narrow it; Folder and Tag take several choices and match any of them.
 *    "No folder" (`NONE`) is a row with none or one the list does not hold, "No tag" a row with no
 *    known tag. Until the names read answers, or while it has failed, a row is under its own folder
 *    and tags, and the menu names them by id: a stored Folder or Tag view lists its rows meanwhile.
 *    A chosen id the names no longer hold stays in its section, by id, so it can be unticked.
 */

export type ChatListSort = 'activity' | 'created' | 'title'
export type ChatListStatus = 'active' | 'archived' | 'all'
export type ChatListKind = 'all' | ThreadKind

export interface ChatListView {
  sort: ChatListSort
  status: ChatListStatus
  kind: ChatListKind
  /** The folders shown, by id (`NONE` is No folder); none chosen shows every row. */
  folders: string[]
  /** The tags shown, by id (`NONE` is No tag); none chosen shows every row. */
  tags: string[]
}

/** The choice for the rows with no folder, or no tag: a key no folder or tag has. */
export const NONE = '~none'

/** A multi section's value as its keys. */
function splitKeys(value: string): string[] {
  return [...new Set(value.split(',').filter(Boolean))]
}

export const DEFAULT_VIEW: ChatListView = { sort: 'activity', status: 'active', kind: 'all', folders: [], tags: [] }

/** Where the sidebar's view is kept, on this device. The panel's is its own and kept nowhere. */
export const VIEW_KEY = 'chat-list-view'

const SORTS: readonly ChatListSort[] = ['activity', 'created', 'title']
const STATUSES: readonly ChatListStatus[] = ['active', 'archived', 'all']
const KINDS: readonly ChatListKind[] = ['all', 'chat', 'loop', 'code']

const SORT_WORD: Record<ChatListSort, string> = { activity: 'Recent activity', created: 'Date created', title: 'Title (A to Z)' }
const STATUS_WORD: Record<ChatListStatus, string> = { active: 'Active', archived: 'Archived', all: 'All' }
const KIND_WORD: Record<ChatListKind, string> = { all: 'All kinds', chat: 'Chats', loop: 'Goal loops', code: 'Code sessions' }

/** How many choices Folder and Tag show until opened. */
export const SECTION_LIMIT = 3

function ids(v: unknown): string[] {
  if (!Array.isArray(v)) return []
  return [...new Set(v.filter((x): x is string => typeof x === 'string'))]
}

function oneOf<T extends string>(v: unknown, of: readonly T[], fallback: T): T {
  return typeof v === 'string' && (of as readonly string[]).includes(v) ? (v as T) : fallback
}

/** *raw*, a stored view, as a view: anything it does not say, or says wrongly, is the default's. */
export function parseView(raw: unknown): ChatListView {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { ...DEFAULT_VIEW }
  const v = raw as Record<string, unknown>
  return {
    sort: oneOf(v.sort, SORTS, DEFAULT_VIEW.sort),
    status: oneOf(v.status, STATUSES, DEFAULT_VIEW.status),
    kind: oneOf(v.kind, KINDS, DEFAULT_VIEW.kind),
    folders: ids(v.folders),
    tags: ids(v.tags),
  }
}

/** The sidebar's view, as stored; the default when none is, or it can't be read. */
export function readRailView(): ChatListView {
  try {
    return parseView(JSON.parse(localStorage.getItem(VIEW_KEY) ?? 'null'))
  } catch {
    return { ...DEFAULT_VIEW }
  }
}

export function writeRailView(view: ChatListView): ChatListView {
  try { localStorage.setItem(VIEW_KEY, JSON.stringify(view)) } catch { /* storage full or off: the view lasts this session */ }
  return view
}

/** Whether *view* filters anything away from the default (its sort aside). */
export function filtersSet(view: ChatListView): boolean {
  return view.status !== DEFAULT_VIEW.status || view.kind !== DEFAULT_VIEW.kind || view.folders.length > 0 || view.tags.length > 0
}

/** *view* with every filter back at its default and its sort kept: what Clear filters gives. */
export function clearedFilters(view: ChatListView): ChatListView {
  return { ...DEFAULT_VIEW, sort: view.sort }
}

const titleOrder = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** The list's order under *sort*: pinned first, then the sort's key, then the row's key. Recent
 *  activity is `byRailOrder` itself, so the default can't drift from it. */
export function rowOrder(sort: ChatListSort): (a: Thread, b: Thread) => number {
  if (sort === 'activity') return byRailOrder
  const byKey = (a: Thread, b: Thread) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
  const bySort = sort === 'created'
    ? (a: Thread, b: Thread) => (b.createdAt ?? -Infinity) - (a.createdAt ?? -Infinity) || 0
    : (a: Thread, b: Thread) => titleOrder.compare(a.title, b.title)
  return (a, b) => Number(b.pinned) - Number(a.pinned) || bySort(a, b) || byKey(a, b)
}

/** Whether *row* is listed under *status*: only a chat is ever archived. */
export function underStatus(row: Thread, status: ChatListStatus): boolean {
  if (status === 'all') return true
  return status === 'archived' ? !!row.archived : !row.archived
}

/** The folder a row is under for the Folder filter: its own when the list holds it, else `NONE`
 *  (No folder). With no list in hand (*known* null), its own, whatever it is. */
function folderOf(row: Thread, known: ReadonlySet<string> | null): string {
  return known ? (known.has(row.folderId) ? row.folderId : NONE) : (row.folderId || NONE)
}

/** The tags a row is under for the Tag filter: its known ones, else `[NONE]` (No tag). With no list
 *  in hand (*known* null), its own. */
function tagsOf(row: Thread, known: ReadonlySet<string> | null): string[] {
  const own = known ? row.tagIds.filter((id) => known.has(id)) : row.tagIds
  return own.length > 0 ? own : [NONE]
}

/** The ids a names list holds, or null while there is no list (unread, or its read failed). */
function idsOf(list: readonly { id: string }[] | undefined): Set<string> | null {
  return list ? new Set(list.filter((x) => !!x && typeof x.id === 'string' && x.id !== '').map((x) => x.id)) : null
}

/** A Folder or Tag section's choices: the names in their order (by id while there are none), a
 *  chosen id the names don't hold, then No folder or No tag. */
function namedOptions(
  names: readonly { id: string; name?: string }[] | undefined, known: ReadonlySet<string> | null,
  count: ReadonlyMap<string, number>, chosen: readonly string[], noneLabel: string,
): { key: string; label: string; count: number }[] {
  const named = names && known
    ? names.filter((n) => known.has(n.id) && (count.has(n.id) || chosen.includes(n.id)))
      .map((n) => ({ key: n.id, label: n.name || n.id, count: count.get(n.id) ?? 0 }))
    : [...count.keys()].filter((id) => id !== NONE).map((id) => ({ key: id, label: id, count: count.get(id) ?? 0 }))
  const unlisted = chosen.filter((id) => id !== NONE && !named.some((o) => o.key === id))
    .map((id) => ({ key: id, label: id, count: count.get(id) ?? 0 }))
  const none = count.has(NONE) || chosen.includes(NONE) ? [{ key: NONE, label: noneLabel, count: count.get(NONE) ?? 0 }] : []
  return [...named, ...unlisted, ...none]
}

/** *rows* as the view lists them: under its status, kind, folders and tags, in its order. */
export function applyView(
  rows: readonly Thread[], view: ChatListView,
  folders: readonly ChatFolder[] | undefined, tags: readonly ChatTag[] | undefined,
): Thread[] {
  const knownFolders = idsOf(folders)
  const knownTags = idsOf(tags)
  return rows
    .filter((r) => underStatus(r, view.status))
    .filter((r) => view.kind === 'all' || r.kind === view.kind)
    .filter((r) => view.folders.length === 0 || view.folders.includes(folderOf(r, knownFolders)))
    .filter((r) => view.tags.length === 0 || tagsOf(r, knownTags).some((id) => view.tags.includes(id)))
    .sort(rowOrder(view.sort))
}

/** What the menu's names read failed to give: a section that shows its choices by id, and Retry. */
export interface SectionNames {
  folders: readonly ChatFolder[] | undefined
  tags: readonly ChatTag[] | undefined
  foldersFailed?: boolean
  tagsFailed?: boolean
  onRetryNames?: () => void
}

function said(names: string[]): string {
  return names.length <= 2 ? names.join(', ') : `${names[0]} and ${names.length - 1} more`
}

/** The Sort and filter menu's sections for *rows* (every row the reads hold, before the view) and
 *  *view*: Sort by and Status always; Kind while the rows hold two kinds or more; Folder and Tag
 *  while one holds a row. A section that is set always shows, so it can be cleared. Kind, Folder and
 *  Tag count their rows under the current status. */
export function viewSections(
  rows: readonly Thread[], view: ChatListView, names: SectionNames, onChange: (view: ChatListView) => void,
): FilterSectionDef[] {
  const listed = rows.filter((r) => underStatus(r, view.status))
  const knownFolders = idsOf(names.folders)
  const knownTags = idsOf(names.tags)
  const set = (patch: Partial<ChatListView>) => onChange({ ...view, ...patch })
  const sections: FilterSectionDef[] = [
    {
      title: 'Sort by', value: view.sort, defaultKey: DEFAULT_VIEW.sort,
      options: SORTS.map((key) => ({ key, label: SORT_WORD[key] })),
      onChange: (key) => set({ sort: oneOf(key, SORTS, DEFAULT_VIEW.sort) }),
    },
    {
      title: 'Status', value: view.status, defaultKey: DEFAULT_VIEW.status,
      options: STATUSES.map((key) => ({ key, label: STATUS_WORD[key] })),
      onChange: (key) => set({ status: oneOf(key, STATUSES, DEFAULT_VIEW.status) }),
    },
  ]

  const kinds = new Set(listed.map((r) => r.kind))
  if (kinds.size >= 2 || view.kind !== 'all') {
    sections.push({
      title: 'Kind', value: view.kind, defaultKey: 'all', quiet: true,
      options: KINDS.map((key) => ({ key, label: KIND_WORD[key], count: key === 'all' ? listed.length : listed.filter((r) => r.kind === key).length })),
      onChange: (key) => set({ kind: oneOf(key, KINDS, 'all') }),
    })
  }

  const folderCount = new Map<string, number>()
  for (const r of listed) { const f = folderOf(r, knownFolders); folderCount.set(f, (folderCount.get(f) ?? 0) + 1) }
  const holdsAFolder = [...folderCount.keys()].some((id) => id !== NONE)
  if (holdsAFolder || view.folders.length > 0 || names.foldersFailed) {
    const options = namedOptions(names.folders, knownFolders, folderCount, view.folders, NO_FOLDER_LABEL)
    const label = (key: string) => (key === NONE ? NO_FOLDER_LABEL : names.folders?.find((f) => f.id === key)?.name || key)
    sections.push({
      title: 'Folder', value: view.folders.join(','), defaultKey: '', quiet: true, multi: true,
      limit: SECTION_LIMIT, limitNoun: 'folders',
      options,
      said: (value) => said(splitKeys(value).map(label)),
      onChange: (value) => set({ folders: splitKeys(value) }),
      ...(names.foldersFailed && names.onRetryNames ? { actions: [{ label: "Couldn't load your folders. Retry", onSelect: names.onRetryNames }] } : {}),
    })
  }

  const tagCount = new Map<string, number>()
  for (const r of listed) for (const t of tagsOf(r, knownTags)) tagCount.set(t, (tagCount.get(t) ?? 0) + 1)
  const holdsATag = [...tagCount.keys()].some((id) => id !== NONE)
  if (holdsATag || view.tags.length > 0 || names.tagsFailed) {
    const options = namedOptions(names.tags, knownTags, tagCount, view.tags, NO_TAG_LABEL)
    const label = (key: string) => (key === NONE ? NO_TAG_LABEL : names.tags?.find((t) => t.id === key)?.name || key)
    sections.push({
      title: 'Tag', value: view.tags.join(','), defaultKey: '', quiet: true, multi: true,
      limit: SECTION_LIMIT, limitNoun: 'tags',
      options,
      said: (value) => said(splitKeys(value).map(label)),
      onChange: (value) => set({ tags: splitKeys(value) }),
      ...(names.tagsFailed && names.onRetryNames ? { actions: [{ label: "Couldn't load your tags. Retry", onSelect: names.onRetryNames }] } : {}),
    })
  }
  return sections
}
