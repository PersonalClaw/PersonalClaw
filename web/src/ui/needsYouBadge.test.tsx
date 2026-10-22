// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../lib/data'
import { NeedsYouBadge } from './NeedsYouBadge'
import { HomeHarness, waitingItem, waitingList } from '../test/homeHarness'

// ── The toolbar's needs-you badge ───────────────────────────────────────────────────────────────
//
// One badge, on every page, for what waits on you: it counts Home's Needs You from the same read,
// shows nothing at 0, and never lets an unknown count read as nothing waiting.

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
vi.mock('../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('../pages/dashboard/PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('./surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))
vi.mock('../pages/dashboard/widgets/Suggestions', () => ({ Suggestions: () => null }))

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  inboxOpen.mockResolvedValue([])
})

const badge = () => screen.queryByRole('button', { name: /waits? on you|waiting on you/ })

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
    const b = await screen.findByRole('button', { name: 'What waits on you is not read yet — open Home' })
    expect(within(b).getByText('—')).toBeInTheDocument()
  })

  it('says the count could not be read, never that nothing waits', async () => {
    waiting.mockRejectedValue(new Error('gateway down'))
    render(<NeedsYouBadge navigate={() => {}} />)
    const b = await screen.findByRole('button', { name: 'What waits on you couldn’t be read — open Home' })
    expect(within(b).getByText('—')).toBeInTheDocument()
  })

  it('counts every row of Needs You, notes included, and opens Home', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem(), waitingItem({ id: 'question:q', kind: 'question', brief: { questions: [{ question: 'Q?' }] } })]))
    inboxOpen.mockResolvedValue([{ id: 'n1', item_kind: 'user_note', status: 'pending', message: 'Call the bank', created_at: 1 }])
    const navigate = vi.fn()
    render(<NeedsYouBadge navigate={navigate} />)
    const b = await screen.findByRole('button', { name: '3 waiting on you — open Home' })
    expect(within(b).getByText('3')).toBeInTheDocument()
    await userEvent.click(b)
    expect(navigate).toHaveBeenCalledWith('dashboard')
  })

  it('agrees with Home’s Needs You, read once for both', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem(), waitingItem({ id: 'question:q', kind: 'question', brief: { questions: [{ question: 'Q?' }] } })]))
    render(<><NeedsYouBadge navigate={() => {}} /><HomeHarness /></>)
    await screen.findByRole('button', { name: '2 waiting on you — open Home' })
    const group = screen.getByRole('region', { name: 'Needs You' })
    expect(within(group).getByText('2')).toHaveAttribute('data-group-count')
    expect(waiting).toHaveBeenCalledTimes(1)
  })
})
