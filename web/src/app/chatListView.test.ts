import { beforeEach, describe, expect, it } from 'vitest'
import type { Thread } from '../lib/useThreads'
import { limitedOptions } from '../ui/FilterMenu'
import {
  DEFAULT_VIEW, NONE, VIEW_KEY, applyView, clearedFilters, filtersSet, parseView, readRailView, viewSections,
  writeRailView, type ChatListView,
} from './chatListView'

// ── The chat list's view: its order and filters (one list for the sidebar and the panel) ──────────
//
// 🔴 Red before: the sidebar grouped its rows under folders or tags (chatHistoryGroups.historyGroups),
// each group in its own order, and had no sort or filter; this module did not exist. Now one flat
// list, pinned first under every sort, filtered by Status, Kind, Folder and Tag.

function row(key: string, over: Partial<Thread> = {}): Thread {
  return {
    key, kind: 'chat', id: key.split(':')[1] ?? key, title: key, projectId: '', route: `chat/${key}`,
    pip: null, pinned: false, activeAt: 0, folderId: '', tagIds: [], ...over,
  }
}
const FOLDERS = [{ id: 'f-work', name: 'Work' }, { id: 'f-home', name: 'Home life' }, { id: 'f-empty', name: 'Empty' }]
const TAGS = [{ id: 't-urgent', name: 'Urgent' }, { id: 't-ideas', name: 'Ideas' }]
const view = (over: Partial<ChatListView> = {}): ChatListView => ({ ...DEFAULT_VIEW, ...over })
const keys = (rows: Thread[]) => rows.map((t) => t.key)

beforeEach(() => localStorage.clear())

describe('the order', () => {
  // Three orders that differ, so pinned-first, the numeric order and the ignored case each bite.
  const rows = [
    row('chat:a', { activeAt: 9, createdAt: 10, title: 'Beta 10' }),
    row('chat:b', { activeAt: 3, title: 'alpha' }),
    row('chat:c', { activeAt: 1, createdAt: 5, pinned: true, title: 'gamma' }),
    row('loop:l', { kind: 'loop', activeAt: 5, createdAt: 20, title: 'Beta 9' }),
  ]
  it('is pinned first, then the latest activity, by default', () => {
    expect(keys(applyView(rows, DEFAULT_VIEW, FOLDERS, TAGS))).toEqual(['chat:c', 'chat:a', 'loop:l', 'chat:b'])
  })
  it('sorts by date created, newest first, rows with none last, pins still first', () => {
    expect(keys(applyView(rows, view({ sort: 'created' }), FOLDERS, TAGS))).toEqual(['chat:c', 'loop:l', 'chat:a', 'chat:b'])
  })
  it('sorts by title in the locale\'s order, numbers as numbers, case ignored', () => {
    expect(keys(applyView(rows, view({ sort: 'title' }), FOLDERS, TAGS))).toEqual(['chat:c', 'chat:b', 'loop:l', 'chat:a'])
  })
  it('breaks ties by the row\'s key', () => {
    expect(keys(applyView([row('chat:z'), row('chat:y')], DEFAULT_VIEW, FOLDERS, TAGS))).toEqual(['chat:y', 'chat:z'])
  })
})

