import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { QuietButton } from './QuietButton'
import { TileButton } from './TileButton'
import { AddItemButton } from './AddItemButton'

// ── Three button tiers were missing half of DESIGN.md:221's six states ────────────────────────
//
// `DESIGN.md` §5: "every interactive component has default, hover, focus-visible, active/pressed,
// disabled, and (where async) loading states". Measured on `origin/main` before this change, per
// tier — `git grep -c` in the primitive's own source:
//
//                    disabled   disabledReason   press (whileTap)
//   Button                 ✓            ✓               ✓
//   SquareIconButton       ✓            ✓               ✓
//   IconButton             ✓            –               ✓
//   QuietButton            ✗            ✗               ✗   ← whole prop list was
//   TileButton             ✗            ✗               ✗     children/onClick/onDoubleClick/
//   AddItemButton          ✗            ✗               ✗     title/ariaExpanded/className
//
// 52 `<QuietButton|TileButton|AddItemButton>` instances across 26 files inherit those three rows.
//
// ── THE SHARP CONSEQUENCE, which is why this is a fix and not a tidy-up ───────────────────────
//
// `pages/workflows/OutboxPanel.tsx` renders a QuietButton whose ONLY job is
// `fileInputRef.current?.click()`, and five lines below it the `<input type="file">` it forwards
// to is `disabled={dropBusy}`. `.click()` on a disabled input is a no-op that fires no event, so
// for the entire hand-over window the button was:
//
//   fully lit (ink-low → ink on hover, no dimming)   fully clickable   `aria-disabled` absent
//   every click a silent no-op                       nothing announced to anyone
//
// The label swapping to "Handing over…" is not a disabled state: it says what is happening, not
// that the control is inert. `pages/workflows/WorkflowDefDetail.tsx` is the same shape one level
// quieter — `refine` opens with `if (refining) return`, and the QuietButton sat BESIDE a `Button`
// carrying `loading` + `disabled`, so the two halves of one header row disagreed about what "in
// flight" looks like.
//
// ── After ────────────────────────────────────────────────────────────────────────────────────
//
// `QuietButton` gains the `disabled`/`disabledReason` treatment `SquareIconButton` already ships,
// VERBATIM rather than as a fourth spelling: `aria-disabled` and never the native attribute (so
// the tab stop survives and a keyboard user can reach the control and hear why), the reason riding
// `title` after an em dash, the click suppressed in the handler, `opacity-40` +
// `cursor-not-allowed` for the sighted signal. All three tiers gain the press spring.
//
// 🔑 THE PRESS ASSERTIONS HERE ARE BEHAVIOURAL, NOT A SOURCE CENSUS. framer-motion's `whileTap`
// is observable in jsdom: `pointerDown` writes `transform: scale(…)` on the element. Measured
// while writing this file — motion allowed `transform: scale(0.9727…)`, reduced motion
// `transform: none`, disabled no `style` attribute at all. So this rail reads the DOM rather than
// grepping for the prop name, and a `whileTap` wired to the wrong value fails here.
//
// 🪤 `whileTap` MAKES FRAMER PUT `tabindex="0"` ON THE ELEMENT, and it drops off again in the
// disabled branch (where `whileTap` is `undefined`) — measured `0` vs `null`. Both are focusable,
// because a native `<button>` needs no tabindex, which is why the tab-stop assertion below tests
// FOCUSABILITY and not the attribute. Asserting `tabindex="0"` would pass today and go red the
// moment the press spring moves, for no accessibility reason.

/** Press and let the spring run; returns the `scale(n)` framer wrote, or null. */
async function pressScaleOf(el: HTMLElement): Promise<number | null> {
  await act(async () => { fireEvent.pointerDown(el, { button: 0, isPrimary: true }) })
  await act(async () => { await new Promise((r) => setTimeout(r, 120)) })
  const m = /scale\(([\d.]+)\)/.exec(el.getAttribute('style') ?? '')
  return m ? Number(m[1]) : null
}

