import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState } from '../test/runState'
import { COMMAND_EVENT, bindingOf } from './keymap'

// ── ⌘1 to ⌘9 open the threads the sidebar shows, and New thread starts one (#3814) ───────────────
//
// Driven through the real shell. ⌘n opens the nth thread row of the rail, top to bottom: the
// projects in their order, each project's threads in the named order (pinned first, then the
// latest activity), the threads of a project the owner closed skipped, and only the matches while a
// search is typed. They replace the chat list's order the keys followed until threads replaced it.
// With the rail collapsed to icons the keys open the same threads, read when the key is pressed.
// A key past the last thread says so and goes nowhere. The desktop app's Go menu sends the same
// rows by id. New thread, at the top of the threads, opens a new thread, as ⌘N does.

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
const chat = (key: string, title: string, project: string, activity: string, over: Record<string, unknown> = {}) => ({
  key, title, messages: 2, run_state: runState({ session: key }), origin: 'manual', project_id: project, last_activity_ts: at(activity), ...over,
})

// Listed in no useful order: the rail sorts them.
const SESSIONS = [
  chat('chat-soil', 'Order the soil', 'p-garden', '09:00'),
  chat('chat-tax', 'A question about tax', '', '12:00'),
  chat('chat-plan', 'Plan the garden', 'p-garden', '11:00'),
  chat('chat-boxes', 'Pack the boxes', 'p-house', '10:00'),
  chat('chat-pin', 'Garden budget', 'p-garden', '01:00', { pinned: true }),
  // A chat the phone started is not the owner's own list, but it is a thread like any other.
  chat('chat-phone', 'Shopping list', 'p-house', '08:00', { origin: 'channel', source_id: 'C1', source_label: 'Telegram' }),
]
const LOOPS = [{
  id: 'e5f6a7b8', kind: 'code', name: 'Fence gate', task: 'fix the gate', status: 'failed', total_cycles: 2,
  max_cycles: 10, started_at: Date.parse(at('10:30')) / 1000, created_at: Date.parse(at('10:29')) / 1000, project_id: 'p-garden',
}]
const PROJECTS = [
  { id: 'p-personal', name: 'Personal', is_builtin: true, status: 'active' },
  { id: 'p-garden', name: 'Garden', status: 'active' },
  { id: 'p-house', name: 'House move', status: 'active' },
]

/** The rail's rows, top to bottom, as ⌘1 to ⌘9 count them. */
const RAIL_ORDER = [
  ['chat/chat-pin', 'Garden budget'], // Garden: pinned first
  ['chat/chat-plan', 'Plan the garden'], // then 11:00
  ['code/e5f6a7b8', 'Fence gate'], // 10:30
  ['chat/chat-soil', 'Order the soil'], // 09:00
  ['chat/chat-boxes', 'Pack the boxes'], // House move: 10:00
  ['chat/chat-phone', 'Shopping list'], // 08:00
  ['chat/chat-tax', 'A question about tax'], // No project, last
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
  chatSessions: SESSIONS,
  uLoops: LOOPS,
  projects: PROJECTS,
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
  it("are the keymap's rows for the sidebar's threads", () => {
    expect(bindingOf('chat-1')).toMatchObject({ label: 'Open thread 1 in the sidebar', chords: ['mod+1'] })
    expect(bindingOf('chat-9')).toMatchObject({ label: 'Open thread 9 in the sidebar', chords: ['mod+9'] })
    expect(bindingOf('new-chat')).toMatchObject({ label: 'New thread', chords: ['mod+n'] })
  })

  it('open the rail\'s threads top to bottom, in its order, across projects', async () => {
    renderApp()
    await row(/^Garden budget/)
    for (const [i, [route]] of RAIL_ORDER.entries()) {
      location.hash = '#/dashboard'
      expect(await afterKey(i + 1)).toBe(route)
    }
  })

  it('skip the threads of a project the owner closed', async () => {
    const user = userEvent.setup()
    renderApp()
    await user.click(await row(/^Garden, 4 threads$/))
    expect(await afterKey(1)).toBe('chat/chat-boxes')
    location.hash = '#/dashboard'
    expect(await afterKey(3)).toBe('chat/chat-tax')
  })

  it('count only the matches while a search is typed', async () => {
    renderApp()
    await row(/^Garden budget/)
    // "the" matches Plan the garden and Order the soil in Garden, and Pack the boxes in House move.
    fireEvent.change(within(rail()).getByRole('searchbox', { name: 'Search threads' }), { target: { value: 'the' } })
    await waitFor(() => expect(within(rail()).queryByRole('button', { name: /^Garden budget/ })).toBeNull())
    expect(await afterKey(1)).toBe('chat/chat-plan')
    location.hash = '#/dashboard'
    expect(await afterKey(3)).toBe('chat/chat-boxes')
    location.hash = '#/dashboard'
    press(4)
    expect((await screen.findAllByText('There is no thread 4 in the sidebar.')).length).toBeGreaterThan(0)
  })

  it('with the rail collapsed to icons, open the same threads, read when pressed', async () => {
    localStorage.setItem('nav-collapsed', '1')
    renderApp()
    await screen.findByRole('navigation')
    expect(within(rail()).queryByRole('group', { name: 'Threads' })).toBeNull()
    expect(await afterKey(3)).toBe('code/e5f6a7b8')
    expect(calls).toContain('uLoops')
    expect(calls).toContain('projects')
  })

  it('a key past the last thread says so, and goes nowhere', async () => {
    renderApp()
    await row(/^Garden budget/)
    press(8)
    expect((await screen.findAllByText('There is no thread 8 in the sidebar.')).length).toBeGreaterThan(0)
    expect(location.hash).toBe('#/dashboard')
  })

  it("the desktop app's Go menu runs the same rows by id", async () => {
    renderApp()
    await row(/^Garden budget/)
    act(() => { window.dispatchEvent(new CustomEvent(COMMAND_EVENT, { detail: 'chat-2' })) })
    await waitFor(() => expect(location.hash).toBe('#/chat/chat-plan'))
  })
})

describe('New thread', () => {
  it('at the top of the threads, opens a new thread, as ⌘N does', async () => {
    const user = userEvent.setup()
    renderApp()
    await row(/^Garden budget/)
    await user.click(within(rail()).getByRole('button', { name: 'New thread' }))
    await waitFor(() => expect(location.hash).toBe('#/chat/new'))
  })
})
