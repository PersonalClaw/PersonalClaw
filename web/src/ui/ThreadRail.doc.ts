import type { UiDoc } from './uiDoc'

// Doc object for ThreadRail — the sidebar's projects with their threads beneath (#3814).
const doc: UiDoc = {
  name: 'ThreadRail',
  keywords: ['threads', 'thread', 'projects', 'sidebar', 'rail', 'chats', 'loops', 'code sessions', 'pip', 'running', 'waiting on you', 'failed', 'disclosure', 'No project'],
  description:
    "The sidebar's threads: every chat, goal loop and code session under its project, each project a disclosure group and each thread a dense row (about 32 px) that opens it. A row says its state in words beside one static StateMark (running, waiting on you, failed); the open thread is lit with aria-current=\"page\" and its group opens so it can be seen. Loading, a failed read and an empty project each have their own row. NavRail renders it through its `threads` slot, in the expanded rail and the phone's drawer.",
  props: [
    { name: 'groups', description: 'The groups in the order they show, each {projectId, name, threads}. The shell builds them with railGroups (app/threadRail.ts): the project list\'s order, "No project" last.' },
    { name: 'selectedKey', description: 'The key of the open thread (threadAt in app/threadRail.ts reads it from the address), or "" when the page is no thread. Never kept by the rail itself, so reload, Back and a copied address light the same row.' },
    { name: 'status', description: "'loading' (nothing read yet: one Loading threads row and no groups), 'ready', or 'failed' (the failure row with Retry, above the groups the last good read gave)." },
    { name: 'failure', description: 'The sentence a failed read shows beside Retry. Defaults to "Couldn\'t load your threads."' },
    { name: 'onOpen', description: "Opens a thread at its route (no leading #/). The shell navigates there and closes the phone's drawer." },
    { name: 'onRetry', description: 'Reads the threads again, from the failure row\'s Retry.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Derive selectedKey from the address on every render; the address is the selection.' },
    { guidance: true, description: "Pass the groups whole: a group's rows window past the shared threshold (WindowedList), so a long project stays fast and keeps its keyboard order." },
    { guidance: false, description: 'Do not show a failed or loading read as an empty project: pass status, and the rail says which it is.' },
    { guidance: false, description: 'Do not animate a row or its mark: a running thread updates its static mark and nothing in the rail moves.' },
  ],
  anatomy: [
    'div[role=group] labelled by its "Threads" Eyebrow',
    'status row: Loading threads (role=status), or the failure sentence with Retry, or No threads yet',
    'per project: a disclosure button (chevron, name, count; aria-expanded) and, when open, its rows or "No threads yet"',
    'thread row: a button (aria-current="page" when open, the selected fill), its title truncated, and a StateMark with the state\'s words',
  ],
}

export default doc
