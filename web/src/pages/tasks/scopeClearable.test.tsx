import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react'
import { useState } from 'react'
import { TasksListPage } from './TasksListPage'
import { resetDataStore } from '../../lib/data'

// ── Clear can express "cleared" (#476) ──────────────────────────────────────────────────────────
//
// The Scope filter is remembered in `localStorage`, and its Clear could never actually clear it.
// Three correct-in-isolation behaviours composed into a trap:
//
//   1. "All tasks" is the EMPTY STRING            `const SCOPE_ALL = ''`
//   2. a falsy scope DROPS the URL param           TasksSection: `setQuery({ scope: v || null })`
//   3. a missing param falls back to the STORE     `scopeProp || localStorage.getItem(SCOPE_KEY)`
//
// So Clear wrote nothing anywhere, and the stale stored scope won again immediately. Measured on the
// validation home: `localStorage['tasks-scope'] === "__goals__"` with `location.hash === '#/tasks'`
// and zero rows rendered, while the filter panel simultaneously reported **27 tasks** under "All
// tasks" and kept its `1` active badge. Unrecoverable through the Clear control itself — the only
// escapes were picking "All tasks" explicitly or clearing site data.
//
// 🪤 The trap is worse than the report's wording suggests, and this is the part worth pinning: the
// stale scope resurrects on the SAME render, not merely on the next bare visit, because the fallback
// at (3) re-reads the store the moment (2) drops the param. So a fix that only cleaned up on unmount
// or on navigation would still leave Clear a visible no-op.
//
// The fix: the URL cannot represent the cleared state, so the STORE must. Every scope choice writes
// through synchronously, before the re-render reads it back.

const TASKS = [
  { id: 't-1', title: 'Deliver Ramirez wedding gallery', status: 'open', priority: 'medium', project: 'Client work' },
  { id: 't-2', title: 'Order Ramirez album print proof', status: 'open', priority: 'medium', project: 'Client work' },
]

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      // `api.tasks()` keeps its envelope (the page unwraps it); `projects`/`taskLists` are already
      // unwrapped by the client, so they must return ARRAYS here.
      tasks: vi.fn(async () => ({ tasks: TASKS, owner: '' })),
      projects: vi.fn(async () => [{ id: 'p-1', name: 'Client work', status: 'active' }]),
      taskLists: vi.fn(async () => []),
      readyTasks: vi.fn(async () => []),
      searchTasks: vi.fn(async () => ({ tasks: [] })),
      uLoops: vi.fn(async () => []),
      updateTask: vi.fn(async () => ({})),
    },
  }
})
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const noop = () => {}

/** The page under its real router contract: `setScope('')` drops `?scope`, and a bare route hands
 *  the page `''` back — which is exactly the half of the trap that lives outside this file. */
function Harness() {
  const [scope, setScope] = useState('')
  return (
    <TasksListPage
      onCreate={noop} view="list" filter="all" openId="" setView={noop} setFilter={noop}
      setOpenId={noop} editing={false} setEditing={noop}
      q="" sort="" scope={scope} list="" tag=""
      setQ={noop} setSort={noop} setScope={(v) => setScope(v || '')} setList={noop} setTag={noop}
    />
  )
}

/** The inline Clear belonging to one filter section (each section renders its own). */
const clearFor = (section: string) =>
  within(screen.getByText(section).parentElement as HTMLElement).getByText('Clear')

// `useQuery`'s cache is MODULE-level, so without the reset the `tasks` key survives from case to
// case and a later mount paints the previous case's list without ever fetching.
beforeEach(() => { resetDataStore(); localStorage.clear(); sessionStorage.clear() })
afterEach(cleanup)

describe('the Tasks scope filter can be cleared', () => {
  it('a stale stored scope with no tasks in it empties the page — the state being escaped', async () => {
    localStorage.setItem('tasks-scope', '__goals__')
    render(<Harness />)
    // No task carries the Goal Loops project, so the remembered scope narrows to nothing.
    await waitFor(() => expect(screen.getByText('No tasks match this scope.')).toBeTruthy())
    expect(screen.queryByText('Deliver Ramirez wedding gallery')).toBeNull()
  })

  it('Clear restores every task and does not resurrect the stale scope', async () => {
    localStorage.setItem('tasks-scope', '__goals__')
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('No tasks match this scope.')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('Filter & sort'))
    fireEvent.click(clearFor('Scope'))

    // Both tasks come back…
    await waitFor(() => expect(screen.getByText('Deliver Ramirez wedding gallery')).toBeTruthy())
    expect(screen.getByText('Order Ramirez album print proof')).toBeTruthy()
    // …and the store no longer holds a scope that would win on the next read.
    expect(localStorage.getItem('tasks-scope'), 'Clear must be able to say "cleared"').not.toBe('__goals__')
  })

  it('and it STAYS cleared across a fresh bare visit', async () => {
    // The report's step 3 — "navigate to a bare #/tasks (or reload)". This is the assertion that
    // fails if Clear only ever updated the URL, because a remount re-reads the store from scratch.
    localStorage.setItem('tasks-scope', '__goals__')
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('No tasks match this scope.')).toBeTruthy())
    fireEvent.click(screen.getByLabelText('Filter & sort'))
    fireEvent.click(clearFor('Scope'))
    await waitFor(() => expect(screen.getByText('Deliver Ramirez wedding gallery')).toBeTruthy())

    cleanup()
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Deliver Ramirez wedding gallery')).toBeTruthy())
    expect(screen.queryByText('No tasks match this scope.')).toBeNull()
  })

  it('picking a scope still persists it — the fix must not break remembering', async () => {
    // The counterpart case. Write-through is only correct if it writes the CHOSEN value too;
    // a fix that simply stopped persisting would pass the three assertions above and silently
    // drop the preference this store exists for.
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Deliver Ramirez wedding gallery')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('Filter & sort'))
    fireEvent.click(within(screen.getByText('Scope').parentElement!.parentElement as HTMLElement).getByText('Goals'))

    await waitFor(() => expect(localStorage.getItem('tasks-scope')).toBe('__goals__'))
  })
})
