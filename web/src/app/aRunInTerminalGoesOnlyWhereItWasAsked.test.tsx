import { StrictMode } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { runState } from '../test/runState'

// ── Run in terminal goes to the session it was asked for, and nowhere else ──────────────────────
//
// Reproduced through the real shell before this was written:
//
// - A refused open kept the command waiting. Run on a chat's code block with no terminal live
//   queued the command and opened the drawer, and when the drawer's session was refused (the
//   gateway's session limit) nothing cleared it: the next terminal to register anywhere was sent it,
//   the Terminal page's restored session among them, unasked.
// - The target was the newest session. Every mounted session registers with the bridge, and the
//   newest became the target, so with two drawer tabs Run went to the hidden second one, and on a
//   split Terminal page to the pane opened last.
//
// The PTY view is a stand-in that registers with the real bridge (`pages/terminal/terminalBridge.ts`)
// and refuses a send until its socket would be open, as `TerminalView` does: what is under test is
// the shell's wiring, not xterm. jsdom reports no Mac, so the keys read as Ctrl.

/** What the PTYs were sent, by session; each create's arguments; the sessions the gateway holds. */
const pty = vi.hoisted(() => ({
  sent: [] as Array<[string, string]>,
  created: [] as unknown[][],
  live: [] as Array<{ session_id: string; cwd: string; shell: string; alive: boolean }>,
  /** Refuse every create, as the gateway does at its session limit. */
  refuse: false,
  /** How long a session's socket takes to open, in ms. */
  openAfter: 40,
}))

