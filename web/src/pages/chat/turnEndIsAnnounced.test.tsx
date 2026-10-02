import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A screen reader heard "Response complete." for turns that did not complete ──────────────
//
// The chat narrates a turn to assistive tech through a polite live region: "Assistant is
// responding…" when a turn starts, then one sentence when it ends. The ending was inferred from
// the composer's streaming flag going false, so every way a turn stops streaming said the same
// thing: pressing Stop, a failed turn, and a mid-turn retry notice ("⟳ Connection lost —
// retrying..."), which also flipped the composer back to Send while the retried turn went on.
// And a queued turn the gateway started next never showed as streaming again.
//
// The gateway now says how a turn ended: the final `chat_done` carries `outcome` (complete /
// stopped / error), the Stop answer says whether it `stopped` anything, and session detail
// serves `last_turn_outcome` for a tab that missed the frame. This page announces THAT.
//
// 🔑 What is asserted is what a screen reader would speak: every text change of a polite live
// region, in order (`spoken`). A final DOM state can look right while the region said something
// false on the way there, which is exactly the Stop defect.

const h = vi.hoisted(() => {
  // A chat with one earlier exchange: the transcript (and the live region inside it) is on screen.
  const detail = {
    key: 'chat-7-x', title: 'chat-7-x', running: false,
    messages: [
      { role: 'user', content: 'first question', ts: '2026-09-26T09:59:00Z' },
      { role: 'assistant', content: 'first answer', ts: '2026-09-26T09:59:05Z' },
      { role: 'user', content: 'hello', ts: '2026-09-26T10:00:00Z' },
    ] as unknown[],
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
  return {
    detail,
    detailCalls: [] as { resolve: (d: unknown) => void }[],
    chatSessionDetail: vi.fn(),
    stopChat: vi.fn(),
    regenerate: vi.fn(),
  }
})

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    stopChat: h.stopChat,
    regenerate: h.regenerate,
    sendChat: () => Promise.resolve({ ok: true, session: 'chat-7-x' }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  // Everything else the page reads resolves to a benign, shape-agnostic value.
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
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: 'chat-7-x', ...data } }) }) })
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

/** Every text change of a polite live region, in order — what a screen reader speaks. */
let spoken: string[] = []
let observer: MutationObserver | null = null
function listen() {
  spoken = []
  const heard = new Map<Element, string>()
  observer = new MutationObserver(() => {
    for (const region of document.querySelectorAll('[aria-live="polite"]')) {
      const text = (region.textContent ?? '').trim()
      if (text && heard.get(region) !== text) spoken.push(text)
      heard.set(region, text)
    }
  })
  observer.observe(document.body, { subtree: true, childList: true, characterData: true })
}

/** Only the turn narration: its start and its end. */
const TURN = /^(Assistant is responding…|Response (complete|stopped|interrupted|ended with an error)\.)$/
const turnSaid = () => spoken.filter((s) => TURN.test(s))
const lastTurnSaid = () => turnSaid().at(-1)

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
  h.stopChat.mockReset().mockResolvedValue({ ok: true, stopped: true })
  h.regenerate.mockReset().mockResolvedValue({ ok: true })
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
  listen()
})

