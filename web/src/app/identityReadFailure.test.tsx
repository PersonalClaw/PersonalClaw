import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

// ── A failed read of who you are is not a fresh home ──────────────────────────────────────
//
// Setup counts as done when a name is stored, so the shell decides between the app and first-run
// setup from ONE read, `GET /api/dashboard/config`. That read's failure used to be swallowed into
// "no name": a gateway mid-restart, a transient 5xx or a timeout made an onboarded home look brand
// new, the app opened "Welcome to PersonalClaw", and its first-run "Skip setup for now" then PUT
// `{"user_name":"Operator","username":""}` — over the real name, and clearing the handle too.
//
// Same family as the `GET /api/onboarding` read #3601 fixed: a read that failed was turned into an
// empty state, and the empty state licensed a write. What is asserted here is the user's side of it,
// through the REAL provider and the REAL shell: what is on screen, where the address bar is, and
// what was written.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
// The first-run flow's 3D dot-wave is a canvas; jsdom has no 2D context.
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))

/** Every gateway call this test's run made, by method name, with its arguments. */
const calls: { method: string; args: unknown[] }[] = []
/** The bodies sent to one write method, in order. */
const sent = (method: string) => calls.filter((c) => c.method === method).map((c) => c.args[0])

/** What successive reads of the stored identity answer; the last entry repeats. An `Error` rejects,
 *  the way `fetch` does when the gateway is not there to answer. */
let identityReads: Array<Record<string, unknown> | Error> = []
const unreachable = () => new TypeError('Failed to fetch')

/** Envelope-shaped reads the shell and the dashboard destructure; anything else resolves `[]`
 *  (the trap `navDisclosure.test.tsx` records). */
const ENVELOPES: Record<string, unknown> = {
  saveDashboardConfig: { ok: true },
  saveOnboardingState: { ok: true, state: {} },
  agents: { agents: [] },
  onboarding: { needs_model: false, has_model_provider: true, has_chat_binding: true },
  discover: { enabled: false, visible_count: 0, areas: [] },
  doctor: { ok: true, capabilities: {} },
  skillProposals: { proposals: [], lastReview: null },
  modelsLoaded: {
    loaded: [], providers: [],
    pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 90, warn: false, source: 'unavailable' },
  },
}

vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      calls.push({ method: prop, args })
      if (prop === 'dashboardConfig') {
        const answer = identityReads.length > 1 ? identityReads.shift() : identityReads[0]
        return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer)
      }
      return Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : [])
    },
  })
  return { ...real, api: stub }
})

// Imported AFTER the mocks so the shell picks them up.
const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
const { IdentityProvider } = await import('./identity')
// The real class — the mock above spreads the real module and replaces only `api`.
const { ApiError } = await import('../lib/api')
// The dashboard is a lazy route; resolving its chunk once keeps the landing assertions about the
// app rather than about a module transform.
await import('../pages/dashboard/DashboardPage')

/** `main.tsx`'s provider stack around the real shell. */
const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
    <App />
  </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

/** The app opened for the named user: Home's page title greets them. */
const greets = (first: string) => screen.findByRole('heading', { level: 1, name: new RegExp(`^Good \\w+, ${first}$`) })
const firstRunSetup = () => screen.queryByRole('heading', { name: /^Welcome to / })

beforeEach(() => {
  calls.length = 0
  identityReads = []
  localStorage.clear()
  sessionStorage.clear()
  location.hash = '#/dashboard'
})
afterEach(cleanup)

