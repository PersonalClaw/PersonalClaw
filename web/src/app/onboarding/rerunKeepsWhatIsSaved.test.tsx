// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── "Run setup again" keeps what is already saved ─────────────────────────────────────────
//
// Settings → Account's re-entry door says: "Walks through first-run setup once more. Your name,
// handle and everything already set up are kept." The flow pre-fills the saved name and handle —
// and then "Skip setup for now" PUT `{"user_name":"Operator","username":""}` (a 200), so Home
// greeted "Good morning, Operator" and the handle was gone. The committed identity was derived
// from THIS run's name step, which a skip never passes, so the first-run fallback won. The skip
// line also said "you'll be called Operator until you pick a name" to someone who had one.

const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const setName = vi.fn()
let stored = { name: 'Maya Chen', username: 'maya' }

vi.mock('../../lib/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    // The re-entered run's chat-model proof; kept passing so this file sees a set-up home.
    onboardingModelCheck: () => Promise.resolve({ ok: true, source: 'binding', bound: ['p:m'], floor: false, provider: 'p' }),
    testModelProvider: () => Promise.resolve({ ok: true, status: 'connected', message: 'Connected' }),
    themes: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
    personalclawConfig: () => new Promise(() => {}),
  },
}))
vi.mock('../identity', async (orig) => {
  const real = await orig<typeof import('../identity')>()
  return { ...real, useIdentity: () => ({ name: stored.name, setName, username: stored.username }) }
})
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('./ImportStep', () => ({
  ImportStep: ({ onSkip }: { onSkip: () => void }) => <button type="button" onClick={onSkip}>stub-skip-import</button>,
}))
vi.mock('./EssentialsStep', () => ({
  EssentialsStep: ({ onSkip }: { onSkip: () => void }) => <button type="button" onClick={onSkip}>stub-skip</button>,
}))
vi.mock('./TryOneStep', () => ({
  TryOneStep: ({ onSkip }: { onSkip: () => void }) => <button type="button" onClick={onSkip}>stub-skip-try</button>,
}))

import { OnboardingHarness } from '../../test/onboardingHarness'
import { AppearanceProvider } from '../appearance'
import { readNavDisclosure, setNavMode } from '../navDisclosure'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  vi.clearAllMocks()
  stored = { name: 'Maya Chen', username: 'maya' }
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  setName.mockResolvedValue(undefined)
  onboarding.mockResolvedValue({ needs_model: false, has_model_provider: true, has_chat_binding: true })
})
afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

function openRerun(onFinished = vi.fn()) {
  render(<AppearanceProvider><OnboardingHarness onFinished={onFinished} /></AppearanceProvider>)
  return onFinished
}

describe('a re-run of setup keeps what is already saved', () => {
  it('"Skip setup for now" writes no identity — the saved name and handle stay', async () => {
    const onFinished = openRerun()
    // Pre-filled, as the Account copy promises.
    expect((screen.getByPlaceholderText('Your name') as HTMLInputElement).value).toBe('Maya Chen')
    fireEvent.click(screen.getByRole('button', { name: 'Skip setup for now' }))
    await waitFor(() => expect(onFinished).toHaveBeenCalled())
    expect(setName, 'a skip overwrote the saved identity').not.toHaveBeenCalled()
    expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
  })

  it('keeps the rail the install already has', async () => {
    setNavMode('expert')
    const onFinished = openRerun()
    fireEvent.click(screen.getByRole('button', { name: 'Skip setup for now' }))
    await waitFor(() => expect(onFinished).toHaveBeenCalled())
    expect(readNavDisclosure().mode, 'a re-run skip shrank the rail to the starter set').toBe('expert')
  })

  it('speaks re-run copy, not first-run copy', () => {
    openRerun()
    expect(screen.queryByText(/you'll be called "Operator" until you pick a name/)).toBeNull()
    expect(screen.getByText(/Skipping keeps your name, your handle and everything already set up exactly as they are/)).toBeTruthy()
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Setup, again')
  })

  it('a name the user edits and commits on the re-run IS saved', async () => {
    // The control: passing the name step is the user answering again, so that answer is written.
    openRerun()
    fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Maya Lin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Skip the rest of setup' }))
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Maya Lin', 'maya'))
  })

  it('a FIRST run skipped from step 1 still commits the visible default', async () => {
    // The first-run half is unchanged: the guard needs a name, and the skip line says which.
    stored = { name: '', username: '' }
    openRerun()
    expect(screen.getByText(/you'll be called "Operator" until you pick a name/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip setup for now' }))
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Operator', ''))
  })
})
