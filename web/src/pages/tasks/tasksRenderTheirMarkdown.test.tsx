import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { CodeProject, TaskItem } from '../../lib/api'
import { HOSTILE, HOSTILE_BODY, LEADING, MD_BLOCK, MD_LINE, inert, plain, rawSinks, rendersBlock, rendersLine } from '../../test/markdownSinks'

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
const { TaskDetailView } = await import('../code/cockpit/tasks/TaskDetailView')
const { TaskListPanel } = await import('../projects/ProjectsSection')
const { resetDataStore } = await import('../../lib/data')
const { registerBuiltinContentTypes } = await import('../../ui/content/registerBuiltins')

// The app registers its content types at startup (`main.tsx`), so a body that ran its widget blocks
// would draw them here as it does in the app.
registerBuiltinContentTypes()
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:tasks-render-test'
  URL.revokeObjectURL = () => {}
}

/** The words a reader sees of MD_LINE: the marks rendered, none of them shown. */
const SEEN = 'Skip model_calls in export_digest() in published.py'

const task = (over: Partial<TaskItem>): TaskItem => ({ id: 't-1', title: MD_LINE, status: 'open', priority: 'medium', ...over } as TaskItem)
const LISTED: TaskItem[] = [
  task({}),
  task({ id: 't-2', title: LEADING[0][1] }),
  task({ id: 't-3', title: HOSTILE }),
]
/** The tasks the api lists: these three, unless a test lists its own. */
let TASKS: TaskItem[] = LISTED

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

beforeEach(() => { TASKS = LISTED; resetDataStore(); localStorage.clear(); sessionStorage.clear() })
afterEach(cleanup)

