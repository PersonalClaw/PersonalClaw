import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { api, type ProjectItem, type TaskItem, type TaskListItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { TaskForm, draftToPayload, emptyDraft, toDraft, type TaskDraft } from './TaskForm'

// Tasks › a task in Personal › Edit › Project: another project, one with no lists yet › Save answered
// 200, and the task lost its project — `project: ""`, `task_list_id: ""`. Edit passed the picker no
// way to report a project, so the save sent the cleared list alone, and the server reads a task's
// project off its list. Moving it only worked by first making a list in the target project.

const PROJECTS: ProjectItem[] = [
  { id: 'p-personal', name: 'Personal', is_builtin: true, status: 'active' },
  { id: 'p-garden', name: 'Garden', status: 'active' },
]
const LISTS: TaskListItem[] = [
  { id: 'tl-general', name: 'General', project_id: 'p-personal' },
  { id: 'tl-errands', name: 'Errands', project_id: 'p-personal' },
]

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
  vi.spyOn(api, 'projects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'taskLists').mockResolvedValue(LISTS)
  vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' })
})

afterEach(() => {
  vi.restoreAllMocks()
  resetDataStore()
  localStorage.clear()
})

/** The form under a real draft state, reporting every draft it produces. */
function Host({ initial, seen }: { initial: TaskDraft; seen: (d: TaskDraft) => void }) {
  const [draft, setDraft] = useState(initial)
  seen(draft)
  return <TaskForm draft={draft} onChange={setDraft} compact allTasks={[]} />
}

const project = () => screen.findByRole('combobox', { name: 'Project' }) as Promise<HTMLSelectElement>
const list = () => screen.getByRole('combobox', { name: 'Task list' }) as HTMLSelectElement

describe('moving a task to another project', () => {
  const TASK: TaskItem = { id: 't-7', title: 'Order seed trays', status: 'open', task_list_id: 'tl-errands' }

  it('from Edit, saves it in the project it was moved to', async () => {
    let last: TaskDraft = toDraft(TASK)
    render(<Host initial={toDraft(TASK)} seen={(d) => { last = d }} />)
    const select = await project()
    await waitFor(() => expect(select.value).toBe('p-personal'))
    await userEvent.selectOptions(select, 'p-garden')
    const sent = draftToPayload(last)
    // 🔴 Before: `task_list_id: ""` and no `project_id` — the task was saved in no project.
    expect(sent.task_list_id).toBe('')
    expect(sent.project_id, 'the project it was moved to is sent').toBe('p-garden')
  })

  it('from Edit, choosing no list keeps it in its project, as on a new task', async () => {
    let last: TaskDraft = toDraft(TASK)
    render(<Host initial={toDraft(TASK)} seen={(d) => { last = d }} />)
    const select = await project()
    await waitFor(() => expect(select.value).toBe('p-personal'))
    await userEvent.selectOptions(list(), '')
    const sent = draftToPayload(last)
    expect([sent.task_list_id, sent.project_id]).toEqual(['', 'p-personal'])
  })

  it('on a new task, a list picked before the move does not come back with it', async () => {
    let last: TaskDraft = emptyDraft()
    render(<Host initial={emptyDraft()} seen={(d) => { last = d }} />)
    const select = await project()
    // Settled on its starting project (a select shows its first option until then).
    await waitFor(() => expect(list().disabled).toBe(false))
    await userEvent.selectOptions(list(), 'tl-errands')
    await userEvent.selectOptions(select, 'p-garden')
    const sent = draftToPayload(last)
    // 🔴 Before: `task_list_id: "tl-errands"` — clearing the list and naming the project were two
    // changes spread from one draft, and the second put the old list back, in the old project.
    expect(sent.task_list_id).toBe('')
    expect(sent.project_id).toBe('p-garden')
  })
})

describe('opening a task to edit moves nothing', () => {
  it('a task in no project shows none, and saving it untouched keeps it there', async () => {
    const loose: TaskItem = { id: 't-9', title: 'A task made with no list', status: 'open', task_list_id: '' }
    let last: TaskDraft = toDraft(loose)
    render(<Host initial={toDraft(loose)} seen={(d) => { last = d }} />)
    const select = await project()
    // 🔴 Before: it showed "Personal (builtin)", a project the task was not in.
    await waitFor(() => expect(select.selectedOptions[0]?.textContent).toBe('(none)'))
    expect(draftToPayload(last).project_id).toBeUndefined()
  })

  it('a task in a list keeps its list when nothing is changed', async () => {
    const TASK: TaskItem = { id: 't-1', title: 'Errand', status: 'open', task_list_id: 'tl-errands' }
    let last: TaskDraft = toDraft(TASK)
    render(<Host initial={toDraft(TASK)} seen={(d) => { last = d }} />)
    await waitFor(async () => expect((await project()).value).toBe('p-personal'))
    const sent = draftToPayload(last)
    expect(sent.task_list_id).toBe('tl-errands')
    expect(sent.project_id).toBeUndefined()
  })
})
