// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { LayoutGrid, List, Plus } from 'lucide-react'
import { HeaderActions, HeaderMenu, HeaderSegmented } from './HeaderActions'
import { HeaderFilter } from './HeaderFilter'
import { HeaderSearch } from './HeaderSearch'
import type { FilterSectionDef } from './FilterMenu'

// ── The header members' narrow forms ─────────────────────────────────────────────────────────────
//
// Where the row sheds to icons, the search is a Search button and the filter its glyph. Each still
// says what it holds: the search's name carries the query and a dot shows one is set; the filter's
// name is its whole label and a dot shows a section is set. jsdom lays nothing out, so the cluster
// is given widths its row cannot hold, which sheds it to the overflow tier.

function tight() { Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => 400 }) }
// The stub is the prototype's own property, so deleting it gives `Element`'s getter back.
afterEach(() => { delete (HTMLElement.prototype as { scrollWidth?: number }).scrollWidth })

const status = (value: string): FilterSectionDef[] => [{
  title: 'Status', value, defaultKey: 'open', onChange: () => {},
  options: [{ key: 'open', label: 'Open' }, { key: 'done', label: 'Done' }],
}]

describe('the header search, shed to its button', () => {
  beforeEach(tight)

  it('is named by what it searches, and by the query once one is set, with a dot', () => {
    const { rerender } = render(<HeaderActions><HeaderSearch value="" onChange={() => {}} placeholder="Search tasks" /></HeaderActions>)
    const button = screen.getByRole('button', { name: 'Search tasks' })
    expect(button.querySelector('[data-header-set]')).toBeNull()
    rerender(<HeaderActions><HeaderSearch value=" beans " onChange={() => {}} placeholder="Search tasks" /></HeaderActions>)
    const set = screen.getByRole('button', { name: 'Search tasks: “beans”' })
    expect(set.querySelector('[data-header-set]')).not.toBeNull()
  })

  it('opens the field, holding the query', () => {
    render(<HeaderActions><HeaderSearch value="beans" onChange={() => {}} placeholder="Search tasks" /></HeaderActions>)
    fireEvent.click(screen.getByRole('button', { name: /^Search tasks/ }))
    expect((screen.getByRole('searchbox', { name: 'Search tasks' }) as HTMLInputElement).value).toBe('beans')
  })
})

describe('the header filter, shed to its glyph', () => {
  beforeEach(tight)

  it('is named by its whole label, with a dot only while a section is set', () => {
    const { rerender } = render(<HeaderActions><HeaderFilter sections={status('open')} /></HeaderActions>)
    const at = screen.getByRole('button', { name: 'Show: Open' })
    expect(at.textContent, 'the glyph alone').toBe('')
    expect(at.querySelector('[data-header-set]')).toBeNull()
    rerender(<HeaderActions><HeaderFilter sections={status('done')} /></HeaderActions>)
    expect(screen.getByRole('button', { name: 'Show: Done' }).querySelector('[data-header-set]')).not.toBeNull()
  })
})

describe('HeaderMenu', () => {
  it('takes the primary fill and its own tooltip', () => {
    render(<HeaderActions><HeaderMenu icon={Plus} label="New" variant="primary" title="Make something new in this project"
      items={[{ label: 'New task', onSelect: () => {} }]} /></HeaderActions>)
    const trigger = screen.getByRole('button', { name: 'New' })
    expect(trigger.getAttribute('title')).toBe('Make something new in this project')
    expect(trigger.className).toContain('bg-primary')
  })
})

describe("the cluster's probes", () => {
  it('draw a box for the search, never a second field', () => {
    const { container } = render(<HeaderActions><HeaderSearch value="" onChange={() => {}} placeholder="Search tasks" /></HeaderActions>)
    expect(container.querySelectorAll('input').length, 'one field: the live one').toBe(1)
    expect(within(container).getAllByRole('searchbox')).toHaveLength(1)
  })
})

describe('the view switch, folded to its pill', () => {
  beforeEach(tight)

  it("takes the compact widths' 44 px hit box, as the search and the filter do", () => {
    render(<HeaderActions><HeaderSegmented ariaLabel="View" value="list" onChange={() => {}}
      options={[{ key: 'list', label: 'List', icon: List }, { key: 'board', label: 'Board', icon: LayoutGrid }]} /></HeaderActions>)
    const pill = screen.getByRole('button', { name: 'View: List' })
    for (const c of ['max-lg:min-h-11', 'max-lg:min-w-11!']) expect(pill.className.split(/\s+/)).toContain(c)
  })
})
