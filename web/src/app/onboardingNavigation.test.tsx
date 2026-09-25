// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The first-run flow is navigable: you know where you are, back works, refresh survives ─────────
//
// The owner's verdict on this screen was "illnavigable". Driven on a fresh home, the four things a
// stranger needs were each broken in a different way:
//
//  · **Back destroyed forward.** From step 3, returning to step 1 re-derived steps 2-5 as
//    `upcoming`, which removed every row-header button — the page was left with `Continue` and
//    `Skip setup` and no way to reach step 5 except by redoing all four steps.
//  · **A resume marked steps complete that never ran.** Jumping from the name step to the try step
//    put a green check on "Bring your setup over", a screen that user had never seen.
//  · **Refresh reset to step 1 and lost the typed name.** With the server reporting
//    `step: "essentials"`, a reload announced "Step 1 of 5: Your name" with both identity fields
//    empty.
//  · **Skip said nothing** about what it cost or how to come back — and the only door back
//    (Settings → Account) cleared the operator's name to get in.
//
// These are render-level tests on purpose: `onboarding/stepMachine.test.ts` proves the machine's
// properties, and this file proves the SCREEN is wired to it — that the rows, the fields, the URL
// and the copy all follow. The step components are stubbed, as in `onboardingProgress.test.tsx`,
// because what is under test is the shell's navigation.

const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const setName = vi.fn()
const notify = vi.fn()

vi.mock('../lib/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    // Kept PENDING deliberately: a promise settling after render lands a setState outside act(),
    // and "not loaded yet" is a real state for all three.
    themes: () => new Promise(() => {}),
    personalclawConfig: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  },
}))
vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  // A FRESH install: no stored name, no stored handle. The re-run case (which seeds both) is the
  // Settings path and is covered by `AccountPanel`'s own wiring.
  return { ...real, useIdentity: () => ({ name: '', username: '', setName }) }
})
// The flow reports a refused identity write through the app toast — the same channel the done
// screen's auto-update switch already uses.
vi.mock('./appSdk', async (orig) => {
  const real = await orig<typeof import('./appSdk')>()
  return { ...real, notify: (...a: unknown[]) => notify(...a) }
})
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))
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

const ORIGINAL_MATCH_MEDIA = window.matchMedia

const FRESH = { needs_model: true, has_model_provider: false, has_chat_binding: false }

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  localStorage.clear()
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
  cleanup()
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

const nav: { path: string; replace: boolean }[] = []

function renderFlow(sub = '', deferred = '') {
  nav.length = 0
  return render(
    <AppearanceProvider>
      <OnboardingHarness sub={sub} deferred={deferred}
        onNavigate={(path, replace) => nav.push({ path, replace })} />
    </AppearanceProvider>,
  )
}

async function mounted() {
  await waitFor(() => expect(onboarding).toHaveBeenCalled())
}

/** The visible progress line for the step the user is standing on. */
const announced = () => screen.getByRole('status', { hidden: true }).textContent

/** A row's header button, if the flow made that row reachable. `null` when it did not. */
const rowButton = (n: number, title: string) =>
  screen.queryByRole('button', { name: `Go back to step ${n}: ${title}` })

/** Pass the name step on an already-rendered flow — the gate every other step sits behind. */
function passName(name = 'Ada Lovelace') {
  fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: name } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
}

/** Render a fresh flow and pass the name step. Renders here rather than in each test so no test
 *  can accidentally assert against nothing rendered at all. */
async function startAndPassName(name = 'Ada Lovelace', sub = '') {
  renderFlow(sub)
  await mounted()
  passName(name)
}

describe('the user always knows where they are, and how much is left', () => {
  it('🔑 the ACTIVE step states its position AND the total, visibly', async () => {
    // The old visible label read a bare "Step 3" while the screen-reader announcement said
    // "Step 3 of 5" — the eye got the position and not the size of the job.
    renderFlow()
    await mounted()
    expect(screen.getByText('Step 1 of 5')).toBeTruthy()
    // Collapsed rows stay bare, so the stack does not repeat "of 5" five times.
    expect(screen.getByText('Step 2')).toBeTruthy()
  })

  it('announces the same position it shows', async () => {
    renderFlow()
    await mounted()
    expect(announced()).toBe('Step 1 of 5: Your name')
    passName()
    await waitFor(() => expect(announced()).toBe('Step 2 of 5: Bring your setup over'))
  })

  it('each step is a URL, so it is addressable at all', async () => {
    renderFlow()
    await mounted()
    // The bare route is corrected to the step it resolved to, with REPLACE — a correction is not a
    // place the user navigated to, so Back must not return them to it.
    await waitFor(() => expect(nav).toContainEqual({ path: 'onboarding/name', replace: true }))
    passName()
    // …and a move the user made is a PUSH, which is what makes the browser's Back button walk steps.
    await waitFor(() => expect(nav).toContainEqual({ path: 'onboarding/import', replace: false }))
  })
})

