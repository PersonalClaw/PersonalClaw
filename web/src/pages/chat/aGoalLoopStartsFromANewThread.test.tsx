import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState } from '../../test/runState'

// ── A goal loop starts from a new thread's composer (#3824) ────────────────────────────────────────
//
// A new chat's + menu offers "Start a goal loop": the draft becomes the loop's task, and the loop
// composer (`#/loop`) drafts and plans it as it always has, through the loop-start route and its
// guards. A started chat is a thread already, so its menu does not offer it. Driven through the real
// ChatPage.

const h = vi.hoisted(() => ({ chatSessionDetail: vi.fn() }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    useCaseSettings: () => Promise.resolve({ value: {}, revision: 'r1' }),
    personalclawConfig: () => Promise.resolve({ voice: {} }),
    agents: () => Promise.resolve({ agents: [] }),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, { get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve([])) } })
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

const SESSION = 'chat-11-goal'

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('requestAnimationFrame', () => 0)
  vi.stubGlobal('cancelAnimationFrame', () => {})
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
  h.chatSessionDetail.mockReset().mockResolvedValue({
    key: SESSION, title: SESSION, run_state: runState(), queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    messages: [{ role: 'user', content: 'hi', ts: 't1' }, { role: 'assistant', content: 'Hello again.', ts: 't2' }],
  })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})
afterEach(() => { vi.unstubAllGlobals() })

function open(sub: string, query: Record<string, string> = {}) {
  const navigate = vi.fn()
  render(
    <AppearanceProvider>
      <ChatPage sub={sub} navigate={navigate} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  return navigate
}

describe('a goal loop starts from a new thread\'s composer', () => {
  it('the + menu hands the draft to the loop composer as a goal loop', async () => {
    const user = userEvent.setup()
    const navigate = open('new', { seed: 'Find a seed supplier that ships north' })
    await user.click(await screen.findByRole('button', { name: 'Add to message' }))
    await user.click(await screen.findByRole('button', { name: /Start a goal loop/ }))
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(`loop?${new URLSearchParams({ kind: 'goal', task: 'Find a seed supplier that ships north' })}`))
  })

  it('a started chat\'s + menu does not offer it: it is a thread already', async () => {
    const user = userEvent.setup()
    open(SESSION)
    await waitFor(() => expect(h.chatSessionDetail).toHaveBeenCalled())
    await user.click(await screen.findByRole('button', { name: 'Add to message' }))
    // The chat has started: the menu offers what a started chat's does.
    await screen.findByRole('button', { name: /Plan this first/ })
    expect(screen.queryByRole('button', { name: /Start a goal loop/ })).toBeNull()
  })
})
