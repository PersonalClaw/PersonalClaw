import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { QuietButton } from './QuietButton'

// ── quiet compact inline action contract (design-system consistency S2) ───────
// Four content-viewer toolbar actions (ArtifactViewer's Source-file + Download,
// FileViewer's Artifact, LoopCockpitPage's findings-log Download) rendered this
// exact quiet inline button inline. The primitive is the single source; this
// test locks the traits that make it the RECEDING toolbar action (NOT the pill
// CTA Button) — the 28px height, ink-low label, medium radius, small text — so
// an edit that drifts any of them (e.g. bumps it toward the h-8 pill ghost
// Button) reddens here. It also proves `title` reaches the DOM (the supplementary
// tooltip three sites pass) and an extra className merges without dropping chrome.

function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('QuietButton', () => {
  it('is the 28px, ink-low, medium-radius quiet toolbar action', () => {
    const { getByRole } = render(<QuietButton>Download</QuietButton>)
    const have = classOf(getByRole('button'))
    // `gap-1`/`px-2` → `gap-xs`/`px-s`: Tailwind's own defaults compile but BYPASS the
    // `--space-scale` slider and cli density (system.md trap 3). Both are 4px/8px at
    // comfortable density, so the swap moves no pixels there and every pixel at dense/cli.
    for (const t of ['inline-flex', 'items-center', 'gap-xs', 'rounded-md',
      'px-s', 'h-7', 'text-[0.75rem]', 'text-on-surface-low',
      'hover:bg-surface-high', 'hover:text-on-surface']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('renders its children (the caller-owned leading glyph + label)', () => {
    const { getByText } = render(<QuietButton>Source file</QuietButton>)
    expect(getByText('Source file')).toBeInTheDocument()
  })

  it('forwards title (the supplementary tooltip) to the button', () => {
    const { getByRole } = render(<QuietButton title="Download this artifact">Download</QuietButton>)
    expect(getByRole('button')).toHaveAttribute('title', 'Download this artifact')
  })

  it('merges an extra className without dropping base chrome', () => {
    const { getByRole } = render(<QuietButton className="ml-auto">x</QuietButton>)
    const have = classOf(getByRole('button'))
    expect(have).toContain('ml-auto')
    expect(have).toContain('h-7')
    expect(have).toContain('text-on-surface-low')
  })
})
