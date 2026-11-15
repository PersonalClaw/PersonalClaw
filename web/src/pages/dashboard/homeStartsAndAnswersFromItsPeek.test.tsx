// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { NeedsYouHarness, gateItem, waitingItem, waitingList } from '../../test/needsYouHarness'

// ── Needs you answers an ask that takes words, from its card and from its panel ───────────────────
//
// An ask answered by one line of words is answered with a typed line, sent to the ask's own route; a
// failed send keeps the line and says why. A loop's question, and a loop that stalled or is blocked,
// carry that line on their card, so the panel is never the only way: a field across the card with
// the chat list's field edge, "Reply in one line: Enter sends", Enter sending it with the panel
// closed. A run's text gate and an agent's one question take it in their panel. (Starting something
// is New chat's, in Home's header, at the sidebar's top and on ⌘N: Needs you holds only what waits.)

const waiting = vi.fn()
const createChatSession = vi.fn()
const sendChat = vi.fn()
const resumeWorkflowRun = vi.fn()
const uLoopNudge = vi.fn()
const answerChatQuestion = vi.fn()
const chatSessionDetail = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    running: () => Promise.resolve({ running: [], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    status: () => Promise.resolve({}),
    createChatSession: (...a: unknown[]) => createChatSession(...a),
    sendChat: (...a: unknown[]) => sendChat(...a),
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...a),
    uLoopNudge: (...a: unknown[]) => uLoopNudge(...a),
    answerChatQuestion: (...a: unknown[]) => answerChatQuestion(...a),
    chatSessionDetail: (...a: unknown[]) => chatSessionDetail(...a),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  localStorage.clear()
  waiting.mockResolvedValue(waitingList())
  createChatSession.mockResolvedValue({ key: 'chat-new-1' })
  sendChat.mockResolvedValue({ ok: true, session: 'chat-new-1' })
  resumeWorkflowRun.mockResolvedValue({ ok: true })
  uLoopNudge.mockResolvedValue({ ok: true })
  answerChatQuestion.mockResolvedValue({ ok: true, outcome: 'answered' })
  chatSessionDetail.mockResolvedValue({ key: 'chat-1', title: 'Release notes', messages: [
    { role: 'user', content: 'Draft the release notes' },
    { role: 'assistant', content: 'Which version are these notes for?' },
  ] })
})

/** A loop that waits on your words: its question, or its stall or its block with the reason. */
function loopAsk(over: Record<string, unknown> = {}, id = 'l1') {
  return waitingItem({
    id: `loop_question:${id}`, kind: 'loop_question', work: `loop:${id}`, session: '', consequence: 'asks',
    brief: { name: 'Docs', question: 'Keep the old page?', ...over },
    answer: { method: 'POST', path: `/api/loops/${id}/nudge`, takes: ['text'], body: {} }, route: `#/loops/${id}`,
  })
}

describe("a loop's line, on its card with the panel closed", () => {
  const cases: [string, Record<string, unknown>, RegExp][] = [
    ['a question', {}, /^Reply to Keep the old page\?/],
    ['a stall', { question: '', status: 'stagnant', why: 'No new findings in 3 cycles' }, /^Reply to Docs has stalled/],
    ['a block', { question: '', status: 'blocked', why: 'The tests need a key' }, /^Reply to Docs is blocked/],
  ]

  it.each(cases)('%s draws its field across the card, with the field edge, and Enter sends it to the nudge', async (_what, brief, name) => {
    waiting.mockResolvedValue(waitingList([loopAsk(brief)]))
    const onQuery = vi.fn()
    render(<NeedsYouHarness onQuery={onQuery} />)
    const field = await screen.findByRole('textbox', { name })
    expect(field.getAttribute('placeholder')).toBe('Reply in one line: Enter sends')
    expect(field.className.split(/\s+/)).toEqual(expect.arrayContaining(['border', 'border-on-surface-low/70', 'max-lg:min-h-11']))
    await userEvent.type(field, 'Try the archive folder too{Enter}')
    await waitFor(() => expect(uLoopNudge).toHaveBeenCalledWith('l1', 'Try the archive folder too'))
    expect(onQuery, 'the panel stayed closed').not.toHaveBeenCalled()
  })

  it('an empty line sends nothing', async () => {
    waiting.mockResolvedValue(waitingList([loopAsk({ status: 'stagnant', question: '' })]))
    render(<NeedsYouHarness />)
    await userEvent.type(await screen.findByRole('textbox', { name: /^Reply to/ }), '   {Enter}')
    expect(uLoopNudge).not.toHaveBeenCalled()
  })

  it('a send that fails keeps the line and says why, on the card', async () => {
    uLoopNudge.mockRejectedValue(new Error('the loop is not running'))
    waiting.mockResolvedValue(waitingList([loopAsk({ status: 'blocked', question: '' })]))
    render(<NeedsYouHarness />)
    const field = await screen.findByRole('textbox', { name: /^Reply to/ })
    await userEvent.type(field, 'Use the staging key{Enter}')
    expect(await screen.findByText(/Could not send that answer: the loop is not running/)).toBeInTheDocument()
    expect(field).toHaveValue('Use the staging key')
  })

  it('a run’s text gate and an agent’s question keep their line in the panel, not on the card', async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'text', prompt: 'Name the release' })]))
    render(<NeedsYouHarness />)
    await screen.findByRole('button', { name: 'Show the details of Name the release' })
    expect(screen.queryByRole('textbox')).toBeNull()
  })
})

