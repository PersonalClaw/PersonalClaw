import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { HeaderFilter, headerFilterLabel, headerFilterSet } from './HeaderFilter'
import { HeaderSearch } from './HeaderSearch'
import type { FilterSectionDef } from './FilterMenu'

// ── The header's search and its filter ───────────────────────────────────────────────────────
//
// The two members a page's header needed so no page draws a controls bar under it. The search is
// the page's search field, in the header. The filter holds every choice set of the page that is not
// its main view switch, and its label says what each is set to ("Show: Chats · Active"), so a
// filtered page never looks unfiltered.

const section = (title: string, value: string, defaultKey: string, options: Array<[string, string]>): FilterSectionDef => ({
  title, value, defaultKey, onChange: vi.fn(),
  options: options.map(([key, label]) => ({ key, label })),
})

const SCOPE = (value = 'manual') => section('Scope', value, 'manual', [['manual', 'Chats'], ['loop', 'Loops'], ['all', 'All']])
const LIFE = (value = 'active') => section('Status', value, 'active', [['active', 'Active'], ['archived', 'Archived']])

describe("the header filter's label", () => {
  it('names every section’s choice, in order', () => {
    expect(headerFilterLabel([SCOPE(), LIFE()])).toBe('Show: Chats · Active')
    expect(headerFilterLabel([SCOPE('loop'), LIFE('archived')])).toBe('Show: Loops · Archived')
  })

  it('takes a prefix, and nothing else of the page’s own', () => {
    expect(headerFilterLabel([section('Order', 'new', 'new', [['new', 'Newest'], ['old', 'Oldest']])], 'Sort')).toBe('Sort: Newest')
    expect(headerFilterLabel([section('Order', 'old', 'new', [['new', 'Newest'], ['old', 'Oldest']])], '')).toBe('Oldest')
  })

  it('says a choice in the words its section gives for it', () => {
    const order = { ...section('Order', 'name', 'name', [['name', 'Name (A–Z)'], ['updated', 'Recently updated']]), said: (k: string) => k === 'name' ? 'Sorted by name' : 'Recently updated first' }
    expect(headerFilterLabel([order], '')).toBe('Sorted by name')
  })

  it('leaves a quiet section out only while it is at its default', () => {
    const kind = (value: string) => ({ ...section('Type', value, 'all', [['all', 'All'], ['provider', 'Provider apps']]), quiet: true })
    expect(headerFilterLabel([LIFE(), kind('all')])).toBe('Show: Active')
    expect(headerFilterLabel([LIFE(), kind('provider')])).toBe('Show: Active · Provider apps')
    expect(headerFilterLabel([kind('all')]), 'nothing set, nothing named').toBe('Filter')
  })

  it('counts the sections set away from their default', () => {
    expect(headerFilterSet([SCOPE(), LIFE()])).toBe(0)
    expect(headerFilterSet([SCOPE('all'), LIFE('archived')])).toBe(2)
  })
})

describe('the header filter', () => {
  it('says what is set in its name, and marks a page set away from its defaults', () => {
    const { rerender } = render(<HeaderFilter sections={[SCOPE(), LIFE()]} />)
    const at = screen.getByRole('button', { name: 'Show: Chats · Active' })
    expect(at.getAttribute('data-header-filter')).toBe('default')

    rerender(<HeaderFilter sections={[SCOPE(), LIFE('archived')]} />)
    expect(screen.getByRole('button', { name: 'Show: Chats · Archived' }).getAttribute('data-header-filter')).toBe('set')
  })

  it('opens every section, and a choice reaches its section', () => {
    const life = LIFE()
    render(<HeaderFilter sections={[SCOPE(), life]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show: Chats · Active' }))
    fireEvent.click(screen.getByText('Archived'))
    expect(life.onChange).toHaveBeenCalledWith('archived')
  })
})

describe('the header search', () => {
  it('is the page’s search field, named, in the header', () => {
    const onChange = vi.fn()
    render(<HeaderSearch value="" onChange={onChange} placeholder="Search chats" />)
    const box = screen.getByRole('searchbox', { name: 'Search chats' })
    fireEvent.change(box, { target: { value: 'kitchen' } })
    expect(onChange).toHaveBeenCalledWith('kitchen')
  })
})
