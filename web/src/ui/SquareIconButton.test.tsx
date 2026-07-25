import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { Copy } from 'lucide-react'
import { SquareIconButton } from './SquareIconButton'

// ── Dense square icon-button contract (design-system consistency S2/T2.3) ──────
// This primitive codifies the size-7 rounded-md dense icon-action role that was
// hand-rolled five times (two byte-identical settings copies + three ui/ near-
// variants) with a dangerous prop collision: the settings copies used `active`
// to mean "busy → disabled", ContentSurface used it to mean "selected → coral".
// The primitive splits that into orthogonal `on` (selected) and `disabled`
// (busy) — this test locks the four visual states and the semantics so a future
// edit that re-conflates them, drops the coral tint, or lets a disabled button
// fire its onClick reddens here.

function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('SquareIconButton', () => {
  it('idle render is the size-7 rounded-md ink-low hit area with bg-fill hover', () => {
    const { container } = render(<SquareIconButton icon={Copy} label="Copy" onClick={() => {}} />)
    const btn = container.querySelector('button')
    const have = classOf(btn)
    for (const t of ['size-7', 'rounded-md', 'text-on-surface-low', 'transition-colors',
      'hover:bg-surface-high', 'hover:text-on-surface']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
    // Idle carries no coral tint and no inline background.
    expect(have).not.toContain('text-primary')
    expect(btn?.getAttribute('style') ?? '').toBe('')
  })

  it('on-state carries the coral tint (text + bg chip), never the idle hover', () => {
    const { container } = render(<SquareIconButton icon={Copy} label="Wrap" on onClick={() => {}} />)
    const btn = container.querySelector('button')
    expect(classOf(btn)).toContain('text-primary')
    // Selected replaces the hover-fill affordance with a persistent tint chip.
    expect(classOf(btn)).not.toContain('hover:bg-surface-high')
    expect(btn?.getAttribute('style') ?? '').toContain('color-mix')
  })

  it('disabled dims, marks aria-disabled, and suppresses onClick', () => {
    const onClick = vi.fn()
    const { container } = render(<SquareIconButton icon={Copy} label="Test" disabled onClick={onClick} />)
    const btn = container.querySelector('button')!
    expect(classOf(btn)).toContain('opacity-40')
    expect(classOf(btn)).toContain('cursor-not-allowed')
    expect(btn.getAttribute('aria-disabled')).toBe('true')
    fireEvent.click(btn)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('disabled never shows the coral chip even when also on', () => {
    const { container } = render(<SquareIconButton icon={Copy} label="Save" on disabled onClick={() => {}} />)
    const btn = container.querySelector('button')
    // A busy-but-selected button reads as inert, not lit — no inline tint chip.
    expect(btn?.getAttribute('style') ?? '').toBe('')
  })

  it('exposes the label as both accessible name and tooltip; enabled click fires', () => {
    const onClick = vi.fn()
    const { getByRole } = render(<SquareIconButton icon={Copy} label="Copy contents" onClick={onClick} />)
    const btn = getByRole('button', { name: 'Copy contents' })
    expect(btn.getAttribute('title')).toBe('Copy contents')
    fireEvent.click(btn)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('renders children when no icon prop is given (for state-swapping glyphs)', () => {
    const { getByTestId } = render(
      <SquareIconButton label="Custom" onClick={() => {}}><span data-testid="glyph" /></SquareIconButton>,
    )
    expect(getByTestId('glyph')).toBeInTheDocument()
  })

  it('tone=danger tints the glyph red on hover with no fill (destructive delete/remove)', () => {
    const { container } = render(<SquareIconButton icon={Copy} label="Delete" tone="danger" onClick={() => {}} />)
    const have = classOf(container.querySelector('button'))
    // Idle stays ink-low; hover reddens the glyph only — never the neutral bg-fill.
    expect(have).toContain('text-on-surface-low')
    expect(have).toContain('hover:text-danger')
    expect(have).not.toContain('hover:bg-surface-high')
    expect(have).not.toContain('text-primary')
  })
})
