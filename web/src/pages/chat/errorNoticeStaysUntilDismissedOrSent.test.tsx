import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { INFO_NOTICE_MS } from '../../ui/composer/ComposerNotice'

// ── An error above the chat composer stays until it is dismissed or the user sends again ─────
//
// 🔴 Before: the page cleared every notice six seconds after showing it — a refused Speak, a
// failed transcription with its provider's reason, a branch that failed — so a sentence that
// named its fix was gone before it was read, and nothing let the user take it down sooner.
// Only an informational notice may clear on its own.
//
// Driven through the real ChatPage: Speak, refused by the server with its own sentence, shows
// an error; then time passes, the Dismiss is pressed, or the composer's Send is. "Plan this
// first" on a running chat parks the run, which is said as an update.

const h = vi.hoisted(() => ({
  detailCalls: [] as { resolve: (d: unknown) => void }[],
  chatSessionDetail: vi.fn(),
  sendChat: vi.fn(),
  voiceSynthesize: vi.fn(),
  chatPlanActivate: vi.fn(),
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: h.sendChat,
    voiceSynthesize: h.voiceSynthesize,
    chatPlanActivate: h.chatPlanActivate,
    // The settings read carries the revision a save names (`lib/staleWrite.ts`).
    useCaseSettings: (uc: string) => Promise.resolve({
      value: uc === 'tts' ? { enabled: true, auto_speak: false } : {}, revision: 'r1',
    }),
    personalclawConfig: () => Promise.resolve({ voice: {} }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    createChatSession: () => Promise.resolve({ key: SESSION }),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
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

/** An AudioContext jsdom lacks: enough for Speak to prime and resume one. */
class FakeAudioContext {
  state = 'suspended'
  currentTime = 0
  destination = {}
  resume() { this.state = 'running'; return Promise.resolve() }
}

const SESSION = 'chat-9-x'
const REFUSED =
  'Text-to-speech is switched off. Turn on “Enable text-to-speech” in Settings → Speech & Transcription.'
const PARKED = 'This run is parked — approve the plan below to resume it.'
const TURNS = [
  { role: 'user', content: 'hi', ts: 't1' },
  { role: 'assistant', content: 'Here is the reply.', ts: 't2' },
]

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  // Faked, and moving with real time too, so the page's own waits run while a test can still
  // jump past the six seconds that used to clear the notice.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('AudioContext', FakeAudioContext as unknown as typeof AudioContext)
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
  h.detailCalls.length = 0
  h.sendChat.mockReset().mockResolvedValue({ ok: true, session: SESSION })
  const { ApiError } = await import('../../lib/api')
  h.voiceSynthesize.mockReset().mockRejectedValue(new ApiError(REFUSED, 503, 'tts_disabled'))
  h.chatPlanActivate.mockReset().mockResolvedValue({ parked: true })
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** Open the chat with a reply to speak, and ``draft`` waiting in the composer. */
async function open(draft = '') {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={draft ? { seed: draft } : {}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
  await act(async () => {
    h.detailCalls[0].resolve({
      key: SESSION, title: SESSION, messages: TURNS, running: false, queue: [],
      task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    })
  })
}

/** Press Speak, which the server refuses: the error the composer shows, as an alert. */
async function refusedSpeak(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await user.click(await screen.findByRole('button', { name: 'Speak' }))
  const alert = (await screen.findByText(REFUSED)).closest<HTMLElement>('[role="alert"]')
  expect(alert, 'announced as an alert').not.toBeNull()
  return alert!
}

describe('an error above the composer', () => {
  it('stays past the six seconds that used to clear it, until it is dismissed', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await open()
    const alert = await refusedSpeak(user)

    act(() => { vi.advanceTimersByTime(60_000) })

    expect(screen.queryByText(REFUSED), 'still there a minute on').not.toBeNull()
    await user.click(within(alert).getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText(REFUSED)).toBeNull()
  })

  it('goes when the user sends again', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await open('Try once more.')
    await refusedSpeak(user)

    await user.click(await screen.findByRole('button', { name: 'Send message' }))

    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    expect(screen.queryByText(REFUSED), 'sending is the user moving on').toBeNull()
  })
})

describe('an update above the composer', () => {
  it('clears on its own: a run parked for its plan', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await open()
    await user.click(await screen.findByRole('button', { name: 'Add to message' }))
    await user.click(await screen.findByRole('button', { name: /Plan this first/ }))
    const update = (await screen.findByText(PARKED)).closest('[role="status"]')
    expect(update, 'said as an update, not an alert').not.toBeNull()
    expect(within(update as HTMLElement).queryByRole('button', { name: 'Dismiss' })).toBeNull()

    act(() => { vi.advanceTimersByTime(INFO_NOTICE_MS) })

    expect(screen.queryByText(PARKED)).toBeNull()
  })
})