describe('QuietButton has a disabled state at all — the tier had none', () => {
  it('announces aria-disabled and NEVER the native attribute', () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="A hand-over is already in flight">Choose files</QuietButton>)
    const el = screen.getByRole('button', { name: 'Choose files' })
    expect(el.getAttribute('aria-disabled')).toBe('true')
    // The native attribute would remove the button from the tab order entirely, which is the
    // failure `Button.disabledReason` was written against ("NOT focusable", measured).
    expect(el.hasAttribute('disabled'), 'the native attribute silences the control').toBe(false)
  })

  it('keeps its tab stop, so a keyboard user can reach it and hear why', () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="A hand-over is already in flight">Choose files</QuietButton>)
    const el = screen.getByRole('button', { name: 'Choose files' })
    el.focus()
    expect(document.activeElement, 'an unavailable control a keyboard cannot reach explains nothing').toBe(el)
  })

  it('carries the reason in its tooltip, after any caller title', () => {
    render(
      <QuietButton onClick={vi.fn()} title="Choose files to hand to this run" disabled disabledReason="A hand-over is already in flight">
        Choose files
      </QuietButton>,
    )
    const el = screen.getByRole('button', { name: 'Choose files' })
    // Exactly `SquareIconButton`'s composition, em dash and order included.
    expect(el.getAttribute('title')).toBe('Choose files to hand to this run — A hand-over is already in flight')
    // …and the reason must NOT reach the accessible name: an sr-only span inside the button would
    // be concatenated into it and the action would stop being findable by its own name.
    expect(el.textContent).toBe('Choose files')
  })

  it('composes a bare reason when the caller passed no title', () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="Only the owner can do this">Delete</QuietButton>)
    expect(screen.getByRole('button', { name: 'Delete' }).getAttribute('title')).toBe('Only the owner can do this')
  })

  it('does not fire onClick or onDoubleClick', () => {
    const onClick = vi.fn()
    const onDoubleClick = vi.fn()
    render(<QuietButton onClick={onClick} onDoubleClick={onDoubleClick} disabled disabledReason="Busy">Choose files</QuietButton>)
    const el = screen.getByRole('button', { name: 'Choose files' })
    el.click()
    fireEvent.doubleClick(el)
    expect(onClick, 'a lit button whose click does nothing is a dead click').not.toHaveBeenCalled()
    expect(onDoubleClick).not.toHaveBeenCalled()
  })

  it('dims to the established 40 and shows the not-allowed cursor', () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="Busy">Choose files</QuietButton>)
    const cls = screen.getByRole('button', { name: 'Choose files' }).className
    // 40 is the level the control primitives use (`Button`, `Toggle`, `Slider`) — see
    // `design/disabledDimLevel.test.ts`, which holds the established set.
    expect(cls).toMatch(/\bopacity-40\b/)
    expect(cls).toMatch(/\bcursor-not-allowed\b/)
    // …and the hover brightening must be gone, or the control still invites the click.
    expect(cls, 'a disabled control must not brighten on hover').not.toMatch(/hover:text-on-surface\b/)
  })

  it('a NOT-disabled QuietButton is untouched — the fix is opt-in', () => {
    const onClick = vi.fn()
    render(<QuietButton onClick={onClick} title="Download the findings log">Download</QuietButton>)
    const el = screen.getByRole('button', { name: 'Download' })
    expect(el.hasAttribute('aria-disabled')).toBe(false)
    expect(el.getAttribute('title')).toBe('Download the findings log')
    expect(el.className).not.toMatch(/opacity-40/)
    el.click()
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('a reason without `disabled` changes nothing', () => {
    // The gate is `disabled && disabledReason`: a permanent tooltip explaining a condition that
    // is not currently true is noise, and `aria-disabled` on an available control is a lie.
    render(<QuietButton onClick={vi.fn()} title="Download" disabledReason="Busy">Download</QuietButton>)
    const el = screen.getByRole('button', { name: 'Download' })
    expect(el.getAttribute('title')).toBe('Download')
    expect(el.hasAttribute('aria-disabled')).toBe(false)
  })
})

