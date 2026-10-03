import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── An agent's question reaches her in the chat that asked it, and her answer goes back ──────────
//
// The gateway broadcast a `question_card` frame for an agent's question and nothing in the web app
// read it: she saw the tool's pill and nothing to answer, while the agent waited. Now the frame is a
// card in the turn with the question, its options and a box for her own words; Send posts her answer
// to the question's route; `question_resolved` settles the card; and a reload rebuilds the card she
// saw from the asking call's row, or, while it still waits, from the session's `pending_questions`.

const SESSION = 'chat-1-service'

const h = vi.hoisted(() => ({
  detail: null as unknown,
  chatSessionDetail: vi.fn(),
  answerChatQuestion: vi.fn(),
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    answerChatQuestion: h.answerChatQuestion,
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
  return { ...real, api, errText: (e: unknown) => String((e as Error)?.message ?? e) }
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
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })
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
  FakeSocket.last = null
  h.chatSessionDetail.mockReset().mockImplementation(() => Promise.resolve(h.detail))
  h.answerChatQuestion.mockReset().mockResolvedValue({ ok: true, outcome: 'answered' })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const ASKED = { role: 'user', content: 'Set up the new service.', ts: '2026-10-03T09:00:00Z' }
const QUESTIONS = [{
  question: 'Which database should the service use?', header: 'Database', multiSelect: false, free_text: true,
  options: [{ label: 'Postgres', description: 'Relational, like the rest' }, { label: 'SQLite', description: 'One file, no server' }],
}]
const CARD = {
  id: 'q-7f3a', tool_call_id: 'toolu_01', questions: QUESTIONS,
  asked_by: 'Claude Code in “Set up the service”', ts: 1, answerable: true,
}
/** The pill the question's call shows, as the gateway sends it for Claude Code's question tool. */
const PILL = { tool_call_id: 'toolu_01', tool: 'Which database should the service use?', kind: 'other', purpose: '', input_preview: '{}' }

const detail = (messages: unknown[], running: boolean, extra: Record<string, unknown> = {}) => ({
  key: SESSION, title: 'Set up the service', messages, running, queue: [], task_mode: 'agent',
  approval: 'normal', memory_mode: 'persistent', ...extra,
})

function openChat() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

describe('an agent’s question in its chat', () => {
  it('renders from its frame as a card she answers, and her answer goes to the question', async () => {
    h.detail = detail([ASKED], true)
    openChat()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeNull())
    pushFrame('tool_call', PILL)
    pushFrame('question_card', CARD)

    const card = await screen.findByRole('group', { name: 'Question for you' })
    expect(within(card).getByText('Which database should the service use?')).toBeTruthy()
    expect(within(card).getByText('Database')).toBeTruthy()
    expect(within(card).getByRole('radio', { name: /Postgres/ }).getAttribute('aria-checked')).toBe('false')

    await userEvent.click(within(card).getByRole('radio', { name: /SQLite/ }))
    await userEvent.click(within(card).getByRole('button', { name: /Send answer/ }))
    expect(h.answerChatQuestion).toHaveBeenCalledWith(SESSION, 'q-7f3a', { answers: [{ selected: [1], other: '' }] })
    // Settled at once, and the frame that follows says the same.
    await screen.findByText('You answered “Database”: SQLite')
    pushFrame('question_resolved', { id: 'q-7f3a', tool_call_id: 'toolu_01', outcome: 'answered', answers: [{ selected: [1], other: '' }] })
    expect(screen.getByText('You answered “Database”: SQLite')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Send answer/ })).toBeNull()
  })

  it('is withdrawn when her Stop ends the turn, saying why', async () => {
    h.detail = detail([ASKED], true)
    openChat()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeNull())
    pushFrame('question_card', CARD)
    await screen.findByRole('group', { name: 'Question for you' })
    pushFrame('question_resolved', { id: 'q-7f3a', outcome: 'cancelled', ended: 'its turn was stopped' })
    expect(await screen.findByText(/The question was withdrawn: its turn was stopped\./)).toBeTruthy()
    expect(screen.queryByRole('group', { name: 'Question for you' })).toBeNull()
  })

  it('opens on a reload while it still waits, from the session’s pending questions', async () => {
    const pill = { role: 'tool', content: PILL.tool, cls: 'msg msg-tool', meta: { tool_call_id: 'toolu_01', input: '{}', kind: 'other' } }
    h.detail = detail([ASKED, pill], true, { pending_questions: [{ ...CARD, session: SESSION }] })
    openChat()
    const card = await screen.findByRole('group', { name: 'Question for you' })
    expect(within(card).getByRole('radio', { name: /Postgres/ })).toBeTruthy()
  })

  it('reads after a reload as it did once answered: the card from the asking call’s row', async () => {
    const answered = {
      role: 'tool', content: PILL.tool, cls: 'msg msg-tool',
      meta: {
        tool_call_id: 'toolu_01', input: '{}', kind: 'other', done: true, output: 'User has answered your questions.',
        question: { id: 'q-7f3a', questions: QUESTIONS, asked_by: CARD.asked_by, outcome: 'answered', answers: [{ selected: [1], other: '' }] },
      },
    }
    h.detail = detail([ASKED, answered, { role: 'assistant', content: 'SQLite it is.', ts: '2026-10-03T09:02:00Z' }], false)
    openChat()
    await screen.findByText('SQLite it is.')
    await userEvent.click(await screen.findByRole('button', { name: /Worked through \d+ steps/ }))
    expect(await screen.findByText('You answered “Database”: SQLite')).toBeTruthy()
  })
})
