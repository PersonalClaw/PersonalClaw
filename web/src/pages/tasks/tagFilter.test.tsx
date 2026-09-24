import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react'
import { useState } from 'react'
import { TasksListPage } from './TasksListPage'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data'

// ── Task tags are readable as well as writable (#477) ───────────────────────────────────────────
//
// Every task can carry up to 10 tags (`max={10}` on the tag input), tags render as chips on rows and
// cards in every view, and `POST /api/tasks/search` has a fully working `tags` filter — plumbed
// through `tasks/handlers.py` to `registry.search_tasks`, which builds a `tag_set` and applies it.
// Nothing in the UI ever called it. Tags were write-only decoration: attachable, visible, and useless
// for finding anything.
//
//   · chips were inert `<span>`s        `!!chip.closest('button,a,[role="button"]') === false`
//   · the filter menu had no Tags section   (Scope, Status, Assigned, Sort by — four sections)
//   · `searchTasks` had ZERO callers passing `tags`, in the whole frontend
//
// The chips being inert is the second half of the same gap rather than a separate nit: they sit
// inches from the project pill, which IS a real `<button>` with the same pill shape, so they read as
// clickable and are not.
//
// 🪤 NOT a search bug, and the issue says so explicitly: `registry.search_tasks` documents its own
// scope honestly — *"case-folded substring over title+description"* — and scores only those two
// fields. Tags were deliberately given their own filter AXIS; that axis simply had no way in. So the
// fix is reachability, not teaching free-text search about labels, and this file asserts the axis
// rather than asserting that typing a tag name into the search box matches.

const TASKS = [
  { id: 't-1', title: 'Deliver Ramirez wedding gallery', status: 'open', priority: 'medium', labels: ['wedding', 'client-deadline'] },
  { id: 't-2', title: 'Press the fall cider batch', status: 'open', priority: 'medium', labels: ['fall market'] },
  { id: 't-3', title: 'Re-tile the back step', status: 'open', priority: 'medium' },
]

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      tasks: vi.fn(async () => ({ tasks: TASKS, owner: '' })),
      // The page loads through the COLLECTION read (#485); `tasks` stays mocked because
      // sibling surfaces still take a window.
      allTasks: vi.fn(async () => ({ tasks: TASKS, total: TASKS.length, complete: true, owner: '' })),
      projects: vi.fn(async () => []),
      taskLists: vi.fn(async () => []),
      readyTasks: vi.fn(async () => []),
      searchTasks: vi.fn(async () => ({ tasks: [TASKS[0]] })),
      uLoops: vi.fn(async () => []),
      updateTask: vi.fn(async () => ({})),
    },
  }
})
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const noop = () => {}

function Harness({ view = 'list', q = '', initialTag = '' }: { view?: string; q?: string; initialTag?: string }) {
  const [tag, setTag] = useState(initialTag)
  return (
    <TasksListPage
      onCreate={noop} view={view} filter="all" openId="" setView={noop} setFilter={noop}
      setOpenId={noop} editing={false} setEditing={noop}
      q={q} sort="" scope="" list="" tag={tag}
      setQ={noop} setSort={noop} setScope={noop} setList={noop} setTag={(v) => setTag(v || '')}
    />
  )
}

// `useQuery`'s cache is MODULE-level: without the reset, the `tasks` key survives between cases
// and a later mount paints the previous case's list without fetching at all.
beforeEach(() => {
  resetDataStore(); localStorage.clear(); sessionStorage.clear()
  vi.mocked(api.searchTasks).mockClear()
  vi.mocked(api.tasks).mockResolvedValue({ tasks: TASKS, owner: '' } as never)
  vi.mocked(api.allTasks).mockResolvedValue({ tasks: TASKS, total: TASKS.length, complete: true, owner: '' } as never)
})
afterEach(cleanup)