describe('🔴 back works, and going back does not destroy going forward', () => {
  it('offers a named Back control on every step after the first', async () => {
    renderFlow()
    await mounted()
    expect(screen.queryByRole('button', { name: /^Back to/ }), 'nothing to go back to on step 1').toBeNull()
    passName()
    expect(await screen.findByRole('button', { name: 'Back to your name' })).toBeTruthy()
  })

  it('🔑 keeps every step reached still reachable after walking back to step 1', async () => {
    // THE DEFECT, stated as a test. Measured before the fix: from step 3, clicking "Go back to
    // step 1" left the page with exactly two buttons (Continue, Skip setup) — rows 2-5 all became
    // plain divs, so the only way forward was to redo the flow.
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByRole('button', { name: /Start using/ })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Go back to step 1: Your name' }))
    await waitFor(() => expect(announced()).toBe('Step 1 of 5: Your name'))

    // Every step the run reached is still one click away — including the recap, which is where the
    // old flow stranded the user.
    expect(rowButton(2, 'Bring your setup over')).toBeTruthy()
    expect(rowButton(3, 'Essential apps')).toBeTruthy()
    expect(rowButton(4, 'Try one')).toBeTruthy()
    expect(rowButton(5, 'All set')).toBeTruthy()

    fireEvent.click(rowButton(5, 'All set') as HTMLElement)
    await waitFor(() => expect(announced()).toBe('Step 5 of 5: All set'))
    expect(screen.getByRole('button', { name: /Start using/ })).toBeTruthy()
  })

  it('going back discards nothing — not the name, not the recorded outcomes', async () => {
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-imported' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Back to bring your setup over' }))
    await waitFor(() => expect(announced()).toBe('Step 2 of 5: Bring your setup over'))
    // The import step's own outcome is still on its row, and the name step still reports the
    // identity it captured.
    fireEvent.click(screen.getByRole('button', { name: 'Go back to step 1: Your name' }))
    await waitFor(() => expect(screen.getByPlaceholderText('Your name')).toHaveValue('Ada Lovelace'))
    expect(screen.getByText('2 imported')).toBeTruthy()
  })

  it('going back never lowers the recorded high-water mark', async () => {
    // A lowered mark would make the NEXT reload resume at the step the user merely reviewed.
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
    const forward = saveOnboardingState.mock.calls.map(([p]) => p.step)
    fireEvent.click(screen.getByRole('button', { name: 'Go back to step 1: Your name' }))
    await waitFor(() => expect(announced()).toBe('Step 1 of 5: Your name'))
    expect(saveOnboardingState.mock.calls.map(([p]) => p.step)).toEqual(forward)
  })
})

