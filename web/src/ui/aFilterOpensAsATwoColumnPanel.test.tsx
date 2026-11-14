// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { HeaderActions } from './HeaderActions'
import { HeaderFilter } from './HeaderFilter'
import type { FilterSectionDef } from './FilterMenu'

// ── A header filter's panel form: two columns, check boxes and radios, no Done ────────────────────
//
// The header filter opens one 264 px column: upper-case captions over each section, a tint and a
// trailing check on a chosen row, and Done. The library's Filter has seven sections, which in one
// column ran past the window. Its panel form (`panel`) opens 560 px wide: each section in the column
// it names (`column`), in order, and those that name neither under both, two to a row; a multi
// section's choices draw a check box and a single section's a radio, in place of the tint and the
// check, each still saying whether it is chosen (`aria-pressed`); sections are named as written; and
// it has no Done, since Escape, Tab past the last row, a click outside and the trigger close it. A
// filter passed no `panel`, as on every other page, is as before.

const sec = (title: string, over: Partial<FilterSectionDef> = {}): FilterSectionDef => ({
  title, value: '', defaultKey: '', onChange: vi.fn(),
  options: [{ key: '', label: 'Any' }, { key: 'a', label: `${title} A`, count: 3 }, { key: 'b', label: `${title} B`, count: 1 }],
  ...over,
})
const SECTIONS = [
  sec('Kind', { multi: true, value: 'a', column: 1 }),
  sec('Made by', { multi: true, column: 1 }),
  sec('Project', { value: 'b', column: 2 }),
  sec('State', { multi: true, column: 2 }),
  sec('Sort by', { value: '', column: 2, quiet: true }),
  sec('Collection'),
  sec('Tag', { multi: true }),
]

function open(panel: boolean) {
  render(<HeaderActions><HeaderFilter sections={SECTIONS} prefix="Filter" panel={panel} /></HeaderActions>)
  fireEvent.click(screen.getByRole('button', { name: /Filter/ }))
  const kind = screen.getByRole('group', { name: 'Kind' })
  let box: HTMLElement | null = kind
  while (box && !box.style.width) box = box.parentElement
  return { box: box!, root: kind.closest('[data-filter-panel]') ?? kind.parentElement! }
}
const titles = (el: Element | null) => [...(el?.querySelectorAll('[data-section]') ?? [])].map((s) => s.getAttribute('data-section'))

describe("a header filter's panel form", () => {
  it('🔑 opens 560 px wide, each section in the column it names and the rest under both', () => {
    const { box, root } = open(true)
    expect(box.style.width).toBe('560px')
    expect(titles(root.querySelector('[data-filter-column="1"]'))).toEqual(['Kind', 'Made by'])
    expect(titles(root.querySelector('[data-filter-column="2"]'))).toEqual(['Project', 'State', 'Sort by'])
    expect(titles(root.querySelector('[data-filter-under]'))).toEqual(['Collection', 'Tag'])
  })

  it('🔑 a multi section draws check boxes and a single one radios, each still aria-pressed', () => {
    open(true)
    const kind = screen.getByRole('group', { name: 'Kind' })
    const chosen = within(kind).getByRole('button', { name: /Kind A/ })
    expect(chosen.getAttribute('aria-pressed')).toBe('true')
    expect(chosen.querySelector('[data-choice-mark="box"]')?.getAttribute('aria-hidden')).toBe('true')
    expect(chosen.querySelector('[data-choice-mark]')?.hasAttribute('data-on')).toBe(true)
    expect(within(kind).getByRole('button', { name: /Kind B/ }).getAttribute('aria-pressed')).toBe('false')
    const project = screen.getByRole('group', { name: 'Project' })
    expect(within(project).getByRole('button', { name: /Project B/ }).querySelector('[data-choice-mark="radio"]')).not.toBeNull()
    // No tint and no trailing check: the mark says it.
    expect(chosen.querySelectorAll('svg.text-primary')).toHaveLength(0)
  })

  it('🔑 names its sections as written, and has no Done', () => {
    open(true)
    const caption = screen.getByText('Made by')
    expect(caption.className).not.toContain('uppercase')
    expect(screen.queryByRole('button', { name: 'Done' })).toBeNull()
  })

  it('🔑 a filter with no panel is today\'s column: 264 px, captions, tint, check and Done', () => {
    const { box } = open(false)
    expect(box.style.width).toBe('264px')
    expect(screen.getByText('Made by').className).toContain('uppercase')
    expect(screen.getByRole('button', { name: 'Done' })).toBeTruthy()
    const chosen = within(screen.getByRole('group', { name: 'Kind' })).getByRole('button', { name: /Kind A/ })
    expect(chosen.querySelector('[data-choice-mark]')).toBeNull()
    expect(chosen.querySelectorAll('svg.text-primary').length).toBeGreaterThan(0)
  })
})
