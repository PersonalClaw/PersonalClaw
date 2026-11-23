import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { runState } from '../../../test/runState'
import { SHARED_THREAD_NOTICE } from './SharedThreadNotice'

// ── A chat linked to a shared thread says, above its composer, that what you write stays here ────
//
// A chat linked to a channel thread used to post a turn you started from the dashboard into that
// thread ("From the dashboard: …"), with its progress and its answer. In a channel or a group,
// everyone there read it. Now such a turn stays in the dashboard where the thread is read by
// anyone else (`chat_runner._the_thread_told`), and the composer says so before you type. The
// gateway tells the page whether the thread is shared (`shared_thread` on the chat's detail).

const SESSION = 'chat-12-thread'

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
  key: SESSION, title: 'Offsite plan', run_state: runState({ session: SESSION }), queue: [],
  task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  messages: [
    { role: 'user', content: 'What is the plan for the offsite?', ts: '2026-11-22T09:00:00+00:00' },
    { role: 'assistant', content: 'The venue opens at nine.', ts: '2026-11-22T09:00:05+00:00' },
  ],
  ...over,
})

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 200 }))))
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
  await screen.findByText(/The venue opens at nine/)
  await act(async () => { await new Promise((r) => setTimeout(r, 60)) })
}

describe('a chat linked to a shared thread', () => {
  it('says above its composer that what you write here stays here', async () => {
    h.detail = chat({ shared_thread: true })
    await open()
    expect(screen.getByText(SHARED_THREAD_NOTICE)).toBeTruthy()
  })

  it('says nothing of it in a chat on no thread, or on the owner’s own DM', async () => {
    h.detail = chat({ shared_thread: false })
    await open()
    expect(screen.queryByText(SHARED_THREAD_NOTICE)).toBeNull()
  })
})
