import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState } from '../test/runState'
import { COMMAND_EVENT, bindingOf } from './keymap'

// ── ⌘1 to ⌘9 open the chats the sidebar shows, and New chat starts one (#3814, #5877) ─────────────
//
// Driven through the real shell. ⌘n opens the nth row of the chat history, top to bottom: the one
// list in its view (pinned first, then the latest activity, by default), only the rows a filter
// keeps, and only the matches while a search is typed. With the rail collapsed to icons the keys
// open the same rows, read when the key is pressed, in the view the device keeps, reading the
// folders only when the view filters by them and the archive only when its Status asks for it. A
// key past the last row says so and goes nowhere. The desktop app's Go menu sends the same rows by
// id. New chat, at the top of the chat history, opens a new chat, as ⌘N does.

vi.mock('../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))

vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

const at = (hhmm: string) => `2026-10-20T${hhmm}:00+00:00`
const chat = (key: string, title: string, folder: string, activity: string, over: Record<string, unknown> = {}) => ({
  key, title, messages: 2, run_state: runState({ session: key }), origin: 'manual', project_id: 'p-garden', folder_id: folder, last_activity_ts: at(activity), ...over,
})

// Listed in no useful order: the rail sorts them.
const SESSIONS = [
  chat('chat-soil', 'Order the soil', 'f-garden', '09:00'),
  chat('chat-tax', 'A question about tax', '', '12:00'),
  chat('chat-plan', 'Plan the garden', 'f-garden', '11:00'),
  chat('chat-boxes', 'Pack the boxes', 'f-house', '10:00'),
  chat('chat-pin', 'Garden budget', 'f-garden', '01:00', { pinned: true }),
  // A chat the phone started is not the owner's own list, but it is a chat like any other.
  chat('chat-phone', 'Shopping list', 'f-house', '08:00', { origin: 'channel', source_id: 'C1', source_label: 'Telegram' }),
]
/** The archive, read only while the view's Status asks for it. */
const ARCHIVED = [chat('chat-seeds', 'Seed catalogue', 'f-garden', '07:00', { archived: true })]
const LOOPS = [{
  id: 'e5f6a7b8', kind: 'code', name: 'Fence gate', task: 'fix the gate', status: 'failed', total_cycles: 2,
  max_cycles: 10, started_at: Date.parse(at('10:30')) / 1000, created_at: Date.parse(at('10:29')) / 1000, project_id: 'p-garden',
}]
const PROJECTS = [
  { id: 'p-personal', name: 'Personal', is_builtin: true, status: 'active' },
  { id: 'p-garden', name: 'Garden', status: 'active' },
]
const FOLDERS = [{ id: 'f-empty', name: 'Empty' }, { id: 'f-garden', name: 'Garden' }, { id: 'f-house', name: 'House move' }]

/** The rail's rows, top to bottom, as ⌘1 to ⌘9 count them. */
const RAIL_ORDER = [
  ['chat/chat-pin', 'Garden budget'], // pinned first
  ['chat/chat-tax', 'A question about tax'], // then 12:00
  ['chat/chat-plan', 'Plan the garden'], // 11:00
  ['code/e5f6a7b8', 'Fence gate'], // 10:30
  ['chat/chat-boxes', 'Pack the boxes'], // 10:00
  ['chat/chat-soil', 'Order the soil'], // 09:00
  ['chat/chat-phone', 'Shopping list'], // 08:00
]

function detail(key: string) {
  const s = SESSIONS.find((x) => x.key === key)
  return {
    key, title: s?.title ?? key, queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    run_state: runState({ session: key }),
    messages: [
      { role: 'user', content: 'Where do we start?', ts: at('10:01') },
      { role: 'assistant', content: `ANSWER FOR ${key}.`, ts: at('10:02') },
    ],
  }
}

const calls: string[] = []
const READS: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
  sessionCost: { turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 },
  chatSessions: (archived?: boolean) => (archived ? ARCHIVED : SESSIONS),
  uLoops: LOOPS,
  projects: PROJECTS,
  chatFolders: FOLDERS,
  chatSessionDetail: detail,
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      calls.push(prop)
      const read = READS[prop]
      return Promise.resolve(typeof read === 'function' ? read(...args) : prop in READS ? read : [])
    },
  })
  return { ...real, api: stub }
})

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
await Promise.all([import('../pages/dashboard/Home'), import('../pages/ChatPage')])

const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

