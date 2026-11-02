import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { Thread } from '../lib/useThreads'
import { bindingLabel } from '../app/keymap'
import { ThreadRail, type ThreadRailGroup } from './ThreadRail'

// ── The top of the chat history: New chat and search (#3814, #5877) ───────────────────────────────
//
// New chat and the search box sit at the top of the chat history, above every group, whatever the
// read says (loading, failed, empty): they never move with the list. New chat opens a new chat and
// shows its key. The search filters the rail's rows by name as the owner types, on what the rail
// has already read: a group with no match leaves the list while the search lasts, a closed group
// with a match shows it, and clearing the search puts every group back as the owner left it.
// Nothing matching has its own row, so it never reads as a group with no chats.
//
// The rail says which rows it shows, top to bottom (`onShown`): the rows ⌘1 to ⌘9 count.

function thread(over: Partial<Thread> & Pick<Thread, 'key' | 'title'>): Thread {
  const id = over.key.split(':')[1]
  return { kind: 'chat', id, projectId: 'p-garden', route: `chat/${id}`, pip: null, pinned: false, activeAt: 0, folderId: '', tagIds: [], ...over }
}

const GARDEN: ThreadRailGroup = {
  id: 'folder:f-garden',
  name: 'Garden',
  threads: [
    thread({ key: 'chat:chat-1', title: 'Plan the garden' }),
    thread({ key: 'loop:a1b2c3d4', kind: 'loop', title: 'Find a seed supplier', route: 'loops/a1b2c3d4', pip: 'running' }),
    thread({ key: 'chat:chat-2', title: 'Order the soil' }),
  ],
}
const HOUSE: ThreadRailGroup = {
  id: 'folder:f-house',
  name: 'House move',
  threads: [thread({ key: 'chat:chat-4', title: 'Book the movers', projectId: 'p-house' }), thread({ key: 'chat:chat-5', title: 'Garden shed', projectId: 'p-house' })],
}
const EMPTY: ThreadRailGroup = { id: 'folder:f-empty', name: 'Empty', threads: [] }
const LOOSE: ThreadRailGroup = {
  id: 'folder:',
  name: 'No folder',
  threads: [thread({ key: 'chat:chat-3', title: 'A question about tax', projectId: '' })],
}

function rail(props: Partial<Parameters<typeof ThreadRail>[0]> = {}) {
  const onOpen = vi.fn()
  const onRetry = vi.fn()
  const onNewThread = vi.fn()
  const onShown = vi.fn()
  const utils = render(
    <ThreadRail groups={[GARDEN, HOUSE, EMPTY, LOOSE]} selectedKey="" status="ready"
      onOpen={onOpen} onRetry={onRetry} onNewThread={onNewThread} onShown={onShown} {...props} />,
  )
  return { ...utils, onOpen, onRetry, onNewThread, onShown }
}

const search = () => screen.getByRole('searchbox', { name: 'Search chats' })
const type = (text: string) => fireEvent.change(search(), { target: { value: text } })
const groupToggle = (name: RegExp) => screen.getByRole('button', { name })
/** Every row the rail shows, top to bottom, by its accessible name. */
const rows = () => [...document.querySelectorAll<HTMLElement>('[data-thread-key]')].map((r) => r.getAttribute('aria-label'))
/** The keys of the last rows the rail said it shows. */
const lastShown = (onShown: ReturnType<typeof vi.fn>) => (onShown.mock.calls.at(-1)?.[0] as Thread[] | null)?.map((t) => t.key) ?? null

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('New chat', () => {
  it('sits at the top of the chat history, above every group, and opens a new chat', () => {
    const { onNewThread } = rail()
    const button = screen.getByRole('button', { name: 'New chat' })
    const group = screen.getByRole('group', { name: 'Chat history' })
    const controls = [...group.querySelectorAll('button, input')]
    expect(controls.indexOf(button)).toBe(0)
    expect(controls.indexOf(search())).toBe(1)
    expect(controls.indexOf(groupToggle(/^Garden,/))).toBeGreaterThan(1)
    fireEvent.click(button)
    expect(onNewThread).toHaveBeenCalledTimes(1)
  })

  it("shows its key in its tooltip, read from the keymap's New chat row", () => {
    rail()
    expect(screen.getByRole('button', { name: 'New chat' })).toHaveAttribute('title', `New chat (${bindingLabel('new-chat')})`)
  })

  it('stays at the top while the chats load, after a failed read, and with none at all', () => {
    for (const props of [{ groups: [], status: 'loading' as const }, { groups: [], status: 'failed' as const }, { groups: [] }]) {
      const { unmount } = rail(props)
      expect(screen.getByRole('button', { name: 'New chat' })).toBeInTheDocument()
      expect(search()).toBeInTheDocument()
      unmount()
    }
  })
})

