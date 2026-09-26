/** A project archive can be imported from the Projects page.
 *
 *  `api.projectImport` existed — the preview-then-import route behind it too — and nothing called
 *  it, so the one way to import the archive the project page's Export button writes was `curl`.
 *  The page now takes the archive, previews it (nothing is written), shows what will and will not
 *  arrive and which credentials must be re-entered, and imports only when asked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api, ApiError, type ProjectImportResult } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { ProjectsSection } from './ProjectsSection'

const PLAN: ProjectImportResult = {
  project_name: 'Ingest rework (imported-1)',
  accepted: ['project.json', 'context/notes.md'],
  refused: [{ path: '.git/config', code: 'unsafe_member', message: 'a VCS directory is never imported', fatal: false }],
  secrets_expected: ['GITHUB_TOKEN'],
  ok: true,
  preview: true,
  summary: 'Ingest rework (imported-1): 2 entities would be imported; 1 refused; 1 credential(s) must be re-entered (GITHUB_TOKEN)',
}

const navigate = vi.fn()

function mount() {
  vi.spyOn(api, 'projects').mockResolvedValue([])
  vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' })
  render(<ProjectsSection sub="" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />)
}

const archive = () => new File([new Uint8Array([80, 75, 3, 4])], 'ingest-rework.zip', { type: 'application/zip' })

async function choose(file: File) {
  await userEvent.click(await screen.findByRole('button', { name: /Import/ }))
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  expect(input, 'the page takes an archive').toBeTruthy()
  await userEvent.upload(input, file)
}

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { vi.restoreAllMocks(); resetDataStore() })

describe('importing a project', () => {
  it('🔑 previews first — nothing is written — and says what will and will not arrive', async () => {
    const importer = vi.spyOn(api, 'projectImport').mockResolvedValue(PLAN)
    mount()
    await choose(archive())

    const dialog = await screen.findByRole('dialog')
    expect(importer).toHaveBeenCalledTimes(1)
    expect(importer.mock.calls[0][1]).toEqual({ preview: true })
    const text = dialog.textContent ?? ''
    expect(text).toContain('Ingest rework (imported-1)')
    expect(text).toContain('2 entities would be imported')
    expect(text).toContain('.git/config')
    expect(text).toContain('a VCS directory is never imported')
    expect(text).toContain('GITHUB_TOKEN')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('🔑 Import writes it and opens the new project', async () => {
    const importer = vi.spyOn(api, 'projectImport')
      .mockResolvedValueOnce(PLAN)
      .mockResolvedValueOnce({ ...PLAN, preview: false, project_id: 'p-new', written: ['project.json'], summary: 'Ingest rework (imported-1): 2 entities imported' })
    mount()
    const file = archive()
    await choose(file)
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Import' }))

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('projects/p-new'))
    expect(importer).toHaveBeenCalledTimes(2)
    expect(importer.mock.calls[1][0]).toBe(file)
    expect(importer.mock.calls[1][1] ?? {}).toEqual({})
  })

  it('an archive the server refuses says why, and imports nothing', async () => {
    vi.spyOn(api, 'projectImport').mockRejectedValue(new ApiError('the archive has no MANIFEST.json', 400))
    mount()
    await choose(archive())
    expect(await screen.findByText(/the archive has no MANIFEST\.json/)).toBeTruthy()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('an archive with nothing importable cannot be imported', async () => {
    vi.spyOn(api, 'projectImport').mockResolvedValue({ ...PLAN, accepted: [], ok: false, summary: 'Ingest rework: 0 entities would be imported; 1 refused' })
    mount()
    await choose(archive())
    const dialog = await screen.findByRole('dialog')
    const go = within(dialog).getByRole('button', { name: 'Import' })
    expect(go.hasAttribute('disabled') || go.getAttribute('aria-disabled') === 'true').toBe(true)
  })
})
