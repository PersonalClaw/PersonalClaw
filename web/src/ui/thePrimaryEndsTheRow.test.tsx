// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { Pencil, Pin, Plus, Sparkles } from 'lucide-react'
import { HeaderActions, HeaderControl, HeaderMenu } from './HeaderActions'

// ── The primary action ends the row, after the `…` ───────────────────────────────────────────────
//
// The `…` was the row's last control, so on a page whose header held one (a thread's Save as
// starter, a narrow row's shed actions) the primary was second from the right, and the one order
// had an exception. A control `isThePrimary` counts as the primary (`variant="primary"`, or
// `priority="primary"` on one that opens no region) draws in a slot after the `…` now. It is a portal
// into that slot, so the page's tree is unchanged, and the slot follows the `…` in the DOM, so a
// keyboard reaches the controls in the order they are seen.

/** The live row's buttons in DOM order, by name (the probes are `aria-hidden`). */
const row = () => screen.getAllByRole('button').map((b) => b.getAttribute('aria-label') || b.textContent?.trim())

describe('the primary action', () => {
  it('follows the `…`', () => {
    render(
      <HeaderActions>
        <HeaderControl icon={Pin} label="Pin" />
        <HeaderControl icon={Sparkles} label="Save as starter" inOverflow />
        <HeaderControl icon={Pencil} label="Edit" variant="primary" priority="primary" />
      </HeaderActions>,
    )
    expect(row()).toEqual(['Pin', 'More actions', 'Edit'])
  })

  it('a primary by priority alone follows the `…` too, and a disclosure keeps its place', () => {
    // The rail counts `priority="primary"` with no `ariaExpanded` as the primary
    // (`app/pageControlsAreInTheHeader.test.ts`); the Tools page's Add tool server is one.
    render(
      <HeaderActions>
        <HeaderControl icon={Plus} label="Add tool server" priority="primary" />
        <HeaderControl icon={Sparkles} label="Reload" inOverflow />
        <HeaderControl icon={Pin} label="Session map" priority="primary" ariaExpanded={false} />
      </HeaderActions>,
    )
    expect(row()).toEqual(['Session map', 'More actions', 'Add tool server'])
  })

  it('a primary menu too, and with no `…` it is still last', () => {
    render(
      <HeaderActions>
        <HeaderMenu icon={Plus} label="New" variant="primary" priority="primary" items={[{ label: 'New task', onSelect: () => {} }]} />
        <HeaderControl icon={Pin} label="Pin" />
      </HeaderActions>,
    )
    expect(row()).toEqual(['Pin', 'New'])
  })

  it('draws once, and only in the live row', () => {
    const { container } = render(
      <HeaderActions>
        <HeaderControl icon={Pencil} label="Edit" variant="primary" priority="primary" />
      </HeaderActions>,
    )
    expect(container.querySelector('[data-header-trailing]')?.textContent).toBe('Edit')
    // The three probes each hold their own copy in place, for the widths.
    expect([...container.querySelectorAll('[aria-hidden] button')].map((b) => b.textContent)).toEqual(['Edit', 'Edit', ''])
  })

  it('two primaries end the row in the row order, one mounting after the other', () => {
    // Each portaled into the one slot in the order it mounted, so a primary that mounted late was
    // appended: a failed loop with edits ended "… · Resume · Commit" once git status arrived.
    // Each has a target of its own now, laid out in the row's order.
    // Every copy (the live row's and each probe's) shows it at once, as one state would.
    const copies = new Set<(on: boolean) => void>()
    const show = (on: boolean) => copies.forEach((set) => set(on))
    function Late() {
      const [on, setOn] = useState(false)
      useEffect(() => { copies.add(setOn); return () => { copies.delete(setOn) } }, [])
      return on ? <HeaderControl icon={Plus} label="Commit" variant="primary" /> : null
    }
    render(
      <HeaderActions>
        <Late />
        <HeaderControl icon={Pencil} label="Resume" variant="primary" />
      </HeaderActions>,
    )
    expect(row()).toEqual(['Resume'])
    act(() => show(true))
    expect(row(), 'Commit is first in the row, so it is first after the `…` too').toEqual(['Commit', 'Resume'])
  })
})
