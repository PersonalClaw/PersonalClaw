// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { WsMessage } from '../../lib/useChatSocket'
import { NeedsYouHarness, waitingItem, waitingList } from '../../test/needsYouHarness'
import { HOLD_MS } from './NeedsYouPage'

// ── Needs you holds still while you work in it ──────────────────────────────────────────────────
//
// The list re-reads on every frame that something waits, and each read came in at once: a card
// you were about to press moved up as the one above it settled, and a new ask pushed the rest
// down under the pointer. Now, while the pointer is over the list, the focus is in it, an answer is
// on its way, or under 2 s have passed since your last press, key or scroll there, a card that
// settles keeps its place and says how it went, a card answered elsewhere says so in its place,
// and new asks wait behind "N new asks · Show them" in the header, which moves nothing. Show them
// takes the latest read and puts the focus on the first new card. Off the list, a read is shown as
// it lands. Nothing it says is assertive.

const waiting = vi.fn()
const resolveApproval = vi.fn()
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    projects: () => Promise.resolve([]),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
  },
}))
const frames: Array<(m: WsMessage) => void> = []
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { frames.push(onMessage) },
}))

/** An approval of a shell call, asked in its own chat. */
const ask = (n: number) => waitingItem({
  id: `approval:a${n}`, work: `chat:c${n}`, session: `c${n}`, consequence: 'asks', risk: '',
  brief: { tool: `tool-${n}`, tool_purpose: `Purpose ${n}`, source_label: 'chat', confirms: [] },
  answer: { method: 'POST', path: `/api/approvals/a${n}/{answer}`, takes: ['approve', 'reject'], body: {} },
})

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  frames.length = 0
  vi.useFakeTimers({ shouldAdvanceTime: true })
  waiting.mockResolvedValue(waitingList([ask(1), ask(2), ask(3)]))
  resolveApproval.mockResolvedValue({ ok: true })
})
afterEach(() => { vi.useRealTimers() })

const list = () => screen.getByRole('region', { name: 'Needs you' })
/** The tools the cards name, in the order the list draws them. */
const shown = () => [...list().querySelectorAll('[data-need-card]')].map((c) => c.querySelector('[data-type="label-m"], code')?.textContent ?? c.textContent ?? '')
  .map((t) => (t.match(/tool-\d/) ?? [''])[0])

/** The next read, then a frame that something waits, past its burst's wait. */
async function nextRead(list: ReturnType<typeof waitingList>) {
  waiting.mockResolvedValue(list)
  act(() => { for (const f of [...frames]) f({ type: 'waiting_changed', data: {} } as WsMessage) })
  await act(async () => { vi.advanceTimersByTime(350) })
  await act(async () => { await Promise.resolve() })
}

async function mounted() {
  render(<NeedsYouHarness />)
  await waitFor(() => expect(shown()).toEqual(['tool-1', 'tool-2', 'tool-3']))
}

describe('while the pointer is over the list', () => {
  it('a card answered elsewhere keeps its place and says so; the cards below do not move up', async () => {
    await mounted()
    fireEvent.pointerEnter(list())
    await nextRead(waitingList([ask(2), ask(3)]))
    expect(shown()).toEqual(['tool-1', 'tool-2', 'tool-3'])
    expect(within(list()).getByText('It is no longer waiting: it was answered or ended somewhere else.')).toBeInTheDocument()
  })

  it('a new ask waits behind the header’s "1 new ask · Show them", and moves no card', async () => {
    await mounted()
    fireEvent.pointerEnter(list())
    await nextRead(waitingList([ask(4), ask(1), ask(2), ask(3)]))
    expect(shown()).toEqual(['tool-1', 'tool-2', 'tool-3'])
    // A polite status, never an alert: a new ask is no emergency.
    expect(screen.getByText('1 new ask').closest('[role="status"]')).not.toBeNull()
    // The note is outside the list, so it covers and moves nothing in it.
    expect(list().contains(screen.getByText('1 new ask'))).toBe(false)
  })

  it('Show them takes the latest read, in order, and puts the focus on the first new card', async () => {
    await mounted()
    fireEvent.pointerEnter(list())
    await nextRead(waitingList([ask(4), ask(1), ask(2), ask(3)]))
    fireEvent.click(screen.getByRole('button', { name: 'Show them' }))
    await act(async () => { vi.advanceTimersByTime(50) })
    expect(shown()).toEqual(['tool-4', 'tool-1', 'tool-2', 'tool-3'])
    await waitFor(() => expect(document.activeElement?.closest('[data-need-key]')?.getAttribute('data-need-key')).toBe('waiting:approval:a4'))
    expect(screen.queryByText('1 new ask')).toBeNull()
  })

  it('once the pointer leaves, the list takes the latest read', async () => {
    await mounted()
    fireEvent.pointerEnter(list())
    await nextRead(waitingList([ask(2), ask(3)]))
    expect(shown()).toHaveLength(3)
    fireEvent.pointerLeave(list())
    await waitFor(() => expect(shown()).toEqual(['tool-2', 'tool-3']))
  })

  it('a press there holds it for 2 s after the pointer leaves, and no longer', async () => {
    await mounted()
    fireEvent.pointerEnter(list())
    fireEvent.pointerDown(list())
    await nextRead(waitingList([ask(2), ask(3)]))
    fireEvent.pointerLeave(list())
    expect(shown(), 'the press is still recent').toHaveLength(3)
    await act(async () => { vi.advanceTimersByTime(HOLD_MS + 50) })
    await waitFor(() => expect(shown()).toEqual(['tool-2', 'tool-3']))
  })
})

describe('the other ways it holds', () => {
  it('while the focus is in it', async () => {
    await mounted()
    act(() => { (list().querySelector('[data-row-hit-target]') as HTMLElement).focus() })
    await nextRead(waitingList([ask(3)]))
    expect(shown()).toEqual(['tool-1', 'tool-2', 'tool-3'])
  })

  it('for 2 s after a key there, and no longer', async () => {
    await mounted()
    fireEvent.keyDown(list(), { key: 'Shift' })
    await nextRead(waitingList([ask(3)]))
    expect(shown()).toHaveLength(3)
    await act(async () => { vi.advanceTimersByTime(HOLD_MS + 50) })
    await waitFor(() => expect(shown()).toEqual(['tool-3']))
  })

  it('an answer you gave keeps its card in place, saying how it went, with nothing assertive', async () => {
    await mounted()
    fireEvent.pointerEnter(list())
    waiting.mockResolvedValue(waitingList([ask(2), ask(3)]))
    fireEvent.click(within(list()).getAllByRole('button', { name: /^Allow/ })[0])
    await waitFor(() => expect(resolveApproval).toHaveBeenCalled())
    await act(async () => { vi.advanceTimersByTime(400) })
    expect(shown()).toEqual(['tool-1', 'tool-2', 'tool-3'])
    expect(within(list()).queryByRole('alert')).toBeNull()
  })
})

describe('off the list', () => {
  it('a read is shown as it lands (the control)', async () => {
    await mounted()
    await nextRead(waitingList([ask(4), ask(1)]))
    await waitFor(() => expect(shown()).toEqual(['tool-4', 'tool-1']))
    expect(screen.queryByText(/new asks?$/)).toBeNull()
  })
})
