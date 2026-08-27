import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { Plus } from 'lucide-react'
import { EmptyState, ListRow } from './ListScaffold'

// ── The primary-empty idiom, locked (design-system consistency S3/T3.1) ──────
// `EmptyState` is the ONE home for a list page's "nothing exists yet" state. The
// interaction-pattern convergence (cy17) brought the last hand-rolled empties
// (LoopsListPage, CodeSection) onto it — a codification, not a redesign. These
// tests pin the exact markup those call-sites now depend on, so a later edit to
// the primitive can't silently drift the pattern the whole app inherits:
//
//  1. NO-ICON branch = the Spark mark at 36px (LoopsListPage's "No loops yet").
//  2. ICON branch = a tinted `size-12 rounded-xl` chip wrapping a 26px
//     `text-primary` glyph (CodeSection's "No code projects yet"). This is the
//     canonical treatment outliers normalize TO — a bare dim glyph is the drift.
//  3. The hint rides the on-ramp type size and the 420px measure.
//  4. The CTA is a default-size Button (never sm) with a 16px leading icon.
//
// Rendered-DOM assertions (not source scans): the invariant is what the browser
// paints, defaults included.

function classOf(el: Element | null): Set<string> {
  return new Set((el?.getAttribute('class') ?? '').trim().split(/\s+/).filter(Boolean))
}
function expectTokens(el: Element | null, tokens: string[]) {
  const have = classOf(el)
  for (const t of tokens) expect(have, `missing "${t}" in: ${[...have].join(' ')}`).toContain(t)
}

describe('EmptyState', () => {
  it('outer column is the shared centered idiom', () => {
    const { container } = render(<EmptyState title="Nothing here" />)
    expectTokens(container.firstElementChild, [
      'flex', 'flex-col', 'items-center', 'gap-l', 'py-2xl', 'text-center',
    ])
  })

  it('no-icon branch renders the Spark mark, not a tinted chip', () => {
    const { container } = render(<EmptyState title="No loops yet" />)
    // Spark renders an <svg>; there must be NO tinted rounded chip wrapper.
    expect(container.querySelector('svg')).not.toBeNull()
    expect(container.querySelector('span.rounded-xl')).toBeNull()
  })

  it('icon branch wraps the glyph in the canonical tinted size-12 chip', () => {
    const { container } = render(<EmptyState icon={Plus} title="No code projects yet" />)
    const chip = container.querySelector('span.rounded-xl')
    expectTokens(chip, ['inline-flex', 'size-12', 'items-center', 'justify-center', 'rounded-xl'])
    // color-mix primary tint on the chip; primary-toned glyph inside it.
    expect(chip?.getAttribute('style')).toContain('color-mix')
    expect(classOf(chip?.querySelector('svg') ?? null)).toContain('text-primary')
  })

  it('hint rides the on-ramp type size and the 420px measure', () => {
    const { container } = render(<EmptyState title="t" hint="a subline" />)
    const p = container.querySelector('p')
    expectTokens(p, ['mt-1', 'max-w-[420px]', 'text-on-surface-low', 'text-[0.9375rem]'])
    // The on-ramp size only — never the off-ramp 0.875rem (14px) drift.
    expect(classOf(p)).not.toContain('text-[0.875rem]')
    // No hint → no paragraph at all.
    expect(render(<EmptyState title="t" />).container.querySelector('p')).toBeNull()
  })

  it('title is the headline-s role on the on-surface tone', () => {
    const h2 = render(<EmptyState title="No loops yet" />).container.querySelector('h2')
    expect(h2?.getAttribute('data-type')).toBe('headline-s')
    expect(classOf(h2)).toContain('text-on-surface')
    expect(h2?.textContent).toBe('No loops yet')
  })

  it('CTA is a default-size Button (not sm) and fires onClick', () => {
    const onClick = vi.fn()
    const { container } = render(
      <EmptyState title="t" action={{ label: 'Start a loop', onClick, icon: Plus }} />,
    )
    const btn = container.querySelector('button')
    // The default Button renders the md height rung (h-10); a sm CTA (h-8) is the
    // outlier cy17 normalized away — locking h-10 keeps empties from shrinking.
    expectTokens(btn, ['h-10'])
    expect(btn?.textContent).toContain('Start a loop')
    fireEvent.click(btn!)
    expect(onClick).toHaveBeenCalledTimes(1)
    // No action → no button.
    expect(render(<EmptyState title="t" />).container.querySelector('button')).toBeNull()
  })
})

