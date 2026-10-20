import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { expiryStamp } from '../lib/epoch'
import { noteInputForTests } from '../lib/useVisiblePoll'

// ── The status line says when the gateway is lost, and a banner follows when it stays lost ──────
//
// #3823, `web/REDESIGN.md` §4: the status line at the bottom of the window holds ambient state, and
// on its left the gateway connection, quiet while connected. The connection reading was the shell
// corner's dot (`ui/SystemWidget`), green and pulsing all day: an endless animation, and a dot that
// said nothing in words when the gateway went away. Now one reading of the connection feeds the line
// and its own banner:
//
// - connected, the line is a still dot and no words, and nothing on it animates;
// - while the first read is out, it says it is connecting;
// - a failed `/api/system` read turns it to disconnected, in words, with when the gateway was last
//   reached; the shared socket dropping reads the gateway at once, without waiting for the heartbeat;
// - a lost connection escalates to a banner, but only once it has stayed lost for BANNER_WAIT_MS
//   (2 s), so a restart or an update that is back within that never flashes it;
// - recovery returns the line to quiet and takes the banner away at once;
// - on a phone the line keeps the connection only.
//
// The incident banner (`IncidentBanner`) is not this banner and keeps its own meaning:
// `incidentBannerFollowsTheSwitch.test.tsx` holds it.

const system = vi.fn<() => Promise<unknown>>()
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      system: () => system(),
      authStatus: () => new Promise(() => {}),
      spawnedAgents: async () => [],
    },
  }
})

const { GatewayConnectionProvider, HEARTBEAT_MS, RETRY_MS } = await import('./gatewayConnection')
const { StatusLine } = await import('./StatusLine')
const { DisconnectedBanner, BANNER_WAIT_MS } = await import('./DisconnectedBanner')

const SYS = { hostname: 'studio.example.test', os: 'Darwin 25.0.0', arch: 'arm64', cpu_count: 8 }
const OPENED_AT = new Date('2026-10-20T11:24:00Z')
const FAILED = () => Promise.reject(new TypeError('Failed to fetch'))

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

