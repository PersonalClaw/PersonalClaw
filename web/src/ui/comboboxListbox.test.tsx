import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Combobox } from './Combobox'

// ── An autocomplete whose arrow keys moved nothing but pixels ─────────────────────────────────────
//
// `Combobox`'s doc says "type to filter; arrow keys + Enter to pick", and that was true for a sighted
// mouse-or-keyboard user. Measured on the live DOM at `#/triggers/new` (19 action providers) before
// this change:
//
//   the search input      role=null · aria-expanded=null · aria-controls=null · aria-activedescendant=null
//   the option list       **0 [role=listbox]** · **0 [role=option]** · 0 aria-selected
//   pressing ArrowDown    the visual highlight moved; the accessibility tree did not change AT ALL
//   pressing Tab          focus landed on a row INSIDE the open popup (19 stops before "Cancel")
//   arrowing to index 12  the active row was OUT OF VIEW and `scrollTop` stayed 0
//
// So: the keyboard model already worked, and nothing said so (WCAG 4.1.2), while the cursor itself
// became invisible past the first screenful (2.4.7) and Tab walked into the popup instead of leaving it.
// This change DECLARES the pattern that already worked and makes the cursor observable — it adds no
// new keyboard contract.
//
// 🔑 WHY THE FAMILY'S OWN RAIL COULD NOT SEE IT. `popupItemRoles.test.tsx` audits every popup container
// in the tree — by finding `role="menu|listbox"` in the source and checking the items match. Combobox
// declared NO role, so it was never a container to audit. **A rail that checks declarations cannot see
// an omission.** That file now also sweeps for the SHAPE (an ArrowDown cursor over an options list must
// declare a container role), which is the check that would have found this one.
//
// 🪤 THE FIX EXPOSED A SECOND BUG, and the measurement is the only reason I caught it. Once the list
// scrolls to follow the cursor, a stationary pointer resting over it has new rows move underneath, and
// the browser fires `mouseenter` for each — so hover kept yanking the keyboard cursor back. Measured:
// 12 ArrowDowns advanced to index 3 with the pointer over the list, and to 12 with it parked off.
// `onMouseMove` needs real movement, so hover still highlights and scrolling no longer counts as it.

const OPTS = [
  { value: 'a', label: 'Alpha' },
  { value: 'b', label: 'Bravo' },
  { value: 'c', label: 'Charlie' },
]

/** Open the collapsed field — it is a button showing the placeholder or the selected label. */
function open(name: RegExp) {
  fireEvent.click(screen.getByRole('button', { name }))
}

describe('the open Combobox declares the pattern it already implemented', () => {
  it('the search field is a combobox wired to the list', () => {
    render(<Combobox options={OPTS} value="b" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Bravo/)
    const input = screen.getByRole('combobox')
    expect(input.getAttribute('aria-expanded')).toBe('true')
    expect(input.getAttribute('aria-autocomplete')).toBe('list')
    const listId = input.getAttribute('aria-controls')
    expect(listId, 'aria-controls must point at the listbox').toBeTruthy()
    expect(screen.getByRole('listbox').id).toBe(listId)
  })

  it('every row is an option, and the CHOSEN one is the selected one', () => {
    render(<Combobox options={OPTS} value="b" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Bravo/)
    const opts = screen.getAllByRole('option')
    expect(opts.map((o) => o.textContent)).toEqual(['Alpha', 'Bravo', 'Charlie'])
    // 🔑 `aria-selected` is the committed value, NOT the keyboard cursor — those are two different
    // states and this control has both. The cursor is published separately, below.
    expect(opts.map((o) => o.getAttribute('aria-selected'))).toEqual(['false', 'true', 'false'])
  })

  it('publishes the keyboard cursor, and moves it on ArrowDown', () => {
    render(<Combobox options={OPTS} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    const input = screen.getByRole('combobox')
    const opts = screen.getAllByRole('option')
    expect(input.getAttribute('aria-activedescendant'), 'the cursor starts on the first row').toBe(opts[0].id)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input.getAttribute('aria-activedescendant'), 'and follows the arrow key').toBe(opts[1].id)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input.getAttribute('aria-activedescendant')).toBe(opts[0].id)
  })

  it('neither the rows nor the list are in the tab order', () => {
    // The input owns focus and publishes the cursor; a row that is also tabbable gives the popup two
    // navigation models that disagree. Measured before: Tab from the field landed on a row.
    render(<Combobox options={OPTS} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    for (const o of screen.getAllByRole('option')) expect(o.getAttribute('tabindex')).toBe('-1')
    expect(screen.getByRole('listbox').getAttribute('tabindex')).toBe('-1')
  })

  it('an empty result announces itself instead of being an empty listbox', () => {
    render(<Combobox options={OPTS} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="No matches" />)
    open(/Pick one/)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'zzzz' } })
    expect(screen.queryByRole('listbox'), 'no listbox when it would hold no options').toBeNull()
    expect(screen.getByRole('status').textContent).toBe('No matches')
  })

  it('the collapsed field advertises the popup it opens', () => {
    render(<Combobox options={OPTS} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    const trigger = screen.getByRole('button', { name: /Pick one/ })
    expect(trigger.getAttribute('aria-haspopup')).toBe('listbox')
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
  })
})

