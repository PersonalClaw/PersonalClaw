import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { Thread } from '../lib/useThreads'
import { ThreadRail } from './ThreadRail'

// ── The chat list: one list of chats, goal loops and code sessions (#3814, #5877) ─────────────────
//
// Every chat, goal loop or code session the view lists is a dense row that opens it, in one list. A
// row can carry one state mark, its state in the row's tooltip and in its name: running, waiting on
// you, failed. Nothing in a row moves: the mark is still. A pinned chat shows its pin, an
// archived one its archive while the view lists both, and an app's chat names the app. The open row
// is lit (`aria-current="page"`). Loading, a failed read, an empty list and an empty view each have a
// row of their own, so none of them reads as a list with nothing in it.

function thread(over: Partial<Thread> & Pick<Thread, 'key' | 'title'>): Thread {
  const id = over.key.split(':')[1]
  return { kind: 'chat', id, projectId: 'p-garden', route: `chat/${id}`, pip: null, pinned: false, activeAt: 0, folderId: '', tagIds: [], ...over }
}

const ROWS: Thread[] = [
  thread({ key: 'chat:chat-1', title: 'Plan the garden' }),
  thread({ key: 'loop:a1b2c3d4', kind: 'loop', title: 'Find a seed supplier', route: 'loops/a1b2c3d4', pip: 'running' }),
  thread({ key: 'loop:e5f6a7b8', kind: 'code', title: 'Fence gate', route: 'code/e5f6a7b8', pip: 'failed' }),
  thread({ key: 'chat:chat-2', title: 'Order the soil', pip: 'waiting' }),
  thread({ key: 'chat:chat-3', title: 'A question about tax', projectId: '' }),
]

function rail(props: Partial<Parameters<typeof ThreadRail>[0]> = {}) {
  const onOpen = vi.fn()
  const onRetry = vi.fn()
  const utils = render(
    <ThreadRail rows={ROWS} selectedKey="" status="ready" onOpen={onOpen} onRetry={onRetry} {...props} />,
  )
  return { ...utils, onOpen, onRetry }
}

