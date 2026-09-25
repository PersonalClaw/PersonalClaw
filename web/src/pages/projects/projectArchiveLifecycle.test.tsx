import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, type ProjectItem, type WorkBoard } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { ProjectsSection } from './ProjectsSection'
import { DialogHost } from '../../ui/dialog/DialogHost'

const PROJECT_ID = 'p-archive'
const EMPTY_WORK: WorkBoard = {
  board: [],
  sections: [],
  completeness: 'complete',
  attention: 0,
  loadedAt: 0,
}

let project: ProjectItem
let updateProject: ReturnType<typeof vi.spyOn>
let updateProjectSettings: ReturnType<typeof vi.spyOn>
let defaultProjectId = ''

function mount(status: ProjectItem['status'], isDefault = false) {
  project = {
    id: PROJECT_ID,
    name: 'Launch notes',
    status,
    brief: 'Ship the release notes.',
    context_dir: '/tmp/personalclaw-test/context',
  }
  defaultProjectId = isDefault ? PROJECT_ID : ''

  vi.spyOn(api, 'project').mockImplementation(async () => project)
  vi.spyOn(api, 'taskLists').mockResolvedValue([])
  vi.spyOn(api, 'projectWork').mockResolvedValue(EMPTY_WORK)
  vi.spyOn(api, 'personalclawConfig').mockResolvedValue({
    legibility: { context_adapters: false },
  } as Awaited<ReturnType<typeof api.personalclawConfig>>)
  updateProject = vi.spyOn(api, 'updateProject').mockImplementation(async (id, body) => {
    project = { ...project, ...body, id }
    return project
  })
  // The default project is server-stored (`GET/PUT /api/projects/settings`); an archived default
  // resolves to "none" there, which this mock mirrors.
  vi.spyOn(api, 'projectSettings').mockImplementation(async () => ({
    default_project_id: project.status === 'archived' ? '' : defaultProjectId,
  }))
  updateProjectSettings = vi.spyOn(api, 'updateProjectSettings').mockImplementation(async (body) => {
    defaultProjectId = body.default_project_id
    return body
  })

  render(
    <>
      <ProjectsSection
        sub={PROJECT_ID}
        navigate={() => {}}
        navEpoch={0}
        query={{}}
        setQuery={() => {}}
      />
      <DialogHost />
    </>,
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  resetDataStore()
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  resetDataStore()
})

describe('project archive lifecycle', () => {
  it('makes Archive findable, explains the picker effect, and writes nothing when dismissed', async () => {
    mount('active')
    const archive = await screen.findByRole('button', { name: /^Archive$/i })

    await userEvent.click(archive)
    const dialog = await screen.findByRole('dialog', { name: /Archive project "Launch notes"/i })
    expect(dialog.textContent).toMatch(/project pickers/i)
    expect(dialog.textContent).toMatch(/nothing is deleted/i)
    expect(updateProject, 'asking is not writing').not.toHaveBeenCalled()

    await userEvent.click(within(dialog).getByRole('button', { name: /^Cancel$/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(updateProject, 'dismissal must keep the persisted status').not.toHaveBeenCalled()
  })

  it('confirms Archive through the project update sink and repaints as Restore', async () => {
    mount('active')
    await userEvent.click(await screen.findByRole('button', { name: /^Archive$/i }))
    const dialog = await screen.findByRole('dialog', { name: /Archive project "Launch notes"/i })
    await userEvent.click(within(dialog).getByRole('button', { name: /^Archive$/i }))

    await waitFor(() => expect(updateProject).toHaveBeenCalledWith(PROJECT_ID, { status: 'archived' }))
    expect(await screen.findByRole('button', { name: /^Restore$/i })).toBeTruthy()
  })

  it('restores immediately through the same sink and repaints as Archive', async () => {
    mount('archived')
    const restore = await screen.findByRole('button', { name: /^Restore$/i })

    await userEvent.click(restore)

    await waitFor(() => expect(updateProject).toHaveBeenCalledWith(PROJECT_ID, { status: 'active' }))
    expect(await screen.findByRole('button', { name: /^Archive$/i })).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('calls the unrelated account preference Default project, never project status', async () => {
    mount('active', true)

    const defaultProject = await screen.findByRole('button', { name: /^Default project$/i })
    expect(defaultProject).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: /Set active/i })).toBeNull()
    // It says what it governs and that it is not this browser's alone.
    expect(defaultProject.getAttribute('title')).toMatch(/new tasks and loops start here, on every device/)
  })

  it('makes a project the default through the server, not this browser', async () => {
    mount('active')
    await userEvent.click(await screen.findByRole('button', { name: /^Make default$/i }))
    await waitFor(() => expect(updateProjectSettings).toHaveBeenCalledWith({ default_project_id: PROJECT_ID }))
    expect(await screen.findByRole('button', { name: /^Default project$/i })).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem('active-project'), 'the per-browser pointer is gone').toBeNull()
  })

  it('an archived project cannot become the default, and says why', async () => {
    mount('archived', true)
    const makeDefault = await screen.findByRole('button', { name: /^Make default$/i })
    expect(makeDefault, 'an archived default resolves to none').toHaveAttribute('aria-pressed', 'false')
    expect(makeDefault).toHaveAttribute('aria-disabled', 'true')
    expect(makeDefault.getAttribute('title')).toMatch(/Restore this project/)
    await userEvent.click(makeDefault)
    expect(updateProjectSettings).not.toHaveBeenCalled()
  })

  it('keeps exactly both status writers behind the one shared update sink', () => {
    const source = readFileSync(
      join(process.cwd(), 'src/pages/projects/ProjectsSection.tsx'),
      'utf8',
    )
    const code = source
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    const statuses = [...code.matchAll(/patch\(\{\s*status:\s*'(active|archived)'\s*\}\)/g)]
      .map((match) => match[1])
      .sort()

    expect(statuses, 'Archive and Restore each reach patch()').toEqual(['active', 'archived'])
    expect(code.match(/api\.updateProject\(/g) ?? [], 'one project update sink').toHaveLength(1)
  })
})
