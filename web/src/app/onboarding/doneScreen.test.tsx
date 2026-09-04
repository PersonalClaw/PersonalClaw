// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── OU-4: the done screen hands over three controls ──────────────────────────
//
// The Design's done screen "points at Inbox, the bounciness slider, and the unlock-everything
// toggle". Pointing is the easy half; the half worth testing is that each pointer is the REAL
// mechanism and not a lookalike:
//
//  • the Inbox link must actually leave the flow (the route guard holds a non-onboarded user
//    on `#/onboarding`, so a plain link here would be bounced — `exitTo.ts` is the seam);
//  • the dial must be the Settings → Design Bounciness control, i.e. moving it must reach
//    `runtime.bounciness`, which is what every spring preset reads;
//  • the switch must drive the ONE nav-disclosure setting OU-5 shipped, and drive it through
//    `finish()` — an abandoned flow must leave no record, because the absence of a record is
//    exactly how the shell tells an upgrade from a fresh install.

const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const setName = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    themes: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
    // The autonomy pointer's config read — PENDING here so its switch stays withheld;
    // `autonomyDisclosure.test.tsx` owns that pointer's behaviour with a resolved read.
    personalclawConfig: () => new Promise(() => {}),
  },
}))
vi.mock('../identity', () => ({
  useIdentity: () => ({ setName }),
  firstNameOf: (n: string) => n.split(' ')[0],
  DEFAULT_USER_NAME: 'Operator',
}))
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))
// PEP-5's import step, stubbed to its escape hatch for the same reason — `importStep.test.tsx`
// owns its own behaviour, and un-stubbed it would fetch a scan on mount.
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
import { readNavDisclosure } from '../navDisclosure'
import { peekOnboardingExit, clearOnboardingExit } from './exitTo'
import { runtime } from '../../design/runtime'

const ORIGINAL_MATCH_MEDIA = window.matchMedia
const DEFAULT_BOUNCINESS = runtime.bounciness

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
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
  runtime.bounciness = DEFAULT_BOUNCINESS
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

describe('the done screen points at the Inbox with a link that can leave the flow', () => {
  it('hands the destination to the route guard and finishes', async () => {
    await reachDoneScreen()
    fireEvent.click(screen.getByRole('button', { name: 'Open the Inbox instead' }))
    // Not a navigation: the guard owns where a just-onboarded user goes (see exitTo.ts).
    expect(peekOnboardingExit()).toBe('inbox')
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace'))
    expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
  })
})

describe('the done screen hands over the real Bounciness dial', () => {
  it('moving it reaches runtime.bounciness — the value every spring preset reads', async () => {
    await reachDoneScreen()
    const dial = screen.getByRole('slider', { name: 'Bounciness' })
    expect(dial).toHaveAttribute('max', '1')
    fireEvent.change(dial, { target: { value: '0' } })
    expect(runtime.bounciness).toBe(0)
  })
})

describe('the done screen unlock switch is the ONE nav-disclosure setting', () => {
  it('writes expert mode when it is on at finish', async () => {
    await reachDoneScreen()
    fireEvent.click(screen.getByRole('switch', { name: 'Show every surface' }))
    fireEvent.click(screen.getByRole('button', { name: /Start using/ }))
    await waitFor(() => expect(readNavDisclosure().mode).toBe('expert'))
  })

  it('leaves the starter rail when it is off at finish', async () => {
    await reachDoneScreen()
    fireEvent.click(screen.getByRole('button', { name: /Start using/ }))
    await waitFor(() => expect(readNavDisclosure().mode).toBe('starter'))
  })

  it('writes nothing until the flow finishes', async () => {
    await reachDoneScreen()
    fireEvent.click(screen.getByRole('switch', { name: 'Show every surface' }))
    // An abandoned flow must leave NO record: the shell reads a missing record as "onboarded
    // before this version" and shows every surface, which is the safe direction to fail in.
    expect(localStorage.getItem('nav-disclosure')).toBeNull()
  })

  it('states what the switch will do, in both positions', async () => {
    await reachDoneScreen()
    expect(screen.getByText(/joins it the first time you open one/)).toBeTruthy()
    fireEvent.click(screen.getByRole('switch', { name: 'Show every surface' }))
    expect(screen.getByText(/every destination from the start/)).toBeTruthy()
  })
})
