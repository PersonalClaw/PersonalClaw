import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { useChatSocket, type WsMessage } from './useChatSocket'

// One WebSocket per tab. Each `useChatSocket` call used to open its own, so an idle Home held
// eight and a chat six, every broadcast frame was serialised and written once per socket, and
// the chat's socket died with the chat: the session-create remount closed it, and the turn's
// terminal frames went to nothing (#3575).

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
const live = () => FakeSocket.all.filter((s) => !s.closed)

type Seen = { frames: string[]; status: boolean[]; reconnects: number }
function Consumer({ seen, throws = false }: { seen: Seen; throws?: boolean }) {
  useChatSocket(
    (m) => { seen.frames.push(m.type); if (throws) throw new Error('consumer bug') },
    () => { seen.reconnects += 1 },
    (connected) => { seen.status.push(connected) },
  )
  return null
}
const fresh = (): Seen => ({ frames: [], status: [], reconnects: 0 })

beforeEach(() => {
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
})
afterEach(() => { vi.unstubAllGlobals() })

describe('useChatSocket — one socket per tab', () => {
  it('shares ONE socket between every consumer, and each gets every frame once', () => {
    const a = fresh(), b = fresh(), c = fresh()
    render(<><Consumer seen={a} /><Consumer seen={b} /><Consumer seen={c} /></>)
    expect(FakeSocket.all, 'each consumer opened its own socket').toHaveLength(1)
    FakeSocket.all[0].open()
    FakeSocket.all[0].frame({ type: 'chat_chunk', data: {} })
    for (const s of [a, b, c]) {
      expect(s.frames).toEqual(['chat_chunk'])
      expect(s.status).toEqual([true])
    }
  })

  it('a consumer that throws does not take the frame from the others', () => {
    const bad = fresh(), good = fresh()
    render(<><Consumer seen={bad} throws /><Consumer seen={good} /></>)
    FakeSocket.all[0].open()
    FakeSocket.all[0].frame({ type: 'chat_done', data: {} })
    expect(good.frames).toEqual(['chat_done'])
  })

  it('tells a consumer that joins an open socket at once, and that is not a reconnect', () => {
    const first = fresh()
    const view = render(<Consumer seen={first} />)
    FakeSocket.all[0].open()
    const late = fresh()
    view.rerender(<><Consumer seen={first} /><Consumer seen={late} /></>)
    expect(FakeSocket.all).toHaveLength(1)
    expect(late.status).toEqual([true])
    expect(late.reconnects).toBe(0)
  })

  it('reports a drop, and on the reopen a reconnect to each consumer that had seen it open', async () => {
    vi.useFakeTimers()
    try {
      const before = fresh()
      const view = render(<Consumer seen={before} />)
      FakeSocket.all[0].open()
      FakeSocket.all[0].drop()
      expect(before.status).toEqual([true, false])
      // Joins during the outage: the reopen is its FIRST connect, as its own socket's would be.
      const during = fresh()
      view.rerender(<><Consumer seen={before} /><Consumer seen={during} /></>)
      expect(FakeSocket.all, 'a consumer joining an outage must wait for the one reconnect').toHaveLength(1)
      await act(async () => { vi.advanceTimersByTime(1_000) })
      expect(FakeSocket.all).toHaveLength(2)
      FakeSocket.all[1].open()
      expect(before.reconnects).toBe(1)
      expect(during.reconnects).toBe(0)
      expect(during.status).toEqual([true])
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps the socket across a remount, and closes it when the last consumer leaves', async () => {
    const seen = fresh()
    const view = render(<Consumer key="new" seen={seen} />)
    FakeSocket.all[0].open()
    // The session-create remount: one consumer unmounts and its replacement mounts in one commit.
    view.rerender(<Consumer key="chat-1" seen={seen} />)
    await act(async () => { await Promise.resolve() })
    expect(live(), 'the remount closed the socket the replacement needs').toHaveLength(1)
    expect(FakeSocket.all).toHaveLength(1)
    view.unmount()
    await act(async () => { await Promise.resolve() })
    expect(live(), 'a socket nobody listens to stayed open').toHaveLength(0)
  })
})
