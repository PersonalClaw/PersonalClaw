import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── With no model chosen for the chores, each chat surface they fill says why ──────────────────
//
// The suggestions on a new chat, the follow-ups under a reply and a chat's title come from
// PersonalClaw's own background calls, which run on a model and never on an agent CLI. With no
// model chosen for them nothing is asked, and the gateway says so in one sentence per surface
// (`needs_model`). Each surface shows it where its answer would be, and the suggestions and
// follow-ups show it as the way to Settings → Models.

const SUGGESTIONS_NEED = 'Suggestions need a model: choose one in Settings → Models.'
const FOLLOWUPS_NEED = 'Follow-ups need a model: choose one in Settings → Models.'
const TITLES_NEED = 'Titles and tags need a model: choose one in Settings → Models.'

const h = vi.hoisted(() => ({
  detail: {
    key: 'chat-7-x', title: 'Reading a file', running: false, queue: [] as unknown[], task_mode: 'agent',
    approval: 'normal', memory_mode: 'persistent',
    messages: [
      { role: 'user', content: 'How do I read a file in Python?', ts: '2026-10-02T09:00:00+00:00' },
      { role: 'assistant', content: 'Use open() with a context manager.', ts: '2026-10-02T09:00:05+00:00' },
    ] as unknown[],
  },
  chatSessionDetail: vi.fn(),
  suggestions: vi.fn(),
  generateTitle: vi.fn(),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    suggestions: h.suggestions,
    generateTitle: h.generateTitle,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  // The page reads ~40 endpoints; anything not named above resolves to a shape-agnostic value.
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  return { ...actual, api }
})

class FakeSocket {
  static last: FakeSocket | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { FakeSocket.last = this; setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

function pushFrame(type: string, data: Record<string, unknown>) {
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data }) }) })
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

function suggestionsRead(needs_model: string) {
  return { suggestions: ['Show health-check status', 'Break a goal into tasks'], generated_at: 1, stale: false, refreshing: false, needs_model }
}

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
  FakeSocket.last = null
  h.chatSessionDetail.mockReset().mockResolvedValue({ ...h.detail })
  h.suggestions.mockReset().mockResolvedValue(suggestionsRead(SUGGESTIONS_NEED))
  h.generateTitle.mockReset().mockResolvedValue({ ok: false, needs_model: TITLES_NEED })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

function open(sub: string) {
  return render(
    <AppearanceProvider>
      <ChatPage sub={sub} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

describe('a new chat with no model chosen for the chores', () => {
  it('keeps the standard suggestions and says why, as the way to Settings → Models', async () => {
    open('')
    expect(await screen.findByRole('button', { name: 'Break a goal into tasks' })).toBeTruthy()
    const why = await screen.findByRole('link', { name: SUGGESTIONS_NEED })
    expect(why.getAttribute('href')).toBe('#/settings/models')
  })

  it('says nothing about a model once one is chosen', async () => {
    h.suggestions.mockResolvedValue(suggestionsRead(''))
    open('')
    expect(await screen.findByRole('button', { name: 'Break a goal into tasks' })).toBeTruthy()
    expect(screen.queryByRole('link', { name: /need a model/ })).toBeNull()
  })
})

describe('a reply with no model chosen for its follow-ups', () => {
  it('shows why where the chips would be, and the live region says it', async () => {
    open('chat-7-x')
    await screen.findByText(/Use open\(\) with a context manager/)
    pushFrame('chat_followups', { session: 'chat-7-x', items: [], needs_model: FOLLOWUPS_NEED })
    const why = await screen.findByRole('link', { name: FOLLOWUPS_NEED })
    expect(why.getAttribute('href')).toBe('#/settings/models')
    expect(screen.queryByRole('group', { name: 'Suggested follow-ups' })).toBeNull()
    expect(screen.getAllByRole('status').some((s) => s.textContent === FOLLOWUPS_NEED)).toBe(true)
  })

  it('shows chips and no such line when they came', async () => {
    open('chat-7-x')
    await screen.findByText(/Use open\(\) with a context manager/)
    pushFrame('chat_followups', { session: 'chat-7-x', items: ['Show a code example'] })
    expect(await screen.findByRole('button', { name: 'Show a code example' })).toBeTruthy()
    expect(screen.queryByRole('link', { name: /need a model/ })).toBeNull()
  })
})

describe('an untitled chat with no model chosen for its title', () => {
  it('says why under the title, as the way to Settings → Models', async () => {
    h.chatSessionDetail.mockResolvedValue({ ...h.detail, title: '', title_needs_model: TITLES_NEED })
    open('chat-7-x')
    expect(await screen.findByText('Untitled chat')).toBeTruthy()
    const why = await screen.findByRole('link', { name: TITLES_NEED })
    expect(why.getAttribute('href')).toBe('#/settings/models')
  })

  it('says nothing once the chat has a title', async () => {
    open('chat-7-x')
    expect(await screen.findByText('Reading a file')).toBeTruthy()
    expect(screen.queryByRole('link', { name: TITLES_NEED })).toBeNull()
  })
})

describe('Regenerate title with no model chosen', () => {
  it('keeps the chat’s name and says what titles are waiting on', async () => {
    const said: string[] = []
    const listen = (e: Event) => said.push(String((e as CustomEvent).detail?.message ?? ''))
    window.addEventListener('ne:toast', listen)
    try {
      const user = userEvent.setup()
      open('chat-7-x')
      await screen.findByText(/Use open\(\) with a context manager/)
      await user.click(screen.getByRole('button', { name: 'Regenerate title' }))
      await vi.waitFor(() => expect(said).toContain(TITLES_NEED))
      expect(h.generateTitle).toHaveBeenCalledWith('chat-7-x')
      expect(screen.getByText('Reading a file')).toBeTruthy()
    } finally {
      window.removeEventListener('ne:toast', listen)
    }
  })
})
