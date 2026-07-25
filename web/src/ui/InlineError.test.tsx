import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { InlineError } from './InlineError'

// ── Inline error band contract (design-system consistency S2 + cy9 fold-in) ──
// This banner was rendered byte-identically inline by the Projects list + hub and
// the Code section's failed-action callout. The primitive is the single source;
// this test locks the traits that make it *the* danger strip — role=alert, the
// rounded danger-tinted band, a flex-1 message, a corner Dismiss "×" — plus the
// optional leading icon and the per-site margin passthrough, so an edit that drops
// any of them reddens here. cy9 folded three more banners onto this band (ChatPage
// turn errors, the FilesSection file-op strip, the Tasks board's rejected-drag
// banner), adding the orthogonal `multiline`/`animated`/non-dismissible modes —
// locked below so the convergence can't silently regress.

function classOf(el: Element | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('InlineError', () => {
  it('is a role=alert danger band holding the message', () => {
    const { getByRole, getByText } = render(<InlineError onDismiss={() => {}}>Boom</InlineError>)
    const alert = getByRole('alert')
    const have = classOf(alert)
    for (const t of ['flex', 'items-center', 'gap-2', 'rounded-lg', 'px-3', 'py-2', 'text-[0.8125rem]']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
    expect(getByText('Boom')).toBeInTheDocument()
  })

  it('fires onDismiss when the corner × is pressed', () => {
    const onDismiss = vi.fn()
    const { getByLabelText } = render(<InlineError onDismiss={onDismiss}>oops</InlineError>)
    fireEvent.click(getByLabelText('Dismiss'))
    expect(onDismiss).toHaveBeenCalledOnce()
  })

  it('omits the leading icon by default and shows it when icon is set', () => {
    const { container: plain } = render(<InlineError onDismiss={() => {}}>x</InlineError>)
    // only the dismiss button's X glyph — no leading AlertTriangle
    expect(plain.querySelectorAll('svg')).toHaveLength(1)
    const { container: withIcon } = render(<InlineError icon onDismiss={() => {}}>x</InlineError>)
    expect(withIcon.querySelectorAll('svg')).toHaveLength(2)
  })

  it('merges the per-site margin className without dropping the base chrome', () => {
    const { getByRole } = render(<InlineError className="mx-l mt-2" onDismiss={() => {}}>x</InlineError>)
    const have = classOf(getByRole('alert'))
    expect(have).toContain('mx-l')
    expect(have).toContain('mt-2')
    expect(have).toContain('rounded-lg')
  })

  it('omits the corner × when no onDismiss is given (non-dismissible turn errors)', () => {
    const { queryByLabelText, container } = render(<InlineError>fatal</InlineError>)
    expect(queryByLabelText('Dismiss')).toBeNull()
    // no dismiss button, no glyphs at all without icon
    expect(container.querySelectorAll('svg')).toHaveLength(0)
  })

  it('top-aligns and wraps in multiline mode; single-line otherwise', () => {
    const { container: multi } = render(<InlineError multiline onDismiss={() => {}}>multi</InlineError>)
    const have = classOf(multi.querySelector('[role="alert"]'))
    expect(have).toContain('items-start')
    expect(have).not.toContain('items-center')
    const { container: plain } = render(<InlineError onDismiss={() => {}}>one</InlineError>)
    expect(classOf(plain.querySelector('[role="alert"]'))).toContain('items-center')
  })

  it('still renders a role=alert danger band in animated mode', () => {
    const { getByRole, getByText } = render(<InlineError animated icon onDismiss={() => {}}>drag rejected</InlineError>)
    const have = classOf(getByRole('alert'))
    for (const t of ['flex', 'gap-2', 'rounded-lg', 'px-3', 'py-2', 'text-[0.8125rem]']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
    expect(getByText('drag rejected')).toBeInTheDocument()
  })
})