describe('🔴 a step whose outcome is unknown reads unknown, never complete', () => {
  it('🔑 a resume that jumps over a step does not put a check on it', async () => {
    // Measured before the fix: row state came from `ORDER.indexOf(step)`, so both skipped-over rows
    // rendered `done`. "Bring your setup over" has no server-side evidence at all, so there is
    // nothing that could honestly mark it complete.
    onboarding.mockResolvedValue({
      ...FRESH, needs_model: false, has_model_provider: true, has_chat_binding: true,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
      first_success: { knowledge: false, trigger: false, loop: false },
    })
    await startAndPassName()
    // The run resumes at the try step…
    await waitFor(() => expect(announced()).toBe('Step 4 of 5: Try one'))
    // …and the import step it jumped is NOT claimed: no summary, and it still offers to be done.
    const importRow = screen.getByText('Bring your setup over').closest('li') as HTMLElement
    expect(importRow.querySelector('svg.lucide-check'), 'no check on a step never seen').toBeNull()
    expect(rowButton(2, 'Bring your setup over'), 'and it is reachable so it can be done').toBeTruthy()
    // The essentials step DOES carry evidence — a live-resolvable chat model — so it is claimed.
    const essentialsRow = screen.getByText('Essential apps').closest('li') as HTMLElement
    expect(essentialsRow.querySelector('svg.lucide-check')).toBeTruthy()
    expect(screen.getByText('anthropic-models')).toBeTruthy()
  })

  it('a skipped step is marked skipped, not done', async () => {
    // A green check beside the word "Skipped" is a contradiction, and the check is what the eye
    // believes. `skipped` renders its own quiet mark.
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    await waitFor(() => expect(screen.getByText('Skipped')).toBeTruthy())
    const importRow = screen.getByText('Bring your setup over').closest('li') as HTMLElement
    expect(importRow.querySelector('svg.lucide-check')).toBeNull()
    expect(importRow.querySelector('svg.lucide-minus')).toBeTruthy()
  })

  it('a skip does not erase work the home already recorded', async () => {
    onboarding.mockResolvedValue({
      ...FRESH, needs_model: false, has_model_provider: true, has_chat_binding: true,
      step: 'first_success', first_success: { knowledge: true, trigger: false, loop: false },
    })
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    // The card that succeeded before the reload still counts as a first success.
    expect(await screen.findByText('First success: 1 of 3 tried')).toBeTruthy()
  })
})

describe('🔴 refresh mid-flow keeps the step AND what was entered', () => {
  it('🔑 a reload lands on the step the run reached, with the name still in the field', async () => {
    // Measured before the fix: with the server reporting `step: "essentials"` a reload announced
    // "Step 1 of 5: Your name" and both identity fields were empty — two steps of visible progress
    // and the typed name, gone.
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-imported' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'essentials' }))

    // The reload: React state is gone, the URL and the server's record are not.
    cleanup()
    onboarding.mockResolvedValue({ ...FRESH, step: 'essentials' })
    renderFlow('essentials')
    await mounted()

    await waitFor(() => expect(announced()).toBe('Step 3 of 5: Essential apps'))
    expect(screen.getByRole('button', { name: 'stub-continue' }), 'the step BODY, not just its heading').toBeTruthy()
    // …and the name survived, so finishing cannot commit the fallback for someone who gave one.
    fireEvent.click(screen.getByRole('button', { name: 'Go back to step 1: Your name' }))
    await waitFor(() => expect(screen.getByPlaceholderText('Your name')).toHaveValue('Ada Lovelace'))
  })

  it('a reload on the recap stays on the recap', async () => {
    // The old vocabulary had no id for the recap, so the furthest recorded point was the try step
    // and a reload walked the user BACK one step.
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'ready' }))

    cleanup()
    onboarding.mockResolvedValue({ ...FRESH, step: 'ready' })
    renderFlow('ready')
    await mounted()
    await waitFor(() => expect(announced()).toBe('Step 5 of 5: All set'))
  })

  it('🔴 a deep link past the name gate is refused, and the URL is corrected in place', async () => {
    // No draft, so nothing has passed the gate. Admitting the link would let `finish()` commit the
    // fallback name for a run that never asked.
    renderFlow('ready')
    await mounted()
    expect(announced()).toBe('Step 1 of 5: Your name')
    await waitFor(() => expect(nav).toContainEqual({ path: 'onboarding/name', replace: true }))
    expect(nav.some((n) => !n.replace), 'a refusal is a correction, not a navigation').toBe(false)
  })

  it('🔑 honours a hand-typed step past the mark, but claims nothing it has no evidence for', async () => {
    // The address bar is user intent, and the flow only ever pushes a step it had already unlocked —
    // so a sub-path naming one is this session's own record that the run stood there, and a MORE
    // reliable one than the server's, because the progress POST is deliberately fire-and-forget.
    // What must not happen is the recap CLAIMING work: the row records are seeded from evidence
    // only, so a jumped-to recap reports honestly that nothing is set up.
    onboarding.mockResolvedValue({ ...FRESH, step: 'import' })
    await startAndPassName()
    await waitFor(() => expect(announced()).toBe('Step 2 of 5: Bring your setup over'))
    cleanup()
    renderFlow('ready')
    await mounted()
    await waitFor(() => expect(announced()).toBe('Step 5 of 5: All set'))
    expect(screen.getByText('Chat model — set up later in Settings')).toBeTruthy()
    expect(screen.getByText(/Nothing tried yet/)).toBeTruthy()
  })

  it('the draft is dropped once identity is committed, so the NEXT run starts clean', async () => {
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    fireEvent.click(await screen.findByRole('button', { name: /Start using/ }))
    await waitFor(() => expect(setName).toHaveBeenCalled())
    expect(sessionStorage.getItem('onboarding-draft')).toBeNull()
  })
})

