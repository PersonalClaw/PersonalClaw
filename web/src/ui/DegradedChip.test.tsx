import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { DegradedChip } from './DegradedChip'
import { api } from '../lib/api'

// ── The shell corner is fixed-width chrome that RESERVES page header space ───
//
// `ShellCornerRight` is absolutely-positioned chrome floating over every page header, and
// it publishes its measured width to `--shell-corner-r` so each page's `TopBar` pads
// itself clear of it. That coupling is what makes a wide corner expensive: it does not
// merely look wide, it shrinks the content slot of EVERY page header in the app.
//
// This chip's text label was the widest thing in that corner — 103px of 257px (40%) —
// while every sibling (terminal toggle, bell, theme, connectivity dot) is a 28-36px icon.
// Measured at 390×844 against a live seeded gateway: the header reserved 270px of a 390px
// viewport, leaving its content slot **28px** for content that wanted **259px**, and the
// corner band painted over titles and controls on **22 of 37 surfaces** — "Native/Library/
// Store" on #/apps, "New project" on #/loops, the "48" count on #/notifications. At 1280px
// the same census reported 0, which is what identifies this as a narrow-width defect and
// not a general layout bug.
//
// Dropping the label below the mobile breakpoint returns ~100px to every page header. The
// indicator itself is preserved (the glyph and its warn tone still say "degraded"), and the
// popover — which carries the real detail: each surface, its floor, its backlog — is
// unchanged. This mirrors the precedent directly above it in the corner cluster: WidthPill
// is already dropped on mobile for the same "the corner must not starve the page" reason.
//
// Asserted through matchMedia rather than a viewport, because `useIsMobile` reads
// `matchMedia('(max-width: 768px)')` — jsdom has no layout, so the media query IS the
// observable input. Both directions are pinned: dropping the label unconditionally would
// silently degrade the desktop chip, which no viewport-less test would otherwise catch.

// NOTE: deliberately WITHOUT `use_cases`, matching the pre-existing fixture. A payload missing
// the key is the shape the width tests below have always used, and the chip must not crash on it —
// which is why the render guards on `use_cases.length` via a defaulted read.
const SURFACES = [
  { surface: 'search_ranking', available: false, floor: 'Keyword ranking', backlog: 3 },
  { surface: 'inbox_classify', available: false, floor: 'Rules only', backlog: 0 },
]

/** The real registry shape: every `DegradedContract` declares the use-cases it needs
 *  (`degraded.py` ships exactly three distinct slugs — chat, embedding, stt). */
const SURFACES_WITH_USE_CASES = [
  { surface: 'inbox_classify', available: false, floor: 'Rules only', backlog: 0, use_cases: ['chat'] },
  { surface: 'knowledge_enrich', available: false, floor: 'Documents still captured', backlog: 7, use_cases: ['embedding'] },
  { surface: 'voice_capture', available: false, floor: 'Text input keeps working', backlog: 0, use_cases: ['stt'] },
  { surface: 'future_thing', available: false, floor: 'Something still works', backlog: 0, use_cases: ['some_new_case'] },
]

/** Point `matchMedia('(max-width: 768px)')` at a fixed answer. Returns the listener-less
 *  shape `useIsMobile` needs (it subscribes via addEventListener). */
function setViewport(isMobile: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /max-width:\s*768px/.test(q) ? isMobile : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }))
}

beforeEach(() => {
  vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: SURFACES } as never)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('DegradedChip width in the shell corner', () => {
  it('drops its text label at mobile width, keeping an accessible name', async () => {
    setViewport(true)
    render(<DegradedChip />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /degraded/i }))
    // The label is the 103px the page header needs back — it must not be rendered.
    expect(btn.textContent?.trim(), 'mobile chip must render no visible text').toBe('')
    // …but the control must still be NAMED. Icon-only + title-only is not an accessible
    // name in every engine, so the aria-label carries it.
    expect(btn.getAttribute('aria-label')).toMatch(/degraded/i)
  })

  // ── The TOOLTIP's own count, which nothing asserted ────────────────────────────────────────
  //
  // 🪤 Every name assertion in this file matches `/degraded/i`, which is satisfied by any wording —
  // so the `title` clause "N surface(s) running without a model" hedged its count under a green rail.
  // Both sides of the boundary, because a hedge and a correct plural are identical above 1.
  //
  // 🪤 And note what is NOT the argument here: the accessible NAME is `summary` (via `aria-label`, and
  // only at mobile width), and `summary` already special-cases one surface by naming it. This clause
  // is the `title`, so "a screen reader speaks the parenthesis" would be the wrong claim.
  it('the tooltip agrees with its own count — singular at one surface', async () => {
    vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: [SURFACES[0]] } as never)
    render(<DegradedChip />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /degraded/i }))
    // `summary` had already got this right, which is what made the appended clause inconsistent.
    expect(btn.getAttribute('title')).toMatch(/1 surface running without a model/)
    expect(btn.getAttribute('title'), 'and not the hedge it replaced').not.toMatch(/surface\(s\)/)
  })

  it('and plural above one', async () => {
    vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: SURFACES } as never)
    render(<DegradedChip />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /degraded/i }))
    expect(btn.getAttribute('title')).toMatch(/2 surfaces running without a model/)
  })

  it('keeps its text label on desktop, where the corner has room', async () => {
    setViewport(false)
    render(<DegradedChip />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /degraded/i }))
    // Two surfaces down → the count form. Visible text is what makes the desktop chip
    // readable at a glance; a fix that dropped it everywhere would regress that.
    expect(btn.textContent).toContain('2 degraded')
    // With visible text present, an aria-label would only override it — and a redundant
    // one is how a chip ends up announced differently from how it reads.
    expect(btn.getAttribute('aria-label')).toBeNull()
  })

  it('still renders nothing when every surface has a model', async () => {
    setViewport(true)
    vi.spyOn(api, 'degraded').mockResolvedValue({
      surfaces: [{ surface: 'search_ranking', available: true, floor: '', backlog: 0 }],
    } as never)
    const { container } = render(<DegradedChip />)
    await waitFor(() => expect(api.degraded).toHaveBeenCalled())
    // The chip is an EXCEPTION indicator; a healthy system must show no corner cost at all.
    expect(container.textContent).toBe('')
  })
})