beforeEach(() => {
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.useFakeTimers()
  vi.setSystemTime(OPENED_AT)
  noteInputForTests()
  system.mockReset()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** Let *ms* pass, with the reads it starts answered. The owner is at the keyboard throughout, so
 *  the heartbeat keeps its active cadence (`useVisiblePoll` stretches it on an idle tab). */
async function pass(ms: number) {
  let left = ms
  do {
    const step = Math.min(left, 1_000)
    noteInputForTests()
    await act(() => vi.advanceTimersByTimeAsync(step))
    left -= step
  } while (left > 0)
}

async function mount(ui: ReactNode = <><StatusLine /><DisconnectedBanner /></>) {
  render(<GatewayConnectionProvider>{ui}</GatewayConnectionProvider>)
  await pass(0)
}

const statusLine = () => screen.getByRole('contentinfo', { name: 'Status' })
const trigger = () => within(statusLine()).getByRole('button', { name: /^System status — / })
const banner = () => screen.queryByRole('alert')
const lastReached = (at: Date) => expiryStamp(at.getTime() / 1000)

describe('the status line, while the gateway answers', () => {
  it('is quiet: a still dot, no words, and nothing on it animates', async () => {
    system.mockResolvedValue(SYS)
    await mount()
    expect(trigger().getAttribute('aria-label')).toBe('System status — Gateway connected')
    expect(statusLine().textContent?.trim(), 'no words while connected').toBe('')
    expect(statusLine().querySelector('.status-pulse, [class*="animate-"]'), 'no resting pulse').toBeNull()
    expect(statusLine().querySelector('[style*="animation"]')).toBeNull()
    expect(banner()).toBeNull()
  })

  it('says it is connecting while the first read is out', async () => {
    system.mockReturnValue(new Promise(() => {}))
    await mount()
    expect(within(statusLine()).getByText('Connecting to gateway…')).toBeTruthy()
    expect(trigger().getAttribute('aria-label')).toBe('System status — Connecting to gateway…')
    await pass(BANNER_WAIT_MS * 3)
    expect(banner(), 'a read still out is not a lost connection').toBeNull()
  })
})

describe('a lost connection', () => {
  it('🔴 a failed read says so in words with when it was last reached, and the banner follows after the wait', async () => {
    system.mockResolvedValueOnce(SYS)
    await mount()
    system.mockImplementation(FAILED)
    await pass(HEARTBEAT_MS)

    const words = `Gateway disconnected · last reached ${lastReached(OPENED_AT)}`
    expect(within(statusLine()).getByText(words)).toBeTruthy()
    expect(trigger().getAttribute('aria-label')).toBe(`System status — ${words}`)
    expect(banner(), 'the banner waits out a short reconnect').toBeNull()

    await pass(BANNER_WAIT_MS - 1)
    expect(banner()).toBeNull()
    await pass(1)
    const shown = banner()!
    expect(shown, `the banner shows once the connection has been lost for ${BANNER_WAIT_MS} ms`).toBeTruthy()
    expect(shown.textContent).toContain('The gateway isn’t answering.')
    expect(shown.textContent).toContain(`Last reached ${lastReached(OPENED_AT)}.`)
    expect(shown.textContent).not.toMatch(/incident/i)
  })

  it('recovery returns the line to quiet and takes the banner away at once', async () => {
    system.mockResolvedValueOnce(SYS)
    await mount()
    system.mockImplementation(FAILED)
    await pass(HEARTBEAT_MS)
    await pass(BANNER_WAIT_MS)
    expect(banner()).toBeTruthy()

    system.mockResolvedValue(SYS)
    await pass(RETRY_MS)
    expect(banner()).toBeNull()
    expect(trigger().getAttribute('aria-label')).toBe('System status — Gateway connected')
    expect(statusLine().textContent?.trim()).toBe('')
  })

  it('a socket drop reads the gateway at once, and a reconnect inside the wait never shows the banner', async () => {
    system.mockResolvedValue(SYS)
    await mount()
    await act(async () => { socket().onopen?.() })
    const before = system.mock.calls.length

    // The gateway restarts: the shared socket drops, and the line reads the gateway at once.
    system.mockImplementation(FAILED)
    await act(async () => { socket().onclose?.() })
    await pass(0)
    expect(system.mock.calls.length, 'the drop read the gateway without waiting for the heartbeat').toBe(before + 1)
    expect(within(statusLine()).getByText(/^Gateway disconnected · last reached /)).toBeTruthy()

    // Back inside the wait: the socket's own retry reopens, and the reopen reads the gateway again.
    system.mockResolvedValue(SYS)
    await pass(BANNER_WAIT_MS / 2)
    await act(async () => { socket().onopen?.() })
    await pass(0)
    expect(trigger().getAttribute('aria-label')).toBe('System status — Gateway connected')

    await pass(BANNER_WAIT_MS * 3)
    expect(banner(), 'a restart that is back within the wait never flashes the banner').toBeNull()
  })

  it('a gateway never reached since the page opened says that', async () => {
    system.mockImplementation(FAILED)
    await mount()
    expect(within(statusLine()).getByText('Gateway disconnected · not reached since this page opened')).toBeTruthy()
    await pass(BANNER_WAIT_MS)
    expect(banner()?.textContent).toContain('Not reached since this page opened.')
  })

  it('Retry now reads the gateway at once', async () => {
    system.mockResolvedValueOnce(SYS)
    await mount()
    system.mockImplementation(FAILED)
    await pass(HEARTBEAT_MS)
    await pass(BANNER_WAIT_MS)
    const reads = system.mock.calls.length

    system.mockResolvedValue(SYS)
    fireEvent.click(within(banner()!).getByRole('button', { name: 'Retry now' }))
    await pass(0)
    expect(system.mock.calls.length).toBe(reads + 1)
    expect(banner()).toBeNull()
  })
})

describe('on a phone', () => {
  function setViewport(isMobile: boolean) {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: /max-width:\s*768px/.test(q) ? isMobile : false,
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    }))
  }

  it('the line keeps the connection only', async () => {
    system.mockResolvedValue(SYS)
    setViewport(false)
    await mount(<StatusLine right={<span>pinned context</span>} />)
    expect(within(statusLine()).getByText('pinned context'), 'a wide window shows the right slot').toBeTruthy()
    cleanup()

    setViewport(true)
    await mount(<StatusLine right={<span>pinned context</span>} />)
    expect(trigger().getAttribute('aria-label')).toBe('System status — Gateway connected')
    expect(within(statusLine()).queryByText('pinned context'), 'a phone drops everything but the connection').toBeNull()
  })
})
