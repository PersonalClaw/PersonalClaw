import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { ListRow } from './ListScaffold'

// ── The row's hit target is a SIBLING of its content, never an ancestor ──────────────
//
// `ListRow` used to put `role="button" tabIndex={0}` on the wrapper. A row that carries its
// own controls then becomes `nested-interactive` (axe, serious): AT is told "one button" and
// finds a checkbox and three tag filters inside it. Measured: 60 nodes — knowledge 26,
// workflows 34. After this change both are 0, with no new violations.
//
// 🔑 WHY AN EMPTY OVERLAY AND NOT `pointer-events` ON THE CHILDREN. The obvious fix keeps the
// wrapper interactive and re-exposes its descendants with
// `[&_button]:pointer-events-auto`-style selectors. That was built in cycle 46 and REVERTED,
// because it has to ENUMERATE every control type and silently misses the conditional ones:
//
//   · workflows' delete button only exists in its `armed` state (`armed === r.id ? … : …`)
//   · workflows' second delete is gated on `d.source !== 'bundled'`
//   · knowledge's tag filters are gated on `tags?.length` AND `hidden md:flex`
//
// A fix that passes on every row you can see and breaks the one you cannot. The overlay owns
// NO descendants, so there is nothing to enumerate and nothing to miss — verified live,
// including arming the workflows delete.
//
// The click still fires through the WRAPPER's onClick: every nested control already calls
// stopPropagation for itself (`ui/forms.tsx`'s Checkbox does it on both onClick and onChange;
// the tag/run/delete buttons do it inline), so bubbling was already the contract.

describe('ListRow hit target', () => {
  it('is a real <button>, not a role=button div', () => {
    const { container } = render(
      <ListRow onClick={() => {}} label="Deploy pipeline"><span>body</span></ListRow>,
    )
    const btn = screen.getByRole('button', { name: 'Deploy pipeline' })
    expect(btn.tagName).toBe('BUTTON')
    // And the wrapper must no longer claim the role.
    expect(container.firstElementChild?.getAttribute('role')).toBeNull()
  })

  it('does not CONTAIN the row content (the nested-interactive shape)', () => {
    render(
      <ListRow onClick={() => {}} label="Row name">
        <button type="button">nested action</button>
      </ListRow>,
    )
    const hit = screen.getByRole('button', { name: 'Row name' })
    const nested = screen.getByRole('button', { name: 'nested action' })
    expect(hit.contains(nested), 'the hit target must not be an ancestor of the row content').toBe(false)
    // Symmetrically, the row's controls must not be inside ANY interactive ancestor.
    for (let el = nested.parentElement; el; el = el.parentElement) {
      expect(
        el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button',
        `a nested control must have no interactive ancestor (found <${el.tagName}>)`,
      ).toBe(false)
    }
  })

  it('owns exactly ONE tab stop per row', () => {
    // `whileTap` makes Framer Motion set tabindex="0" on the wrapper itself, so simply
    // dropping the attribute left TWO tab stops per row (measured live: Tab landed on a bare
    // div, then on the overlay). The wrapper is pinned to -1 to keep the single stop.
    const { container } = render(<ListRow onClick={() => {}} label="Row"><span>body</span></ListRow>)
    const wrapper = container.firstElementChild!
    expect(wrapper.getAttribute('tabindex')).toBe('-1')
    expect(container.querySelectorAll('[tabindex="0"]').length).toBe(0)
    // The <button> is focusable natively without an explicit tabindex.
    expect(screen.getByRole('button', { name: 'Row' }).getAttribute('tabindex')).toBeNull()
  })

  it('fires onClick from the row body (bubbling), so the whole row stays clickable', () => {
    const onClick = vi.fn()
    render(<ListRow onClick={onClick} label="Row"><span>the body text</span></ListRow>)
    fireEvent.click(screen.getByText('the body text'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('lets a nested control stop the row from firing', () => {
    const onRow = vi.fn()
    const onNested = vi.fn()
    render(
      <ListRow onClick={onRow} label="Row">
        <button type="button" onClick={(e) => { e.stopPropagation(); onNested() }}>action</button>
      </ListRow>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'action' }))
    expect(onNested).toHaveBeenCalledTimes(1)
    expect(onRow, 'a control that stops propagation must not also trigger the row').not.toHaveBeenCalled()
  })

  it('adds no hit target to a NON-interactive row', () => {
    const { container } = render(<ListRow label="unused"><span>static</span></ListRow>)
    expect(container.querySelector('button')).toBeNull()
    expect(container.firstElementChild?.getAttribute('tabindex')).toBeNull()
  })
})
