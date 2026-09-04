// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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
    // The done screen renders the real Settings → Design Bounciness dial, so the flow now
    // needs the appearance store around it; the provider loads saved themes on mount. Kept
    // PENDING deliberately — "themes have not loaded" is a real state and a promise settling
    // after render would land a setState outside act().
    themes: () => new Promise(() => {}),
    // The done screen's autonomy pointer reads the config for the auto-update switch; kept
    // PENDING for the same reason — the disclosure copy renders, the control stays withheld.
    personalclawConfig: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  },
}))
vi.mock('./identity', () => ({
  useIdentity: () => ({ setName }),
  firstNameOf: (n: string) => n.split(' ')[0],
  DEFAULT_USER_NAME: 'Operator',
}))
// The 3D backdrop needs a real canvas; the flow's logic does not.
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))
// PEP-5's import step, stubbed like its siblings: this file tests the SHELL's resume writes,
// and `onboarding/importStep.test.tsx` owns the step itself (un-stubbed it fetches a scan).
vi.mock('./onboarding/ImportStep', () => ({
  ImportStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('2 imported')}>stub-imported</button>
      <button type="button" onClick={onSkip}>stub-skip-import</button>
    </div>
  ),
}))
vi.mock('./onboarding/EssentialsStep', () => ({
  EssentialsStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('gpt-5')}>stub-continue</button>
      <button type="button" onClick={onSkip}>stub-skip</button>
    </div>
  ),
}))
// The OU-3 first-success step, stubbed for the same reason: this file tests the SHELL's resume
// writes, and `tryOneOutcome.test.tsx` / `tryOneFailure.test.tsx` own the step's own behaviour.
vi.mock('./onboarding/TryOneStep', () => ({
  TryOneStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('1 of 3 tried')}>stub-tried</button>
      <button type="button" onClick={onSkip}>stub-skip-try</button>
    </div>
  ),
}))

import { Onboarding } from './Onboarding'
import { AppearanceProvider } from './appearance'
import { readNavDisclosure } from './navDisclosure'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

beforeEach(() => {
  vi.clearAllMocks()
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
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

function renderFlow() {
  return render(<AppearanceProvider><Onboarding /></AppearanceProvider>)
}

async function enterName() {
  renderFlow()
  // The flow reads its resume point on mount; a test that races that fetch would assert
  // against whichever half of the state landed first.
  await waitFor(() => expect(onboarding).toHaveBeenCalled())
  fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada Lovelace' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
}

/** Walk past PEP-5's import step — which is where committing the name now lands.
 *
 *  The import step is NOT a stored resume point (`STEPS` in `onboarding.py` has no id for it,
 *  exactly as it has none between `first_success` and `done`), so the name commit records
 *  nothing and LEAVING import is what records `essentials`. A fresh run therefore has to pass
 *  through here to reach the essentials step; a RESUMED run (the describe below) jumps over it. */
async function enterNameAndImport() {
  await enterName()
  fireEvent.click(await screen.findByRole('button', { name: 'stub-imported' }))
}


describe('every step transition persists its resume point', () => {
  it('records nothing for the import step, then `essentials` when it is left', async () => {
    // PEP-5 put a step between `name` and `essentials` that has no id in `STEPS`. Writing a
    // point on the way IN would claim the user finished a step they are standing on; writing
    // `import` would be a fifth stored value `merge_onboarding_state` rejects with a 400.
    await enterName()
    expect(await screen.findByRole('button', { name: 'stub-imported' })).toBeTruthy()
    expect(saveOnboardingState).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'stub-imported' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'essentials' }))
  })

  it('records `essentials` when the import step is SKIPPED too', async () => {
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'essentials' }))
  })

  it('records `first_success` when the essentials step is completed', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'first_success' }))
  })

  it('records `first_success` when the essentials step is SKIPPED too', async () => {
    // A skip is still a resume point: a user who comes back should not be dropped
    // onto the step they deliberately walked past.
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'first_success' }))
  })

  it('records `done` and commits the name LAST', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    fireEvent.click(await screen.findByRole('button', { name: /Start using/ }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' }))
    // `onboarded` is derived from a non-empty server name, so committing it is what
    // closes the flow — it must happen after the terminal step is recorded.
    expect(setName).toHaveBeenCalledWith('Ada Lovelace')
    // Still exactly three resume points, because `STEPS` in `onboarding.py` has no id between
    // `first_success` and `done`: OU-3's step IS `first_success`, so leaving it for the recap
    // writes nothing new and a user who reloads on the recap resumes at the unfinished step.
    const steps = saveOnboardingState.mock.calls.map(([p]) => p.step)
    expect(steps).toEqual(['essentials', 'first_success', 'done'])
  })

  it('leaving the first-success step does NOT invent a fourth resume point', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    // `merge_onboarding_state` rejects an unknown step value with a 400, so a spelled-out
    // `try`/`ready` here would be a silent 400 on every first run.
    const steps = saveOnboardingState.mock.calls.map(([p]) => p.step)
    expect(steps).toEqual(['essentials', 'first_success'])
  })

  it('skipping the first-success step reaches the recap too', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByRole('button', { name: /Start using/ })).toBeTruthy()
  })

  it('writes only the `step` key — no lane progress the shell did not observe', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    for (const [patch] of saveOnboardingState.mock.calls) expect(Object.keys(patch)).toEqual(['step'])
  })
})

