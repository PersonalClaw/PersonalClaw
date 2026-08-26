import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { readNavDisclosure, setNavMode } from '../navDisclosure'
import { requestProductTour, consumeProductTourRequest } from './tourLaunch'
import { PRODUCT_TOUR_STOPS } from './ProductTour'

// ── The replayable product tour, driven over the real shell (ONBOARDING-UX T5.1 + T5.2) ──
//
// `ui/SpotlightTour.test.tsx` covers the overlay's own contract (modality, Escape, the click
// shield, the degraded stop). What is asserted HERE is the product claim, and every one of
// them is an outcome rather than a mechanism:
//
//   1. the done screen launches it, and the tour is up on a WORKING app (the shell, not the
//      flow) — the seam exists because `finish()` unmounts the very button that was clicked;
//   2. it walks all five stops, and each stop's anchor RESOLVES on the surface it names;
//   3. Escape from a mid-tour stop leaves an app you can still use — asserted by clicking
//      something afterwards and watching it work, not by the overlay having unmounted;
//   4. nothing is stored and nothing is asked of the gateway on any step;
//   5. OU-5's auto-pin model is byte-identical with the tour present;
//   6. Discover replays it, and the entry cannot be dismissed away.
//
// jsdom notes: there is no layout, so every rect is 0×0 and the overlay takes its unanchored
// (centred card, no ring) path — which is why the anchoring assertions read
// `data-tour-anchored`, the overlay's report of whether it FOUND the element, rather than
// looking for a ring that jsdom could never place. The WS liveness hook has no gateway, and
// every `api.*` read resolves empty.

// Each walk mounts the shell, then five lazily-imported page chunks in turn, and waits for
// the overlay's anchor poll on each — well past the 5s default.
vi.setConfig({ testTimeout: 30_000 })

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
// The onboarding flow's 3D dot-wave is a canvas; jsdom has no 2D context.
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))
// The two middle steps are stubbed down to their escape hatch — this file is about what
// happens AFTER the flow, and `essentialsStep.test.tsx` / `tryOneOutcome.test.tsx` own them.
vi.mock('./ImportStep', () => ({
  ImportStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-import</button>
  ),
}))
vi.mock('./EssentialsStep', () => ({
  EssentialsStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-essentials</button>
  ),
}))
vi.mock('./TryOneStep', () => ({
  TryOneStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-try</button>
  ),
}))
// The settings bento's THIRTY cards each read their own endpoint and several destructure the
// envelope (`doctor.capabilities`, `channels.length`, …). Stubbing every one of them here
// would make this file a settings fixture; an empty registry keeps SettingsHome's OWN
// markup — the `data-tour="settings"` wrapper and the autofocusing search — which is all the
// tour interacts with. `settingsSubpageCoverage.test.ts` owns the card set.
vi.mock('../../pages/settings/settingsWidgets', () => ({ SETTINGS_WIDGETS: [] }))

/** Every gateway method name this test's run called, in order — the recorder that makes the
 *  "zero requests for tour progress" claim measurable instead of asserted in prose. */
const calls: string[] = []
/** Marks an endpoint that must REJECT — a failed fetch is a state some of these surfaces
 *  render differently, and swallowing it would hide the branch. */
const REJECT = Symbol('reject')
/** Envelope-shaped reads have to be named: `[]` for everything left three shell reads
 *  throwing inside a render, which reads as "the surface did not render" (the trap
 *  `navDisclosure.test.tsx` records). Mutable so a test can pick a Discover state. */
const ENVELOPES: Record<string, unknown> = {}
function resetEnvelopes() {
  for (const k of Object.keys(ENVELOPES)) delete ENVELOPES[k]
  Object.assign(ENVELOPES, {
    // An ALREADY-onboarded instance by default, because most of these tests are about the
    // shell. `onboarded` is DERIVED from a non-empty server name, so the two done-screen
    // tests blank it to make the first-run flow render at all.
    dashboardConfig: { user_name: 'Ada' },
    saveDashboardConfig: { ok: true },
    agents: { agents: [] },
    onboarding: { needs_model: true, has_model_provider: false, has_chat_binding: false },
    saveOnboardingState: { ok: true, state: {} },
    discover: { enabled: false, visible_count: 0, areas: [] },
    // The dashboard's "On this machine" widget reads `.loaded`; `[]` makes it throw inside
    // its own render, the ErrorBoundary swaps the WHOLE dashboard for the fallback, and the
    // `approvals` anchor vanishes — which reads as "the tour lost its anchor".
    // Settings' bento reads `.capabilities` off the doctor report.
    doctor: { ok: true, capabilities: {} },
    // Same trap, one surface over: ActionCenter maps `.proposals`, and the feed carries the
    // ladder's `lastReview` beside them so an empty queue can say WHY it is empty.
    skillProposals: { proposals: [], lastReview: null },
    modelsLoaded: {
      loaded: [], providers: [],
      pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 90, warn: false, source: 'unavailable' },
    },
  })
}

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (..._args: unknown[]) => {
      calls.push(prop)
      const v = prop in ENVELOPES ? ENVELOPES[prop] : []
      return v === REJECT ? Promise.reject(new Error('stubbed failure')) : Promise.resolve(v)
    },
  })
  return { ...real, api: stub }
})

