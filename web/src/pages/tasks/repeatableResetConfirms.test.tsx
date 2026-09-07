import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A Repeatable list's Reset destroyed every task's execution notes on one click ──────────────────
//
// `POST /api/task-lists/{id}/reset` says what it does in its own docstring — *"all its tasks → open,
// exit criteria → incomplete, execution notes cleared"* — and `tasks/hierarchy_handlers.py` loops the
// whole list passing `execution_notes=[]`. Nothing restores them.
//
// The control was a 20px `RotateCcw` beside a list chip, with **no confirmation**, and the only copy it
// carried was its tooltip: *"Reset this repeatable list (all tasks must be done)"* — which names the
// PRECONDITION and never the loss. It reads as "start the checklist again", not "discard the record of
// what happened last run".
//
// 🪤 THE OBVIOUS IMPROVEMENT WOULD HAVE LIED, and that is the most useful thing in this file. Naming
// how many tasks lose notes is the informative version — and it is unavailable here honestly. The
// page's `tasks` comes from `api.tasks()` with no `limit`, and the server defaults that to **50**
// (`tasks/handlers.py:25`) across the WHOLE ACCOUNT rather than per list. So a client-side count
// undercounts on exactly the large lists where the loss is biggest, and an undercounting warning is
// worse than one that does not count. The dialog states the consequence categorically instead — true
// for every list at every size — and the rail below pins that no count sneaks back in.

const resetMock = vi.fn(async (..._a: unknown[]): Promise<unknown> => ({ ok: true, reset_task_ids: [] }))

const PROJECT = { id: 'p-rep', name: 'Repeatable', status: 'active' }
const LIST = { id: 'tl-1', name: 'Morning checklist', project_id: 'p-rep' }
const TASK = {
  id: 't-1', title: 'Water the plants', status: 'done', priority: 'medium',
  task_list_id: 'tl-1', execution_notes: [{ at: 1, text: 'did it' }],
}

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      // 🪤 MOCK THE CLIENT METHOD'S CONTRACT, NOT THE WIRE ENVELOPE. `api.projects()` and
      // `api.taskLists()` already unwrap (`.then(d => d.projects)`), so returning `{ projects: [...] }`
      // here hands the page an OBJECT where it expects an array — `.find` throws, the loader's own
      // `.catch` empties both lists, and the list bar silently never mounts. `api.tasks()` is the
      // exception: the page unwraps that one itself, so it keeps its envelope.
      tasks: vi.fn(async () => ({ tasks: [TASK], owner: 'me' })),
      projects: vi.fn(async () => [PROJECT]),
      taskLists: vi.fn(async () => [LIST]),
      readyTasks: vi.fn(async () => []),
      searchTasks: vi.fn(async () => ({ results: [] })),
      uLoops: vi.fn(async () => []),
      tasksBulk: vi.fn(async () => ({ ok: true })),
      updateTask: vi.fn(async () => ({})),
      resetTaskList: (...a: unknown[]) => resetMock(...a),
    },
  }
})
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

import { TasksListPage } from './TasksListPage'
import { DialogHost } from '../../ui/dialog/DialogHost'

const noop = () => {}
function mount() {
  return render(
    <>
      <TasksListPage
        onCreate={noop} view="list" filter="all" openId="" setView={noop} setFilter={noop}
        setOpenId={noop} editing={false} setEditing={noop}
        // 🪤 `scope` is the project NAME, not an id: `scopedProject = projects.find(p => p.name === scope)`,
        // and the list bar renders only under `isProjectScope && projectLists.length > 0`. With an empty
        // scope the bar — and so the Reset control — never mounts at all.
        q="" sort="" scope="Repeatable" list="" setQ={noop} setSort={noop} setScope={noop} setList={noop}
      />
      <DialogHost />
    </>,
  )
}

/** The list chip's Reset control — named for the list, per this page's own naming contract. */
const findReset = () => waitFor(() => screen.getByRole('button', { name: `Reset list ${LIST.name}` }))

beforeEach(() => { resetMock.mockClear(); sessionStorage.clear() })
afterEach(() => cleanup())

