// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'

// ── TSE-1: first run asks for the attribution handle beside the display name ──────────────
//
// `dashboard.username` is the one identity field that cannot be applied retroactively — a
// rename affects future writes only (TEAM-SHARED-ENTITIES §1), so every record written before
// the handle exists is permanently unattributed. It shipped editable in Settings → Account and
// nowhere else, and first run never mentioned it: measured, ZERO occurrences of `username` in
// `Onboarding.tsx` against 79 of `name`.
//
// What makes this worth a test file rather than a grep is that the field has to gain a
// QUESTION without gaining a GATE, and both halves are load-bearing:
//
//   • the suggestion tracks the display name while the field is untouched, so the common case
//     needs no typing at all;
//   • '' stays reachable — "leave it empty to keep records unattributed" is shipped copy and
//     `slugify_username` never invents a fallback — so a pre-fill that grew back over a
//     deliberate clear would fabricate the one value this field must never invent.
//
// The steps are stubbed exactly as `onboardingProgress.test.tsx` stubs them: under test is the
// shell's identity commit, not the steps it walks through. The provider's own half of the
// contract — that only a caller who ASKED may send a handle — is `identityHandleWrite.test.tsx`.
//
// Falsified (each mutation measured against these tests):
//   • suggestion recomputed in `finish()` instead of captured in `commitName` → "offers back a
//     handle this install already has" RED: committed `ada-king` over the operator's `lovelace`.
//   • `handleTouched` never set on change → "clears the field" RED: committed `ada-lovelace`
//     for a field the operator had emptied.
//   • `savedHandle` defaulted the way the name is → "skipped without a name" RED with
//     `operator`, a handle nobody chose.

const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const setName = vi.fn()

/** The handle this install already has, as the provider would report it: '' on a fresh
 *  install, non-empty for the "Restart onboarding" case. Read at render time. */
let storedHandle = ''

vi.mock('../lib/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    // Kept PENDING deliberately, like the sibling flow tests: the done screen renders the real
    // appearance dial, and a promise settling after render lands a setState outside act().
    themes: () => new Promise(() => {}),
    personalclawConfig: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  },
}))
vi.mock('./identity', async (orig) => {
  // PARTIAL mock: `suggestHandle` is the rule under test, so it stays real — a stubbed
  // suggester would let the flow pass while showing the operator something else.
  const real = await orig<typeof import('./identity')>()
  // `name` is the STORED display name the flow seeds its name field from; '' is a fresh install.
  return { ...real, useIdentity: () => ({ name: '', setName, username: storedHandle }) }
})
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('./onboarding/ImportStep', () => ({
  ImportStep: ({ onDone }: { onDone: (s: string) => void }) => (
    <button type="button" onClick={() => onDone('2 imported')}>stub-imported</button>
  ),
}))
vi.mock('./onboarding/EssentialsStep', () => ({
  EssentialsStep: ({ onDone }: { onDone: (s: string) => void }) => (
    <button type="button" onClick={() => onDone('gpt-5')}>stub-continue</button>
  ),
}))
vi.mock('./onboarding/TryOneStep', () => ({
  TryOneStep: ({ onDone }: { onDone: (s: string) => void }) => (
    <button type="button" onClick={() => onDone('1 of 3 tried')}>stub-tried</button>
  ),
}))

import { OnboardingHarness } from '../test/onboardingHarness'
import { AppearanceProvider } from './appearance'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

beforeEach(() => {
  // The flow persists the typed name in `sessionStorage` so a refresh mid-flow keeps it, which
  // makes it shared state BETWEEN TESTS: without this, a later test that skips setup without typing
  // a name inherits the previous test's draft and reads as a rename instead of the default.
  sessionStorage.clear()
  vi.clearAllMocks()
  storedHandle = ''
  // jsdom has no matchMedia and the appearance provider's useIsMobile calls it unguarded.
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  onboarding.mockResolvedValue({ needs_model: true, has_model_provider: false, has_chat_binding: false })
})

afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

async function renderFlow() {
  render(<AppearanceProvider><OnboardingHarness /></AppearanceProvider>)
  // The flow reads its resume point on mount; asserting before that lands races the fetch.
  await waitFor(() => expect(onboarding).toHaveBeenCalled())
}

const nameField = () => screen.getByLabelText('Your name')
const handleField = () => screen.getByLabelText('Username') as HTMLInputElement

