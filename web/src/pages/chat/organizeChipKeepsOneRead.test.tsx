import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, cleanup, act, screen } from '@testing-library/react'

// ── The organize chip keeps one read per chat on the wire ─────────────────────────────────────
//
// The chip re-reads its proposal after every turn. It used to start a fresh read each turn and only
// stop LISTENING to the read before (`live = false`), so a slow read kept its connection to the end.
// A browser keeps six HTTP/1.1 connections to the gateway for every tab together; seven organize
// reads held them all, and the chat's own send waited 138 s inside the browser. Now a newer turn
// aborts the read that is out, leaving the chat aborts the last, and the model's answer, pushed as
// a `chat_organize` frame naming the chat, is read once.

const h = vi.hoisted(() => ({
  reads: [] as Array<{ session: string; signal: AbortSignal | undefined; resolve: (v: unknown) => void }>,
  socket: {
    onMessage: null as null | ((m: { type: string; data: Record<string, unknown> }) => void),
    onReconnect: null as null | (() => void),
  },
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => {
        if (key === 'organizeSuggestion') {
          return (session: string, opts: { signal?: AbortSignal } = {}) =>
            new Promise((resolve) => { h.reads.push({ session, signal: opts.signal, resolve }) })
        }
        return (target as Record<string | symbol, unknown>)[key]
      },
    }),
  }
})

vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<typeof import('../../lib/useChatSocket')>()),
  useChatSocket: (onMessage: typeof h.socket.onMessage, onReconnect: typeof h.socket.onReconnect) => {
    h.socket.onMessage = onMessage
    h.socket.onReconnect = onReconnect
  },
}))

import { OrganizeChip } from './OrganizeChip'

const live = () => h.reads.filter((r) => !r.signal?.aborted)
const proposal = (folder: string) => ({
  proposal: { session: 'chat-1', folder_id: 'f-1', folder_name: folder, tags: [], source: 'llm', reason: '' },
  pending: false,
})

beforeEach(() => { h.reads.length = 0; h.socket.onMessage = null; h.socket.onReconnect = null })
afterEach(() => cleanup())

describe('the organize chip keeps one read per chat on the wire', () => {
  it('a newer turn aborts the read that is out, so only one is ever live', () => {
    const { rerender } = render(<OrganizeChip sessionKey="chat-1" refreshKey={0} />)
    expect(h.reads).toHaveLength(1)
    for (let turn = 1; turn <= 5; turn++) {
      rerender(<OrganizeChip sessionKey="chat-1" refreshKey={turn} />)
      expect(h.reads[turn - 1].signal?.aborted, `turn ${turn} left the read before it on the wire`).toBe(true)
      expect(live()).toHaveLength(1)
    }
    expect(h.reads).toHaveLength(6)
  })

  it('leaving the chat aborts the read that is out', () => {
    const { unmount } = render(<OrganizeChip sessionKey="chat-1" refreshKey={0} />)
    expect(live()).toHaveLength(1)
    unmount()
    expect(live(), 'an unmounted chip still holds a connection').toHaveLength(0)
  })

  it('a re-render that is not a newer turn starts no read', () => {
    const { rerender } = render(<OrganizeChip sessionKey="chat-1" refreshKey={3} />)
    rerender(<OrganizeChip sessionKey="chat-1" refreshKey={3} />)
    rerender(<OrganizeChip sessionKey="chat-1" refreshKey={3} onApplied={() => {}} />)
    expect(h.reads).toHaveLength(1)
  })

  it("an aborted read's late answer is never shown", async () => {
    const { rerender } = render(<OrganizeChip sessionKey="chat-1" refreshKey={0} />)
    rerender(<OrganizeChip sessionKey="chat-1" refreshKey={1} />)
    await act(async () => { h.reads[0].resolve(proposal('Archive')) })
    expect(screen.queryByRole('status')).toBeNull()
    await act(async () => { h.reads[1].resolve(proposal('Research')) })
    expect(screen.getByRole('status').textContent).toContain('Organize this chat under Research')
  })

  it("the model's answer is read when its frame names this chat, and only then", async () => {
    render(<OrganizeChip sessionKey="chat-1" refreshKey={0} />)
    await act(async () => { h.reads[0].resolve({ proposal: null, pending: true }) })
    expect(screen.queryByRole('status')).toBeNull()
    expect(h.socket.onMessage, 'the chip does not listen for its answer').not.toBeNull()

    act(() => h.socket.onMessage?.({ type: 'chat_organize', data: { session: 'chat-2' } }))
    act(() => h.socket.onMessage?.({ type: 'chat_organize', data: {} }))
    act(() => h.socket.onMessage?.({ type: 'chat_followups', data: { session: 'chat-1' } }))
    expect(h.reads, "another chat's frame, or none, started a read").toHaveLength(1)

    act(() => h.socket.onMessage?.({ type: 'chat_organize', data: { session: 'chat-1' } }))
    expect(h.reads).toHaveLength(2)
    await act(async () => { h.reads[1].resolve(proposal('Research')) })
    expect(screen.getByRole('status').textContent).toContain('Organize this chat under Research')
  })

  it('reads again when this chat is titled, since an untitled chat has nothing to be sorted by', () => {
    render(<OrganizeChip sessionKey="chat-1" refreshKey={0} />)
    act(() => h.socket.onMessage?.({ type: 'session_title', data: { key: 'chat-2', title: 'Budget review' } }))
    expect(h.reads).toHaveLength(1)
    act(() => h.socket.onMessage?.({ type: 'session_title', data: { key: 'chat-1', title: 'Quarterly planning' } }))
    expect(h.reads).toHaveLength(2)
    expect(h.reads[0].signal?.aborted).toBe(true)
  })

  it('a reconnect reads again, aborting a read that is out', () => {
    render(<OrganizeChip sessionKey="chat-1" refreshKey={0} />)
    act(() => h.socket.onReconnect?.())
    expect(h.reads).toHaveLength(2)
    expect(h.reads[0].signal?.aborted).toBe(true)
    expect(live()).toHaveLength(1)
  })
})
