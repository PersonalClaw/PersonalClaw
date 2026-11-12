import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act, cleanup } from '@testing-library/react'
import { ApiError } from '../../lib/api'
import { runState, runningState } from '../../test/runState'

// ── The chat's stream, as a controller of its own ───────────────────────────────────────────────
//
// `useChatStream` is the part of the chat page that listens: it opens a session from its detail,
// folds the session's `/api/ws` frames into the transcript, and hands what other regions own to
// their sinks. These are its contract, driven through the one socket the way the gateway drives it.

const SESSION = 'chat-3-stream'

const h = vi.hoisted(() => ({ chatSessionDetail: vi.fn() }))

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    usageTotals: () => Promise.resolve({ totals: { turns: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, priced: true } }),
    dashboardConfig: () => Promise.resolve({}),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve({}))
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
  constructor(public url: string) { FakeSocket.last = this; setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

function frame(type: string, data: Record<string, unknown>, session: string | null = SESSION) {
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: session ? { session, ...data } : data }) }) })
}

/** A chat's detail as the gateway serves it, with nothing beyond what a case sets. */
function detail(over: Record<string, unknown> = {}) {
  return {
    key: SESSION, title: 'Feed fetch', messages: [
      { role: 'user', content: 'Why does the feed refetch?', ts: '2026-10-01T09:00:00Z' },
      { role: 'assistant', content: 'It ignores the validators.', ts: '2026-10-01T09:00:05Z' },
    ],
    run_state: runState({ session: SESSION }),
    queue: [{ id: 'q-1', content: 'and the cache?' }],
    memory_mode: 'persistent',
    ...over,
  }
}

let useChatStream: typeof import('./useChatStream')['useChatStream']

function sinks() {
  return { setRouting: vi.fn(), onVoiceChunk: vi.fn(), onReplyFinished: vi.fn(), onOpened: vi.fn() }
}

function open(s: ReturnType<typeof sinks>, sessionId: string | null = SESSION) {
  return renderHook(() => useChatStream({ sessionId, liveRun: '', setLiveRun: () => {}, ...s }))
}