describe('the cursor, the announcement and Enter all index the RENDERED order', () => {
  // Grouping buckets the filtered list, so interleaved groups render in a different order than they
  // arrive. The highlight indexed the rendered order and Enter indexed the arrival order, so the two
  // could commit different rows — latent while every caller ships contiguous groups.
  const INTERLEAVED = [
    { value: 'a', label: 'Alpha', group: 'One' },
    { value: 'b', label: 'Bravo', group: 'Two' },
    { value: 'c', label: 'Charlie', group: 'One' },
  ]

  it('renders grouped order, not arrival order', () => {
    render(<Combobox options={INTERLEAVED} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Alpha', 'Charlie', 'Bravo'])
  })

  it('Enter commits the row the cursor is on', () => {
    const onChange = vi.fn()
    render(<Combobox options={INTERLEAVED} value="" onChange={onChange} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    const input = screen.getByRole('combobox')
    fireEvent.keyDown(input, { key: 'ArrowDown' })   // second RENDERED row = Charlie
    expect(input.getAttribute('aria-activedescendant')).toBe(screen.getAllByRole('option')[1].id)
    fireEvent.keyDown(input, { key: 'Enter' })
    // Arrival order would have committed 'b' (Bravo) here — the row the user was not looking at.
    expect(onChange).toHaveBeenCalledWith('c')
  })

  it('the group headings are groups, not options', () => {
    render(<Combobox options={INTERLEAVED} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    expect(screen.getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual(['One', 'Two'])
    expect(screen.getAllByRole('option').length, 'a heading must not count as a row').toBe(3)
  })
})

describe('the two behaviours jsdom cannot execute are pinned at the source', () => {
  const src = readFileSync(join(process.cwd(), 'src/ui/Combobox.tsx'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('the active row is scrolled into view when the cursor moves', () => {
    // jsdom has no layout, so this cannot be asserted by rendering. Measured in Chromium instead:
    // before, the active row was out of view from index 12 of 19 and scrollTop stayed 0; after, it is
    // in view at every index and the list scrolls.
    expect(src).toMatch(/scrollIntoView\?\.\(\{ block: 'nearest' \}\)/)
    expect(src, 'and it must run when the cursor or open-state changes').toMatch(/\}, \[active, open, optId\]\)/)
  })

  it('hover is bound to movement, not to entering', () => {
    expect(src).toMatch(/onMouseMove=\{\(\) => setActive\(idx\)\}/)
    expect(src, 'mouseenter fires when scrolled content moves under a still pointer')
      .not.toMatch(/onMouseEnter=\{\(\) => setActive\(idx\)\}/)
  })
})
