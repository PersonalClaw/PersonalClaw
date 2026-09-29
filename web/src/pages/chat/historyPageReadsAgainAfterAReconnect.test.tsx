import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import type { ChatSessionSummary, ChatTag, RetagJob } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'

// ── The chat history page reads again what went by while its socket was down ──────────────────────
//
// A restart drops every socket and ends every reply and every re-tag job, and the frames that say
// so go to nobody. The history page's peek kept the cut-off reply's first words as a reply still
// being written, under a composer that stayed busy, and the Generate Tags control kept counting a
// job that no longer existed. Both are read again when the socket comes back. Drives the tab's real
// shared socket through a drop and a reopen.

const RESTARTED = 'The gateway restarted before this reply finished. Send your message again to retry.'
const TAGS: ChatTag[] = [{ id: 't0', name: 'Pricing', order: 0 }]
const SESSION: ChatSessionSummary = {
  key: 's1', title: 'Quick sums', messages: 1, tags: ['t0'], origin: 'manual', lifecycle: 'active',
}

const h = vi.hoisted(() => ({
  detail: vi.fn(),
  retagStatus: vi.fn(),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      chatSessions: () => Promise.resolve([SESSION]),
      chatFolders: () => Promise.resolve([]),
      chatTags: () => Promise.resolve(TAGS),
      rooms: () => Promise.resolve({ rooms: [] }),
      chatSessionDetail: h.detail,
      retagStatus: h.retagStatus,
    },
  }
})

import { ChatPage } from '../ChatPage'

class FakeSocket {
  static all: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeSocket.all.push(this) }
  close(): void { this.closed = true }
  open(): void { act(() => { this.onopen?.() }) }
  frame(m: WsMessage): void { act(() => { this.onmessage?.({ data: JSON.stringify(m) }) }) }
  drop(): void { act(() => { this.onclose?.() }) }
}
const latest = () => FakeSocket.all[FakeSocket.all.length - 1]

/** The gateway goes away and comes back. `meanwhile` is what is stored by the time the tab is
 *  listening again. */
async function restart(meanwhile: () => void) {
  const before = FakeSocket.all.length
  latest().drop()
  meanwhile()
  await waitFor(() => expect(FakeSocket.all.length, 'the tab never reconnected').toBe(before + 1))
  latest().open()
}

const question = { role: 'user', content: "What's 2+2?", ts: '2026-09-29T10:00:00Z' }
const idle: RetagJob = { status: 'idle' }

function historyPage(query: Record<string, string> = {}) {
  render(<ChatPage sub="history" navigate={() => {}} query={query} setQuery={() => {}} />)
}

beforeEach(() => {
  sessionStorage.clear()
  // The peek keeps its tail in view; jsdom lays nothing out.
  Element.prototype.scrollIntoView = vi.fn()
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  h.detail.mockReset()
  h.retagStatus.mockReset()
  h.retagStatus.mockResolvedValue(idle)
})
afterEach(async () => {
  cleanup()
  await act(async () => { await Promise.resolve() })  // the last consumer's socket closes
  vi.unstubAllGlobals()
})

describe('the peek of a chat whose reply a restart cut off', () => {
  it('reads the chat again: the partial reply goes, and what ended it shows', async () => {
    h.detail.mockResolvedValue({ key: 's1', title: 'Quick sums', messages: [question], running: true })
    historyPage({ peek: 's1' })
    await waitFor(() => expect(FakeSocket.all.length).toBeGreaterThan(0))
    latest().open()
    await screen.findByText("What's 2+2?")
    latest().frame({ type: 'chat_chunk', data: { session: 's1', content: 'Two plus two' } })
    expect(screen.getByText('Two plus two')).toBeTruthy()

    await restart(() => h.detail.mockResolvedValue({
      key: 's1', title: 'Quick sums', running: false,
      messages: [question, { role: 'error', content: RESTARTED, ts: '2026-09-29T10:00:04Z' }],
    }))

    await waitFor(() => expect(screen.queryByText(RESTARTED),
      'the peek does not say the restart cut the reply off').toBeTruthy())
    expect(screen.queryByText('Two plus two'), 'the cut-off words still read as a reply being written')
      .toBeNull()
  })
})

describe('a re-tag job the restart ended', () => {
  it('stops counting, and Generate Tags is offered again', async () => {
    h.detail.mockResolvedValue({ key: 's1', title: 'Quick sums', messages: [question], running: false })
    h.retagStatus.mockResolvedValue({ status: 'running', done: 3, total: 10, updated: 1 })
    historyPage()
    await waitFor(() => expect(FakeSocket.all.length).toBeGreaterThan(0))
    latest().open()
    expect(await screen.findByRole('button', { name: /Generating 3\/10/ })).toBeTruthy()

    await restart(() => h.retagStatus.mockResolvedValue(idle))

    await waitFor(() => expect(screen.queryByRole('button', { name: /Generating/ }),
      'the control still counts a job that ended with the gateway').toBeNull())
    expect(screen.getByRole('button', { name: 'Generate Tags' })).toBeTruthy()
  })
})
