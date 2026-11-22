import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'

// ── Settings › Models reads again when the models it shows change ────────────────────────────────
//
// Driven (dogfood, 11-21): on #/settings/models, after an instance was added in the Providers
// section above it, Chat's row still offered only the bundled model 87 s later; after a bound
// instance was deleted, the gateway held `chat: []` and the row still read "1 active" 88 s later.
// The Models section read its catalog and chains once, and nothing told it to read again. The
// gateway says when a binding or a provider changes (a `refresh` frame naming `models`), and the
// section now reads again on it, from whichever page or tab made the change, and when the socket
// comes back.

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

const frame = (kinds: string[]) =>
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type: 'refresh', data: { kinds } }) }) })

const h = vi.hoisted(() => ({ available: vi.fn(), chains: vi.fn() }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const answers: Record<string, (...a: unknown[]) => unknown> = {
    modelsAvailable: () => h.available(),
    activeChains: () => h.chains(),
    modelsHealth: () => Promise.resolve({ providers: [] }),
    modelsLoaded: () => Promise.resolve({
      loaded: [], providers: [],
      pressure: { total_mb: 16384, used_mb: 8192, available_mb: 8192, used_pct: 50, warn_pct: 85, warn: false, source: 'vm_stat' },
    }),
    embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
    hfTokenStatus: () => Promise.resolve({ sources: [] }),
    judgeBench: () => Promise.resolve({ ran: false }),
    modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], total_bytes: 0 }),
    modelDownloads: () => Promise.resolve([]),
    personalclawConfig: () => Promise.resolve({ agent: { prompt_cache_enabled: true } }),
  }
  const api = new Proxy({} as Record<string, unknown>, {
    get(_t, p) {
      if (typeof p !== 'string') return undefined
      return (...a: unknown[]) => (p in answers ? answers[p](...a) : Promise.resolve([]))
    },
  })
  return { ...real, api }
})

let ModelsPanel: typeof import('./ModelsPanel')['ModelsPanel']

beforeEach(async () => {
  vi.resetModules()
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  FakeSocket.last = null
  h.available.mockReset().mockResolvedValue([])
  h.chains.mockReset().mockResolvedValue({})
  sessionStorage.clear()
  localStorage.clear()
  ;({ ModelsPanel } = await import('./ModelsPanel'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function opened() {
  render(<ModelsPanel />)
  await waitFor(() => expect(h.chains).toHaveBeenCalled())
  await waitFor(() => expect(screen.queryByText(/Loading/)).toBeNull())
  await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
  return h.chains.mock.calls.length
}

describe('Settings › Models', () => {
  it('🔴 reads its models and chains again on the frame that says they changed', async () => {
    const before = await opened()

    frame(['models'])

    await waitFor(() => expect(h.chains.mock.calls.length).toBeGreaterThan(before))
    expect(h.available.mock.calls.length).toBeGreaterThan(1)
  })

  it('reads nothing again on a frame about something else', async () => {
    const before = await opened()

    frame(['loops'])
    await act(async () => { await new Promise((r) => setTimeout(r, 50)) })

    expect(h.chains.mock.calls.length).toBe(before)
  })
})