const rail = () => screen.getByRole('navigation')
const row = (name: RegExp) => within(rail()).findByRole('button', { name })
const press = (n: number) => act(() => {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: String(n), code: `Digit${n}`, metaKey: true, ctrlKey: true, bubbles: true, cancelable: true }))
})
/** The address after ⌘n, from the page the test is on. */
async function afterKey(n: number, before = location.hash) {
  press(n)
  await waitFor(() => expect(location.hash).not.toBe(before))
  return location.hash.replace(/^#\//, '')
}

beforeEach(() => {
  calls.length = 0
  localStorage.clear()
  sessionStorage.clear()
  location.hash = '#/dashboard'
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  }))
  Element.prototype.scrollIntoView ??= () => {}
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('⌘1 to ⌘9', () => {
  it("are the keymap's rows for the sidebar's chats", () => {
    expect(bindingOf('chat-1')).toMatchObject({ label: 'Open chat 1 in the sidebar', chords: ['mod+1'] })
    expect(bindingOf('chat-9')).toMatchObject({ label: 'Open chat 9 in the sidebar', chords: ['mod+9'] })
    expect(bindingOf('new-chat')).toMatchObject({ label: 'New chat', chords: ['mod+n'] })
  })

  it('open the rail\'s rows top to bottom, in its order', async () => {
    renderApp()
    await row(/^Garden budget/)
    for (const [i, [route]] of RAIL_ORDER.entries()) {
      location.hash = '#/dashboard'
      expect(await afterKey(i + 1)).toBe(route)
    }
  })

  it('open only the rows the view keeps', async () => {
    const user = userEvent.setup()
    renderApp()
    await row(/^Garden budget/)
    await user.click(within(rail()).getByRole('button', { name: /^Sort and filter/ }))
    // The menu's Garden folder, not the Garden budget row in the rail.
    const garden = (await screen.findAllByRole('button', { name: /^Garden/ })).find((b) => !rail().contains(b))
    await user.click(garden!)
    await user.click(screen.getByRole('button', { name: 'Done' }))
    await waitFor(() => expect(within(rail()).queryByRole('button', { name: /^Pack the boxes/ })).toBeNull())
    expect(await afterKey(2)).toBe('chat/chat-plan')
    location.hash = '#/dashboard'
    expect(await afterKey(3)).toBe('chat/chat-soil')
  })

  it('count only the matches while a search is typed', async () => {
    renderApp()
    await row(/^Garden budget/)
    // "the" matches Plan the garden (11:00), Pack the boxes (10:00) and Order the soil (09:00).
    fireEvent.change(within(rail()).getByRole('searchbox', { name: 'Search chats' }), { target: { value: 'the' } })
    await waitFor(() => expect(within(rail()).queryByRole('button', { name: /^Garden budget/ })).toBeNull())
    expect(await afterKey(1)).toBe('chat/chat-plan')
    location.hash = '#/dashboard'
    expect(await afterKey(3)).toBe('chat/chat-soil')
    location.hash = '#/dashboard'
    press(4)
    expect((await screen.findAllByText('There is no chat 4 in the sidebar.')).length).toBeGreaterThan(0)
  })

  it('with the rail collapsed to icons, open the same rows, read when pressed', async () => {
    localStorage.setItem('nav-collapsed', '1')
    renderApp()
    await screen.findByRole('navigation')
    expect(within(rail()).queryByRole('group', { name: 'Chat history' })).toBeNull()
    expect(await afterKey(7)).toBe('chat/chat-phone')
    expect(calls).toContain('uLoops')
    expect(calls, 'the default view filters by no folder').not.toContain('chatFolders')
  })

  it('with the rail mounted, follow a stored Title view', async () => {
    localStorage.setItem('chat-list-view', JSON.stringify({ sort: 'title', status: 'active', kind: 'all', folders: [], tags: [] }))
    renderApp()
    await row(/^Garden budget/)
    expect(await afterKey(3)).toBe('code/e5f6a7b8')
  })

  it('with the rail collapsed, follow a stored Title view: pinned first, then A to Z', async () => {
    localStorage.setItem('nav-collapsed', '1')
    localStorage.setItem('chat-list-view', JSON.stringify({ sort: 'title', status: 'active', kind: 'all', folders: [], tags: [] }))
    renderApp()
    await screen.findByRole('navigation')
    expect(await afterKey(1)).toBe('chat/chat-pin')
    location.hash = '#/dashboard'
    // A question about tax, then Fence gate: under Recent activity ⌘3 is Plan the garden.
    expect(await afterKey(3)).toBe('code/e5f6a7b8')
  })

  it('with the rail collapsed, count the archived rows under Archived, and only those', async () => {
    localStorage.setItem('nav-collapsed', '1')
    localStorage.setItem('chat-list-view', JSON.stringify({ sort: 'activity', status: 'archived', kind: 'all', folders: [], tags: [] }))
    renderApp()
    await screen.findByRole('navigation')
    expect(await afterKey(1)).toBe('chat/chat-seeds')
    location.hash = '#/dashboard'
    press(2)
    expect((await screen.findAllByText('There is no chat 2 in the sidebar.')).length).toBeGreaterThan(0)
  })

  it('a key past the last row says so, and goes nowhere', async () => {
    renderApp()
    await row(/^Garden budget/)
    press(8)
    expect((await screen.findAllByText('There is no chat 8 in the sidebar.')).length).toBeGreaterThan(0)
    expect(location.hash).toBe('#/dashboard')
  })

  it("the desktop app's Go menu runs the same rows by id", async () => {
    renderApp()
    await row(/^Garden budget/)
    act(() => { window.dispatchEvent(new CustomEvent(COMMAND_EVENT, { detail: 'chat-2' })) })
    await waitFor(() => expect(location.hash).toBe('#/chat/chat-tax'))
  })
})

describe('New chat', () => {
  it('at the top of the chat history, opens a new chat, as ⌘N does', async () => {
    const user = userEvent.setup()
    renderApp()
    await row(/^Garden budget/)
    await user.click(within(rail()).getByRole('button', { name: 'New chat' }))
    await waitFor(() => expect(location.hash).toBe('#/chat/new'))
  })
})
