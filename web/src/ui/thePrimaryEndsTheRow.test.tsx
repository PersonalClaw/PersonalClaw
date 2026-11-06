// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
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
    // Resume's target stays where it is while Commit's arrives: a target re-appended to reorder
    // the row took Resume's button out of the document and back, and its focus with it.
    const slot = document.querySelector('[data-header-trailing]')!
    const resume = slot.querySelector('span')!
    const removed: Node[] = []
    const watch = new MutationObserver((records) => records.forEach((r) => removed.push(...r.removedNodes)))
    watch.observe(slot, { childList: true })
    const button = within(resume as HTMLElement).getByRole('button', { name: 'Resume' })
    button.focus()
    act(() => show(true))
    watch.takeRecords().forEach((r) => removed.push(...r.removedNodes))
    watch.disconnect()
    expect(row(), 'Commit is first in the row, so it is first after the `…` too').toEqual(['Commit', 'Resume'])
    // In the slot itself, not only in the row: a target waits for a placed member, so a Commit left
    // in the rail would read the same in the row's order.
    expect(within(slot as HTMLElement).getAllByRole('button').map((b) => b.textContent?.trim()), 'both after the `…`')
      .toEqual(['Commit', 'Resume'])
    expect(removed, "no target left the slot, Resume's least of all").toEqual([])
    expect(document.activeElement, 'Resume kept its focus').toBe(button)
  })

  it('leaves no target for a primary a tight row sheds into the `…`', () => {
    // jsdom lays nothing out, so the cluster is given widths its row cannot hold, which sheds it to
    // the overflow tier (as `headerMembersNarrowForms.test.tsx` does). A `variant="primary"` control
    // of default priority is not held, so it goes into the `…`, and its target went with it, empty.
    Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => 400 })
    try {
      render(
        <HeaderActions>
          <HeaderControl icon={Pin} label="Pin" />
          <HeaderControl icon={Sparkles} label="Publish" variant="primary" />
        </HeaderActions>,
      )
      expect(screen.getByRole('button', { name: 'More actions' }), 'the row sheds').toBeTruthy()
      const slot = document.querySelector('[data-header-trailing]')!
      expect(slot.querySelectorAll('span').length).toBe(0)
      expect(slot.className).toContain('hidden')
    } finally {
      delete (HTMLElement.prototype as { scrollWidth?: number }).scrollWidth
    }
  })

  it('leaves no target for a primary that lives in the `…`', () => {
    // A primary the row does not draw (one always in the `…`, or one a tight row sheds there) left
    // an empty target after the `…`, and the slot's gap with it.
    render(
      <HeaderActions>
        <HeaderControl icon={Pin} label="Pin" />
        <HeaderControl icon={Sparkles} label="Publish" variant="primary" inOverflow />
      </HeaderActions>,
    )
    const slot = document.querySelector('[data-header-trailing]')!
    expect(slot.querySelectorAll('span').length).toBe(0)
    expect(slot.className).toContain('hidden')
  })
})