describe('🔴 skip says what it costs and where to come back', () => {
  it('names the default it will use, and the door back, on the first step', async () => {
    renderFlow()
    await mounted()
    expect(screen.getByRole('button', { name: 'Skip setup for now' })).toBeTruthy()
    expect(screen.getByText(/you'll be called "Operator" until you pick a name/)).toBeTruthy()
    expect(screen.getByText(/Settings → Account → Run setup again/)).toBeTruthy()
  })

  it('promises that finished work is kept, from a later step', async () => {
    await startAndPassName()
    expect(await screen.findByRole('button', { name: 'Skip the rest of setup' })).toBeTruthy()
    expect(screen.getByText(/Whatever you have finished so far is kept/)).toBeTruthy()
  })

  it('offers no skip on the recap — "Start using" is the door', async () => {
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByRole('button', { name: /Start using/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Skip/ })).toBeNull()
  })
})

describe('🔴 a refused identity write keeps the user in the flow, and says so', () => {
  it('🔑 does not bounce back to step 1 with the typed name erased', async () => {
    // THE MEASURED DEAD END. With the gateway killed, clicking the flow's one advertised exit
    // returned the user to step 1 with the field empty and `alerts: []` — nothing that advanced and
    // nothing that explained. The chain: an optimistic name flip released the route guard, the
    // remount's config read failed, the empty name sent them straight back in, and the discarded
    // write meant nothing could say why. Every failure of that PUT did it, not only a dead backend.
    setName.mockRejectedValueOnce(new Error('{"error":"config.json is read-only"}'))
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Skip the rest of setup' }))
    await waitFor(() => expect(setName).toHaveBeenCalled())
    // Still on the step, with the draft intact — and told.
    await waitFor(() => expect(notify).toHaveBeenCalledWith(
      expect.stringContaining('config.json is read-only'), 'error'))
    expect(announced()).toBe('Step 3 of 5: Essential apps')
    expect(sessionStorage.getItem('onboarding-draft'), 'the draft must survive a refusal').toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Go back to step 1: Your name' }))
    await waitFor(() => expect(screen.getByPlaceholderText('Your name')).toHaveValue('Ada Lovelace'))
  })

  it("🪤 a dead gateway is said in words, not as the browser's 'Failed to fetch'", async () => {
    // A `fetch` that never completes rejects with a TypeError carrying the browser's own string.
    // `lib/errText` cannot help — it turns a failed RESPONSE into a sentence and there is no
    // response — and "Failed to fetch" tells a first-run user nothing they can act on.
    setName.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'Skip the rest of setup' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(
      expect.stringContaining("didn't respond — check it is still running"), 'error'))
    expect(notify.mock.calls.at(-1)?.[0]).not.toContain('Failed to fetch')
  })

  it('🪤 and the report has somewhere to land — the flow ships its own toast host', async () => {
    // A reported failure with no surface is the same defect wearing a fix. `App`'s onboarding branch
    // returned the flow ALONE, so `<Toaster />` was mounted nowhere on the product's first screen and
    // every `notify()` in here — including the done screen's auto-update switch, inert the same way —
    // rendered into nothing. Measured: the refusal above kept the user in place correctly and said
    // nothing at all. Asserted on `App.tsx` because this component cannot mount its own host.
    const app = readFileSync(join(process.cwd(), 'src/app/App.tsx'), 'utf8')
    const at = app.indexOf("if (route === 'onboarding' || !onboarded)")
    expect(app.slice(at, at + 900)).toMatch(/<Toaster \/>/)
  })

  it('claims nothing about the run until the write that ends it has landed', async () => {
    // A `done` resume point or a rail marker written before the commit would be a claim the flow
    // finished when it did not.
    setName.mockRejectedValueOnce(new Error('nope'))
    await startAndPassName()
    fireEvent.click(await screen.findByRole('button', { name: 'Skip the rest of setup' }))
    await waitFor(() => expect(setName).toHaveBeenCalled())
    expect(saveOnboardingState.mock.calls.map(([p]) => p.step)).not.toContain('done')
    expect(localStorage.getItem('nav-disclosure')).toBeNull()
  })
})
describe('🔴 the redirect into setup is a deferral, not a hijack', () => {
  it('says so when the guard pulled the user off a route they asked for', async () => {
    // A fresh home pulls EVERY route to `#/onboarding`. With nothing said, that is
    // indistinguishable from the app swallowing the click.
    renderFlow('', 'settings/providers')
    await mounted()
    expect(screen.getByText(/Setup comes first/)).toBeTruthy()
    expect(screen.getByText(/straight to the page you asked for/)).toBeTruthy()
  })

  it('🪤 stays quiet on an ordinary first load, and the value is a PROP', async () => {
    // It was a `useState(() => peekOnboardingExit())` first, and it NEVER RENDERED: `App` returns
    // this component during its first render and the guard that records the destination is an
    // effect, so the slot was still empty when the initialiser ran. Measured live — asking for
    // `#/settings/providers` on a fresh home landed on the flow with no deferral line at all. The
    // guard owns the slot and passes down what it put there.
    renderFlow()
    await mounted()
    expect(screen.queryByText(/Setup comes first/)).toBeNull()
  })
})

