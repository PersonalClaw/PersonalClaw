import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MenuRow } from './Popover'
import { ContextMenu } from './motion/ContextMenu'

// ── A menu row's check is decoration; its label says the state ──────────────────────────────────
//
// The Projects list's row "…" marks the default project with a check at the end of "Default
// project", and no menu primitive drew one: `selected`'s dot is the context menu's cursor. So
// `MenuRow` takes an opt-in `checkMark`, hidden from assistive tech, while the row's label carries
// the state ("Default project" while it is, "Make default project" while it is not). A check that
// announced itself would say the state twice, and one standing for it alone would say nothing.

describe("a menu row's check", () => {
  it('draws a hidden check after the label, and the row is still named by its label', () => {
    render(<MenuRow label="Default project" checkMark onClick={() => {}} />)
    const row = screen.getByRole('button', { name: 'Default project' })
    const check = row.querySelector('[data-menu-check]')
    expect(check).not.toBeNull()
    expect(check?.getAttribute('aria-hidden')).toBe('true')
    expect(row.textContent).toBe('Default project')
    expect(row.hasAttribute('aria-checked')).toBe(false)
    // After the label, at the row's end.
    const label = screen.getByText('Default project')
    expect(label.compareDocumentPosition(check as Element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('is off by default, so every other menu row draws none', () => {
    render(<MenuRow label="Make default project" onClick={() => {}} />)
    expect(screen.getByRole('button', { name: 'Make default project' }).querySelector('[data-menu-check]')).toBeNull()
  })

  it("is drawn by a context menu from its item's checkMark", () => {
    render(
      <ContextMenu items={[
        { label: 'Default project', checkMark: true, onSelect: () => {} },
        { label: 'Rename', onSelect: () => {} },
      ]}>
        <div>Garden</div>
      </ContextMenu>,
    )
    fireEvent.contextMenu(screen.getByText('Garden'), { clientX: 10, clientY: 10 })
    const marked = screen.getByRole('menuitem', { name: 'Default project' })
    expect(marked.querySelector('[data-menu-check]')?.getAttribute('aria-hidden')).toBe('true')
    expect(screen.getByRole('menuitem', { name: 'Rename' }).querySelector('[data-menu-check]')).toBeNull()
  })
})

describe("on the Projects list, a row's Default project", () => {
  it('reads "Default project" with the check while it is, and "Make default project" with none for another', async () => {
    const { resetDataStore } = await import('../lib/data')
    const { mountList, project } = await import('../test/projectListHarness')
    resetDataStore()
    mountList({ projects: [project('p-garden', 'Garden'), project('p-trip', 'Trip')], defaultProjectId: 'p-garden' })
    const row = async (name: string) => (await screen.findByRole('heading', { level: 2, name: new RegExp(`^${name}`) })).closest('li') as HTMLElement
    const garden = await row('Garden')
    await waitFor(() => expect(garden.textContent).toContain('Default'))
    fireEvent.click(within(garden).getByRole('button', { name: 'More actions for Garden' }))
    const isDefault = await screen.findByRole('button', { name: 'Default project' })
    expect(isDefault.querySelector('[data-menu-check]')?.getAttribute('aria-hidden')).toBe('true')
    fireEvent.keyDown(document, { key: 'Escape' })
    const trip = await row('Trip')
    fireEvent.click(within(trip).getByRole('button', { name: 'More actions for Trip' }))
    const make = await screen.findByRole('button', { name: 'Make default project' })
    expect(make.querySelector('[data-menu-check]')).toBeNull()
    // The same items open from the row's context menu.
    fireEvent.contextMenu(within(garden).getByRole('heading', { level: 2 }), { clientX: 5, clientY: 5 })
    const fromMenu = await screen.findByRole('menuitem', { name: 'Default project' })
    expect(fromMenu.querySelector('[data-menu-check]')).not.toBeNull()
    resetDataStore()
  })
})
