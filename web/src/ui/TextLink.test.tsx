import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { ExternalLink } from 'lucide-react'
import { TextLink } from './TextLink'

// ── inline text-link idiom contract (design-system consistency, G4) ───────────
// ~16 sites hand-rolled `text-primary hover:underline` (with drifting size
// classes, element types, icon slots, and margins) for in-sentence navigations,
// quiet inline actions ("Remove from queue", "View all loops"), and the odd real
// external `<a>`. TextLink is the single source. This locks the traits that make
// it THAT idiom (coral label, hover-underline, disabled dim) and the API knobs the
// migrated call sites depend on — element type (button vs anchor), the size scale,
// the leading/trailing icon slot, and external-link safety attrs — so a drift in
// any of them reddens here instead of silently diverging a call site.

function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('TextLink', () => {
  it('is the coral, hover-underline, disabled-dim inline link', () => {
    const { getByRole } = render(<TextLink>Manage Sources</TextLink>)
    const have = classOf(getByRole('button'))
    for (const t of ['text-primary', 'hover:underline', 'disabled:opacity-50']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('renders a <button type="button"> by default (a quiet inline action)', () => {
    const { getByRole } = render(<TextLink>View all loops</TextLink>)
    const btn = getByRole('button')
    expect(btn.tagName).toBe('BUTTON')
    expect(btn).toHaveAttribute('type', 'button')
  })

  it('renders an <a href> when href is set, and stays bare (no flex) without an icon', () => {
    const { getByRole } = render(<TextLink href="#/settings/memory">Manage in Memory</TextLink>)
    const a = getByRole('link')
    expect(a.tagName).toBe('A')
    expect(a).toHaveAttribute('href', '#/settings/memory')
    // in-app hash link: no target/rel, and no inline-flex wrapper (flows inside text)
    expect(a).not.toHaveAttribute('target')
    expect(a).not.toHaveAttribute('rel')
    expect(classOf(a)).not.toContain('inline-flex')
  })

  it('adds off-app safety attrs only when external', () => {
    const { getByRole } = render(<TextLink href="https://example.com" external>Open</TextLink>)
    const a = getByRole('link')
    expect(a).toHaveAttribute('target', '_blank')
    expect(a).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('opts into the icon row layout and renders the glyph before the label by default', () => {
    const { getByRole } = render(<TextLink icon={ExternalLink}>Open in Artifacts</TextLink>)
    const have = classOf(getByRole('button'))
    for (const t of ['inline-flex', 'items-center', 'gap-1']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('maps the size scale to the house type sizes', () => {
    // each render adds to the shared document body, so scope to its own container
    const btnOf = (ui: Parameters<typeof render>[0]) =>
      render(ui).container.querySelector('button') as HTMLElement
    expect(classOf(btnOf(<TextLink size="xs">Clear</TextLink>))).toContain('text-[0.75rem]')
    expect(classOf(btnOf(<TextLink size="sm">Show more</TextLink>))).toContain('text-[0.8125rem]')
    // inherit (the default) adds no size class — it takes the surrounding text size
    const have = classOf(btnOf(<TextLink>inline</TextLink>))
    expect(have).not.toContain('text-[0.75rem]')
    expect(have).not.toContain('text-[0.8125rem]')
  })

  it('forwards disabled + title, and merges an extra className without dropping base chrome', () => {
    const { getByRole } = render(
      <TextLink disabled title="Filter by project" className="ml-auto">Remove from queue</TextLink>,
    )
    const btn = getByRole('button')
    expect(btn).toBeDisabled()
    expect(btn).toHaveAttribute('title', 'Filter by project')
    const have = classOf(btn)
    expect(have).toContain('ml-auto')
    expect(have).toContain('text-primary')
  })
})