beforeEach(async () => {
  vi.resetModules()
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  sessionStorage.clear()
  FakeSocket.last = null
  h.chatSessionDetail.mockReset().mockImplementation(() => Promise.resolve(detail()))
  ;({ useChatStream } = await import('./useChatStream'))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('useChatStream opens a session', () => {
  it('reads it once the chat is listening, adopts the snapshot and hands the read to the page', async () => {
    const s = sinks()
    const { result } = open(s)
    expect(result.current.loadingHistory, 'nothing cached, so the transcript is loading').toBe(true)
    await waitFor(() => expect(result.current.loadingHistory).toBe(false))
    expect(h.chatSessionDetail).toHaveBeenCalledWith(SESSION)
    expect(result.current.turns.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(result.current.title).toBe('Feed fetch')
    expect(result.current.queued).toEqual([{ id: 'q-1', content: 'and the cache?' }])
    expect(result.current.streaming).toBe(false)
    expect(s.onOpened).toHaveBeenCalledTimes(1)
    expect(s.onOpened.mock.calls[0][0]).toMatchObject({ key: SESSION, title: 'Feed fetch' })
  })

  it('says the chat does not exist when the read answers 404, and hands the page nothing', async () => {
    h.chatSessionDetail.mockImplementation(() => Promise.reject(new ApiError('Session not found', 404)))
    const s = sinks()
    const { result } = open(s)
    await waitFor(() => expect(result.current.missing).toBe(true))
    expect(result.current.loadFailure).toBeNull()
    expect(s.onOpened).not.toHaveBeenCalled()
  })

  it('reads nothing for a new chat, which has no session yet', async () => {
    const { result } = open(sinks(), null)
    await act(async () => { await new Promise((r) => setTimeout(r, 10)) })
    expect(h.chatSessionDetail).not.toHaveBeenCalled()
    expect(result.current.turns).toEqual([])
    expect(result.current.loadingHistory).toBe(false)
  })
})

describe('useChatStream folds the session’s frames', () => {
  async function opened(s = sinks()) {
    const hook = open(s)
    await waitFor(() => expect(hook.result.current.loadingHistory).toBe(false))
    return hook
  }

  it('applies a frame for this chat and drops one for another chat', async () => {
    const { result } = await opened()
    frame('chat_message', { role: 'error', content: 'The model refused.' }, 'chat-9-other')
    expect(result.current.turns.at(-1)?.segments.some((sg) => sg.kind === 'error')).toBe(false)
    frame('chat_message', { role: 'error', content: 'The model refused.' })
    expect(result.current.turns.at(-1)?.segments.at(-1)).toMatchObject({ kind: 'error', text: 'The model refused.' })
  })

  it('keeps the queue strip in the gateway’s order', async () => {
    const { result } = await opened()
    frame('queue_push', { queue_id: 'q-2', content: 'and the ETag?' })
    frame('queue_promoted', { queue_id: 'q-2' })
    expect(result.current.queued.map((q) => q.id)).toEqual(['q-2', 'q-1'])
    frame('queue_pop', { queue_id: 'q-2' })
    expect(result.current.queued.map((q) => q.id)).toEqual(['q-1'])
  })

  it('keeps what a queued message is from the frame that queued it: its id, and a widget\'s mark', async () => {
    // The composer's Edit gives a queued widget message back marked by these (`takeBackQueued`).
    const { result } = await opened()
    frame('queue_push', { queue_id: 'q-3', content: '[UI] open', message_id: 'msg_01jbxq3c6m8n9p2r4s5t6v7w8y', widget_action: true })
    frame('queue_push', { queue_id: 'q-4', content: 'my own words', message_id: 'msg_01jbxq3c6m8n9p2r4s5t6v7w8z' })
    frame('queue_push', { queue_id: 'q-5', content: 'not a mark', widget_action: 'yes' })
    const byId = Object.fromEntries(result.current.queued.map((q) => [q.id, q]))
    expect(byId['q-3']).toMatchObject({ messageId: 'msg_01jbxq3c6m8n9p2r4s5t6v7w8y', fromWidget: true })
    expect(byId['q-4'].fromWidget).toBeUndefined()
    expect(byId['q-4'].messageId).toBe('msg_01jbxq3c6m8n9p2r4s5t6v7w8z')
    expect(byId['q-5'].fromWidget, 'only `true` marks it').toBeUndefined()
  })

  it('🔴 marks a turn the gateway starts from a widget\'s steered or queued message, and only that one', async () => {
    // A steer the turn took, and a queued message the drain started, arrive as `chat_user_message`
    // with `widget_action` when they came from a widget: a quote of that bubble says so.
    const { result } = await opened()
    frame('chat_user_message', { content: '[UI] open', ts: '2026-10-01T09:01:00Z', widget_action: true })
    expect(result.current.turns.at(-1)).toMatchObject({ role: 'user', fromWidget: true })
    frame('chat_done', { outcome: 'complete' })
    frame('chat_user_message', { content: 'my own words', ts: '2026-10-01T09:02:00Z' })
    expect(result.current.turns.at(-1)?.fromWidget).toBeUndefined()
    frame('chat_done', { outcome: 'complete' })
    frame('chat_user_message', { content: 'not a mark', ts: '2026-10-01T09:03:00Z', widget_action: 'yes' })
    expect(result.current.turns.at(-1)?.fromWidget, 'only `true` marks it').toBeUndefined()
  })

  it('starts the streaming claim on a turn the gateway starts, and settles it on chat_done', async () => {
    const s = sinks()
    const { result } = await opened(s)
    frame('chat_user_message', { content: 'and the cache?', ts: '2026-10-01T09:01:00Z' })
    expect(result.current.streaming).toBe(true)
    expect(result.current.srAnnounce).toBe('Assistant is responding…')
    expect(s.onReplyFinished).toHaveBeenLastCalledWith(SESSION, { last: false })
    frame('chat_done', { outcome: 'complete' })
    expect(result.current.streaming).toBe(false)
    expect(s.onReplyFinished).toHaveBeenLastCalledWith(SESSION, { last: true })
  })

  it('hands what other regions own to their sinks', async () => {
    const s = sinks()
    await opened(s)
    frame('routing_suggestion', { agent: 'researcher', specialty: 'sources', score: 0.8, method: 'embedding' })
    expect(s.setRouting).toHaveBeenCalledWith({ session: SESSION, agent: 'researcher', specialty: 'sources', score: 0.8, method: 'embedding' })
    frame('voice_chunk', { audio: 'UklGRg==', request: 'read-1' })
    expect(s.onVoiceChunk).toHaveBeenCalledWith('UklGRg==', 'read-1')
  })

  it('takes the run state a work_phase frame carries, and no older one', async () => {
    const { result } = await opened()
    // A turn this chat follows (its own queued message starting), so its frames apply as they come.
    frame('chat_user_message', { content: 'and the cache?', ts: '2026-10-01T09:01:00Z' })
    frame('work_phase', { ...runningState({ session: SESSION, phase: 'tool', line: 'Reading fetch.py', seq: 5, steerable: true }) })
    expect(result.current.turnLine).toBe('Reading fetch.py')
    expect(result.current.takesSteers).toBe(true)
    frame('work_phase', { ...runningState({ session: SESSION, phase: 'thinking', line: 'Thinking', seq: 4 }) })
    expect(result.current.turnLine, 'a frame older than the state held is not applied').toBe('Reading fetch.py')
  })

  it("takes the running turn's latest step from the run state, so the row can say how long it has been quiet", async () => {
    const { result } = await opened()
    frame('chat_user_message', { content: 'and the cache?', ts: '2026-10-01T09:01:00Z' })
    frame('work_phase', { ...runningState({ session: SESSION, phase: 'tool', line: 'Reading fetch.py', seq: 5, last_step_at: '2026-10-01T09:01:30Z' }) })
    expect(result.current.turnLastStep).toBe('2026-10-01T09:01:30Z')
    frame('work_phase', { ...runState({ session: SESSION, phase: 'done', line: 'Done in 40 seconds', seq: 6, last_step_at: '2026-10-01T09:01:40Z' }) })
    expect(result.current.turnLastStep, 'a turn that ended has no latest step to be quiet since').toBeNull()
  })

  it.each([
    ['complete', 'Done in 40 seconds'],
    ['stopped', 'Stopped'],
    ['error', 'Claude stopped after you denied shell.'],
  ] as const)('hands the turn its ending with how it ended (%s), so only a completed turn blooms', async (outcome, words) => {
    const { result } = await opened()
    frame('chat_user_message', { content: 'and the cache?', ts: '2026-10-01T09:01:00Z' })
    frame('work_phase', { ...runningState({ session: SESSION, phase: 'tool', line: 'Reading fetch.py', seq: 5 }) })
    expect(result.current.turnEnding, 'the control: a running turn has no ending').toBeNull()
    frame('work_phase', { ...runState({ session: SESSION, phase: 'done', line: words, last_outcome: outcome, seq: 6 }) })
    expect(result.current.turnEnding).toEqual({ phase: 'done', line: words, outcome })
  })

  it("takes no helper's phase for the chat's own, though it names the chat it works for", async () => {
    const { result } = await opened()
    frame('chat_user_message', { content: 'and the cache?', ts: '2026-10-01T09:01:00Z' })
    frame('work_phase', { ...runningState({ session: SESSION, phase: 'tool', line: 'Reading fetch.py', seq: 5 }) })
    // One frame type for every kind of work: a helper's ending, newer than the chat's, is the helper's.
    frame('work_phase', { work: 'subagent:h1', kind: 'subagent', phase: 'done', line: 'Done in 3 seconds', since: null, seq: 9 })
    expect(result.current.turnLine).toBe('Reading fetch.py')
    expect(result.current.turnPhase).toBe('tool')
    frame('work_phase', { ...runningState({ session: SESSION, phase: 'streaming', line: 'Writing', seq: 6 }) })
    expect(result.current.turnLine, "the helper's stamp moved no mark of the chat's").toBe('Writing')
  })
})
