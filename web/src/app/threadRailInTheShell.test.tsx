import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState } from '../test/runState'
import { SHIPPED_ROUTES } from './shippedRoutes'

// ── The shell's sidebar lists the chat history, and the address says which row is open (#3814) ────
//
// Driven through the real shell: the rail lists every chat, goal loop and code session as one list,
// in the view the owner picks from its Sort and filter menu, kept across a reload, and lights the one
// the address opens. A row keeps the address of its kind (`#/chat/<key>`, `#/loops/<id>`,
// `#/code/<id>`), so the selection is the address: a reload, Back and a copied address each light
// the row the page shows. While a row is lit, or Open Chat history on its page, the rail's Chat and
// Projects destinations are not also marked current.
//
// The compact path: at a phone's width the rail is the drawer the toolbar opens, and the chat
// history is in it; opening a chat closes the drawer.
//
// A project's page lists its chats beside its detail, each with the rail's mark, opening at its own
// address. And the pages the rows came from still open at their bare addresses.
//
// jsdom notes (the shape `everyDestinationShowsForEveryone.test.tsx` keeps): no gateway socket, every read
// resolves empty unless named below, and the viewport is the `(max-width: 768px)` media query.

vi.mock('../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (await import('../test/quietSocket')).useChatSocket,
}))

vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

const SESSIONS = [
  { key: 'chat-1', title: 'Plan the garden', messages: 2, run_state: runState({ session: 'chat-1' }), origin: 'manual', project_id: 'p-garden', folder_id: 'f-garden' },
  { key: 'chat-2', title: 'Order the soil', messages: 2, run_state: runState({ session: 'chat-2' }), origin: 'manual', project_id: 'p-garden', folder_id: 'f-garden', tags: ['t-buy'] },
  { key: 'chat-3', title: 'A question about tax', messages: 2, run_state: runState({ session: 'chat-3' }), origin: 'manual', project_id: '' },
]
const LOOPS = [{
  id: 'e5f6a7b8', kind: 'code', name: 'Fence gate', task: 'fix the gate', status: 'failed', total_cycles: 2,
  max_cycles: 10, started_at: 1_000, project_id: 'p-garden',
}]
const PROJECTS = [
  { id: 'p-personal', name: 'Personal', is_builtin: true, status: 'active' },
  { id: 'p-garden', name: 'Garden', status: 'active' },
]
// Errands and Later hold no chat, so the sidebar leaves them out.
const FOLDERS = [{ id: 'f-errands', name: 'Errands' }, { id: 'f-garden', name: 'Garden' }]
const TAGS = [{ id: 't-later', name: 'Later' }, { id: 't-buy', name: 'To buy' }]

function detail(key: string) {
  const s = SESSIONS.find((x) => x.key === key)
  return {
    key, title: s?.title ?? key, queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    run_state: runState({ session: key }),
    messages: [
      { role: 'user', content: 'Where do we start?', ts: '2026-10-20T10:01:00+00:00' },
      { role: 'assistant', content: `ANSWER FOR ${key}.`, ts: '2026-10-20T10:01:30+00:00' },
    ],
  }
}

/** The reads that fail in a case, by api method. */
const FAILING = new Set<string>()

const READS: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
  sessionCost: { turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 },
  chatSessions: SESSIONS,
  uLoops: LOOPS,
  projects: PROJECTS,
  chatFolders: FOLDERS,
  chatTags: TAGS,
  chatSessionDetail: detail,
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      if (FAILING.has(prop)) return Promise.reject(new Error(`${prop} failed`))
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
await Promise.all([
  import('../pages/dashboard/Home'), import('../pages/ChatPage'), import('../pages/projects/ProjectsSection'),
  import('../pages/loops/LoopsSection'), import('../pages/code/CodeSection'),
])

const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

const rail = () => screen.getByRole('navigation')
const row = (name: RegExp) => within(rail()).findByRole('button', { name })
const lit = () => [...rail().querySelectorAll('[aria-current="page"]')].map((e) => e.getAttribute('aria-label'))

function setViewport(isMobile: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /max-width:\s*768px/.test(q) ? isMobile : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }))
}

