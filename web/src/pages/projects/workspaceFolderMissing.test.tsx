/** A project whose workspace folder no longer exists SAYS SO on the project page.
 *
 *  Measured on the container image (2026-09-25): a project bound to a folder created at the picker's
 *  old starting point, `/home/personalclaw/garden-planner`, lost it on `docker rm` + `docker run`
 *  — only `/data` is a volume — and the project page went on showing
 *  `WORKSPACE /home/personalclaw/garden-planner` with Change and × beside it, exactly as it looked
 *  when the folder was there. Nothing said it was gone; the user found out when work in it failed.
 *  The same happens on any install when a repo is moved or deleted.
 *
 *  So the page asks browse-dirs, and a 404 — only a 404 — becomes a visible, announced line that
 *  names the folder and says what to do. The chip also stops offering to open the folder, since
 *  both "view contents" and "Open in Files" would open a path that is not there.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, ApiError, type ProjectItem, type WorkBoard } from '../../lib/api'
import { ProjectsSection } from './ProjectsSection'

const PROJECT_ID = 'p-eae77a55'
const DEAD = '/home/personalclaw/garden-planner'
const EMPTY_WORK: WorkBoard = { board: [], sections: [], completeness: 'complete', attention: 0, loadedAt: 0 }

function mount(browse: () => Promise<unknown>) {
  const project: ProjectItem = {
    id: PROJECT_ID,
    name: 'Garden planner',
    status: 'active',
    brief: 'Plan the spring vegetable garden.',
    workspace_dir: DEAD,
    context_dir: `/data/projects/${PROJECT_ID}/context`,
  }
  vi.spyOn(api, 'project').mockResolvedValue(project)
  vi.spyOn(api, 'taskLists').mockResolvedValue([])
  vi.spyOn(api, 'projectWork').mockResolvedValue(EMPTY_WORK)
  vi.spyOn(api, 'personalclawConfig').mockResolvedValue({
    legibility: { context_adapters: false },
  } as Awaited<ReturnType<typeof api.personalclawConfig>>)
  const browseDirs = vi.spyOn(api, 'browseDirs').mockImplementation(browse as () => ReturnType<typeof api.browseDirs>)
  render(<ProjectsSection sub={PROJECT_ID} navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />)
  return browseDirs
}

/** The probe has been asked and answered, so an absent warning is a verdict, not a race. */
async function settled(browseDirs: ReturnType<typeof vi.spyOn>) {
  await waitFor(() => expect(browseDirs).toHaveBeenCalledWith(DEAD))
  await new Promise((resolve) => setTimeout(resolve, 0))
}

afterEach(() => { vi.restoreAllMocks() })

describe('the project page and a workspace folder that is gone', () => {
  it('🔑 says the folder no longer exists, names it, and says what to do', async () => {
    mount(() => Promise.reject(new ApiError('No such directory', 404)))
    const line = await screen.findByRole('status')
    expect(line.textContent).toMatch(/Folder missing/)
    expect(line.textContent).toContain('garden-planner')
    expect(line.textContent).toContain(`no longer exists at ${DEAD}`)
    expect(line.textContent).toMatch(/Choose Change to bind a folder that exists/)
    // The way out is on screen, beside the warning.
    expect(screen.getByRole('button', { name: 'Change' })).toBeTruthy()
  })

  it('stops offering to open a folder that is not there', async () => {
    mount(() => Promise.reject(new ApiError('No such directory', 404)))
    await screen.findByRole('status')
    // The path is still SHOWN (it is the thing that is wrong), but no longer as a peek button, and
    // "Open Workspace in Files" is gone — both would open the dead path.
    expect(screen.getAllByText(DEAD).some((el) => el.tagName === 'CODE')).toBe(true)
    expect(screen.queryByRole('button', { name: DEAD })).toBeNull()
    expect(screen.queryByRole('button', { name: /Open Workspace in Files/i })).toBeNull()
    // Unbind stays: it is one of the two ways out.
    expect(screen.getByRole('button', { name: /Unbind Workspace/i })).toBeTruthy()
  })

  it('says nothing about a folder that is there, and offers to open it', async () => {
    const browseDirs = mount(() => Promise.resolve({ path: DEAD, parent: '/home/personalclaw', dirs: [] }))
    await settled(browseDirs)
    expect(screen.queryByText(/Folder missing/)).toBeNull()
    expect(screen.getByRole('button', { name: DEAD })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Open Workspace in Files/i })).toBeTruthy()
  })

  it('does not call a protected or unreadable folder missing', async () => {
    const browseDirs = mount(() => Promise.reject(new ApiError("Can't open it — permission denied", 403)))
    await settled(browseDirs)
    expect(screen.queryByText(/Folder missing/)).toBeNull()
  })
})

describe('both surfaces that ask use the one status-keyed hook', () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')

  it('🔑 the code cockpit no longer decides "missing" by matching the error text', () => {
    const cockpit = read('pages/code/CodeCockpitPage.tsx')
    expect(cockpit).toContain('useWorkspaceMissing(')
    expect(cockpit.toLowerCase()).not.toContain(".includes('no such directory')")
  })

  it('the project page asks through the same hook', () => {
    expect(read('pages/projects/ProjectsSection.tsx')).toContain('useWorkspaceMissing(')
  })
})
