// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../lib/data'
import { NeedsYouBadge } from './NeedsYouBadge'
import { NeedsYouHarness, waitingItem, waitingList } from '../test/needsYouHarness'

// ── The toolbar's needs-you badge ───────────────────────────────────────────────────────────────
//
// One badge, on every page, for what waits on you: it counts Needs you's first group, what waits on
// your answer, from the same read, and opens Needs you. With none waiting and proposals or notes to
// review it shows their count muted; it shows nothing when both are 0, and never lets an unknown
// count read as nothing waiting. Its tip is the app's own, never a native title.

const waiting = vi.fn()
const inboxOpen = vi.fn()
vi.mock('../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => inboxOpen(),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    running: () => Promise.resolve({ running: [], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    status: () => Promise.resolve({}),
  },
}))
vi.mock('../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  inboxOpen.mockResolvedValue([])
})

const badge = () => screen.queryByRole('button', { name: /open Needs you$/ })
const note = { id: 'n1', item_kind: 'user_note', status: 'pending', message: 'Call the bank', created_at: 1 }

describe('the needs-you badge', () => {
  it('shows nothing at 0', async () => {
    waiting.mockResolvedValue(waitingList())
    render(<NeedsYouBadge navigate={() => {}} />)
    await waitFor(() => expect(waiting).toHaveBeenCalled())
    await waitFor(() => expect(badge()).toBeNull())
  })

  it('keeps a dash until the first read lands: its absence would say nothing waits', async () => {
    waiting.mockReturnValue(new Promise(() => {}))
    render(<NeedsYouBadge navigate={() => {}} />)
    const b = await screen.findByRole('button', { name: 'What waits on you is not read yet, open Needs you' })
    expect(within(b).getByText('—')).toBeInTheDocument()
  })

  it('says the count could not be read, never that nothing waits', async () => {
    waiting.mockRejectedValue(new Error('gateway down'))
    render(<NeedsYouBadge navigate={() => {}} />)
    const b = await screen.findByRole('button', { name: 'What waits on you couldn’t be read, open Needs you' })
    expect(within(b).getByText('—')).toBeInTheDocument()
  })

  it('counts what waits on your answer, not the notes to review, and opens Needs you', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem(), waitingItem({ id: 'question:q', kind: 'question', brief: { questions: [{ question: 'Q?' }] } })]))
    inboxOpen.mockResolvedValue([note])
    const navigate = vi.fn()
    render(<NeedsYouBadge navigate={navigate} />)
    const b = await screen.findByRole('button', { name: '2 waiting on your answer, open Needs you' })
    expect(within(b).getByText('2')).not.toHaveAttribute('data-badge-muted')
    expect(b.hasAttribute('title'), 'its tip is the app’s own').toBe(false)
    await userEvent.click(b)
    // 🔴 Red before: it opened Home on its Attention view, which is Needs you's own page now.
    expect(navigate).toHaveBeenCalledWith('mission-control')
  })

  it('with nothing waiting on your answer, counts what there is to review, muted', async () => {
    waiting.mockResolvedValue(waitingList())
    inboxOpen.mockResolvedValue([note])
    render(<NeedsYouBadge navigate={() => {}} />)
    const b = await screen.findByRole('button', { name: '1 to review, open Needs you' })
    expect(within(b).getByText('1')).toHaveAttribute('data-badge-muted')
  })

  it('agrees with Needs you’s first group, read once for both', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem(), waitingItem({ id: 'question:q', kind: 'question', brief: { questions: [{ question: 'Q?' }] } })]))
    inboxOpen.mockResolvedValue([note])
    render(<><NeedsYouBadge navigate={() => {}} /><NeedsYouHarness /></>)
    await screen.findByRole('button', { name: '2 waiting on your answer, open Needs you' })
    const group = screen.getByRole('region', { name: /^Waiting on your answer/ })
    expect(within(group).getByText('2')).toHaveAttribute('data-group-count')
    expect(waiting).toHaveBeenCalledTimes(1)
  })
})
