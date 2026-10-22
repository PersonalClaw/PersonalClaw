import { StrictMode } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { runState } from '../test/runState'
import type { Command } from './CommandPalette'

// ── The terminal drawer, inside a thread ────────────────────────────────────────────────────────
//
// The terminal is the drawer at the bottom of every page (#3826), and the Terminal page is its Open
// full view. Driven here through the real shell, in a chat:
//
// - A shell code block's Run opens the drawer and runs its command once, in the one session the
//   drawer opens for it, though that session registers while its socket is still connecting. A
//   second Run goes to the same session.
// - ⌘J and ⌘` show and hide it, and moving to another thread keeps its sessions.
// - A new session starts where the gateway puts it (no folder asked for), as it did before.
// - Open full opens the session the drawer shows on the Terminal page, not the page's first one.
// - The sidebar has no Terminal row, even where the old sidebar listed or pinned one; the page stays
//   at its address and ⌘K's Terminal row opens it.
//
// The PTY view is a stand-in that registers with the real bridge (`pages/terminal/terminalBridge.ts`)
// and refuses a send until its socket would be open, as `TerminalView` does: what is under test is
// the shell's wiring, not xterm. jsdom reports no Mac, so the keys read as Ctrl.

/** What the PTYs were sent, by session; each create's arguments; the sessions the gateway holds. */
const pty = vi.hoisted(() => ({
  sent: [] as Array<[string, string]>,
  created: [] as unknown[][],
  live: [] as Array<{ session_id: string; cwd: string; shell: string; alive: boolean }>,
}))

vi.mock('../pages/terminal/TerminalView', async () => {
  const { useEffect } = await import('react')
  const { registerTerminal, unregisterTerminal } = await import('../pages/terminal/terminalBridge')
  return {
    TerminalView: ({ tab }: { tab: { id: string; label: string } }) => {
      useEffect(() => {
        let open = false
        const opening = setTimeout(() => { open = true }, 40)
        registerTerminal(tab.id, (text) => {
          if (!open) return false
          pty.sent.push([tab.id, text])
          return true
        })
        return () => { clearTimeout(opening); unregisterTerminal(tab.id) }
      }, [tab.id])
      return <div role="group" aria-label={`PTY ${tab.label}`} />
    },
  }
})

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

/** The rows the shell hands ⌘K. */
let rows: readonly Command[] = []
vi.mock('./CommandPalette', async (orig) => {
  const real = await orig<typeof import('./CommandPalette')>()
  return { ...real, CommandPalette: ({ commands }: { commands: Command[] }) => { rows = commands; return null } }
})

vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

const SESSION = 'chat-7-drawer'
const OTHER = 'chat-8-other'
const COMMAND = 'ls -la'
const detail = (key: string, content: string) => ({
  key, title: 'Listing the folder', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  run_state: runState({ session: key }),
  messages: [
    { role: 'user', content: 'What is in the folder?', ts: '2026-09-25T10:01:00+00:00' },
    { role: 'assistant', content, ts: '2026-09-25T10:01:30+00:00' },
  ],
})
const DETAILS: Record<string, unknown> = {
  [SESSION]: detail(SESSION, `RUN THIS ONE:\n\n\`\`\`bash\n${COMMAND}\n\`\`\`\n`),
  [OTHER]: detail(OTHER, 'THE OTHER THREAD.'),
}
const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
  sessionCost: { turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 },
  personalclawConfig: { dashboard: { terminal: { persist: false } } },
  sandboxProviders: { providers: [] },
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const terminal: Record<string, (...args: never[]) => Promise<unknown>> = {
    createTerminal: (...args: unknown[]) => {
      pty.created.push(args)
      const s = { session_id: `pty-${pty.created.length}`, cwd: '/home/ada', shell: '/bin/zsh', alive: true }
      pty.live.push(s)
      return Promise.resolve(s)
    },
    terminalSessions: () => Promise.resolve({ enabled: true, persist_available: false, sessions: [...pty.live] }),
    deleteTerminal: () => Promise.resolve({ ok: true }),
    chatSessionDetail: (key: string) => Promise.resolve(DETAILS[key]),
  }
  const stub = new Proxy({}, {
    get: (_t, prop: string) => terminal[prop] ?? (() => Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : [])),
  })
  return { ...real, api: stub }
})

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
await Promise.all([import('../pages/ChatPage'), import('../pages/terminal/TerminalPage')])

const J = { key: 'j', code: 'KeyJ', ctrlKey: true }
const BACKQUOTE = { key: '`', code: 'Backquote', ctrlKey: true }

/** The corner's toggle names what it will do, so it says whether the drawer is open. */
const drawerOpen = () => screen.queryByRole('button', { name: 'Hide terminal' }) !== null
/** The drawer itself, found by its Open full control. */
const theDrawer = () => screen.getByRole('button', { name: 'Open full terminal' }).closest('div.fixed') as HTMLElement

async function openChat(key: string, text: RegExp) {
  location.hash = `#/chat/${key}`
  // Under `StrictMode`, as `main.tsx` renders: each session view registers, leaves and registers again.
  render(<StrictMode><ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider></StrictMode>)
  await screen.findByText(text)
}

/** Every send to the PTYs that was *command*, by session. */
const runsOf = (command: string) => pty.sent.filter(([, text]) => text === `${command}\n`)

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
  pty.sent.length = 0
  pty.created.length = 0
  pty.live.length = 0
  localStorage.clear()
  sessionStorage.clear()
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
afterEach(() => { cleanup(); vi.unstubAllGlobals(); location.hash = '' })