// ── ListRow keyboard operability, locked (#307) ───────────────────────────────
// A clickable ListRow is a motion.div, and `whileTap` makes framer-motion mark it
// focusable — so Tab landed on rows that Enter/Space could not fire, across all 11
// list surfaces. These tests pin the button semantics on the interactive branch and,
// just as importantly, pin that the STATIC branch stays inert: adding a role or a tab
// stop to non-clickable rows would flood every list with phantom keyboard stops.
//
// The GUARANTEE is unchanged; the MECHANISM moved. The row's button used to be the
// wrapper itself (`role="button"` + a hand-rolled Enter/Space handler), which made any
// row carrying its own controls `nested-interactive` — 60 nodes across knowledge and
// workflows. The tab stop is now a real <button> stretched over the row as a SIBLING of
// the content, so these tests probe that element instead of the wrapper.
//
// That is strictly stronger than what they locked before: Enter/Space activation and the
// no-page-scroll-on-Space behaviour come from the platform rather than from a keydown
// branch we maintain, so they cannot regress by someone editing the handler.

describe('ListRow', () => {
  it('an interactive row exposes ONE button-role tab stop', () => {
    const { getByRole, container } = render(<ListRow onClick={() => {}} label="Row">Row</ListRow>)
    const hit = getByRole('button', { name: 'Row' })
    expect(hit.tagName).toBe('BUTTON')
    // Natively focusable — no explicit tabindex needed, and none should be added.
    expect(hit.getAttribute('tabindex')).toBeNull()
    // Exactly one stop: the wrapper is pinned to -1 because `whileTap` sets tabindex="0"
    // on it, which would otherwise give every row a second, nameless stop.
    expect(container.querySelectorAll('[tabindex="0"]').length).toBe(0)
    expect(container.firstElementChild!.getAttribute('tabindex')).toBe('-1')
  })

  it('activates on click — including the synthetic click Enter/Space produce', () => {
    // A native <button> converts Enter and Space into a click event for us (and Space
    // does not scroll the page). fireEvent.click is the same event those keys dispatch;
    // jsdom does not synthesise it from keydown, so this asserts the path they take.
    // Verified separately in the browser: Enter and Space both open the row, and Space
    // leaves scrollY unchanged.
    const onClick = vi.fn()
    const { getByRole } = render(<ListRow onClick={onClick} label="Row">Row</ListRow>)
    fireEvent.click(getByRole('button', { name: 'Row' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('ignores other keys so list-level shortcuts still pass through', () => {
    const onClick = vi.fn()
    const { getByRole } = render(<ListRow onClick={onClick} label="Row">Row</ListRow>)
    const hit = getByRole('button', { name: 'Row' })
    fireEvent.keyDown(hit, { key: 'ArrowDown' })
    fireEvent.keyDown(hit, { key: 'Escape' })
    expect(onClick).not.toHaveBeenCalled()
  })

  it('interactive rows name their keyboard focus with the shared inset ring', () => {
    // The ring is drawn on the ROW, keyed off the overlay's focus via `:has()`. It cannot
    // live on the overlay: that sits at `-z-10`, so its own ring would paint behind this
    // element's background (measured in the browser — nothing reached the screen). The
    // `> button` scope is narrower than `focus-within` on purpose, so focusing a checkbox
    // or tag filter INSIDE the row does not also ring the row and double up with that
    // control's own indicator.
    const { container } = render(<ListRow onClick={() => {}} label="Row">Row</ListRow>)
    const have = classOf(container.firstElementChild)
    for (const t of ['has-[>button:focus-visible]:ring-2', 'has-[>button:focus-visible]:ring-inset',
      'has-[>button:focus-visible]:ring-primary']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('a static row stays inert — no role, no tab stop, no focus ring', () => {
    const { container } = render(<ListRow>Row</ListRow>)
    const row = container.firstElementChild!
    expect(row.getAttribute('role')).toBeNull()
    expect(row.getAttribute('tabindex')).toBeNull()
    expect(classOf(row)).not.toContain('focus-visible:ring-2')
    expect(classOf(row)).not.toContain('cursor-pointer')
  })
})
