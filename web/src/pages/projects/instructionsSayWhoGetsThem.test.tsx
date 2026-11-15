import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api, ApiError } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { mountPage, PID, project } from '../../test/projectPageHarness'

// ── The instructions' window says who gets them (§5.10, §7) ─────────────────────────────────────
//
// The window's first line is the honest one: an agent gets them when it loads this project's
// context. A stale save keeps the draft and says so (`StaleWriteNotice`). The file line names no
// file and no tool. A proposed change from Learning shows as "1 change proposed" with Review.

beforeEach(() => { resetDataStore() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const garden = () => project(PID, 'Garden', { agent_instructions_template: 'Plant early.', revisions: { agent_instructions_template: 'r1' }, workspace_dir: '~/code/garden' })

describe("a project's instructions", () => {
  it("open over the revision they read, with the honest first line", async () => {
    mountPage({ project: garden() })
    const part = (await screen.findByRole('heading', { level: 3, name: 'Instructions' })).closest('section')!
    await userEvent.click(within(part).getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog', { name: 'Instructions for Garden' })
    expect(within(dialog).getByText("An agent gets these when it loads this project's context. Write them as plain rules.")).toBeTruthy()
  })

  it('keep the draft through a stale save, and say so', async () => {
    vi.spyOn(api, 'setProjectInstructions').mockRejectedValue(new ApiError('changed since you read it', 409, 'stale_write'))
    mountPage({ project: garden() })
    const part = (await screen.findByRole('heading', { level: 3, name: 'Instructions' })).closest('section')!
    await userEvent.click(within(part).getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog', { name: 'Instructions for Garden' })
    const field = within(dialog).getByRole('textbox', { name: 'Instructions for Garden' })
    await userEvent.type(field, ' Then water.')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(within(dialog).getByText(/Garden's instructions/)).toBeTruthy())
    expect((field as HTMLTextAreaElement).value).toBe('Plant early. Then water.')
  })

  it('name no file and no tool on the files line, and count what Learning proposes', async () => {
    mountPage({ project: garden(), adapters: true, proposals: [{ id: 'pr-1', kind: 'project_instruction', project_id: PID }, { id: 'pr-2', kind: 'project_instruction', project_id: 'p-other' }] })
    const part = (await screen.findByRole('heading', { level: 3, name: 'Instructions' })).closest('section')!
    expect(await within(part).findByText('1 change proposed')).toBeTruthy()
    expect(within(part).getByRole('button', { name: 'Review' })).toBeTruthy()
    await waitFor(() => expect(within(part).getByText('Also written into files in its folder that other AI tools read.')).toBeTruthy())
    expect(part.textContent).not.toMatch(/CLAUDE\.md|AGENTS\.md|\.cursorrules|Cursor|Claude Code/)
  })
})