describe('🔴 arriving at a step leaves focus on ONE thing: the new step’s heading', () => {
  // 🔑 WHY THIS LIVES HERE AND NOT BESIDE THE OTHER FOCUS TESTS. `stepsReachableByKeyboard.test.tsx`
  // already proves a `StepRow` focuses its `<h2>` when it becomes active — rendering the ROW on its
  // own. That is exactly the tree in which this defect is invisible: the row's effect was correct and
  // the WHOLE FLOW still ended up with focus somewhere else, because `Onboarding`'s arrival effect
  // called `row.focus()` as well and React runs a child's effects before its parent's. Two correct
  // fixes for one defect, and effect ORDER picked the weaker.
  //
  // So the assertion has to be made on the assembled flow, which jsdom can do perfectly well —
  // `document.activeElement` and `tabindex` are semantics, not layout. Nobody had looked; the only
  // thing that caught it was the browser rail's "advancing a step moves focus to the new step, not to
  // <body>" (`web/e2e/onboardingGeometry.spec.ts`), reporting `{ tag: 'li' }` where it wanted
  // `{ tag: 'h2' }`.
  it('focus lands on the heading of the step just opened', async () => {
    await startAndPassName()
    const h = await screen.findByRole('heading', { name: 'Bring your setup over' })
    expect(h.tagName, 'the step title must be a real heading for focus to mean anything').toBe('H2')
    await waitFor(() => expect(document.activeElement).toBe(h))
  })

  it('🪤 and the row itself is not a competing focus target', async () => {
    // The floor that makes the assertion above hard to satisfy by accident. A second programmatic
    // focus target on the same arrival is how this broke: whichever effect ran last silently decided
    // the destination, and a `<li>` is the worse one — no role, no accessible name, so a screen
    // reader reads the row's entire text content (title, position, subtitle AND the expanded step
    // body) instead of "heading level 2, Bring your setup over".
    await startAndPassName()
    await screen.findByRole('heading', { name: 'Bring your setup over' })
    const active = document.querySelector('[aria-current="step"]') as HTMLElement
    expect(active, 'the active row must be identifiable').toBeTruthy()
    expect(active.tagName).toBe('LI')
    expect(active.getAttribute('tabindex'), 'the active row must not be focusable').toBeNull()
    // Exactly one thing in the flow is a programmatic focus destination, and it is the heading.
    expect(Array.from(document.querySelectorAll('[tabindex="-1"]')).map((el) => el.tagName)).toEqual(['H2'])
  })

  it('🪤 mounting on step 1 leaves the caret in the name field', async () => {
    // The first render is not an arrival, and the name field carries `autoFocus`. Moving focus (or
    // the page) on mount would take the user off the one field the whole flow is gated on.
    renderFlow()
    await mounted()
    expect(document.activeElement).toBe(screen.getByPlaceholderText('Your name'))
  })
})
