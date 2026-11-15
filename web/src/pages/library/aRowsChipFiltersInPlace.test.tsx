/** A row's project chip filters the list in place: pressing it pushes
 *  `project=`; at 768 px and below the chip is text, and the row opens its item. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { atWidth, row } from '../../test/libraryHarness'
import { LibraryRow } from '../../ui/LibraryRow'
import { ProjectChip } from '../../ui/ProjectChip'

afterEach(() => { cleanup(); atWidth(1280) })

const item = row('knowledge:k1', 'Router manual', { project: { id: 'p-home', name: 'Homelab' } })

describe('a row\'s chip filters in place', () => {
  it('🔑 pressing the chip filters by its project, and does not open the row', () => {
    atWidth(1280)
    const onFilter = vi.fn(); const onOpen = vi.fn()
    render(<LibraryRow item={item} onOpen={onOpen} onFilterProject={onFilter} />)
    fireEvent.click(screen.getByRole('button', { name: 'Filter by project Homelab' }))
    expect(onFilter).toHaveBeenCalledWith('p-home')
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('🔑 on a phone the chip is text, and pressing the row opens its item', () => {
    atWidth(390)
    const onFilter = vi.fn(); const onOpen = vi.fn()
    render(<LibraryRow item={item} onOpen={onOpen} onFilterProject={onFilter} />)
    expect(screen.queryByRole('button', { name: 'Filter by project Homelab' })).toBeNull()
    expect(screen.getByText('Homelab')).toBeTruthy()
    fireEvent.click(screen.getAllByRole('button').find((b) => (b.getAttribute('aria-label') ?? '').startsWith('Router manual'))!)
    expect(onOpen).toHaveBeenCalled()
    expect(onFilter).not.toHaveBeenCalled()
  })

  it('a chip inside a control is text on a phone wherever it is drawn, never a control in a control', () => {
    atWidth(390)
    const onFilter = vi.fn()
    render(<ProjectChip id="p-home" name="Homelab" inControl onFilter={onFilter} />)
    expect(screen.getByText('Homelab')).toBeTruthy()
    expect(screen.queryByRole('button'), 'no button').toBeNull()
    expect(screen.queryByRole('link'), 'nor a link').toBeNull()
  })
})
