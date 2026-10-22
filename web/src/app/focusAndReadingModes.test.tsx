import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent, act } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import { runState } from '../test/runState'
import { PALETTE_SEARCH_LABEL } from './paletteSearchLabel'

// ── Focus mode and reading mode ─────────────────────────────────────────────────────────────────
//
// Two ways to give the window to the work, both driven here through the real shell:
//
// - **Focus mode** (one key) hides the sidebar and closes the side pane. Something that needs a pane
//   opens only that pane, and leaving the mode brings the layout back. It lasts like the collapsed
//   sidebar does.
// - **Reading mode** (one key, in a chat) folds the composer while the owner reads, and it comes back
//   on the key, on focus and on typing, with the draft it held.
//
// Both are reachable by key and from the palette, which shows each one's key, and nothing waits
// behind a hover: focus mode leaves a control on screen that ends it, and the folded composer is a
// line that opens it again.
//
// jsdom notes (the shape `everyDestinationShowsForEveryone.test.tsx` keeps): the socket has no gateway, every read
// resolves empty unless named below, and the file pane's viewer is a stand-in, since what is under
// test is which pane opens, not what a file looks like. jsdom reports no Mac, so keys read as Ctrl.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

vi.mock('../pages/chat/ChatFilePanel', () => ({
  ChatFilePanel: ({ path }: { path: string }) => <section aria-label={`File ${path}`}>{path}</section>,
}))

const SESSION = 'chat-6-focus'
const DETAIL = {
  key: SESSION, title: 'A long answer', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  run_state: runState({ session: SESSION }),
  messages: [
    { role: 'user', content: 'What changed in the feed?', ts: '2026-09-25T10:01:00+00:00' },
    { role: 'assistant', content: 'THE LONG ANSWER. Edited `src/fetch.py` to send the header.', ts: '2026-09-25T10:01:30+00:00' },
  ],
}
const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
  chatSessionDetail: DETAIL,
  sessionCost: { turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 },
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => () => Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : []),
  })
  return { ...real, api: stub }
})

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
await Promise.all([import('../pages/dashboard/DashboardPage'), import('../pages/ChatPage')])

const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

const FOCUS_KEY = { key: '\\', code: 'Backslash', ctrlKey: true }
const READING_KEY = { key: '>', code: 'Period', ctrlKey: true, shiftKey: true }
const PALETTE_KEY = { key: 'k', code: 'KeyK', ctrlKey: true }

const rail = () => document.querySelector('nav[data-tour="rail"]')
const composerRegion = () => document.querySelector<HTMLElement>('[data-composer-region]')
const folded = () => composerRegion()?.dataset.folded === 'true'
const editor = () => document.querySelector<HTMLElement>('.cm-content')!
const foldLine = () => screen.queryByRole('button', { name: /^(Write a reply|Continue your draft)/ })

/** Put *text* in the composer the way a keystroke does (the dispatch → `onChange` path). */
function typeDraft(text: string) {
  const view = EditorView.findFromDOM(editor())!
  act(() => { view.dispatch(view.state.replaceSelection(text)) })
}

/** The palette's row for *name*, found by typing it. */
async function paletteRow(name: RegExp, query: string) {
  fireEvent.keyDown(window, PALETTE_KEY)
  const input = await screen.findByLabelText(PALETTE_SEARCH_LABEL)
  fireEvent.change(input, { target: { value: query } })
  return screen.findByRole('option', { name })
}

async function openChat(query = '') {
  location.hash = `#/chat/${SESSION}${query}`
  renderApp()
  await screen.findByText(/THE LONG ANSWER/)
  await waitFor(() => expect(document.querySelector('.cm-content')).not.toBeNull())
}

class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  location.hash = '#/dashboard'
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
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

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('focus mode', () => {
  it('one key hides the sidebar, and the same key brings it back', async () => {
    renderApp()
    await waitFor(() => expect(rail()).not.toBeNull())
    fireEvent.keyDown(window, FOCUS_KEY)
    await waitFor(() => expect(rail()).toBeNull())
    fireEvent.keyDown(window, FOCUS_KEY)
    await waitFor(() => expect(rail()).not.toBeNull())
  })

  it('lasts across a reload, as the collapsed sidebar does', async () => {
    const first = renderApp()
    await waitFor(() => expect(rail()).not.toBeNull())
    fireEvent.keyDown(window, FOCUS_KEY)
    await waitFor(() => expect(rail()).toBeNull())
    first.unmount()
    renderApp()
    await screen.findByRole('button', { name: 'Leave focus mode' })
    expect(rail()).toBeNull()
  })

  it('leaves a control on screen that ends it, with its key, so nothing waits behind a hover', async () => {
    renderApp()
    await waitFor(() => expect(rail()).not.toBeNull())
    fireEvent.keyDown(window, FOCUS_KEY)
    const leave = await screen.findByRole('button', { name: 'Leave focus mode' })
    expect(leave.getAttribute('title')).toContain('Ctrl+\\')
    fireEvent.click(leave)
    await waitFor(() => expect(rail()).not.toBeNull())
    expect(screen.queryByRole('button', { name: 'Leave focus mode' })).toBeNull()
  })

  it('the palette lists it with its key, and runs it', async () => {
    renderApp()
    await waitFor(() => expect(rail()).not.toBeNull())
    const row = await paletteRow(/Focus mode/, 'focus')
    expect(row.textContent).toContain('Ctrl+\\')
    fireEvent.click(row)
    await waitFor(() => expect(rail()).toBeNull())
  })

  it('on a phone, where the sidebar is already a drawer, it leaves the drawer and its toggle alone', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: /max-width:\s*768px/.test(q), media: q,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
      onchange: null, dispatchEvent: () => false,
    }))
    renderApp()
    await waitFor(() => expect(rail()).not.toBeNull())
    fireEvent.keyDown(window, FOCUS_KEY)
    await waitFor(() => expect(localStorage.getItem('focus-mode')).toBe('1'))
    expect(rail()).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeTruthy()
  })
})

