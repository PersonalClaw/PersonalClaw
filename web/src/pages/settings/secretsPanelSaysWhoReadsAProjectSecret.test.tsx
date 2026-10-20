import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'

// ── Settings › Secrets: a project's secret is chosen by project, and the page says who reads it ──
//
// The backend reads a project's secret for that project's work alone, ahead of a global secret of
// the same name, and for nothing outside the project. The page used to take the project as a
// free-text id (an id typed wrong stored a secret no run would ever read), headed each group with
// that id, and described a project secret only as "scoped to a single project". These pin what the
// page now says and sends:
//
//   • the project is chosen from the projects that exist, by name, with the shared picker;
//   • storing sends the chosen project's id, and the note names the project;
//   • each group is headed by its project's name, and an id no project has is said to be one;
//   • the per-project section says which work reads one, and that nothing outside does;
//   • a project row's "used by" names the workflows that read it in this project;
//   • removing a project's secret says what that project's work reads next.

type Row = {
  name: string
  scope: 'global' | 'project' | 'host'
  project_id?: string
  consumers?: { kind: 'workflow' | 'trigger'; id: string; label: string }[]
}

const payload = (rows: Row[]) => {
  const secrets = rows.map((r) => ({
    name: r.name,
    scope: r.scope,
    project_id: r.project_id ?? '',
    present: true as const,
    inherited_from_host: r.scope === 'host',
    consumers: r.consumers ?? [],
  }))
  return {
    secrets,
    counts: {
      total: secrets.length,
      global: secrets.filter((s) => s.scope === 'global').length,
      project: secrets.filter((s) => s.scope === 'project').length,
      host: secrets.filter((s) => s.scope === 'host').length,
    },
    empty_hint: secrets.length === 0 ? 'No secrets stored yet.' : '',
    // No OS keychain answers here, so the page names no keychain namespace.
    store: { backend: 'dotenv' as const, keychain_namespace: '', keychain_scope: '' as const },
  }
}

const PROJECTS = [
  { id: 'p-0a1b2c3d', name: 'Garden', status: 'active' as const },
  { id: 'p-9f8e7d6c', name: 'Allotment', status: 'active' as const },
]

const putSecret = vi.fn()
const deleteSecret = vi.fn()
const confirmed = vi.fn()

async function mount(rows: Row[], projects: () => Promise<unknown> = async () => PROJECTS) {
  vi.resetModules()
  sessionStorage.clear()
  putSecret.mockReset().mockResolvedValue({ secret: {}, secrets: [] })
  deleteSecret.mockReset().mockResolvedValue({ deleted: '', project_id: '', secrets: [] })
  confirmed.mockReset().mockResolvedValue(true)
  vi.doMock('../../ui/dialog', () => ({ confirm: confirmed }))
  vi.doMock('../../lib/api', () => ({
    // The shared load-error sentence asks whether a failure is an HTTP one.
    ApiError: class ApiError extends Error { status = 0 },
    api: {
      secrets: async () => payload(rows),
      projects,
      putSecret,
      deleteSecret,
    },
  }))
  const { SecretsPanel } = await import('./SecretsPanel')
  await act(async () => {
    render(<SecretsPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

beforeEach(() => { sessionStorage.clear() })
afterEach(() => { cleanup(); vi.resetModules(); vi.restoreAllMocks() })

describe('a project secret is stored for a project chosen by name', () => {
  it('offers the projects that exist and sends the chosen one\'s id', async () => {
    await mount([])
    const picker = screen.getByRole('button', { name: /^project: every project$/i })
    await act(async () => {
      fireEvent.click(picker)
      await new Promise((res) => setTimeout(res, 0))
    })
    const list = screen.getByRole('listbox', { name: /project/i })
    expect(within(list).getByRole('option', { name: /allotment/i })).toBeTruthy()
    await act(async () => { fireEvent.click(within(list).getByRole('option', { name: /garden/i })) })

    fireEvent.change(screen.getByLabelText('Secret name'), { target: { value: 'GARDEN_TOKEN' } })
    fireEvent.change(screen.getByLabelText('Secret value'), { target: { value: 'not-a-real-value' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /store secret/i }))
      await new Promise((res) => setTimeout(res, 0))
    })

    expect(putSecret).toHaveBeenCalledWith('GARDEN_TOKEN', 'not-a-real-value', 'p-0a1b2c3d')
    expect(screen.getByRole('status').textContent).toBe('GARDEN_TOKEN stored for Garden.')
  })

  it('stores for every project when no project is chosen', async () => {
    await mount([])
    fireEvent.change(screen.getByLabelText('Secret name'), { target: { value: 'GLOBAL_TOKEN' } })
    fireEvent.change(screen.getByLabelText('Secret value'), { target: { value: 'not-a-real-value' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /store secret/i }))
      await new Promise((res) => setTimeout(res, 0))
    })
    expect(putSecret).toHaveBeenCalledWith('GLOBAL_TOKEN', 'not-a-real-value', '')
    expect(screen.getByRole('status').textContent).toBe('GLOBAL_TOKEN stored for every project.')
  })

  it('takes no project id as typed text', async () => {
    await mount([])
    expect(screen.queryByLabelText(/project id/i)).toBeNull()
  })
})

