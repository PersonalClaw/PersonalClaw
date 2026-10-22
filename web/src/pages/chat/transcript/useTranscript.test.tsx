import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useState } from 'react'
import { userTurn, assistantTurn, type ChatTurn } from '../chatTypes'
import type { ComposerValue } from '../../../ui/composer/types'

// ── The transcript's controller ─────────────────────────────────────────────────────────────────
//
// `useTranscript` holds what the chat's transcript region keeps and does: the find bar and its
// deep link, the Session Map's open state, and the actions a turn offers. Driven here without the
// page, against a stream it is handed, the way ChatSession hands it one.

const SESSION = 'chat-4-transcript'

const h = vi.hoisted(() => ({
  approve: vi.fn(),
  setTaskMode: vi.fn(),
  editResend: vi.fn(),
  regenerate: vi.fn(),
  chatSessionDetail: vi.fn(),
  editSessionBookmarks: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const api = new Proxy({
    approve: h.approve, setTaskMode: h.setTaskMode, editResend: h.editResend,
    regenerate: h.regenerate, chatSessionDetail: h.chatSessionDetail, editSessionBookmarks: h.editSessionBookmarks,
  } as Record<string, unknown>, {
    get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) },
  })
  return { ...real, api }
})
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))

let useTranscript: typeof import('./useTranscript')['useTranscript']

const TURNS: ChatTurn[] = [userTurn('Why does the feed refetch?', '2026-10-01T09:00:00Z'), assistantTurn('It ignores the validators.')]

/** The page around the controller: the URL, the composer's selection, a stream that holds turns. */
function mount({ sessionId = SESSION as string | null, query = {} as Record<string, string>, send = vi.fn(() => Promise.resolve()) } = {}) {
  const setQuery = vi.fn()
  const notice = { notice: null, showError: vi.fn(), showInfo: vi.fn(), clear: vi.fn() }
  const markStreaming = vi.fn()
  const readSnapshot = vi.fn(() => Promise.reject(new Error('offline')))
  const hook = renderHook(() => {
    const [turns, setTurns] = useState<ChatTurn[]>(TURNS)
    const [selection, setSelection] = useState<ComposerValue>({ agent: 'researcher', model: 'Auto', approval: 'normal', taskMode: 'ask', reasoning: '' })
    const [bookmarks, setBookmarks] = useState<readonly string[]>([])
    const t = useTranscript({
      sessionId, query, setQuery, navigate: vi.fn(),
      stream: {
        sessionRef: { current: sessionId }, turns, setTurns, streaming: false, streamingRef: { current: false },
        markStreaming, dropTextRun: vi.fn(), readSnapshot, adoptSnapshot: vi.fn(), bookmarks, setBookmarks,
      },
      started: turns.length > 0, notice, memoryMode: 'persistent', setSelection, agent: selection.agent, send,
    })
    return { t, turns, selection, bookmarks }
  })
  return { ...hook, setQuery, notice, markStreaming, send }
}

const cmdF = () => act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', metaKey: true, bubbles: true })) })

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.approve.mockResolvedValue({})
  h.setTaskMode.mockResolvedValue({})
  ;({ useTranscript } = await import('./useTranscript'))
})
afterEach(() => cleanup())

describe('useTranscript keeps the find bar', () => {
  it('opens and closes it on ⌘F while a session is open', () => {
    const { result } = mount()
    expect(result.current.t.findOpen).toBe(false)
    cmdF()
    expect(result.current.t.findOpen).toBe(true)
    cmdF()
    expect(result.current.t.findOpen).toBe(false)
  })

  it('leaves ⌘F to the browser on a new chat', () => {
    const { result } = mount({ sessionId: null })
    cmdF()
    expect(result.current.t.findOpen).toBe(false)
  })

  it('opens seeded from a ?find deep link, and drops the param', () => {
    const { result, setQuery } = mount({ query: { find: 'validators' } })
    expect(result.current.t.findOpen).toBe(true)
    expect(result.current.t.findSeed).toBe('validators')
    expect(setQuery).toHaveBeenCalledWith({ find: null }, { replace: true })
  })
})

describe('useTranscript keeps the Session Map', () => {
  it('is open by default on a pointer device and writes a close to the URL', () => {
    const { result, setQuery } = mount()
    expect(result.current.t.mapOpen).toBe(true)
    act(() => result.current.t.setMapOpen(false))
    expect(setQuery).toHaveBeenCalledWith({ map: '0' }, undefined)
  })

  it('reads a deliberate close back from the URL', () => {
    expect(mount({ query: { map: '0' } }).result.current.t.mapOpen).toBe(false)
  })

  it('derives one entry per message she sent', () => {
    expect(mount().result.current.t.sessionEntries).toHaveLength(1)
  })
})

