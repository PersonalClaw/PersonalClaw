/** Adding to a project moves the thing for real: the write goes through the
 *  library's one path to the thing's own store, Move says what it leaves, and Undo puts it back. No
 *  project shows only while the thing is in one, its button reads "Remove from Homelab", and it sends
 *  "" with an Undo that files it back. The first sentence names the project picked. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answers, calls, resetGateway, row } from '../../test/libraryHarness'

const toasts: { message: string; action?: { label: string; run: () => void } }[] = []
vi.mock('../../app/appSdk', async (orig) => ({
  ...(await orig<typeof import('../../app/appSdk')>()),
  notify: (message: string) => { toasts.push({ message }) },
  notifyWithAction: (message: string, action: { label: string; run: () => void }) => { toasts.push({ message, action }) },
}))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { fileInto } = await import('./libraryActions')
const { ProjectPickerDialog } = await import('../../ui/ProjectPicker')
const { resetDataStore } = await import('../../lib/data/store')

const PROJECTS = [
  { id: 'p-trip', name: 'Trip planning', status: 'active', updated_at: '2026-11-10T00:00:00Z' },
  { id: 'p-home', name: 'Homelab', status: 'active', updated_at: '2026-11-09T00:00:00Z' },
  { id: 'p-garden', name: 'Garden', status: 'active', updated_at: '2026-11-08T00:00:00Z' },
]
const ok = (body: { refs: string[] }) => ({ op: 'project', results: body.refs.map((ref) => ({ ref, ok: true })) })

beforeEach(() => {
  resetDataStore(); resetGateway(); toasts.length = 0
  answers.libraryBulk = ok
  answers.projects = PROJECTS
})
afterEach(cleanup)

describe('adding to a project moves it for real', () => {
  it('🔑 Add files the thing in the project, and Undo takes it out again', async () => {
    const item = row('knowledge:k1', 'Lisbon trip plan')
    await fileInto(item, { id: 'p-trip', name: 'Trip planning' }, { projects: [] })
    expect(calls.libraryBulk).toEqual([[{ op: 'project', refs: ['knowledge:k1'], project_id: 'p-trip' }]])
    expect(toasts.at(-1)?.message).toBe('Added “Lisbon trip plan” to Trip planning.')
    toasts.at(-1)!.action!.run()
    await waitFor(() => expect(calls.libraryBulk.at(-1)).toEqual([{ op: 'project', refs: ['knowledge:k1'], project_id: '' }]))
  })

  it('🔑 Move says so, and Undo puts it back where it was', async () => {
    const item = row('artifact:router', 'Router manual', { project: { id: 'p-home', name: 'Homelab' } })
    await fileInto(item, { id: 'p-garden', name: 'Garden' }, { projects: [] })
    expect(toasts.at(-1)?.message).toBe('Moved “Router manual” to Garden.')
    toasts.at(-1)!.action!.run()
    await waitFor(() => expect(calls.libraryBulk.at(-1)).toEqual([{ op: 'project', refs: ['artifact:router'], project_id: 'p-home' }]))
  })

  it('🔑 No project sends "" to its store, and Undo files it back', async () => {
    const item = row('artifact:router', 'Router manual', { project: { id: 'p-home', name: 'Homelab' } })
    await fileInto(item, null, { projects: [] })
    expect(calls.libraryBulk[0]).toEqual([{ op: 'project', refs: ['artifact:router'], project_id: '' }])
    expect(toasts.at(-1)?.message).toBe('Removed “Router manual” from Homelab.')
    toasts.at(-1)!.action!.run()
    await waitFor(() => expect(calls.libraryBulk.at(-1)).toEqual([{ op: 'project', refs: ['artifact:router'], project_id: 'p-home' }]))
  })

  it('a write that fails says so, and offers no Undo', async () => {
    answers.libraryBulk = (body: { refs: string[] }) => ({ op: 'project', results: body.refs.map((ref) => ({ ref, ok: false, error: 'failed' })) })
    await fileInto(row('knowledge:k1', 'Lisbon trip plan'), { id: 'p-trip', name: 'Trip planning' }, { projects: [] })
    expect(toasts.at(-1)).toEqual({ message: "Couldn't file “Lisbon trip plan”." })
  })
})

describe('the picker\'s dialog form', () => {
  it('🔑 for a thing in no project: no No project row, and the sentence names the project picked', async () => {
    const onPick = vi.fn()
    render(<ProjectPickerDialog itemTitle="Lisbon trip plan" current={null} onPick={onPick} onClose={vi.fn()} />)
    await screen.findByRole('radio', { name: /Trip planning/ })
    expect(screen.queryByRole('radio', { name: /No project/ })).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: /Garden/ }))
    expect(screen.getByText("It's filed under Garden. It stays in your library.")).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Add to Garden' }))
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 'p-garden' }))
  })

  it('🔑 for a thing in Homelab: No project heads the list, Homelab is In it now and not pickable, and No project reads Remove from Homelab', async () => {
    const onPick = vi.fn()
    render(<ProjectPickerDialog itemTitle="Router manual" current={{ id: 'p-home', name: 'Homelab' }} onPick={onPick} onClose={vi.fn()} />)
    const radios = await screen.findAllByRole('radio')
    expect(radios[0].textContent).toContain('No project')
    const home = screen.getByRole('radio', { name: /Homelab/ })
    expect(home.textContent).toContain('In it now')
    expect((home as HTMLButtonElement).disabled).toBe(true)
    await userEvent.click(screen.getByRole('radio', { name: /Garden/ }))
    expect(screen.getByText('It leaves Homelab.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Move to Garden' })).toBeTruthy()
    await userEvent.click(screen.getByRole('radio', { name: /No project/ }))
    expect(screen.getByText('It leaves Homelab. It stays in your library.')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Remove from Homelab' }))
    expect(onPick).toHaveBeenCalledWith(null)
  })

  it('"New project…" is last, so the list never dead-ends', async () => {
    render(<ProjectPickerDialog itemTitle="Router manual" current={null} onPick={vi.fn()} onClose={vi.fn()} />)
    await screen.findByRole('radio', { name: /Trip planning/ })
    const group = screen.getByRole('radiogroup', { name: 'Projects' })
    expect(group.lastElementChild?.textContent).toBe('New project…')
  })
})