beforeEach(() => {
  FAILING.clear()
  localStorage.clear()
  sessionStorage.clear()
  location.hash = '#/dashboard'
  setViewport(false)
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

/** The rows the rail lists, top to bottom, by name. */
const listed = () => [...rail().querySelectorAll('[data-thread-key]')].map((e) => e.getAttribute('aria-label'))

describe('the sidebar lists the chat history', () => {
  it('as one list, with no group, and Sort and filter at the end of the search row', async () => {
    renderApp()
    await row(/^Plan the garden/)
    expect(listed()).toHaveLength(4)
    expect(await row(/^Code session: Fence gate, failed$/)).toBeInTheDocument()
    expect(within(rail()).queryByRole('button', { name: /^Group by/ })).toBeNull()
    expect(within(rail()).getByRole('button', { name: 'Sort and filter: Recent activity · Active' })).toBeInTheDocument()
  })

  it('in the view the owner picks, kept across a reload', async () => {
    const user = userEvent.setup()
    const first = renderApp()
    await row(/^Plan the garden/)
    await user.click(within(rail()).getByRole('button', { name: /^Sort and filter/ }))
    await user.click(await screen.findByRole('button', { name: /^To buy/ }))
    await waitFor(() => expect(listed()).toEqual(['Order the soil']))
    first.unmount()
    renderApp()
    await row(/^Order the soil/)
    expect(listed()).toEqual(['Order the soil'])
    expect(within(rail()).getByRole('button', { name: 'Sort and filter: Recent activity · Active · To buy' })).toBeInTheDocument()
  })
})

describe('the open row is the one the address opens', () => {
  it('lights its row, and no destination beside it', async () => {
    location.hash = '#/chat/chat-1'
    renderApp()
    await screen.findByText(/ANSWER FOR chat-1/)
    await waitFor(() => expect(lit()).toEqual(['Plan the garden']))
  })

  it('opening another chat moves the light to it, and Back moves it back', async () => {
    location.hash = '#/chat/chat-1'
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(lit()).toEqual(['Plan the garden']))
    await user.click(await row(/^Order the soil/))
    await waitFor(() => expect(location.hash).toBe('#/chat/chat-2'))
    await waitFor(() => expect(lit()).toEqual(['Order the soil']))
    history.back()
    await waitFor(() => expect(location.hash).toBe('#/chat/chat-1'))
    await waitFor(() => expect(lit()).toEqual(['Plan the garden']))
  })

  it('a reload or a copied address lights the same row', async () => {
    location.hash = '#/chat/chat-1'
    const first = renderApp()
    await waitFor(() => expect(lit()).toEqual(['Plan the garden']))
    first.unmount()
    location.hash = '#/code/e5f6a7b8'
    renderApp()
    await waitFor(() => expect(lit()).toEqual(['Code session: Fence gate, failed']))
  })

  it('on the Chat history page lights Open Chat history, and no destination beside it', async () => {
    location.hash = '#/chat/history'
    renderApp()
    await row(/^Plan the garden/)
    await waitFor(() => expect(lit()).toEqual(['Open Chat history']))
  })

  it('Open Chat history, in the pinned bar, opens the Chat history page and is lit there', async () => {
    const user = userEvent.setup()
    location.hash = '#/dashboard'
    renderApp()
    await row(/^Plan the garden/)
    const open = within(rail()).getByRole('button', { name: 'Open Chat history' })
    expect(open.closest('[data-chat-list-bar]')).not.toBeNull()
    await user.click(open)
    await waitFor(() => expect(location.hash).toBe('#/chat/history'))
    await waitFor(() => expect(lit()).toEqual(['Open Chat history']))
  })

  it('lights nothing on a page that is no chat, and the destination keeps its light', async () => {
    location.hash = '#/projects'
    renderApp()
    await row(/^Plan the garden/)
    await waitFor(() => expect(lit()).toEqual(['Projects']))
  })
})

describe('the compact path', () => {
  it("a phone's drawer holds the chat history, and opening a chat closes it", async () => {
    setViewport(true)
    const user = userEvent.setup()
    renderApp()
    const drawer = () => document.querySelector('[role="dialog"][aria-label="Navigation"]')!
    await waitFor(() => expect(drawer()).toHaveAttribute('inert'))
    await user.click(await screen.findByRole('button', { name: 'Expand sidebar' }))
    await waitFor(() => expect(drawer().hasAttribute('inert')).toBe(false))
    await user.click(await within(drawer() as HTMLElement).findByRole('button', { name: /^Order the soil/ }))
    await waitFor(() => expect(location.hash).toBe('#/chat/chat-2'))
    await waitFor(() => expect(drawer()).toHaveAttribute('inert'))
  })
})

describe('a read that fails, and a gateway that is away, each say so', () => {
  it('a failed folders read never fails the list: the menu says it and offers a retry', async () => {
    FAILING.add('chatFolders')
    const user = userEvent.setup()
    renderApp()
    expect(await row(/^Plan the garden/)).toBeInTheDocument()
    expect(within(rail()).queryByText(/Couldn't load/)).toBeNull()
    expect(within(rail()).queryByText('No chats yet')).toBeNull()
    await user.click(within(rail()).getByRole('button', { name: /^Sort and filter/ }))
    expect(await screen.findByRole('button', { name: "Couldn't load your folders. Retry" })).toBeInTheDocument()
  })

  it('a stored Folder view lists its chats while the folders read has failed, and names the folder by its id', async () => {
    FAILING.add('chatFolders')
    localStorage.setItem('chat-list-view', JSON.stringify({ sort: 'activity', status: 'active', kind: 'all', folders: ['f-garden'], tags: [] }))
    const user = userEvent.setup()
    renderApp()
    expect(await row(/^Plan the garden/)).toBeInTheDocument()
    expect(await row(/^Order the soil/)).toBeInTheDocument()
    expect(listed()).toHaveLength(2)
    expect(within(rail()).queryByText('No chats in this view')).toBeNull()
    await user.click(within(rail()).getByRole('button', { name: /^Sort and filter/ }))
    expect(await screen.findByRole('button', { name: /^f-garden/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('a gateway that stopped answering is offline, not a failed read and not an empty list', async () => {
    FAILING.add('system')
    FAILING.add('chatSessions')
    FAILING.add('uLoops')
    FAILING.add('chatFolders')
    FAILING.add('chatTags')
    renderApp()
    expect(await within(rail()).findByText(/^Offline\./)).toBeInTheDocument()
    expect(within(rail()).queryByText(/Couldn't load/)).toBeNull()
    expect(within(rail()).queryByText('No chats yet')).toBeNull()
    expect(within(rail()).queryByText('Loading chats…')).toBeNull()
  })
})

describe("a project's page lists its chats, and the pages they came from still open", () => {
  it("a project's page lists its chats, goal loops and code sessions, and opens each at its address", async () => {
    location.hash = '#/projects/p-garden'
    const user = userEvent.setup()
    renderApp()
    const main = () => document.querySelector('main') as HTMLElement
    expect(await within(main()).findByText('Chats · 3')).toBeInTheDocument()
    const rows = within(main()).getAllByRole('button', { name: /^(Chat|Goal loop|Code session): / }).map((b) => b.getAttribute('aria-label'))
    expect(rows).toEqual(expect.arrayContaining(['Chat: Plan the garden', 'Chat: Order the soil', 'Code session: Fence gate, failed']))
    expect(rows, 'another project\'s chat').not.toContain('Chat: A question about tax')
    await user.click(within(main()).getByRole('button', { name: 'Code session: Fence gate, failed' }))
    await waitFor(() => expect(location.hash).toBe('#/code/e5f6a7b8'))
  })

  // 🔴 Red before (#5877): the Projects page had no view of the chats by project; the sidebar was it.
  // The list is that view now: each project with its live work, the chats in no project folded
  // last, and its search finds a project's chats under it.
  it("the Projects page lists each project, Not in a project last, and its search finds a project's chats, opening each at its address", async () => {
    location.hash = '#/projects'
    const user = userEvent.setup()
    renderApp()
    const main = () => document.querySelector('main') as HTMLElement
    const headings = async () => (await within(main()).findAllByRole('heading', { level: 2 })).map((h) => h.textContent)
    expect(await headings()).toEqual(expect.arrayContaining(['Personal', 'Garden']))
    expect(within(main()).getByRole('button', { name: /^Not in a project, 1 chat/ })).toBeInTheDocument()
    await user.type(within(main()).getByRole('searchbox', { name: 'Search projects and chats' }), 'gate')
    await waitFor(async () => expect(await headings()).toEqual(['Garden']))
    expect(within(main()).queryByRole('button', { name: /^Not in a project/ }), 'no chat in no project matches').toBeNull()
    await user.click(within(main()).getByRole('button', { name: 'Code session: Fence gate, failed' }))
    await waitFor(() => expect(location.hash).toBe('#/code/e5f6a7b8'))
  })

  // Where each lands is the route catalogue's: the bare loop and code addresses open the composer a
  // goal loop or code session starts from (`shippedRoutes.ts`), as they did before the sidebar listed them.
  it.each([['chat', 'chat'], ['projects', 'projects'], ['loops', 'loop'], ['code', 'loop?kind=code']])('#/%s still opens a page: #/%s', async (route, lands) => {
    expect(SHIPPED_ROUTES.find((e) => e.route === route)?.lands ?? route, 'the catalogue agrees').toBe(lands)
    location.hash = `#/${route}`
    renderApp()
    await waitFor(() => expect(document.querySelector('main h1')?.textContent ?? '').not.toBe(''))
    expect(location.hash).toBe(`#/${lands}`)
    expect(document.querySelector('main h1')?.textContent, 'not the fallback').not.toBe('Home')
  })
})