/** What a row's tooltip, the app's own, says: it shows at once on keyboard focus. */
function tipOf(el: Element): string | null {
  act(() => { fireEvent.focus(el) })
  const said = document.querySelector('[data-tooltip-bubble]')?.textContent ?? null
  act(() => { fireEvent.blur(el) })
  return said
}
const rowNames = () => [...document.querySelectorAll('[data-thread-key]')].map((b) => b.getAttribute('aria-label'))

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('the chat history rail', () => {
  it('lists its rows as one list, in the order it is given, with no group', () => {
    rail()
    expect(rowNames()).toEqual([
      'Plan the garden', 'Goal loop: Find a seed supplier, running', 'Code session: Fence gate, failed',
      'Order the soil, waiting on you', 'A question about tax',
    ])
    expect(screen.queryByRole('button', { expanded: true })).toBeNull()
  })

  it.each(['rail', 'panel'] as const)('marks a pinned chat, an archived one while the view lists both, and an app\'s chat by its app, in the %s', (surface) => {
    rail({
      surface,
      markArchived: true,
      rows: [
        thread({ key: 'chat:p', title: 'Trip planning', pinned: true, pip: 'unread', startedBy: { created_by_app: 'zap', created_by_app_name: 'Zapier' } }),
        thread({ key: 'chat:o', title: 'Old notes', archived: true }),
      ],
    })
    const trip = screen.getByRole('button', { name: 'Trip planning, pinned, started by Zapier, unread' })
    expect(trip.querySelectorAll('[data-row-mark]'), 'the pin').toHaveLength(1)
    expect(trip.querySelector('[data-started-by]')?.textContent).toBe('Zapier')
    expect(tipOf(trip)).toContain("A turn in it runs with Zapier's permissions.")
    expect(screen.getByRole('button', { name: 'Old notes, archived' }).querySelectorAll('[data-row-mark]')).toHaveLength(1)
  })

  it.each(['rail', 'panel'] as const)('draws the at-rest edge on the search, the Sort and filter button and the lit row, in the %s', (surface) => {
    rail({
      surface, selectedKey: 'chat:chat-1',
      menu: [{ title: 'Sort by', value: 'activity', defaultKey: 'activity', options: [{ key: 'activity', label: 'Recent activity' }], onChange: () => {} }],
    })
    const edged = [
      screen.getByRole('searchbox', { name: 'Search chats' }),
      screen.getByRole('button', { name: /^Sort and filter/ }),
      document.querySelector('[data-thread-key="chat:chat-1"] span[aria-hidden]'),
    ]
    for (const el of edged) expect(el?.className, el?.tagName).toContain('border-on-surface-low/70')
  })

  it('draws the Sort and filter button open while its menu is', () => {
    rail({ menu: [{ title: 'Sort by', value: 'activity', defaultKey: 'activity', options: [{ key: 'activity', label: 'Recent activity' }], onChange: () => {} }] })
    const trigger = screen.getByRole('button', { name: /^Sort and filter/ })
    fireEvent.click(trigger)
    const classes = trigger.className.split(/\s+/)
    expect(classes).toEqual(expect.arrayContaining(['bg-surface-high', 'text-on-surface']))
    expect(classes).not.toContain('bg-surface-container')
    expect(classes).not.toContain('text-on-surface-var')
  })

  it('says nothing of an archive when the view lists only one kind of row', () => {
    rail({ rows: [thread({ key: 'chat:o', title: 'Old notes', archived: true })] })
    expect(screen.getByRole('button', { name: 'Old notes' }).querySelector('[data-row-mark]')).toBeNull()
  })

  it('says each state in its name and its tooltip, never by the mark alone', () => {
    rail()
    for (const [name, word, family] of [
      [/^Goal loop: Find a seed supplier/, 'running', 'active'],
      [/^Code session: Fence gate/, 'failed', 'blocked'],
      [/^Order the soil/, 'waiting on you', 'waiting'],
    ] as const) {
      const row = screen.getByRole('button', { name })
      const mark = row.querySelector('[data-state-family]')
      expect(mark?.getAttribute('data-state-family')).toBe(family)
      expect(tipOf(row)?.endsWith(` — ${word.charAt(0).toUpperCase() + word.slice(1)}`), `${word}: the tooltip ends with the word`).toBe(true)
      expect(row.getAttribute('aria-label')).toMatch(new RegExp(`, ${word}$`))
    }
    expect(screen.getByRole('button', { name: 'Plan the garden' }).querySelector('[data-state-family]')).toBeNull()
  })

  it('nothing in a row moves: a running or waiting row is a still mark, and it schedules no frame', () => {
    const raf = vi.spyOn(window, 'requestAnimationFrame')
    rail()
    expect(raf).not.toHaveBeenCalled()
    for (const name of [/^Goal loop: Find a seed supplier/, /^Order the soil/, /^Code session: Fence gate/, /^Plan the garden/]) {
      const row = screen.getByRole('button', { name })
      expect(row.querySelector('.status-pulse'), String(name)).toBeNull()
      expect(row.outerHTML, String(name)).not.toMatch(/animate-|pulse|spin/)
    }
    // The control: the running and waiting rows do carry their marks.
    for (const name of [/^Goal loop: Find a seed supplier/, /^Order the soil/]) {
      expect(screen.getByRole('button', { name }).querySelector('[data-orb-mark]'), String(name)).not.toBeNull()
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

  it('an empty view says so and offers Clear filters, rather than saying there are no chats', () => {
    const onClearFilters = vi.fn()
    rail({ rows: [], filtered: true, onClearFilters })
    expect(screen.getByText('No chats in this view')).toBeInTheDocument()
    expect(screen.queryByText('No chats yet')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(onClearFilters).toHaveBeenCalledTimes(1)
  })

  it('a row whose mark changes keeps its place and the focus on its row', () => {
    const view = rail()
    const order = () => [...document.querySelectorAll('[data-thread-key]')].map((r) => r.getAttribute('data-thread-key'))
    const before = order()
    const row = screen.getByRole('button', { name: 'Plan the garden' })
    row.focus()
    const running = ROWS.map((t) => (t.key === 'chat:chat-1' ? { ...t, pip: 'running' as const } : t))
    view.rerender(<ThreadRail rows={running} selectedKey="" status="ready" onOpen={view.onOpen} onRetry={view.onRetry} />)
    expect(screen.getByRole('button', { name: 'Plan the garden, running' }), 'the row says its new state').toBe(document.activeElement)
    expect(order(), 'and nothing moved').toEqual(before)
  })

  it('a long name is cut short on screen and whole in its name and tooltip', () => {
    const title = 'Find a supplier for heritage tomato seeds that ships to the north before the frost'
    rail({ rows: [thread({ key: 'chat:long', title })] })
    const row = screen.getByRole('button', { name: title })
    expect(tipOf(row)).toBe(title)
    expect(row.querySelector('.truncate')?.textContent).toBe(title)
  })

  it('says it is loading, rather than showing no chats', () => {
    rail({ rows: [], status: 'loading' })
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
    rail({ rows: [], status: 'offline' })
    expect(screen.getByText('Offline. Your chats show when the gateway answers again.')).toBeInTheDocument()
    expect(screen.queryByText('No chats yet')).toBeNull()
    expect(screen.queryByText('Loading chats…')).toBeNull()
  })

  it('offline under a filter that lists nothing, says the view is empty, not that the chats are away', () => {
    rail({ rows: [], status: 'offline', filtered: true, onClearFilters: () => {} })
    expect(screen.getByText('Offline. No chats in this view.')).toBeInTheDocument()
  })

  it('with no row at all, says there are none', () => {
    rail({ rows: [] })
    expect(screen.getByText('No chats yet')).toBeInTheDocument()
  })
})
