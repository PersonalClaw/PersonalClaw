// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { NeedsYouHarness, waitingItem, waitingList } from '../../test/needsYouHarness'

// ── Needs you's keys ────────────────────────────────────────────────────────────────────────────
//
// The list is a grid of cards, each with one open target. ↑↓ move between cards (in the same
// column across a grid; in order in one column, as here, where jsdom draws one), ←→ in reading
// order, Home and End to the first and last. Enter opens the focused card's panel; Escape closes it
// and puts the focus back on its card. With the panel open, ↑↓ move the selection too: the panel
// follows a moment later, with a replace, so one Back closes it. None of these is taken from a
// field: a key typed in the card's reply field is the field's. The focus stays on a card when its
// answer's control leaves with the answer. ⌘Enter approves the focused approval, as in a chat, and
// nothing else.

const resolveApproval = vi.fn()
const waiting = vi.fn()
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    projects: () => Promise.resolve([]),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
    uLoopNudge: () => Promise.resolve({ ok: true }),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))

const ask = (n: number) => waitingItem({
  id: `question:q${n}`, kind: 'question', work: `chat:c${n}`, session: `c${n}`, consequence: 'asks', risk: '',
  brief: { questions: [{ question: `Question ${n}?` }], asked_by: `Chat ${n}` },
  answer: { method: 'POST', path: `/api/chat/sessions/c${n}/questions/q${n}/answer`, takes: ['answers', 'skip'], body: {} },
})
const approval = waitingItem({ consequence: 'asks', risk: '' })
const loopAsk = waitingItem({
  id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'asks', risk: '',
  brief: { name: 'Docs', question: 'Keep the old page?' },
  answer: { method: 'POST', path: '/api/loops/l1/nudge', takes: ['text'], body: {} }, route: '#/loops/l1',
})

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  resolveApproval.mockResolvedValue({ ok: true })
  waiting.mockResolvedValue(waitingList([ask(1), ask(2), ask(3)]))
})
afterEach(() => { vi.useRealTimers() })

/** The card the focus is on, by its key. */
const focusedCard = () => document.activeElement?.closest('[data-need-key]')?.getAttribute('data-need-key') ?? ''
const target = (n: number) => screen.getByRole('button', { name: new RegExp(`^Show the details of Question ${n}\\?`) })

async function mounted(onQuery?: (q: Record<string, string>, replaced: boolean) => void, query?: Record<string, string>) {
  render(<NeedsYouHarness onQuery={onQuery} query={query} />)
  await screen.findByRole('button', { name: /^Show the details of Question 3\?/ })
}

describe('moving between cards', () => {
  it('↓ and ↑ step between cards, End and Home go to the last and the first', async () => {
    await mounted()
    act(() => target(1).focus())
    await userEvent.keyboard('{ArrowDown}')
    expect(focusedCard()).toBe('waiting:question:q2')
    await userEvent.keyboard('{ArrowUp}')
    expect(focusedCard()).toBe('waiting:question:q1')
    await userEvent.keyboard('{End}')
    expect(focusedCard()).toBe('waiting:question:q3')
    await userEvent.keyboard('{Home}')
    expect(focusedCard()).toBe('waiting:question:q1')
    // At an end, a key goes nowhere.
    await userEvent.keyboard('{ArrowUp}')
    expect(focusedCard()).toBe('waiting:question:q1')
  })

  it('→ and ← step in reading order', async () => {
    await mounted()
    act(() => target(2).focus())
    await userEvent.keyboard('{ArrowRight}')
    expect(focusedCard()).toBe('waiting:question:q3')
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}')
    expect(focusedCard()).toBe('waiting:question:q1')
  })

  it('a key typed in a card’s field is the field’s, never the list’s', async () => {
    waiting.mockResolvedValue(waitingList([loopAsk, ask(2)]))
    render(<NeedsYouHarness />)
    const field = await screen.findByRole('textbox', { name: /^Reply to Keep the old page\?/ })
    await userEvent.click(field)
    await userEvent.type(field, 'abc{ArrowLeft}{Home}{End}{ArrowDown}')
    expect(document.activeElement).toBe(field)
    expect(field).toHaveValue('abc')
  })
})

describe('the panel', () => {
  it('Enter opens the focused card’s panel, and Escape closes it and puts the focus back on its card', async () => {
    const onQuery = vi.fn()
    await mounted(onQuery)
    act(() => target(2).focus())
    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(onQuery).toHaveBeenLastCalledWith({ open: 'waiting:question:q2' }, false))
    await screen.findByRole('region', { name: 'Question 2?' })
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Question 2?' })).toBeNull())
    await waitFor(() => expect(focusedCard()).toBe('waiting:question:q2'))
  })

  it('arriving at a card’s panel by its address, Escape closes it and puts the focus on that card', async () => {
    // Nothing in the list had the focus before the panel opened, so there is nothing to return it
    // to: the page puts it on the card the panel showed.
    await mounted(undefined, { open: 'waiting:question:q3' })
    await screen.findByRole('region', { name: 'Question 3?' })
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Question 3?' })).toBeNull())
    await waitFor(() => expect(focusedCard()).toBe('waiting:question:q3'))
  })

  it('with the panel open, ↓ moves the selection, and the panel follows with a replace', async () => {
    const onQuery = vi.fn()
    await mounted(onQuery, { open: 'waiting:question:q1' })
    await screen.findByRole('region', { name: 'Question 1?' })
    act(() => target(1).focus())
    await userEvent.keyboard('{ArrowDown}')
    expect(focusedCard()).toBe('waiting:question:q2')
    await waitFor(() => expect(onQuery).toHaveBeenLastCalledWith({ open: 'waiting:question:q2' }, true))
    expect(await screen.findByRole('region', { name: 'Question 2?' })).toBeInTheDocument()
    // The focus stays in the list.
    expect(focusedCard()).toBe('waiting:question:q2')
  })
})

describe('the focus and the answers', () => {
  it('after an answer, the focus stays on its card', async () => {
    waiting.mockResolvedValue(waitingList([approval, ask(2)]))
    render(<NeedsYouHarness />)
    const allow = await screen.findByRole('button', { name: /^Allow/ })
    await userEvent.click(allow)
    await waitFor(() => expect(resolveApproval).toHaveBeenCalled())
    await waitFor(() => expect(focusedCard()).toBe('waiting:approval:appr-1'))
    expect(document.activeElement?.hasAttribute('data-row-hit-target')).toBe(true)
  })

  it('⌘Enter approves the focused approval, and does nothing on a card that is not one', async () => {
    waiting.mockResolvedValue(waitingList([approval, ask(2)]))
    render(<NeedsYouHarness />)
    const group = await screen.findByRole('group', { name: /Permission needed to run/ })
    act(() => within(group).getByRole('button', { name: /^Allow/ }).focus())
    // Only a press after the page has had the focus counts (`document.hasFocus`); jsdom's does.
    await userEvent.keyboard('{Meta>}{Enter}{/Meta}')
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledTimes(1))
    act(() => target(2).focus())
    await userEvent.keyboard('{Meta>}{Enter}{/Meta}')
    expect(resolveApproval).toHaveBeenCalledTimes(1)
  })
})