describe('the search', () => {
  it("filters the rail's rows by name, in any case, and leaves out a group with no match", () => {
    rail()
    type('GARDEN')
    expect(rows()).toEqual(['Plan the garden', 'Garden shed'])
    expect(screen.queryByRole('button', { name: /^No folder,/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Empty,/ })).toBeNull()
    expect(groupToggle(/^Garden,/)).toHaveAttribute('aria-label', 'Garden, 1 of 3 chats')
    expect(groupToggle(/^House move,/)).toHaveAttribute('aria-label', 'House move, 1 of 2 chats')
  })

  it('shows a match in a group the owner closed, and puts the group back as it was left once cleared', () => {
    rail()
    fireEvent.click(groupToggle(/^House move,/))
    expect(rows()).not.toContain('Garden shed')
    type('shed')
    expect(groupToggle(/^House move,/)).toHaveAttribute('aria-expanded', 'true')
    expect(rows()).toEqual(['Garden shed'])
    type('')
    expect(groupToggle(/^House move,/)).toHaveAttribute('aria-expanded', 'false')
    expect(rows()).toEqual(['Plan the garden', 'Goal loop: Find a seed supplier, running', 'Order the soil', 'A question about tax'])
    expect(JSON.parse(localStorage.getItem('chat-history-groups-open') ?? '{}')).toEqual({ 'folder:f-house': false })
  })

  it('a group closed during a search opens again when the search changes, and keeps its stored state', () => {
    rail()
    type('garden')
    fireEvent.click(groupToggle(/^Garden,/))
    expect(rows()).toEqual(['Garden shed'])
    type('garden s')
    type('garden')
    expect(rows()).toEqual(['Plan the garden', 'Garden shed'])
    expect(localStorage.getItem('chat-history-groups-open')).toBeNull()
  })

  it('says when nothing matches, in a row of its own, and aloud', () => {
    rail()
    type('  zebra ')
    expect(rows()).toEqual([])
    const said = screen.getAllByText('No chats match “zebra”')
    expect(said.filter((e) => !e.classList.contains('sr-only'))).toHaveLength(1)
    expect(screen.getByRole('status')).toHaveTextContent('No chats match “zebra”')
    expect(screen.queryByText('No chats yet')).toBeNull()
  })

  it('says how many chats match, aloud, and nothing while no search is typed', () => {
    rail()
    expect(screen.getByRole('status')).toHaveTextContent(/^$/)
    type('garden')
    expect(screen.getByRole('status')).toHaveTextContent('2 chats')
    type('soil')
    expect(screen.getByRole('status')).toHaveTextContent('1 chat')
  })

  it('Escape clears it', () => {
    rail()
    type('zebra')
    fireEvent.keyDown(search(), { key: 'Escape' })
    expect(search()).toHaveValue('')
    expect(rows()).toHaveLength(6)
  })

  it('searches only what the rail has read, and asks for nothing', () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
    rail()
    type('soil')
    expect(rows()).toEqual(['Order the soil'])
    expect(fetch).not.toHaveBeenCalled()
  })
})

describe('the rows the rail shows', () => {
  it('are said top to bottom, without the rows of a closed group', () => {
    const { onShown } = rail()
    expect(lastShown(onShown)).toEqual(['chat:chat-1', 'loop:a1b2c3d4', 'chat:chat-2', 'chat:chat-4', 'chat:chat-5', 'chat:chat-3'])
    fireEvent.click(groupToggle(/^Garden,/))
    expect(lastShown(onShown)).toEqual(['chat:chat-4', 'chat:chat-5', 'chat:chat-3'])
  })

  it('follow the search', () => {
    const { onShown } = rail()
    type('garden')
    expect(lastShown(onShown)).toEqual(['chat:chat-1', 'chat:chat-5'])
  })

  it('are none known while the chats load, and the last good read after a failed one', () => {
    const loading = rail({ groups: [], status: 'loading' })
    expect(loading.onShown).toHaveBeenLastCalledWith(null)
    loading.unmount()
    const failedEmpty = rail({ groups: [], status: 'failed' })
    expect(failedEmpty.onShown).toHaveBeenLastCalledWith(null)
    failedEmpty.unmount()
    const failed = rail({ groups: [LOOSE], status: 'failed' })
    expect(lastShown(failed.onShown)).toEqual(['chat:chat-3'])
  })

  it('a long group keeps every row in its order while it windows', () => {
    const many: ThreadRailGroup = {
      ...GARDEN,
      threads: Array.from({ length: 300 }, (_, i) => thread({ key: `chat:c-${String(i).padStart(3, '0')}`, title: `Chat ${i}` })),
    }
    const { onShown } = rail({ groups: [many] })
    const shown = lastShown(onShown)!
    expect(shown).toHaveLength(300)
    expect(shown.slice(0, 3)).toEqual(['chat:c-000', 'chat:c-001', 'chat:c-002'])
    act(() => { type('Chat 29') })
    expect(lastShown(onShown)).toEqual(['chat:c-029', ...Array.from({ length: 10 }, (_, i) => `chat:c-${290 + i}`)])
    expect(within(screen.getByRole('group', { name: 'Chat history' })).getAllByRole('button', { name: /^Chat 29/ }).length).toBeGreaterThan(0)
  })
})
