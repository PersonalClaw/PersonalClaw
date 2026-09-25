import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A fast-failing FIRST turn wedged the composer on Stop, forever ────────────────────────────
//
// Measured out of the box (fresh install, bundled offline model bound to Chat), 6/6: send one
// message, the turn is refused in ~1.4 s, and the composer's primary action stays the filled
// **Stop** square. Never Send again — held for 100 s, and the thinking animation kept running.
// The next message then vanished: the composer cleared and the text reached no transcript.
//
// The captured network says the request SUCCEEDED and the client ignored the result. From
// `.lanes/day56-evidence/s2g-final-repro.json`, relative to page load:
//
//     +4.519s  POST /api/chat/sessions            ← send() creates the session
//     +4.539s  POST /api/chat?ws=1
//     +4.560s  GET  /api/chat/sessions/<key>      ← the [sessionId] load effect, turn RUNNING
//     +4.971s  ws   chat_message role=error  +  chat_done        ← the turn TERMINATES
//     ~+5.05s  ←    that GET resolves: { running: true }         ← and RE-ARMS streaming
//     +9.081s… GET  /api/chat/sessions/<key> every 2 s, 41 times, each `running:false`, discarded
//
// So the snapshot was HONEST when it was read and stale by the time it landed: the load effect's
// `if (d.running) markStreaming(true)` had no way to know a terminal event had arrived since the
// fetch was issued. `chat_done` and a turn-level `chat_message role==='error'` both clear
// streaming correctly — they just lose the race.
//
// The trigger is TIMING, not the error class: a control with the Chat model UNBOUND failed in
// ~25 s and recovered correctly (`s2j-scope.json`). Any fast first-turn failure hits this — a
// 401, a rate limit, a refused tool, a context refusal.
//
// 🔑 WHY THIS IS NOT `streamStall.ts`'s JOB, MEASURED RATHER THAN ARGUED. `resolveStalledStream`
// heals a streaming claim that outlived its turn, whatever produced it, and it does heal this
// one — but only after its silent window elapses. Driving this exact reproduction against a tree
// carrying ONLY that reconciler: the instant the turn ended the composer read **Stop**, the live
// region read *"Assistant is responding…"*, and a message sent in that window went out with
// `queue_mode: 'steer'` and rendered nowhere at all. So the two are sequential, not redundant:
// that one is the safety net, this one keeps the false claim from being made. The reconciler's
// own behaviour is asserted in `streamStall.test.ts` and `web/e2e/streamStall.spec.ts`, and is
// deliberately NOT re-asserted here.
//
// These tests drive the ORDERING, because the ordering is the bug: the detail promise is held
// open, the terminal event is delivered, and only then does the detail resolve saying
// `running: true`. A test that resolves the fetch before the event passes on the defect.

// ── mocks ────────────────────────────────────────────────────────────────────────────────────

const h = vi.hoisted(() => {
  const detail = {
    key: 'chat-6-x', title: 'chat-6-x', messages: [] as unknown[], running: false,
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
  return {
    detail,
    // every chatSessionDetail() call, so a test can resolve them out of order
    detailCalls: [] as { resolve: (d: unknown) => void }[],
    sendChat: vi.fn(),
    chatSessionDetail: vi.fn(),
  }
})

vi.mock('../../lib/api', () => {
  // An empty ARRAY as the catch-all: it satisfies the list readers (`.slice`, `.filter`)
  // and the object readers alike (`r?.ok` is simply undefined), so one default covers a
  // page that reads ~40 endpoints.
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: h.sendChat,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    createChatSession: () => Promise.resolve({ key: 'chat-6-x' }),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  // Anything ChatSession reaches for that this test does not care about resolves to a
  // benign, shape-agnostic value rather than throwing — the page reads ~40 endpoints and
  // enumerating them would make this file a mock inventory instead of a test.
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  return { api, errText: (e: unknown) => String((e as Error)?.message ?? e) }
})

// A WebSocket jsdom can't open. Captures the instance so a test can push frames.
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

/** Push a WS envelope into the page exactly as useChatSocket would. */
function pushFrame(type: string, data: Record<string, unknown>) {
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data }) }) })
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  // jsdom has no layout, so it ships neither scrollIntoView (the transcript's
  // stick-to-bottom effect) nor IntersectionObserver (the session map's region tracking).
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
  h.sendChat.mockReset().mockResolvedValue({ ok: true, session: 'chat-6-x' })
  // Every detail read is parked; the test decides what it answers and WHEN.
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const page = (query: Record<string, string> = {}) =>
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-6-x" navigate={() => {}} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )

/** Resolve the Nth outstanding detail read (0-based) with a patch over the base payload. */
async function answerDetail(n: number, patch: Record<string, unknown>) {
  const call = h.detailCalls[n]
  expect(call, `detail read #${n} was never issued`).toBeTruthy()
  await act(async () => { call.resolve({ ...h.detail, ...patch }) })
}

