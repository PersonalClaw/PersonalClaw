import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'

// ── The header's model chip changes when the models it describes do ──────────────────────────────
//
// Measured before this was written: the chip ("Set up a model", "Choose a model", "N degraded") read
// the gateway on its own 20 s poll alone, so it kept "Set up a model" for up to 20 s after a model
// was ready (80 s and 200 s once the page went idle). Now the gateway says when a binding or a
// provider changes (a `refresh` frame naming `models`), and the chip reads again on it, and when the
// socket comes back (frames sent while it was down are lost). The poll stays as the fallback.

class FakeSocket {
  static last: FakeSocket | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { FakeSocket.last = this; setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

const frame = (type: string, data: Record<string, unknown>) =>
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data }) }) })

const NEEDS_SETUP = [{ surface: 'inbox_enrichment', label: 'Inbox triage', available: false, floor: 'Rules only', backlog: 0, use_cases: ['chat'] }]
const READY = [{ ...NEEDS_SETUP[0], available: true }]

let api: typeof import('../lib/api')['api']
let DegradedChip: typeof import('./DegradedChip')['DegradedChip']

beforeEach(async () => {
  vi.resetModules()
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  FakeSocket.last = null
  ;({ api } = await import('../lib/api'))
  ;({ DegradedChip } = await import('./DegradedChip'))
})

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

/** The chip showing "Set up a model", with any socket it opens open. */
async function needingSetup() {
  const degraded = vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: NEEDS_SETUP } as never)
  const onboarding = vi.spyOn(api, 'onboarding').mockResolvedValue({ has_model_provider: false } as never)
  render(<DegradedChip />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Set up a model/ })).toBeTruthy())
  await act(async () => { await new Promise((r) => setTimeout(r, 5)) })  // a socket the chip opens, opens
  return { degraded, onboarding }
}

/** The gateway now has a model bound and its surfaces ready. */
function modelReady(spies: Awaited<ReturnType<typeof needingSetup>>) {
  spies.degraded.mockResolvedValue({ surfaces: READY } as never)
  spies.onboarding.mockResolvedValue({ has_model_provider: true } as never)
}

describe('the header model chip', () => {
  it('🔴 reads again, and goes, on the frame that says the models changed', async () => {
    const spies = await needingSetup()
    modelReady(spies)

    frame('refresh', { kinds: ['models'] })

    await waitFor(() => expect(screen.queryByRole('button', { name: /Set up a model/ })).toBeNull(), { timeout: 2000 })
  })

  it('reads nothing again on a frame about something else', async () => {
    const spies = await needingSetup()
    const reads = spies.degraded.mock.calls.length
    modelReady(spies)

    frame('refresh', { kinds: ['loops'] })
    await act(async () => { await new Promise((r) => setTimeout(r, 50)) })

    expect(spies.degraded.mock.calls.length).toBe(reads)
    expect(screen.getByRole('button', { name: /Set up a model/ })).toBeTruthy()
  })
})
