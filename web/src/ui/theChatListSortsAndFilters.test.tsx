import { act, cleanup, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { runState } from '../test/runState'

// ── The chat list sorts and filters from one small menu (one list for the sidebar and the panel) ──
//
// 🔴 Red before: the sidebar's search row held a Group by control (Folders or Tags), the list could
// not be sorted, and the archive was never in it. Now a Sort and filter menu at the end of the search
// row orders the one list (Recent activity, Date created, Title) and filters it (Active, Archived,
// All; a kind, a folder, a tag). Its name says what is set, a dot shows while a filter is, an empty
// view offers Clear filters, and the archive is read only while the view asks for it, and kept
// current by the frames Chat history relists on.

/** The frames the list listens for, sent by the test. */
const listeners = new Set<(m: { type: string; data?: unknown }) => void>()
vi.mock('../lib/useChatSocket', async (orig) => {
  const { useEffect, useRef } = await import('react')
  return {
    ...(await orig<Record<string, unknown>>()),
    useChatSocket: (onMessage: (m: { type: string }) => void, _onReconnect: unknown, onStatus?: (c: boolean) => void) => {
      const cb = useRef(onMessage)
      cb.current = onMessage
      useEffect(() => {
        const fn = (m: { type: string }) => cb.current(m)
        listeners.add(fn)
        onStatus?.(true)
        return () => { listeners.delete(fn) }
      }, [onStatus])
    },
  }
})

const session = (key: string, title: string, over: Record<string, unknown> = {}) => ({
  key, title, messages: 2, run_state: runState({ session: key }), origin: 'manual', project_id: '', ...over,
})
let ACTIVE = [
  session('chat-a', 'Bake the bread', { last_activity_ts: '2026-10-20T10:00:00+00:00' }),
  session('chat-b', 'Answer the letters', { last_activity_ts: '2026-10-20T12:00:00+00:00' }),
]
let ARCHIVE = [session('chat-old', 'Cancel the gym', { lifecycle: 'archived' })]
const chatSessions = vi.fn((archived?: boolean) => Promise.resolve(archived ? ARCHIVE : ACTIVE))
/** The reads a case makes fail, by the api method's name. */
let FAILING = new Set<string>()

vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      if (prop === 'chatSessions') return chatSessions(args[0] as boolean | undefined)
      if (FAILING.has(prop)) return Promise.reject(new Error('boom'))
      return Promise.resolve([])
    },
  })
  return { ...real, api: stub }
})

const { ShellThreadRail } = await import('../app/threadRail')
const { resetDataStore } = await import('../lib/data/store')

function rail() {
  return render(<ShellThreadRail route="dashboard" sub="" onOpen={() => {}} onLit={() => {}} search="" onSearch={() => {}} />)
}
const list = () => screen.getByRole('group', { name: 'Chat history' })
const listed = () => [...list().querySelectorAll('[data-thread-key]')].map((e) => e.getAttribute('aria-label'))
const trigger = () => within(list()).getByRole('button', { name: /^Sort and filter/ })
async function choose(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(trigger())
  await user.click(await screen.findByRole('button', { name: new RegExp(`^${name}`) }))
  await user.click(screen.getByRole('button', { name: 'Done' }))
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  resetDataStore()
  // Reset, not cleared: a case that makes the archive fail leaves no failure for the next.
  chatSessions.mockReset()
  chatSessions.mockImplementation((archived?: boolean) => Promise.resolve(archived ? ARCHIVE : ACTIVE))
  ACTIVE = [
    session('chat-a', 'Bake the bread', { last_activity_ts: '2026-10-20T10:00:00+00:00' }),
    session('chat-b', 'Answer the letters', { last_activity_ts: '2026-10-20T12:00:00+00:00' }),
  ]
  ARCHIVE = [session('chat-old', 'Cancel the gym', { lifecycle: 'archived' })]
  FAILING = new Set()
})
afterEach(() => { cleanup(); listeners.clear() })

