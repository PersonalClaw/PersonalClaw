import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { runState } from '../../../test/runState'

// ── An untitled chat's header names it by its first message ──────────────────────────────────────
//
// Measured before this was written: a chat with no title yet (its title is its key until auto-title
// runs after its first turn, and a first turn that waits on a question or an approval keeps it so)
// read "Untitled chat" in its header, though its first message was on screen below. The chat
// history list read from disk already named it by that message. The header now does too, until a
// title arrives; a title the chat has is shown as before.

const SESSION = 'chat-12-1791067183'

const h = vi.hoisted(() => ({ detail: {} as Record<string, unknown> }))

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy({} as Record<string, unknown>, {
    get(_t, p) {
      if (typeof p !== 'string') return undefined
      return (...args: unknown[]) => (p in answers ? answers[p](...args) : Promise.resolve([]))
    },
  })
  return { ...real, api }
})

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

let ChatPage: typeof import('../../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../../app/appearance')['AppearanceProvider']

const chat = (over: Record<string, unknown> = {}) => ({
  key: SESSION, title: SESSION, run_state: runState({ session: SESSION }), queue: [],
  task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  messages: [{ role: 'user', content: 'Which of the parser tests fail on main?', ts: '2026-11-21T09:00:00+00:00' }],
  ...over,
})

beforeEach(async () => {
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
  sessionStorage.clear()
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function open() {
  render(<AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
  await screen.findAllByText('Which of the parser tests fail on main?')
  await act(async () => { await new Promise((r) => setTimeout(r, 40)) })
  return screen.getByTitle('Rename chat')
}

describe('the chat header', () => {
  it('🔴 names an untitled chat by its first message', async () => {
    h.detail = chat()
    const heading = await open()
    expect(heading.textContent).toContain('Which of the parser tests fail on main?')
    expect(heading.textContent).not.toContain('Untitled chat')
  })

  it('shows a title the chat has', async () => {
    h.detail = chat({ title: 'Parser tests' })
    expect((await open()).textContent).toContain('Parser tests')
  })
})
