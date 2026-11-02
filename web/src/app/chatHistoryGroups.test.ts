import { beforeEach, describe, expect, it } from 'vitest'
import { toThreads, type ProjectThreads, type Thread, type ThreadChat } from '../lib/useThreads'
import { GROUP_BY_KEY, historyGroups, numberedRows, readGroupBy, writeGroupBy } from './chatHistoryGroups'

// ── The sidebar's chat history, grouped by folder or by tag (#5877) ────────────────────────────────
//
// 🔴 Red before: the sidebar grouped every chat, goal loop and code session under its project, so a
// folder or a tag the owner keeps in Chat history said nothing there. Now the groups are the folders,
// or the tags after the switch, with No folder (or No tag) last; an empty folder is left out (Chat
// history's own page keeps it as a drop target), and a chat under two tags is numbered once.

function row(key: string, over: Partial<Thread> = {}): Thread {
  return {
    key, kind: 'chat', id: key.split(':')[1] ?? key, title: key, projectId: '', route: `chat/${key}`,
    pip: null, pinned: false, activeAt: 0, folderId: '', tagIds: [], ...over,
  }
}
const FOLDERS = [{ id: 'f-work', name: 'Work' }, { id: 'f-home', name: 'Home life' }, { id: 'f-empty', name: 'Empty' }]
const TAGS = [{ id: 't-urgent', name: 'Urgent' }, { id: 't-ideas', name: 'Ideas' }]
const byProject = (...threads: Thread[]): ProjectThreads[] => [
  { projectId: 'p-1', threads: threads.filter((_, i) => i % 2 === 0) },
  { projectId: '', threads: threads.filter((_, i) => i % 2 === 1) },
]
const shape = (groups: ReturnType<typeof historyGroups>) => groups.map((g) => [g.name, g.threads.map((t) => t.key)])

beforeEach(() => localStorage.clear())

describe('the chat history by folder', () => {
  it('lists each folder that holds a row, in the folders\' order, then No folder last', () => {
    const groups = historyGroups(byProject(
      row('chat:a', { folderId: 'f-home', activeAt: 3 }),
      row('chat:b', { folderId: 'f-work', activeAt: 1 }),
      row('chat:c', { activeAt: 5 }),
      row('loop:l1', { kind: 'loop', activeAt: 4 }),
      row('chat:d', { folderId: 'f-work', activeAt: 9 }),
      row('chat:e', { folderId: 'f-gone', activeAt: 2 }),
    ), 'folder', FOLDERS, TAGS)
    expect(shape(groups)).toEqual([
      ['Work', ['chat:d', 'chat:b']],
      ['Home life', ['chat:a']],
      // A goal loop has no folder, and a folder the list no longer holds is no folder either.
      ['No folder', ['chat:c', 'loop:l1', 'chat:e']],
    ])
    expect(groups.map((g) => g.id)).toEqual(['folder:f-work', 'folder:f-home', 'folder:'])
  })

  it('leaves an empty folder out, and No folder out when every row has one', () => {
    const groups = historyGroups(byProject(row('chat:a', { folderId: 'f-work' })), 'folder', FOLDERS, TAGS)
    expect(shape(groups)).toEqual([['Work', ['chat:a']]])
  })

  it('keeps pinned first, then the latest activity, within a group', () => {
    const groups = historyGroups(byProject(
      row('chat:old', { folderId: 'f-work', activeAt: 1, pinned: true }),
      row('chat:new', { folderId: 'f-work', activeAt: 9 }),
    ), 'folder', FOLDERS, TAGS)
    expect(shape(groups)).toEqual([['Work', ['chat:old', 'chat:new']]])
  })
})

describe('the chat history by tag', () => {
  it('lists a chat under each of its tags, then No tag last', () => {
    const groups = historyGroups(byProject(
      row('chat:both', { tagIds: ['t-ideas', 't-urgent'], activeAt: 5 }),
      row('chat:idea', { tagIds: ['t-ideas'], activeAt: 3 }),
      row('chat:none', { activeAt: 4 }),
      row('code:c1', { kind: 'code', activeAt: 1 }),
      row('chat:gone', { tagIds: ['t-gone'], activeAt: 2 }),
    ), 'tag', FOLDERS, TAGS)
    expect(shape(groups)).toEqual([
      ['Urgent', ['chat:both']],
      ['Ideas', ['chat:both', 'chat:idea']],
      ['No tag', ['chat:none', 'chat:gone', 'code:c1']],
    ])
  })

  it('numbers a chat under two tags once, at its first row', () => {
    const groups = historyGroups(byProject(
      row('chat:both', { tagIds: ['t-ideas', 't-urgent'], activeAt: 5 }),
      row('chat:idea', { tagIds: ['t-ideas'], activeAt: 3 }),
    ), 'tag', FOLDERS, TAGS)
    expect(numberedRows(groups.flatMap((g) => g.threads)).map((t) => t.key)).toEqual(['chat:both', 'chat:idea'])
  })
})

describe('a chat\'s folder and tags', () => {
  it('are read from its row in the chat list, a malformed one read as none', () => {
    const chat = (key: string, over: Partial<ThreadChat>) => ({ key, title: key, origin: 'manual', ...over }) as ThreadChat
    const threads = toThreads({
      sessions: [
        chat('filed', { folder_id: 'f-work', tags: ['t-urgent', 't-ideas'] }),
        chat('odd', { folder_id: 7 as unknown as string, tags: ['', 3, null, 't-ideas'] as unknown as string[] }),
        chat('bare', {}),
      ],
      loops: [], items: [], approvals: [],
    })
    const read = Object.fromEntries(threads.flatMap((g) => g.threads).map((t) => [t.key, [t.folderId, t.tagIds]]))
    expect(read).toEqual({
      'chat:filed': ['f-work', ['t-urgent', 't-ideas']],
      'chat:odd': ['', ['t-ideas']],
      'chat:bare': ['', []],
    })
  })
})

describe('the choice', () => {
  it('is by folder until the owner picks tags, and is kept', () => {
    expect(readGroupBy()).toBe('folder')
    writeGroupBy('tag')
    expect(localStorage.getItem(GROUP_BY_KEY)).toBe('tag')
    expect(readGroupBy()).toBe('tag')
    localStorage.setItem(GROUP_BY_KEY, 'nonsense')
    expect(readGroupBy()).toBe('folder')
  })
})
