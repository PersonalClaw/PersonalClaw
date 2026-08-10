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
