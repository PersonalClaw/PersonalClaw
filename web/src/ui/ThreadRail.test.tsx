import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { Thread } from '../lib/useThreads'
import { ThreadRail, type ThreadRailGroup } from './ThreadRail'

// ── The chat history rail: groups with their chats beneath (#3814, #5877) ─────────────────────────
//
// Each folder (or tag) is a disclosure group, and under it each chat, goal loop or code session is a
// dense row that opens it. A row can carry one state dot, its state in the dot's tooltip and in the
// row's name: running, waiting on you, failed. Only a running or waiting row's glow moves. The open row is lit
// (`aria-current="page"`), and its group opens so the lit row can be seen. A group's disclosure
// lasts across a reload. Loading, a failed read and an empty group each have a row of their own, so
// none of them reads as a group with nothing in it.

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
    thread({ key: 'loop:e5f6a7b8', kind: 'code', title: 'Fence gate', route: 'code/e5f6a7b8', pip: 'failed' }),
    thread({ key: 'chat:chat-2', title: 'Order the soil', pip: 'waiting' }),
  ],
}
const EMPTY: ThreadRailGroup = { id: 'folder:f-house', name: 'House move', threads: [] }
const LOOSE: ThreadRailGroup = {
  id: 'folder:',
  name: 'No folder',
  threads: [thread({ key: 'chat:chat-3', title: 'A question about tax', projectId: '' })],
}

function rail(props: Partial<Parameters<typeof ThreadRail>[0]> = {}) {
  const onOpen = vi.fn()
  const onRetry = vi.fn()
  const utils = render(
    <ThreadRail groups={[GARDEN, EMPTY, LOOSE]} selectedKey="" status="ready" onOpen={onOpen} onRetry={onRetry} onNewThread={vi.fn()} {...props} />,
  )
  return { ...utils, onOpen, onRetry }
}

