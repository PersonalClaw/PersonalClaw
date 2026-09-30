import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import type { WsMessage } from '../lib/useChatSocket'

// The incident banner follows the kill switch the moment the gateway says it moved.
//
// Measured on a tab left alone: the banner appeared 24 s after `personalclaw incident on` and
// stayed 77 s after `off`. It polled through `useVisiblePoll`, whose idle back-off stretches an
// interval 4x after 30 s without input and 10x after five minutes — right for a list, wrong for
// the one control that says unattended work has stopped. The gateway now watches the switch (the
// CLI's flips included) and sends a `refresh` frame naming `incident`; the banner re-reads on it,
// and on a reconnect, since a frame sent while the socket was down is lost.

const incident = vi.fn()
vi.mock('../lib/api', () => ({
  api: {
    incident: () => incident(),
    incidentResume: () => Promise.resolve({ active: false }),
  },
}))
vi.mock('./appSdk', () => ({ notify: () => {} }))

class FakeSocket {
  static all: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeSocket.all.push(this) }
  close(): void { this.closed = true }
}
const socket = () => FakeSocket.all.filter((s) => !s.closed).at(-1)!

async function frame(m: WsMessage) {
  await act(async () => { socket().onmessage?.({ data: JSON.stringify(m) }) })
}

let active = false
beforeEach(() => {
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  active = false
  incident.mockReset()
  incident.mockImplementation(() => Promise.resolve({ active, reason: active ? 'a drill' : '', started_at: '' }))
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function mountBanner() {
  const { IncidentBanner } = await import('./IncidentBanner')
  render(<IncidentBanner />)
  await act(async () => { socket().onopen?.() })
  await act(async () => {})
}

describe('the incident banner follows the switch', () => {
  it('appears on the gateway\'s incident hint, and clears on the next one', async () => {
    await mountBanner()
    expect(screen.queryByRole('alert')).toBeNull()

    active = true
    await frame({ type: 'refresh', data: { kinds: ['incident', 'loops'] } })
    expect((await screen.findByRole('alert')).textContent).toContain('a drill')

    active = false
    await frame({ type: 'refresh', data: { kinds: ['incident', 'loops'] } })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('reads nothing on a hint about something else', async () => {
    await mountBanner()
    const reads = incident.mock.calls.length
    await frame({ type: 'refresh', data: { kinds: ['crons'] } })
    await frame({ type: 'chat_status', data: { status: 'Thinking' } })
    expect(incident.mock.calls.length).toBe(reads)
  })

  it('re-reads after the socket comes back, since a hint sent while it was down is lost', async () => {
    await mountBanner()
    active = true
    await act(async () => { socket().onclose?.() })
    await act(async () => { await new Promise((r) => setTimeout(r, 600)) })
    await act(async () => { socket().onopen?.() })
    expect(await screen.findByRole('alert')).toBeTruthy()
  })

  it('does not poll a tab nobody touches', async () => {
    vi.useFakeTimers()
    await mountBanner()
    const reads = incident.mock.calls.length
    await act(async () => { vi.advanceTimersByTime(10 * 60_000) })
    expect(incident.mock.calls.length).toBe(reads)
  })
})
