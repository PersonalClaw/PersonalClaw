import type { UiDoc } from './uiDoc'

// Doc object for ThreadRail — the chat list, one list for the sidebar and the Chat history panel (#3814, #5877).
const doc: UiDoc = {
  name: 'ThreadRail',
  keywords: ['chat history', 'chat list', 'chats', 'sidebar', 'rail', 'panel', 'sort', 'filter', 'folders', 'tags', 'archived', 'pinned', 'loops', 'code sessions', 'pip', 'state mark', 'running', 'waiting on you', 'failed', 'New chat', 'Open Chat history', 'search', '⌘1'],
  description:
    "The chat list: one list of every chat, goal loop and code session the view lists, in the sidebar and in the Chat history panel. At the top, one bar pinned to the top of the list while the rows scroll under it, on its surface's solid ground so no row shows through: the sidebar's New chat and, at its end, Open Chat history (lit on the Chat history page); then the search with a small Sort and filter menu at the end of its row. The menu orders the rows (Recent activity, Date created, Title), pinned first under every sort, and filters them (Active, Archived or All; a kind, folders, tags); long Folder and Tag sections show three choices, the chosen ones first and then the largest, and a Show all row; its name says what is set, and a dot shows while a filter is. Each row is a dense button (about 32 px) that opens it. A goal loop's or code session's row says its kind by an icon and in its name, a pinned chat its pin, an archived chat (while the view lists both) its archive, and an app's chat the app's name in visible text. A row shows its state as the orb's 12 px still mark, the word in the row's tooltip, the app's own, and in its name: working's two rings in green while it runs, the clasp in orange while it waits on you, the orb's done mark in grey when it finished unread and in red with a × when it failed; nothing in a row moves; a row's tooltip sits beside it, so it covers no other row; the open row is lit with aria-current=\"page\". The search filters the rows by name over what the list has read, and nothing matching has its own row. Loading, a failed read, an empty list and an empty view each have their own row. The list reports the rows it shows, top to bottom, which ⌘1 to ⌘9 open. NavRail renders it through its `threads` slot (ShellThreadRail), and the New chat page's Chat history panel with `surface=\"panel\"`.",
  props: [
    { name: 'rows', description: 'The rows the view lists, in its order, before the search: useChatList (app/useChatList.ts) applies the view (chatListView.applyView: pinned first, then the sort, ties by key; Status, Kind, Folder and Tag).' },
    { name: 'selectedKey', description: 'The key of the open row (threadAt in app/threadRail.tsx reads it from the address), or "" when the page is no chat; always "" in the panel. Never kept by the list itself, so reload, Back and a copied address light the same row.' },
    { name: 'status', description: "'loading' (nothing read yet: one Loading chats row and no rows), 'ready', 'failed' (the failure row with Retry, above the rows the last good read gave), or 'offline' (the gateway is not answering: an Offline row with Retry, above the rows it last listed, and no failure wording). useChatList computes it once for both hosts." },
    { name: 'failure', description: 'The sentence a failed read shows beside Retry. Defaults to "Couldn\'t load your chats."; a failed archive read says "Couldn\'t load your archived chats."' },
    { name: 'onOpen', description: "Opens a row at its route (no leading #/). The shell navigates there and closes the phone's drawer." },
    { name: 'onRetry', description: 'Reads the rows again, from the failure or offline row\'s Retry.' },
    { name: 'top', description: "The sidebar's first row: { onNewChat, onOpenHistory, historyLit }. New chat's tooltip shows the keymap's New chat key; Open Chat history is a 32 px icon button (Maximize2, the panel's own way to the page), lit with aria-current=\"page\" while the Chat history page is the page. New chat is never cut short: at large text its label wraps, and where one word has no room beside Open Chat history, Open Chat history takes a line of its own at the row's end. The panel passes none." },
    { name: 'surface', description: "'rail' (the default: the bar on var(--color-rail), the field named chat-search) or 'panel' (the bar on var(--color-surface), the dock's ground, the field named chat-panel-search, so both fields can be on screen at once)." },
    { name: 'label', description: 'The list\'s accessible name, default "Chat history"; the panel passes "Chats", since its region is already named Chat history.' },
    { name: 'onShown', description: 'Told the rows the list shows, top to bottom (the view and the search applied), whenever they change; null while it shows none it has read. The shell keeps them for ⌘1 to ⌘9. Keep it stable.' },
    { name: 'search', description: 'The search, when the caller keeps it. The shell does (App.tsx), so a collapse, which unmounts the rail, gives back what was typed; a reload starts with none. Unset, the list keeps its own (the panel).' },
    { name: 'onSearch', description: 'Told each change of the search, with search.' },
    { name: 'menu', description: "The Sort and filter menu's sections (chatListView.viewSections), drawn as FilterMenu size=\"sm\" at the end of the search row and named by headerFilterLabel(menu, 'Sort and filter'). Unset, no menu is drawn." },
    { name: 'filtered', description: 'A filter is off its default, so an empty view says "No chats in this view" with Clear filters rather than "No chats yet".' },
    { name: 'onClearFilters', description: "Puts every filter back at its default, keeping the sort, from the empty view's Clear filters." },
    { name: 'markArchived', description: 'The view lists archived chats beside active ones (Status: All), so an archived row shows the Archive mark and says "archived" in its name.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Derive selectedKey from the address on every render; the address is the selection.' },
    { guidance: true, description: "Pass the view's rows whole: the list windows past the shared threshold (WindowedList), so hundreds of chats stay fast and keep their keyboard order." },
    { guidance: true, description: 'Read both hosts through useChatList, so the two lists on the New chat page never say different things about the same chats.' },
    { guidance: false, description: 'Do not show a failed or loading read as an empty list: pass status, and the list says which it is.' },
    { guidance: false, description: 'Do not move anything in a row: its state mark is the orb\'s still 12 px mark (OrbMark), in green (--color-ok) while it runs, orange (--color-state-waiting) while it waits on you, grey (--color-state-quiet) when unread and red (--color-state-blocked) when failed, and it never glows or turns.' },
    { guidance: false, description: 'Do not give these colours to the orb, the buddy or the project page: they are the list\'s reading of the states, and those keep the shared state tokens.' },
    { guidance: false, description: 'Do not show a state by colour alone: the row\'s tooltip and its name say it in words, and the mark\'s form tells the states apart.' },
    { guidance: false, description: 'Do not sort by status: a turn starting or a run failing changes a pip, never a row\'s place.' },
  ],
  anatomy: [
    'div[role=group] named "Chat history" (the panel\'s "Chats"; aria-label: no label row on screen), data-chat-list naming its surface',
    'the pinned bar (data-chat-list-bar): sticky at the top of the list, painted its surface\'s ground, above the rows',
    'the sidebar\'s first row: New chat, a button with its key in the tooltip, and Open Chat history at its end',
    'the search row: Search chats, a SearchField (sm) that Escape clears, and at its end the Sort and filter menu (FilterMenu size="sm": the sliders icon, the field\'s height, named by what is set, a dot while a filter is), after the field in tab order',
    'status row: Loading chats (role=status), or the failure sentence with Retry, or No chats yet, or No chats in this view with Clear filters, or No chats match (role=status) while a search finds nothing',
    'row: a button (aria-current="page" when open, the selected fill), its marks before the title (a goal loop\'s or code session\'s kind, a pin, an archive), its title truncated, an app\'s chat\'s app name at its end, and a state mark (data-state-family, the orb\'s still 12 px mark of its state in the row\'s colour); the row\'s tooltip is the app\'s own, beside the row: its name in words with the state\'s word, then what the name lacks (an app\'s permissions, a helper\'s ask), which alone is its description; never a native title',
  ],
}

export default doc
