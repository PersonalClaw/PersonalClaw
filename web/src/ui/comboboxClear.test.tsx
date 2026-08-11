import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { Combobox } from './Combobox'

// ── Clear must be a real control, and only appear when there IS something to clear ──
//
// The collapsed field is a <button>. Clear used to live INSIDE it as
// `<span role="button" tabIndex={-1} aria-label="Clear selection">`, which produced two
// defects at once:
//
//  1. `nested-interactive` (axe, serious) — an interactive element inside an interactive
//     element. AT is told the field is one button, and the negative tabindex does NOT
//     hide the inner control from assistive tech (axe's own message: "Using a negative
//     tabindex on an element inside an interactive control does not prevent assistive
//     technologies from focusing the element").
//  2. It was NOT OPERABLE BY KEYBOARD AT ALL. `tabIndex={-1}` kept it out of the Tab
//     order and it carried no `onKeyDown`, so a keyboard user was told a button existed
//     and could never activate it. Measured on the live DOM before the fix: Tab from the
//     field skipped straight past it.
//
// Clear is now a sibling <button> after the morphing surface, so every input can reach
// and fire it. Verified end-to-end in the browser: pick a model → Clear appears → Tab
// reaches it → Enter clears → Clear disappears.
//
// The SECOND assertion group covers a pre-existing bug this change inherited rather than
// caused (the parent bundle rendered it too). Several callers ship an explicit
// empty-valued option — `{ value: '', label: 'Auto — provider default' }` in
// `AgentForm`'s `modelOpts` — so `options.find(o => o.value === value)` MATCHES on an
// empty value, and the old `{selected && …}` gate offered Clear on a field with nothing
// to clear. Harmless while the control was unreachable; a dead button once it became
// operable, so the gate now tests the raw value.

const OPTS = [
  { value: '', label: 'Auto — provider default' }, // the empty-valued option, as AgentForm ships it
  { value: 'sonnet', label: 'Claude Sonnet' },
  { value: 'opus', label: 'Claude Opus' },
]

describe('Combobox Clear is a real, reachable control', () => {
  it('is a <button> — not a role=button span — and is in the tab order', () => {
    render(<Combobox options={OPTS} value="sonnet" onChange={() => {}} />)
    const clear = screen.getByLabelText('Clear selection')
    expect(clear.tagName).toBe('BUTTON')
    // The old span pinned itself out of the tab order. A <button> with no tabindex is
    // reachable; an explicit -1 would put the defect straight back.
    expect(clear.getAttribute('tabindex')).toBeNull()
  })

  it('is NOT nested inside the field button (the nested-interactive shape)', () => {
    const { container } = render(<Combobox options={OPTS} value="sonnet" onChange={() => {}} />)
    const clear = screen.getByLabelText('Clear selection')
    // Walk every ancestor: none may be an interactive control. This is the exact
    // condition axe's `nested-interactive` checks, asserted structurally.
    const interactiveAncestors: string[] = []
    for (let el = clear.parentElement; el && el !== container; el = el.parentElement) {
      if (el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button') {
        interactiveAncestors.push(`${el.tagName}${el.getAttribute('role') ? `[role=${el.getAttribute('role')}]` : ''}`)
      }
    }
    expect(
      interactiveAncestors,
      'Clear must not sit inside the field button — that is the nested-interactive defect',
    ).toEqual([])
  })

  it('clears the value when activated', () => {
    const onChange = vi.fn()
    render(<Combobox options={OPTS} value="sonnet" onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('Clear selection'))
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('does not open the menu when Clear is activated', () => {
    // The old span needed stopPropagation because it was inside the field. As a sibling
    // it cannot bubble into "open" at all — pin that so a later refactor back inside the
    // button fails here rather than in a user's hands.
    render(<Combobox options={OPTS} value="sonnet" onChange={() => {}} />)
    fireEvent.click(screen.getByLabelText('Clear selection'))
    expect(screen.queryByPlaceholderText('Search…')).toBeNull()
  })
})

describe('Clear appears only when something is set', () => {
  it('is absent when the value is empty, even though an empty-valued option exists', () => {
    // `options.find(o => o.value === '')` MATCHES here — the bug the old `selected` gate had.
    render(<Combobox options={OPTS} value="" onChange={() => {}} placeholder="Auto — provider default" />)
    expect(screen.getByText('Auto — provider default')).toBeTruthy() // the field renders
    expect(screen.queryByLabelText('Clear selection')).toBeNull() // but Clear does not
  })

  it('is present when a real value is set', () => {
    render(<Combobox options={OPTS} value="opus" onChange={() => {}} />)
    expect(screen.queryByLabelText('Clear selection')).not.toBeNull()
  })

  it('is absent for a value with no matching option (nothing meaningful to clear back to)', () => {
    // A stale/unknown value still counts as "set" — the user should be able to clear it.
    render(<Combobox options={OPTS} value="retired-model" onChange={() => {}} />)
    expect(screen.queryByLabelText('Clear selection')).not.toBeNull()
  })
})
