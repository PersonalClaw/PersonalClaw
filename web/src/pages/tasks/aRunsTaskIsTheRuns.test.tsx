import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react'
import { TasksListPage } from './TasksListPage'
import { TaskDetail } from './TaskDetail'
import type { TaskItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data'

// ── A task a workflow run files for its own step reads as the run's, not the owner's ─────────────
//
// A morning digest's run filed "Collect, filter, propose and deliver" in the owner's Tasks, where it
// read "Created by noor" and counted as hers under "Mine". The run keeps its status — a save of it is
// refused — so it was neither hers to do nor hers to edit. Now the row names the run and links to it,
// "Mine" leaves it out, and its detail offers the run instead of an edit that cannot land.

const RUNS_TASK: TaskItem = {
  id: 't-run', title: 'Collect, filter, propose and deliver', status: 'done', priority: 'medium',
  // Stamped with the owner's name by a store that did not yet know better: the row must not repeat it.
  author: 'noor',
  workflow_binding: { run_id: '8e14e1a8', node_id: 'triage', managed: true },
}
const HER_TASK: TaskItem = {
  id: 't-mine', title: 'Book the physio appointment', status: 'open', priority: 'medium', author: 'noor',
}
const TASKS = [RUNS_TASK, HER_TASK]

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      tasks: vi.fn(async () => ({ tasks: TASKS, owner: 'noor' })),
      allTasks: vi.fn(async () => ({ tasks: TASKS, total: TASKS.length, complete: true, owner: 'noor' })),
      projects: vi.fn(async () => []),
      taskLists: vi.fn(async () => []),
      readyTasks: vi.fn(async () => []),
      uLoops: vi.fn(async () => []),
      taskComments: vi.fn(async () => []),
    },
  }
})
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const noop = () => {}

function Page() {
  return (
    <TasksListPage
      onCreate={noop} view="list" filter="all" openId="" setView={noop} setFilter={noop}
      setOpenId={noop} editing={false} setEditing={noop}
      q="" sort="" scope="" list="" tag=""
      setQ={noop} setSort={noop} setScope={noop} setList={noop} setTag={noop}
    />
  )
}

const rowOf = (title: string) => screen.getByText(title).closest('[data-task-row], li, [role="listitem"], .group') as HTMLElement

beforeEach(() => {
  resetDataStore(); localStorage.clear(); sessionStorage.clear()
})
afterEach(cleanup)

describe('the Tasks list', () => {
  it("names the run a run's task comes from, and links to it", async () => {
    render(<Page />)
    await waitFor(() => expect(screen.getByText(RUNS_TASK.title)).toBeTruthy())
    const link = screen.getByText('From a workflow run').closest('a') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('#/workflows/runs/8e14e1a8')
    // Not "Created by noor": the run made it.
    const runRow = rowOf(RUNS_TASK.title)
    expect(within(runRow).queryByTitle('Created by noor')).toBeNull()
    // Her own task still says she made it.
    expect(within(rowOf(HER_TASK.title)).getByTitle('Created by noor')).toBeTruthy()
  })

  it('leaves it out of "Mine"', async () => {
    render(<Page />)
    await waitFor(() => expect(screen.getByText(RUNS_TASK.title)).toBeTruthy())
    fireEvent.click(screen.getByLabelText('Filter & sort'))
    const section = screen.getByText('Assigned').parentElement!.parentElement as HTMLElement
    fireEvent.click(within(section).getByText('Mine'))
    await waitFor(() => expect(screen.queryByText(RUNS_TASK.title)).toBeNull())
    expect(screen.getByText(HER_TASK.title)).toBeTruthy()
  })
})

describe("a run's task, opened", () => {
  it('offers its run, not an edit the gateway refuses', () => {
    render(<TaskDetail task={RUNS_TASK} onSaved={noop} onDeleted={noop} editing={false} onEditingChange={noop} />)
    expect(screen.getByText("A workflow run keeps this task's status")).toBeTruthy()
    expect((screen.getByText('Open the run').closest('a') as HTMLAnchorElement).getAttribute('href'))
      .toBe('#/workflows/runs/8e14e1a8')
    expect(screen.queryByRole('button', { name: /Edit/ })).toBeNull()
  })

  it('never opens the edit form, even asked to', () => {
    render(<TaskDetail task={RUNS_TASK} onSaved={noop} onDeleted={noop} editing onEditingChange={noop} />)
    expect(screen.queryByRole('button', { name: /Save/ })).toBeNull()
  })

  it('her own task is still hers to edit', () => {
    render(<TaskDetail task={HER_TASK} onSaved={noop} onDeleted={noop} editing={false} onEditingChange={noop} />)
    expect(screen.getByRole('button', { name: /Edit/ })).toBeTruthy()
    expect(screen.queryByText('Open the run')).toBeNull()
  })
})
