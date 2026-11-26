import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { useState } from 'react'
import type { ChatSessionSummary } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'
import { memoryBackend, setStoreForTests, type StoreBackend, type StoreName } from '../../../lib/browserStore'
import { loadDraft, saveDraft } from '../../../lib/drafts'
import { clearChat, flush, hold, pending, type HeldSend } from '../../../lib/outbox'

// ── A chat's draft and its waiting sends are its own, and go with it (#4012's privacy rows) ─────
//
//   1. A saved draft and a waiting send never outlive their chat's delete: the history page's
//      Delete takes both, in this browser, once the gateway has deleted the chat, and leaves every
//      other chat's.
//   2. Nothing is written anywhere but the chat's own entries in this browser's store: no
//      localStorage, no sessionStorage, no request to the gateway, and every write is the chat's
//      own draft or a waiting send naming the chat.
//   3. An offline send goes exactly once: flushes at once in a page post it once, and a flush after
//      it went posts nothing.

const h = vi.hoisted(() => ({
  chatSessions: vi.fn(),
  deleteChatSession: vi.fn(),
  confirm: vi.fn(),
}))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const api = new Proxy({
    chatSessions: h.chatSessions, deleteChatSession: h.deleteChatSession, chatRunsGoing: () => Promise.resolve({ runs: 0 }),
    chatFolders: () => Promise.resolve([]), chatTags: () => Promise.resolve([]), rooms: () => Promise.resolve({ enabled: false }),
  } as Record<string, unknown>, { get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) } })
  return { ...real, api }
})
vi.mock('../../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../../ui/dialog', () => ({ confirm: h.confirm, promptInput: vi.fn() }))
vi.mock('../../../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))

const CHAT = { key: 'chat-9-private', title: 'Salary notes', origin: 'manual', last_activity_ts: '2026-11-24T09:00:00Z' } as ChatSessionSummary
const OTHER = 'chat-9-other'
const draft = (text: string) => ({ text, attachments: [], mentions: [], pastes: [] })
const waiting = (id: string, session: string) => ({ client_message_id: id, session, message: `sent offline in ${session}` })

function history() {
  return renderHook(() => {
    const [query, setQ] = useState<Record<string, string>>({})
    const setQuery: RouteProps['setQuery'] = (patch) => setQ((prev) => {
      const next = { ...prev }
      for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
      return next
    })
    return useChatHistory({ navigate: vi.fn(), query, setQuery })
  })
}
let useChatHistory: typeof import('./useChatHistory')['useChatHistory']

beforeEach(async () => {
  setStoreForTests(memoryBackend())
  for (const f of Object.values(h)) f.mockReset()
  h.chatSessions.mockResolvedValue([CHAT])
  h.deleteChatSession.mockResolvedValue({ ok: true })
  h.confirm.mockResolvedValue(true)
  ;({ useChatHistory } = await import('./useChatHistory'))
})
afterEach(() => { cleanup(); setStoreForTests(null); vi.restoreAllMocks() })

describe('1. a draft and a waiting send never outlive their chat', () => {
  beforeEach(async () => {
    await saveDraft(CHAT.key, draft('half a note on salaries'))
    await saveDraft(OTHER, draft('another chat'))
    await hold(waiting('msg_01eeeeeeeeeeeeeeeeeeeeeeee', CHAT.key), true)
    await hold(waiting('msg_01ffffffffffffffffffffffff', OTHER), true)
  })

  it("goes with the chat's Delete, and every other chat's stays", async () => {
    const { result } = history()
    await waitFor(() => expect(result.current.filtered.map((s) => s.key)).toContain(CHAT.key))
    await act(async () => { await result.current.del(CHAT) })
    await waitFor(async () => expect(await loadDraft(CHAT.key)).toBeNull())
    expect(await pending(CHAT.key)).toEqual([])
    expect((await loadDraft(OTHER))?.text).toBe('another chat')
    expect(await pending(OTHER)).toHaveLength(1)
  })

  it('stays while the chat could not be deleted', async () => {
    h.deleteChatSession.mockRejectedValue(new Error('the gateway could not remove it'))
    const { result } = history()
    await waitFor(() => expect(result.current.filtered.map((s) => s.key)).toContain(CHAT.key))
    await act(async () => { await result.current.del(CHAT) })
    expect((await loadDraft(CHAT.key))?.text).toBe('half a note on salaries')
    expect(await pending(CHAT.key)).toHaveLength(1)
  })
})

describe("2. nothing is written outside the chat's own store", () => {
  it('keeps a draft and a waiting send only as the chat\'s own entries in this browser', async () => {
    const writes: { store: StoreName; key: string; value: unknown }[] = []
    const inner = memoryBackend()
    const recording: StoreBackend = { ...inner, put: async (store, key, value) => { writes.push({ store, key, value }); await inner.put(store, key, value) } }
    setStoreForTests(recording)
    const local = vi.spyOn(Storage.prototype, 'setItem')
    const fetched = vi.spyOn(globalThis, 'fetch')

    await saveDraft(CHAT.key, draft('half a note on salaries'))
    await hold(waiting('msg_01gggggggggggggggggggggggg', CHAT.key), true)
    await clearChat(CHAT.key)

    expect(local).not.toHaveBeenCalled()
    expect(fetched).not.toHaveBeenCalled()
    expect(writes.map((w) => w.store)).toEqual(['drafts', 'outbox'])
    expect(writes[0].key).toBe(CHAT.key)
    expect((writes[1].value as { session: string }).session).toBe(CHAT.key)
  })

  it('the modules that keep them reach no gateway route', () => {
    // The code, its comments taken out: a comment may name a route it never calls.
    const src = (f: string) => readFileSync(join(process.cwd(), 'src', 'lib', f), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/ \/\/ .*$/gm, '')
    for (const f of ['browserStore.ts', 'drafts.ts']) expect(src(f)).not.toMatch(/from '\.\/api'|fetch\(|localStorage|sessionStorage/)
    // The outbox reads only the error type of an answer; it posts through the one it is handed.
    expect(src('outbox.ts')).toMatch(/^import \{ ApiError \} from '\.\/api'$/m)
    expect(src('outbox.ts')).not.toMatch(/\bapi\.|fetch\(|localStorage|sessionStorage/)
  })
})

describe('3. an offline send goes exactly once', () => {
  it('is posted once by flushes at once, and never again after it went', async () => {
    await hold(waiting('msg_01hhhhhhhhhhhhhhhhhhhhhhhh', CHAT.key), true)
    let answer: () => void = () => {}
    const post = vi.fn((_send: HeldSend) => new Promise((resolve) => { answer = () => resolve({ disposition: 'started' }) }))
    const first = flush(CHAT.key, post, async () => true)
    const second = flush(CHAT.key, post, async () => true)
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    answer()
    const [a, b] = await Promise.all([first, second])
    expect(a.sent).toHaveLength(1)
    expect(b.sent).toHaveLength(0)
    await flush(CHAT.key, post, async () => true)
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][0]).toMatchObject({ client_message_id: 'msg_01hhhhhhhhhhhhhhhhhhhhhhhh' })
  })
})