/** Walk from the name step to the end of the flow, which is where identity is committed. */
async function finishFlow() {
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-imported' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
  fireEvent.click(await screen.findByRole('button', { name: /Start using/ }))
}

describe('the first-run identity step asks for a handle beside the display name', () => {
  it('offers the field, pre-filled from the display name as it is typed', async () => {
    await renderFlow()
    // The control the clause is about: a named input a user can actually reach.
    expect(handleField().value).toBe('')
    fireEvent.change(nameField(), { target: { value: 'Ada Lovelace' } })
    expect(handleField().value).toBe('ada-lovelace')
    // Still tracking — an untouched field DISPLAYS the suggestion rather than owning it.
    fireEvent.change(nameField(), { target: { value: 'Ada King' } })
    expect(handleField().value).toBe('ada-king')
  })

  it('commits the suggestion nobody edited', async () => {
    await renderFlow()
    fireEvent.change(nameField(), { target: { value: 'Ada Lovelace' } })
    await finishFlow()
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace'))
  })

  it('commits an edited handle instead of the suggestion, and stops tracking the name', async () => {
    await renderFlow()
    fireEvent.change(nameField(), { target: { value: 'Ada Lovelace' } })
    fireEvent.change(handleField(), { target: { value: 'lovelace' } })
    // The first edit makes the field the operator's; the name may still change afterwards.
    fireEvent.change(nameField(), { target: { value: 'Ada King' } })
    expect(handleField().value).toBe('lovelace')
    await finishFlow()
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada King', 'lovelace'))
  })

  // ── NEGATIVE CONTROL — '' stays reachable ───────────────────────────────────────────────
  it('finishes with an EMPTY handle when the operator clears the field', async () => {
    await renderFlow()
    fireEvent.change(nameField(), { target: { value: 'Ada Lovelace' } })
    expect(handleField().value).toBe('ada-lovelace')
    fireEvent.change(handleField(), { target: { value: '' } })
    // The suggestion must NOT grow back — this is the clear that has to survive.
    expect(handleField().value).toBe('')
    await finishFlow()
    // A name, and no handle: records written afterwards carry no attribution, which is the
    // shipped promise and the pre-existing behaviour of every install that never sets one.
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', ''))
  })

  it('invents no handle when setup is skipped without a name', async () => {
    await renderFlow()
    fireEvent.click(screen.getByRole('button', { name: /^Skip setup/ }))
    // The NAME falls back to the shared default because the route guard needs a non-empty
    // one. The handle has no such need and `slugify_username` never invents a fallback, so a
    // skipped run must not be silently stamped `operator`.
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Operator', ''))
  })

  it('offers back a handle this install already has, rather than a fresh suggestion', async () => {
    // The "Restart onboarding" case (Settings → Account clears the name, not the handle).
    // Re-suggesting here would silently replace a deliberate handle — already stamped onto
    // existing records — with one derived from whatever name is typed this time.
    storedHandle = 'lovelace'
    await renderFlow()
    expect(handleField().value).toBe('lovelace')
    fireEvent.change(nameField(), { target: { value: 'Ada King' } })
    expect(handleField().value).toBe('lovelace')
    await finishFlow()
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada King', 'lovelace'))
  })

  it('gains a question, not a gate: an empty handle still advances the step', async () => {
    await renderFlow()
    fireEvent.change(nameField(), { target: { value: 'Ada Lovelace' } })
    fireEvent.change(handleField(), { target: { value: '' } })
    const advance = screen.getByRole('button', { name: 'Continue' })
    // Optional means optional: `aria-disabled` is how this flow withholds a control, so a
    // handle that gated Continue would announce itself here.
    expect(advance.getAttribute('aria-disabled')).toBeNull()
    fireEvent.click(advance)
    expect(await screen.findByRole('button', { name: 'stub-imported' })).toBeTruthy()
  })

  it('names the handle it will store in the step summary', async () => {
    // The step collapses once passed, and its summary is the only place the committed handle
    // is visible afterwards — a silent commit is how an unwanted handle survives to the first
    // task that carries it.
    await renderFlow()
    fireEvent.change(nameField(), { target: { value: 'Ada Lovelace' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('Ada Lovelace · @ada-lovelace')).toBeTruthy()
  })
})
