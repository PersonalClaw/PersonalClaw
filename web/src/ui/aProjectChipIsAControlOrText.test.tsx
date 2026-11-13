import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ProjectChip } from './ProjectChip'

// ── A project chip opens the project, filters by it, or is text ─────────────────────────────────
//
// The open form is a link to the project's page, "Open project Garden"; the filter form a button,
// "Filter by project Garden". Each stops its click, since it sits in a row or a card whose own click
// opens the work. From 320 to 768 px a chip inside a control is plain text, so a phone gains no
// target under 44 px; a chip that is its surface's only way to the project keeps a 44 px box. It
// draws nothing for the project the view is in, nor for an id it cannot name.

function atWidth(phone: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: phone && query === '(max-width: 768px)', media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

afterEach(() => { vi.unstubAllGlobals() })

describe('a project chip', () => {
  it('opens the project, named for it, and keeps its click from the card it sits in', () => {
    atWidth(false)
    const onCard = vi.fn()
    render(<div onClick={onCard}><ProjectChip id="p-garden" name="Garden" inControl /></div>)
    const link = screen.getByRole('link', { name: 'Open project Garden' })
    expect(link.getAttribute('href')).toBe('#/projects/p-garden')
    expect(link.getAttribute('title')).toBe('Garden')
    expect(link.textContent).toBe('GGarden')
    fireEvent.click(link)
    expect(onCard).not.toHaveBeenCalled()
  })

  it('filters by the project in its filter form', () => {
    atWidth(false)
    const onFilter = vi.fn()
    const onRow = vi.fn()
    render(<div onClick={onRow}><ProjectChip id="p-garden" name="Garden" onFilter={onFilter} /></div>)
    fireEvent.click(screen.getByRole('button', { name: 'Filter by project Garden' }))
    expect(onFilter).toHaveBeenCalledWith('p-garden')
    expect(onRow).not.toHaveBeenCalled()
  })

  it('is plain text on a phone inside a control, and a 44 px control where it is the only way', () => {
    atWidth(true)
    const { container, rerender } = render(<ProjectChip id="p-garden" name="Garden" inControl />)
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(container.textContent).toBe('GGarden')
    rerender(<ProjectChip id="p-garden" name="Garden" />)
    const link = screen.getByRole('link', { name: 'Open project Garden' })
    expect(link.className).toContain('max-lg:min-h-11')
  })

  it('on a desktop a chip inside a control is still the link, at the pill size', () => {
    atWidth(false)
    render(<ProjectChip id="p-garden" name="Garden" inControl />)
    const link = screen.getByRole('link', { name: 'Open project Garden' })
    expect(link.className).toContain('h-6')
    expect(link.className).not.toContain('min-h-11')
  })

  it('draws nothing for the project the view is in, for work in none, or for an id with no name', () => {
    atWidth(false)
    const { container, rerender } = render(<ProjectChip id="p-garden" name="Garden" current="p-garden" />)
    expect(container.innerHTML).toBe('')
    rerender(<ProjectChip id="" name="" />)
    expect(container.innerHTML).toBe('')
    rerender(<ProjectChip id="p-gone" name="" />)
    expect(container.innerHTML).toBe('')
  })
})
