import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── Find in conversation opens the long message it lands on ──────────────────────────────────────
//
// Measured before this was written: a word that sat only below a long message's fold (a message of
// over 1,500 characters or 16 lines, sent as text) was counted, and its turn was scrolled into view,
// but the word itself was clipped behind the fold, so nothing highlighted was on screen. Now the
// step opens the message it lands on, and only that one: a long message that holds no match stays
// folded. Driven through the real ChatPage, opened from a search result's `?find=` link.

const SESSION = 'chat-5-find'

const h = vi.hoisted(() => ({ chatSessionDetail: vi.fn() }))

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  return { api, errText: (e: unknown) => String((e as Error)?.message ?? e) }
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

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

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
  localStorage.clear()
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const lines = (n: number, last: string) => [...Array.from({ length: n - 1 }, (_, i) => `step ${i + 1} of the log`), last].join('\n')
const HOLDS_IT = lines(40, 'and the quokka is on the last line')
const HOLDS_NONE = lines(40, 'and nothing else is on the last line')

function open(find: string) {
  h.chatSessionDetail.mockResolvedValue({
    key: SESSION, title: 'Logs', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    messages: [
      { role: 'user', content: HOLDS_NONE, ts: 't-1' },
      { role: 'assistant', content: 'Read it.', ts: 't-2' },
      { role: 'user', content: HOLDS_IT, ts: 't-3' },
      { role: 'assistant', content: 'Read that too.', ts: 't-4' },
    ],
    run_state: runState({ session: SESSION }), stream_seq: 0,
  })
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{ find }} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

/** The long message that holds *text*: its bubble, and its fold's toggle. */
const bubbleOf = (text: string) => {
  const toggle = screen.getAllByRole('button', { name: /Show full message|Show less/ })
    .find((b) => b.parentElement?.textContent?.includes(text))
  expect(toggle, `no long message holds "${text}"`).toBeTruthy()
  return { bubble: toggle!.parentElement as HTMLElement, toggle: toggle! }
}

describe('a search result opened at a word below a long message’s fold', () => {
  it('🔴 opens that message, and leaves a long message without the word folded', async () => {
    open('quokka')
    await waitFor(() => expect(screen.getByRole('search')).toBeTruthy())

    await waitFor(() => expect(bubbleOf('the quokka is on the last line').toggle.getAttribute('aria-expanded')).toBe('true'))
    expect(bubbleOf('the quokka is on the last line').bubble.querySelector('[data-user-message-folded]')).toBeNull()

    const other = bubbleOf('nothing else is on the last line')
    expect(other.toggle.getAttribute('aria-expanded')).toBe('false')
    expect(other.bubble.querySelector('[data-user-message-folded]')).not.toBeNull()
  })
})
