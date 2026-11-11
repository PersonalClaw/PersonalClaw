import type { Thread } from '../lib/useThreads'

/** ── The chat history's groups and its one order ─────────────────────────────────────────────────
 *
 *  The sidebar lists every chat, goal loop and code session as one list in the view the owner picks
 *  (`chatListView.ts`). What remains here is shared with Projects, which groups a project's chats:
 *
 *  · **One order** (`byRailOrder`): pinned first, then the latest activity, the sidebar's default.
 *  · **The words for a row with no folder or no tag**, which the list's Folder and Tag filters say.
 */

export const NO_FOLDER_LABEL = 'No folder'
export const NO_TAG_LABEL = 'No tag'

/** A group of chats: its key, the name it is shown under, and its rows in order. */
export interface HistoryGroup {
  id: string
  name: string
  threads: Thread[]
}

/** The sidebar's one order for rows, chats, goal loops and code sessions alike: pinned first, then
 *  the latest activity, newest first (`Thread.activeAt`), with ties broken by the row's key. Status
 *  never moves a row: the pips say what needs the owner and ⌘⌥A jumps to it. */
export function byRailOrder(a: Thread, b: Thread): number {
  return Number(b.pinned) - Number(a.pinned)
    || b.activeAt - a.activeAt
    || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
}
