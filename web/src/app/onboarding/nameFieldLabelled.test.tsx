// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'

// ── The first control a new user meets must have a real, programmatic label ────────────────────
//
// `#/onboarding` is the first screen every new user sees, and it is not in `scripts/surfaces.json`
// (a route guard, not a nav destination), so no `ux-audit`, no axe rail and no baseline ever touched
// it. The name `<input>` had no `<label>`, no `aria-label`, no `aria-labelledby` — its ONLY accessible
// name was its `placeholder`, which vanishes on the first keystroke and is unreliably announced
// (WCAG 1.3.1 / 3.3.2 / 4.1.2).
//
// The fix is a field-local `aria-label` matching the visible StepRow title ("Your name") verbatim — so
// the accessible name is stable AND equals the visible label (no 2.5.3 Label-in-Name conflict). The
// visible title flows from the single-source `TITLES.name`, and this rail reads the RENDERED name
// against the RENDERED title, so neither can drift from the other or from a hardcoded literal here.
//
// 🪤 This rail used to regex `Onboarding.tsx` for an `<input>` tag carrying both `placeholder="Your
// name"` and an `aria-label=`, and TSE-1 broke it without touching the property: the identity step
// gained a second field, the two were de-duplicated into one local `PillField`, and the literals
// moved apart — the label to the call site, the attribute to the component. The scan read that as
// "the name input does not exist". A refactor that PRESERVES the accessible name must not be able to
// fail an accessible-name rail, so the property is now asserted where a screen reader would find it:
// the accessibility tree. That also makes it strictly stronger, measured: deleting
// `aria-label={ariaLabel}` from `PillField` — a mutation the regex passed, because the call site
// still reads `ariaLabel="Your name"` — fails both assertions below with "Unable to find a label
// with the text of: Your name".

const saveOnboardingState = vi.fn()
const onboarding = vi.fn()

// Stubbed exactly as the sibling flow tests stub it (`onboardingProgress`, `onboardingHandle`):
// under test is the name step's labelling, not the steps behind it.
// 🪤 One level deeper than the sibling flow tests, so every mock path here has an extra `../`
// than theirs — a wrong one does not error, it just leaves the REAL module in place and the
// assertion fails somewhere else entirely.
vi.mock('../../lib/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    // Kept PENDING deliberately: a promise settling after render lands a setState outside act().
    themes: () => new Promise(() => {}),
    personalclawConfig: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  },
}))
vi.mock('../identity', async (orig) => {
  // PARTIAL mock, so the real `suggestHandle` still feeds the second field — this file only needs
  // the flow to render, and a full mock would have to be edited every time the module grows.
  const real = await orig<typeof import('../identity')>()
  return { ...real, useIdentity: () => ({ name: '', setName: vi.fn(), username: '' }) }
})
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))

import { OnboardingHarness } from '../../test/onboardingHarness'
import { AppearanceProvider } from '../appearance'

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
  cleanup()
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

async function renderFlow() {
  render(<AppearanceProvider><OnboardingHarness /></AppearanceProvider>)
  // The flow reads its resume point on mount; asserting before that lands races the fetch.
  await waitFor(() => expect(onboarding).toHaveBeenCalled())
}

describe('the onboarding name field is programmatically labelled', () => {
  it('the input carries a real label, not just a placeholder', async () => {
    await renderFlow()
    // `getByLabelText` resolves ONLY through a label/aria-label/aria-labelledby — never a
    // placeholder — so finding the field at all is the assertion. (The control below pins that.)
    const field = screen.getByLabelText('Your name')
    expect(field.tagName).toBe('INPUT')
    // …and the placeholder is still there for sighted users; it is the SOLE-name case that failed.
    expect(field.getAttribute('placeholder')).toBe('Your name')
  })

  it('the accessible name matches the visible title source (no Label-in-Name conflict)', async () => {
    await renderFlow()
    // Both sides read from the DOM: the step's visible heading, and the input's accessible name.
    // `TITLES.name` is the single source they share, so asserting they are EQUAL covers the
    // literal without repeating it — a rename that reaches only one of the two is a red here.
    // Scoped to the `h2` because that is what `StepRow` renders the ACTIVE step's title as, and
    // `name` is the active step on first render. It is a real heading now — the focus destination a
    // step change moves to, so that advancing does not drop focus on `<body>`; the live region
    // (`stepProgressAnnounced`) still announces the change, because an announcement and a focus
    // position are two different things. The selector is a PIN on the title's element, not a claim
    // that it must not be a heading: this clause is the equality, and narrowing to the heading keeps
    // it from accidentally matching the collapsed rows' `<span>` titles.
    const title = screen.getByText('Your name', { selector: 'h2' })
    expect(screen.getByLabelText(title.textContent!).getAttribute('aria-label')).toBe(title.textContent)
  })

  it('the query would miss the placeholder-only shape it replaced', () => {
    // Vacuity control, and the old sabotage test's job: the exact markup that shipped before this
    // fix must not satisfy the query above. Rendered rather than regexed, because what is being
    // pinned is now the MATCHER — a `getByLabelText` that fell back to placeholders would make
    // every assertion in this file pass on the unlabelled input.
    render(<input autoFocus placeholder="Your name" />)
    expect(screen.queryByLabelText('Your name')).toBeNull()
    expect(screen.getByPlaceholderText('Your name')).toBeTruthy()
  })
})

// ── …and the browser can tell the two fields apart, and fill them ──────────────────────────────
//
// Both pills carried only an `aria-label`: no id, no name, no `autocomplete`. The browser flagged the
// form ("A form field element should have an id or name attribute", count 2), and autofill could not
// offer the person's own name on the first field they ever fill in. Each field now names itself and
// says how it may be filled. The handle's is `off`, not `username`: the hint under it says it is a
// label, not a login, and the sign-in username is a separate field in Settings → Account. (`nickname`,
// the spec's word for a handle, is a value Chrome rejects with an issue of its own.)
describe('the onboarding identity fields can be told apart and autofilled', () => {
  it('🔴 each field carries an id, a name and the autocomplete value for what it asks', async () => {
    await renderFlow()
    const name = screen.getByLabelText('Your name')
    const handle = screen.getByLabelText('Username')
    expect({ id: name.id, name: name.getAttribute('name'), autocomplete: name.getAttribute('autocomplete') })
      .toEqual({ id: 'onboarding-name', name: 'name', autocomplete: 'name' })
    expect({ id: handle.id, name: handle.getAttribute('name'), autocomplete: handle.getAttribute('autocomplete') })
      .toEqual({ id: 'onboarding-handle', name: 'handle', autocomplete: 'off' })
  })

  it('no field on the step is left without an id or a name', async () => {
    await renderFlow()
    const fields = Array.from(document.querySelectorAll('input, textarea, select'))
    expect(fields.length, 'the step rendered its fields').toBeGreaterThanOrEqual(2)
    expect(fields.filter((f) => !f.id && !f.getAttribute('name')).map((f) => f.outerHTML.slice(0, 80))).toEqual([])
  })
})
