import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
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
// ⚠️ `inert="false"` is STILL inert, because the attribute's mere presence applies. So it must be
// present when closed and ABSENT when open, never present-and-false, or focus is trapped in the
// OPEN drawer, which is a worse bug than the one being fixed. React 19 types `inert` as a boolean
// and omits the attribute when false, which is what makes the plain prop safe. Both directions
// are pinned below because the omission is the load-bearing half.

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

// ── A count badge that never says what it counts ───────────────────────────────────────
//
// The rail badges Projects with the ACTIVE-LOOP count ("loops live under projects now"). Measured
// on #/projects, with five projects listed:
//
//   visible text:     "Projects1"
//   accessible name:  "Projects"      ← the number is announced NOWHERE
//   title:            null
//
// Two defects in one span. A sighted user reads "Projects 1" as a count of that destination's
// contents, which is wrong — there are five projects and one running loop. And because the button
// carries `aria-label={item.label}`, and an aria-label OVERRIDES the element's text, the badge is
// dropped from the accessibility tree entirely: screen-reader users lost the ambient signal
// outright rather than merely receiving it ambiguously.
//
// The shell already solves this one corner over: the notifications bell announces
// "Notifications, 3 unread" with `title="3 unread notifications"`. `badgeLabel` gives the rail the
// same composition.
//
// 🪤 THE UNIT IS NOT ALWAYS THE SHELL'S TO NAME. `setNavBadge(appName, count)` lets an app badge
// its own tile, and the SDK deliberately leaves the unit app-defined — so the shell cannot say
// "3 notifications" without inventing a meaning it can't back up. Hence the fallback: with no
// `badgeLabel` the RAW COUNT is announced. "Projects, 1" is worse than "Projects, 1 active loop"
// and far better than silence, and it claims nothing.

const badged = (extra: Partial<{ badge: string; badgeLabel: string }>) => [
  { id: 'projects', label: 'Projects', icon: Home, ...extra },
]

describe('a nav count badge says what it counts', () => {
  it('announces the meaning, not just the number', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: 'Projects, 1 active loop' })).toBeTruthy()
  })

  it('spells it out in the tooltip too, as the bell does', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: /Projects/ }).getAttribute('title')).toBe('1 active loop')
  })

  it('still announces the COUNT when the unit is not the shell to name', () => {
    // An SDK-set app badge: no label, so the number itself must survive into the name.
    const { getByRole } = render(
      <NavRail items={badged({ badge: '3' })} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: 'Projects, 3' })).toBeTruthy()
  })

  it('leaves an unbadged item exactly as it was', () => {
    const { getByRole } = render(
      <NavRail items={badged({})} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    const b = getByRole('button', { name: 'Projects' })
    expect(b.getAttribute('title')).toBeNull()
  })

  it('carries BOTH label and meaning while collapsed, where neither is visible', () => {
    // Collapsed, the number is replaced by a bare dot that conveyed nothing on its own.
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} collapsed />,
    )
    expect(getByRole('button', { name: 'Projects, 1 active loop' }).getAttribute('title')).toBe('Projects, 1 active loop')
  })

  it('keeps the badge VISIBLY a bare number (this is a naming fix, not a redesign)', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: /Projects/ }).textContent).toBe('Projects1')
  })
})

// ── The call-site half ────────────────────────────────────────────────────────────────
// The prop existing is not the fix; the shell has to supply the meaning it knows.

describe('the shell supplies the meaning it owns', () => {
  const app = readFileSync(join(process.cwd(), 'src/app/App.tsx'), 'utf8')

  it('names the Projects badge as the active-LOOP count', () => {
    expect(app, 'the badge is activeLoops, so say so').toMatch(/badgeLabel: `\$\{activeLoops\} active loop/)
  })

  it('pluralises rather than shipping "1 active loops"', () => {
    expect(app).toMatch(/activeLoops === 1 \? '' : 's'/)
  })

  it('claims the Store badge is updates ONLY when nothing else is summed in', () => {
    // appBadgeTotal = per-app SDK badges + apps-with-updates. Calling the total "updates" while an
    // app contributed to it would be a false statement, so the label is conditional and falls back
    // to the raw count.
    expect(app).toMatch(/appBadgeTotal === updatesCount/)
    expect(app).toMatch(/app update\$\{updatesCount === 1 \? '' : 's'\} available/)
  })

  it('has not started labelling a badge it cannot explain', () => {
    // The per-app tiles: `setNavBadge` leaves the unit app-defined, so these must stay label-less
    // and fall through to the count-only name.
    const perApp = /const badge = appBadges\[[\s\S]{0,120}?badge \? \{ \.\.\.ai, badge: String\(badge\) \}/.test(app)
    expect(perApp, 'per-app SDK badges must not be given an invented unit').toBe(true)
  })
})
