import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ChatSessionSummary } from '../../lib/api'
import { NeedsYouHarness, gateItem, runningRow, waitingItem, waitingList } from '../../test/needsYouHarness'
import { HOSTILE, HOSTILE_BODY, LEADING, MD_BLOCK, MD_LINE, inert, rawSinks, rendersBlock, rendersLine } from '../../test/markdownSinks'
import { findGenUiBlock } from '../../ui/widget/blocks'
import { firstLine } from '../../lib/attentionLanes'
import { registerBuiltinContentTypes } from '../../ui/content/registerBuiltins'

// ── Needs you shows what agents wrote, rendered ────────────────────────────────────────────────
//
// 🔴 An ask's title, a gate's prompt and the conversation an ask came from are written by agents
// and people, and carry markdown: the page showed each with its marks. Each now renders in the form
// its sink holds (a line, a body), and every name built from one reads it as plain text. An
// approval's title is a tool's name and a browse grant's second line is ours: both stay text. (What
// runs and your chats moved to Home's band and the sidebar, whose own tests read them.)

const waiting = vi.fn()
const running = vi.fn()
const chatSessions = vi.fn()
const chatSessionDetail = vi.fn()
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    status: () => Promise.resolve({}),
    running: () => running(),
    chatSessions: () => chatSessions(),
    chatSessionDetail: (key: string) => chatSessionDetail(key),
    // A widget's frame asks whether its artifact exists.
    artifactExists: () => Promise.resolve(false),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: 'Sam Rivera' }), firstNameOf: (n: string) => n.split(' ')[0] }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))

// The app registers its content types at startup (`main.tsx`), so a body that ran its widget blocks
// would draw them here as it does in the app.
registerBuiltinContentTypes()
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:home-renders-test'
  URL.revokeObjectURL = () => {}
}
vi.mock('./DashboardPage', () => ({ HomeOverview: () => null }))

const SEEN = 'Skip model_calls in export_digest() in published.py'
/** Each innermost element that shows MD_LINE's words. */
const linesOf = (root: ParentNode = document.body) =>
  Array.from(root.querySelectorAll('span, p, div, li, h2')).filter((el) =>
    (el.textContent ?? '').includes(SEEN) && !Array.from(el.children).some((c) => (c.textContent ?? '').includes(SEEN)))
const names = () => Array.from(document.querySelectorAll('[aria-label], [title]'))
  .map((el) => `${el.getAttribute('aria-label') ?? ''} ${el.getAttribute('title') ?? ''}`)

const question = waitingItem({
  id: 'question:q-1', kind: 'question', work: 'chat:chat-9', session: 'chat-9',
  brief: { asked_by: 'Researcher', questions: [{ question: MD_LINE }, { question: LEADING[0][1] }] },
  answer: { method: 'POST', path: '/api/questions/q-1', takes: ['answer'], body: {} },
})
// A gate's prompt may carry a genui block, as `findGenUiBlock` reads one: Home leaves it out.
const GENUI = '<widget kind="genui" title="Expense">\nf = Form(fields: ["amount"], action: "log_expense", submit: "Log expense")\n</widget>'
const GATE_PROMPT = `${MD_BLOCK}\n\n${GENUI}`
const gate = gateItem({ kind: 'text', prompt: GATE_PROMPT }, { id: 'gate:run-9:g:1' })
// (An approval's purpose line and its card's names are the approval card's, rendered with the
// chat's approval prompt in their own change; here the purpose is plain.)
const approval = waitingItem({ brief: { tool: 'shell.run_**now**', tool_purpose: 'List the folder', source_label: 'chat', confirms: [] } })
const thread = { key: 'chat-9', title: MD_LINE, last_message: `**Key poin…`, last_ts: '2026-11-07T10:00:00Z' } as unknown as ChatSessionSummary