const groupToggle = (name: RegExp) => screen.getByRole('button', { name })

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('the chat history rail', () => {
  it('lists each group with its rows beneath, in the order it is given', () => {
    rail()
    const toggles = screen.getAllByRole('button', { name: /, (\d+ chats?|no chats)$/ })
    expect(toggles.map((b) => b.getAttribute('aria-label'))).toEqual(['Garden, 4 chats', 'House move, no chats', 'No folder, 1 chat'])
    const garden = within(document.getElementById(groupToggle(/^Garden/).getAttribute('aria-controls')!)!)
    expect(garden.getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Plan the garden', 'Goal loop: Find a seed supplier, running', 'Code session: Fence gate, failed', 'Order the soil, waiting on you',
    ])
  })

  it('says each state in its name and its dot\'s tooltip, never by the dot alone', () => {
    rail()
    for (const [name, word, family] of [
      [/^Goal loop: Find a seed supplier/, 'running', 'active'],
      [/^Code session: Fence gate/, 'failed', 'blocked'],
      [/^Order the soil/, 'waiting on you', 'waiting'],
    ] as const) {
      const row = screen.getByRole('button', { name })
      const mark = row.querySelector('[data-state-family]')
      expect(mark?.getAttribute('data-state-family')).toBe(family)
      expect(mark?.getAttribute('title')).toBe(word.charAt(0).toUpperCase() + word.slice(1))
      expect(row.getAttribute('aria-label')).toMatch(new RegExp(`, ${word}$`))
    }
    expect(screen.getByRole('button', { name: 'Plan the garden' }).querySelector('[data-state-family]')).toBeNull()
  })

  it("a running or waiting row's one motion is the shared glow, and it schedules no frame", () => {
    const raf = vi.spyOn(window, 'requestAnimationFrame')
    rail()
    expect(raf).not.toHaveBeenCalled()
    for (const name of [/^Goal loop: Find a seed supplier/, /^Order the soil/]) {
      const row = screen.getByRole('button', { name })
      expect(row.querySelectorAll('.status-pulse')).toHaveLength(1)
      expect(row.outerHTML).not.toMatch(/animate-|spin/)
    }
    // Nothing else in the rail moves: a failed row and a calm one have no glow.
    for (const name of [/^Code session: Fence gate/, /^Plan the garden/]) {
      expect(screen.getByRole('button', { name }).outerHTML).not.toMatch(/animate-|pulse|spin/)
    }
  })

  it('lights the open row, and only it', () => {
    rail({ selectedKey: 'loop:e5f6a7b8' })
    const lit = [...document.querySelectorAll('[aria-current="page"]')]
    expect(lit).toHaveLength(1)
    expect(lit[0].getAttribute('aria-label')).toBe('Code session: Fence gate, failed')
  })

  it('opens a row at its own address', () => {
    const { onOpen } = rail()
    fireEvent.click(screen.getByRole('button', { name: /^Code session: Fence gate/ }))
    expect(onOpen).toHaveBeenCalledWith('code/e5f6a7b8')
  })

  it('collapses a group, and keeps it collapsed after a reload', () => {
    const first = rail()
    fireEvent.click(groupToggle(/^Garden/))
    expect(groupToggle(/^Garden/)).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Plan the garden' })).toBeNull()
    first.unmount()
    rail()
    expect(groupToggle(/^Garden/)).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(groupToggle(/^Garden/))
    expect(screen.getByRole('button', { name: 'Plan the garden' })).toBeInTheDocument()
  })

  it('opens the group that holds the open row, so its row can be seen', () => {
    const first = rail()
    fireEvent.click(groupToggle(/^Garden/))
    first.unmount()
    rail({ selectedKey: 'chat:chat-1' })
    expect(groupToggle(/^Garden/)).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Plan the garden' })).toHaveAttribute('aria-current', 'page')
  })

  it('an empty group starts closed, and says it has no chats when opened', () => {
    rail()
    expect(groupToggle(/^House move/)).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(groupToggle(/^House move/))
    const list = document.getElementById(groupToggle(/^House move/).getAttribute('aria-controls')!)!
    expect(list.textContent).toBe('No chats yet')
  })

  it('a row whose mark changes keeps its place and the focus on its row', () => {
    const view = rail()
    const order = () => [...document.querySelectorAll('[data-thread-key]')].map((r) => r.getAttribute('data-thread-key'))
    const before = order()
    const row = screen.getByRole('button', { name: 'Plan the garden' })
    row.focus()
    const running: ThreadRailGroup = { ...GARDEN, threads: GARDEN.threads.map((t) => (t.key === 'chat:chat-1' ? { ...t, pip: 'running' } : t)) }
    view.rerender(<ThreadRail groups={[running, EMPTY, LOOSE]} selectedKey="" status="ready" onOpen={view.onOpen} onRetry={view.onRetry} onNewThread={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Plan the garden, running' }), 'the row says its new state').toBe(document.activeElement)
    expect(order(), 'and nothing moved').toEqual(before)
  })

  it('a long name is cut short on screen and whole in its name and tooltip', () => {
    const title = 'Find a supplier for heritage tomato seeds that ships to the north before the frost'
    rail({ groups: [{ ...GARDEN, threads: [thread({ key: 'chat:long', title })] }] })
    const row = screen.getByRole('button', { name: title })
    expect(row).toHaveAttribute('title', title)
    expect(row.querySelector('.truncate')?.textContent).toBe(title)
  })

  it('says it is loading, rather than showing no chats', () => {
    rail({ groups: [], status: 'loading' })
    expect(screen.getByText('Loading chats…')).toHaveAttribute('role', 'status')
    expect(screen.queryByText('No chats yet')).toBeNull()
  })

  it('says a failed read failed and offers to read again, keeping the rows it last read', () => {
    const { onRetry } = rail({ status: 'failed', failure: "Couldn't load your chats." })
    expect(screen.getByText("Couldn't load your chats.")).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Plan the garden' })).toBeInTheDocument()
  })

  it('offline, says so and keeps the rows the gateway last listed, without calling the read failed', () => {
    const { onRetry } = rail({ status: 'offline' })
    expect(screen.getByText('Offline. These are the chats from before the gateway stopped answering.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Plan the garden' })).toBeInTheDocument()
    expect(screen.queryByText(/Couldn't load/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('offline before any read answered, says the chats come back with the gateway, not that there are none', () => {
    rail({ groups: [], status: 'offline' })
    expect(screen.getByText('Offline. Your chats show when the gateway answers again.')).toBeInTheDocument()
    expect(screen.queryByText('No chats yet')).toBeNull()
    expect(screen.queryByText('Loading chats…')).toBeNull()
  })

  it('with no group and no row at all, says there are none', () => {
    rail({ groups: [] })
    expect(screen.getByText('No chats yet')).toBeInTheDocument()
  })
})
