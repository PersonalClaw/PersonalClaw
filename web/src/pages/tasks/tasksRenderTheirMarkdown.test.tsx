import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { TaskItem } from '../../lib/api'
import { HOSTILE, LEADING, MD_LINE, inert, rawSinks, rendersLine } from '../../test/markdownSinks'

// ── A task shows what its agent wrote, rendered ────────────────────────────────────────────────
//
// 🔴 The owner's report: a task's title, its steps, its exit criteria and its notes are written by
// agents (the task tools, the code planner, triage) and carry markdown, and every place a task is
// listed or opened showed it raw — "Skip `model_calls` in `export_digest()` in **published.py**",
// backticks and asterisks on the page. Each title now renders as one line (`MarkdownLine`) inside
// the element that already cut it, and every name and tooltip built from it reads the same line as
// plain text (`previewLine`). Notes render as text written in lines; an agent's instructions read
// rendered with their raw source a press away.

const graph = vi.fn(async () => ({ analysis: null, tasks: [] }))
vi.mock('../../lib/api', async (orig) => {
  const mod = await orig<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      tasks: vi.fn(async () => ({ tasks: TASKS, owner: '' })),
      allTasks: vi.fn(async () => ({ tasks: TASKS, total: TASKS.length, complete: true, owner: '' })),
      projects: vi.fn(async () => []),
      taskLists: vi.fn(async () => []),
      readyTasks: vi.fn(async () => []),
      searchTasks: vi.fn(async () => ({ tasks: [] })),
      uLoops: vi.fn(async () => []),
      updateTask: vi.fn(async () => ({})),
      deleteTask: vi.fn(async () => ({})),
      taskComments: vi.fn(async () => []),
      taskNotes: vi.fn(async () => []),
      taskGraph: () => graph(),
    },
  }
})
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const { TasksListPage } = await import('./TasksListPage')
const { TaskBoard } = await import('./TaskBoard')
const { TaskGraph } = await import('./TaskGraph')
const { TaskDetail } = await import('./TaskDetail')
const { DependencyEditor, NotesEditor } = await import('./formControls')
const { TaskRow } = await import('../code/cockpit/tasks/TaskRow')
const { resetDataStore } = await import('../../lib/data')

/** The words a reader sees of MD_LINE: the marks rendered, none of them shown. */
const SEEN = 'Skip model_calls in export_digest() in published.py'

const task = (over: Partial<TaskItem>): TaskItem => ({ id: 't-1', title: MD_LINE, status: 'open', priority: 'medium', ...over } as TaskItem)
const TASKS: TaskItem[] = [
  task({}),
  task({ id: 't-2', title: LEADING[0][1] }),
  task({ id: 't-3', title: HOSTILE }),
]

/** Every element under `root` that shows MD_LINE's words. */
const linesOf = (root: ParentNode) =>
  Array.from(root.querySelectorAll('span, div, h2, h3, button')).filter((el) =>
    (el.textContent ?? '').trim() === SEEN && !Array.from(el.children).some((c) => (c.textContent ?? '').trim() === SEEN))

/** The sink element that carries a line's tooltip. */
const sinkOf = (line: Element) => line.closest('[title]')

const noop = () => {}
function List({ view, openId = '' }: { view: string; openId?: string }) {
  return (
    <TasksListPage onCreate={noop} view={view} filter="all" openId={openId} setView={noop} setFilter={noop}
      setOpenId={noop} editing={false} setEditing={noop} q="" sort="" scope="" list="" tag=""
      setQ={noop} setSort={noop} setScope={noop} setList={noop} setTag={noop} />
  )
}

beforeEach(() => { resetDataStore(); localStorage.clear(); sessionStorage.clear() })
afterEach(cleanup)