describe('the Sort and filter menu', () => {
  it('follows the search in the bar, named by what it is set to, and draws no dot at the defaults', async () => {
    rail()
    await waitFor(() => expect(listed()).toEqual(['Answer the letters', 'Bake the bread']))
    const controls = [...list().querySelectorAll('[data-chat-list-bar] button, [data-chat-list-bar] input')]
    const field = within(list()).getByRole('searchbox', { name: 'Search chats' })
    expect(controls.indexOf(trigger())).toBe(controls.indexOf(field) + 1)
    expect(trigger()).toHaveAttribute('aria-label', 'Sort and filter: Recent activity · Active')
    expect(trigger().querySelector('[data-filter-set]')).toBeNull()
  })

  it('reorders the list by title, pinned first, and a pinned app\'s chat keeps its pin and its app', async () => {
    // Made in another order again: Bake the bread newest, then Water, Answer and Zest.
    ACTIVE = [
      session('chat-a', 'Bake the bread', { last_activity_ts: '2026-10-20T10:00:00+00:00', created: '2026-10-19T12:00:00+00:00' }),
      session('chat-b', 'Answer the letters', { last_activity_ts: '2026-10-20T12:00:00+00:00', created: '2026-10-19T10:00:00+00:00' }),
      session('chat-w', 'Water the plants', { last_activity_ts: '2026-10-20T13:00:00+00:00', created: '2026-10-19T11:00:00+00:00' }),
      session('chat-z', 'Zest the lemons', {
        last_activity_ts: '2026-10-20T09:00:00+00:00', created: '2026-10-19T09:00:00+00:00', pinned: true, created_by_app: 'zap', created_by_app_name: 'Zapier',
      }),
    ]
    const user = userEvent.setup()
    rail()
    const zest = 'Zest the lemons, pinned, started by Zapier'
    await waitFor(() => expect(listed()).toEqual([zest, 'Water the plants', 'Answer the letters', 'Bake the bread']))
    await choose(user, 'Title')
    await waitFor(() => expect(listed()).toEqual([zest, 'Answer the letters', 'Bake the bread', 'Water the plants']))
    const pinned = within(list()).getByRole('button', { name: zest })
    expect(pinned.querySelectorAll('[data-row-mark]'), 'its pin, under Title').toHaveLength(1)
    expect(pinned.querySelector('[data-started-by]')?.textContent).toBe('Zapier')
    await choose(user, 'Date created')
    await waitFor(() => expect(listed()).toEqual([zest, 'Bake the bread', 'Water the plants', 'Answer the letters']))
    await choose(user, 'Recent activity')
    await choose(user, 'Date created')
    expect(trigger()).toHaveAttribute('aria-label', 'Sort and filter: Date created · Active')
  })

  it('reads the archive only once Archived is chosen, and lists only it, with a dot on the trigger', async () => {
    const user = userEvent.setup()
    rail()
    await waitFor(() => expect(listed()).toHaveLength(2))
    expect(chatSessions).not.toHaveBeenCalledWith(true)
    await choose(user, 'Archived')
    await waitFor(() => expect(listed()).toEqual(['Cancel the gym']))
    expect(chatSessions).toHaveBeenCalledWith(true)
    expect(trigger().querySelector('[data-filter-set]')).not.toBeNull()
  })

  it('says a failed archive read in its own words, with Retry, and keeps the active rows under All', async () => {
    chatSessions.mockImplementation((archived?: boolean) => (archived ? Promise.reject(new Error('boom')) : Promise.resolve(ACTIVE)))
    const user = userEvent.setup()
    rail()
    await waitFor(() => expect(listed()).toHaveLength(2))
    await choose(user, 'Archived')
    expect(await within(list()).findByText("Couldn't load your archived chats.")).toBeInTheDocument()
    expect(within(list()).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(within(list()).queryByText('No chats in this view')).toBeNull()
    await choose(user, 'All')
    await waitFor(() => expect(listed()).toEqual(['Answer the letters', 'Bake the bread']))
  })

  it('says a failed folders read and a failed tags read each in its own section, and returns each error', async () => {
    const { useChatList } = await import('../app/useChatList')
    for (const [read, line, failed, held] of [
      ['chatFolders', "Couldn't load your folders. Retry", 'foldersError', 'tagsError'],
      ['chatTags', "Couldn't load your tags. Retry", 'tagsError', 'foldersError'],
    ] as const) {
      // Each read fails on its own, on a store with nothing kept from the one before.
      localStorage.clear()
      sessionStorage.clear()
      resetDataStore()
      FAILING = new Set([read])
      const hook = renderHook(() => useChatList('panel'))
      await waitFor(() => expect(hook.result.current[failed], failed).toBeTruthy())
      expect(hook.result.current[held], held).toBeNull()
      hook.unmount()
      const user = userEvent.setup()
      const view = rail()
      await waitFor(() => expect(listed()).toHaveLength(2))
      await user.click(trigger())
      expect(await screen.findByRole('button', { name: line })).toBeInTheDocument()
      view.unmount()
    }
  })

  it('mixes the archive in under All, marking each archived row', async () => {
    const user = userEvent.setup()
    rail()
    await waitFor(() => expect(listed()).toHaveLength(2))
    await choose(user, 'All')
    await waitFor(() => expect(listed()).toHaveLength(3))
    expect(listed()).toContain('Cancel the gym, archived')
  })

  it('an empty view offers Clear filters, which brings the rows back and keeps the sort', async () => {
    const user = userEvent.setup()
    ARCHIVE = []
    ACTIVE = [...ACTIVE, session('chat-w', 'Water the plants', { last_activity_ts: '2026-10-20T13:00:00+00:00' })]
    rail()
    await waitFor(() => expect(listed()).toHaveLength(3))
    await choose(user, 'Title')
    await choose(user, 'Archived')
    expect(await within(list()).findByText('No chats in this view')).toBeInTheDocument()
    await user.click(within(list()).getByRole('button', { name: 'Clear filters' }))
    // By title, not by the latest activity, which would put Water the plants first.
    await waitFor(() => expect(listed()).toEqual(['Answer the letters', 'Bake the bread', 'Water the plants']))
    expect(trigger()).toHaveAttribute('aria-label', 'Sort and filter: Title (A to Z) · Active')
    // The button is gone with the empty view; the focus is in the search, not lost.
    expect(document.activeElement).toBe(within(list()).getByRole('searchbox', { name: 'Search chats' }))
  })

  it('under All, a sessions frame after an archive moves the row to the archive, with no remount', async () => {
    const user = userEvent.setup()
    rail()
    await waitFor(() => expect(listed()).toHaveLength(2))
    await choose(user, 'All')
    await waitFor(() => expect(listed()).toHaveLength(3))
    const root = list()
    ACTIVE = [ACTIVE[1]]
    ARCHIVE = [session('chat-a', 'Bake the bread', { lifecycle: 'archived' }), ...ARCHIVE]
    act(() => { for (const fn of listeners) fn({ type: 'sessions' }) })
    await waitFor(() => expect(listed()).toContain('Bake the bread, archived'), { timeout: 3000 })
    expect(list()).toBe(root)
  })
})
