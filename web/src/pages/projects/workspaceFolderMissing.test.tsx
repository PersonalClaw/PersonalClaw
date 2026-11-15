/** A project whose folder no longer exists SAYS SO on the project's page.
 *
 *  Measured on the container image: a project bound to a folder created at the picker's old starting
 *  point lost it on `docker rm` + `docker run` (only `/data` is a volume), and the project page went on
 *  showing the path with Change beside it, exactly as it looked when the folder was there. Nothing said
 *  it was gone; the user found out when work in it failed. The same happens on any install when a repo
 *  is moved or deleted.
 *
 *  So the page asks browse-dirs, and a 404, only a 404, is "missing": the header's chip reads "Folder
 *  missing" and About › Folder says where it was and what to do, with Choose another folder and Remove
 *  folder, and offers no Open and no tree, which would open a path that is not there. A 403 is a folder
 *  that may well be there: it is never called missing, and says it could not be read.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { mountPage, PID, project } from '../../test/projectPageHarness'

const DIR = '~/code/garden'
const garden = () => project(PID, 'Garden', { workspace_dir: DIR, folder: { resolved: '/Users/me/code/garden', trust: 'preview' } })

beforeEach(() => { resetDataStore() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const folderPart = async () => (await screen.findByRole('heading', { level: 3, name: 'Folder' })).closest('section')!

/** The probe has been asked and answered, so an absent warning is a verdict, not a race. */
async function settled() {
  await waitFor(() => expect(api.browseDirs).toHaveBeenCalledWith(DIR))
  await new Promise((resolve) => setTimeout(resolve, 0))
}

describe("the project's page and a folder that is gone", () => {
  it('🔑 says the folder is missing, where it was, and what to do', async () => {
    mountPage({ project: garden(), folder: 404 })
    expect(await screen.findByText('Folder missing')).toBeTruthy()
    const part = await folderPart()
    await waitFor(() => expect(part.textContent).toContain(`This folder is missing. It was at ${DIR}. Restore it there, or choose another.`))
    expect(within(part).getByRole('button', { name: 'Choose another folder' })).toBeTruthy()
    expect(within(part).getByRole('button', { name: 'Remove folder' })).toBeTruthy()
    expect(within(part).queryByRole('button', { name: 'Open' }), 'it offered to open a folder that is not there').toBeNull()
    expect(screen.queryByRole('button', { name: DIR }), 'the chip still opens the tree').toBeNull()
    expect(screen.getByText("Garden's folder is missing, so chats here can't open its files. Choose another folder in About this project.")).toBeTruthy()
  })

  it('says nothing about a folder that is there, and offers to open it', async () => {
    mountPage({ project: garden(), folder: 'there' })
    await settled()
    expect(screen.queryByText('Folder missing')).toBeNull()
    const part = await folderPart()
    expect(within(part).getByRole('button', { name: 'Open' })).toBeTruthy()
    expect(screen.getByRole('button', { name: DIR }), 'the header chip opens the folder beside').toBeTruthy()
  })

  it('never calls a protected or unreadable folder missing', async () => {
    mountPage({ project: garden(), folder: 403 })
    await settled()
    expect(screen.queryByText('Folder missing')).toBeNull()
    const part = await folderPart()
    await waitFor(() => expect(part.textContent).toContain("PersonalClaw can't read this folder right now. It may still be there."))
    expect(part.textContent).not.toMatch(/missing/)
  })
})

describe('both surfaces that ask use the one status-keyed hook', () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')

  it('🔑 the code cockpit no longer decides "missing" by matching the error text', () => {
    // The cockpit asks from its controller, where its state moved.
    const cockpit = read('pages/code/cockpit/useCodeCockpit.ts')
    expect(cockpit).toContain('useWorkspaceMissing(')
    expect(cockpit.toLowerCase()).not.toContain(".includes('no such directory')")
  })

  it("the project's page asks through the same module, which keeps an unreadable folder apart", () => {
    expect(read('pages/projects/ProjectPage.tsx')).toContain('useWorkspaceState(')
    expect(read('lib/useWorkspaceMissing.ts')).toMatch(/return useWorkspaceState\(path, opts\) === 'missing'/)
  })
})
