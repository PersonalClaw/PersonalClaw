import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useState } from 'react'
import type { ChatTurn } from './chatTypes'
import type { ChatStream } from './useChatStream'
import { receiptFor } from '../../test/chatReceipt'

// ── A message sent twice is one message ─────────────────────────────────────────────────────────
//
// A send that failed on the way (a dropped connection) may still have reached the gateway, and the
// owner sends it again. The page names the message before it sends it, and sending the same words
// again before a send of them was answered sends the same message: the same id, which the gateway
// answers with the first send's receipt and runs nothing (`duplicate`), and one bubble on the page.

const NEW_KEY = 'chat-6-twice'

const h = vi.hoisted(() => ({
  createChatSession: vi.fn(),
  sendChat: vi.fn(),
  setApprovalMode: vi.fn(),
  setSessionReplyStyle: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  const api = new Proxy({
    createChatSession: h.createChatSession, sendChat: h.sendChat, setApprovalMode: h.setApprovalMode,
    setSessionReplyStyle: h.setSessionReplyStyle,
  } as Record<string, unknown>, {
    get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) },
  })
  return { ...real, api }
})
vi.mock('../../app/appSdk', () => ({ notify: h.notify }))
vi.mock('../../lib/useComposerData', () => ({ useComposerData: () => ({ discovered: {}, agents: [], providers: [], models: [], ready: true }) }))
vi.mock('../../app/usePlatform', () => ({ usePlatform: () => 'linux' }))
vi.mock('../../ui/composer/useScreenShare', () => ({
  useScreenShare: () => ({ available: false, sharing: false, disabledReason: '', toggle: vi.fn(), captureAndStage: vi.fn(), lastFrame: () => null }),
}))

let useChatComposer: typeof import('./composer/useChatComposer')['useChatComposer']

/** The page around the controller: the stream it reads, and the two lazy hooks into its peers. */
function mount({ sessionId = null as string | null, turns: initial = [] as ChatTurn[], streaming = false, takesSteers = false } = {}) {
  const navigate = vi.fn()
  const setLiveRun = vi.fn()
  const followNewTurn = vi.fn()
  const oweSpokenReply = vi.fn()
  const setSteered = vi.fn()
  const markStreaming = vi.fn()
  const hook = renderHook(() => {
    const [turns, setTurns] = useState<ChatTurn[]>(initial)
    const stream = {
      sessionRef: { current: sessionId }, turns, setTurns,
      patchLastAssistant: (fn: (s: ChatTurn['segments']) => ChatTurn['segments']) => setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? { ...t, segments: fn(t.segments) } : t))),
      dropTextRun: vi.fn(), streamingRef: { current: streaming }, markStreaming, sayTurnEnded: vi.fn(),
      turnSeqRef: { current: 0 }, stoppedTurnRef: { current: false }, takesSteersRef: { current: takesSteers },
      setMissing: vi.fn(), setSteered, followups: [], setFollowups: vi.fn(), followupsNeed: '', setFollowupsNeed: vi.fn(),
      checkWorkOffer: null, setCheckWorkOffer: vi.fn(), sessionBindingRef: { current: null }, bindingToldRef: { current: false }, bindingNonce: 0,
    } as unknown as ChatStream
    const c = useChatComposer({
      sessionId, navigate, seed: '', initialProjectId: '', initialAgent: '', pendingRouting: null, setRoutingSuggestion: vi.fn(),
      setLiveRun, started: turns.length > 0, stream, followNewTurn, oweSpokenReply,
    })
    return { c, turns }
  })
  return { ...hook, navigate, setLiveRun, followNewTurn, oweSpokenReply, setSteered, markStreaming }
}

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.createChatSession.mockResolvedValue({ key: NEW_KEY })
  ;({ useChatComposer } = await import('./composer/useChatComposer'))
})

afterEach(() => { cleanup() })

const idOf = (call: unknown[]) => (call[5] as { messageId: string }).messageId

describe('a message sent again after its send failed', () => {
  it('carries the same id, and the page draws one bubble', async () => {
    h.sendChat
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockImplementationOnce(receiptFor('started', { duplicate: true }))
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.setInput('Plan the garden for spring'))
    await act(async () => { await m.result.current.c.send() })
    act(() => m.result.current.c.setInput('Plan the garden for spring'))
    await act(async () => { await m.result.current.c.send() })

    expect(h.sendChat).toHaveBeenCalledTimes(2)
    const [first, again] = h.sendChat.mock.calls
    expect(idOf(again), 'the same words sent again are the same message').toBe(idOf(first))
    const bubbles = m.result.current.turns.filter((t) => t.role === 'user')
    expect(bubbles.map((t) => t.messageId)).toEqual([idOf(first)])
  })

  it('names a new message once a send of the last one was answered', async () => {
    h.sendChat.mockImplementation(receiptFor('started'))
    const m = mount({ sessionId: NEW_KEY })
    for (const _ of [1, 2]) {
      act(() => m.result.current.c.setInput('yes'))
      await act(async () => { await m.result.current.c.send() })
    }
    const [first, second] = h.sendChat.mock.calls
    expect(idOf(second), 'a "yes" sent after the first was answered is another message').not.toBe(idOf(first))
    expect(m.result.current.turns.filter((t) => t.role === 'user')).toHaveLength(2)
  })

  it('keeps the time the chat took it in, which Edit & resend finds it by', async () => {
    h.sendChat.mockImplementation(receiptFor('started', { accepted_at: '2026-10-29T10:00:00+00:00' }))
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.setInput('Plan the garden for spring'))
    await act(async () => { await m.result.current.c.send() })
    const [bubble] = m.result.current.turns.filter((t) => t.role === 'user')
    expect(bubble.ts).toBe('2026-10-29T10:00:00+00:00')
  })
})
