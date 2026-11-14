import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { api, type ProjectItem, type WorkBoard } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { mountList, navigate as listNavigate, project } from '../../test/projectListHarness'
import { ProjectsSection } from './ProjectsSection'

// ── Rename opens the page's title in edit mode ───────────────────────────────────────────────────
//
// A project row's "…" Rename opens the project's page at `?rename=1`. The key is dropped at once
// with a replace, while the read still pends, so Back and a reload never reopen the editor; once the
// project arrives its title opens in edit mode with the name in it, focused. It opens nothing when
// the project does not load. A built-in's row has Rename off with its reason, and its page ignores
// the key.

const BOARD: WorkBoard = { board: [], sections: [], completeness: 'complete', attention: 0, loadedAt: 0 }
const navigate = vi.fn()

function page(p: ProjectItem | Error | Promise<ProjectItem>) {
  vi.spyOn(api, 'project').mockImplementation(() => (p instanceof Error ? Promise.reject(p) : Promise.resolve(p)))
  vi.spyOn(api, 'taskLists').mockResolvedValue([])
  vi.spyOn(api, 'projectWork').mockResolvedValue(BOARD)
  vi.spyOn(api, 'personalclawConfig').mockResolvedValue({ legibility: { context_adapters: false } } as Awaited<ReturnType<typeof api.personalclawConfig>>)
  vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '', pinned_project_ids: [] })
  const writes: { patch: Record<string, string | null | undefined>; opts?: { replace?: boolean } }[] = []
  function Host() {
    const [query, setQueryState] = useState<Record<string, string>>({ rename: '1' })
    const setQuery = (patch: Record<string, string | null | undefined>, opts?: { replace?: boolean }) => {
      writes.push({ patch, opts })
      setQueryState((q) => {
        const next = { ...q }
        for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
        return next
      })
    }
    return <ProjectsSection sub="p-garden" navigate={navigate} navEpoch={0} query={query} setQuery={setQuery} />
  }
  render(<Host />)
  return writes
}

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset(); listNavigate.mockReset() })
afterEach(() => { vi.restoreAllMocks(); resetDataStore() })

describe('?rename=1 on a project’s page', () => {
  it('opens the title editor with the name in it, focused, and drops the key with a replace', async () => {
    const writes = page({ id: 'p-garden', name: 'Garden', status: 'active', brief: '', context_dir: '/tmp/ctx' } as ProjectItem)
    const field = await screen.findByRole('textbox', { name: 'Rename this project' })
    expect((field as HTMLInputElement).value).toBe('Garden')
    expect(document.activeElement).toBe(field)
    expect(writes).toContainEqual({ patch: { rename: null }, opts: { replace: true } })
  })

  it('drops the key at once while the read pends, and opens the editor when the project arrives', async () => {
    let arrive: (p: ProjectItem) => void = () => {}
    const pending = new Promise<ProjectItem>((resolve) => { arrive = resolve })
    const writes = page(pending)
    await waitFor(() => expect(writes).toContainEqual({ patch: { rename: null }, opts: { replace: true } }))
    expect(screen.queryByRole('textbox', { name: 'Rename this project' })).toBeNull()
    arrive({ id: 'p-garden', name: 'Garden', status: 'active', brief: '', context_dir: '/tmp/ctx' } as ProjectItem)
    const field = await screen.findByRole('textbox', { name: 'Rename this project' })
    expect((field as HTMLInputElement).value).toBe('Garden')
  })

  it('drops the key when the project does not load', async () => {
    const writes = page(new Error('404'))
    await waitFor(() => expect(writes).toContainEqual({ patch: { rename: null }, opts: { replace: true } }))
    expect(screen.queryByRole('textbox', { name: 'Rename this project' })).toBeNull()
  })

  it("is ignored on a built-in's page", async () => {
    const writes = page({ id: 'p-garden', name: 'Personal', status: 'active', is_builtin: true, brief: '', context_dir: '/tmp/ctx' } as ProjectItem)
    await waitFor(() => expect(writes).toContainEqual({ patch: { rename: null }, opts: { replace: true } }))
    expect(screen.queryByRole('textbox', { name: 'Rename this project' })).toBeNull()
  })
})

describe("a row's Rename", () => {
  it('opens the page at ?rename=1, and is off with its reason on a built-in', async () => {
    mountList({ projects: [project('p-garden', 'Garden'), project('p-personal', 'Personal', { is_builtin: true })] })
    const garden = (await screen.findByRole('heading', { level: 2, name: /^Garden/ })).closest('li') as HTMLElement
    await userEvent.click(within(garden).getByRole('button', { name: 'More actions for Garden' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Rename…' }))
    expect(listNavigate).toHaveBeenCalledWith('projects/p-garden?rename=1')
    const personal = (await screen.findByRole('heading', { level: 2, name: /^Personal/ })).closest('li') as HTMLElement
    await userEvent.click(within(personal).getByRole('button', { name: 'More actions for Personal' }))
    const off = await screen.findByRole('button', { name: 'Rename…' })
    expect(off.getAttribute('aria-disabled')).toBe('true')
    expect(off.getAttribute('title')).toBe("Personal is built in. It can't be renamed.")
  })
})
