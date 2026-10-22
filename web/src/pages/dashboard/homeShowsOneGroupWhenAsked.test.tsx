// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { NeedsYouBadge } from '../../ui/NeedsYouBadge'
import { HomeHarness, runningRow, waitingItem, waitingList } from '../../test/homeHarness'

// ── Home shows one group when asked, and never hides what waits from the count ─────────────────
//
// A filter row above the groups picks all three or one of them, so a narrow window can show the one
// it is for. It is a view choice, kept per browser like the sidebar's state, and the toolbar's
// needs-you badge always counts everything: a filter never hides that something waits (#3813).

const waiting = vi.fn()
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    running: () => Promise.resolve({ running: [runningRow({ name: 'Plan the garden' })], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    status: () => Promise.resolve({}),
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
  waiting.mockResolvedValue(waitingList([waitingItem(), waitingItem({ id: 'question:q', kind: 'question', brief: { questions: [{ question: 'Which branch?' }] } })]))
})

const filter = () => screen.getByRole('radiogroup', { name: 'Show' })

describe('the group filter', () => {
  it('shows all three until one is picked, then only that one', async () => {
    render(<HomeHarness />)
    await screen.findByText('Which branch?')
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['Needs You', 'Running Now', 'Recent and completed'])
    await userEvent.click(within(filter()).getByRole('radio', { name: 'Running Now' }))
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['Running Now'])
    expect(screen.getByText('Plan the garden')).toBeInTheDocument()
    expect(screen.queryByText('Which branch?')).toBeNull()
  })

  it('is kept per browser, so the next visit opens on the same choice', async () => {
    render(<HomeHarness />)
    await userEvent.click(within(filter()).getByRole('radio', { name: 'Recent and completed' }))
    cleanup()
    render(<HomeHarness />)
    expect(within(filter()).getByRole('radio', { name: 'Recent and completed' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.queryByRole('heading', { name: 'Needs You' })).toBeNull()
    await userEvent.click(within(filter()).getByRole('radio', { name: 'All' }))
    expect(localStorage.getItem('home-groups')).toBeNull()
  })

  it('never hides what waits from the badge: with Needs You filtered out, it still counts every ask', async () => {
    localStorage.setItem('home-groups', 'running-now')
    render(<><NeedsYouBadge navigate={() => {}} /><HomeHarness /></>)
    expect(await screen.findByRole('button', { name: '2 waiting on you — open Home' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Needs You' })).toBeNull()
  })

  it('ignores a stored choice it does not know, and shows all three', async () => {
    localStorage.setItem('home-groups', 'idle')
    render(<HomeHarness />)
    expect(within(filter()).getByRole('radio', { name: 'All' })).toHaveAttribute('aria-checked', 'true')
  })
})