// Imported AFTER the mocks so the shell picks them up.
const { App } = await import('../App')
const { ThemeProvider } = await import('../theme')
const { AppearanceProvider } = await import('../appearance')
const { PersonalityProvider } = await import('../personality')
const { IdentityProvider } = await import('../identity')

/** The shell in `main.tsx`'s provider stack — the REAL IdentityProvider, because the flip
 *  from onboarding to the app shell is the thing under test. */
const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
    <App />
  </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

const rail = () => screen.getByRole('navigation')
const railLinks = () => within(rail()).getAllByRole('button').map((b) => b.getAttribute('aria-label'))
const tour = () => screen.queryByRole('dialog')
const next = () => screen.getByRole('button', { name: /Next/ })

/** jsdom has no layout, so `useIsMobile`'s media query IS the viewport. */
function setViewport(isMobile: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /max-width:\s*768px/.test(q) ? isMobile : false,
    media: q,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }))
}

/** Walk the whole first-run flow to its recap, skipping the two middle steps. Call
 *  `firstRun()` before rendering, or the shell renders instead of the flow. */
function firstRun() { ENVELOPES.dashboardConfig = { user_name: '' } }

async function reachDoneScreen(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Your name'), 'Ada')
  await user.click(screen.getByRole('button', { name: 'Continue' }))
  await user.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
  await user.click(await screen.findByRole('button', { name: 'stub-skip-essentials' }))
  await user.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
  return screen.findByRole('button', { name: /Take the quick tour/ })
}

/** Advance to a stop, tolerating the surface's own lazy chunk + the overlay's anchor poll. */
async function atStop(id: string) {
  await waitFor(() => expect(tour()).toHaveAttribute('data-tour-step', id), { timeout: 5000 })
  return screen.getByRole('dialog')
}