describe('the filters', () => {
  const rows = [
    row('chat:a', { folderId: 'f-work', tagIds: ['t-urgent'] }),
    row('chat:b', { folderId: 'f-home', tagIds: ['t-ideas', 't-urgent'] }),
    row('chat:c', { folderId: 'f-gone', tagIds: ['t-gone'] }),
    row('chat:old', { archived: true }),
    row('loop:l', { kind: 'loop' }),
    row('loop:k', { kind: 'code' }),
  ]
  it('lists the active chats and every goal loop and code session by default', () => {
    expect(keys(applyView(rows, DEFAULT_VIEW, FOLDERS, TAGS)).sort()).toEqual(['chat:a', 'chat:b', 'chat:c', 'loop:k', 'loop:l'])
  })
  it('lists only the archive under Archived, and both under All', () => {
    expect(keys(applyView(rows, view({ status: 'archived' }), FOLDERS, TAGS))).toEqual(['chat:old'])
    expect(applyView(rows, view({ status: 'all' }), FOLDERS, TAGS)).toHaveLength(6)
  })
  it('filters by kind', () => {
    expect(keys(applyView(rows, view({ kind: 'code' }), FOLDERS, TAGS))).toEqual(['loop:k'])
  })
  it('matches any chosen folder, No folder taking a row with none or one the list does not hold', () => {
    expect(keys(applyView(rows, view({ folders: ['f-work', 'f-home'] }), FOLDERS, TAGS)).sort()).toEqual(['chat:a', 'chat:b'])
    expect(keys(applyView(rows, view({ folders: [NONE] }), FOLDERS, TAGS)).sort()).toEqual(['chat:c', 'loop:k', 'loop:l'])
  })
  it('matches any chosen tag, No tag taking a row with no known tag', () => {
    expect(keys(applyView(rows, view({ tags: ['t-ideas'] }), FOLDERS, TAGS))).toEqual(['chat:b'])
    expect(keys(applyView(rows, view({ tags: [NONE] }), FOLDERS, TAGS)).sort()).toEqual(['chat:c', 'loop:k', 'loop:l'])
  })
  it('filters by a row\'s own folder and tags while the names read is not in hand, or has failed', () => {
    const two = [row('chat:a', { folderId: 'f-work', tagIds: ['t-urgent'] }), row('chat:b')]
    expect(keys(applyView(two, view({ folders: ['f-work'] }), undefined, TAGS))).toEqual(['chat:a'])
    expect(keys(applyView(two, view({ tags: ['t-urgent'] }), FOLDERS, undefined))).toEqual(['chat:a'])
    expect(keys(applyView(two, view({ folders: [NONE] }), undefined, TAGS))).toEqual(['chat:b'])
  })
  it('says when a filter is set, and Clear filters keeps the sort', () => {
    expect(filtersSet(DEFAULT_VIEW)).toBe(false)
    expect(filtersSet(view({ sort: 'title' }))).toBe(false)
    expect(filtersSet(view({ tags: ['t-ideas'] }))).toBe(true)
    expect(clearedFilters(view({ sort: 'title', status: 'all', folders: ['f-work'] }))).toEqual(view({ sort: 'title' }))
  })
})

describe('the kept view', () => {
  it('reads the default when nothing is kept, and what was written after', () => {
    expect(readRailView()).toEqual(DEFAULT_VIEW)
    writeRailView(view({ sort: 'title', tags: ['t-ideas'] }))
    expect(JSON.parse(localStorage.getItem(VIEW_KEY) ?? '')).toEqual(view({ sort: 'title', tags: ['t-ideas'] }))
    expect(readRailView()).toEqual(view({ sort: 'title', tags: ['t-ideas'] }))
  })
  it('reads junk as the default, field by field', () => {
    expect(parseView('folder')).toEqual(DEFAULT_VIEW)
    expect(parseView([1])).toEqual(DEFAULT_VIEW)
    expect(parseView({ sort: 'size', status: 'archived', kind: 7, folders: 'f', tags: ['t', 3, 't'] }))
      .toEqual(view({ status: 'archived', tags: ['t'] }))
    localStorage.setItem(VIEW_KEY, '{not json')
    expect(readRailView()).toEqual(DEFAULT_VIEW)
  })
})