describe('a free-text ask, answered from its peek', () => {
  /** The detail pane opened on the ask's card. */
  async function peek(title: RegExp | string) {
    const card = await screen.findByRole('button', { name: typeof title === 'string' ? `Show the details of ${title}` : title })
    await userEvent.click(card)
    return screen.findByRole('region', { name: typeof title === 'string' ? title : undefined })
  }

  it('a run’s text gate takes the line as its answer, through its resume token', async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'text', prompt: 'Name the release' })]))
    render(<NeedsYouHarness />)
    const pane = await peek('Name the release')
    await userEvent.type(within(pane).getByRole('textbox', { name: 'Reply to Name the release' }), 'v2.0 Spring{Enter}')
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-77', { answer: 'v2.0 Spring', resume_token: 'tok-9' }))
    expect(await within(pane).findByText('Answered “v2.0 Spring”. The run is moving again.')).toBeInTheDocument()
  })

  it('a loop’s question takes the line as its nudge', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'asks',
      brief: { name: 'Docs', question: 'Keep the old page?' },
      answer: { method: 'POST', path: '/api/loops/l1/nudge', takes: ['text'], body: {} }, route: '#/loops/l1',
    })]))
    render(<NeedsYouHarness />)
    const pane = await peek('Keep the old page?')
    await userEvent.type(within(pane).getByRole('textbox', { name: /^Reply to/ }), 'Keep it, link it from the new one{Enter}')
    await waitFor(() => expect(uLoopNudge).toHaveBeenCalledWith('l1', 'Keep it, link it from the new one'))
  })

  it('an agent’s question shows the conversation it came from, and takes the line as your own answer', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'question:q1', kind: 'question', consequence: 'asks',
      brief: { questions: [{ question: 'Which version?' }], asked_by: 'Release notes' },
      answer: { method: 'POST', path: '/api/chat/sessions/chat-1/questions/q1/answer', takes: ['answers', 'skip'], body: {} },
    })]))
    render(<NeedsYouHarness />)
    const pane = await peek('Which version?')
    const tail = await within(pane).findByRole('region', { name: 'The conversation it came from' })
    expect(within(tail).getByText('Which version are these notes for?')).toBeInTheDocument()
    await userEvent.type(within(pane).getByRole('textbox', { name: 'Reply to Which version?' }), '2.0{Enter}')
    await waitFor(() => expect(answerChatQuestion).toHaveBeenCalledWith('chat-1', 'q1', { answers: [{ selected: [], other: '2.0' }] }))
    expect(await within(pane).findByText('Answered. The agent carries on.')).toBeInTheDocument()
  })

  it('a send that fails keeps the line and says why', async () => {
    uLoopNudge.mockRejectedValue(new Error('the loop is not running'))
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'asks',
      brief: { name: 'Docs', question: 'Keep the old page?' },
      answer: { method: 'POST', path: '/api/loops/l1/nudge', takes: ['text'], body: {} }, route: '#/loops/l1',
    })]))
    render(<NeedsYouHarness />)
    const pane = await peek('Keep the old page?')
    const field = within(pane).getByRole('textbox', { name: /^Reply to/ })
    await userEvent.type(field, 'Keep it{Enter}')
    expect(await within(pane).findByText(/Could not send that answer: the loop is not running/)).toBeInTheDocument()
    expect(field).toHaveValue('Keep it')
  })

  it('takes no line for an ask answered otherwise: a yes or a no, a choice, or a card of several questions', async () => {
    waiting.mockResolvedValue(waitingList([
      gateItem(),
      waitingItem({ id: 'question:q2', kind: 'question', consequence: 'asks',
        brief: { questions: [{ question: 'Which version?' }, { question: 'Which date?' }] },
        answer: { method: 'POST', path: '/api/chat/sessions/chat-1/questions/q2/answer', takes: ['answers', 'skip'], body: {} } }),
    ]))
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-77:gate.push:1' }} />)
    const pane = await screen.findByRole('region', { name: 'Which branch should I push?' })
    expect(within(pane).queryByRole('textbox', { name: /^Reply to/ })).toBeNull()
  })
})
