import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SidePanel } from './SidePanel'

// ── The docked inspector is a named landmark region ────────────────────────────────────────────
//
// Measured on `#/tasks` with a task detail open (cycle 198): the SidePanel root had `role`,
// `aria-label` and `aria-labelledby` all null, and its title "Ship the release" was a bare <span>
// (0 headings, 0 landmarks on the page). A screen-reader user entering the panel got a stream of
// controls with no region to navigate to and no programmatic name for what the panel was — the app's
// inspector, shared by 23 surfaces. It is now a `region` named by its title via `aria-labelledby`
// (robust when `title` is a ReactNode; the name is computed from the rendered title). Attributes + an
// id only — no visual change.

describe('SidePanel is a named landmark region', () => {
  it('the docked panel exposes role="region" named by its title', () => {
    render(<SidePanel title="Ship the release" storeKey="test-panel-w" onClose={() => {}}>body</SidePanel>)
    const region = screen.getByRole('region', { name: 'Ship the release' })
    expect(region).toBeTruthy()
  })

  it('the region contains a heading named by its title, so heading-nav reaches it', () => {
    // Cycle 198 named the region; cycle 199 made the title a HEADING (an <h2>), so a screen reader can
    // jump to the panel by heading navigation — measured before: the panel had 0 headings. Verified in
    // the browser that h2 renders byte-identically to the old span (preflight resets h2 margins/weight;
    // `data-type="title-l"` sets size/line-height/wght), so this is a pure semantic upgrade.
    render(<SidePanel title="Ship the release" storeKey="test-panel3-w" onClose={() => {}}>body</SidePanel>)
    const heading = screen.getByRole('heading', { name: 'Ship the release' })
    expect(heading.tagName).toBe('H2')
    // and it is the SAME element that labels the region (one element, both roles of "the title")
    const region = screen.getByRole('region', { name: 'Ship the release' })
    expect(region.getAttribute('aria-labelledby')).toBe(heading.id)
  })

  it('the name comes from the title element, not a duplicated aria-label string', () => {
    // aria-labelledby → the <span> holding the title, so a ReactNode title still names the region and
    // the name can never drift from what is shown.
    const { container } = render(
      <SidePanel title="Chat history" storeKey="test-panel2-w" onClose={() => {}}>body</SidePanel>,
    )
    const region = screen.getByRole('region', { name: 'Chat history' })
    const labelledby = region.getAttribute('aria-labelledby')
    expect(labelledby, 'region must be labelled by an element, not a raw string').toBeTruthy()
    expect(region.getAttribute('aria-label'), 'no raw aria-label — labelledby is the source').toBeNull()
    const titleEl = container.querySelector(`#${CSS.escape(labelledby!)}`)
    expect(titleEl?.textContent).toBe('Chat history')
  })
})