beforeEach(() => {
  calls.length = 0
  resetEnvelopes()
  localStorage.clear()
  sessionStorage.clear()
  consumeProductTourRequest()  // no request may leak between tests
  location.hash = '#/dashboard'
  setViewport(false)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

// ─────────────────────────────────────────────────────────────────────────────────────────
describe('the done screen launches it, and the app is what it runs on', () => {
  it('finishing with "Take the quick tour" lands on a working shell with the tour up', async () => {
    // The whole point of the seam: the click that starts the tour is on a component that
    // `finish()` REPLACES with the shell, so the flow cannot host the tour. It leaves a
    // request, and the shell that is about to mount picks it up.
    firstRun()
    const user = userEvent.setup()
    renderApp()
    await user.click(await reachDoneScreen(user))

    // The app, not the flow: the rail is the shell's one navigation landmark.
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    // And the tour is on its first stop, over that rail.
    const d = await atStop('rail')
    expect(d).toHaveAttribute('aria-modal', 'true')
    expect(d).toHaveAttribute('data-tour-anchored', 'true')
    expect(d.getAttribute('aria-label')).toBe('PersonalClaw tour — step 1 of 5: The sidebar is the whole app')
  })

  it('"Start using" finishes WITHOUT the tour — it is offered, never imposed', async () => {
    firstRun()
    const user = userEvent.setup()
    renderApp()
    await reachDoneScreen(user)
    await user.click(screen.getByRole('button', { name: /Start using/ }))

    await waitFor(() => expect(railLinks()).toContain('Chat'))
    // Vacuity guard for the assertion above: the request seam is genuinely empty here, so
    // this is "no tour", not "the tour failed to appear".
    expect(tour()).toBeNull()
    expect(consumeProductTourRequest()).toBe(false)
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────
describe('it walks all five stops, over the real surfaces', () => {
  it('rail → chat → inbox → approvals → settings, every anchor resolved', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    act(() => { requestProductTour() })

    const walked: string[] = []
    for (const stop of PRODUCT_TOUR_STOPS) {
      const d = await atStop(stop.id)
      walked.push(stop.id)
      // The claim that matters: the element this stop names EXISTS on the surface the tour
      // just took the user to. A renamed anchor still renders a perfect card over nothing.
      await waitFor(() => expect(d).toHaveAttribute('data-tour-anchored', 'true'), { timeout: 5000 })
      expect(d.getAttribute('aria-label')).toContain(stop.title)
      if (stop.id !== 'settings') await user.click(next())
    }
    expect(walked).toEqual(['rail', 'chat', 'inbox', 'approvals', 'settings'])

    // The last stop offers Done, not Next — the tour ends rather than dead-ending.
    expect(screen.queryByRole('button', { name: /Next/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: /Done/ }))
    await waitFor(() => expect(tour()).toBeNull())
  })

  it('the settings stop keeps focus even though that surface autofocuses its own search', async () => {
    // Settings' bento autofocuses its search field on mount, and the tour navigates there
    // BEFORE it mounts — so a tour that only focused on step change would be left declaring
    // aria-modal with focus on the page behind the dim.
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    act(() => { requestProductTour() })

    await atStop('rail')
    for (let n = 0; n < 4; n += 1) await user.click(next())
    const d = await atStop('settings')
    // The search really is there (vacuity: something WAS competing for focus).
    expect(await screen.findByLabelText('Search settings')).toBeInTheDocument()
    await waitFor(() => expect(d.contains(document.activeElement)).toBe(true), { timeout: 5000 })
  })

  it('each stop names an anchor that exists in the file hosting that surface', () => {
    // The source twin of the drive above: it catches an anchor renamed on one side only,
    // which the drive would report as a degraded stop long after the fact.
    const SRC = join(process.cwd(), 'src')
    const HOSTS: Record<string, string> = {
      rail: 'ui/NavRail.tsx',
      chat: 'pages/ChatPage.tsx',
      inbox: 'pages/inbox/InboxPage.tsx',
      approvals: 'pages/dashboard/DashboardPage.tsx',
      settings: 'pages/settings/SettingsHome.tsx',
    }
    for (const stop of PRODUCT_TOUR_STOPS) {
      const host = HOSTS[stop.anchor]
      expect(host, `no host recorded for the "${stop.anchor}" anchor`).toBeTruthy()
      const src = readFileSync(join(SRC, host), 'utf8')
      // `approvals` is passed as a prop to a local Section, so accept either spelling.
      const ok = src.includes(`data-tour="${stop.anchor}"`) || src.includes(`tour="${stop.anchor}"`)
      expect(ok, `${host} must carry the "${stop.anchor}" tour anchor`).toBe(true)
    }
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────
describe('Escape exits anywhere, and what is left behind is a working app', () => {
  it('quitting mid-tour leaves every surface reachable', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    act(() => { requestProductTour() })

    await atStop('rail')
    await user.click(next())
    await atStop('chat')
    await user.click(next())
    await atStop('inbox')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(tour()).toBeNull())

    // NOT just "the overlay unmounted". A real click on a real control, doing a real thing:
    // the rail still navigates, so nothing about the app was left in a tour-shaped state.
    await user.click(within(rail()).getByRole('button', { name: 'Chat' }))
    await waitFor(() => expect(document.querySelector('[data-tour="chat"]')).not.toBeNull(), { timeout: 5000 })
  })

  it('the X and a click on the overlay are the pointer twins of Escape', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))

    act(() => { requestProductTour() })
    await atStop('rail')
    await user.click(screen.getByRole('button', { name: 'End the tour' }))
    await waitFor(() => expect(tour()).toBeNull())

    act(() => { requestProductTour() })
    await atStop('rail')
    await user.click(document.querySelector<HTMLElement>('[data-tour-shield]')!)
    await waitFor(() => expect(tour()).toBeNull())
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────
describe('nothing about the tour is stored, and nothing is reported', () => {
  it('walking every stop asks the gateway for nothing and writes no key', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    // Vacuity: the recorder CAN see gateway traffic — the shell's own reads are in it.
    expect(calls.length).toBeGreaterThan(0)

    const before = calls.length
    const keysBefore = { ...localStorage }
    act(() => { requestProductTour() })
    await atStop('rail')
    for (let n = 0; n < 4; n += 1) await user.click(next())
    await atStop('settings')
    await user.click(screen.getByRole('button', { name: /Done/ }))

    // No progress write, and nothing tour-shaped at all. The shell's own polls may land in
    // this window, so the assertion names what must NOT be there rather than freezing a
    // count a background poll could move.
    const during = calls.slice(before)
    expect(during.filter((c) => /tour/i.test(c))).toEqual([])
    expect(during.filter((c) => /onboarding/i.test(c))).toEqual([])

    // And no local record either: the tour is replayable, not resumable, so it has no
    // memory to keep. The surfaces it walks past DO write their own per-device prefs (the
    // rail's width, a page's view state), so the claim is about the tour's keys, not about
    // localStorage standing still — freezing the whole store would be a false claim that
    // reds on an unrelated surface.
    const newKeys = Object.keys({ ...localStorage }).filter((k) => !(k in keysBefore))
    expect(newKeys.filter((k) => /tour/i.test(k))).toEqual([])
    expect(Object.keys({ ...sessionStorage }).filter((k) => /tour/i.test(k))).toEqual([])
  })

  it('the tour modules import no gateway client and touch no storage', () => {
    // The source rail behind the drive above. A progress write added later would have to
    // come through one of these, and this reds before anyone has to notice a request.
    const SRC = join(process.cwd(), 'src')
    for (const rel of ['app/onboarding/ProductTour.tsx', 'app/onboarding/tourLaunch.ts', 'ui/SpotlightTour.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      expect(/from '[^']*lib\/api'/.test(src), `${rel} must not import the gateway client`).toBe(false)
      expect(/\bfetch\s*\(/.test(src), `${rel} must not call fetch`).toBe(false)
      expect(/localStorage|sessionStorage/.test(src), `${rel} must not persist anything`).toBe(false)
    }
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────
describe("OU-5's auto-pin model behaves identically with the tour present", () => {
  it('a full walk leaves the disclosure record untouched', async () => {
    // Auto-pin fires on REACHING a held-back surface, and the tour reaches four of them on
    // the user's behalf — so a tour that toured a non-starter surface would grow their rail
    // without them asking. Every stop is a starter surface, and this is how that stays true.
    setNavMode('starter')
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    const before = readNavDisclosure()

    act(() => { requestProductTour() })
    await atStop('rail')
    for (let n = 0; n < 4; n += 1) await user.click(next())
    await atStop('settings')
    await user.click(screen.getByRole('button', { name: /Done/ }))
    await waitFor(() => expect(tour()).toBeNull())

    expect(readNavDisclosure()).toEqual(before)
    expect(readNavDisclosure().pinned).toEqual([])
    // The starter rail is still the starter rail — five rows plus the expander.
    expect(railLinks()).not.toContain('Tools')
  })

  it('and auto-pin still WORKS — the assertion above is not a dead mechanism', async () => {
    // The vacuity guard. Without it, an auto-pin that had stopped writing entirely would
    // make "the record is untouched" pass forever.
    setNavMode('starter')
    location.hash = '#/tools'
    renderApp()
    expect(await screen.findByRole('heading', { name: 'Tools', level: 1 })).toBeInTheDocument()
    await waitFor(() => expect(readNavDisclosure().pinned).toContain('tools'))
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────
describe('Discover is the replay entry, and it cannot be lost', () => {
  it('replays the tour from the hub', async () => {
    ENVELOPES.discover = { enabled: true, visible_count: 0, areas: [] }
    location.hash = '#/discover'
    const user = userEvent.setup()
    renderApp()

    const start = await screen.findByRole('button', { name: 'Start the tour' })
    await user.click(start)
    await atStop('rail')

    // Ending it hands the user back to where they started — a tour taken from Discover must
    // not strand them in Settings — and focus returns to the control they used.
    await user.keyboard('{Escape}')
    await waitFor(() => expect(tour()).toBeNull())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start the tour' })).toHaveFocus())
  })

  it('is there for a user who dismissed everything', async () => {
    // T5.2's clause. A catalog tip would carry a dismiss, and dismissing the tour would
    // remove the product's only replay entry — so it is not a tip.
    ENVELOPES.discover = { enabled: true, visible_count: 0, areas: [] }
    location.hash = '#/discover'
    renderApp()
    expect(await screen.findByRole('button', { name: 'Start the tour' })).toBeInTheDocument()
    // Vacuity: this really is the explored-everything state.
    expect(await screen.findByText(/explored every part/)).toBeInTheDocument()
    // And no dismiss control belongs to it.
    expect(screen.queryByRole('button', { name: /Dismiss/ })).toBeNull()
  })

  it('is there even when Discover itself is switched off', async () => {
    ENVELOPES.discover = { enabled: false, visible_count: 0, areas: [] }
    location.hash = '#/discover'
    renderApp()
    expect(await screen.findByRole('button', { name: 'Start the tour' })).toBeInTheDocument()
    expect(await screen.findByText('Discover is off')).toBeInTheDocument()
  })

  it('survives a failed tips fetch, which is when a lost user needs it most', async () => {
    ENVELOPES.discover = REJECT
    location.hash = '#/discover'
    renderApp()
    expect(await screen.findByRole('button', { name: 'Start the tour' })).toBeInTheDocument()
  })
})
