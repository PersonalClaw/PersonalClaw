import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

// ── The desktop app's sidebar is the web UI's own rail, in light and in dark ─────────────────────
//
// The shell paints the web UI's own surfaces and no translucent material (`desktop/windowChrome.js`).
// An older shell still writes `data-window-material="sidebar"` on the page's root; the page takes no
// notice of it: the rail paints `var(--color-rail)`, the shell's root the canvas, and the body keeps
// its own colour, so no square box shows around New chat and the search. Driven through the real
// shell, as `threadRailInTheShell.test.tsx` renders it.

vi.mock('../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (await import('../test/quietSocket')).useChatSocket,
}))

vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

const READS: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
  sessionCost: { turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 },
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => () => Promise.resolve(prop in READS ? READS[prop] : []),
  })
  return { ...real, api: stub }
})

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')

const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

function noMedia() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

beforeEach(() => {
  localStorage.clear()
  location.hash = '#/dashboard'
  noMedia()
  Element.prototype.scrollIntoView ??= () => {}
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  delete document.documentElement.dataset.windowMaterial
  document.body.style.background = ''
})

describe('the desktop sidebar is the web rail', () => {
  for (const mode of ['dark', 'light'] as const) {
    it(`in ${mode}, an older shell's material mark changes nothing: the rail, the root and the body paint their own colours`, async () => {
      localStorage.setItem('mode', mode)
      document.documentElement.dataset.windowMaterial = 'sidebar'
      renderApp()
      const nav = await screen.findByRole('navigation')
      expect(nav.style.background).toBe('var(--color-rail)')
      const root = nav.closest('div.flex.h-full.flex-col') as HTMLElement | null
      expect(root?.style.background).toBe('var(--color-canvas)')
      expect(document.body.style.background).not.toBe('transparent')
      expect(document.querySelector('main')?.getAttribute('style') ?? '').not.toMatch(/background/)
    })
  }
})