describe('the page says who reads a project secret', () => {
  it('heads each group with its project\'s name, and says when no project has the id', async () => {
    await mount([
      { name: 'GARDEN_TOKEN', scope: 'project', project_id: 'p-0a1b2c3d' },
      { name: 'OLD_TOKEN', scope: 'project', project_id: 'p-gone0000' },
    ])
    expect(screen.getByText('Garden')).toBeTruthy()
    expect(screen.getByText('p-gone0000 (not a current project)')).toBeTruthy()
  })

  it('a failed projects read names groups by id and says the names could not be loaded', async () => {
    await mount(
      [{ name: 'GARDEN_TOKEN', scope: 'project', project_id: 'p-0a1b2c3d' }],
      // No message of its own, so the panel's sentence is what shows.
      async () => { throw new Error('') },
    )
    expect(screen.getByText('p-0a1b2c3d')).toBeTruthy()
    expect(within(screen.getByRole('alert')).getByText(/couldn't load your projects/i)).toBeTruthy()
  })

  it('says only that project\'s work reads one, ahead of the global one, and nothing outside', async () => {
    await mount([{ name: 'GARDEN_TOKEN', scope: 'project', project_id: 'p-0a1b2c3d' }])
    expect(screen.getByText(/only that project's work — its workflow runs, loops and chats — reads one, ahead of a global secret of the same name\. nothing outside the project reads it\./i)).toBeTruthy()
  })

  it('names the workflows that read a project row in that project', async () => {
    await mount([
      {
        name: 'GARDEN_TOKEN',
        scope: 'project',
        project_id: 'p-0a1b2c3d',
        consumers: [{ kind: 'workflow', id: 'garden-sync', label: 'Garden sync' }],
      },
      { name: 'UNUSED_TOKEN', scope: 'project', project_id: 'p-0a1b2c3d' },
    ])
    expect(screen.getByText('Read in this project by')).toBeTruthy()
    expect(screen.getByText('Garden sync')).toBeTruthy()
    // An automation runs in no project, so a project row never claims to have been checked for one.
    expect(screen.getByText('Not referenced by any workflow.')).toBeTruthy()
  })
})

describe('removing a secret says what reads it next', () => {
  it('a project secret: that project\'s work falls back to the global one', async () => {
    await mount([
      { name: 'GARDEN_TOKEN', scope: 'global' },
      { name: 'GARDEN_TOKEN', scope: 'project', project_id: 'p-0a1b2c3d' },
    ])
    const projectGroup = screen.getByText('Garden').parentElement as HTMLElement
    await act(async () => { fireEvent.click(within(projectGroup).getByRole('button', { name: /remove/i })) })

    const asked = confirmed.mock.calls[0][0]
    expect(asked.title).toBe('Remove GARDEN_TOKEN from Garden?')
    expect(asked.body).toMatch(/work in garden that references \{\{secret:GARDEN_TOKEN\}\} then reads the global GARDEN_TOKEN instead/i)
    expect(deleteSecret).toHaveBeenCalledWith('GARDEN_TOKEN', 'p-0a1b2c3d')
  })

  it('a project secret with no global one: that project\'s work fails until it is replaced', async () => {
    await mount([{ name: 'GARDEN_TOKEN', scope: 'project', project_id: 'p-0a1b2c3d' }])
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /remove/i })) })
    expect(confirmed.mock.calls[0][0].body).toMatch(/fails until it is replaced, since no global GARDEN_TOKEN is stored/i)
  })

  it('a global secret a project also holds: that project keeps its own', async () => {
    await mount([
      { name: 'GARDEN_TOKEN', scope: 'global' },
      { name: 'GARDEN_TOKEN', scope: 'project', project_id: 'p-0a1b2c3d' },
    ])
    const globalRemove = screen.getAllByRole('button', { name: /remove/i })[0]
    await act(async () => { fireEvent.click(globalRemove) })
    expect(confirmed.mock.calls[0][0].title).toBe('Remove GARDEN_TOKEN?')
    expect(confirmed.mock.calls[0][0].body).toMatch(/except work in Garden, which reads its project's own GARDEN_TOKEN/)
    expect(deleteSecret).toHaveBeenCalledWith('GARDEN_TOKEN', '')
  })
})