afterEach(() => {
  observer?.disconnect()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const page = () =>
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-7-x" navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )

async function answerDetail(n: number, patch: Record<string, unknown>) {
  const call = h.detailCalls[n]
  expect(call, `detail read #${n} was never issued`).toBeTruthy()
  await act(async () => { call.resolve({ ...h.detail, ...patch }) })
}

/** The composer's primary action, by accessible name — Stop while a turn runs, Send otherwise. */
const primaryAction = () =>
  ['Stop', 'Steer — send into the running turn', 'Queue — sent when this turn ends', 'Send message']
    .find((name) => screen.queryByRole('button', { name })) ?? '(no primary action)'

/** Open the chat on a turn that is running. `beforeTheTurn` runs once the page has issued its
 *  first read and before that read answers, so nothing has streamed yet. */
async function aRunningTurn(beforeTheTurn?: () => void) {
  page()
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
  beforeTheTurn?.()
  await answerDetail(0, { running: true })
  await waitFor(() => expect(primaryAction()).toBe('Stop'))
  await waitFor(() => expect(lastTurnSaid()).toBe('Assistant is responding…'))
}

describe('a turn that fails', () => {
  it('is still running after its error message, and ends said as an error', async () => {
    await aRunningTurn()
    pushFrame('chat_message', { role: 'error', content: "This turn's context does not fit." })
    // The error row is not the end of the turn: `chat_done` is. Until it lands, Stop stays.
    expect(primaryAction()).toBe('Stop')
    expect(turnSaid()).not.toContain('Response complete.')

    pushFrame('chat_done', { outcome: 'error' })
    await waitFor(() => expect(primaryAction()).toBe('Send message'))
    await waitFor(() => expect(lastTurnSaid()).toBe('Response ended with an error.'))
    expect(turnSaid()).not.toContain('Response complete.')
  })
})

describe('a turn that hits a transient failure and is retried', () => {
  it('keeps running through the retry notice, and only the retried answer ends it', async () => {
    await aRunningTurn()
    pushFrame('chat_message', { role: 'error', content: '⟳ Connection lost — retrying...' })
    expect(primaryAction()).toBe('Stop')
    // The gateway re-runs the message as the session's next turn.
    pushFrame('chat_user_message', { content: 'hello', ts: '2026-09-26T10:00:01Z' })
    pushFrame('chat_chunk', { content: 'Recovered.', seq: 1 })
    expect(primaryAction()).toBe('Stop')
    expect(turnSaid()).toEqual(['Assistant is responding…'])

    pushFrame('chat_done', { outcome: 'complete' })
    await waitFor(() => expect(lastTurnSaid()).toBe('Response complete.'))
    expect(turnSaid()).toEqual(['Assistant is responding…', 'Response complete.'])
  })
})

describe('Stop', () => {
  it('is said as a stop once the gateway confirms it, never as a completion, and only once', async () => {
    const user = userEvent.setup()
    await aRunningTurn()
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(h.stopChat).toHaveBeenCalledWith('chat-7-x')
    await waitFor(() => expect(lastTurnSaid()).toBe('Response stopped.'))
    expect(primaryAction()).toBe('Send message')

    // The stopped turn's own terminal frame follows. It says the same thing, so nothing more.
    pushFrame('chat_done', { outcome: 'stopped' })
    await act(async () => { await Promise.resolve() })
    expect(turnSaid()).toEqual(['Assistant is responding…', 'Response stopped.'])
  })

  it('is not said as a stop, or as anything, when the gateway did not carry it out', async () => {
    const user = userEvent.setup()
    await aRunningTurn()
    h.stopChat.mockRejectedValueOnce(new Error('the gateway did not answer'))
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(h.stopChat).toHaveBeenCalled())
    await act(async () => { await Promise.resolve() })
    expect(turnSaid()).toEqual(['Assistant is responding…'])

    // The turn went on and finished; its own end is what the region says.
    pushFrame('chat_done', { outcome: 'complete' })
    await waitFor(() => expect(lastTurnSaid()).toBe('Response complete.'))
  })

  it('leaves the ending to the turn when the press found nothing to stop', async () => {
    const user = userEvent.setup()
    await aRunningTurn()
    h.stopChat.mockResolvedValueOnce({ ok: true, stopped: false })
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(h.stopChat).toHaveBeenCalled())
    await act(async () => { await Promise.resolve() })
    expect(turnSaid()).not.toContain('Response stopped.')

    pushFrame('chat_done', { outcome: 'complete' })
    await waitFor(() => expect(lastTurnSaid()).toBe('Response complete.'))
    expect(turnSaid()).not.toContain('Response stopped.')
  })
})

describe('a queued turn the gateway starts', () => {
  it('shows as streaming again, and says so', async () => {
    page()
    await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
    await answerDetail(0, { running: false })
    await waitFor(() => expect(primaryAction()).toBe('Send message'))

    pushFrame('chat_user_message', { content: 'the queued one', ts: '2026-09-26T10:00:02Z' })
    await waitFor(() => expect(primaryAction()).toBe('Stop'))
    expect(lastTurnSaid()).toBe('Assistant is responding…')
  })
})

