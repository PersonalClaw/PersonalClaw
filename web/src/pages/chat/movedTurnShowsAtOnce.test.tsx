import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { hydrateTurns, type HistMsg } from './chatTypes'

// ── A chat moved to another agent mid-turn kept showing the turn that no longer existed ─────
//
// Measured on a running gateway: the routing chip ("oncall-triage may fit better — route this
// chat to it?") was pressed while the default agent's turn waited on a permission card. The
// page kept "Thinking…", the live permission card, Stop, and "Steer — send into the running
// turn"; the steer went to the queue, and a later Allow answered a turn that was gone.
//
// The gateway now ends that turn as stopped, answers the card cancelled, writes a notice
// ("Moved to oncall-triage — it is answering your message."), tells every page what the chat
// runs on (`session_binding`), and runs her message on the new agent. And it says whether the
// running turn takes a steer at all (`turn_steerable`, session detail's `steerable`). This page
// shows each of those as it arrives, with no reload.

const SESSION = 'chat-9-x'
const ASKED = 'req-1'

const h = vi.hoisted(() => ({
  detailCalls: [] as { resolve: (d: unknown) => void }[],
  chatSessionDetail: vi.fn(),
  sendChat: vi.fn(),
}))

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: h.sendChat,
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

/** Her message, and the default agent's turn parked on a permission card for it. */
const RUNNING = {
  key: SESSION, title: SESSION, running: true, steerable: false, stream_seq: 0,
  queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  messages: [
    { role: 'user', content: 'sentry is lighting up for the carrier adapter again', ts: '2026-10-01T20:06:36Z' },
    { role: 'permission', content: 'mcp/sentry/get_issue_details', meta: { approval_id: ASKED, request_id: ASKED, tool_input: '{}', risk: 'caution' } },
  ],
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
  FakeSocket.last = null
  h.detailCalls.length = 0
  h.sendChat.mockReset().mockResolvedValue({ ok: true, queued: true })
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function openOnTheParkedTurn(query: Record<string, string> = {}, patch: Record<string, unknown> = {}) {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
  await act(async () => { h.detailCalls[0].resolve({ ...RUNNING, ...patch }) })
  // The turn is running: the composer offers Stop, or a mid-turn send for a draft.
  await waitFor(() => expect(midTurnAction()).not.toBe('(no mid-turn action)'))
}

/** The composer's action while a turn runs, by accessible name. */
const midTurnAction = () =>
  ['Stop', 'Steer — send into the running turn', 'Queue — sent when this turn ends']
    .find((name) => screen.queryByRole('button', { name })) ?? '(no mid-turn action)'

const allowButtons = () => screen.queryAllByRole('button', { name: /^Allow / })

describe('a chat moved to another agent while its turn waits on a permission card', () => {
  it('drops the live card, says where it moved, and names the new agent in the composer', async () => {
    await openOnTheParkedTurn()
    // The parked turn's card is live: it can be answered.
    expect(allowButtons().length).toBe(1)

    // What the gateway sends when she routes the chat to oncall-triage.
    pushFrame('approval_resolved', { request_id: ASKED, outcome: 'cancelled' })
    pushFrame('chat_message', { role: 'notice', content: 'Moved to oncall-triage — it is answering your message.' })
    pushFrame('session_binding', { agent: 'oncall-triage', model: '', acp_provider: '', acp_provider_agent: '', reasoning_effort: '' })

    // No card answers a turn that no longer exists; it says how it ended instead.
    await waitFor(() => expect(allowButtons()).toEqual([]))
    expect(screen.getByText('mcp/sentry/get_issue_details — not run — the turn was stopped')).toBeTruthy()
    // The notice is on the page where the conversation is, once (the gateway sends it on two
    // transports), and the composer names the agent the next message goes to.
    pushFrame('chat_message', { role: 'notice', content: 'Moved to oncall-triage — it is answering your message.' })
    expect(screen.getAllByText('Moved to oncall-triage — it is answering your message.')).toHaveLength(1)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Agent: oncall-triage' })).toBeTruthy())
    // Her message is being answered by the agent she moved it to: the turn still runs.
    expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy()
  })

  it('names the default agent in the composer when the chat is moved back to it', async () => {
    await openOnTheParkedTurn({}, { agent: 'oncall-triage' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Agent: oncall-triage' })).toBeTruthy())
    pushFrame('session_binding', { agent: '', model: '', acp_provider: '', acp_provider_agent: '', reasoning_effort: '' })
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Agent: oncall-triage' })).toBeNull())
  })
})

describe('the mid-turn send button follows the running turn', () => {
  it('queues a message behind a turn that cannot take one, and asks the gateway to queue it', async () => {
    const user = userEvent.setup()
    await openOnTheParkedTurn({ seed: 'ignore the EU region' })
    expect(screen.queryByRole('button', { name: 'Steer — send into the running turn' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Queue — sent when this turn ends' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    expect(h.sendChat.mock.calls[0][3]).toBe('followup')
  })

  it('offers Steer once the gateway says the running turn takes one, and Queue again when it does not', async () => {
    await openOnTheParkedTurn({ seed: 'ignore the EU region' })
    pushFrame('turn_steerable', { steerable: true })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Steer — send into the running turn' })).toBeTruthy())
    // The turn it could steer ended and the next one runs on an agent CLI that cannot.
    pushFrame('turn_steerable', { steerable: false })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Queue — sent when this turn ends' })).toBeTruthy())
  })

  it('reads whether the running turn takes a steer from the session when the page opens', async () => {
    const user = userEvent.setup()
    await openOnTheParkedTurn({ seed: 'ignore the EU region' }, { steerable: true })
    await user.click(screen.getByRole('button', { name: 'Steer — send into the running turn' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    expect(h.sendChat.mock.calls[0][3]).toBe('steer')
  })
})

describe('a notice in the transcript', () => {
  it('is rebuilt on reload where it was written, after the card it explains', () => {
    const turns = hydrateTurns([
      RUNNING.messages[0],
      { ...RUNNING.messages[1], meta: { ...RUNNING.messages[1].meta, resolved: 'cancelled' } },
      { role: 'notice', content: 'Moved to oncall-triage — it is answering your message.' },
      { role: 'assistant', content: 'The alerts are the carrier adapter timing out.' },
    ] as HistMsg[])
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(turns[1].segments.map((sg) => sg.kind)).toEqual(['approval', 'activity', 'text'])
    expect(turns[1].segments[1]).toEqual({ kind: 'activity', text: 'Moved to oncall-triage — it is answering your message.', activityKind: 'notice' })
  })
})