const liveRegionText = () =>
  [...document.querySelectorAll('.sr-only[aria-live="polite"]')].map((n) => n.textContent ?? '').join(' | ')

/** The composer's primary action, BY ACCESSIBLE NAME. jsdom has no layout, so a found node
 *  is not a node anyone can see — the name is what a user and a screen reader actually get,
 *  and it is the whole difference between Stop and Send. Returns the one that is mounted,
 *  so a failure reads `expected 'Stop' to be 'Send message'` rather than "not found". */
const primaryAction = () =>
  ['Stop', 'Steer — send into the running turn', 'Send message']
    .find((name) => screen.queryByRole('button', { name })) ?? '(no primary action)'

// ── 1. the race: a late `running: true` must not re-arm a turn that already ended ────────────

describe('a detail snapshot that lands AFTER the turn terminated', () => {
  /** Reproduce the measured ordering: read issued mid-turn → turn terminates → read lands. */
  async function raceTheTerminalEvent(query: Record<string, string> = {}) {
    page(query)
    // The load effect issued its read while the turn was still running (+4.560s).
    await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
    // The turn terminates — both terminal frames the backend actually sent (+4.971s).
    pushFrame('chat_message', { session: 'chat-6-x', role: 'error', content: "This turn's context does not fit." })
    pushFrame('chat_done', { session: 'chat-6-x' })
    // …and only NOW does the in-flight read land, honestly reporting the state it was read
    // at (~+5.05s). This ordering IS the bug: resolve it first and the defect passes.
    await answerDetail(0, { running: true })
  }

  it('leaves the composer on Send, not wedged on Stop', async () => {
    await raceTheTerminalEvent()
    await waitFor(() => expect(primaryAction()).toBe('Send message'))
  })

  it('stops announcing "Assistant is responding" to a screen reader', async () => {
    await raceTheTerminalEvent()
    await waitFor(() => expect(primaryAction()).toBe('Send message'))
    expect(liveRegionText()).not.toMatch(/Assistant is responding/)
  })

  it('sends the NEXT message as a real turn, not a mid-stream orphan', async () => {
    // `?seed=` pre-fills the composer, so this drives the real send path without typing
    // into CodeMirror (which jsdom cannot lay out).
    const user = userEvent.setup()
    await raceTheTerminalEvent({ seed: 'does this one land?' })

    // Press whatever the composer offers — on the defect that is "Steer". The contract
    // here is about the OUTCOME of pressing the primary action, not about which one is
    // mounted (the two tests above own that), so the red states the data loss directly.
    await user.click(screen.getByRole('button', { name: primaryAction() }))

    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    // 4th arg is `queue_mode`. 'steer' is the mid-stream branch, and it is the branch that
    // loses the message: it pushes no user bubble, and the server suppresses its own echo
    // on the standing "FE adds it optimistically" contract — so the text renders nowhere
    // on either side. That is the measured disappearance.
    const [, , , queueMode] = h.sendChat.mock.calls[0]
    expect(queueMode, 'a turn that already ended must not be steered into').toBeUndefined()
    expect(await screen.findByText('does this one land?')).toBeTruthy()
  })
})

// ── 2. a legitimate mid-stream arm still happens — the guard is about ORDER, not about running ─

describe('a detail snapshot for a turn that is genuinely still running', () => {
  it('still arms streaming, so a resumed session shows Stop', async () => {
    // The falsifiability control. Without it, "the composer shows Send" above is also
    // satisfied by a fix that simply never arms streaming from a load at all — which would
    // break resuming a live turn, the branch's actual job.
    page()
    await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
    await answerDetail(0, { running: true })  // no terminal event seen → the read is current
    await waitFor(() => expect(primaryAction()).toBe('Stop'))
  })
})

// ── 3. the swallow's own floor: the server dispatched it, so SHOW it ─────────────────────────

describe('a mid-stream send the server ran as a fresh turn', () => {
  it('renders the user bubble instead of losing the message', async () => {
    const user = userEvent.setup()
    page({ seed: 'did this one survive?' })
    await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
    // Streaming armed with no terminal event — legitimate, so the composer offers Steer.
    await answerDetail(0, { running: true })
    await waitFor(() => expect(primaryAction()).toBe('Steer — send into the running turn'))

    // The server gates on ITS OWN `session.running`. When the turn is already over it
    // ignores the steer and dispatches a normal turn, answering with neither `steered`
    // nor `queued` — the shape the client used to render nowhere.
    h.sendChat.mockResolvedValue({ ok: true, session: 'chat-6-x' })
    await user.click(screen.getByRole('button', { name: 'Steer — send into the running turn' }))

    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    expect(await screen.findByText('did this one survive?')).toBeTruthy()
  })
})