describe('finishing marks the install as onboarded under THIS version (OU-5 / C4)', () => {
  // Progressive disclosure needs to tell a fresh install from an upgrade, and the marker is the
  // absence of a `nav-disclosure` record — so the write has to happen at the one act only a
  // fresh install performs. Without it a brand-new user lands on the full 19-row rail and the
  // starter rail never ships to anybody.
  async function finishFlow() {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    // OU-3 landed a fourth step (`try`) between essentials and ready while this atom was in
    // flight, so the flow has to pass through it to reach the finish button — the same path
    // every other test in this file already takes.
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    fireEvent.click(await screen.findByRole('button', { name: /Start using/ }))
  }

  it('writes the starter-rail marker', async () => {
    localStorage.clear()
    // No record reads as "onboarded before this version" — the full rail.
    expect(readNavDisclosure().mode).toBe('expert')
    await finishFlow()
    await waitFor(() => expect(readNavDisclosure().mode).toBe('starter'))
  })

  it('leaves already-earned pins alone', async () => {
    // "Restart onboarding" (Settings → Account) runs this flow again on an install that has
    // history. It may reset the MODE — that is what restarting means — but taking away surfaces
    // the user had already reached would be a rug-pull.
    localStorage.setItem('nav-disclosure', JSON.stringify({ mode: 'expert', pinned: ['tools'] }))
    await finishFlow()
    await waitFor(() => expect(readNavDisclosure()).toEqual({ mode: 'starter', pinned: ['tools'] }))
  })
})

// ── OU-4: the READER of everything above ─────────────────────────────────────
//
// OU-1 shipped the `step` field, OU-2/OU-3 wrote it, and until now nothing read it back: a
// mid-flow reload restarted at the essentials step and silently redid work the home had
// already recorded. These tests pin the resume, and the tell they watch is which step's BODY
// is on the page — a step stack renders every row, so asserting on a heading would pass for a
// flow that resumed nowhere.

describe('re-entering the flow resumes at the persisted step', () => {
  it('lands on the try-one step when the home stopped at first_success', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
      first_success: { knowledge: false, trigger: false, loop: false },
    })
    await enterName()
    // The try step's body, not the essentials step's — the run already finished that one.
    expect(await screen.findByRole('button', { name: 'stub-tried' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'stub-continue' })).toBeNull()
  })

  it('does not walk the stored resume point backwards', async () => {
    // Recording `essentials` on the way INTO first_success would cost the user that step
    // again on their next reload — the resume would decay one step per reload.
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true, step: 'first_success',
    })
    await enterName()
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalled())
    expect(saveOnboardingState.mock.calls.map(([p]) => p.step)).toEqual(['first_success'])
  })

  it('restates what the earlier visit set up, checked against live readiness', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
      first_success: { knowledge: true, trigger: false, loop: false },
    })
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    // The collapsed essentials row AND the recap both state the app the earlier visit
    // installed — the row as its done summary, the recap as the chat-model line.
    expect(await screen.findByText('anthropic-models')).toBeTruthy()
    expect(screen.getByText('Chat model: anthropic-models')).toBeTruthy()
    // …and the card completed BEFORE the reload still counts as a first success, even though
    // this visit's cards started idle (only the flags survive a reload, not the outcomes).
    expect(screen.getByText(/1 of 3 tried/)).toBeTruthy()
  })

  it('does not promise a model the home no longer resolves', async () => {
    onboarding.mockResolvedValue({
      needs_model: true, has_model_provider: false, has_chat_binding: false,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
    })
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByText(/Chat model — set up later in Settings/)).toBeTruthy()
    expect(screen.queryByText(/Chat model: anthropic-models/)).toBeNull()
  })

  it('starts a completed home over instead of dropping it on the recap', async () => {
    // `done` is the "Restart onboarding" case (Settings → Account clears the name). Resuming
    // at the recap would skip the steps the user just asked to run again.
    onboarding.mockResolvedValue({
      needs_model: true, has_model_provider: false, has_chat_binding: false, step: 'done',
    })
    await enterName()
    // The FIRST step after the name, which is PEP-5's import step — not the recap. A restarted
    // run redoes the import too, and re-entry is free there (already-imported items come back
    // marked `existing`, so nothing is duplicated by walking it again).
    expect(await screen.findByRole('button', { name: 'stub-imported' })).toBeTruthy()
  })
})

describe('skip at any step lands in a working dashboard', () => {
  it('skips from the FIRST step, committing the shared default name', async () => {
    localStorage.clear()
    renderFlow()
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: /^Skip setup/ }))
    // Identity is what releases the route guard, so a skip that did not commit it would
    // leave the user pinned to the onboarding screen forever.
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Operator'))
    expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
    // …and the rail marker is written, so the skipper gets the starter rail like anyone else.
    expect(readNavDisclosure().mode).toBe('starter')
  })

  it('names the default it will use, rather than renaming you silently', async () => {
    renderFlow()
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: /Skip setup — start as Operator/ })).toBeTruthy()
  })

  it('skips from a MIDDLE step, keeping the name that was typed', async () => {
    await enterNameAndImport()
    expect(await screen.findByRole('button', { name: 'stub-continue' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip setup and go to the dashboard' }))
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace'))
    expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
  })

  it('offers no skip on the last step — "Start using" is the door', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByRole('button', { name: /Start using/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Skip setup/ })).toBeNull()
  })
})

describe('a failed progress write costs the user nothing', () => {
  it('still advances when the resume-point POST rejects', async () => {
    saveOnboardingState.mockRejectedValue(new Error('gateway down'))
    await enterNameAndImport()
    // The essentials step is reached regardless: resume is a convenience, not a gate.
    expect(await screen.findByRole('button', { name: 'stub-continue' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    expect(await screen.findByRole('button', { name: /Start using/ })).toBeTruthy()
  })
})
