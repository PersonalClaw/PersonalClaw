import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A failed chat action says so, and leaves the page as it was (F-40, F-41, F-42) ───────────
//
// Measured before the fix, through the real ChatPage:
//
//  · Edit & resend and Regenerate removed the turns they replace BEFORE the request went out.
//    A refusal (a turn already running) left the later turns gone from the page, and the only
//    word about it was a warning-sign line of prose in the assistant's voice carrying the raw
//    error. A failed send wrote the same line.
//  · A failed content search on the chat list fell back to title matches in silence.
//  · A second tab on the same chat got the reply's chunks painted onto the PREVIOUS answer, with
//    no question above them and no Stop control, because a sender adds its own question and the
//    server never echoes it.

const h = vi.hoisted(() => ({
  details: [] as unknown[],
  detailReads: 0,
  chatSessionDetail: vi.fn(),
  editResend: vi.fn(),
  regenerate: vi.fn(),
  sendChat: vi.fn(),
  stopChat: vi.fn(),
  sessionsSearch: vi.fn(),
  chatSessions: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../app/appSdk', async (orig) => ({ ...(await orig<typeof import('../../app/appSdk')>()), notify: h.notify }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    editResend: h.editResend,
    regenerate: h.regenerate,
    sendChat: h.sendChat,
    stopChat: h.stopChat,
    sessionsSearch: h.sessionsSearch,
    chatSessions: h.chatSessions,
    useCaseSettings: () => Promise.resolve({}),
    personalclawConfig: () => Promise.resolve({ voice: {} }),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    chatFolders: () => Promise.resolve([]),
    chatTags: () => Promise.resolve([]),
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

const SESSION = 'chat-40-x'
const deliver = (type: string, data: Record<string, unknown>) =>
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
let ApiError: typeof import('../../lib/api')['ApiError']
let DialogHost: typeof import('../../ui/dialog/DialogHost')['DialogHost']

const TWO_EXCHANGES = [
  { role: 'user', content: 'First question', ts: '2026-09-26T10:00:00Z' },
  { role: 'assistant', content: 'First answer', ts: '2026-09-26T10:00:05Z' },
  { role: 'user', content: 'Second question', ts: '2026-09-26T10:01:00Z' },
  { role: 'assistant', content: 'Second answer', ts: '2026-09-26T10:01:05Z' },
]
const detail = (messages: unknown[], extra: Record<string, unknown> = {}) => ({
  key: SESSION, title: SESSION, messages, running: false, queue: [], task_mode: 'agent',
  approval: 'normal', memory_mode: 'persistent', ...extra,
})

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
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
  localStorage.clear()
  FakeSocket.last = null
  h.details = [detail(TWO_EXCHANGES)]
  h.detailReads = 0
  h.chatSessionDetail.mockReset().mockImplementation(() => {
    const d = h.details[Math.min(h.detailReads, h.details.length - 1)]
    h.detailReads++
    return Promise.resolve(d)
  })
  h.editResend.mockReset()
  h.regenerate.mockReset()
  h.sendChat.mockReset()
  h.stopChat.mockReset().mockResolvedValue({ ok: true })
  h.sessionsSearch.mockReset()
  h.chatSessions.mockReset().mockResolvedValue([])
  h.notify.mockReset()
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
  ;({ ApiError } = await import('../../lib/api'))
  ;({ DialogHost } = await import('../../ui/dialog/DialogHost'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function openChat(query: Record<string, string> = {}) {
  const utils = render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={query} setQuery={() => {}} />
      <DialogHost />
    </AppearanceProvider>,
  )
  expect(await screen.findByText('Second answer')).toBeTruthy()
  await waitFor(() => expect(FakeSocket.last).not.toBeNull())
  return utils
}

const noWarningSign = (root: HTMLElement) => expect(root.textContent ?? '').not.toContain('⚠️')

describe('Edit & resend (F-40)', () => {
  it('leaves every later turn in place when the server refuses, and says why in the editor', async () => {
    const user = userEvent.setup()
    const { container } = await openChat()
    h.editResend.mockRejectedValue(new ApiError('session is running', 409))
    await user.click(screen.getAllByRole('button', { name: 'Edit & resend' })[0])
    const box = screen.getByRole('textbox', { name: 'Edit your message' })
    await user.clear(box)
    await user.type(box, 'First question, edited')
    await user.click(screen.getByRole('button', { name: 'Resend & replace' }))
    await waitFor(() => expect(h.editResend).toHaveBeenCalledTimes(1))
    await act(async () => { await Promise.resolve() })

    expect(screen.queryByText('Second question'), 'a refused edit removed the later turns').toBeTruthy()
    expect(screen.queryByText('Second answer')).toBeTruthy()
    expect(screen.queryByText('First answer')).toBeTruthy()
    expect(await screen.findByText("Couldn't resend your edited message: session is running")).toBeTruthy()
    expect((screen.getByRole('textbox', { name: 'Edit your message' }) as HTMLTextAreaElement).value)
      .toBe('First question, edited')
    noWarningSign(container)
  })

  it('shows the transcript the server cut once it accepts', async () => {
    const user = userEvent.setup()
    await openChat()
    h.editResend.mockResolvedValue({ ok: true, rewound: 3 })
    h.details.push(detail([{ role: 'user', content: 'First question, edited', ts: '2026-09-26T10:02:00Z' }], { running: true }))
    await user.click(screen.getAllByRole('button', { name: 'Edit & resend' })[0])
    const box = screen.getByRole('textbox', { name: 'Edit your message' })
    await user.clear(box)
    await user.type(box, 'First question, edited')
    await user.click(screen.getByRole('button', { name: 'Resend & replace' }))

    await waitFor(() => expect(screen.queryByText('Second answer')).toBeNull())
    expect(screen.queryByRole('textbox', { name: 'Edit your message' })).toBeNull()
    expect(screen.getByText('First question, edited')).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Stop' })).toBeTruthy()
  })

  it('leaves the turns in place when Rewind to here is refused, and says why', async () => {
    const user = userEvent.setup()
    const { container } = await openChat()
    h.editResend.mockRejectedValue(new ApiError('session is running', 409))
    await user.click(screen.getAllByRole('button', { name: 'Rewind to here' })[0])
    await user.click(await screen.findByRole('button', { name: 'Rewind' }))
    await waitFor(() => expect(h.notify).toHaveBeenCalled())
    expect(h.notify).toHaveBeenCalledWith("Couldn't rewind to this message: session is running", 'error')
    expect(screen.getByText('Second answer')).toBeTruthy()
    noWarningSign(container)
  })
})

describe('Regenerate (F-40)', () => {
  it('keeps the answer when the server refuses, and says why', async () => {
    const user = userEvent.setup()
    const { container } = await openChat()
    h.regenerate.mockRejectedValue(new ApiError('session is running', 409))
    await user.click(screen.getByRole('button', { name: 'Regenerate' }))
    await waitFor(() => expect(h.regenerate).toHaveBeenCalledTimes(1))
    await act(async () => { await Promise.resolve() })
    expect(screen.queryByText('Second answer'), 'a refused regenerate removed the answer').toBeTruthy()
    await waitFor(() => expect(h.notify).toHaveBeenCalled())
    expect(h.notify).toHaveBeenCalledWith("Couldn't regenerate this reply: session is running", 'error')
    noWarningSign(container)
  })
})

describe('Send (F-40)', () => {
  it('reports a failed send as the turn error with the platform sentence', async () => {
    const user = userEvent.setup()
    const { container } = await openChat({ seed: 'Third question' })
    h.sendChat.mockRejectedValue(new ApiError('the gateway is restarting', 503))
    await user.click(await screen.findByRole('button', { name: 'Send message' }))
    expect(await screen.findByText("Couldn't send this message: the gateway is restarting")).toBeTruthy()
    noWarningSign(container)
  })
})

describe('A second tab on the same chat (F-42)', () => {
  it('shows the question and Stop for a turn another tab started', async () => {
    await openChat()
    const readsAtRest = h.detailReads
    h.details.push(detail([
      ...TWO_EXCHANGES,
      { role: 'user', content: 'Asked in the other tab', ts: '2026-09-26T10:03:00Z' },
      { role: 'streaming', content: 'The answer so far', ts: '2026-09-26T10:03:01Z' },
    ], { running: true, stream_seq: 1 }))
    act(() => { deliver('chat_chunk', { content: 'The answer so far', seq: 1 }) })
    await waitFor(() => expect(h.detailReads).toBe(readsAtRest + 1))
    expect(await screen.findByText('Asked in the other tab')).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Stop' })).toBeTruthy()
    act(() => { deliver('chat_chunk', { content: ', and the rest.', seq: 2 }) })
    paintFrames()
    await waitFor(() => expect(screen.getByText(/The answer so far, and the rest\./)).toBeTruthy())
    // Painted once: the chunk the snapshot already held is not added again.
    expect(screen.queryByText(/The answer so farThe answer so far/)).toBeNull()
    // The previous answer is untouched — the old defect painted the new chunks onto it.
    expect(screen.getByText('Second answer')).toBeTruthy()
  })

  it('does not re-read for the last frames of a turn this tab stopped', async () => {
    const user = userEvent.setup()
    await openChat({ seed: 'Stop me' })
    h.sendChat.mockResolvedValue({ ok: true, session: SESSION })
    await user.click(await screen.findByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    await user.click(await screen.findByRole('button', { name: 'Stop' }))
    const readsAfterStop = h.detailReads
    act(() => { deliver('chat_chunk', { content: 'a late chunk', seq: 1 }) })
    await new Promise((r) => setTimeout(r, 50))
    expect(h.detailReads).toBe(readsAfterStop)
  })
})

describe('Chat history content search (F-41)', () => {
  it('says the content search failed instead of quietly matching titles', async () => {
    const user = userEvent.setup()
    h.chatSessions.mockResolvedValue([{ key: 'chat-1', title: 'Trip planning', updated_at: '2026-09-26T10:00:00Z', origin: 'manual' }])
    h.sessionsSearch.mockRejectedValueOnce(new ApiError('search index unavailable', 503))
      .mockResolvedValue({ sessions: [], source: 'index' })
    // The list keeps its search in the URL, so the page needs a query that actually updates.
    function History() {
      const [query, setQuery] = useState<Record<string, string>>({})
      const patch = (p: Record<string, string | null | undefined>) => setQuery((q) => {
        const next = { ...q }
        for (const [k, v] of Object.entries(p)) { if (v == null || v === '') delete next[k]; else next[k] = v }
        return next
      })
      return <ChatPage sub="history" navigate={() => {}} query={query} setQuery={patch} />
    }
    render(<AppearanceProvider><History /></AppearanceProvider>)
    await user.type(await screen.findByRole('searchbox', { name: 'Search chats' }), 'budget')
    const line = await screen.findByText(/Couldn't search inside your chats: search index unavailable\./, {}, { timeout: 3000 })
    expect(line.textContent).toContain('Only titles and previews are matched below.')
    await user.click(within(line.closest('div')!).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(h.sessionsSearch).toHaveBeenCalledTimes(2), { timeout: 3000 })
    await waitFor(() => expect(screen.queryByText(/Couldn't search inside your chats/)).toBeNull())
  })

  it('says how many chats it searched while the index is built, and reads the rest when asked', async () => {
    // Ledger 271, measured before: with the index 200 chats into a rebuild of 12,005, a word 240
    // chats say answered 4 — and the list showed those 4 as the whole answer.
    const user = userEvent.setup()
    h.chatSessions.mockResolvedValue([
      { key: 'chat-1', title: 'Trip planning', updated_at: '2026-09-26T10:00:00Z', origin: 'manual' },
      { key: 'chat-2', title: 'Budget review', updated_at: '2026-09-26T11:00:00Z', origin: 'manual' },
    ])
    const building = { indexed: 3210, of: 12005, building: true, long: 0 }
    h.sessionsSearch.mockImplementation((_q: string, opts?: { rest?: boolean }) => Promise.resolve(opts?.rest
      ? { sessions: [{ key: 'dashboard_chat-1', snippet: 'the <<zebra>> crossing' }], source: 'index+scan', searched: { chats: 12005, of: 12005 }, complete: true, index: building }
      : { sessions: [], source: 'index', searched: { chats: 3210, of: 12005 }, complete: false, index: building }))
    function History() {
      const [query, setQuery] = useState<Record<string, string>>({})
      const patch = (p: Record<string, string | null | undefined>) => setQuery((q) => {
        const next = { ...q }
        for (const [k, v] of Object.entries(p)) { if (v == null || v === '') delete next[k]; else next[k] = v }
        return next
      })
      return <ChatPage sub="history" navigate={() => {}} query={query} setQuery={patch} />
    }
    render(<AppearanceProvider><History /></AppearanceProvider>)
    await user.type(await screen.findByRole('searchbox', { name: 'Search chats' }), 'zebra')
    const notice = await screen.findByText(
      /^Searched 3,210 of 12,005 chats — the search index is still being built, so matches in the other 8,795 are not listed yet\./,
      {}, { timeout: 3000 },
    )
    expect(notice.getAttribute('data-partial')).toBe('true')
    await user.click(within(notice).getByRole('button', { name: 'Search the other 8,795 directly' }))
    await waitFor(() => expect(h.sessionsSearch).toHaveBeenLastCalledWith('zebra', { rest: true, limit: 200 }), { timeout: 3000 })
    await waitFor(() => expect(screen.queryByText(/^Searched 3,210 of 12,005 chats/)).toBeNull(), { timeout: 3000 })
    expect(await screen.findByText('matched via index and scanned transcripts')).toBeTruthy()
    expect(await screen.findByText('Trip planning')).toBeTruthy()
    expect(screen.queryByText('Budget review')).toBeNull()
  })

  it('says when more chats matched than the search lists', async () => {
    const user = userEvent.setup()
    h.chatSessions.mockResolvedValue([
      { key: 'chat-1', title: 'Trip planning', updated_at: '2026-09-26T10:00:00Z', origin: 'manual' },
    ])
    h.sessionsSearch.mockResolvedValue({
      sessions: [{ key: 'dashboard_chat-1', snippet: 'the <<zebra>> crossing' }], source: 'index',
      searched: { chats: 12005, of: 12005 }, complete: true, index: { indexed: 12005, of: 12005, building: false, long: 0 }, matched: 240,
    })
    function History() {
      const [query, setQuery] = useState<Record<string, string>>({})
      const patch = (p: Record<string, string | null | undefined>) => setQuery((q) => {
        const next = { ...q }
        for (const [k, v] of Object.entries(p)) { if (v == null || v === '') delete next[k]; else next[k] = v }
        return next
      })
      return <ChatPage sub="history" navigate={() => {}} query={query} setQuery={patch} />
    }
    render(<AppearanceProvider><History /></AppearanceProvider>)
    await user.type(await screen.findByRole('searchbox', { name: 'Search chats' }), 'zebra')
    const notice = await screen.findByText(/^Showing 1 of 240 matching chats — the search lists its 1 best/, {}, { timeout: 3000 })
    expect(notice.getAttribute('data-partial')).toBe('true')
    expect(screen.queryByText(/^Searched /)).toBeNull()
  })
})
