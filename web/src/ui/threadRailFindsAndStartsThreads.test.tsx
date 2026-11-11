import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { Thread } from '../lib/useThreads'
import { bindingLabel } from '../app/keymap'
import { ThreadRail } from './ThreadRail'

// ── The top of the chat history: New chat, Open Chat history and the search (#3814, #5877) ─────────
//
// New chat, Open Chat history and the search box sit at the top of the chat history, above every
// row, whatever the read says (loading, failed, empty): they never move with the list. New chat opens
// a new chat and shows its key; Open Chat history opens the page, and is lit while it is the page.
// The search filters the list's rows by name as the owner types, on what the list has already read.
// Nothing matching has its own row, so it never reads as a list with no chats.
//
// The list says which rows it shows, top to bottom (`onShown`): the rows ⌘1 to ⌘9 count.

function thread(over: Partial<Thread> & Pick<Thread, 'key' | 'title'>): Thread {
  const id = over.key.split(':')[1]
  return { kind: 'chat', id, projectId: 'p-garden', route: `chat/${id}`, pip: null, pinned: false, activeAt: 0, folderId: '', tagIds: [], ...over }
}

const ROWS: Thread[] = [
  thread({ key: 'chat:chat-1', title: 'Plan the garden' }),
  thread({ key: 'loop:a1b2c3d4', kind: 'loop', title: 'Find a seed supplier', route: 'loops/a1b2c3d4', pip: 'running' }),
  thread({ key: 'chat:chat-2', title: 'Order the soil' }),
  thread({ key: 'chat:chat-4', title: 'Book the movers', projectId: 'p-house' }),
  thread({ key: 'chat:chat-5', title: 'Garden shed', projectId: 'p-house' }),
  thread({ key: 'chat:chat-3', title: 'A question about tax', projectId: '' }),
]

function rail(props: Partial<Parameters<typeof ThreadRail>[0]> = {}, historyLit = false) {
  const onOpen = vi.fn()
  const onRetry = vi.fn()
  const onNewChat = vi.fn()
  const onOpenHistory = vi.fn()
  const onShown = vi.fn()
  const utils = render(
    <ThreadRail rows={ROWS} selectedKey="" status="ready"
      onOpen={onOpen} onRetry={onRetry} top={{ onNewChat, onOpenHistory, historyLit }} onShown={onShown} {...props} />,
  )
  return { ...utils, onOpen, onRetry, onNewChat, onOpenHistory, onShown }
}

const search = () => screen.getByRole('searchbox', { name: 'Search chats' })
const type = (text: string) => fireEvent.change(search(), { target: { value: text } })
/** Every row the rail shows, top to bottom, by its accessible name. */
const rows = () => [...document.querySelectorAll<HTMLElement>('[data-thread-key]')].map((r) => r.getAttribute('aria-label'))
/** The keys of the last rows the rail said it shows. */
const lastShown = (onShown: ReturnType<typeof vi.fn>) => (onShown.mock.calls.at(-1)?.[0] as Thread[] | null)?.map((t) => t.key) ?? null

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('New chat', () => {
  it('sits at the top of the chat history, Open Chat history after it, above every row, and opens a new chat', () => {
    const { onNewChat } = rail()
    const button = screen.getByRole('button', { name: 'New chat' })
    const group = screen.getByRole('group', { name: 'Chat history' })
    const controls = [...group.querySelectorAll('button, input')]
    expect(controls.indexOf(button)).toBe(0)
    expect(controls.indexOf(screen.getByRole('button', { name: 'Open Chat history' }))).toBe(1)
    expect(controls.indexOf(search())).toBe(2)
    expect(controls.indexOf(screen.getByRole('button', { name: 'Plan the garden' }))).toBeGreaterThan(2)
    fireEvent.click(button)
    expect(onNewChat).toHaveBeenCalledTimes(1)
  })

  it('Open Chat history opens the page, and is lit while it is the page', () => {
    const { onOpenHistory } = rail()
    const open = screen.getByRole('button', { name: 'Open Chat history' })
    expect(open).toHaveAttribute('title', 'Open Chat history')
    expect(open).not.toHaveAttribute('aria-current')
    fireEvent.click(open)
    expect(onOpenHistory).toHaveBeenCalledTimes(1)
    cleanup()
    rail({}, true)
    expect(screen.getByRole('button', { name: 'Open Chat history' })).toHaveAttribute('aria-current', 'page')
  })

  it('the panel draws no top row, and names its field apart from the sidebar\'s', () => {
    render(<ThreadRail rows={ROWS} selectedKey="" status="ready" onOpen={vi.fn()} onRetry={vi.fn()} surface="panel" label="Chats" />)
    expect(screen.queryByRole('button', { name: 'New chat' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Open Chat history' })).toBeNull()
    expect(screen.getByRole('group', { name: 'Chats' })).toBeInTheDocument()
    expect(search()).toHaveAttribute('id', 'chat-panel-search')
  })

  it("shows its key in its tooltip, read from the keymap's New chat row", () => {
    rail()
    expect(screen.getByRole('button', { name: 'New chat' })).toHaveAttribute('title', `New chat (${bindingLabel('new-chat')})`)
  })

  it('stays at the top while the chats load, after a failed read, and with none at all', () => {
    for (const props of [{ rows: [], status: 'loading' as const }, { rows: [], status: 'failed' as const }, { rows: [] }]) {
      const { unmount } = rail(props)
      expect(screen.getByRole('button', { name: 'New chat' })).toBeInTheDocument()
      expect(search()).toBeInTheDocument()
      unmount()
    }
  })
})

describe('the search', () => {
  it("filters the list's rows by name, in any case, keeping their order", () => {
    rail()
    type('GARDEN')
    expect(rows()).toEqual(['Plan the garden', 'Garden shed'])
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
  it('are said top to bottom', () => {
    const { onShown } = rail()
    expect(lastShown(onShown)).toEqual(['chat:chat-1', 'loop:a1b2c3d4', 'chat:chat-2', 'chat:chat-4', 'chat:chat-5', 'chat:chat-3'])
  })

  it('follow the search', () => {
    const { onShown } = rail()
    type('garden')
    expect(lastShown(onShown)).toEqual(['chat:chat-1', 'chat:chat-5'])
  })

  it('are none known while the chats load, and the last good read after a failed one', () => {
    const loading = rail({ rows: [], status: 'loading' })
    expect(loading.onShown).toHaveBeenLastCalledWith(null)
    loading.unmount()
    const failedEmpty = rail({ rows: [], status: 'failed' })
    expect(failedEmpty.onShown).toHaveBeenLastCalledWith(null)
    failedEmpty.unmount()
    const failed = rail({ rows: [ROWS[5]], status: 'failed' })
    expect(lastShown(failed.onShown)).toEqual(['chat:chat-3'])
  })

  it('a long list keeps every row in its order while it windows', () => {
    const many = Array.from({ length: 300 }, (_, i) => thread({ key: `chat:c-${String(i).padStart(3, '0')}`, title: `Chat ${i}` }))
    const { onShown } = rail({ rows: many })
    const shown = lastShown(onShown)!
    expect(shown).toHaveLength(300)
    expect(shown.slice(0, 3)).toEqual(['chat:c-000', 'chat:c-001', 'chat:c-002'])
    act(() => { type('Chat 29') })
    expect(lastShown(onShown)).toEqual(['chat:c-029', ...Array.from({ length: 10 }, (_, i) => `chat:c-${290 + i}`)])
    expect(within(screen.getByRole('group', { name: 'Chat history' })).getAllByRole('button', { name: /^Chat 29/ }).length).toBeGreaterThan(0)
  })
})
