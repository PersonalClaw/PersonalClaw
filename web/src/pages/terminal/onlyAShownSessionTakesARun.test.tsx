/** Only a session the owner can see takes "Run in terminal", and a restarted one still does.
 *
 *  The bridge's target is the session the drawer or the Terminal page shows, as each says when it
 *  shows one and when it no longer does. Two holes were left after the target stopped being the
 *  newest session to register:
 *
 *  - A target that closed fell back to the session registered last, which may be one nobody can
 *    see: on a split page with the right pane worked in, a drawer tab opened in the background,
 *    then the right pane closed, the next Run went into that hidden tab. A pane the page stops
 *    showing (Close split) stayed the target while hidden.
 *  - A Restart gives its PTY a new id, which no surface shows, so the restarted session took no Run
 *    while it was the one on screen.
 *
 *  Now a session is the target only while a surface shows it, the one shown before it takes over
 *  when it is hidden, a closed target leaves none (so the shell opens the drawer), and a restarted
 *  view answers to its tab's id too.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  hasActiveTerminal,
  hideTerminal,
  registerTerminal,
  runInTerminal,
  showTerminal,
  unregisterTerminal,
} from './terminalBridge'
import type { TermTab } from './TerminalPage'

const IDS = ['left', 'right', 'drawer-tab', 'pty-a', 'pty-b']
afterEach(() => { for (const id of IDS) { hideTerminal?.(id); unregisterTerminal(id) } })

/** A session registered with the bridge, recording what it was sent. */
function session(id: string, got: string[]) {
  registerTerminal(id, (text) => { got.push(`${id}:${text.trim()}`); return true })
}

describe('the target is a shown session, never one that only registered', () => {
  it('🔴 a session that registers with nothing shown takes nothing', () => {
    const got: string[] = []
    session('drawer-tab', got)
    expect(hasActiveTerminal()).toBe(false)
    expect(runInTerminal('pwd')).toBe(false)
    expect(got).toEqual([])
  })

  it('🔴 a target that closes leaves no target, never a session registered in the background', () => {
    const got: string[] = []
    showTerminal('left'); session('left', got)
    showTerminal('right'); session('right', got)
    session('drawer-tab', got)

    unregisterTerminal('right')

    expect(hasActiveTerminal(), 'the shell hands the command to the drawer instead').toBe(false)
    expect(runInTerminal('pwd')).toBe(false)
    expect(got).toEqual([])
  })

  it('🔴 a target its surface hides gives way to the one shown before it, or to none', () => {
    const got: string[] = []
    showTerminal('left'); session('left', got)
    showTerminal('right'); session('right', got)
    session('drawer-tab', got)

    hideTerminal('right')
    expect(runInTerminal('pwd')).toBe(true)
    hideTerminal('left')
    expect(runInTerminal('ls')).toBe(false)
    expect(got).toEqual(['left:pwd'])
  })

  it('showing one again makes it the target again', () => {
    const got: string[] = []
    showTerminal('left'); session('left', got)
    showTerminal('right'); session('right', got)
    showTerminal('left')
    hideTerminal('left')
    expect(runInTerminal('pwd')).toBe(true)
    expect(got).toEqual(['right:pwd'])
  })
})

// ── A restarted session is still its tab's target ───────────────────────────────────────────────

const createTerminal = vi.fn()

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = 80
    rows = 24
    options = {}
    loadAddon() {}
    open() {}
    attachCustomKeyEventHandler() {}
    onData() { return { dispose() {} } }
    write() {}
    clear() {}
    focus() {}
    dispose() {}
  },
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }))
vi.mock('@xterm/addon-web-links', () => ({ WebLinksAddon: class {} }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    createTerminal: (...args: unknown[]) => createTerminal(...args),
    terminalSessions: () => Promise.resolve({ enabled: true, persist_available: false, sessions: [] }),
    personalclawConfig: () => Promise.resolve({}),
    sandboxProviders: () => Promise.resolve({ providers: [] }),
    deleteTerminal: () => Promise.resolve({}),
  },
}))

class FakeSocket {
  static OPEN = 1
  static opened: FakeSocket[] = []
  readyState = 0
  binaryType = ''
  sent: string[] = []
  onopen?: () => void
  onmessage?: (e: { data: unknown }) => void
  onclose?: () => void
  constructor(public url: string) { FakeSocket.opened.push(this) }
  send(data: unknown) { if (data instanceof Uint8Array) this.sent.push(new TextDecoder().decode(data)) }
  close() { this.readyState = 3 }
  open() { this.readyState = 1; this.onopen?.() }
}

beforeEach(() => {
  FakeSocket.opened = []
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

describe('a restarted session', () => {
  it('🔴 still takes a Run while its tab is the one shown', async () => {
    createTerminal.mockResolvedValue({ session_id: 'pty-b', cwd: '/w', shell: '/bin/zsh', alive: true })
    const tab: TermTab = { id: 'pty-a', label: 'Session 1' }
    const { TerminalView } = await import('./TerminalView')
    const background: string[] = []
    session('drawer-tab', background)
    render(<TerminalView tab={tab} onExited={() => {}} onClose={() => {}} />)
    showTerminal('pty-a')
    await waitFor(() => expect(FakeSocket.opened).toHaveLength(1))
    act(() => FakeSocket.opened[0].open())
    expect(runInTerminal('pwd')).toBe(true)

    act(() => { FakeSocket.opened[0].onmessage?.({ data: JSON.stringify({ type: 'exited', code: 0 }) }) })
    expect(runInTerminal('ls'), 'an exited session takes nothing').toBe(false)
    await userEvent.click(screen.getByRole('button', { name: /Restart/ }))
    await waitFor(() => expect(FakeSocket.opened).toHaveLength(2))
    act(() => FakeSocket.opened[1].open())

    expect(runInTerminal('ls')).toBe(true)
    expect(FakeSocket.opened[1].sent).toEqual(['ls\n'])
    expect(FakeSocket.opened[1].url).toMatch(/\/api\/ws\/terminal\/pty-b$/)
    expect(background, 'a session registered in the background takes nothing').toEqual([])
  })
})
