import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'

// ── A shell widget that throws leaves the rest of the shell standing ────────────────────────────
//
// Opening System status on a poll that was missing a reading threw from the header's widget, and
// nothing between it and the root caught the throw: the page went to an empty body (0 buttons) and
// only a reload brought it back. The page boundary covered the routed page alone, never the shell
// around it. Here the widget throws on every render, in the REAL shell, and the rest must survive:
// the page, the rail, the corner's other controls, with a Retry in the widget's place.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})
// Every gateway read resolves empty; the envelope-shaped reads are named (see navDisclosure.test).
const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => () => Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : []),
  })
  return { ...real, api: stub }
})

const widget = vi.hoisted(() => ({ throwing: true }))
vi.mock('../ui/SystemWidget', async (orig) => {
  const real = await orig<typeof import('../ui/SystemWidget')>()
  const { createElement: h } = await import('react')
  return {
    ...real,
    SystemWidget: () => {
      if (widget.throwing) throw new TypeError("Cannot read properties of undefined (reading 'toFixed')")
      return h('button', { type: 'button', 'aria-label': 'System status — Gateway connected' })
    },
  }
})

// A plain routed page, so the assertion is about the shell and not about any one page's data.
vi.mock('../pages/tools/ToolsPage', async () => {
  const { createElement: h } = await import('react')
  return { ToolsPage: () => h('h1', null, 'Probe page') }
})

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
await import('../pages/tools/ToolsPage')

let consoleError: MockInstance<typeof console.error>
beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  location.hash = '#/tools'
  widget.throwing = true
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => { cleanup(); consoleError.mockRestore() })

describe('a shell widget that throws', () => {
  it('🔴 leaves the page, the rail and the corner’s other controls rendered, with Retry in its place', async () => {
    render(<ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>)

    // The page and the rail: the shell did not unmount.
    expect(await screen.findByRole('heading', { name: 'Probe page' })).toBeTruthy()
    expect(screen.getByRole('navigation')).toBeTruthy()
    // The corner's other controls are still there…
    expect(screen.getByRole('button', { name: /^Open terminal/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Theme: / })).toBeTruthy()
    // …and the widget's slot says what could not show, with Retry.
    const notice = screen.getByText("Couldn't show the system status.").closest('[role="alert"]') as HTMLElement
    expect(notice, 'the notice sits in the widget’s place').toBeTruthy()
    expect(within(notice).getByRole('button', { name: 'Retry' })).toBeTruthy()
    // Logged under the widget's name, not swallowed.
    expect(consoleError.mock.calls.some((call) => call[0] === '[ui] the system status failed to render')).toBe(true)
  })

  it('and Retry brings the widget back once it renders', async () => {
    render(<ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>)
    await screen.findByRole('heading', { name: 'Probe page' })
    const notice = screen.getByText("Couldn't show the system status.").closest('[role="alert"]') as HTMLElement
    widget.throwing = false
    fireEvent.click(within(notice).getByRole('button', { name: 'Retry' }))
    expect(screen.getByRole('button', { name: 'System status — Gateway connected' })).toBeTruthy()
    expect(screen.queryByText("Couldn't show the system status.")).toBeNull()
  })
})
