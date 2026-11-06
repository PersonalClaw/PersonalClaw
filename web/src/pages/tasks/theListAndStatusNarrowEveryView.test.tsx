import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within, fireEvent } from '@testing-library/react'
import { findHeaderFilter } from '../../test/headerFilter'

// ── A project's List section and Status narrow every view, and say so ───────────────────────────
//
// - The List section showed on Board and DAG, which drew every list's tasks (`scopedTasks` skipped
//   it). It narrows them as the tag does now, with a list case in their empty state.
// - Status narrows a search's matches, so a search the Status emptied blamed the search: it names
//   Status now, with Any status as its way out.
// - A `?list` left from another project showed its raw id in the filter's label. It is dropped once
//   the catalog has loaded, and a list the project holds is kept.
// - Ready under a query listed every match under the Ready label while the ready read was out, and
//   hid its failure. It waits for that read, and shows its error whatever the query. Board and the
//   graph draw their own tasks, so that read neither holds them nor stands in for them.
// - Assigned › Mine narrowed only the list and the cards, while Board and the graph drew every
//   assignee's tasks under a label naming Mine. It narrows them too, with Everyone as the way out.

const PROJECT = { id: 'p-1', name: 'Garden', status: 'active' }
const LISTS = [
  { id: 'tl-beds', name: 'Beds', project_id: 'p-1' },
  { id: 'tl-shed', name: 'Shed', project_id: 'p-1' },
]
const ON_BEDS = { id: 't-1', title: 'Weed the beds', status: 'open', priority: 'medium', project: 'Garden', task_list_id: 'tl-beds' }
const ON_SHED = { id: 't-2', title: 'Oil the shears', status: 'open', priority: 'medium', project: 'Garden', task_list_id: 'tl-shed' }

const ready = vi.fn(async (): Promise<unknown[]> => [])
const all = vi.fn(async (): Promise<unknown> => ({ tasks: [ON_BEDS, ON_SHED], total: 2, complete: true, owner: '' }))
const search = vi.fn(async (): Promise<unknown> => ({ tasks: [ON_BEDS, ON_SHED], total: 2 }))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      allTasks: () => all(),
      projects: vi.fn(async () => [PROJECT]),
      taskLists: vi.fn(async () => LISTS),
      readyTasks: () => ready(),
      searchTasks: () => search(),
      uLoops: vi.fn(async () => []),
      tasksBulk: vi.fn(async () => ({ ok: true })),
      updateTask: vi.fn(async () => ({})),
    },
  }
})
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

import { TasksListPage } from './TasksListPage'

const noop = () => {}
function mount(over: Partial<{ view: string; filter: string; q: string; list: string; tag: string; setList: (v: string) => void }>) {
  return render(
    <TasksListPage
      onCreate={noop} view={over.view ?? 'list'} filter={over.filter ?? 'all'} openId="" setView={noop} setFilter={noop}
      setOpenId={noop} editing={false} setEditing={noop}
      q={over.q ?? ''} sort="" scope="Garden" list={over.list ?? ''} tag={over.tag ?? ''} setQ={noop} setSort={noop} setScope={noop}
      setList={over.setList ?? noop} setTag={noop}
    />,
  )
}

beforeEach(() => {
  ready.mockClear(); search.mockClear(); all.mockClear()
  ready.mockImplementation(async () => [])
  search.mockImplementation(async () => ({ tasks: [ON_BEDS, ON_SHED], total: 2 }))
  all.mockImplementation(async () => ({ tasks: [ON_BEDS, ON_SHED], total: 2, complete: true, owner: '' }))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('the List section narrows the board and the graph', () => {
  for (const view of ['board', 'dag']) {
    it(`${view}: only the chosen list's tasks`, async () => {
      mount({ view, list: 'tl-beds' })
      expect(await screen.findByText('Weed the beds')).toBeTruthy()
      expect(screen.queryByText('Oil the shears'), "the Shed list's task").toBeNull()
    })
  }

  it('a list with none of the tasks says so, with All lists as its way out', async () => {
    const empty = { ...LISTS[1], id: 'tl-empty', name: 'Tools' }
    LISTS.push(empty)
    try {
      mount({ view: 'board', list: 'tl-empty' })
      expect(await screen.findByText('Nothing in Tools')).toBeTruthy()
      expect(screen.getByRole('button', { name: 'All lists' })).toBeTruthy()
    } finally { LISTS.pop() }
  })
})

describe('a search the Status emptied names Status', () => {
  it('blames Status, not the search, and offers Any status', async () => {
    mount({ filter: 'blocked', q: 'the' })
    expect(await screen.findByText('No tasks match “the” with Status Blocked', {}, { timeout: 3000 })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Any status' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Clear search' })).toBeNull()
  })
})

