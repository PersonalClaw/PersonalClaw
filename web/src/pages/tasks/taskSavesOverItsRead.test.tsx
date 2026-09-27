import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { api, type ProjectItem, type TaskItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { TaskDetail } from './TaskDetail'

// ── A task is saved only over the copy the panel read, and a tick is one item's tick ───────────────
//
// The task panel's Save sends every field, each list replaced whole, built from the task as the panel
// read it. The agent's `task_update` ticking a criterion or appending a note in between was undone by
// that save without a word — and the inline ticks were the same write in miniature: ticking one
// criterion sent the WHOLE list from the panel's copy. The gateway now refuses a whole-task save from
// a stale copy (`409 stale_write`, `tasks/handlers.py`) and applies a tick to the list as stored
// (`PUT /api/tasks/{id} {tick: …}`); these pin the panel's half of both.

function staleWrite() {
  return Object.assign(new Error('This write replaces the task, which changed after the copy it was built from was read.'), { status: 409, code: 'stale_write' })
}

const PAINTED: TaskItem = {
  id: 't-1',
  title: 'Draft landing page copy',
  status: 'open',
  priority: 'high',
  task_list_id: 'tl-launch',
  exit_criteria: [
    { description: 'Copy reviewed by Sam', status: 'incomplete', comment: '', met: false },
    { description: 'Screenshots attached', status: 'incomplete', comment: '', met: false },
  ],
  revision: 't1',
}
// What is stored by the time the panel saves: the agent ticked the second criterion.
const STORED: TaskItem = {
  ...PAINTED,
  exit_criteria: [
    { description: 'Copy reviewed by Sam', status: 'incomplete', comment: '', met: false },
    { description: 'Screenshots attached', status: 'complete', comment: '', met: true },
  ],
  revision: 't2',
}
const PROJECTS: ProjectItem[] = [{ id: 'p-q4', name: 'Q4 Launch Plan', status: 'active' }]

let saveTask: ReturnType<typeof vi.spyOn>
let updateTask: ReturnType<typeof vi.spyOn>
let tickTaskItem: ReturnType<typeof vi.spyOn>
let onSaved: ReturnType<typeof vi.fn<(t: TaskItem) => void>>
let onEditingChange: ReturnType<typeof vi.fn<(v: boolean) => void>>

beforeEach(() => {
  resetDataStore()
  vi.spyOn(api, 'projects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'taskLists').mockResolvedValue([{ id: 'tl-launch', name: 'Launch', project_id: 'p-q4' }])
  vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' })
  vi.spyOn(api, 'taskComments').mockResolvedValue([])
  vi.spyOn(api, 'task').mockResolvedValue(STORED)
  saveTask = vi.spyOn(api, 'saveTask').mockImplementation((_id: string, body: Record<string, unknown>, base: string) =>
    base === 't2' ? Promise.resolve({ ...STORED, ...body, revision: 't3' } as TaskItem) : Promise.reject(staleWrite()))
  updateTask = vi.spyOn(api, 'updateTask')
  tickTaskItem = vi.spyOn(api, 'tickTaskItem')
  onSaved = vi.fn<(t: TaskItem) => void>()
  onEditingChange = vi.fn<(v: boolean) => void>()
})

afterEach(() => {
  vi.restoreAllMocks()
  resetDataStore()
  sessionStorage.clear()
})

async function editTitleAndSave() {
  render(<TaskDetail task={PAINTED} editing onEditingChange={onEditingChange} onSaved={onSaved} onDeleted={() => {}} />)
  fireEvent.change(await screen.findByPlaceholderText('What needs to happen?'), { target: { value: 'Draft the landing page copy' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
}

describe('the task editor', () => {
  it('a save from a stale copy is refused with the notice, and the draft is kept', async () => {
    await editTitleAndSave()
    const alert = await screen.findByText(/This task changed elsewhere/)
    expect(alert.closest('[role="alert"]')).toBeTruthy()
    expect((screen.getByPlaceholderText('What needs to happen?') as HTMLInputElement).value, 'the draft survives')
      .toBe('Draft the landing page copy')
    expect(onSaved).not.toHaveBeenCalled()
    expect(onEditingChange).not.toHaveBeenCalledWith(false)
    // The whole-task save names the revision the panel painted; the scalar door is not used for it.
    expect(saveTask).toHaveBeenCalledTimes(1)
    const [id, body, base] = saveTask.mock.calls[0]
    expect([id, base]).toEqual(['t-1', 't1'])
    expect(body).toMatchObject({ title: 'Draft the landing page copy' })
    expect(updateTask).not.toHaveBeenCalled()
  })

  it('Reload and reapply keeps the criterion the agent ticked and puts the rename on top', async () => {
    await editTitleAndSave()
    const alert = (await screen.findByText(/This task changed elsewhere/)).closest('[role="alert"]') as HTMLElement
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(saveTask).toHaveBeenCalledTimes(2))
    const [, body, base] = saveTask.mock.calls[1]
    expect(base).toBe('t2')
    expect(body).toMatchObject({ title: 'Draft the landing page copy' })
    expect((body as { exit_criteria: { met: boolean }[] }).exit_criteria.map((c) => c.met)).toEqual([false, true])
    // The list is patched with what the gateway answered, not with the page's guess.
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ revision: 't3' })))
  })

  it('a save from a current copy lands, names the painted revision, and hands on the answer', async () => {
    saveTask.mockImplementation((_id: string, body: Record<string, unknown>) =>
      Promise.resolve({ ...PAINTED, ...body, revision: 't9' } as TaskItem))
    await editTitleAndSave()
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ title: 'Draft the landing page copy', revision: 't9' })))
    expect(saveTask.mock.calls[0][2]).toBe('t1')
    expect(onEditingChange).toHaveBeenCalledWith(false)
    expect(screen.queryByText(/changed elsewhere/)).toBeNull()
  })
})

