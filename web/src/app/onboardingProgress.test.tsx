// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── OU-2: the flow shell's resume-point writes ────────────────────────────────
//
// OU-1 shipped `entity_settings/onboarding.json` with a `step` field and nothing that
// wrote it — a stored key with no writer reads exactly like a working resume until
// someone tries to resume. These tests pin the writer: every transition of the step
// stack persists its resume point through the ONE existing write path
// (`POST /api/onboarding/state`), using the canonical step vocabulary from
// `personalclaw/onboarding.py` — `name → essentials → first_success → done`.
//
// The step component itself is stubbed here on purpose: what is under test is the
// shell's wiring, and `essentialsStep.test.tsx` owns the step's own behaviour.

const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const setName = vi.fn()

vi.mock('../lib/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
  },
}))
vi.mock('./identity', () => ({
  useIdentity: () => ({ setName }),
  firstNameOf: (n: string) => n.split(' ')[0],
}))
// The 3D backdrop needs a real canvas; the flow's logic does not.
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('./onboarding/EssentialsStep', () => ({
  EssentialsStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('gpt-5')}>stub-continue</button>
      <button type="button" onClick={onSkip}>stub-skip</button>
    </div>
  ),
}))

import { Onboarding } from './Onboarding'

beforeEach(() => {
  vi.clearAllMocks()
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  onboarding.mockResolvedValue({ needs_model: true, has_model_provider: false, has_chat_binding: false })
})

async function enterName() {
  render(<Onboarding />)
  fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada Lovelace' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
}

describe('every step transition persists its resume point', () => {
  it('records `essentials` when the name is committed', async () => {
    await enterName()
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'essentials' }))
  })

  it('records `first_success` when the essentials step is completed', async () => {
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'first_success' }))
  })

  it('records `first_success` when the essentials step is SKIPPED too', async () => {
    // A skip is still a resume point: a user who comes back should not be dropped
    // onto the step they deliberately walked past.
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'first_success' }))
  })

  it('records `done` and commits the name LAST', async () => {
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: /Start using/ }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' }))
    // `onboarded` is derived from a non-empty server name, so committing it is what
    // closes the flow — it must happen after the terminal step is recorded.
    expect(setName).toHaveBeenCalledWith('Ada Lovelace')
    const steps = saveOnboardingState.mock.calls.map(([p]) => p.step)
    expect(steps).toEqual(['essentials', 'first_success', 'done'])
  })

  it('writes only the `step` key — no lane progress the shell did not observe', async () => {
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    for (const [patch] of saveOnboardingState.mock.calls) expect(Object.keys(patch)).toEqual(['step'])
  })
})

describe('a failed progress write costs the user nothing', () => {
  it('still advances when the resume-point POST rejects', async () => {
    saveOnboardingState.mockRejectedValue(new Error('gateway down'))
    await enterName()
    // The essentials step is reached regardless: resume is a convenience, not a gate.
    expect(await screen.findByRole('button', { name: 'stub-continue' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'stub-continue' }))
    expect(await screen.findByRole('button', { name: /Start using/ })).toBeTruthy()
  })
})
