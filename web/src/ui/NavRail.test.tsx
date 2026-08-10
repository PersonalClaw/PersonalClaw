import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { Home, ListChecks } from 'lucide-react'
import { NavRail } from './NavRail'

// ── The closed mobile drawer must be INERT, not merely aria-hidden ───────────
//
// `aria-hidden` hides the drawer from the accessibility TREE but leaves its 18 nav buttons in
// the TAB ORDER. Measured at 390px before the fix: the FIRST Tab on every route landed on an
// invisible, off-screen "Home" — and axe reported `aria-hidden-focus` on all 37 surfaces, the
// most widespread violation in the app.
//
// `inert` removes focusability, pointer events and the a11y tree together, which is exactly the
// "closed drawer" semantics.
//
// ⚠️ React 18 has no typed `inert` prop and forwards unknown attributes as STRINGS — and
// `inert="false"` is STILL inert, because the attribute's mere presence applies. So it must be
// `''` when closed and OMITTED when open. A boolean would trap focus in the OPEN drawer, which
// is a worse bug than the one being fixed. These two tests pin both directions.

const ITEMS = [
  { id: 'dashboard', label: 'Home', icon: Home },
  { id: 'tasks', label: 'Tasks', icon: ListChecks },
]
// Queried by attribute, not by role: an `inert` element is removed from the accessibility tree
// altogether, so `getByRole('dialog')` cannot see the closed drawer — which is the fix working.
const drawer = (c: HTMLElement) => c.querySelector('[role="dialog"][aria-label="Navigation"]')!

describe('NavRail overlay drawer', () => {
  it('is inert while closed, so the tab order skips it', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} overlay overlayOpen={false} />)
    const d = drawer(container)
    expect(d).toHaveAttribute('inert')
    expect(d).toHaveAttribute('aria-hidden', 'true')
    // The buttons still EXIST (the drawer is only translated off-screen, not unmounted) —
    // which is precisely why `inert` is required rather than sufficient markup alone.
    expect(d.querySelectorAll('button').length).toBeGreaterThan(0)
  })

  it('drops inert entirely when open, so every nav item stays reachable', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} overlay overlayOpen />)
    const d = drawer(container)
    // Not `inert="false"` — ABSENT. Present-but-false would still disable the open drawer.
    expect(d.hasAttribute('inert')).toBe(false)
    expect(d).toHaveAttribute('aria-hidden', 'false')
    const first = d.querySelector('button')!
    first.focus()
    expect(document.activeElement).toBe(first)
  })
})
