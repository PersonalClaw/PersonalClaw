import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import { useState } from 'react'
import type { ChatSessionSummary } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'

// ── The chat history page's controller ──────────────────────────────────────────────────────────
//
// `useChatHistory` keeps what `#/chat/history` lists and does: the scope and search that narrow the
// list, a failed read told apart from an empty one, the bulk selection, and a row's pin and delete.
// Driven here against a URL it is handed, the way the page hands it one.

const h = vi.hoisted(() => ({
  chatSessions: vi.fn(),
  sessionsSearch: vi.fn(),
  pinChatSession: vi.fn(),
  bulkSessions: vi.fn(),
  deleteChatSession: vi.fn(),
  confirm: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const api = new Proxy({
    chatSessions: h.chatSessions, sessionsSearch: h.sessionsSearch, pinChatSession: h.pinChatSession,
    bulkSessions: h.bulkSessions, deleteChatSession: h.deleteChatSession,
    chatFolders: () => Promise.resolve([]), chatTags: () => Promise.resolve([]),
    rooms: () => Promise.resolve({ enabled: false }),
  } as Record<string, unknown>, {
    get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) },
  })
  return { ...real, api }
})
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))
vi.mock('../../../ui/dialog', () => ({ confirm: h.confirm, promptInput: vi.fn() }))
vi.mock('../../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

let useChatHistory: typeof import('./useChatHistory')['useChatHistory']

const row = (key: string, title: string, extra: Partial<ChatSessionSummary> = {}) =>
  ({ key, title, origin: 'manual', last_activity_ts: '2026-10-01T09:00:00Z', ...extra }) as ChatSessionSummary
const ROWS = [
  row('chat-1-feed', 'Why the feed refetches'),
  row('chat-2-loop', 'Nightly digest', { origin: 'loop', source_id: 'loop-1' }),
  row('chat-3-plan', 'Release plan', { last_activity_ts: '2026-09-01T09:00:00Z' }),
]

/** The page around the controller: a URL that holds the filters. */
function mount() {
  const navigate = vi.fn()
  const hook = renderHook(() => {
    const [query, setQ] = useState<Record<string, string>>({})
    const setQuery: RouteProps['setQuery'] = (patch) => setQ((prev) => {
      const next = { ...prev }
      for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
      return next
    })
    return { c: useChatHistory({ navigate, query, setQuery }), query }
  })
  return { ...hook, navigate }
}
const keys = (r: { current: { c: { filtered: ChatSessionSummary[] } } }) => r.current.c.filtered.map((s) => s.key)

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.chatSessions.mockResolvedValue(ROWS)
  h.sessionsSearch.mockResolvedValue({ sessions: [], source: 'index', searched: 3, complete: true, index: 'ready', matched: 0 })
  h.pinChatSession.mockResolvedValue({})
  h.confirm.mockResolvedValue(true)
  ;({ useChatHistory } = await import('./useChatHistory'))
})
afterEach(() => cleanup())

describe('useChatHistory narrows the list', () => {
  it('shows your own chats by default, and every chat on the All scope', async () => {
    const { result } = mount()
    await waitFor(() => expect(keys(result)).toEqual(['chat-1-feed', 'chat-3-plan']))
    expect(result.current.c.originCounts).toMatchObject({ manual: 2, loop: 1, all: 3 })
    act(() => result.current.c.setOrigin('all'))
    expect(result.current.query.origin).toBe('all')
    expect(keys(result)).toEqual(['chat-1-feed', 'chat-2-loop', 'chat-3-plan'])
  })

  it('says a failed read failed, rather than that there are no chats', async () => {
    h.chatSessions.mockRejectedValue(new Error('gateway restarted'))
    const { result } = mount()
    await waitFor(() => expect(result.current.c.sessionsError).toBeTruthy())
    expect(result.current.c.sessions).toBeNull()
  })

  it('matches a title at once, and a transcript once the content search answers', async () => {
    h.sessionsSearch.mockResolvedValue({
      sessions: [{ key: 'chat-3-plan', snippet: 'ship once the <<etag>> lands' }], source: 'index', searched: 3, complete: true, index: 'ready', matched: 1,
    })
    const { result } = mount()
    await waitFor(() => expect(keys(result)).toHaveLength(2))
    act(() => result.current.c.setQ('etag'))
    expect(result.current.query.q).toBe('etag')
    await waitFor(() => expect(keys(result)).toEqual(['chat-3-plan']))
    expect(h.sessionsSearch).toHaveBeenCalledWith('etag', expect.objectContaining({ rest: false }))
    expect(result.current.c.contentSnippets.get('chat-3-plan')).toBe('ship once the <<etag>> lands')
  })
})

describe('useChatHistory acts on rows', () => {
  it('pins a chat at once, puts it on top, and writes the pin', async () => {
    const { result } = mount()
    await waitFor(() => expect(keys(result)).toEqual(['chat-1-feed', 'chat-3-plan']))
    await act(async () => { await result.current.c.togglePin('chat-3-plan', true) })
    expect(keys(result)).toEqual(['chat-3-plan', 'chat-1-feed'])
    expect(h.pinChatSession).toHaveBeenCalledWith('chat-3-plan', true)
  })

  it('archives a selection, says how many changed, and clears the selection', async () => {
    h.bulkSessions.mockResolvedValue({ changed: ['chat-1-feed'], unchanged: ['chat-3-plan'], missing: [] })
    const { result } = mount()
    await waitFor(() => expect(keys(result)).toHaveLength(2))
    act(() => { result.current.c.toggleSelected('chat-1-feed'); result.current.c.toggleSelected('chat-3-plan') })
    expect(result.current.c.selecting).toBe(true)
    await act(async () => { await result.current.c.runBulk('archive') })
    expect(h.bulkSessions).toHaveBeenCalledWith('archive', ['chat-1-feed', 'chat-3-plan'], {})
    expect(result.current.c.bulkNote).toBe('Archived 1 · 1 already set')
    expect(result.current.c.selected.size).toBe(0)
  })

  it('says why a delete failed and keeps the chat', async () => {
    h.deleteChatSession.mockRejectedValue(new Error('session is running'))
    const { result } = mount()
    await waitFor(() => expect(keys(result)).toHaveLength(2))
    await act(async () => { await result.current.c.del(ROWS[0]) })
    expect(h.confirm).toHaveBeenCalledWith(expect.objectContaining({ title: 'Delete chat?', danger: true }))
    expect(h.notify).toHaveBeenCalledWith("Couldn't delete this chat: session is running", 'error')
    expect(keys(result)).toContain('chat-1-feed')
  })
})
