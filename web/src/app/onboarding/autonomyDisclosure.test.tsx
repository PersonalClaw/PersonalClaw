// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── FB-05: what the product does ON ITS OWN is told at first run, not discovered ──────
//
// Two defaults ship on: auto-update (pulls, rebuilds and restarts unattended) and the
// seeded community Store source. Both are defensible for a tool that keeps itself
// healthy — but a local-first product that quietly reaches out and restarts itself is
// exactly the surprise that erodes the trust the product is built on. The done screen
// discloses both, in the screen's own idiom: the claim comes WITH its real control.
//
// The half worth testing, as with the other three pointers, is that the handover is the
// REAL mechanism and not a lookalike:
//
//  • the switch must reflect the CONFIG's auto_update value, not a hardcoded default;
//  • flipping it must reach the same write Settings → Updates performs (`setAutoUpdate`);
//  • a refused write must TELL (the app toast) and not silently fight the control —
//    UpdatesPanel's documented remedy for this exact switch;
//  • the Store-source sentence must track `apps.registry_source_enabled` — a
//    pre-provisioned opt-out must not be told it got a source it never did;
//  • an unreadable config must withhold the switch (a control claiming a guessed state
//    is worse than none) and offer the Settings path instead.

const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const personalclawConfig = vi.fn()
const setAutoUpdate = vi.fn()
const setName = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    personalclawConfig: () => personalclawConfig(),
    setAutoUpdate: (...a: unknown[]) => setAutoUpdate(...a),
    themes: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  },
}))
vi.mock('../identity', () => ({
  useIdentity: () => ({ setName }),
  firstNameOf: (n: string) => n.split(' ')[0],
  DEFAULT_USER_NAME: 'Operator',
}))
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('./ImportStep', () => ({
  ImportStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-import</button>
  ),
}))
vi.mock('./EssentialsStep', () => ({
  EssentialsStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip</button>
  ),
}))
vi.mock('./TryOneStep', () => ({
  TryOneStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-try</button>
  ),
}))

import { Onboarding } from '../Onboarding'
import { AppearanceProvider } from '../appearance'
import { clearOnboardingExit, peekOnboardingExit } from './exitTo'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  localStorage.clear()
  clearOnboardingExit()
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  onboarding.mockResolvedValue({ needs_model: true, has_model_provider: false, has_chat_binding: false })
  personalclawConfig.mockResolvedValue({ auto_update: true, apps: { registry_source_enabled: true } })
  setAutoUpdate.mockResolvedValue({ ok: true })
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

/** Drive the real flow to its last step: name → skip import → skip essentials → skip try-one. */
async function reachDoneScreen() {
  render(<AppearanceProvider><Onboarding /></AppearanceProvider>)
  await waitFor(() => expect(onboarding).toHaveBeenCalled())
  fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada Lovelace' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
  await screen.findByRole('button', { name: /Start using/ })
}

describe('the done screen tells what the product does on its own', () => {
  it('hands over the real auto-update switch, reflecting the config', async () => {
    await reachDoneScreen()
    const sw = await screen.findByRole('switch', { name: 'Update automatically' })
    expect(sw).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(sw)
    // The same write Settings → Updates performs — a lookalike would go nowhere.
    expect(setAutoUpdate).toHaveBeenCalledWith(false)
    expect(sw).toHaveAttribute('aria-checked', 'false')
  })

  it('a refused write tells through the app toast and does not fight the switch', async () => {
    setAutoUpdate.mockRejectedValue(new Error('nope'))
    const toasts: string[] = []
    const onToast = (e: Event) => toasts.push((e as CustomEvent<{ message: string }>).detail.message)
    window.addEventListener('ne:toast', onToast)
    try {
      await reachDoneScreen()
      fireEvent.click(await screen.findByRole('switch', { name: 'Update automatically' }))
      await waitFor(() => expect(toasts.some((m) => m.includes("Couldn't disable automatic updates"))).toBe(true))
      // Told, not fought: the optimistic flip stands (UpdatesPanel's documented remedy).
      expect(screen.getByRole('switch', { name: 'Update automatically' })).toHaveAttribute('aria-checked', 'false')
    } finally {
      window.removeEventListener('ne:toast', onToast)
    }
  })

  it('names the seeded Store source and routes to where removal persists', async () => {
    await reachDoneScreen()
    await screen.findByText(/one community source/)
    fireEvent.click(screen.getByRole('button', { name: 'Review Store sources' }))
    // Handed to the route guard, like every exit from the flow (see exitTo.ts).
    expect(peekOnboardingExit()).toBe('apps')
  })

  it('stays quiet about a source a pre-provisioned opt-out never got', async () => {
    personalclawConfig.mockResolvedValue({ auto_update: true, apps: { registry_source_enabled: false } })
    await reachDoneScreen()
    await screen.findByRole('switch', { name: 'Update automatically' })
    expect(screen.queryByText(/one community source/)).toBeNull()
  })

  it('an unreadable config withholds the switch and offers the Settings path', async () => {
    personalclawConfig.mockRejectedValue(new Error('boom'))
    await reachDoneScreen()
    const link = await screen.findByRole('button', { name: 'Manage updates in Settings' })
    expect(screen.queryByRole('switch', { name: 'Update automatically' })).toBeNull()
    fireEvent.click(link)
    expect(peekOnboardingExit()).toBe('settings/updates')
  })
})
