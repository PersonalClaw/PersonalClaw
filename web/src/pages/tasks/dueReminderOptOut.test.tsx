/** A task's due-date reminder can be turned off, per task, from the form.
 *
 *  The server announces a due date the day before (`tasks/due_notices.py`) unless the task's
 *  `due_reminder` is off. The form is where a user decides that a date is a soft target rather
 *  than a deadline, so the choice lives next to the Due field and travels with the save.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { api, type TaskItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { TaskForm, draftToPayload, emptyDraft, toDraft, type TaskDraft } from './TaskForm'
import { TaskDetail } from './TaskDetail'
import { notificationLink } from '../notifications/notificationMeta'

const REMIND = /Remind me the day before/

beforeEach(() => {
  resetDataStore()
  vi.spyOn(api, 'projects').mockResolvedValue([])
  vi.spyOn(api, 'taskLists').mockResolvedValue([])
  vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' })
})
afterEach(() => { vi.restoreAllMocks(); resetDataStore() })

function Host({ initial, seen }: { initial: TaskDraft; seen: (d: TaskDraft) => void }) {
  const [draft, setDraft] = useState(initial)
  seen(draft)
  return <TaskForm draft={draft} onChange={setDraft} compact allTasks={[]} />
}

const DUE: TaskItem = { id: 't-1', title: 'Ship the release notes', status: 'open', due: '2026-10-02' }

describe('the due-date reminder', () => {
  it('🔑 is on by default, and unticking it is what the save sends', async () => {
    let last: TaskDraft = toDraft(DUE)
    render(<Host initial={toDraft(DUE)} seen={(d) => { last = d }} />)
    const box = await screen.findByRole('checkbox', { name: REMIND })
    expect((box as HTMLInputElement).checked).toBe(true)
    expect(draftToPayload(last).due_reminder).toBe(true)

    await userEvent.click(box)
    expect((box as HTMLInputElement).checked).toBe(false)
    expect(draftToPayload(last).due_reminder).toBe(false)
  })

  it('a task that opted out opens with the box unticked', async () => {
    render(<Host initial={toDraft({ ...DUE, due_reminder: false })} seen={() => {}} />)
    const box = await screen.findByRole('checkbox', { name: REMIND })
    expect((box as HTMLInputElement).checked).toBe(false)
  })

  it('is not offered until there is a date to be reminded of', async () => {
    render(<Host initial={emptyDraft()} seen={() => {}} />)
    await screen.findByRole('textbox', { name: 'Title' })
    expect(screen.queryByRole('checkbox', { name: REMIND })).toBeNull()
    expect(draftToPayload(emptyDraft()).due_reminder, 'a new task is reminded unless told otherwise').toBe(true)
  })

  it('an opted-out task says so beside its due date, so the choice is visible without editing', () => {
    const noop = () => {}
    const { rerender } = render(<TaskDetail task={DUE} onSaved={noop} onDeleted={noop} editing={false} onEditingChange={noop} />)
    expect(screen.queryByText(/no reminder/)).toBeNull()
    rerender(<TaskDetail task={{ ...DUE, due_reminder: false }} onSaved={noop} onDeleted={noop} editing={false} onEditingChange={noop} />)
    expect(screen.getByText(/no reminder/)).toBeTruthy()
  })

  it('the notice opens its task: the server links it as `#/tasks?open=<id>`', () => {
    expect(notificationLink({ statusUrl: '#/tasks?open=t-1' })).toEqual({ label: 'Open task', path: 'tasks?open=t-1' })
  })
})