describe('a tab that missed the terminal frame', () => {
  it('says the ending session detail reports when it reconnects', async () => {
    await aRunningTurn()
    // The socket drops; the turn ends while it is down; the reconnect re-reads the chat.
    act(() => { FakeSocket.last?.onclose?.() })
    await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(2), { timeout: 5000 })
    await answerDetail(h.detailCalls.length - 1, { running: false, last_turn_outcome: 'error' })
    await waitFor(() => expect(primaryAction()).toBe('Send message'))
    expect(lastTurnSaid()).toBe('Response ended with an error.')
  })

  it('says the ending session detail reports when the stall reconciler settles it', async () => {
    // Only the reconciler's clock is faked: its tick and the quiet windows it measures. It is
    // faked once the page has issued its first read, not before the render. `waitFor` polls on
    // `setInterval`, so with that faked, the wait for the read (a count, not a DOM change) is
    // re-checked only by a later DOM mutation. When the socket opens after the page has finished
    // painting, which a loaded suite produces, there is none, and the wait timed out on a read
    // that had been issued.
    await aRunningTurn(() => vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] }))
    const reads = h.detailCalls.length
    // No frame arrives. The reconciler reads the chat on its next tick…
    await act(async () => { vi.advanceTimersByTime(2_100) })
    expect(h.detailCalls.length, 'the reconciler never read the chat').toBe(reads + 1)
    // …and the read lands once the transcript has been quiet past the settle grace.
    await act(async () => { vi.advanceTimersByTime(2_000) })
    await answerDetail(reads, { running: false, last_turn_outcome: 'stopped' })
    await act(async () => { await Promise.resolve() })
    expect(primaryAction()).toBe('Send message')
    expect(lastTurnSaid()).toBe('Response stopped.')
  })
})

// ── A reply the gateway's restart cut off ────────────────────────────────────────────────────
//
// The gateway ends a running turn when it restarts, and the turn says so in the chat before it is
// saved. The page said "Response stopped." for it, as if she had pressed Stop, and offered no way
// to ask again except the hover row's Regenerate.

const RESTARTED = 'The gateway restarted before this reply finished. Send your message again to retry.'

describe('a reply the gateway restart cut off', () => {
  it('is said as interrupted, never as a stop', async () => {
    await aRunningTurn()
    pushFrame('chat_message', { role: 'error', content: RESTARTED })
    pushFrame('chat_done', { outcome: 'interrupted' })
    await waitFor(() => expect(primaryAction()).toBe('Send message'))
    await waitFor(() => expect(lastTurnSaid()).toBe('Response interrupted.'))
    expect(turnSaid()).not.toContain('Response stopped.')
  })

  it('shows the notice the saved chat ends on, with a Retry that sends the question again', async () => {
    const user = userEvent.setup()
    page()
    await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
    const earlier = 'The reply stopped before it finished. Send your message again to retry.'
    await answerDetail(0, {
      running: false,
      messages: [
        { role: 'user', content: 'first question', ts: '2026-09-26T09:59:00Z' },
        { role: 'error', content: earlier, ts: '2026-09-26T09:59:05Z' },
        { role: 'user', content: 'hello', ts: '2026-09-26T10:00:00Z' },
        { role: 'error', content: RESTARTED, ts: '2026-09-26T10:00:03Z' },
      ],
    })
    await waitFor(() => expect(screen.getByText(RESTARTED)).toBeTruthy())
    // Only the turn the chat ends on is sent again: an earlier one has been answered since.
    const older = screen.getByText(earlier).closest('[role="alert"]') as HTMLElement
    expect(within(older).queryByRole('button', { name: 'Retry' })).toBeNull()
    const alert = screen.getByText(RESTARTED).closest('[role="alert"]') as HTMLElement
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(h.regenerate).toHaveBeenCalledWith('chat-7-x')
  })
})