beforeEach(() => {
  sessionStorage.clear()
  waiting.mockResolvedValue(waitingList([question, gate, approval]))
  running.mockResolvedValue({
    running: [runningRow({ name: MD_LINE, line: `Asks: ${MD_LINE}` })],
    finished: [runningRow({ work: 'workflow:r9', kind: 'workflow', session: '', name: MD_LINE, phase: 'done', line: MD_LINE,
      since: '2026-11-07T10:06:00Z', route: '#/workflows/runs/r9' })],
  })
  chatSessions.mockResolvedValue([thread])
  chatSessionDetail.mockResolvedValue({ messages: [
    { role: 'assistant', content: `${MD_BLOCK}\n\n<widget title="Chart"><div>chart</div></widget>` },
    { role: 'user', content: 'line one\nline two' },
  ] })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Needs you renders what agents wrote', () => {
  it("an ask's title renders as a line", async () => {
    render(<NeedsYouHarness />)
    await waitFor(() => expect(linesOf().length).toBeGreaterThanOrEqual(1))
    for (const line of linesOf()) expect(rendersLine(line), line.outerHTML).toEqual([])
  })

  it('every name built from it is plain text', async () => {
    render(<NeedsYouHarness />)
    await waitFor(() => expect(linesOf().length).toBeGreaterThanOrEqual(1))
    for (const name of names()) expect(name, 'a name carries a mark').not.toMatch(/`|\*\*published/)
  })

  it("an approval's title is a tool's name, and stays the text it is", async () => {
    render(<NeedsYouHarness />)
    expect(await screen.findByText('shell.run_**now**')).toBeInTheDocument()
  })

  it("a gate's prompt renders as a body, without its widget block", async () => {
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-9:g:1' }} />)
    await waitFor(() => expect(document.querySelector('pre')).not.toBeNull())
    expect(findGenUiBlock(GATE_PROMPT), 'the fixture holds a block Home would find').not.toBeNull()
    const prompt = document.querySelector('pre')!.closest('div[data-type="body-s"]')!
    expect(rendersBlock(prompt)).toEqual([])
    expect(document.body.textContent).not.toContain('<widget')
    expect(document.body.textContent, "the widget's source is left out").not.toContain('log_expense')
    expect(document.querySelector('iframe')).toBeNull()
  })

  it("the reply field is named for the ask in plain text", async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'text', prompt: MD_LINE }, { id: 'gate:run-9:g:3' })]))
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-9:g:3' }} />)
    expect(await screen.findByRole('textbox', { name: `Reply to ${SEEN}` })).toBeInTheDocument()
  })

  it("a hostile gate prompt is inert in its body", async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'text', prompt: `Notes\n\n${HOSTILE}\n\n${HOSTILE_BODY}` }, { id: 'gate:run-9:g:2' })]))
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-9:g:2' }} />)
    const sink = (await screen.findAllByText(/open the report/))[0].closest('div[data-type="body-s"]')!
    expect(sink, "the prompt's body").not.toBeNull()
    expect(inert(sink, { body: true })).toEqual([])
  })

  it("a question card's list renders each question as a line, a leading mark kept", async () => {
    render(<NeedsYouHarness query={{ open: 'waiting:question:q-1' }} />)
    const item = await screen.findByText('# of retries')
    expect(item.closest('li')).not.toBeNull()
  })

  it('the conversation an ask came from renders as the transcript does, cut at a block', async () => {
    render(<NeedsYouHarness query={{ open: 'waiting:question:q-1' }} />)
    const region = await screen.findByRole('region', { name: 'The conversation it came from' })
    await waitFor(() => expect(region.querySelector('pre')).not.toBeNull())
    expect(rendersBlock(region)).toEqual([])
    expect(region.textContent).not.toContain('<widget')
    expect(region.querySelector('br'), 'your lines keep their breaks').not.toBeNull()
  })

  it('the panel heads with the rendered title, wrapping in full with no native tooltip', async () => {
    render(<NeedsYouHarness query={{ open: 'waiting:question:q-1' }} />)
    const heading = await screen.findByRole('heading', { level: 2, name: SEEN })
    expect(rendersLine(heading)).toEqual([])
    expect(heading.hasAttribute('title')).toBe(false)
  })

  it('a hostile title is inert where it is drawn', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({ id: 'question:q-2', kind: 'question', brief: { questions: [{ question: HOSTILE }] } })]))
    render(<NeedsYouHarness />)
    const lines = await screen.findAllByText(/open the report/)
    // The sink is the card's title line, not the link's own span inside it.
    const sinks = [...new Set(lines.map((line) => line.closest('p[data-type="title-m"]')))]
    expect(sinks).toHaveLength(1)
    expect(sinks[0], "the card's title").not.toBeNull()
    expect(inert(sinks[0]!)).toEqual([])
    fireEvent.keyDown(window, { key: 'Escape' })
  })

  it("a title naming a tool keeps the tool's double underscores in its names", async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({ id: 'question:q-4', kind: 'question', brief: { questions: [{ question: 'Run mcp__github__create_issue for **triage**?' }] } })]))
    render(<NeedsYouHarness />)
    expect(await screen.findByRole('button', { name: /^Show the details of Run mcp__github__create_issue for triage\?/ })).toBeInTheDocument()
  })

  it.each(LEADING)("%s: an ask's title that reads as a block elsewhere is the text it is", async (_construct, text) => {
    waiting.mockResolvedValue(waitingList([waitingItem({ id: 'question:q-3', kind: 'question', brief: { questions: [{ question: text }] } })]))
    running.mockResolvedValue({ running: [], finished: [] })
    chatSessions.mockResolvedValue([])
    render(<NeedsYouHarness />)
    const card = (await screen.findByRole('button', { name: /^Show the details of / })).parentElement!
    expect(card.querySelector('p[data-type="title-m"]')!.textContent).toBe(firstLine(text))
  })

})

describe("an approval's title is a tool's name: text in its card, its pane and its names", () => {
  const tool = (id: string, name: string) => waitingItem({ kind: 'approval', id: `approval:${id}`, work: 'chat:chat-9', session: 'chat-9',
    brief: { tool: name, tool_purpose: 'List the folder', source_label: 'chat', confirms: [] } })

  it('a tool name with double underscores is named as written', async () => {
    waiting.mockResolvedValue(waitingList([tool('mcp', 'mcp__github__create_issue')]))
    render(<NeedsYouHarness />)
    expect(await screen.findByRole('button', { name: /^Show the details of mcp__github__create_issue/ })).toBeInTheDocument()
  })

  it("the pane heads with the tool's name as text, and its names say it as written", async () => {
    waiting.mockResolvedValue(waitingList([tool('run', 'shell.run_**now**')]))
    render(<NeedsYouHarness query={{ open: 'waiting:approval:run' }} />)
    const heading = await screen.findByRole('heading', { level: 2, name: 'shell.run_**now**' })
    expect(heading.querySelector('strong, code')).toBeNull()
    expect(heading.hasAttribute('title')).toBe(false)
    expect(screen.getByRole('button', { name: /^Show the details of shell\.run_\*\*now\*\*/ })).toBeInTheDocument()
  })
})

describe("no file on Needs you or Home draws what an agent wrote raw", () => {
  const FIELDS = ['title', 'name', 'line', 'reason', 'prompt', 'question', 'subtitle', 'last_message', 'content']
  const FILES = [
    'pages/dashboard/NeedsYouPage.tsx', 'pages/dashboard/NeedCards.tsx', 'pages/dashboard/RunningLane.tsx', 'pages/dashboard/widgets/ScheduleWidget.tsx',
    'pages/dashboard/PinnedTiles.tsx', 'pages/capabilities/ListBesideDetail.tsx', 'pages/dashboard/agents/AgentDay.tsx',
  ]
  /** Sinks in these files that draw no agent's words: an artifact's name, the pane's plain title
   *  where its caller gave no rendered heading, and an agent's own name on the band. */
  const NOT_WRITTEN: Record<string, string[]> = {
    'pages/dashboard/agents/AgentDay.tsx': ['{row.name}'],
    'pages/dashboard/PinnedTiles.tsx': ['{artifact?.name || slug}'],
    'pages/capabilities/ListBesideDetail.tsx': ['{detail.heading ?? detail.title}'],
  }
  const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')
  const raw = (rel: string, source = read(rel)) =>
    rawSinks(source, FIELDS).filter((sink) => !(NOT_WRITTEN[rel] ?? []).some((o) => sink.endsWith(`: ${o}`)))

  it.each(FILES)('%s', (rel) => {
    expect(raw(rel)).toEqual([])
  })

  it('the sinks that draw no agent\'s words are still drawn where they were', () => {
    for (const [rel, sinks] of Object.entries(NOT_WRITTEN)) {
      const found = rawSinks(read(rel), FIELDS)
      for (const sink of sinks) expect(found.some((f) => f.endsWith(`: ${sink}`)), `${rel}: ${sink}`).toBe(true)
    }
  })

  it.each(FILES)('plant: a title drawn raw in %s is caught', (rel) => {
    const planted = `${read(rel)}\n<p>{x.title}</p>`
    expect(raw(rel, planted)).toEqual([`${planted.split('\n').length}: {x.title}`])
  })
})