describe('resetting a Repeatable list asks first', () => {
  it('a declined confirm destroys NOTHING', async () => {
    mount()
    fireEvent.click(await findReset())
    // `danger: true`, so `DialogShell` renders an alertdialog rather than a dialog.
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    fireEvent.click(within(dlg).getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(resetMock, 'declining must not reach the API').not.toHaveBeenCalled()
  })

  it('🔑 the dialog names the list AND what is lost, not the precondition', async () => {
    mount()
    fireEvent.click(await findReset())
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    expect(within(dlg).getByText(new RegExp(`Reset .${LIST.name}`))).toBeInTheDocument()
    // The consequence, in the dialog rather than in a tooltip.
    expect(within(dlg).getByText(/execution notes are cleared/i)).toBeInTheDocument()
    expect(within(dlg).getByText(/cannot be recovered/i)).toBeInTheDocument()
    // 🪤 And NOT the precondition, which is what the old tooltip said instead of the loss. A dialog
    // that repeats "all tasks must be done" would be describing when the button works, not what it does.
    expect(within(dlg).queryByText(/must be done/i)).toBeNull()
    // Resolve it, so the module-level dialog store is clean for the next test.
    fireEvent.click(within(dlg).getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
  })

  it('a confirmed reset calls the API exactly once, with the list id', async () => {
    mount()
    fireEvent.click(await findReset())
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    fireEvent.click(within(dlg).getByRole('button', { name: /reset list/i }))
    await waitFor(() => expect(resetMock).toHaveBeenCalledTimes(1))
    expect(resetMock).toHaveBeenCalledWith(LIST.id)
  })
})

describe('the gate is real, and the copy makes no promise it cannot keep', () => {
  const code = readFileSync(join(process.cwd(), 'src/pages/tasks/TasksListPage.tsx'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')
  const body = code.match(/async function resetList\(list: TaskListItem\)[\s\S]*?\n  \}/)?.[0] ?? ''

  it('confirm precedes the destroy', () => {
    expect(body, 'found resetList').not.toBe('')
    expect(body).toMatch(/if \(!\(await confirm\(\{/)
    // Ordering is the whole gate: a confirm placed after the call would change nothing.
    expect(body.indexOf('await confirm('), 'the confirm comes first')
      .toBeLessThan(body.indexOf('api.resetTaskList'))
  })

  it('it is danger-toned, so the dialog is an alertdialog', () => {
    expect(body).toMatch(/danger: true/)
  })

  it('🪤 the body states the consequence CATEGORICALLY — no task count', () => {
    // The load-bearing decision. `tasks` here is capped at the server's default limit of 50 across the
    // whole account, so any count interpolated into this dialog would understate the loss on a large
    // list. If someone later "improves" the copy with a number, this reds — and the comment above
    // resetList says why. Re-argue it against `tasks/handlers.py`'s default before changing it.
    const dialogArgs = body.match(/await confirm\(\{[\s\S]*?\}\)/)?.[0] ?? ''
    expect(dialogArgs, 'found the confirm options').not.toBe('')
    expect(dialogArgs, 'no count is interpolated').not.toMatch(/\.length/)
    expect(dialogArgs, 'and it speaks for every task').toMatch(/Every task/)
  })

  it('VACUITY: the server really does clear the notes this dialog warns about', () => {
    // Guards the WARNING's truth, not the dialog. If the backend stopped clearing notes, this copy
    // would be scaring people about a loss that no longer happens — the mirror defect.
    const py = readFileSync(
      join(process.cwd(), '..', 'src/personalclaw/tasks/hierarchy_handlers.py'), 'utf8',
    )
    // 🪤 Bounded to the NEXT top-level def, not to the next blank line. A `[\s\S]*?\n\n` window stops
    // at the first blank line inside the docstring, which cuts the function off well before the loop
    // that does the clearing — so the assertion would red against a handler that is perfectly correct.
    const handler = py.match(/async def api_task_lists_reset[\s\S]*?(?=\nasync def |\ndef |$)/)?.[0] ?? ''
    expect(handler, 'found the reset handler').not.toBe('')
    expect(handler, 'and the window really reached the loop body').toMatch(/for t in tasks:/)
    expect(handler, 'it still clears execution notes for every task').toMatch(/execution_notes=\[\]/)
  })
})
