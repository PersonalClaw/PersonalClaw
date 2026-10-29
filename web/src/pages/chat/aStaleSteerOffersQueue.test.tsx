import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useState } from 'react'
import { ApiError } from '../../lib/api'
import { userTurn, assistantTurn, type ChatTurn } from './chatTypes'
import type { ChatStream } from './useChatStream'
import { receiptFor } from '../../test/chatReceipt'

// ── A steer for a turn that is not running offers to send or queue it ──────────────────────────
//
// The composer says Steer while it believes a turn runs, and the belief can be stale: the turn may
// have ended, or another begun, before the steer arrives. The steer names the turn it is for (the
// message that started it), and the gateway refuses one whose turn is not the running one, keeping
// nothing. The page keeps the draft, says why in the gateway's words, and offers what to do instead:
// Queue behind the turn that runs, or Send when none does.

const NEW_KEY = 'chat-6-stale'

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


const ASKED_ID = 'msg_01jaa0000000000000000000ab'
const running = () => mount({
  sessionId: NEW_KEY,
  turns: [{ ...userTurn('Fix the loader'), messageId: ASKED_ID }, assistantTurn('Working…')],
  streaming: true,
  takesSteers: true,
})
const refused = (code: string, said: string) => new ApiError(said, 409, code)

describe('a steer whose turn is not the running one', () => {
  it('names its turn, keeps the draft, and offers Queue', async () => {
    h.sendChat
      .mockRejectedValueOnce(refused('turn_not_current', 'That turn is no longer running, so nothing was changed.'))
      .mockImplementationOnce(receiptFor('queued'))
    const m = running()
    act(() => m.result.current.c.setInput('use the ETag too'))
    await act(async () => { await m.result.current.c.send() })

    const [text, , , mode, , ids] = h.sendChat.mock.calls[0]
    expect([text, mode]).toEqual(['use the ETag too', 'steer'])
    expect(ids.expectedMessageId, 'a steer names the turn it is for').toBe(ASKED_ID)
    expect(m.result.current.c.input, 'nothing was sent, so the draft stays').toBe('use the ETag too')
    const notice = m.result.current.c.notice.notice
    expect(notice?.text).toBe('That turn is no longer running, so nothing was changed.')
    expect(notice?.action?.label).toBe('Queue')
    expect(m.setSteered).not.toHaveBeenCalled()

    await act(async () => { notice?.action?.run() })
    await vi.waitFor(() => expect(h.sendChat).toHaveBeenCalledTimes(2))
    const [, , , queued, , queuedIds] = h.sendChat.mock.calls[1]
    expect(queued).toBe('followup')
    expect(queuedIds.expectedMessageId, 'queued, it is for no turn in particular').toBeUndefined()
    expect(queuedIds.messageId, 'and it is still the same message').toBe(ids.messageId)
    await vi.waitFor(() => expect(m.result.current.c.input).toBe(''))
  })

  it('offers Queue for a turn that takes no steer', async () => {
    h.sendChat.mockRejectedValueOnce(refused('turn_not_steerable', 'This turn cannot take a message while it runs; queue it instead.'))
    const m = running()
    act(() => m.result.current.c.setInput('and the docs'))
    await act(async () => { await m.result.current.c.send() })
    expect(m.result.current.c.notice.notice?.action?.label).toBe('Queue')
  })

  it('offers Send when no turn is running, and sends it as a message', async () => {
    h.sendChat
      .mockRejectedValueOnce(refused('no_running_turn', 'No turn is running in this chat, so there was nothing to steer; send it as a message.'))
      .mockImplementationOnce(receiptFor('started'))
    const m = running()
    act(() => m.result.current.c.setInput('use the ETag too'))
    await act(async () => { await m.result.current.c.send() })
    const notice = m.result.current.c.notice.notice
    expect(notice?.action?.label).toBe('Send')

    await act(async () => { notice?.action?.run() })
    await vi.waitFor(() => expect(h.sendChat).toHaveBeenCalledTimes(2))
    const [, , , mode, , ids] = h.sendChat.mock.calls[1]
    expect(mode, 'a message, not a steer').toBeUndefined()
    expect(ids.expectedMessageId).toBeUndefined()
    // Its receipt says it started a turn: its bubble is drawn, and a stream opens for its reply.
    await vi.waitFor(() => expect(m.result.current.turns.filter((t) => t.role === 'user').map((t) => t.messageId)).toEqual([ASKED_ID, ids.messageId]))
    expect(m.markStreaming).toHaveBeenCalledWith(true)
  })
})