// ── The popover says what is MISSING, not only what still works ──────────────
//
// `DegradedSurface` carries `use_cases` — the `active_models` use-cases a surface needs to run at
// full capability — and the popover read 4 of the row's 5 fields, skipping exactly that one. So it
// told a user their surface was degraded and nothing about the CAUSE, on a chip whose entire job is
// "a provider went away".
//
// The backend already treats this as the headline. Its own degradation notice is:
//
//     f"No model for {', '.join(contract.use_cases)} — {contract.floor}"
//
// `floor` is the reassurance; `use_cases` is the diagnosis, and therefore the thing that tells you
// what to go bind. The popover was the one surface stating the second half without the first.
//
// Labels match ModelsPanel's `USE_CASE_META` wording ("stt" → "Speech-to-text") so the chip and the
// settings row you go bind it in agree. That map is NOT imported: it is a page-local const carrying
// icons, descriptions and chain flags for 14 use cases, and a shell chip depending on a settings
// page would be a worse coupling than three labels.
async function openPopover(surfaces: unknown[]) {
  setViewport(false)
  vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces } as never)
  const r = render(<DegradedChip />)
  await waitFor(() => expect(api.degraded).toHaveBeenCalled())
  await waitFor(() => expect(screen.getByRole('button')).toBeTruthy())
  screen.getByRole('button').click()
  await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
  return r
}

describe('the degraded popover names the missing use-case', () => {
  it('states what is missing beside what still works', async () => {
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    const text = container.textContent ?? ''
    expect(text).toContain('No model for Chat')
    expect(text).toContain('Rules only')          // the floor is still there, not replaced
  })

  it('uses the canonical use-case label, not the raw slug', async () => {
    // "stt" is the case a naive slug-prettifier gets wrong — it would render "Stt".
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    expect(container.textContent).toContain('No model for Speech-to-text')
    expect(container.textContent).not.toContain('Stt')
  })

  it('names Embedding for the knowledge surface', async () => {
    expect((await openPopover(SURFACES_WITH_USE_CASES)).container.textContent)
      .toContain('No model for Embedding')
  })

  it('falls back to a prettified slug for a use-case the map does not know', async () => {
    // A new DegradedContract must still read sensibly rather than showing a raw slug — the map is
    // deliberately minimal (only the three the registry declares), so the fallback is load-bearing.
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    expect(container.textContent).toContain('No model for Some new case')
  })

  it('renders no use-case line when the payload omits the field', async () => {
    // The pre-existing fixture has no `use_cases` at all. A missing key must not crash the chip or
    // print an empty "No model for" — the popover degrades to exactly what it showed before.
    const { container } = await openPopover(SURFACES)
    expect(container.textContent).toContain('Keyword ranking')
    expect(container.textContent).not.toContain('No model for')
  })

  it('renders no use-case line for an empty use_cases array', async () => {
    const { container } = await openPopover([
      { surface: 'x', available: false, floor: 'Still fine', backlog: 0, use_cases: [] },
    ])
    expect(container.textContent).toContain('Still fine')
    expect(container.textContent).not.toContain('No model for')
  })
})

