import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { mountPage, PID, project } from '../../test/projectPageHarness'
import { SAVED_LINES } from './ProjectAbout'

// ── Each part says who gets its change (§1.4 item 3) ────────────────────────────────────────────
//
// After a save, the brief's, the instructions' and the overview's lines are §1.4 item 3's, each under
// its own part. The brief's says a chat already going gets it the next time it loads this project's
// context and a loop already going when it resumes or starts its next stage, and never that chats or
// loops already going keep what they started with; the instructions' line never says a new chat gets
// them. The gateway side of the brief's promise is `tests/test_a_saved_brief_reaches_work_already_going.py`.

beforeEach(() => { resetDataStore() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const part = async (name: string) => (await screen.findByRole('heading', { level: 3, name })).closest('section')!

describe('each part of About says who gets its change', () => {
  it("the brief's line, under the brief, once it is saved", async () => {
    const { updateProject } = mountPage({ project: project(PID, 'Garden', { brief: 'Grow the beds.' }) })
    const brief = await part('Brief')
    await userEvent.click(within(brief).getByRole('button', { name: 'Edit' }))
    const field = within(brief).getByRole('textbox', { name: "Garden's brief" })
    await userEvent.clear(field)
    await userEvent.type(field, 'Grow the spring beds.')
    await userEvent.click(within(brief).getByRole('button', { name: 'Save brief' }))
    await waitFor(() => expect(updateProject).toHaveBeenCalledWith(PID, { brief: 'Grow the spring beds.' }))
    expect(await within(brief).findByText(SAVED_LINES.brief)).toBeTruthy()
    expect(brief.textContent).not.toMatch(/keep what they started with|keep the old brief/)
  })

  it("the instructions' line, under the instructions, and never that a new chat gets them", async () => {
    vi.spyOn(api, 'setProjectInstructions').mockResolvedValue(project(PID, 'Garden', { agent_instructions_template: 'Plant late.' }))
    mountPage({ project: project(PID, 'Garden', { agent_instructions_template: 'Plant early.', revisions: { agent_instructions_template: 'r1' } }) })
    const part2 = await part('Instructions')
    await userEvent.click(within(part2).getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog', { name: 'Instructions for Garden' })
    const field = within(dialog).getByRole('textbox', { name: 'Instructions for Garden' })
    await userEvent.clear(field)
    await userEvent.type(field, 'Plant late.')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(await within(part2).findByText(SAVED_LINES.instructions)).toBeTruthy()
    expect(SAVED_LINES.instructions).not.toMatch(/new chat/i)
  })

  it("the lines say what §1.4 item 3 says", () => {
    expect(SAVED_LINES.brief).toBe("Saved. New chats and loops get it. A chat already going gets it the next time it loads this project's context, and a loop already going when it resumes or starts its next stage.")
    expect(SAVED_LINES.instructions).toBe("Saved. An agent gets them the next time it loads this project's context.")
    expect(SAVED_LINES.overview).toBe('Saved. New chats get it. Ones already going keep what they started with.')
  })
})
