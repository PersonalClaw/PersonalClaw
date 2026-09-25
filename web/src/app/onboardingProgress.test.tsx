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
// `personalclaw/onboarding.py` — `name → import → essentials → first_success → ready → done`.
//
// 🔑 EVERY STEP HAS A POINT NOW, AND IT IS WRITTEN ON ENTRY. The first three-value version meant
// "the next step you have not finished", which left the import step and the recap with no id at
// all — measured cost on a fresh home: stopped on the import step the file still said `name`, so a
// reload restarted at the beginning; stopped on the recap it said `first_success`, so a reload
// walked the user BACK a step. The field is the HIGH-WATER MARK: the furthest step the run has
// stood on, raised on entry and never lowered, which is the only reading a reload can resume from.
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
vi.mock('./identity', async (orig) => {
  // PARTIAL mock, so the real `suggestHandle` runs: it is the rule the handle field shows,
  // and a stub would let these tests pass while the operator saw something else. The full
  // mock this replaced also had to be edited every time the module gained an export.
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    // `username` is the STORED handle the flow seeds its handle field from (TSE-1);
    // '' is a fresh install, which is what these tests are.
    // `name` is the STORED display name (offered back on a deliberate re-run); `username` the stored
    // handle. Both '' here, which is a fresh install — the case every test in this file is about.
    useIdentity: () => ({ name: '', setName, username: '' }),
  }
})
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

import { OnboardingHarness } from '../test/onboardingHarness'
import { AppearanceProvider } from './appearance'
import { readNavDisclosure } from './navDisclosure'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

