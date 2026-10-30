import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runningState, TURN_STARTED } from '../../test/runState'

// ── Stop names the turn it means ───────────────────────────────────────────────────────────────
//
// Stop used to stop whatever ran when the press reached the gateway: a Stop pressed for a turn that
// had just ended stopped the next one, and cleared its queue. Now it names its turn, the running one
// as the gateway named it (`run_state.turn.message_id`, the id of the message that started it), or
// as the transcript shows it when the gateway's name is not an id. A turn that ended meanwhile
// answers `409 turn_not_current`, and nothing was stopped: that is no failure to report, and the
// turn's own last frame says how it ended.

const SESSION = 'chat-7-x'
const STARTED_BY = 'msg_01m66g21zm27gqp6qkhma6fx3h'

const h = vi.hoisted(() => ({
  detail: null as unknown,
  stopChat: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: () => Promise.resolve(h.detail),
    stopChat: h.stopChat,
    sendChat: () => Promise.resolve({ ok: true, session: SESSION }),
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
  return { ...real, api }
})

vi.mock('../../app/appSdk', async (orig) => ({
  ...(await orig<typeof import('../../app/appSdk')>()),
  notify: h.notify,
}))

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

/** The chat open on a turn the message *asked* started, its run state naming it *named*. */
function aRunningTurn(named: string, asked: Record<string, unknown> = { message_id: STARTED_BY }) {
  h.detail = {
    key: SESSION, title: SESSION, run_state: runningState({ session: SESSION, turn: { message_id: named, started_at: TURN_STARTED } }),
    messages: [{ role: 'user', content: 'hello', ts: TURN_STARTED, meta: asked }],
    queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
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
  h.stopChat.mockReset().mockResolvedValue({ ok: true, stopped: true, withdrawn: [] })
  h.notify.mockReset()
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function pressStop() {
  const user = userEvent.setup()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Stop this turn' })).toBeTruthy())
  await user.click(screen.getByRole('button', { name: 'Stop this turn' }))
  await waitFor(() => expect(h.stopChat).toHaveBeenCalled())
  await act(async () => { await Promise.resolve() })
}

describe('Stop names the running turn', () => {
  it('as the gateway named it', async () => {
    aRunningTurn(STARTED_BY)
    await pressStop()
    expect(h.stopChat).toHaveBeenCalledWith(SESSION, false, STARTED_BY)
  })

  it('as the transcript shows it, when the gateway named it by a time', async () => {
    aRunningTurn(TURN_STARTED)
    await pressStop()
    expect(h.stopChat).toHaveBeenCalledWith(SESSION, false, STARTED_BY)
  })

  it('and names none when nothing says which it is', async () => {
    aRunningTurn(TURN_STARTED, {})
    await pressStop()
    expect(h.stopChat).toHaveBeenCalledWith(SESSION, false, undefined)
  })
})

describe('a Stop for a turn that ended meanwhile', () => {
  it('is no failure to report', async () => {
    const { ApiError } = await import('../../lib/api')
    h.stopChat.mockRejectedValueOnce(new ApiError('That turn is no longer running, so nothing was stopped.', 409, 'turn_not_current'))
    aRunningTurn(STARTED_BY)
    await pressStop()
    expect(h.notify).not.toHaveBeenCalledWith(expect.anything(), 'error')
  })

  it('while any other failure is reported', async () => {
    const { ApiError } = await import('../../lib/api')
    h.stopChat.mockRejectedValueOnce(new ApiError('The gateway did not answer.', 502, 'upstream_error'))
    aRunningTurn(STARTED_BY)
    await pressStop()
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(expect.stringMatching(/stop this turn/i), 'error'))
  })
})