describe('the menu\'s sections', () => {
  const titles = (rows: Thread[], v: ChatListView = DEFAULT_VIEW) =>
    viewSections(rows, v, { folders: FOLDERS, tags: TAGS }, () => {}).map((s) => s.title)

  it('shows Sort by and Status always, and hides a section with nothing to choose', () => {
    expect(titles([row('chat:a')])).toEqual(['Sort by', 'Status'])
  })
  it('shows Kind for two kinds, Folder while a folder holds a row and Tag while a tag does', () => {
    expect(titles([row('chat:a', { folderId: 'f-work', tagIds: ['t-ideas'] }), row('loop:l', { kind: 'loop' })]))
      .toEqual(['Sort by', 'Status', 'Kind', 'Folder', 'Tag'])
  })
  it('keeps a set section in sight with nothing to choose', () => {
    expect(titles([row('chat:a')], view({ kind: 'loop', tags: ['t-ideas'] }))).toEqual(['Sort by', 'Status', 'Kind', 'Tag'])
  })
  it('counts the choices under the current Status, and lists the folders in their order, then No folder', () => {
    const rows = [row('chat:a', { folderId: 'f-home' }), row('chat:b', { folderId: 'f-work' }), row('chat:c', { folderId: 'f-work', archived: true }), row('chat:d')]
    const folder = viewSections(rows, DEFAULT_VIEW, { folders: FOLDERS, tags: TAGS }, () => {}).find((s) => s.title === 'Folder')
    expect(folder?.options.map((o) => [o.label, o.count])).toEqual([['Work', 1], ['Home life', 1], ['No folder', 1]])
  })
  it('changes the view from a section, a multi section by its keys', () => {
    let got: ChatListView | null = null
    const rows = [row('chat:a', { folderId: 'f-work' })]
    const sections = viewSections(rows, DEFAULT_VIEW, { folders: FOLDERS, tags: TAGS }, (v) => { got = v })
    sections.find((s) => s.title === 'Folder')?.onChange(`f-work,${NONE}`)
    expect(got).toEqual(view({ folders: ['f-work', NONE] }))
    sections.find((s) => s.title === 'Sort by')?.onChange('title')
    expect(got).toEqual(view({ sort: 'title' }))
  })
  it('shows ids and a Retry row when the names read failed, and never fails the list', () => {
    const folder = viewSections([row('chat:a', { folderId: 'f-x' }), row('chat:b')], DEFAULT_VIEW,
      { folders: undefined, tags: TAGS, foldersFailed: true, onRetryNames: () => {} }, () => {}).find((s) => s.title === 'Folder')
    expect(folder?.options.map((o) => [o.key, o.label, o.count])).toEqual([['f-x', 'f-x', 1], [NONE, 'No folder', 1]])
    expect(folder?.actions?.map((a) => a.label)).toEqual(["Couldn't load your folders. Retry"])
  })
  it('keeps a chosen folder or tag the names no longer hold, by its id, so it can be unticked', () => {
    const sections = viewSections([row('chat:a', { folderId: 'f-work', tagIds: ['t-urgent'] })], view({ folders: ['f-gone'], tags: ['t-gone'] }),
      { folders: FOLDERS, tags: TAGS }, () => {})
    expect(sections.find((s) => s.title === 'Folder')?.options.find((o) => o.key === 'f-gone')).toMatchObject({ label: 'f-gone', count: 0 })
    expect(sections.find((s) => s.title === 'Tag')?.options.find((o) => o.key === 't-gone')).toMatchObject({ label: 't-gone', count: 0 })
  })
  it('shows at most three choices until opened, the chosen ones always among them', () => {
    const many = Array.from({ length: 6 }, (_, i) => ({ id: `f${i}`, name: `F${i}` }))
    const rows = many.flatMap((f, i) => Array.from({ length: i + 1 }, (_, j) => row(`chat:${f.id}-${j}`, { folderId: f.id })))
    const sections = viewSections(rows, view({ folders: ['f0'] }), { folders: many, tags: TAGS }, () => {})
    const folder = sections.find((s) => s.title === 'Folder')
    expect(folder && limitedOptions(folder).map((o) => o.key)).toEqual(['f0', 'f4', 'f5'])
    expect(folder?.options).toHaveLength(6)
  })
})
