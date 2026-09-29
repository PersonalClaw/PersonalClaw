// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'

// ── A second tab joins the run it finds, instead of starting a new one ──────────────────
//
// Measured in the container, with the run on its second step and 66 items imported: a SECOND TAB
// (opened on `#/files`, which the route guard sends to setup) landed on "Step 1 of 5 · Your name"
// with both fields empty and the caption "Nothing is set up, and you'll be called 'Operator' until
// you pick a name." — while the server already recorded `step: import`. Its "Skip setup for now"
// would have committed "Operator". The typed name lived in the first tab's `sessionStorage` only.
//
// The name the run passed step 1 with is now kept with the rest of the run (`name_draft` in
// `onboarding.json`), so a second tab, or a new one after the first was closed, picks the run up
// where it is. Identity is still written only when the run finishes.

const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const setName = vi.fn()
const keepOrDefaultName = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    onboardingModelCheck: () => new Promise(() => {}),
    testModelProvider: () => new Promise(() => {}),
    themes: () => new Promise(() => {}),
    personalclawConfig: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  },
}))
vi.mock('../identity', async (orig) => {
  const real = await orig<typeof import('../identity')>()
  // A first run: this install has no name yet.
  return { ...real, useIdentity: () => ({ name: '', setName, keepOrDefaultName, username: '' }) }
})
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('./ImportStep', () => ({
  ImportStep: ({ onDone }: { onDone: (s: string) => void }) => (
    <button type="button" onClick={() => onDone('66 imported')}>stub-imported</button>
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

import { OnboardingHarness } from '../../test/onboardingHarness'
import { AppearanceProvider } from '../appearance'

const ORIGINAL_MATCH_MEDIA = window.matchMedia
const FRESH = { needs_model: true, has_model_provider: false, has_chat_binding: false }
const MIRA = { name: 'Mira Castell', handle: '', handle_touched: false }

beforeEach(() => {
  // A new tab's `sessionStorage` is empty — which is the whole premise of this file.
  sessionStorage.clear()
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
  onboarding.mockResolvedValue(FRESH)
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

function renderFlow() {
  return render(<AppearanceProvider><OnboardingHarness deferred="files" /></AppearanceProvider>)
}

describe('the run keeps the name it passed step 1 with', () => {
  it('continuing past the name step records the draft with the step, and writes no identity', async () => {
    renderFlow()
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada Lovelace' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({
      step: 'import', name_draft: { name: 'Ada Lovelace', handle: '', handle_touched: false },
    }))
    expect(setName, 'identity is still written only when the run finishes').not.toHaveBeenCalled()
  })
})

describe('a second tab joins the run', () => {
  it('lands where the run is, with the name the first tab passed', async () => {
    onboarding.mockResolvedValue({ ...FRESH, step: 'import', name_draft: MIRA })
    renderFlow()
    // The import step's body, and the position announced — not step 1.
    expect(await screen.findByRole('button', { name: 'stub-imported' })).toBeTruthy()
    expect(screen.getByText('Step 2 of 5: Bring your setup over')).toBeTruthy()
    // The collapsed name row shows her name and the handle it suggests.
    expect(screen.getByText('Mira Castell · @mira-castell')).toBeTruthy()
    expect(screen.queryByText(/Nothing is set up/)).toBeNull()
    // Joining writes nothing: the run already records where it is.
    expect(saveOnboardingState).not.toHaveBeenCalled()
  })

  it('never replaces a name typed in the new tab before the run was read', async () => {
    let answer: (v: unknown) => void = () => {}
    onboarding.mockReturnValue(new Promise((r) => { answer = r }))
    renderFlow()
    fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada' } })
    await act(async () => { answer({ ...FRESH, step: 'import', name_draft: MIRA }) })
    expect((screen.getByPlaceholderText('Your name') as HTMLInputElement).value).toBe('Ada')
  })

  it('with the run under way but no name to join, step 1 says what is kept', async () => {
    // A draft that never landed (the write is fire-and-forget): the run is on step 2, and this
    // tab has no name. Skipping keeps what was done and commits the default — and says so.
    onboarding.mockResolvedValue({ ...FRESH, step: 'import' })
    renderFlow()
    expect(await screen.findByText(/Whatever you have finished so far is kept, and you'll be called "Operator" until you pick a name/)).toBeTruthy()
    expect(screen.queryByText(/Nothing is set up/)).toBeNull()
  })
})

describe('step 1 says what skipping keeps', () => {
  it('back on step 1 with a name passed, skipping keeps that name, not "Operator"', async () => {
    renderFlow()
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada Lovelace' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    fireEvent.click(await screen.findByRole('button', { name: /^Back/ }))
    expect(await screen.findByText(/Skipping keeps the name above and whatever you have finished so far/)).toBeTruthy()
    expect(screen.queryByText(/called "Operator"/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Skip setup for now' }))
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace'))
    expect(keepOrDefaultName).not.toHaveBeenCalled()
  })
})