beforeEach(() => {
  // The flow persists the typed name in `sessionStorage` so a refresh mid-flow keeps it, which
  // makes it shared state BETWEEN TESTS: without this, a later test that skips setup without typing
  // a name inherits the previous test's draft and reads as a rename instead of the default.
  sessionStorage.clear()
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
  return render(<AppearanceProvider><OnboardingHarness /></AppearanceProvider>)
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
  it('records `import` on ENTRY, then `essentials` when it is left', async () => {
    // The mark names where the user IS, not what they have finished — that is the only reading a
    // reload can resume from. Before this, standing on the import step recorded nothing, so a
    // refresh there threw away the name AND two steps of visible progress.
    await enterName()
    expect(await screen.findByRole('button', { name: 'stub-imported' })).toBeTruthy()
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'import' }))
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
    // TWO arguments since TSE-1: the attribution handle rides along in the SAME write, so
    // the name and the handle cannot disagree about whether first run happened. The second
    // is the suggestion the untouched field was visibly showing.
    expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace')
    // One point per step entered, in order, with no repeats — the mark rising once per move.
    const steps = saveOnboardingState.mock.calls.map(([p]) => p.step)
    expect(steps).toEqual(['import', 'essentials', 'first_success', 'ready', 'done'])
  })

  it('records `ready` for the recap — the step that used to have nowhere to be recorded', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    // `merge_onboarding_state` rejects an unknown step value with a 400, so every value spelled here
    // has to be a member of `STEPS` — `ready` is one now, which is what makes a reload on the recap
    // stay on the recap instead of walking the user back to the try step.
    const steps = saveOnboardingState.mock.calls.map(([p]) => p.step)
    expect(steps).toEqual(['import', 'essentials', 'first_success', 'ready'])
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

// ── the name step's promise must match when the write actually happens ────────
//
// The step's subtitle is the FIRST sentence the product ever says about persistence, and it
// read *"Saved on the server, so it follows you across devices"* while the step was still
// being filled in. That is `AccountPanel`'s sentence, where it is true because the panel
// writes on change; here the write is deliberately the last thing the flow does, so the
// past tense was a claim about a write that had not happened.
//
// Measured on a fresh container from the published wheel: typing a name and advancing issued
// ZERO writes, `GET /api/onboarding` still answered `step: "name"`, and a reload returned an
// empty field — after the collapsed row had already shown `Ada Lovelace · @ada-lovelace`
// back as a completed step. The second test is the CONTROL: it is what makes the first one
// mean something, because if the write ever moves earlier the copy should move back with it.

describe("the name step does not promise a save it has not made", () => {
  it('does not claim the name is already saved while the step is being filled in', async () => {
    renderFlow()
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    const subtitle = screen.getByText(/How the system addresses you/)
    expect(subtitle.textContent).not.toMatch(/Saved on the server/)
    // ...and it still says WHEN the promise is kept, rather than dropping the fact entirely.
    expect(subtitle.textContent).toMatch(/Saved when you finish setup/)
    expect(subtitle.textContent).toMatch(/follows you across devices/)
  })

  it('because advancing off the name step really does write nothing', async () => {
    await enterName()
    // The collapsed row now shows the name back, which is exactly why the copy mattered.
    expect(await screen.findByText('Ada Lovelace · @ada-lovelace')).toBeTruthy()
    // No identity write yet — the flow is showing a value the server has never seen.
    expect(setName).not.toHaveBeenCalled()
  })

  it('and the promise IS kept, at the end', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    fireEvent.click(await screen.findByRole('button', { name: /Start using/ }))
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace'))
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

  it('does not walk the stored resume point backwards — or rewrite one it already has', async () => {
    // Recording `essentials` on the way INTO first_success would cost the user that step again on
    // their next reload: the resume would decay one step per reload. The mark only ever RISES, so
    // resuming to the step it already names writes nothing at all.
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true, step: 'first_success',
    })
    await enterName()
    expect(await screen.findByRole('button', { name: 'stub-tried' })).toBeTruthy()
    expect(saveOnboardingState).not.toHaveBeenCalled()
  })

  it('restates what the earlier visit set up, checked against live readiness', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true,
      chat_model_refs: ['my-anthropic:claude-sonnet-4-5'],
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
      first_success: { knowledge: true, trigger: false, loop: false },
    })
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    // The collapsed essentials row AND the recap both state the MODEL the earlier visit bound
    // — the row as its done summary, the recap as the chat-model line.
    expect(await screen.findByText('claude-sonnet-4-5')).toBeTruthy()
    expect(screen.getByText('Chat model: claude-sonnet-4-5')).toBeTruthy()
    // …and the card completed BEFORE the reload still counts as a first success, even though
    // this visit's cards started idle (only the flags survive a reload, not the outcomes).
    // 🪤 Scoped to the summary PARAGRAPH. A bare `getByText(/1 of 3 tried/)` matches every
    // ancestor whose text content contains it, so wrapping the row header in a <button>
    // (needed to make a done row keyboard-reachable) made this find two nodes. The property
    // asserted is that the summary is VISIBLE, which the scoped query states directly.
    expect(screen.getByText(/1 of 3 tried/, { selector: 'p' })).toBeTruthy()
  })

  // ── #3528: the recap names the bound MODEL, never the app that provides it ──────────
  //
  // Measured in a browser on a fresh 0.1.3 container, against a loopback Ollama. First pass
  // through step 3: `Chat model: qwen2.5vl:7b` — correct. Reload (first-run progress is
  // server-side, so the flow resumes) and reach the recap: `Chat model: ollama-models`, and
  // the collapsed step-3 row repeated it. `active_models.json` held
  // `{"chat": ["Local Ollama:qwen2.5vl:7b"]}` the whole time — the binding was right and only
  // the sentence was wrong.
  //
  // The cause was that the correct label lived in `EssentialsStep`'s component state, which a
  // reload drops, while the field the flow PERSISTS (`essentials.model`) is the app the lane
  // installed. So the recap fell back to a field that answers "which model-provider app did
  // you install" and rendered it under the words "Chat model".
  //
  // 🔴 THESE ASSERT THE RENDERED SENTENCE, not the state behind it. A test on the state would
  // have passed throughout: the binding was never wrong. The sentence is the contract.
  it('#3528 names the bound model on a re-entered run, not the app that provides it', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true,
      // The app the lane installed — still recorded, still the honest answer to its own
      // question, and still not a model.
      step: 'first_success', essentials: { model: 'ollama-models', search: false, speech: false, channel: null },
      // What `active_models.json` actually holds. The provider name carries a space and the
      // model id carries its own colon, which is the pair that catches a careless split.
      chat_model_refs: ['Local Ollama:qwen2.5vl:7b'],
      first_success: { knowledge: false, trigger: false, loop: false },
    })
    await enterName()
    // SURFACE 1 — the collapsed step-3 row, read before walking on.
    const essentialsRow = screen.getByText('Essential apps').closest('li') as HTMLElement
    expect(essentialsRow.textContent).toContain('qwen2.5vl:7b')
    expect(essentialsRow.textContent).not.toContain('ollama-models')

    // SURFACE 2 — the "All set" recap, the last screen of first-run setup.
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByText('Chat model: qwen2.5vl:7b')).toBeTruthy()
    // The app name appears NOWHERE on the screen that is telling the user what they now have.
    expect(screen.queryByText(/ollama-models/)).toBeNull()
  })

  it('#3528 names the mechanism, not a model, when the home has no explicit binding', async () => {
    // `needs_model: false` with an empty chain means resolution comes from the implicit
    // "first capable configured provider" rule. There is no model the user chose, so the recap
    // may not name one — and it may not fall back to the app name either.
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: false,
      chat_model_refs: [],
      step: 'first_success', essentials: { model: 'ollama-models', search: false, speech: false, channel: null },
    })
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByText('Chat model: Ready — using a configured provider')).toBeTruthy()
    expect(screen.queryByText(/ollama-models/)).toBeNull()
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
    // The handle is '' rather than a slug of the default name: the guard needs a non-empty
    // NAME, nothing needs a handle, and a skipped run must not be stamped `operator` (TSE-1).
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Operator', ''))
    expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
    // …and the rail marker is written, so the skipper gets the starter rail like anyone else.
    expect(readNavDisclosure().mode).toBe('starter')
  })

  it('names the default it will use, rather than renaming you silently', async () => {
    renderFlow()
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    // The link stayed short and the consequence moved into the caption beside it, which also says
    // where to come back — the two things the old one-line label left out.
    expect(screen.getByRole('button', { name: 'Skip setup for now' })).toBeTruthy()
    expect(screen.getByText(/you'll be called "Operator" until you pick a name/)).toBeTruthy()
  })

  it('skips from a MIDDLE step, keeping the name that was typed', async () => {
    await enterNameAndImport()
    expect(await screen.findByRole('button', { name: 'stub-continue' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip the rest of setup' }))
    // Both halves of identity survive the skip: the name that was typed, and the handle the
    // untouched field was showing when the name step was passed (TSE-1).
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace'))
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
