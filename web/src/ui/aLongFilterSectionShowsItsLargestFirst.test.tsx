import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { FilterSections, type FilterSectionDef } from './FilterMenu'

// ── A long filter section shows its largest choices first, and the rest on request ────────────────
//
// 🔴 Red before: a section drew every choice, so a home whose chats carry dozens of tags (Generate
// Tags can tag every chat at once) left the chat list's 264 px menu one long Tag section. Now a
// section with a `limit` shows that many choices, the chosen ones always among them and the rest the
// largest, each in the section's order, then a "Show all N tags" row that opens the rest in place,
// with the menu still open and the focus on the section's first choice. A choice shown stays shown
// until the menu closes, so unticking it neither moves it nor drops the focus. A section with no
// limit is drawn as before.

const tags = (n: number) => Array.from({ length: n }, (_, i) => ({ key: `t${i}`, label: `Tag ${i}`, count: i + 1 }))
function section(over: Partial<FilterSectionDef> = {}): FilterSectionDef {
  return { title: 'Tag', value: '', defaultKey: '', multi: true, options: tags(6), onChange: vi.fn(), limit: 3, limitNoun: 'tags', ...over }
}
/** The section's rows, its Clear aside. */
const shown = () => within(screen.getByRole('group', { name: 'Tag' })).getAllByRole('button').map((b) => b.textContent).filter((t) => t !== 'Clear')

afterEach(cleanup)

describe('a section with a limit', () => {
  it('shows the largest choices in the section\'s order, then Show all', () => {
    render(<FilterSections sections={[section()]} close={vi.fn()} />)
    expect(shown()).toEqual(['Tag 34', 'Tag 45', 'Tag 56', 'Show all 6 tags'])
  })

  it('always keeps a chosen choice among those shown', () => {
    render(<FilterSections sections={[section({ value: 't0' })]} close={vi.fn()} />)
    expect(shown()[0]).toMatch(/^Tag 0/)
    expect(shown()).toHaveLength(4)
  })

  it('opens the rest in place from Show all, without closing the menu', () => {
    const close = vi.fn()
    render(<FilterSections sections={[section()]} close={close} />)
    fireEvent.click(screen.getByRole('button', { name: /^Show all 6 tags/ }))
    expect(shown()).toHaveLength(6)
    expect(close).not.toHaveBeenCalled()
  })
})

describe('the focus in a section with a limit', () => {
  it('goes to the section\'s first choice when Show all opens the rest', () => {
    render(<FilterSections sections={[section()]} close={vi.fn()} />)
    const all = screen.getByRole('button', { name: /^Show all 6 tags/ })
    all.focus()
    fireEvent.click(all)
    expect(document.activeElement?.textContent).toMatch(/^Tag 0/)
  })

  it('stays on a choice unticked there, which stays where it was until the menu closes', () => {
    function Stateful() {
      const [value, setValue] = useState('t0')
      return <FilterSections sections={[section({ value, onChange: (v) => setValue(v) })]} close={vi.fn()} />
    }
    render(<Stateful />)
    const tag0 = screen.getByRole('button', { name: /^Tag 0/ })
    tag0.focus()
    fireEvent.click(tag0)
    expect(tag0.getAttribute('aria-pressed')).toBe('false')
    expect(shown()[0]).toMatch(/^Tag 0/)
    expect(document.activeElement).toBe(tag0)
  })
})

describe('a section with no limit', () => {
  it('draws every choice, as before', () => {
    render(<FilterSections sections={[section({ limit: undefined })]} close={vi.fn()} />)
    expect(shown()).toHaveLength(6)
    expect(screen.queryByRole('button', { name: /^Show all/ })).toBeNull()
  })
})