describe('all three tiers acknowledge a press', () => {
  const cases: [string, () => void][] = [
    ['QuietButton', () => { render(<QuietButton onClick={vi.fn()}>Download</QuietButton>) }],
    ['TileButton', () => { render(<TileButton onClick={vi.fn()} ariaLabel="Download">tile body</TileButton>) }],
    ['AddItemButton', () => { render(<AddItemButton onClick={vi.fn()}>Download</AddItemButton>) }],
  ]

  for (const [name, mount] of cases) {
    it(`${name} springs in on pointer-down`, async () => {
      mount()
      const scale = await pressScaleOf(screen.getByRole('button', { name: 'Download' }))
      expect(scale, `${name} writes no press transform at all`).not.toBeNull()
      expect(scale, `${name} must press IN`).toBeLessThan(1)
      // "Press feedback is scale(0.97), not 0.8" (rubric lens 3). The floor stops a future edit
      // turning an acknowledgement into a collapse; the ceiling stops it becoming invisible.
      expect(scale!, `${name} presses too deep to read as a button`).toBeGreaterThan(0.9)
      expect(scale!, `${name} presses too shallow to be perceptible`).toBeLessThan(0.995)
    })
  }

  it('a DISABLED QuietButton does not spring — inert must look inert', async () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="Busy">Download</QuietButton>)
    expect(await pressScaleOf(screen.getByRole('button', { name: 'Download' }))).toBeNull()
  })
})

describe('the two call sites whose gate was invisible', () => {
  const SRC = join(process.cwd(), 'src')
  /** Comments stripped: both files EXPLAIN the defect in prose that quotes the markup, and a
   *  text scan reads comments (the recorded "a ratchet counts markup in comments" trap). */
  const code = (rel: string) =>
    readFileSync(join(SRC, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('OutboxPanel gates "Choose files" on the same flag as the input it forwards to', () => {
    const src = code('pages/workflows/OutboxPanel.tsx')
    // The button and the picker must agree. Asserted as a PAIR, because either half alone is the
    // bug: the input alone is the silent dead click, the button alone is a picker that still opens.
    expect(src, 'the QuietButton must carry the gate').toMatch(/<QuietButton[\s\S]{0,400}?disabled=\{dropBusy\}/)
    expect(src, 'the reason must be announced, not just the state')
      .toMatch(/<QuietButton[\s\S]{0,400}?disabledReason="A hand-over is already in flight"/)
    expect(src, 'the input keeps its own gate').toMatch(/type="file"[\s\S]{0,300}?disabled=\{dropBusy\}/)
  })

  it('WorkflowDefDetail gates "Refine now" on the same flag its JS guard reads', () => {
    const src = code('pages/workflows/WorkflowDefDetail.tsx')
    // `refine` opens with `if (refining) return`, so without this the second click is a no-op.
    expect(src).toMatch(/if \(refining\) return/)
    expect(src).toMatch(/<QuietButton[\s\S]{0,400}?disabled=\{refining\}/)
    expect(src).toMatch(/<QuietButton[\s\S]{0,400}?disabledReason="A refinement is already in flight"/)
  })

  it('the comment-stripping is load-bearing, not decorative', () => {
    // Vacuity guard: prove the raw source really does name the markup in prose, so that a future
    // reader cannot conclude the `.replace()` above is a no-op and delete it.
    const raw = readFileSync(join(SRC, 'pages/workflows/OutboxPanel.tsx'), 'utf8')
    expect(raw).toMatch(/disabled=\{dropBusy\}/)
    expect(raw.length, 'the file must actually carry comments').toBeGreaterThan(
      code('pages/workflows/OutboxPanel.tsx').length,
    )
  })
})

describe('the three tiers spell spacing in tokens, not Tailwind defaults', () => {
  const UI = join(process.cwd(), 'src', 'ui')
  const body = (f: string) =>
    readFileSync(join(UI, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  // `gap-1`/`gap-1.5`/`px-2` all COMPILE (Tailwind's own defaults leak past the scale) and all
  // bypass `--space-scale` and cli density — system.md trap 3. `gap-1`→`gap-xs` and `px-2`→`px-s`
  // are pixel-identical at comfortable density (4px, 8px); `gap-1.5`→`gap-xs` is NOT (6px → 4px,
  // and 6px is not a rung on the scale at all), and it lands on the icon↔label gap QuietButton
  // and SquareIconButton already use.
  it('QuietButton uses gap-xs / px-s', () => {
    const src = body('QuietButton.tsx')
    expect(src).toMatch(/\bgap-xs\b/)
    expect(src).toMatch(/\bpx-s\b/)
    expect(src).not.toMatch(/\bgap-1(\.5)?\b/)
    expect(src).not.toMatch(/\bpx-2\b/)
  })

  it('AddItemButton uses gap-xs', () => {
    const src = body('AddItemButton.tsx')
    expect(src).toMatch(/\bgap-xs\b/)
    expect(src).not.toMatch(/\bgap-1(\.5)?\b/)
  })
})
