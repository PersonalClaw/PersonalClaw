import type { UiDoc } from './uiDoc'

// Doc object for ThreadRail — the sidebar's chat history, grouped by folder or tag (#3814, #5877).
const doc: UiDoc = {
  name: 'ThreadRail',
  keywords: ['chat history', 'chats', 'sidebar', 'rail', 'folders', 'tags', 'group by', 'loops', 'code sessions', 'pip', 'state dot', 'running', 'waiting on you', 'failed', 'disclosure', 'No folder', 'No tag', 'New chat', 'search', 'filter', '⌘1'],
  description:
    "The sidebar's chat history: New chat and a search with a Group by control (Folders or Tags) at the end of its row, at the top, one bar pinned to the top of the chat history while the rows scroll under it, on the rail's solid surface so no row shows through; then every chat, goal loop and code session under its folder (or each of its tags), each group a disclosure and each row a dense button (about 32 px) that opens it. A goal loop's or code session's row says its kind by an icon and in its name. A row shows its state as one dot, the word in the dot's tooltip and the row's name: green and glowing while it runs, orange and glowing while it waits on you, a steady grey dot when it finished unread and a steady red one when it failed (each glow a still ring under reduced motion); the open row is lit with aria-current=\"page\" and its group opens so it can be seen. The search filters the rows by name over what the rail has read; a group with no match leaves the list while it lasts, and nothing matching has its own row. Loading, a failed read and an empty list each have their own row. The rail reports the rows it shows, top to bottom, which ⌘1 to ⌘9 open. NavRail renders it through its `threads` slot, in the expanded rail and the phone's drawer.",
  props: [
    { name: 'groups', description: 'The groups in the order they show, each {id, name, threads}. The shell builds them with historyGroups (app/chatHistoryGroups.ts): the folders (or tags) that hold a row, in Chat history\'s order, then No folder (or No tag) last, each group\'s rows in the named order (byRailOrder: pinned first, then the latest activity, ties by key). A chat under two tags is in both groups.' },
    { name: 'selectedKey', description: 'The key of the open row (threadAt in app/threadRail.tsx reads it from the address), or "" when the page is no chat. Never kept by the rail itself, so reload, Back and a copied address light the same row, under every group it is in.' },
    { name: 'status', description: "'loading' (nothing read yet: one Loading chats row and no groups), 'ready', 'failed' (the failure row with Retry, above the groups the last good read gave), or 'offline' (the gateway is not answering: an Offline row with Retry, above the groups it last listed, and no failure wording)." },
    { name: 'failure', description: 'The sentence a failed read shows beside Retry. Defaults to "Couldn\'t load your chats."' },
    { name: 'onOpen', description: "Opens a row at its route (no leading #/). The shell navigates there and closes the phone's drawer." },
    { name: 'onRetry', description: 'Reads the chats, folders and tags again, from the failure row\'s Retry.' },
    { name: 'onNewThread', description: 'Opens a new chat, from New chat at the top. Its tooltip shows the keymap\'s New chat key.' },
    { name: 'onShown', description: 'Told the rows the rail shows, top to bottom (closed groups and the search applied), whenever they change; null while it shows none it has read. The shell keeps them for ⌘1 to ⌘9, a chat under two tags counted once. Keep it stable.' },
    { name: 'search', description: 'The search, when the caller keeps it. The shell does (App.tsx), so a collapse, which unmounts the rail, gives back what was typed; a reload starts with none. Unset, the rail keeps its own.' },
    { name: 'onSearch', description: 'Told each change of the search, with search.' },
    { name: 'groupBy', description: "How the groups are made, 'folder' (the default) or 'tag'; the search row's Group by control names it." },
    { name: 'onGroupBy', description: 'Told the grouping the owner picks from the Group by control. The shell keeps it on the device (writeGroupBy). Unset, the control is not drawn.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Derive selectedKey from the address on every render; the address is the selection.' },
    { guidance: true, description: "Pass the groups whole: a group's rows window past the shared threshold (WindowedList), so a long folder stays fast and keeps its keyboard order." },
    { guidance: false, description: 'Do not show a failed or loading read as an empty list: pass status, and the rail says which it is.' },
    { guidance: false, description: 'Do not move anything in a row but its glow: a running row\'s dot (green, --color-ok) and a waiting row\'s (orange, --color-state-waiting) glow with the shared status-pulse, each a still ring in its own colour under reduced motion; unread (--color-state-quiet) and failed (--color-state-blocked) are steady dots.' },
    { guidance: false, description: 'Do not give these colours to the orb, the buddy or the project page: they are the rail\'s reading of the states, and those keep the shared state tokens.' },
    { guidance: false, description: 'Do not show a state by colour alone: the dot\'s tooltip and the row\'s name say it in words.' },
    { guidance: false, description: 'Do not sort by status: a turn starting or a run failing changes a pip, never a row\'s place.' },
  ],
  anatomy: [
    'div[role=group] named "Chat history" (aria-label: no label row on screen)',
    'the pinned bar: sticky at the top of the chat history, painted --color-rail, above the rows',
    'New chat, in the bar: a button with its key in the tooltip',
    'the search row, in the bar: Search chats, a SearchField (sm) that Escape clears, and at its end the Group by control (the field\'s height, a folder or tag icon with a chevron, named "Group by: Folders" or "Group by: Tags", opening a two-row menu), after the field in tab order; the field\'s placeholder is "Search" and its name "Search chats"',
    'status row: Loading chats (role=status), or the failure sentence with Retry, or No chats yet, or No chats match (role=status) while a search finds nothing',
    'per group: a disclosure button (chevron, name, count; aria-expanded) and, when open, its rows or "No chats yet"',
    'row: a button (aria-current="page" when open, the selected fill), a goal loop\'s or code session\'s kind icon, its title truncated, and a state dot (data-state-family, the word as its tooltip, the glow behind it while it runs or waits on you, or a still ring under reduced motion)',
  ],
}

export default doc
