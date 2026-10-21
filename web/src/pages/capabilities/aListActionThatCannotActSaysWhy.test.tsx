/** A Capabilities list's More menu says why a row cannot act (#3817).
 *
 *  The list's More menu holds what a tab's header no longer does: Sync agents, Export to Claude
 *  Code, Re-probe MCP servers, Manage Sources. While a sync or a re-probe runs, its row cannot run
 *  it again. That row stays in the menu, dimmed and announced as unavailable, and says why under its
 *  name, so a person who opens the menu mid-sync learns the action exists and when it comes back.
 *  Choosing it does nothing and leaves the menu open. A row that can act names itself alone.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { RefreshCw, Upload } from 'lucide-react'
import { ListMoreMenu } from './ListMoreMenu'

afterEach(cleanup)

const WHY = 'A sync is running; it can run again once it ends.'

function openMenu(sync: () => void, exportAgents: () => void, running: boolean) {
  render(<ListMoreMenu actions={[
    { label: running ? 'Syncing…' : 'Sync agents', icon: RefreshCw, onSelect: sync, disabledReason: running ? WHY : undefined },
    { label: 'Export to Claude Code', icon: Upload, onSelect: exportAgents },
  ]} />)
  fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
}

describe('a More menu row that cannot act says why', () => {
  it('stays in the menu, announced as unavailable, with its reason under its name', () => {
    openMenu(vi.fn(), vi.fn(), true)
    const row = screen.getByRole('button', { name: 'Syncing…' })
    expect(row.getAttribute('aria-disabled')).toBe('true')
    expect(row.textContent).toContain(WHY)
    expect(row.getAttribute('title'), 'the whole reason, where the row can show only its start').toBe(WHY)
  })

  it('does nothing when chosen, and the menu stays open', () => {
    const sync = vi.fn()
    openMenu(sync, vi.fn(), true)
    fireEvent.click(screen.getByRole('button', { name: 'Syncing…' }))
    expect(sync).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Export to Claude Code' })).toBeTruthy()
  })

  it('a row that can act runs, names itself alone and has no reason', () => {
    const sync = vi.fn()
    const exportAgents = vi.fn()
    openMenu(sync, exportAgents, false)
    const row = screen.getByRole('button', { name: 'Sync agents' })
    expect(row.getAttribute('aria-disabled')).toBeNull()
    expect(row.textContent).toBe('Sync agents')
    fireEvent.click(row)
    expect(sync).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(screen.getByRole('button', { name: 'Export to Claude Code' }))
    expect(exportAgents).toHaveBeenCalledTimes(1)
  })
})