describe('a tag chip is a control, not decoration', () => {
  it('every chip is a real button with an accessible name', async () => {
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Deliver Ramirez wedding gallery')).toBeTruthy())

    const chip = screen.getByLabelText('Filter by tag wedding')
    // The exact measurement from the report, inverted: the chip WAS a bare span.
    expect(chip.tagName).toBe('BUTTON')
    expect(chip.closest('button,a,[role="button"]')).toBeTruthy()
    expect(chip.getAttribute('title')).toBe('Filter by tag “wedding”')
  })

  it('clicking a chip filters the list to that tag', async () => {
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Press the fall cider batch')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('Filter by tag wedding'))

    await waitFor(() => expect(screen.queryByText('Press the fall cider batch')).toBeNull())
    expect(screen.getByText('Deliver Ramirez wedding gallery')).toBeTruthy()
    // The untagged task is excluded too — the filter is "carries this tag", not "carries any tag".
    expect(screen.queryByText('Re-tile the back step')).toBeNull()
  })

  it('a tag that narrows a graph to nothing names the TAG, not the scope', async () => {
    // The board/DAG nothing-here state could only ever blame "this scope", because scope was the
    // only narrowing those views had. With a tag applying to them too, that sentence would point at
    // a control the user never touched — and, unlike a scope, a tag has an in-place escape.
    render(<Harness view="dag" initialTag="nope" />)
    await waitFor(() => expect(screen.getByText('No tasks tagged “nope”')).toBeTruthy())
    expect(screen.queryByText('No tasks match this scope.')).toBeNull()

    fireEvent.click(screen.getByText('Clear tag'))
    await waitFor(() => expect(screen.getByLabelText(/^Dependency graph/)).toBeTruthy())
  })
})

describe('the tag axis is reachable from the filter menu', () => {
  it('a Tag section lists the vocabulary with counts', async () => {
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Deliver Ramirez wedding gallery')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('Filter & sort'))
    // Was absent entirely: Scope / Status / Assigned / Sort by.
    const section = screen.getByText('Tag').parentElement!.parentElement as HTMLElement
    expect(within(section).getByText('Any tag')).toBeTruthy()
    expect(within(section).getByText('wedding')).toBeTruthy()
    expect(within(section).getByText('fall market')).toBeTruthy()
  })

  it('picking a tag from the menu narrows every view the same way', async () => {
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Press the fall cider batch')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('Filter & sort'))
    const section = screen.getByText('Tag').parentElement!.parentElement as HTMLElement
    fireEvent.click(within(section).getByText('fall market'))

    await waitFor(() => expect(screen.queryByText('Deliver Ramirez wedding gallery')).toBeNull())
    expect(screen.getByText('Press the fall cider batch')).toBeTruthy()
  })

  it('the section is hidden when nothing is tagged — no filter that can only be a no-op', async () => {
    // The vacuity counterpart. Without this, the "a Tag section exists" assertion above could be
    // satisfied by a section that renders unconditionally and offers only "Any tag".
    // `mockResolvedValue`, not `…Once`: the page calls `api.tasks()` twice on mount (the list, and
    // `{limit:1}` for the owner), so a one-shot override lands on whichever race won.
    vi.mocked(api.tasks).mockResolvedValue({ tasks: [TASKS[2]], owner: '' } as never)
    vi.mocked(api.allTasks).mockResolvedValue({ tasks: [TASKS[2]], total: 1, complete: true, owner: '' } as never)
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Re-tile the back step')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('Filter & sort'))
    expect(screen.queryByText('Tag')).toBeNull()
    expect(screen.getByText('Scope')).toBeTruthy()
  })
})

describe("the search API's tags filter finally has a caller", () => {
  it('an active tag is sent to /api/tasks/search alongside the query', async () => {
    render(<Harness q="ramirez" />)
    // The unfiltered shape first: a query with no tag must not start sending an empty `tags`.
    await waitFor(() => expect(api.searchTasks).toHaveBeenCalled())
    expect(vi.mocked(api.searchTasks).mock.calls[0][0]).toEqual({ query: 'ramirez', limit: 100 })

    fireEvent.click(screen.getByLabelText('Filter by tag wedding'))

    await waitFor(() => {
      const last = vi.mocked(api.searchTasks).mock.calls.at(-1)![0] as Record<string, unknown>
      expect(last.tags, 'the tags filter had zero callers before this').toEqual(['wedding'])
      expect(last.query).toBe('ramirez')
    })
  })
})