describe('a task title renders as one line everywhere a task is listed or headed', () => {
  it.each(['list', 'cards'])('the %s view: rendered, named and tooltipped in plain text', async (view) => {
    render(<List view={view} />)
    await waitFor(() => expect(linesOf(document.body).length).toBeGreaterThan(0))
    for (const line of linesOf(document.body)) expect(rendersLine(line), line.outerHTML).toEqual([])
    // The row's hit target is named by the plain line (and, in the list, its status).
    expect(screen.getAllByRole('button', { name: new RegExp(`^${SEEN.replace(/[().]/g, '\\$&')}`) }).length).toBeGreaterThan(0)
    for (const named of document.querySelectorAll('[title], [aria-label]')) {
      const name = `${named.getAttribute('title') ?? ''} ${named.getAttribute('aria-label') ?? ''}`
      expect(name, 'a name carries a mark').not.toMatch(/`|\*\*/)
    }
  })

  it('a leading mark is the text it is, and a hostile title is inert', async () => {
    render(<List view="list" />)
    expect((await screen.findAllByText('# of retries')).length).toBeGreaterThan(0)
    for (const line of await screen.findAllByText(/open the report/)) {
      const sink = sinkOf(line)!
      expect(inert(sink), sink.outerHTML).toEqual([])
    }
  })

  it('the task panel heads with the rendered title, and its tooltip is the plain line', async () => {
    render(<List view="list" openId="t-1" />)
    const heading = await screen.findByRole('heading', { level: 2, name: SEEN })
    expect(rendersLine(heading)).toEqual([])
    expect(heading.getAttribute('title')).toBe(SEEN)
  })

  it('the board card', () => {
    render(<TaskBoard tasks={[task({})]} onOpen={noop} onMove={noop} />)
    const card = screen.getByRole('button', { name: SEEN })
    expect(rendersLine(card.querySelector('.line-clamp-2')!)).toEqual([])
  })

  it('the dependency graph node', async () => {
    render(<TaskGraph tasks={[task({})]} onOpen={noop} />)
    await waitFor(() => expect(linesOf(document.body).length).toBeGreaterThan(0))
    const node = linesOf(document.body)[0]
    expect(rendersLine(node)).toEqual([])
    expect(sinkOf(node)?.getAttribute('title')).toBe(SEEN)
  })

  it('the code cockpit row, planned and live', () => {
    const { container, unmount } = render(<TaskRow task={task({})} state="ready" preview onSelect={noop} />)
    expect(rendersLine(container.firstElementChild!)).toEqual([])
    unmount()
    render(<TaskRow task={task({})} state="ready" preview={false} onSelect={noop} />)
    const row = screen.getByRole('button', { name: new RegExp(`^${SEEN.replace(/[().]/g, '\\$&')} — `) })
    expect(rendersLine(row)).toEqual([])
  })

  it('a prerequisite picked in the form', () => {
    render(<DependencyEditor selfId="t-9" allTasks={[task({})]} value={['t-1']} onChange={noop} />)
    const [picked] = linesOf(document.body)
    expect(rendersLine(picked)).toEqual([])
    expect(sinkOf(picked)?.getAttribute('title')).toBe(SEEN)
  })
})

describe("a task's detail renders what its agent wrote", () => {
  const DEP = task({ id: 't-dep', title: MD_LINE })
  const DETAILED = task({
    id: 't-main', title: 'Ship the digest', status: 'blocked', blocked_reason_kind: 'auto',
    block_reason: { is_blocked: true, blocking_task_ids: ['t-dep'], blocking_task_titles: [MD_LINE], message: `Waiting on: ${MD_LINE}` },
    dependencies: [{ depends_on_task_id: 't-dep', dependency_type: 'BLOCKS' }],
    exit_criteria: [{ description: MD_LINE }],
    action_plan: [{ content: MD_LINE }],
    notes: [{ content: '- one\n- two' }],
    research_notes: [{ content: 'line one\nline two' }],
    agent_instructions_template: '## Steps\n\nRun `make test`, then **ship**.',
  } as Partial<TaskItem>)
  const mount = () => render(
    <TaskDetail task={DETAILED} allTasks={[DEP, DETAILED]} onOpenTask={noop} onSaved={noop} onDeleted={noop} editing={false} onEditingChange={noop} />,
  )

  it('the blocked notice names each prerequisite as a rendered line, after our own "Waiting on"', () => {
    const { container } = mount()
    expect(container.textContent).toContain(`Waiting on ${SEEN}`)
    // The server's own sentence, under the prerequisites, names the titles too: read as plain text.
    expect(container.textContent, 'a mark shows somewhere in the detail').not.toMatch(/`|\*\*/)
  })

  it('the exit criterion, the plan step and the prerequisite row render as lines', () => {
    const lines = linesOf(mount().container)
    expect(lines.length, 'blocked notice, criterion, step, prerequisite').toBeGreaterThanOrEqual(4)
    for (const line of lines) expect(rendersLine(line), line.outerHTML).toEqual([])
  })

  it('a note written as a list is a list, and one written in lines keeps its breaks', () => {
    const { container } = mount()
    expect(Array.from(container.querySelectorAll('ul li')).map((li) => li.textContent)).toEqual(expect.arrayContaining(['one', 'two']))
    expect(Array.from(container.querySelectorAll('br')).length).toBeGreaterThanOrEqual(1)
    expect(container.textContent).toContain('line one')
  })

  it("an agent's instructions read rendered, and Raw shows the source", () => {
    const { container } = mount()
    expect(within(container).getByRole('heading', { name: 'Steps' })).toBeInTheDocument()
    fireEvent.click(within(container).getByRole('button', { name: /Raw/ }))
    expect(container.querySelector('pre')?.textContent).toContain('Run `make test`, then **ship**.')
  })

  it('the notes field in the form renders each note', () => {
    const { container } = render(<NotesEditor items={[{ content: '- one\n- two' }]} onChange={noop} placeholder="Add a note" />)
    expect(container.querySelectorAll('li')).toHaveLength(2)
  })
})

describe('no file that draws a task draws its title raw', () => {
  const FILES = [
    'pages/tasks/TasksListPage.tsx', 'pages/tasks/TaskBoard.tsx', 'pages/tasks/TaskGraph.tsx', 'pages/tasks/TaskDetail.tsx',
    'pages/tasks/formControls.tsx', 'pages/code/cockpit/tasks/TaskRow.tsx', 'pages/code/cockpit/tasks/TaskDetailView.tsx',
    'pages/projects/ProjectsSection.tsx', 'pages/loops/LoopCockpitPage.tsx',
  ]
  const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')

  /** Titles in these files that are not a task's: a project's chats and its knowledge, which render
   *  in the change that renders chat and knowledge titles. */
  const NOT_A_TASK: Record<string, string[]> = {
    'pages/projects/ProjectsSection.tsx': ['{c.title || c.key}', '{k.title || k.kind || k.id}'],
  }

  it.each(FILES)('%s', (rel) => {
    const others = NOT_A_TASK[rel] ?? []
    expect(rawSinks(read(rel), ['title']).filter((sink) => !others.some((o) => sink.endsWith(`: ${o}`)))).toEqual([])
  })

  it("the titles that are not a task's are still drawn where they were", () => {
    // So the list above names real sinks, and goes when they render.
    for (const [rel, sinks] of Object.entries(NOT_A_TASK)) {
      const found = rawSinks(read(rel), ['title'])
      for (const sink of sinks) expect(found.some((f) => f.endsWith(`: ${sink}`)), `${rel}: ${sink}`).toBe(true)
    }
  })

  it('plant: a title drawn raw is caught', () => {
    expect(rawSinks('<span className="truncate">{t.title}</span>', ['title'])).toEqual(['1: {t.title}'])
  })
})
