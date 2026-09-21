import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, type ProjectItem, type WorkBoard } from '../../lib/api'
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

function mount(status: ProjectItem['status'], isDefault = false) {
  project = {
    id: PROJECT_ID,
    name: 'Launch notes',
    status,
    brief: 'Ship the release notes.',
    context_dir: '/tmp/personalclaw-test/context',
  }
  if (isDefault) localStorage.setItem('active-project', PROJECT_ID)

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
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
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

  it('calls the unrelated local preference Default project, never project status', async () => {
    mount('active', true)

    const defaultProject = await screen.findByRole('button', { name: /^Default project$/i })
    expect(defaultProject).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: /Set active/i })).toBeNull()
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
