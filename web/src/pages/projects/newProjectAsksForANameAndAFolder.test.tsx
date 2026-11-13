import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api, type ProjectItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { NewProjectModal } from './NewProjectDialog'

// ── New project asks for a name and a folder ─────────────────────────────────────────────────────
//
// The dialog asks only what a project needs to begin: a name, and optionally a folder. The brief is
// written on the project's page and the default set from its "…", so neither is asked here. Create
// stays reachable while the name is empty and says why on screen; choosing a folder while the name
// is empty names the project after it; a name already taken is said before the gateway refuses it.

vi.mock('../code/WorkspacePicker', () => ({
  WorkspacePicker: ({ onPick }: { onPick: (dir: string) => void }) => (
    <button type="button" onClick={() => onPick('/home/me/code/garden')}>Pick garden</button>
  ),
}))

beforeEach(() => { resetDataStore() })
afterEach(() => { vi.restoreAllMocks(); resetDataStore() })

function open(taken: string[] = []) {
  const onCreated = vi.fn()
  render(<NewProjectModal onClose={() => {}} onCreated={onCreated} taken={taken} />)
  return { onCreated }
}

describe('the New project dialog', () => {
  it('asks no brief and offers no default box', () => {
    open()
    expect(screen.getByRole('textbox', { name: 'Name' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /Brief/ })).toBeNull()
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.getByText('A project keeps related chats, loops, tasks and files together.')).toBeInTheDocument()
  })

  it('keeps Create reachable while the name is empty, and says why on screen', () => {
    open()
    const create = screen.getByRole('button', { name: /Create project/ })
    expect(create.getAttribute('aria-disabled'), 'off, and still reachable').toBe('true')
    expect(screen.getByText('Give it a name first.')).toBeInTheDocument()
  })

  it("names the project after its folder when the name is empty", async () => {
    open()
    await userEvent.click(screen.getByRole('button', { name: /Choose a folder/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Pick garden' }))
    expect((screen.getByRole('textbox', { name: 'Name' }) as HTMLInputElement).value).toBe('garden')
    expect(screen.getByRole('button', { name: 'Remove folder' })).toBeInTheDocument()
  })

  it('says a taken name before anything is sent', async () => {
    const create = vi.spyOn(api, 'createProject')
    open(['Garden'])
    await userEvent.type(screen.getByRole('textbox', { name: 'Name' }), 'garden')
    expect(screen.getByText('You already have a project called “Garden”.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Create project/ }))
    expect(create).not.toHaveBeenCalled()
  })

  it('is the compact dialog, draws no placeholder for a folder, and every control is a 44 px target at the compact widths', async () => {
    open()
    expect(screen.queryByText('No folder')).toBeNull()
    expect(screen.getByRole('dialog').getAttribute('style')).toMatch(/max-width: 520px/)
    const touch = (el: Element) => expect(el.className).toMatch(/max-lg:min-h-11/)
    touch(screen.getByRole('textbox', { name: 'Name' }))
    touch(screen.getByRole('button', { name: /Choose a folder/ }))
    touch(screen.getByRole('button', { name: 'Cancel' }))
    touch(screen.getByRole('button', { name: /Create project/ }))
    await userEvent.click(screen.getByRole('button', { name: /Choose a folder/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Pick garden' }))
    touch(screen.getByRole('button', { name: 'Remove folder' }))
    touch(screen.getByRole('button', { name: /Change/ }))
  })

  it('makes the project, its name locked, with its folder', async () => {
    const made = { id: 'p-new', name: 'Beds', status: 'active' } as ProjectItem
    const create = vi.spyOn(api, 'createProject').mockResolvedValue(made)
    const { onCreated } = open()
    await userEvent.type(screen.getByRole('textbox', { name: 'Name' }), 'Beds')
    await userEvent.click(screen.getByRole('button', { name: /Create project/ }))
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(made))
    expect(create).toHaveBeenCalledWith({ name: 'Beds', name_locked: true, workspace_dir: undefined })
  })
})