describe('an inline tick', () => {
  it('is ONE item\'s tick, named by its text and where the panel saw it — never the whole list', async () => {
    tickTaskItem.mockResolvedValue(STORED)
    render(<TaskDetail task={PAINTED} editing={false} onEditingChange={onEditingChange} onSaved={onSaved} onDeleted={() => {}} />)
    const ticks = await screen.findAllByRole('button', { name: 'Mark criterion complete' })
    await act(async () => { fireEvent.click(ticks[0]) })

    await waitFor(() => expect(tickTaskItem).toHaveBeenCalledTimes(1))
    expect(tickTaskItem.mock.calls[0]).toEqual(['t-1', 'exit_criteria', 0, 'Copy reviewed by Sam', true])
    expect(updateTask, 'no list is sent from the panel\'s copy').not.toHaveBeenCalled()
    expect(saveTask).not.toHaveBeenCalled()
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(STORED))
  })

  // 🔴 The refusal used to be the whole answer: the gateway's "Reload the task and tick it again" under a
  // checklist still showing the copy that went stale, and nothing on the panel that reloads it.
  const refusedTick = () => Object.assign(new Error("The exit criterion 'Copy reviewed by Sam' is no longer on the task as it was read — it was changed or removed since — so nothing was ticked. Reload the task and tick it again."), { status: 409, code: 'stale_write' })
  // Stored by the time of the tick: the criterion was reworded, and the agent ticked the other one.
  const REWORDED: TaskItem = {
    ...STORED,
    exit_criteria: [
      { description: 'Copy reviewed by Sam and Ana', status: 'incomplete', comment: '', met: false },
      { description: 'Screenshots attached', status: 'complete', comment: '', met: true },
    ],
  }
  /** The panel as the list mounts it: `onSaved` patches the row, and the row is the task it shows. */
  function Panel() {
    const [task, setTask] = useState(PAINTED)
    return <TaskDetail task={task} editing={false} onEditingChange={onEditingChange} onSaved={(t) => { onSaved(t); setTask(t) }} onDeleted={() => {}} />
  }

  it('a tick of an item that changed elsewhere ticks nothing, and the panel then shows the task as stored', async () => {
    const read = vi.spyOn(api, 'task').mockResolvedValue(REWORDED)
    tickTaskItem.mockRejectedValue(refusedTick())
    render(<Panel />)
    const ticks = await screen.findAllByRole('button', { name: 'Mark criterion complete' })
    await act(async () => { fireEvent.click(ticks[0]) })

    expect(await screen.findByText('“Copy reviewed by Sam” was changed or removed elsewhere, so nothing was ticked. This is the task as it is stored now.')).toBeTruthy()
    expect(screen.queryByText(/Reload the task/), 'the panel did the reload itself').toBeNull()
    expect(read).toHaveBeenCalledWith('t-1', undefined)
    expect(onSaved, 'the list row is patched with the task as stored').toHaveBeenCalledWith(REWORDED)
    expect(screen.getByText('Copy reviewed by Sam and Ana')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'Mark criterion incomplete' }), 'the tick made elsewhere shows').toHaveLength(1)
  })

  it('a refused tick whose task cannot be re-read says both, and patches nothing', async () => {
    vi.spyOn(api, 'task').mockRejectedValue(new Error('the gateway did not answer'))
    tickTaskItem.mockRejectedValue(refusedTick())
    render(<Panel />)
    const ticks = await screen.findAllByRole('button', { name: 'Mark criterion complete' })
    await act(async () => { fireEvent.click(ticks[0]) })

    expect(await screen.findByText(/^“Copy reviewed by Sam” was changed or removed elsewhere, so nothing was ticked, and the task as it is stored now couldn.t be read: the gateway did not answer$/)).toBeTruthy()
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('a tick that fails for another reason says the gateway\'s reason and re-reads nothing', async () => {
    const read = vi.spyOn(api, 'task')
    tickTaskItem.mockRejectedValue(Object.assign(new Error('Tasks managed by a project are read-only here.'), { status: 403 }))
    render(<Panel />)
    const ticks = await screen.findAllByRole('button', { name: 'Mark criterion complete' })
    await act(async () => { fireEvent.click(ticks[0]) })

    expect(await screen.findByText('Tasks managed by a project are read-only here.')).toBeTruthy()
    expect(read).not.toHaveBeenCalled()
    expect(onSaved).not.toHaveBeenCalled()
  })
})
