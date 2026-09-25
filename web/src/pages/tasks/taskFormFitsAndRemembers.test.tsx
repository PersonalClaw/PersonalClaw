import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { api, type ProjectItem, type TaskItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { TaskForm, draftToPayload, emptyDraft, toDraft, type TaskDraft } from './TaskForm'

// Three defects in the one form behind both the create page and the edit panel, each measured
// live against the gateway before it was fixed.

const PROJECTS: ProjectItem[] = [
  { id: 'p-personal', name: 'Personal', is_builtin: true, status: 'active' },
  { id: 'p-q4', name: 'Q4 Launch Plan', status: 'active' },
  { id: 'p-other', name: 'Other', status: 'active' },
]

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
  sessionStorage.clear()
  vi.spyOn(api, 'projects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'taskLists').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
  resetDataStore()
  localStorage.clear()
  sessionStorage.clear()
})

/** The form under a real draft state, reporting every draft it produces. */
function Host({ initial, seen }: { initial: TaskDraft; seen: (d: TaskDraft) => void }) {
  const [draft, setDraft] = useState(initial)
  seen(draft)
  return <TaskForm draft={draft} onChange={setDraft} compact allTasks={[]} />
}

describe('the default project follows the user, not the browser', () => {
  // MEASURED across two browsers on one gateway: a project made the default in the first read
  // "Make default" in the second, and "New task" there opened on Personal — the default was a
  // localStorage key while the project page called it "Default project". It is an account
  // setting now (`GET /api/projects/settings`), so every browser starts a new task in it.
  it('a new task starts in the server-stored default project', async () => {
    vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: 'p-q4' })
    let last: TaskDraft = emptyDraft()
    render(<Host initial={emptyDraft()} seen={(d) => { last = d }} />)
    const project = await screen.findByRole('combobox', { name: 'Project' })
    await waitFor(() => expect((project as HTMLSelectElement).value).toBe('p-q4'))
    expect(last.project_id, 'the draft carries it, so the task is created there').toBe('p-q4')
  })

  it('the retired per-browser pointer decides nothing', async () => {
    localStorage.setItem('active-project', 'p-other')
    vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' })
    render(<Host initial={emptyDraft()} seen={() => {}} />)
    const project = await screen.findByRole('combobox', { name: 'Project' })
    await waitFor(() => expect((project as HTMLSelectElement).value).toBe('p-personal'))
  })

  it('a failed read falls back to Personal AND says so, rather than passing it off as the choice', async () => {
    vi.spyOn(api, 'projectSettings').mockRejectedValue(new Error('offline'))
    render(<Host initial={emptyDraft()} seen={() => {}} />)
    const project = await screen.findByRole('combobox', { name: 'Project' })
    await waitFor(() => expect((project as HTMLSelectElement).value).toBe('p-personal'))
    expect(await screen.findByText(/Couldn.t load your default project/)).toBeTruthy()
  })
})

describe('a criterion ticked in the edit form is saved as ticked', () => {
  // MEASURED: the server stores `{description, status, comment, met}` and reads `status` when both
  // are present; the form's checklist flipped `met` only. So a loaded criterion ticked in the edit
  // form went back as `{status: 'incomplete', met: true}` and was stored incomplete — the form
  // showed it ticked and Save answered 200 while the task still read 0/1.
  const TASK: TaskItem = {
    id: 't-1', title: 'Draft landing page copy', status: 'blocked',
    exit_criteria: [{ description: 'Copy reviewed by Sam', status: 'incomplete', comment: '', met: false }],
  }

  it('the payload carries the tick in a shape the server reads as complete', async () => {
    vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' })
    let last: TaskDraft = toDraft(TASK)
    render(<Host initial={toDraft(TASK)} seen={(d) => { last = d }} />)
    await userEvent.click(screen.getByRole('button', { name: 'Mark done: Copy reviewed by Sam' }))
    const [criterion] = draftToPayload(last).exit_criteria as Record<string, unknown>[]
    expect(criterion.met).toBe(true)
    // The stale `status: 'incomplete'` is what the server honoured over `met`.
    expect(criterion.status).toBeUndefined()
  })

  it('and a criterion completed by status loads ticked', () => {
    const draft = toDraft({ ...TASK, exit_criteria: [{ description: 'Done by status', status: 'complete' }] })
    expect(draft.exit_criteria?.[0]).toEqual({ description: 'Done by status', met: true })
  })
})

describe('Status and Priority fit a narrow panel', () => {
  // MEASURED in the 420px task panel: the six icon+label statuses measured 649px, so Completed was
  // cut off, Cancelled and Skipped sat outside the panel, and choosing one scrolled the whole form
  // sideways (38px by click, 245px by arrow key). Both strips now wrap; the geometry is measured in
  // the browser, and what jsdom can pin is that they wrap AND are still single radiogroups.
  it('both strips wrap and remain one radiogroup each, with the arrow-key contract', async () => {
    vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' })
    let last: TaskDraft = emptyDraft()
    render(<Host initial={emptyDraft()} seen={(d) => { last = d }} />)
    for (const name of ['Status', 'Priority']) {
      const group = screen.getByRole('radiogroup', { name })
      expect(group.className, `${name} wraps`).toMatch(/\bflex-wrap\b/)
      expect(group.className, `${name} is capped at its container`).toMatch(/\bmax-w-full\b/)
      expect(screen.getAllByRole('radiogroup', { name }), `one ${name} group, no probe copy`).toHaveLength(1)
    }
    const status = screen.getByRole('radiogroup', { name: 'Status' })
    await userEvent.click(within(status).getByRole('radio', { name: 'Blocked' }))
    await userEvent.keyboard('{ArrowRight}')
    expect(last.status, 'an arrow key still moves AND selects').toBe('done')
  })
})
