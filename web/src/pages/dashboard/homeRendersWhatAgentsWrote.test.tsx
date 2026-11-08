import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ChatSessionSummary } from '../../lib/api'
import { HomeHarness, gateItem, runningRow, waitingItem, waitingList } from '../../test/homeHarness'
import { HOSTILE, LEADING, MD_BLOCK, MD_LINE, inert, rendersBlock, rendersLine } from '../../test/markdownSinks'

// ── Home shows what agents wrote, rendered ─────────────────────────────────────────────────────
//
// 🔴 An ask's title, a gate's prompt, a running row's line, a chat's title and last words and the
// conversation an ask came from are written by agents and people, and carry markdown: Home showed
// each with its marks on the page. Each now renders in the form its sink holds (a line, an excerpt,
// a body), and every name built from one reads it as plain text. An approval's title is a tool's
// name and a browse grant's second line is ours: both stay text.

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
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: 'Sam Rivera' }), firstNameOf: (n: string) => n.split(' ')[0] }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))
vi.mock('./widgets/Suggestions', () => ({ Suggestions: () => null }))
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
const gate = gateItem({ kind: 'text', prompt: `${MD_BLOCK}\n\n<widget title="Chart"><div>chart</div></widget>` }, { id: 'gate:run-9:g:1' })
// (An approval's purpose line and its card's names are the approval card's, rendered with the
// chat's approval prompt in their own change; here the purpose is plain.)
const approval = waitingItem({ brief: { tool: 'shell.run_**now**', tool_purpose: 'List the folder', source_label: 'chat', confirms: [] } })
const thread = { key: 'chat-9', title: MD_LINE, last_message: `**Key poin…`, last_ts: '2026-11-07T10:00:00Z' } as unknown as ChatSessionSummary

beforeEach(() => {
  sessionStorage.clear()
  waiting.mockResolvedValue(waitingList([question, gate, approval]))
  running.mockResolvedValue({ running: [runningRow({ name: MD_LINE, line: `Asks: ${MD_LINE}` })], finished: [] })
  chatSessions.mockResolvedValue([thread])
  chatSessionDetail.mockResolvedValue({ messages: [
    { role: 'assistant', content: `${MD_BLOCK}\n\n<widget title="Chart"><div>chart</div></widget>` },
    { role: 'user', content: 'line one\nline two' },
  ] })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Attention renders what agents wrote', () => {
  it("an ask's title, a running row's name and line, and a chat's title render as lines", async () => {
    render(<HomeHarness />)
    await waitFor(() => expect(linesOf().length).toBeGreaterThanOrEqual(4))
    for (const line of linesOf()) expect(rendersLine(line), line.outerHTML).toEqual([])
  })

  it('every name built from them is plain text', async () => {
    render(<HomeHarness />)
    await waitFor(() => expect(linesOf().length).toBeGreaterThanOrEqual(4))
    for (const name of names()) expect(name, 'a name carries a mark').not.toMatch(/`|\*\*published/)
  })

  it("an approval's title is a tool's name, and stays the text it is", async () => {
    render(<HomeHarness />)
    expect(await screen.findByText('shell.run_**now**')).toBeInTheDocument()
  })

  it("a gate's prompt renders as a body, without its widget block", async () => {
    render(<HomeHarness query={{ open: 'waiting:gate:run-9:g:1' }} />)
    await waitFor(() => expect(document.querySelector('pre')).not.toBeNull())
    const prompt = document.querySelector('pre')!.closest('div[data-type="body-s"]')!
    expect(rendersBlock(prompt)).toEqual([])
    expect(document.body.textContent).not.toContain('<widget')
    expect(document.querySelector('iframe')).toBeNull()
  })

  it("a question card's list renders each question as a line, a leading mark kept", async () => {
    render(<HomeHarness query={{ open: 'waiting:question:q-1' }} />)
    const item = await screen.findByText('# of retries')
    expect(item.closest('li')).not.toBeNull()
  })

  it('the conversation an ask came from renders as the transcript does, cut at a block', async () => {
    render(<HomeHarness query={{ open: 'waiting:question:q-1' }} />)
    const region = await screen.findByRole('region', { name: 'The conversation it came from' })
    await waitFor(() => expect(region.querySelector('pre')).not.toBeNull())
    expect(rendersBlock(region)).toEqual([])
    expect(region.textContent).not.toContain('<widget')
    expect(region.querySelector('br'), 'your lines keep their breaks').not.toBeNull()
  })

  it("a chat's last words render, the gateway's cut repaired", async () => {
    render(<HomeHarness query={{ open: 'thread:chat-9' }} />)
    const strong = await screen.findByText('Key poin')
    expect(strong.tagName).toBe('STRONG')
    expect(document.body.textContent).not.toContain('**Key')
  })

  it('the detail pane heads with the rendered title, and its name and tooltip are plain', async () => {
    render(<HomeHarness query={{ open: 'thread:chat-9' }} />)
    const heading = await screen.findByRole('heading', { level: 2, name: SEEN })
    expect(rendersLine(heading)).toEqual([])
    expect(heading.getAttribute('title')).toBe(SEEN)
  })

  it('a hostile title is inert where it is drawn', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({ id: 'question:q-2', kind: 'question', brief: { questions: [{ question: HOSTILE }] } })]))
    render(<HomeHarness />)
    const lines = await screen.findAllByText(/open the report/)
    for (const line of lines) expect(inert(line.closest('p, span') ?? line)).toEqual([])
    fireEvent.keyDown(window, { key: 'Escape' })
  })
})