describe('Run in terminal, from a thread', () => {
  it('opens the drawer and runs the command once, in the one session it opened for it', async () => {
    await openChat(SESSION, /RUN THIS ONE/)
    expect(drawerOpen()).toBe(false)
    fireEvent.click(screen.getByTitle('Run in terminal'))

    await waitFor(() => expect(drawerOpen()).toBe(true))
    await waitFor(() => expect(runsOf(COMMAND)).toEqual([['pty-1', `${COMMAND}\n`]]), { timeout: 4000 })
    // Long enough for a second registration or another retry to have sent it again.
    await act(() => new Promise((r) => setTimeout(r, 300)))
    expect(runsOf(COMMAND)).toHaveLength(1)
    expect(pty.created).toHaveLength(1)
    // The session starts where the gateway puts it: the drawer asks for no folder.
    expect(pty.created[0]).toEqual([])

    // With the drawer open, Run sends to its open session, and opens no other.
    fireEvent.click(screen.getByTitle('Run in terminal'))
    await waitFor(() => expect(runsOf(COMMAND)).toHaveLength(2))
    expect(runsOf(COMMAND).map(([id]) => id)).toEqual(['pty-1', 'pty-1'])
    expect(pty.created).toHaveLength(1)
  })
})

describe('the drawer in a thread', () => {
  it('⌘J and ⌘` show and hide it, and another thread keeps its sessions', async () => {
    await openChat(SESSION, /RUN THIS ONE/)
    fireEvent.keyDown(window, J)
    await waitFor(() => expect(drawerOpen()).toBe(true))
    await within(theDrawer()).findByRole('tab', { name: /^Session 1/ })
    fireEvent.keyDown(window, J)
    await waitFor(() => expect(drawerOpen()).toBe(false))
    fireEvent.keyDown(window, BACKQUOTE)
    await waitFor(() => expect(drawerOpen()).toBe(true))
    fireEvent.keyDown(window, BACKQUOTE)
    await waitFor(() => expect(drawerOpen()).toBe(false))

    // Another thread, with the drawer open: the same session, and no new one.
    fireEvent.keyDown(window, J)
    await waitFor(() => expect(drawerOpen()).toBe(true))
    act(() => { location.hash = `#/chat/${OTHER}` })
    await screen.findByText(/THE OTHER THREAD/)
    expect(drawerOpen()).toBe(true)
    expect(within(theDrawer()).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Session 1'])
    expect(pty.created).toHaveLength(1)
  })

  it('Open full opens the session the drawer shows, on the Terminal page', async () => {
    await openChat(SESSION, /RUN THIS ONE/)
    fireEvent.keyDown(window, J)
    await within(await waitFor(theDrawer)).findByRole('tab', { name: /^Session 1/ })
    fireEvent.click(within(theDrawer()).getByRole('button', { name: 'New session' }))
    await waitFor(() => expect(within(theDrawer()).getByRole('tab', { name: /^Session 2/ })).toHaveAttribute('aria-selected', 'true'))

    fireEvent.click(screen.getByRole('button', { name: 'Open full terminal' }))
    await waitFor(() => expect(drawerOpen()).toBe(false))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Open full terminal' })).toBeNull())
    await waitFor(() => expect(location.hash).toMatch(/^#\/terminal\b/))
    expect(location.hash).toBe('#/terminal?active=pty-2')
    const page = await screen.findByRole('tablist', { name: 'Terminal sessions' })
    await within(page).findByRole('tab', { name: /^Session 2/ })
    expect(within(page).getByRole('tab', { name: /^Session 2/ })).toHaveAttribute('aria-selected', 'true')
    expect(within(page).getByRole('tab', { name: /^Session 1/ })).toHaveAttribute('aria-selected', 'false')
  })
})

describe('the sidebar has no Terminal row', () => {
  /** The sidebar's rows, as the old full sidebar listed Terminal among them. */
  const railRows = () => within(document.querySelector('nav[data-tour="rail"]') as HTMLElement)
    .getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? '')

  it('the sidebar lists no Terminal, and the corner\'s toggle and ⌘K still reach it', async () => {
    await openChat(SESSION, /RUN THIS ONE/)
    expect(railRows()).toContain('Automations')
    expect(railRows()).not.toContain('Terminal')
    expect(screen.getByRole('button', { name: 'Open terminal' }), 'the corner\'s toggle').toBeInTheDocument()
    const row = rows.find((r) => r.id === 'go:terminal')
    expect(row?.label).toBe('Terminal')
    act(() => row!.run())
    await waitFor(() => expect(document.querySelector('main h1')?.textContent).toBe('Terminal'), { timeout: 10_000 })
    expect(location.hash).toBe('#/terminal')
  })

  it('a record the starter sidebar kept, with Terminal pinned, brings no row for it back', async () => {
    localStorage.setItem('nav-disclosure', JSON.stringify({ mode: 'starter', pinned: ['automations', 'terminal'] }))
    await openChat(SESSION, /RUN THIS ONE/)
    expect(railRows()).toContain('Automations')
    expect(railRows()).not.toContain('Terminal')
  })

  it('a copied #/terminal address opens the Terminal page', async () => {
    location.hash = '#/terminal'
    render(<StrictMode><ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider></StrictMode>)
    await waitFor(() => expect(document.querySelector('main h1')?.textContent).toBe('Terminal'), { timeout: 10_000 })
    expect(location.hash).toBe('#/terminal')
  })
})
