import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { Plus } from 'lucide-react'
import { EmptyState } from './ListScaffold'

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