describe("a ?list the project doesn't hold", () => {
  it('is dropped once the catalog has loaded', async () => {
    const setList = vi.fn()
    mount({ list: 'tl-elsewhere', setList })
    await waitFor(() => expect(setList).toHaveBeenCalledWith(''))
  })

  it("one of the project's lists is kept", async () => {
    const setList = vi.fn()
    mount({ list: 'tl-shed', setList })
    expect(await screen.findByText('Oil the shears')).toBeTruthy()
    expect(setList).not.toHaveBeenCalled()
  })
})

describe('Ready under a query', () => {
  it('lists no match under the Ready label while the ready read is out', async () => {
    ready.mockImplementation(() => new Promise(() => {}))
    mount({ filter: 'ready', q: 'the' })
    await waitFor(() => expect(search).toHaveBeenCalled(), { timeout: 3000 })
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByText('Weed the beds'), 'not every match, before Ready is known').toBeNull()
  })

  it('shows the ready read failing, whatever the query', async () => {
    ready.mockImplementation(async () => { throw new Error('ready read failed') })
    mount({ filter: 'ready', q: 'the' })
    expect(await screen.findByText(/ready tasks/i, {}, { timeout: 3000 })).toBeTruthy()
    expect(screen.queryByText('Weed the beds')).toBeNull()
  })

  // Board and the graph draw the scoped tasks, not the search's results or the ready read, so a
  // ready read or a search that fails neither holds them on the skeleton nor stands in for them.
  for (const view of ['board', 'dag']) {
    for (const q of ['the', '']) {
      it(`${view}: draws its tasks when the ready read fails${q ? ' under a query' : ''}`, async () => {
        ready.mockImplementation(async () => { throw new Error('ready read failed') })
        mount({ view, filter: 'ready', q })
        expect(await screen.findByText('Weed the beds', {}, { timeout: 3000 })).toBeTruthy()
        expect(screen.queryByText(/ready tasks/i), 'no ready error in its place').toBeNull()
      })
    }

    it(`${view}: draws its tasks when the search fails`, async () => {
      search.mockImplementation(async () => { throw new Error('search failed') })
      mount({ view, q: 'the' })
      expect(await screen.findByText('Weed the beds', {}, { timeout: 3000 })).toBeTruthy()
      await waitFor(() => expect(search).toHaveBeenCalled(), { timeout: 3000 })
      await new Promise((r) => setTimeout(r, 50))
      expect(screen.getByText('Weed the beds'), 'still drawn once the search failed').toBeTruthy()
      expect(screen.queryByText(/search results/i), 'no search error in its place').toBeNull()
    })
  }
})

describe('Assigned › Mine narrows the board and the graph', () => {
  const ANAS = { ...ON_BEDS, assignee: 'ana' }
  const BOS = { ...ON_SHED, assignee: 'bo' }
  const chooseMine = async () => {
    fireEvent.click(await findHeaderFilter())
    fireEvent.click(within(await screen.findByRole('group', { name: 'Assigned' })).getByRole('button', { name: /Mine/ }))
  }

  for (const view of ['board', 'dag']) {
    it(`${view}: only the owner's tasks`, async () => {
      all.mockImplementation(async () => ({ tasks: [ANAS, BOS], total: 2, complete: true, owner: 'ana' }))
      mount({ view })
      expect(await screen.findByText('Oil the shears')).toBeTruthy()
      await chooseMine()
      await waitFor(() => expect(screen.queryByText('Oil the shears'), "bo's task").toBeNull())
      expect(screen.getByText('Weed the beds')).toBeTruthy()
    })
  }

  // Mine is named first where it emptied the view, with the tasks before it counted: a tag or a list
  // that only someone else's task carries said "Nothing in Shed … just none on this list".
  for (const [what, over] of [['a tag', { tag: 'oiling' }], ['a list', { list: 'tl-shed' }]] as const) {
    it(`under ${what} only another's task carries, names Mine, counting the tasks there`, async () => {
      all.mockImplementation(async () => ({ tasks: [ANAS, { ...BOS, labels: ['oiling'] }], total: 2, complete: true, owner: 'ana' }))
      mount({ view: 'board', ...over })
      expect(await screen.findByText('Oil the shears')).toBeTruthy()
      await chooseMine()
      expect(await screen.findByText('No tasks of yours here')).toBeTruthy()
      expect(screen.getByText('The one task here is not assigned to you.')).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Everyone' })).toBeTruthy()
    })
  }

  it('a board with none of the owner\'s says so, with Everyone as its way out', async () => {
    all.mockImplementation(async () => ({ tasks: [{ ...ANAS, assignee: 'cy' }, BOS], total: 2, complete: true, owner: 'ana' }))
    mount({ view: 'board' })
    expect(await screen.findByText('Oil the shears')).toBeTruthy()
    await chooseMine()
    expect(await screen.findByText('No tasks of yours here')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Everyone' })).toBeTruthy()
  })
})
