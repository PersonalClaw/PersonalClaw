import type { ChatFolder, ChatTag } from '../lib/api'
import type { ProjectThreads, Thread } from '../lib/useThreads'

/** ── The sidebar's chat history, grouped by your folders or your tags (#5877) ─────────────────────
 *
 *  The sidebar lists every chat, goal loop and code session (`useThreads`) under the folders Chat
 *  history keeps, or under its tags once the owner switches, the choice kept on this device.
 *
 *  · **By folder:** each folder that holds a row, in the order Chat history lists them (flat, as its
 *    list view is), then **No folder**, last, when a row has none (or one this list does not hold).
 *    An empty folder is left out: the sidebar is for finding a chat, and Chat history's own page
 *    keeps every folder, empty ones included, as a place to drop one.
 *  · **By tag:** each tag that holds a row, in order, then **No tag**, last. A chat with two tags is
 *    listed under both, as the tag board lists it; ⌘1 to ⌘9 number it once, at its first row
 *    (`numberedRows`).
 *  · A goal loop or a code session has no folder and no tag, so it is under No folder or No tag.
 *  · **Within a group, one order** (`byRailOrder`): pinned first, then the latest activity.
 */

export type GroupBy = 'folder' | 'tag'

/** Where the choice is kept, on this device, as the sidebar's width and open groups are. */
export const GROUP_BY_KEY = 'chat-history-group-by'

export const NO_FOLDER_LABEL = 'No folder'
export const NO_TAG_LABEL = 'No tag'

/** A group of the chat history: its key (`folder:<id>`, `tag:<id>`, `folder:` or `tag:` for the
 *  rows with none), the name it is shown under, and its rows in order. */
export interface HistoryGroup {
  id: string
  name: string
  threads: Thread[]
}

/** The owner's choice, as stored; by folder when none is. */
export function readGroupBy(): GroupBy {
  try {
    return localStorage.getItem(GROUP_BY_KEY) === 'tag' ? 'tag' : 'folder'
  } catch {
    return 'folder'
  }
}

export function writeGroupBy(by: GroupBy): GroupBy {
  try { localStorage.setItem(GROUP_BY_KEY, by) } catch { /* storage full or off: the choice lasts this session */ }
  return by
}

/** The sidebar's one order for the rows of a group, chats, goal loops and code sessions alike:
 *  pinned first, then the latest activity, newest first (`Thread.activeAt`), with ties broken by the
 *  row's key. Status never moves a row: the pips say what needs the owner and ⌘⌥A jumps to it. */
export function byRailOrder(a: Thread, b: Thread): number {
  return Number(b.pinned) - Number(a.pinned)
    || b.activeAt - a.activeAt
    || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
}

function listed<T extends { id: string }>(list: readonly T[] | undefined): T[] {
  const seen = new Set<string>()
  return (list ?? []).filter((x) => !!x && typeof x.id === 'string' && x.id !== '' && !seen.has(x.id) && !!seen.add(x.id))
}

/** Every row of *groups* (by project, as `useThreads` reads them), grouped as *by* says. */
export function historyGroups(
  groups: readonly ProjectThreads[],
  by: GroupBy,
  folders: readonly ChatFolder[] | undefined,
  tags: readonly ChatTag[] | undefined,
): HistoryGroup[] {
  const all = groups.flatMap((g) => g.threads).sort(byRailOrder)
  const out: HistoryGroup[] = []
  if (by === 'folder') {
    const known = listed(folders)
    const ids = new Set(known.map((f) => f.id))
    for (const f of known) {
      const threads = all.filter((t) => t.folderId === f.id)
      if (threads.length > 0) out.push({ id: `folder:${f.id}`, name: f.name || f.id, threads })
    }
    const loose = all.filter((t) => !ids.has(t.folderId))
    if (loose.length > 0) out.push({ id: 'folder:', name: NO_FOLDER_LABEL, threads: loose })
    return out
  }
  const known = listed(tags)
  const ids = new Set(known.map((t) => t.id))
  for (const tag of known) {
    const threads = all.filter((t) => t.tagIds.includes(tag.id))
    if (threads.length > 0) out.push({ id: `tag:${tag.id}`, name: tag.name || tag.id, threads })
  }
  const loose = all.filter((t) => !t.tagIds.some((id) => ids.has(id)))
  if (loose.length > 0) out.push({ id: 'tag:', name: NO_TAG_LABEL, threads: loose })
  return out
}

/** The rows ⌘1 to ⌘9 count, top to bottom: each row the sidebar shows, a chat listed under two tags
 *  counted once, at its first row. */
export function numberedRows(shown: readonly Thread[]): Thread[] {
  const seen = new Set<string>()
  return shown.filter((t) => !seen.has(t.key) && !!seen.add(t.key))
}
