import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { mountList, navigate, project } from '../../test/projectListHarness'

// ── The list never lands on a blank page ─────────────────────────────────────────────────────────
//
// Show: Active with every project archived says so, with Show archived and New project. Show:
// Archived with nothing archived says so, with a way back to the active ones.

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { resetDataStore() })

describe('an empty view of the list', () => {
  it('Active, with every project archived: says no project is active, with Show archived and New project', async () => {
    const { address } = mountList({ projects: [project('p-a', 'Personal', { is_builtin: true, status: 'archived' }), project('p-b', 'Garden', { status: 'archived' })] })
    const said = await screen.findByText('No project is active.')
    expect(within(said.parentElement as HTMLElement).getByRole('button', { name: /New project/ })).toBeInTheDocument()
    expect(document.querySelector('[data-list-line]'), 'no "0 projects" over it').toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Show archived' }))
    await waitFor(() => expect(address.query.show).toBe('archived'))
  })

  it('Archived, with nothing archived: says so, with a way back', async () => {
    const { address } = mountList({ projects: [project('p-b', 'Garden')], query: { show: 'archived' } })
    expect(await screen.findByText('Nothing is archived.')).toBeInTheDocument()
    expect(document.querySelector('[data-list-line]'), 'no "0 projects" over it').toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Show active projects' }))
    await waitFor(() => expect(address.query.show).toBeUndefined())
  })

  it('All carries the Archived chip on an archived project', async () => {
    mountList({ projects: [project('p-b', 'Garden', { status: 'archived' }), project('p-c', 'Trip')], query: { show: 'all' } })
    const garden = (await screen.findByRole('heading', { level: 2, name: /^Garden/ })).closest('li') as HTMLElement
    expect(garden.textContent).toContain('Archived')
  })
})
