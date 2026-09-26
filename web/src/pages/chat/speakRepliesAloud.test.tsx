import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── "Speak replies aloud" speaks replies, and Speak's refusal names the real fix (F-37) ──────
//
// Measured before the fix: the setting only switched the Speak button on. `auto_speak` was
// published by the TTS registry and read nowhere, so no reply was ever spoken on its own. And a
// Speak with text-to-speech switched off got the server's correct "Turn on …" sentence REPLACED:
// the page pattern-matched `/Settings/` and said "Text-to-speech needs a voice — choose one", a
// fix for a problem the user did not have.
//
// Driven through the real ChatPage: the send path, the socket's closing frames, the Speak button.

const h = vi.hoisted(() => ({
  detailCalls: [] as { resolve: (d: unknown) => void }[],
  chatSessionDetail: vi.fn(),
  sendChat: vi.fn(),
  voiceSynthesize: vi.fn(),
  stopChat: vi.fn(),
  tts: { enabled: true, auto_speak: true } as Record<string, unknown>,
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: h.sendChat,
    voiceSynthesize: h.voiceSynthesize,
    stopChat: h.stopChat,
    useCaseSettings: (uc: string) => Promise.resolve(uc === 'tts' ? h.tts : {}),
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
  static last: FakeSocket | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) {
    FakeSocket.last = this
    setTimeout(() => this.onopen?.(), 0)
  }
  send(): void {}
  close(): void { this.readyState = 3 }
}

/** An AudioContext jsdom lacks: enough for the page to prime and resume one. */
class FakeAudioContext {
  static made = 0
  state = 'suspended'
  currentTime = 0
  destination = {}
  constructor() { FakeAudioContext.made++ }
  resume() { this.state = 'running'; return Promise.resolve() }
}

const SESSION = 'chat-9-x'
type Frame = [type: string, data: Record<string, unknown>]
const deliver = ([type, data]: Frame) =>
  FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) })

let rafQueue: FrameRequestCallback[] = []
let rafClock = 0
function paintFrames(n = 30) {
  for (let i = 0; i < n && rafQueue.length; i++) {
    const due = rafQueue; rafQueue = []
    rafClock += 20
    act(() => { due.forEach((cb) => cb(rafClock)) })
  }
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('AudioContext', FakeAudioContext as unknown as typeof AudioContext)
  rafQueue = []; rafClock = 0
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { rafQueue.push(cb); return rafQueue.length })
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
  FakeSocket.last = null
  FakeAudioContext.made = 0
  h.tts = { enabled: true, auto_speak: true }
  h.detailCalls.length = 0
  h.sendChat.mockReset().mockResolvedValue({ ok: true, session: SESSION })
  h.voiceSynthesize.mockReset().mockResolvedValue({ ok: true, chunks: 1 })
  h.stopChat.mockReset().mockResolvedValue({ ok: true })
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const ANSWER = 'Here is the reply, read out loud.'
const base = { key: SESSION, title: SESSION, messages: [] as unknown[], running: false, queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent' }

async function openWith(query: Record<string, string>, patch: Record<string, unknown> = {}) {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
  await act(async () => { h.detailCalls[0].resolve({ ...base, ...patch }) })
}

/** Send the seeded message the way a user does: press the composer's Send. */
async function sendSeeded() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
}

function streamTheReply() {
  for (const [i, word] of ANSWER.split(/(?<= )/).entries()) act(() => { deliver(['chat_chunk', { content: word, seq: i + 1 }]) })
  paintFrames()
  act(() => { deliver(['chat_done', {}]) })
  paintFrames()
}

describe('Speak replies aloud', () => {
  it('reads a finished reply out in the tab that sent the message', async () => {
    await openWith({ seed: 'Read me the reply.' })
    await sendSeeded()
    streamTheReply()
    await waitFor(() => expect(h.voiceSynthesize).toHaveBeenCalledTimes(1))
    expect(h.voiceSynthesize).toHaveBeenCalledWith(ANSWER, SESSION)
    expect(FakeAudioContext.made, 'the send primed audio, so the reply can play when it lands').toBeGreaterThan(0)
  })

  it('reads nothing out when it is off', async () => {
    h.tts = { enabled: true, auto_speak: false }
    await openWith({ seed: 'Stay quiet.' })
    await sendSeeded()
    streamTheReply()
    await new Promise((r) => setTimeout(r, 50))
    expect(h.voiceSynthesize).not.toHaveBeenCalled()
  })

  it('reads nothing out while text-to-speech itself is off', async () => {
    h.tts = { enabled: false, auto_speak: true }
    await openWith({ seed: 'Stay quiet.' })
    await sendSeeded()
    streamTheReply()
    await new Promise((r) => setTimeout(r, 50))
    expect(h.voiceSynthesize).not.toHaveBeenCalled()
  })

  it('does not read out a reply to a message another tab sent', async () => {
    await openWith({}, { running: true, messages: [{ role: 'user', content: 'from the other tab', ts: 't1' }], stream_seq: 0 })
    streamTheReply()
    await new Promise((r) => setTimeout(r, 50))
    expect(h.voiceSynthesize).not.toHaveBeenCalled()
  })

  it('does not read out a reply you stopped', async () => {
    const user = userEvent.setup()
    await openWith({ seed: 'Then I change my mind.' })
    await sendSeeded()
    act(() => { deliver(['chat_chunk', { content: 'Half a ', seq: 1 }]) })
    paintFrames()
    await user.click(await screen.findByRole('button', { name: 'Stop' }))
    act(() => { deliver(['chat_done', {}]) })
    paintFrames()
    await new Promise((r) => setTimeout(r, 50))
    expect(h.voiceSynthesize).not.toHaveBeenCalled()
  })
})

describe('Speak says what is actually missing', () => {
  it('shows the server sentence for a switched-off text-to-speech, not "choose a voice"', async () => {
    const { ApiError } = await import('../../lib/api')
    const sentence = 'Text-to-speech is switched off. Turn on “Enable text-to-speech” in Settings → Speech & Transcription.'
    h.voiceSynthesize.mockRejectedValue(new ApiError(sentence, 503, 'tts_disabled'))
    await openWith({}, { messages: [{ role: 'user', content: 'hi', ts: 't1' }, { role: 'assistant', content: ANSWER, ts: 't2' }] })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Speak' }))
    expect(await screen.findByText(sentence)).toBeTruthy()
    expect(screen.queryByText(/needs a voice/)).toBeNull()
  })

  it('shows the server sentence when no text-to-speech model is set up', async () => {
    const { ApiError } = await import('../../lib/api')
    const sentence = 'No text-to-speech model is set up. Choose one for Text-to-speech in Settings → Models.'
    h.voiceSynthesize.mockRejectedValue(new ApiError(sentence, 503, 'tts_unbound'))
    await openWith({}, { messages: [{ role: 'user', content: 'hi', ts: 't1' }, { role: 'assistant', content: ANSWER, ts: 't2' }] })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Speak' }))
    expect(await screen.findByText(sentence)).toBeTruthy()
  })
})