describe('focus mode in a chat', () => {
  it('closes the side pane, opens only the pane asked for, and leaving brings the layout back', async () => {
    await openChat('?activity=1')
    await screen.findByRole('region', { name: 'Activity' })
    fireEvent.keyDown(window, FOCUS_KEY)
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Activity' })).toBeNull())
    expect(rail()).toBeNull()

    fireEvent.click(screen.getByTitle('Open src/fetch.py'))
    await screen.findByRole('region', { name: 'File src/fetch.py' })
    expect(screen.queryByRole('region', { name: 'Activity' })).toBeNull()

    fireEvent.keyDown(window, FOCUS_KEY)
    await screen.findByRole('region', { name: 'Activity' })
    expect(screen.getByRole('region', { name: 'File src/fetch.py' })).toBeTruthy()
    expect(rail()).not.toBeNull()
  })
})

describe('reading mode', () => {
  it('one key folds the composer and the same key brings it back, with its draft', async () => {
    await openChat()
    typeDraft('half a thought')
    expect(folded()).toBe(false)
    fireEvent.keyDown(window, READING_KEY)
    await waitFor(() => expect(folded()).toBe(true))
    const line = foldLine()!
    expect(line.textContent).toContain('half a thought')
    expect(line.getAttribute('title')).toContain('Ctrl+Shift+.')
    fireEvent.keyDown(window, READING_KEY)
    await waitFor(() => expect(folded()).toBe(false))
    expect(foldLine()).toBeNull()
    expect(editor().textContent).toContain('half a thought')
  })

  it('typing brings it back, and the key typed lands in the draft', async () => {
    await openChat()
    typeDraft('half')
    fireEvent.keyDown(window, READING_KEY)
    await waitFor(() => expect(folded()).toBe(true))
    fireEvent.keyDown(document.body, { key: 'x', code: 'KeyX' })
    await waitFor(() => expect(folded()).toBe(false))
    await waitFor(() => expect(editor().textContent).toContain('halfx'))
  })

  it('focus in the composer brings it back', async () => {
    await openChat()
    fireEvent.keyDown(window, READING_KEY)
    await waitFor(() => expect(folded()).toBe(true))
    act(() => { editor().focus() })
    await waitFor(() => expect(folded()).toBe(false))
  })

  it('the line it folds to opens it, with the caret in it', async () => {
    await openChat()
    fireEvent.keyDown(window, READING_KEY)
    await waitFor(() => expect(folded()).toBe(true))
    fireEvent.click(foldLine()!)
    await waitFor(() => expect(folded()).toBe(false))
    await waitFor(() => expect(editor().contains(document.activeElement)).toBe(true))
  })

  it('folding it from inside the composer leaves focus on the line that brings it back', async () => {
    await openChat()
    act(() => { editor().focus() })
    fireEvent.keyDown(editor(), READING_KEY)
    await waitFor(() => expect(folded()).toBe(true))
    expect(document.activeElement).toBe(foldLine())
  })

  it('the palette lists it with its key in a chat, and runs it', async () => {
    await openChat()
    const row = await paletteRow(/Reading mode/, 'reading')
    expect(row.textContent).toContain('Ctrl+Shift+.')
    fireEvent.click(row)
    await waitFor(() => expect(folded()).toBe(true))
  })

  it('away from a chat the palette lists it dimmed, with why, and runs nothing', async () => {
    renderApp()
    await waitFor(() => expect(rail()).not.toBeNull())
    const row = await paletteRow(/Reading mode/, 'reading mode')
    expect(row.getAttribute('aria-disabled')).toBe('true')
    expect(row.textContent).toContain('Open a chat to fold its composer')
    fireEvent.keyDown(window, READING_KEY)
    fireEvent.click(row)
    expect(screen.getByRole('listbox')).toBeTruthy()
    expect(composerRegion()).toBeNull()
  })

  it('the palette lists each mode once, from the keymap', async () => {
    await openChat()
    fireEvent.keyDown(window, PALETTE_KEY)
    const input = await screen.findByLabelText(PALETTE_SEARCH_LABEL)
    fireEvent.change(input, { target: { value: 'mode' } })
    await screen.findByRole('listbox')
    expect(screen.getAllByRole('option', { name: /Focus mode/ })).toHaveLength(1)
    expect(screen.getAllByRole('option', { name: /Reading mode/ })).toHaveLength(1)
  })
})

describe('the shell\'s own toggles', () => {
  it('show their keys in their tooltips, as the keymap writes them', async () => {
    renderApp()
    await waitFor(() => expect(rail()).not.toBeNull())
    expect(screen.getByRole('button', { name: 'Collapse sidebar' }).getAttribute('title')).toBe('Collapse sidebar (Ctrl+B)')
    expect(screen.getByRole('button', { name: 'Open terminal' }).getAttribute('title')).toBe('Open terminal (Ctrl+J or Ctrl+`)')
  })
})