describe('a failed read of the stored name', () => {
  it('shows a retry instead of first-run setup, and writes nothing', async () => {
    identityReads = [unreachable()]
    renderApp()
    expect(await screen.findByRole('heading', { name: "Couldn't load your account" })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
    // Not the first-run flow: no welcome, no name field, and no Skip that could commit a name.
    expect(firstRunSetup(), 'an unread home was shown first-run setup').toBeNull()
    expect(screen.queryByLabelText('Your name')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Skip setup/ })).toBeNull()
    // …and the address bar was not moved there either.
    expect(location.hash).toBe('#/dashboard')
    expect(sent('saveDashboardConfig'), 'identity was written while the stored one was unknown').toEqual([])
    expect(sent('saveOnboardingState')).toEqual([])
  })

  it('a Retry that reads a stored name opens the app normally, and still writes nothing', async () => {
    identityReads = [unreachable(), { user_name: 'Ada Lovelace', username: 'lovelace' }]
    renderApp()
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(await greets('Ada')).toBeTruthy()
    expect(screen.getByRole('navigation')).toBeTruthy()
    expect(firstRunSetup()).toBeNull()
    expect(location.hash).toBe('#/dashboard')
    expect(sent('saveDashboardConfig')).toEqual([])
  })

  it('a Retry that fails again stays on the retry, still showing no setup', async () => {
    identityReads = [unreachable(), unreachable()]
    renderApp()
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    // The retry really asked again — two reads — and the answer was the same failure.
    await waitFor(() => expect(calls.filter((c) => c.method === 'dashboardConfig')).toHaveLength(2))
    expect(await screen.findByRole('heading', { name: "Couldn't load your account" })).toBeTruthy()
    expect(firstRunSetup()).toBeNull()
    expect(sent('saveDashboardConfig')).toEqual([])
  })

  it('a genuinely fresh home — the read succeeded and no name is stored — still opens first-run setup', async () => {
    // The control: the fix must not cost a new install its welcome.
    identityReads = [{ user_name: '', username: '' }]
    renderApp()
    expect(await screen.findByRole('heading', { name: /^Welcome to / })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Skip setup for now' })).toBeTruthy()
    await waitFor(() => expect(location.hash).toMatch(/^#\/onboarding/))
    expect(sent('saveDashboardConfig')).toEqual([])
  })
})

describe('"Skip setup for now" on a first run commits the default only onto a home that still has no name', () => {
  it('keeps a name the home gained after setup opened — another tab or device finished it', async () => {
    // Identity lives on the server and follows the user across devices, so the read this tab took
    // when it opened is not what is stored NOW.
    identityReads = [{ user_name: '', username: '' }, { user_name: 'Ada Lovelace', username: 'lovelace' }]
    renderApp()
    fireEvent.click(await screen.findByRole('button', { name: 'Skip setup for now' }))
    expect(await greets('Ada')).toBeTruthy()
    expect(sent('saveDashboardConfig'), 'Skip overwrote a stored name').toEqual([])
  })

  it('writes nothing and keeps setup open when that read fails', async () => {
    identityReads = [{ user_name: '', username: '' }, unreachable()]
    renderApp()
    fireEvent.click(await screen.findByRole('button', { name: 'Skip setup for now' }))
    // Twice by design: the toast card, and the assertive live region that announces it.
    expect(await screen.findAllByText(/^Couldn't finish setup: .+ Setup is still open, and nothing was changed\.$/)).toHaveLength(2)
    expect(firstRunSetup()).toBeTruthy()
    expect(sent('saveDashboardConfig')).toEqual([])
  })

  it('says a 503 in words, not as "HTTP 503"', async () => {
    // What the browser drive measured: a proxy's 503 has no gateway envelope, so the rejection's
    // message is `errEnvelope`'s placeholder, and the toast read "Couldn't finish setup: HTTP 503."
    identityReads = [{ user_name: '', username: '' }, new ApiError('HTTP 503', 503)]
    renderApp()
    fireEvent.click(await screen.findByRole('button', { name: 'Skip setup for now' }))
    const said = await screen.findAllByText(/^Couldn't finish setup: /)
    expect(said[0].textContent).toBe(
      "Couldn't finish setup: PersonalClaw didn't respond — check it is still running. Setup is still open, and nothing was changed.")
    expect(sent('saveDashboardConfig')).toEqual([])
  })

  it('on a home with no name, writes the visible default and leaves a stored handle alone', async () => {
    // The state the old "Restart onboarding" left behind: it cleared the name and kept the handle.
    // The skip never asked for a handle, so it may not write one — `username: ''` would erase it.
    identityReads = [{ user_name: '', username: 'lovelace' }]
    renderApp()
    fireEvent.click(await screen.findByRole('button', { name: 'Skip setup for now' }))
    expect(await greets('Operator')).toBeTruthy()
    expect(sent('saveDashboardConfig')).toEqual([{ user_name: 'Operator' }])
  })
})
