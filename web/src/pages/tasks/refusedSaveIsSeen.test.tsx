import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api, ApiError, type ProjectItem, type TaskItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { TaskDetail } from './TaskDetail'

// ── A refused task Save must be SEEN, not merely present ────────────────────────────────────
//
// MEASURED in the task side panel at 1440×900: set a task with an unmet exit criterion to
// Completed and press Save. The server answers 400 "cannot complete: unfinished exit criteria —
// Copy reviewed by Sam", and the panel rendered that sentence at y=1664 — the END of the scrolling
// form, 803px below the sticky Save button at y=828 — while focus fell to <body> (the button is
// natively disabled while it saves). The user saw no change at all. The Kanban board shows the
// same refusal in a banner at the top, so the product knew how; this surface did not.
//
// 🪤 `getByRole('alert')` PASSED THE WHOLE TIME. The alert existed; nobody could see it. So these
// assertions pin WHERE it is and WHO has focus, not merely that it rendered: it must sit inside the
// same sticky action bar as the Save button that produced it, directly above it, and it must hold
// focus. (jsdom has no layout; the real geometry — on screen, hit-testable, beside Save — is
// measured in the browser for the PR.)

const TASK: TaskItem = {
  id: 't-draft',
  title: 'Draft landing page copy',
  status: 'blocked',
  priority: 'high',
  task_list_id: 'tl-launch',
  exit_criteria: [{ description: 'Copy reviewed by Sam', status: 'incomplete', comment: '', met: false }],
}
const PROJECTS: ProjectItem[] = [
  { id: 'p-personal', name: 'Personal', is_builtin: true, status: 'active' },
  { id: 'p-q4', name: 'Q4 Launch Plan', status: 'active' },
]
const REFUSAL = 'cannot complete: unfinished exit criteria — Copy reviewed by Sam'

let updateTask: ReturnType<typeof vi.spyOn>
let onSaved: ReturnType<typeof vi.fn<(t: TaskItem) => void>>

beforeEach(() => {
  resetDataStore()
  vi.spyOn(api, 'projects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'taskLists').mockResolvedValue([{ id: 'tl-launch', name: 'Launch', project_id: 'p-q4' }])
  vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' })
  vi.spyOn(api, 'taskComments').mockResolvedValue([])
  updateTask = vi.spyOn(api, 'updateTask').mockRejectedValue(new ApiError(REFUSAL, 400))
  onSaved = vi.fn<(t: TaskItem) => void>()
})

afterEach(() => {
  vi.restoreAllMocks()
  resetDataStore()
  sessionStorage.clear()
})

async function refuseASave() {
  render(<TaskDetail task={TASK} editing onEditingChange={() => {}} onSaved={onSaved} onDeleted={() => {}} />)
  await userEvent.click(within(screen.getByRole('radiogroup', { name: 'Status' })).getByRole('radio', { name: 'Completed' }))
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(updateTask).toHaveBeenCalled())
  return screen.findByRole('alert')
}

describe('a refused task save', () => {
  it('says the save did not happen, and names what blocked it', async () => {
    const alert = await refuseASave()
    expect(alert.textContent).toMatch(/Couldn.t save this task/)
    expect(alert.textContent).toContain('Copy reviewed by Sam')
    expect(onSaved, 'nothing was saved').not.toHaveBeenCalled()
  })

  it('renders in the sticky action bar, directly above the Save button that produced it', async () => {
    const alert = await refuseASave()
    const save = screen.getByRole('button', { name: 'Save' })
    const bar = save.closest('[data-form-footer]')
    expect(bar, 'Save lives in the sticky footer').not.toBeNull()
    expect(bar, 'the refusal is beside the action, not at the end of the form').toContainElement(alert)
    expect(alert.compareDocumentPosition(save) & Node.DOCUMENT_POSITION_FOLLOWING, 'it precedes Save').toBeTruthy()
  })

  it('takes focus, so a keyboard user lands on it instead of on <body>', async () => {
    const alert = await refuseASave()
    await waitFor(() => expect(document.activeElement).toBe(alert))
  })

  it('keeps the form open with the user’s choice intact, ready to fix and retry', async () => {
    await refuseASave()
    const completed = within(screen.getByRole('radiogroup', { name: 'Status' })).getByRole('radio', { name: 'Completed' })
    expect(completed).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy()
  })
})
