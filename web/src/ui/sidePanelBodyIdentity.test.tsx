// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { SidePanel } from './SidePanel'

// ── Expanding the panel must not REMOUNT its body (issue 2515) ────────────────────────────────────
//
// 🔴 EXPAND THREW AWAY WHAT THE USER HAD TYPED. The two modes used to be two return statements —
// a portaled full-screen overlay and an in-flow dock — each rendering `{children}` at its own
// position in the element tree. At that position React sees a different fiber type (`HostPortal` vs
// a host element), so it does not reconcile: it unmounts the body and mounts a fresh one. Every
// piece of state inside goes with it. On the tool inspector that is the filled-in argument form and
// the result you just ran for, which is exactly the moment a person reaches for Expand.
//
// 🔑 THE ASSERTION IS MOUNT IDENTITY, NOT VALUE EQUALITY. A body that unmounts and remounts can
// still SHOW the right values — if the parent happens to hold them, or if two mounted copies are
// kept in sync. Those are the designs this fix rejects (46 consumers hold arbitrary state; there is
// nothing here to lift). So this counts mounts and compares the DOM node itself: one mount, one
// node, re-parented. React state and live DOM state (the uncontrolled input) then survive as a
// consequence rather than as a thing to maintain.

/** A body that is expensive to lose: it counts its own mounts, holds React state, and carries an
 *  uncontrolled input whose value lives only in the DOM node. */
function Body({ onMount }: { onMount: () => void }) {
  const [ticks, setTicks] = useState(0)
  useEffect(() => { onMount() }, [onMount])
  return (
    <div data-testid="body">
      <input aria-label="Draft" defaultValue="" />
      <button onClick={() => setTicks((t) => t + 1)}>bump</button>
      <span data-testid="ticks">{ticks}</span>
    </div>
  )
}

function mountPanel() {
  let mounts = 0
  render(
    <SidePanel title="Inspector" storeKey="test-identity-w" onClose={() => {}}>
      <Body onMount={() => { mounts += 1 }} />
    </SidePanel>,
  )
  return { count: () => mounts }
}

describe('SidePanel keeps ONE mounted body across expand and collapse', () => {
  it('expanding mounts the body exactly once and keeps the same DOM node', () => {
    const { count } = mountPanel()
    expect(count(), 'one mount to begin with').toBe(1)
    const before = screen.getByTestId('body')

    fireEvent.click(screen.getByRole('button', { name: 'Expand to full width' }))

    expect(count(), 'expand must RE-PARENT the body, not remount it').toBe(1)
    expect(screen.getByTestId('body'), 'the same DOM node, moved').toBe(before)
  })

  it('a filled input and React state survive expand → collapse', () => {
    const { count } = mountPanel()
    fireEvent.change(screen.getByLabelText('Draft'), { target: { value: 'half-written argument' } })
    fireEvent.click(screen.getByRole('button', { name: 'bump' }))
    expect(screen.getByTestId('ticks').textContent).toBe('1')

    fireEvent.click(screen.getByRole('button', { name: 'Expand to full width' }))
    expect(screen.getByLabelText('Draft')).toHaveValue('half-written argument')
    expect(screen.getByTestId('ticks').textContent).toBe('1')

    fireEvent.click(screen.getByRole('button', { name: 'Collapse to panel' }))
    expect(screen.getByLabelText('Draft'), 'and back again — one body, two chromes')
      .toHaveValue('half-written argument')
    expect(screen.getByTestId('ticks').textContent).toBe('1')
    expect(count(), 'still one mount after a full round trip').toBe(1)
  })

  it('the body lands INSIDE the chrome that is on screen, in both modes', () => {
    // Re-parenting is only correct if the node ends up in the visible chrome — a body left behind in
    // the detached dock would pass a mount-count check while showing nothing at all.
    mountPanel()
    const docked = screen.getByRole('region', { name: 'Inspector' })
    expect(docked.contains(screen.getByTestId('body')), 'docked: body is in the dock').toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Expand to full width' }))
    const expanded = screen.getByRole('region', { name: 'Inspector' })
    expect(expanded.contains(screen.getByTestId('body')), 'expanded: body is in the overlay').toBe(true)
  })
})