describe('useTranscript keeps the bookmarks', () => {
  it('🔑 shows a bookmark at once, keeps what the gateway stored, and says nothing more', async () => {
    h.editSessionBookmarks.mockResolvedValue({ ok: true, bookmarks: ['2026-10-01T09:00:00Z'] })
    const { result } = mount()
    const entry = result.current.t.sessionEntries[0]
    expect(entry.bookmarked).toBe(false)
    act(() => { result.current.t.toggleBookmark(entry) })
    // Shown before the gateway answers.
    expect(result.current.t.sessionEntries[0].bookmarked).toBe(true)
    expect(h.editSessionBookmarks).toHaveBeenCalledWith(SESSION, { add: ['2026-10-01T09:00:00Z'] })
    await act(async () => { await Promise.resolve() })
    expect(result.current.bookmarks).toEqual(['2026-10-01T09:00:00Z'])
  })

  it('a refusal puts the map back and says so in the composer’s notice', async () => {
    h.editSessionBookmarks.mockRejectedValue(new Error('the gateway is busy'))
    const { result, notice } = mount()
    act(() => { result.current.t.toggleBookmark(result.current.t.sessionEntries[0]) })
    expect(result.current.t.sessionEntries[0].bookmarked).toBe(true)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(result.current.t.sessionEntries[0].bookmarked).toBe(false)
    expect(notice.showError).toHaveBeenCalledWith('Couldn’t bookmark that message: the gateway is busy')
  })
})

describe('useTranscript does what a turn offers', () => {
  it('moves the Permission pill only once the decision lands', async () => {
    const { result } = mount()
    await act(async () => { result.current.t.approve('req-1', 'trust') })
    expect(h.approve).toHaveBeenCalledWith(SESSION, 'trust', 'req-1')
    expect(result.current.selection.approval).toBe('trust')
  })

  it('tells the card whether its decision landed: true once it did, and not when it failed', async () => {
    const { result } = mount()
    let landed: unknown
    await act(async () => { landed = await result.current.t.approve('req-1', 'approved') })
    expect(landed).toBe(true)
    h.approve.mockRejectedValue(new Error('gateway restarted'))
    await act(async () => { landed = await result.current.t.approve('req-1', 'approved') })
    expect(landed).not.toBe(true)
  })

  it('leaves the pill where it was when the decision fails', async () => {
    h.approve.mockRejectedValue(new Error('gateway restarted'))
    const { result } = mount()
    await act(async () => { result.current.t.approve('req-1', 'yolo') })
    expect(result.current.selection.approval).toBe('normal')
  })

  it('switches to Agent and runs the continuation, and stops when the switch fails', async () => {
    const ok = mount()
    await act(async () => { await ok.result.current.t.switchToAgentAndRun('Write the fix.') })
    expect(h.setTaskMode).toHaveBeenCalledWith('agent', SESSION)
    expect(ok.send).toHaveBeenCalledWith('Write the fix.')
    cleanup()

    h.setTaskMode.mockRejectedValue(new Error('refused'))
    const failed = mount()
    await act(async () => { await failed.result.current.t.switchToAgentAndRun('Write the fix.') })
    expect(failed.send).not.toHaveBeenCalled()
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("Couldn't switch this session to Agent"), 'error')
  })

  it('keeps the transcript and the editor when an edit is refused, and says why in the editor', async () => {
    h.editResend.mockRejectedValue(new Error('A turn is already running'))
    const { result, markStreaming } = mount()
    act(() => result.current.t.setEditingTurn(0))
    await act(async () => { await result.current.t.editResend(0, 'Why does the feed refetch at all?') })
    expect(result.current.turns).toBe(TURNS)
    expect(result.current.t.editingTurn).toBe(0)
    expect(result.current.t.editFailure).toMatch(/A turn is already running/)
    expect(markStreaming).not.toHaveBeenCalled()
  })

  it('regenerates once the gateway accepts, and cuts the page copy when the read that follows fails', async () => {
    h.regenerate.mockResolvedValue({})
    const { result, markStreaming } = mount()
    await act(async () => { await result.current.t.regenerate() })
    expect(markStreaming).toHaveBeenCalledWith(true)
    await act(async () => { await Promise.resolve() })
    expect(result.current.turns.map((t) => t.role), 'the last answer goes; the reply streams in beneath its question').toEqual(['user'])
  })
})
