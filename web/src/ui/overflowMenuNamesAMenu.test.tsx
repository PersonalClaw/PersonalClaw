// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { HeaderOverflow } from './HeaderActions'

// ── A control with a menu of its own lists its rows in the `…` under its name ───────────────────
//
// A `HeaderMenu` (a project's New: New task, New chat) cannot be one row of the `…`, since a row
// cannot open a second menu, so its rows go there instead. They went with nothing over them, so
// "New task" sat beside "Export" and read as one of the header's own actions. Its name is a heading
// over them now, and names the group for a screen reader.

describe('the `…` menu', () => {
  it("heads a menu's rows with its name, and keeps a plain control a plain row", () => {
    const onTask = vi.fn()
    render(<HeaderOverflow actions={[
      { id: 1, label: 'Export' },
      { id: 2, label: 'New', rows: [{ label: 'New task', onSelect: onTask }, { label: 'New chat' }] },
    ]} />)
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const group = screen.getByRole('group', { name: 'New' })
    expect(within(group).getByText('New')).toBeTruthy()
    expect(within(group).getAllByRole('button').map((b) => b.textContent?.trim())).toEqual(['New task', 'New chat'])
    expect(screen.queryByRole('group', { name: 'Export' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Export' })).toBeTruthy()
    fireEvent.click(within(group).getByRole('button', { name: 'New task' }))
    expect(onTask).toHaveBeenCalledOnce()
  })
})
