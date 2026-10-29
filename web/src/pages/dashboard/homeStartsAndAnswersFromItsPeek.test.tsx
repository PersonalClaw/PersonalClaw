// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { HomeHarness, gateItem, waitingItem, waitingList } from '../../test/homeHarness'

// ── Home starts something, and answers a free-text ask from its peek (#3813) ──────────────────────
//
// "Start something" is the shortest path from Home to work: one line, Enter, and a new thread opens
// with that line as its first message, sent the way the chat's own first send is. And an ask that
// takes words (a run's text gate, a loop's question, an agent's one question) is answered from its
// detail with a typed line, sent to the ask's own route; a failed send keeps the line and says why.

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
vi.mock('./widgets/Suggestions', () => ({ Suggestions: () => null }))

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

const start = () => screen.getByRole('textbox', { name: 'Start something' })

describe('Start something', () => {
  it('Enter opens a new thread whose first message is the line', async () => {
    const navigate = vi.fn()
    render(<HomeHarness navigate={navigate} />)
    await userEvent.type(start(), 'Plan the garden for spring{Enter}')
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('chat/chat-new-1'))
    expect(createChatSession).toHaveBeenCalledWith()
    expect(sendChat).toHaveBeenCalledWith('Plan the garden for spring', 'chat-new-1')
  })

  it('sends nothing while the owner types, or when the field is left without Enter', async () => {
    render(<HomeHarness />)
    await userEvent.type(start(), 'Half a thought')
    fireEvent.blur(start())
    await userEvent.tab()
    expect(createChatSession).not.toHaveBeenCalled()
    expect(sendChat).not.toHaveBeenCalled()
  })

  it('a start that fails keeps the line and says why', async () => {
    createChatSession.mockRejectedValue(new Error('the gateway is restarting'))
    const navigate = vi.fn()
    render(<HomeHarness navigate={navigate} />)
    await userEvent.type(start(), 'Plan the garden{Enter}')
    expect(await screen.findByText('Could not start a new thread: the gateway is restarting. Nothing was sent, so try again.')).toBeInTheDocument()
    expect(start()).toHaveValue('Plan the garden')
    expect(navigate).not.toHaveBeenCalled()
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
    render(<HomeHarness />)
    const pane = await peek('Name the release')
    await userEvent.type(within(pane).getByRole('textbox', { name: 'Reply to Name the release' }), 'v2.0 Spring{Enter}')
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-77', { answer: 'v2.0 Spring', resume_token: 'tok-9' }))
    expect(await within(pane).findByText('Answered “v2.0 Spring” — the run is moving again.')).toBeInTheDocument()
  })

  it('a loop’s question takes the line as its nudge', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'asks',
      brief: { name: 'Docs', question: 'Keep the old page?' },
      answer: { method: 'POST', path: '/api/loops/l1/nudge', takes: ['text'], body: {} }, route: '#/loops/l1',
    })]))
    render(<HomeHarness />)
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
    render(<HomeHarness />)
    const pane = await peek('Which version?')
    const tail = await within(pane).findByRole('region', { name: 'The conversation it came from' })
    expect(within(tail).getByText('Which version are these notes for?')).toBeInTheDocument()
    await userEvent.type(within(pane).getByRole('textbox', { name: 'Reply to Which version?' }), '2.0{Enter}')
    await waitFor(() => expect(answerChatQuestion).toHaveBeenCalledWith('chat-1', 'q1', { answers: [{ selected: [], other: '2.0' }] }))
    expect(await within(pane).findByText('Answered — the agent carries on.')).toBeInTheDocument()
  })

  it('a send that fails keeps the line and says why', async () => {
    uLoopNudge.mockRejectedValue(new Error('the loop is not running'))
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'asks',
      brief: { name: 'Docs', question: 'Keep the old page?' },
      answer: { method: 'POST', path: '/api/loops/l1/nudge', takes: ['text'], body: {} }, route: '#/loops/l1',
    })]))
    render(<HomeHarness />)
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
    render(<HomeHarness query={{ open: 'waiting:gate:run-77:gate.push:1' }} />)
    const pane = await screen.findByRole('region', { name: 'Which branch should I push?' })
    expect(within(pane).queryByRole('textbox', { name: /^Reply to/ })).toBeNull()
  })
})
