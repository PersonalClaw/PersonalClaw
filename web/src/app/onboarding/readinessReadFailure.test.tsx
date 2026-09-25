// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── a failed `GET /api/onboarding` is reported as a failure, never as "needs a model" ──────
//
// The flow's one readiness read seeds the essentials step AND the recap. When it failed, the
// flow fabricated `{needs_model: true, …}` in its place: step 3 opened as if nothing were set
// up, and the recap said "Chat model — set up later in Settings" — both claims about a home
// nobody had read.

const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const setName = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    themes: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
    personalclawConfig: () => new Promise(() => {}),
  },
}))
vi.mock('../identity', async (orig) => {
  const real = await orig<typeof import('../identity')>()
  return { ...real, useIdentity: () => ({ name: '', setName, username: '' }) }
})
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('./ImportStep', () => ({
  ImportStep: ({ onSkip }: { onSkip: () => void }) => <button type="button" onClick={onSkip}>stub-skip-import</button>,
}))
vi.mock('./EssentialsStep', () => ({
  EssentialsStep: ({ onSkip }: { onSkip: () => void }) => <button type="button" onClick={onSkip}>stub-essentials</button>,
}))
vi.mock('./TryOneStep', () => ({
  TryOneStep: ({ onSkip }: { onSkip: () => void }) => <button type="button" onClick={onSkip}>stub-skip-try</button>,
}))

import { OnboardingHarness } from '../../test/onboardingHarness'
import { AppearanceProvider } from '../appearance'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  vi.clearAllMocks()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  onboarding.mockRejectedValue(new Error('the gateway did not answer'))
})
afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

async function reachEssentials() {
  render(<AppearanceProvider><OnboardingHarness /></AppearanceProvider>)
  await waitFor(() => expect(onboarding).toHaveBeenCalled())
  fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada Lovelace' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
}

describe('the flow says it could not read the setup state', () => {
  it('step 3 reports the failed read and offers the retry, instead of assuming no model', async () => {
    await reachEssentials()
    const band = await screen.findByTestId('onboarding-readiness-error')
    expect(band.textContent).toMatch(/Couldn't load your setup state/)
    expect(band.textContent).toMatch(/the gateway did not answer/)
    // The step never rendered from a guessed readiness.
    expect(screen.queryByRole('button', { name: 'stub-essentials' })).toBeNull()

    // A retry that succeeds opens the real step.
    onboarding.mockResolvedValue({ needs_model: true, has_model_provider: false, has_chat_binding: false })
    fireEvent.click(screen.getByRole('button', { name: /Retry|Try again/ }))
    expect(await screen.findByRole('button', { name: 'stub-essentials' })).toBeTruthy()
  })

  it('the recap says it could not read what was set up — not "set up later"', async () => {
    await reachEssentials()
    await screen.findByTestId('onboarding-readiness-error')
    fireEvent.click(screen.getByRole('button', { name: 'Set up later' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    await screen.findByRole('button', { name: /Start using/ })
    expect(screen.getByText("Chat model — couldn't read whether one is set up")).toBeTruthy()
    expect(screen.queryByText('Chat model — set up later in Settings')).toBeNull()
    expect(screen.getByText("First success — couldn't read what was tried")).toBeTruthy()
  })
})
