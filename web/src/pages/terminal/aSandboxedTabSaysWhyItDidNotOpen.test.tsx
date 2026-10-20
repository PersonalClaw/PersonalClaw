/** A sandboxed terminal tab that cannot open says why, and a restored one keeps its sandbox.
 *
 *  A terminal opened in a sandbox tier used to reopen on this computer's own shell after a
 *  gateway restart. The server now refuses instead, with a sentence naming the tier and the
 *  reason, and the pane has to show that sentence: it said "Session error" / "The shell session
 *  has ended." whatever the server sent. A Restart whose create is refused reopens the old id,
 *  which carries the tier, so it meets the same refusal rather than a shell on this computer. And
 *  the page's restore dropped each session's tier, so Restart on a restored sandboxed tab asked
 *  for a shell with no sandbox.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { TermTab } from './TerminalPage'

const REFUSAL =
  'This terminal is set to run in the lima sandbox, which is not installed or is turned off. It '
  + 'was not opened, and it never falls back to a shell on this computer.'

const createTerminal = vi.fn()
const terminalSessions = vi.fn()
const seenTabs: TermTab[] = []

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
    terminalSessions: () => terminalSessions(),
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
  onopen?: () => void
  onmessage?: (e: { data: unknown }) => void
  onclose?: () => void
  constructor(public url: string) { FakeSocket.opened.push(this) }
  send() {}
  close() { this.onclose?.() }
}

beforeEach(() => {
  FakeSocket.opened = []
  seenTabs.length = 0
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); vi.resetModules() })

async function openView(tab: TermTab) {
  const { TerminalView } = await import('./TerminalView')
  render(<TerminalView tab={tab} onExited={() => {}} onClose={() => {}} />)
  await waitFor(() => expect(FakeSocket.opened).toHaveLength(1))
  return FakeSocket.opened[0]
}

describe('a sandboxed tab the server will not open', () => {
  const tab: TermTab = { id: '0123456789ab@lima', label: 'Session 1', sandbox: 'lima' }

  it('🔑 shows the server’s sentence, and does not keep reconnecting', async () => {
    const socket = await openView(tab)
    act(() => {
      socket.onmessage?.({ data: JSON.stringify({ type: 'error', message: REFUSAL }) })
      socket.onclose?.()
    })

    expect(screen.getByText('The terminal did not open')).toBeTruthy()
    expect(screen.getByText(REFUSAL)).toBeTruthy()
    await new Promise((r) => setTimeout(r, 1100))
    expect(FakeSocket.opened, 'a refused session is not retried').toHaveLength(1)
  })

  it('a Restart asks for the same sandbox, and a refused one reopens the same tier-carrying id', async () => {
    createTerminal.mockRejectedValue(new Error('The lima sandbox is not installed or is turned off.'))
    const socket = await openView(tab)
    act(() => {
      socket.onmessage?.({ data: JSON.stringify({ type: 'error', message: REFUSAL }) })
      socket.onclose?.()
    })

    await userEvent.click(screen.getByRole('button', { name: /Restart/ }))

    expect(createTerminal).toHaveBeenCalledWith(undefined, 'lima')
    await waitFor(() => expect(FakeSocket.opened).toHaveLength(2))
    expect(FakeSocket.opened[1].url).toMatch(/\/api\/ws\/terminal\/0123456789ab%40lima$/)
    act(() => {
      FakeSocket.opened[1].onmessage?.({ data: JSON.stringify({ type: 'error', message: REFUSAL }) })
      FakeSocket.opened[1].onclose?.()
    })
    expect(screen.getByText(REFUSAL)).toBeTruthy()
  })
})

describe('the page’s restore', () => {
  it('keeps each session’s sandbox on its tab', async () => {
    vi.doMock('./TerminalView', () => ({
      TerminalView: ({ tab }: { tab: TermTab }) => { seenTabs.push(tab); return null },
    }))
    terminalSessions.mockResolvedValue({
      enabled: true,
      persist_available: false,
      sessions: [{ session_id: '0123456789ab@lima', alive: true, cwd: '/w', shell: '/bin/sh', sandbox: 'lima' }],
    })
    const { TerminalPage } = await import('./TerminalPage')
    render(<TerminalPage query={{}} setQuery={() => {}} />)

    await waitFor(() => expect(seenTabs.length).toBeGreaterThan(0))
    expect(seenTabs.at(-1)?.sandbox).toBe('lima')
    vi.doUnmock('./TerminalView')
  })
})
