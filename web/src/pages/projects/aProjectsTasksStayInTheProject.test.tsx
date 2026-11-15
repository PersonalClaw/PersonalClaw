import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { chat, loop, mountPage, navigate, PID, task, taskList } from '../../test/projectPageHarness'

// ── A project's tasks stay in the project (§5.7, §3.3 item 4) ───────────────────────────────────
//
// A task opens beside, on this page (`?open=task:<id>`), not on All tasks. New task starts in this
// project and stays here. A loop's steps are not listed, by `/work`'s rule: a code session's phase
// tasks (their list's `loop_id` names it); with two goal loops sharing one Sub-goals list
// (`loop_id: ""`), neither loop's sub-goals (`linked_task_ids`), while the owner's own task in that
// list is listed; a phase list moved in from another project's loop stays listed. With the thread
// read failed, every step is listed and Recent says so. With every task done the group still draws,
// "No open tasks." with New task and See all tasks; with no task at all there is no Tasks group.
// `?open=task:<id>` opens only one of this project's own tasks: another project's says it isn't here.

beforeEach(() => { resetDataStore(); localStorage.clear(); navigate.mockReset() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const tasksGroup = async () => (await screen.findByRole('heading', { level: 2, name: 'Tasks' })).closest('section')!
const listed = (group: HTMLElement) => within(group).queryAllByRole('listitem').map((li) => li.textContent ?? '')

const LISTS = [
  taskList('tl-own', 'Garden'),
  taskList('tl-phases', 'Fix the gate: phases', { loop_id: 'l-code' }),
  taskList('tl-sub', 'Sub-goals', { loop_id: '' }),
  taskList('tl-moved', 'Old phases', { loop_id: 'l-elsewhere' }),
]
const TASKS = [
  task('t-own', 'Order the soil', 'tl-own'),
  task('t-phase', 'Write the gate tests', 'tl-phases'),
  task('t-sub1', 'Pick the feeds', 'tl-sub'),
  task('t-sub2', 'Write the digest', 'tl-sub'),
  task('t-mine', 'Label the jars', 'tl-sub'),
  task('t-moved', 'Moved-in step', 'tl-moved'),
]
const LOOPS = [
  loop('l-code', 'Fix the gate', { kind: 'code' }),
  loop('l-goal1', 'Plan the beds', { linked_task_ids: ['t-sub1'] } as never),
  loop('l-goal2', 'Weekly digest', { linked_task_ids: ['t-sub2'] } as never),
]

describe("a project's Tasks", () => {
  it("lists the project's own tasks, leaving out the steps of the loops it lists", async () => {
    mountPage({ lists: LISTS, tasks: TASKS, loops: LOOPS })
    const group = await tasksGroup()
    await waitFor(() => expect(listed(group).some((t) => t.includes('Order the soil'))).toBe(true))
    const rows = listed(group).join('\n')
    expect(rows).toContain('Label the jars')
    expect(rows).toContain('Moved-in step')
    expect(rows, "a code session's phase task").not.toContain('Write the gate tests')
    expect(rows, "a goal loop's sub-goal").not.toContain('Pick the feeds')
    expect(rows, "another goal loop's sub-goal").not.toContain('Write the digest')
    expect(within(group).getByText('3 open')).toBeTruthy()
  })

  it('lists every step while the thread read has failed, and Recent says why', async () => {
    mountPage({ lists: LISTS, tasks: TASKS, sessions: new Error('down') })
    const group = await tasksGroup()
    await waitFor(() => expect(listed(group).join('\n')).toContain('Write the gate tests'))
    expect(listed(group).join('\n')).toContain('Pick the feeds')
    expect(await screen.findByText(/Couldn't load its chats and loops\./)).toBeTruthy()
  })

  it('opens a task beside, on this page, and New task starts in this project and stays here', async () => {
    const { address } = mountPage({ lists: LISTS, tasks: TASKS, loops: LOOPS })
    const group = await tasksGroup()
    await userEvent.click(await within(group).findByRole('button', { name: /^Task: Order the soil/ }))
    expect(address.query.open).toBe('task:t-own')
    await userEvent.click(within(group).getByRole('button', { name: 'New task' }))
    expect(address.query.open).toBe('task:new')
  })

  it('draws "No open tasks." with New task and See all tasks once every task is done', async () => {
    const { address, writes } = mountPage({ lists: [taskList('tl-own', 'Garden')], tasks: [task('t-done', 'Sow the seeds', 'tl-own', { status: 'done' })] })
    const group = await tasksGroup()
    expect(within(group).getByText('No open tasks.')).toBeTruthy()
    expect(within(group).getByRole('button', { name: 'New task' })).toBeTruthy()
    await userEvent.click(within(group).getByRole('button', { name: 'See all tasks' }))
    expect(address.query.tab).toBe('tasks')
    expect(writes.at(-1)?.opts?.replace, 'the Tasks view replaces the entry').toBe(true)
  })

  it("opens only this project's own task beside it, never another project's", async () => {
    // A task of another project's list (`tl-b` is not one of Garden's): its id in the address opens
    // no detail, so no Edit or Delete acts on it under Garden's header.
    const theirs = task('t-theirs', 'Fix the fence', 'tl-b')
    mountPage({ lists: LISTS, tasks: [...TASKS, theirs], loops: LOOPS, query: { open: 'task:t-theirs' } })
    expect(await screen.findByText("This task isn't in Garden.")).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Delete/ }), "another project's task draws no Delete here").toBeNull()
    expect(screen.queryByText('Fix the fence')).toBeNull()
    cleanup(); resetDataStore()
    // This project's own task opens, with its actions.
    mountPage({ lists: LISTS, tasks: [...TASKS, theirs], loops: LOOPS, query: { open: 'task:t-own' } })
    expect(await screen.findByRole('button', { name: /Delete/ })).toBeTruthy()
    expect(screen.queryByText("This task isn't in Garden.")).toBeNull()
    cleanup(); resetDataStore()
    // A task that exists nowhere says so.
    mountPage({ lists: LISTS, tasks: TASKS, loops: LOOPS, query: { open: 'task:t-nowhere' } })
    expect(await screen.findByText('This task no longer exists.')).toBeTruthy()
  })

  it('its Tasks view is kept as it was: Rename, and All projects back to the list', async () => {
    mountPage({ lists: LISTS, tasks: TASKS, loops: LOOPS, query: { tab: 'tasks' } })
    expect(await screen.findByRole('button', { name: 'Rename' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'All projects' }))
    expect(navigate).toHaveBeenCalledWith('projects')
  })

  it('draws no Tasks group for a project with no task at all', async () => {
    mountPage({ lists: [taskList('tl-own', 'Garden')], tasks: [], sessions: [chat('c-1', 'Seed ideas', PID)] })
    await screen.findByRole('heading', { level: 2, name: 'Recent' })
    expect(screen.queryByRole('heading', { level: 2, name: 'Tasks' })).toBeNull()
  })
})
