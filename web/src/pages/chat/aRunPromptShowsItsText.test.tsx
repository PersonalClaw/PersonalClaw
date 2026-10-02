import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { hydrateTurns, ranPromptOf, type HistMsg } from './chatTypes'
import { MessageUser } from '../../ui/chat/MessageUser'

// ── A message that ran a saved prompt shows the prompt's text, folded and labelled as the prompt's
//
// She types `@handoff` and the agent is sent the prompt's whole text in its place. The chat showed
// only "@handoff", so what the agent actually received appeared nowhere in the transcript. Her
// bubble still shows what she typed — those are her words — and under it, folded until she opens
// it, the text the agent was sent, labelled as the prompt's rather than hers. Live (the expansion
// announces itself as `activity_event {kind: "prompt"}`) and after a reload (`meta.ran_prompt`).

const TEXT = 'Execute the following instructions:\n\nWrite a handoff note for whoever is on call next.'
const RAN = { name: 'handoff', text: TEXT }

describe('the prompt a message ran, on reload', () => {
  it('is carried onto her turn, and her words stay what she typed', () => {
    const [turn] = hydrateTurns([
      { role: 'user', content: '@handoff', ts: '2026-10-02T02:25:00Z', meta: { ran_prompt: RAN } },
    ])
    expect(turn.ranPrompt).toEqual(RAN)
    expect(turn.segments).toEqual([{ kind: 'text', text: '@handoff' }])
  })

  it('is absent from a message that ran none, and from a record of the wrong shape', () => {
    const msg = (meta?: HistMsg['meta']): HistMsg => ({ role: 'user', content: 'hello', ...(meta ? { meta } : {}) })
    expect(hydrateTurns([msg()])[0].ranPrompt).toBeUndefined()
    expect(hydrateTurns([msg({ ran_prompt: { name: 'handoff' } })])[0].ranPrompt).toBeUndefined()
    expect(ranPromptOf({ name: 7, text: TEXT })).toBeUndefined()
    expect(ranPromptOf('handoff')).toBeUndefined()
  })
})

describe('her bubble', () => {
  it('folds the prompt text under what she typed, and labels it as the prompt’s', async () => {
    const user = userEvent.setup()
    render(<MessageUser ranPrompt={RAN}>{'@handoff'}</MessageUser>)

    const toggle = screen.getByRole('button', { name: /Ran the prompt @handoff/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText(/Write a handoff note/)).toBeNull()
    expect(screen.getByText('@handoff')).toBeTruthy()

    await user.click(toggle)
    const open = screen.getByRole('button', { name: /Text of the prompt @handoff, as the agent received it/ })
    expect(open.getAttribute('aria-expanded')).toBe('true')
    const body = document.getElementById(open.getAttribute('aria-controls')!)!
    expect(body.textContent).toContain('Write a handoff note for whoever is on call next.')
  })

  it('shows nothing extra for a message that ran no prompt', () => {
    render(<MessageUser>{'just a question'}</MessageUser>)
    expect(screen.queryByRole('button', { name: /prompt/i })).toBeNull()
  })
})

// ── The page: reopened, and live ──────────────────────────────────────────────────────────────

const h = vi.hoisted(() => ({ chatSessionDetail: vi.fn() }))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
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

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  Element.prototype.scrollIntoView = vi.fn() as unknown as Element['scrollIntoView']
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
  FakeSocket.last = null
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

function openChat(messages: unknown[]) {
  h.chatSessionDetail.mockReset().mockResolvedValue({
    key: 'chat-4-x', title: 'Handoff', messages, running: false, queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  })
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-4-x" navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

describe('the chat', () => {
  it('reopened, shows under her message the prompt it ran, folded', async () => {
    openChat([
      { role: 'user', content: '@handoff', ts: '2026-10-02T02:25:00Z', meta: { ran_prompt: RAN } },
      { role: 'assistant', content: 'Here is the handoff.', ts: '2026-10-02T02:25:09Z' },
    ])
    const toggle = await screen.findByRole('button', { name: /Ran the prompt @handoff/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText(/whoever is on call next/)).toBeNull()
  })

  it('shows it as soon as the prompt expands, before the reply', async () => {
    openChat([{ role: 'user', content: '@handoff', ts: '2026-10-02T02:25:00Z' }])
    await waitFor(() => expect(screen.getByText('@handoff')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /Ran the prompt/ })).toBeNull()

    act(() => {
      FakeSocket.last?.onmessage?.({
        data: JSON.stringify({ type: 'activity_event', data: { session: 'chat-4-x', kind: 'prompt', prompt: RAN } }),
      })
    })
    expect(await screen.findByRole('button', { name: /Ran the prompt @handoff/ })).toBeTruthy()
  })

  it('a page that missed the announcement shows it once the turn ends', async () => {
    openChat([{ role: 'user', content: '@handoff', ts: '2026-10-02T02:25:00Z' }])
    await waitFor(() => expect(screen.getByText('@handoff')).toBeTruthy())
    h.chatSessionDetail.mockResolvedValue({
      key: 'chat-4-x', title: 'Handoff', running: false, queue: [],
      task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
      messages: [
        { role: 'user', content: '@handoff', ts: '2026-10-02T02:25:00Z', meta: { ran_prompt: RAN } },
        { role: 'assistant', content: 'Here is the handoff.', ts: '2026-10-02T02:25:09Z' },
      ],
    })

    act(() => {
      FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type: 'chat_done', data: { session: 'chat-4-x' } }) })
    })
    expect(await screen.findByRole('button', { name: /Ran the prompt @handoff/ })).toBeTruthy()
  })

  it('takes no prompt from another chat’s announcement', async () => {
    openChat([{ role: 'user', content: '@handoff', ts: '2026-10-02T02:25:00Z' }])
    await waitFor(() => expect(screen.getByText('@handoff')).toBeTruthy())
    act(() => {
      FakeSocket.last?.onmessage?.({
        data: JSON.stringify({ type: 'activity_event', data: { session: 'chat-9-other', kind: 'prompt', prompt: RAN } }),
      })
    })
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(screen.queryByRole('button', { name: /Ran the prompt/ })).toBeNull()
  })
})