describe('a task title renders as one line everywhere a task is listed or headed', () => {
  it.each(['list', 'cards'])('the %s view: rendered, named and tooltipped in plain text', async (view) => {
    render(<List view={view} />)
    await waitFor(() => expect(linesOf(document.body).length).toBeGreaterThan(0))
    for (const line of linesOf(document.body)) expect(rendersLine(line), line.outerHTML).toEqual([])
    // The row's hit target is named by the plain line (and, in the list, its status).
    expect(screen.getAllByRole('button', { name: (name: string) => name.startsWith(SEEN) }).length).toBeGreaterThan(0)
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

  it.each(LEADING)("%s: a list row's title that reads as a block elsewhere is the text it is", async (_construct, text) => {
    TASKS = [task({ id: 't-L', title: text })]
    render(<List view="list" />)
    await waitFor(() => expect(document.querySelector('span.text-\\[0\\.9375rem\\]')).not.toBeNull())
    expect(document.querySelector('span.text-\\[0\\.9375rem\\]')!.textContent).toBe(text)
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
    const row = screen.getByRole('button', { name: (name: string) => name.startsWith(`${SEEN} — `) })
    expect(rendersLine(row)).toEqual([])
  })

  it('a prerequisite picked in the form', () => {
    render(<DependencyEditor selfId="t-9" allTasks={[task({})]} value={['t-1']} onChange={noop} />)
    const [picked] = linesOf(document.body)
    expect(rendersLine(picked)).toEqual([])
    expect(sinkOf(picked)?.getAttribute('title')).toBe(SEEN)
  })

  it("the picker's result rows", async () => {
    render(<DependencyEditor selfId="t-9" allTasks={[task({})]} value={[]} onChange={noop} />)
    fireEvent.click(screen.getByRole('button', { name: /Add prerequisite/ }))
    const row = await screen.findByRole('button', { name: SEEN })
    const [line] = linesOf(row)
    expect(rendersLine(line)).toEqual([])
    expect(sinkOf(line)?.getAttribute('title')).toBe(SEEN)
  })

  it("a project's task list", async () => {
    render(<TaskListPanel list={{ id: 'tl-1', name: 'General', project_id: 'p-1' }} onOpenTask={noop} />)
    const row = await screen.findByRole('button', { name: (name: string) => name.includes(SEEN) })
    const [line] = linesOf(row)
    expect(rendersLine(line)).toEqual([])
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
    const { container } = mount()
    const lines = linesOf(container)
    expect(lines.length, 'blocked notice, criterion, step, prerequisite').toBeGreaterThanOrEqual(4)
    for (const line of lines) expect(rendersLine(line), line.outerHTML).toEqual([])
    // The prerequisite row cuts its line, and its tooltip is the line's own, the plain line.
    expect(container.querySelector('span.block.truncate[title]')?.getAttribute('title')).toBe(SEEN)
  })

  it('a hostile note is inert in its body', () => {
    render(<TaskDetail task={{ ...DETAILED, notes: [{ content: `${HOSTILE}\n\n${HOSTILE_BODY}` }] } as TaskItem} allTasks={[DEP, DETAILED]}
      onOpenTask={noop} onSaved={noop} onDeleted={noop} editing={false} onEditingChange={noop} />)
    const sink = screen.getAllByText(/open the report/)[0].closest('div[data-type="body-s"]')!
    expect(sink, "the note's body").not.toBeNull()
    expect(inert(sink, { body: true })).toEqual([])
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

describe("the code cockpit's task detail renders what its agent wrote", () => {
  const project = { id: 'p-1', status: 'running', autopilot: false, queued_task_ids: [] } as unknown as CodeProject
  const OPENED = task({
    id: 't-main', description: MD_BLOCK, dependencies: [{ depends_on_task_id: 't-dep', dependency_type: 'BLOCKS' }],
    exit_criteria: [{ description: MD_LINE }], action_plan: [{ content: MD_LINE }],
  } as Partial<TaskItem>)
  const mount = () => render(
    <TaskDetailView project={project} task={OPENED} doneIds={new Set()} stageOpen knownIds={new Set(['t-dep', 't-main'])}
      findings={[]} liveActivity={[]} blockers={[{ id: 't-dep', title: MD_LINE }]} onOpenTask={noop} onBack={noop} onChanged={noop} />,
  )

  it('its title, plan step, exit criterion and blocker render as lines, and its description as a body', () => {
    const { container } = mount()
    const heading = container.querySelector('h3')!
    expect(rendersLine(heading)).toEqual([])
    const lines = linesOf(container)
    expect(lines.length, 'title, step, criterion, blocker').toBeGreaterThanOrEqual(4)
    for (const line of lines) expect(rendersLine(line), line.outerHTML).toEqual([])
    expect(rendersBlock(heading.nextElementSibling!)).toEqual([])
  })

  it("the blocker's button is named in plain text", () => {
    mount()
    const blocker = screen.getByRole('button', { name: SEEN })
    expect(blocker.getAttribute('title')).toBe(`Open “${SEEN}”`)
    expect(plain(blocker.getAttribute('title')!)).toEqual([])
  })
})

describe('no file that draws a task draws its title raw', () => {
  const FILES = [
    'pages/tasks/TasksListPage.tsx', 'pages/tasks/TaskBoard.tsx', 'pages/tasks/TaskGraph.tsx', 'pages/tasks/TaskDetail.tsx',
    'pages/tasks/formControls.tsx', 'pages/code/cockpit/tasks/TaskRow.tsx', 'pages/code/cockpit/tasks/TaskDetailView.tsx',
    'pages/projects/ProjectsSection.tsx', 'pages/loops/LoopCockpitPage.tsx',
  ]
  const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')

  /** Titles in these files that are not a task's, which render in the change that renders their
   *  kind's titles. None now: the Projects list's peek, which drew a project's chats and knowledge
   *  in `ProjectsSection.tsx`, is retired for each project's own page. */
  const NOT_A_TASK: Record<string, string[]> = {}

  const FIELDS = ['title', 'description', 'content']
  const raw = (rel: string, source = read(rel)) =>
    rawSinks(source, FIELDS).filter((sink) => !(NOT_A_TASK[rel] ?? []).some((o) => sink.endsWith(`: ${o}`)))

  it.each(FILES)('%s', (rel) => {
    expect(raw(rel)).toEqual([])
  })

  it("the titles that are not a task's are still drawn where they were", () => {
    // So the list above names real sinks, and goes when they render.
    for (const [rel, sinks] of Object.entries(NOT_A_TASK)) {
      const found = rawSinks(read(rel), FIELDS)
      for (const sink of sinks) expect(found.some((f) => f.endsWith(`: ${sink}`)), `${rel}: ${sink}`).toBe(true)
    }
  })

  it.each(FILES)('plant: a title drawn raw in %s is caught', (rel) => {
    const planted = `${read(rel)}\n<p>{x.title}</p>`
    expect(raw(rel, planted)).toEqual([`${planted.split('\n').length}: {x.title}`])
  })
})