vi.mock('../pages/terminal/TerminalView', async () => {
  const { useEffect } = await import('react')
  const { registerTerminal, unregisterTerminal } = await import('../pages/terminal/terminalBridge')
  return {
    TerminalView: ({ tab }: { tab: { id: string; label: string } }) => {
      useEffect(() => {
        let open = false
        const opening = setTimeout(() => { open = true }, pty.openAfter)
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

vi.mock('../lib/useChatSocket', () => import('../test/quietSocket'))

// ⌘K's palette is not under test here.
vi.mock('./CommandPalette', async (orig) => {
  const real = await orig<typeof import('./CommandPalette')>()
  return { ...real, CommandPalette: () => null }
})

vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

const SESSION = 'chat-7-drawer'
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
      if (pty.refuse) {
        return Promise.reject(new real.ApiError('You have reached the limit of 1 terminal sessions. Close one first.', 409, 'terminal_session_limit'))
      }
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
const { registerTerminal, showTerminal, unregisterTerminal } = await import('../pages/terminal/terminalBridge')



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
  pty.refuse = false
  pty.openAfter = 40
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

const run = () => fireEvent.click(screen.getByRole('button', { name: 'Run', description: 'Run in terminal' }))
/** Run in terminal as the shell hears it from anywhere: a code block's Run, a provider's login. */
const askToRun = (command: string) => act(() => { window.dispatchEvent(new CustomEvent('ne:run-in-terminal', { detail: { command } })) })
const settle = () => act(() => new Promise((r) => setTimeout(r, 300)))

describe('a refused open runs nothing later', () => {
  it('🔴 drops the command, saying so, and a session that registers later is sent nothing', async () => {
    const toasts: string[] = []
    const onToast = (e: Event) => toasts.push(String((e as CustomEvent).detail?.message ?? ''))
    window.addEventListener('ne:toast', onToast)
    try {
      pty.refuse = true
      await openChat(SESSION, /RUN THIS ONE/)
      run()
      await screen.findByText('Couldn’t open a session')
      await waitFor(() => expect(toasts.some((t) => /Couldn't run the command/.test(t))).toBe(true))

      // A session the Terminal page restores registers now: it is not the one Run was asked for.
      pty.refuse = false
      pty.live.push({ session_id: 'pty-restored', cwd: '/home/ada', shell: '/bin/zsh', alive: true })
      fireEvent.click(screen.getByRole('button', { name: 'Hide terminal' }))
      act(() => { location.hash = '#/terminal' })
      await screen.findByRole('group', { name: /PTY/ })
      await settle()
      expect(runsOf(COMMAND)).toEqual([])
    } finally {
      window.removeEventListener('ne:toast', onToast)
    }
  })
})

describe('Run goes to the session shown', () => {
  it('🔴 with two drawer tabs and the first shown, Run sends to the first', async () => {
    await openChat(SESSION, /RUN THIS ONE/)
    run()
    await waitFor(() => expect(runsOf(COMMAND)).toEqual([['pty-1', `${COMMAND}\n`]]), { timeout: 4000 })
    fireEvent.click(screen.getByRole('button', { name: 'New session' }))
    await screen.findByRole('tab', { name: /Session 2/ })
    fireEvent.click(screen.getByRole('tab', { name: /Session 1/ }))
    await settle()

    run()
    await waitFor(() => expect(runsOf(COMMAND)).toHaveLength(2))
    expect(runsOf(COMMAND).map(([id]) => id)).toEqual(['pty-1', 'pty-1'])
  })

  it('🔴 on a split Terminal page, Run sends to the pane shown on the left until the right one is focused', async () => {
    pty.live.push(
      { session_id: 'pty-left', cwd: '/home/ada', shell: '/bin/zsh', alive: true },
      { session_id: 'pty-right', cwd: '/home/ada', shell: '/bin/zsh', alive: true },
    )
    location.hash = '#/terminal?active=pty-left&split=pty-right'
    render(<StrictMode><ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider></StrictMode>)
    const panes = await screen.findAllByRole('group', { name: /PTY/ })
    expect(panes).toHaveLength(2)
    await settle()

    askToRun('pwd')
    await waitFor(() => expect(runsOf('pwd')).toEqual([['pty-left', 'pwd\n']]))

    const right = screen.getAllByRole('group', { name: /PTY/ }).find((p) => p.closest('[style*="order: 2"]'))
    expect(right).toBeTruthy()
    fireEvent.pointerDown(right!)
    askToRun('pwd')
    await waitFor(() => expect(runsOf('pwd')).toHaveLength(2))
    expect(runsOf('pwd').map(([id]) => id)).toEqual(['pty-left', 'pty-right'])
  })
})

describe('a session nobody can see takes nothing', () => {
  /** A split Terminal page with a third session in the background, the right pane worked in. */
  async function workInTheRightPane() {
    pty.live.push(
      { session_id: 'pty-left', cwd: '/home/ada', shell: '/bin/zsh', alive: true },
      { session_id: 'pty-right', cwd: '/home/ada', shell: '/bin/zsh', alive: true },
      { session_id: 'pty-background', cwd: '/home/ada', shell: '/bin/zsh', alive: true },
    )
    location.hash = '#/terminal?active=pty-left&split=pty-right'
    render(<StrictMode><ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider></StrictMode>)
    // The background session is mounted too, hidden (`display: none`), as the page keeps every one.
    await waitFor(() => expect(screen.getAllByRole('group', { name: /PTY/, hidden: true })).toHaveLength(3))
    await settle()
    const right = screen.getAllByRole('group', { name: /PTY/ }).find((p) => p.closest('[style*="order: 2"]'))
    fireEvent.pointerDown(right!)
    askToRun('pwd')
    await waitFor(() => expect(runsOf('pwd')).toEqual([['pty-right', 'pwd\n']]))
  }

  it('🔴 Close split after working in the right pane sends Run to the left one, not the hidden right one', async () => {
    await workInTheRightPane()
    fireEvent.click(screen.getByRole('button', { name: 'Close split' }))
    await settle()

    askToRun('pwd')
    await waitFor(() => expect(runsOf('pwd')).toHaveLength(2))
    expect(runsOf('pwd').map(([id]) => id)).toEqual(['pty-right', 'pty-left'])
  })

  it("🔴 closing the right pane's session after working in it sends Run to the left one, not a background session", async () => {
    await workInTheRightPane()
    fireEvent.click(screen.getByRole('button', { name: 'Close Session 2' }))
    await waitFor(() => expect(screen.getAllByRole('group', { name: /PTY/, hidden: true })).toHaveLength(2))
    await settle()

    askToRun('pwd')
    await waitFor(() => expect(runsOf('pwd')).toHaveLength(2))
    expect(runsOf('pwd').map(([id]) => id)).toEqual(['pty-right', 'pty-left'])
  })

  it('🔴 a session that registers where nothing shows it takes nothing: Run opens the drawer and runs there', async () => {
    const stray: string[] = []
    registerTerminal('pty-stray', (text) => { stray.push(text); return true })
    try {
      await openChat(SESSION, /RUN THIS ONE/)
      run()
      await waitFor(() => expect(runsOf(COMMAND)).toEqual([['pty-1', `${COMMAND}\n`]]), { timeout: 4000 })
      expect(stray).toEqual([])
    } finally {
      unregisterTerminal('pty-stray')
    }
  })

  // Last in the file: the session it shows is the bridge's for the rest of the file.
  it("the drawer's command goes to the session it opened, though another is shown while it opens", async () => {
    pty.openAfter = 400
    const other: string[] = []
    await openChat(SESSION, /RUN THIS ONE/)
    run()
    await screen.findByRole('group', { name: /PTY/ })
    act(() => {
      showTerminal('pty-other')
      registerTerminal('pty-other', (text) => { other.push(text); return true })
    })
    try {
      await waitFor(() => expect(runsOf(COMMAND)).toEqual([['pty-1', `${COMMAND}\n`]]), { timeout: 4000 })
      expect(other).toEqual([])
    } finally {
      unregisterTerminal('pty-other')
    }
  })
})
