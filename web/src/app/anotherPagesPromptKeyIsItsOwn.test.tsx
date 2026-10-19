import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'

// ── A page's own `prompt` key is its own ───────────────────────────────────────────────────────
//
// The shell writes a link's `prompt` and `pane` as the chat's own keys, and on the chat only. The
// Loop cockpit keeps its Prompt card's expander in the address as `?prompt=1`, so a reload or a
// shared address reopens it. This drives the REAL shell at a loop's cockpit and reads the card.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

const LOOP = {
  id: 'l-1', name: 'Report loop', status: 'complete', kind: 'general',
  task: 'Write the quarterly report\n\nWith every section in full.',
  kind_config: {}, linked_task_ids: [], cycles: [], created_at: 1, updated_at: 2,
}
const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
  uLoop: LOOP,
  uLoopReport: { report: '', log: '' },
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => () => Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : []),
  })
  return { ...real, api: stub }
})

class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) {}
  send(): void {}
  close(): void { this.readyState = 3 }
}
class FakeEventSource {
  onopen: (() => void) | null = null
  onmessage: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) {}
  addEventListener(): void {}
  removeEventListener(): void {}
  close(): void { this.readyState = 2 }
}

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource)
  vi.stubGlobal('fetch', () => Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })))
  Element.prototype.scrollIntoView ??= () => {}
  Element.prototype.scrollTo ??= (() => {}) as never
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function shell() {
  return render(<ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>)
}
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)) })

/** The cockpit's Prompt card disclosure, as it is drawn now. */
function promptCard(container: HTMLElement): Promise<HTMLButtonElement> {
  return waitFor(() => {
    const b = [...container.querySelectorAll('button[aria-expanded]')].find((el) => /^Prompt/.test(el.textContent ?? '')) as HTMLButtonElement | undefined
    expect(b, 'the loop cockpit drew its Prompt card').toBeTruthy()
    return b!
  })
}

describe("the Loop cockpit's Prompt card, which the address holds as ?prompt=1", () => {
  it('opens from the address, and the address keeps the flag', async () => {
    location.hash = '#/loops/l-1?prompt=1'
    const { container } = shell()
    await promptCard(container)
    await settle()
    expect(location.hash, 'the address still holds the cockpit flag').toBe('#/loops/l-1?prompt=1')
    expect((await promptCard(container)).getAttribute('aria-expanded'), 'the card is open').toBe('true')
  })

  it('opens on a click, which the address records as one entry of its own', async () => {
    location.hash = '#/loops/l-1'
    const { container } = shell()
    const card = await promptCard(container)
    await settle()
    expect(card.getAttribute('aria-expanded')).toBe('false')
    const entries = history.length

    await act(async () => { fireEvent.click(card) })
    await settle()
    expect((await promptCard(container)).getAttribute('aria-expanded'), 'the click opened the card').toBe('true')
    expect(location.hash).toBe('#/loops/l-1?prompt=1')
    expect(history.length, 'the click added its own entry and nothing rewrote it').toBe(entries + 1)
  })
})
