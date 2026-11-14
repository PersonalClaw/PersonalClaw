// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { NeedsYouHarness, gateItem, runningRow, waitingItem, waitingList } from '../../test/needsYouHarness'
import { RunningLane } from './RunningLane'

// ── A card's own controls take their action, and never open its detail ──────────────────────────
//
// 🔴 A card is one press target that opens its panel (`openable()`), and every click inside it
// bubbled there: Allow sent the answer and also opened the panel, and below 840 px the panel took
// the list's place, so answering one card took you off the list. The owner's ruling: a card's
// action buttons take the action and never open the panel. Allow, Deny, a run's choice, a
// proposal's Accept, the reply field on a loop's card and the project chip each keep their press;
// a press on the card's body opens it. A running card's controls keep theirs too.

const resolveApproval = vi.fn()
const resumeWorkflowRun = vi.fn()
const acceptSkillProposal = vi.fn()
const uLoopNudge = vi.fn()
const waiting = vi.fn()
const skillProposals = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => skillProposals(),
    projects: () => Promise.resolve([{ id: 'p-garden', name: 'Garden' }]),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...a),
    acceptSkillProposal: (...a: unknown[]) => acceptSkillProposal(...a),
    uLoopNudge: (...a: unknown[]) => uLoopNudge(...a),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))

function atWidth(phone: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: phone && /max-width:\s*(768|839)px/.test(query), media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  atWidth(false)
  resolveApproval.mockResolvedValue({ ok: true })
  resumeWorkflowRun.mockResolvedValue({ ok: true })
  acceptSkillProposal.mockResolvedValue({ ok: true })
  uLoopNudge.mockResolvedValue({ ok: true })
  skillProposals.mockResolvedValue({ proposals: [], lastReview: null })
  waiting.mockResolvedValue(waitingList([
    waitingItem({ consequence: 'asks', risk: '', project_id: 'p-garden' }),
    gateItem(),
    waitingItem({
      id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'asks', risk: '',
      brief: { name: 'Docs', question: 'Keep the old page?' },
      answer: { method: 'POST', path: '/api/loops/l1/nudge', takes: ['text'], body: {} }, route: '#/loops/l1',
    }),
  ]))
})
afterEach(() => { vi.unstubAllGlobals() })

/** Needs you, with every query it sets heard. */
function mount() {
  const onQuery = vi.fn()
  render(<NeedsYouHarness onQuery={onQuery} />)
  /** Whether anything opened a card's panel. */
  const opened = () => onQuery.mock.calls.some(([q]) => !!(q as Record<string, string>).open)
  return { onQuery, opened }
}

const list = () => screen.getByRole('region', { name: 'Needs you' })

describe("a card's controls keep their press", () => {
  it.each([
    ['Allow', /^Allow/, () => resolveApproval],
    ['Deny', /^Deny/, () => resolveApproval],
  ])('%s answers the approval and opens nothing', async (_what, name, sent) => {
    const { opened } = mount()
    await userEvent.click(await within(await screen.findByRole('group', { name: /Permission needed to run/ })).findByRole('button', { name }))
    await waitFor(() => expect(sent()).toHaveBeenCalled())
    expect(opened(), 'the panel opened').toBe(false)
  })

  it("a run's choice answers it and opens nothing", async () => {
    const { opened } = mount()
    await userEvent.click(await screen.findByRole('button', { name: /^Answer Which branch should I push\? .*Ship it$/ }))
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalled())
    expect(opened()).toBe(false)
  })

  it("a proposal's Accept takes it and opens nothing", async () => {
    skillProposals.mockResolvedValue({ proposals: [{ id: 'p1', slug: 'tidy-notes', description: 'Tidy the notes', created_at: '2026-11-13T09:00:00Z' }], lastReview: null })
    const { opened } = mount()
    await userEvent.click(await screen.findByRole('button', { name: /^Accept/ }))
    await waitFor(() => expect(acceptSkillProposal).toHaveBeenCalled())
    expect(opened()).toBe(false)
  })

  it("the reply field on a loop's card takes the line and opens nothing", async () => {
    const { opened } = mount()
    const field = await screen.findByRole('textbox', { name: /^Reply to Keep the old page\?/ })
    await userEvent.click(field)
    await userEvent.type(field, 'Keep it{Enter}')
    await waitFor(() => expect(uLoopNudge).toHaveBeenCalledWith('l1', 'Keep it'))
    expect(opened()).toBe(false)
  })

  it('the project chip goes to the project and opens nothing', async () => {
    const { opened } = mount()
    const chip = await within(await screen.findByRole('group', { name: /Permission needed to run/ })).findByRole('link', { name: 'Open project Garden' })
    await userEvent.click(chip)
    expect(opened()).toBe(false)
  })

  it("a press on the card's body opens its detail (the control)", async () => {
    const { onQuery } = mount()
    await userEvent.click(await screen.findByRole('button', { name: /^Show the details of Which branch should I push\?/ }))
    await waitFor(() => expect(onQuery).toHaveBeenCalledWith({ open: 'waiting:gate:run-77:gate.push:1' }, false))
  })

  it('at 390 px an answer keeps you on the list', async () => {
    atWidth(true)
    const { opened } = mount()
    await userEvent.click(await within(await screen.findByRole('group', { name: /Permission needed to run/ })).findByRole('button', { name: /^Allow/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalled())
    expect(opened()).toBe(false)
    expect(list()).toBeInTheDocument()
  })
})

describe("a running card's controls keep their press", () => {
  it('its link and its chip go where they go; its body opens its detail', async () => {
    const onOpen = vi.fn()
    render(<RunningLane rows={[runningRow({ project_id: 'p-garden', name: 'Plan the garden' })]} loading={false} failed={false} empty=""
      onOpen={onOpen} projectOf={() => ({ id: 'p-garden', name: 'Garden' })} />)
    const card = screen.getByRole('listitem', { name: /Plan the garden/ })
    await userEvent.click(within(card).getByRole('link', { name: /^Open Plan the garden/ }))
    await userEvent.click(within(card).getByRole('link', { name: 'Open project Garden' }))
    expect(onOpen, 'a control opened the detail').not.toHaveBeenCalled()
    await userEvent.click(within(card).getByRole('button', { name: 'Show the details of Plan the garden' }))
    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})