// ── It said where to fix it, and never linked there ──────────────────────────
//
// Measured on a demo-seeded gateway whose `/api/onboarding` reports
// `{"needs_model": true, "has_model_provider": false}` — the state every fresh install sits in —
// on `#/dashboard`, opening the chip ("12 degraded"):
//
//     [role=dialog][aria-label="Degraded surfaces"]
//       button, a                      0
//       focusable descendants          0
//       rows                          12
//
// Twelve surfaces named as broken, twelve floors explained, no destination — while the panel's own
// copy issued instructions: *"Chat is unavailable without a model — the composer shows how to bind
// one."* Copy that instructs and cannot navigate is the whole defect.
//
// The destination was never in doubt: `USE_CASE_LABEL` at the top of the component exists ONLY so
// "No model for Speech-to-text" here matches the "Speech-to-text" row of ModelsPanel's
// `USE_CASE_META` you go bind it in. That deliberate word-matching was unusable without a link.
//
// AFTER, same gateway, driven by KEYBOARD in both themes at 1440×900 and 390×844: `button, a` = 1,
// focusable = 1, `#/settings/models`. Enter on the chip opens the panel, ONE Tab reaches the link
// (`:focus-visible` true, 2px solid coral outline at 2px offset), its box measures 207.2×26 so a
// 24×24 square fits (SC 2.5.8), and its ink measures 5.9:1 dark / 4.83:1 light against the
// popover's composited ground. Enter navigated to `#/settings/models`, the dialog AND the chip's
// own scrim unmounted, and `document.activeElement` was the chip trigger — not `<body>`. axe over
// the OPEN panel (wcag2a/2aa/21a/21aa/22aa): **zero violations** in both modes, with
// `color-contrast`, `link-name` and `target-size` among the passes.
//
// 🪤 THE LINK SITS ABOVE THE ROW LIST, AND THAT IS MEASURED. The panel has `max-height: none` /
// `overflow-y: visible`, so at 12 surfaces it renders 320×1770 from y=43 inside the FIXED shell
// corner, which nothing scrolls. A footer line lands at y≈1750 — outside the viewport at 1440×900,
// 1280×800 AND 390×844, so it would have shipped INERT in the only state it exists for. Above the
// rows it measures y=84 and is on screen at all three.
//
// 🪤 NO RESTING UNDERLINE, and that is measured too rather than assumed: `link-in-text-block` only
// applies to a link inside a block of text, and this is a standalone action line — axe reports it
// nowhere, in either mode. So the chip matches `#/settings/voice`'s `ManageLink` exactly (the owner
// already settled that same job — "go bind a model in Models" — as a convergence onto `TextLink`)
// instead of inventing a second inline-link idiom next door to the primitive itself.

const MODELS_LINK = 'a[href="#/settings/models"]'

describe('the degraded popover links to where you fix it', () => {
  it('offers exactly one actionable — the Models link', async () => {
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    const dialog = screen.getByRole('dialog')
    // The measured before/after: `button, a` inside the panel is the whole population of things a
    // user can act on. It was 0 while the copy told them to act.
    expect(dialog.querySelectorAll('button, a').length,
      'the panel had zero actionables; it needs exactly one').toBe(1)
    const link = dialog.querySelector<HTMLAnchorElement>(MODELS_LINK)!
    expect(link, 'and that actionable is the Models link').not.toBeNull()
    // Named by its visible text, in the words the destination page uses. No aria-label: it would
    // only override text the user can already read.
    expect(link.textContent).toContain('Settings')
    expect(link.textContent).toContain('Models')
    expect(link.getAttribute('aria-label')).toBeNull()
    // Positive control that the diagnosis it points at actually rendered.
    expect(container.textContent).toContain('No model for Speech-to-text')
  })

  it('places the link ABOVE the first surface row, where a tall panel still shows it', async () => {
    // The reachability clause. 12 surfaces make the panel 1770px tall inside fixed chrome with no
    // scroller, so DOM order is the only thing keeping this control on screen.
    await openPopover(SURFACES_WITH_USE_CASES)
    const link = screen.getByRole('dialog').querySelector(MODELS_LINK)!
    // Anchored on the first surface's NAME, not on a layout class: the action line carries the same
    // `border-b` hairline the rows do, so a class selector would match the action line itself.
    const firstRowName = screen.getByText('Inbox classify')
    expect(
      link.compareDocumentPosition(firstRowName) & Node.DOCUMENT_POSITION_FOLLOWING,
      'the link must precede the surface rows, or a long list pushes it off-screen',
    ).toBeTruthy()
  })

  it('closes the popover and its scrim on activate, and returns focus to the chip', async () => {
    // `open` is component state, not route-derived: without an explicit close, the panel and its
    // full-viewport `fixed inset-0` scrim stay mounted over the page the link just opened and
    // swallow every click on it. Focus must not be dropped on the unmounted anchor either — the
    // same contract the Escape handler above already honours.
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    const trigger = screen.getByRole('button')
    screen.getByRole('dialog').querySelector<HTMLAnchorElement>(MODELS_LINK)!.click()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(container.querySelector('.fixed.inset-0'), 'the click-away scrim must go too').toBeNull()
    expect(document.activeElement, 'focus must not be dropped on <body>').toBe(trigger)
  })

  it('offers no link in the unknown state, where no fault has been measured', async () => {
    // The chip's standing rule, kept: when the check itself could not be read it says so and
    // deliberately does NOT claim a fault. Pointing at a fix would be that same error in reverse.
    setViewport(false)
    vi.spyOn(api, 'degraded').mockRejectedValue(new Error('unreachable'))
    const { container } = render(<DegradedChip />)
    await waitFor(() => expect(screen.getByRole('button')).toBeTruthy())
    screen.getByRole('button').click()
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    expect(container.textContent).toContain('Could not read the check')
    expect(container.querySelector(MODELS_LINK), 'no destination without a diagnosis').toBeNull()
  })
})
