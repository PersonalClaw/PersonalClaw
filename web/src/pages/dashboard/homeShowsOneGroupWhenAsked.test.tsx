// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { NeedsYouBadge } from '../../ui/NeedsYouBadge'
import { HomeHarness, runningRow, waitingItem, waitingList } from '../../test/homeHarness'
import { headerFilter } from '../../test/headerFilter'

// ── Home shows one group when asked, and never hides what waits from the count ─────────────────
//
// The header's filter picks all three groups or one of them, so a narrow window can show the one
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

/** The header's filter, and a choice in its open menu. */
const filter = () => headerFilter()
const choose = async (name: string) => {
  await userEvent.click(filter())
  await userEvent.click(await screen.findByRole('button', { name }))
}

describe('the group filter', () => {
  it('shows all three until one is picked, then only that one', async () => {
    render(<HomeHarness />)
    await screen.findByText('Which branch?')
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['Needs You', 'Running Now', 'Recent and completed'])
    await choose('Running Now')
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['Running Now'])
    expect(screen.getByText('Plan the garden')).toBeInTheDocument()
    expect(screen.queryByText('Which branch?')).toBeNull()
  })

  it('is kept per browser, so the next visit opens on the same choice', async () => {
    render(<HomeHarness />)
    await choose('Recent and completed')
    cleanup()
    render(<HomeHarness />)
    expect(filter(), 'the filter says what it shows').toHaveAttribute('aria-label', 'Show: Recent and completed')
    expect(filter()).toHaveAttribute('data-header-filter', 'set')
    expect(screen.queryByRole('heading', { name: 'Needs You' })).toBeNull()
    await choose('All groups')
    expect(localStorage.getItem('home-groups')).toBeNull()
  })

  it('never hides what waits from the badge: with Needs You filtered out, it still counts every ask', async () => {
    localStorage.setItem('home-groups', 'running-now')
    render(<><NeedsYouBadge navigate={() => {}} /><HomeHarness /></>)
    expect(await screen.findByRole('button', { name: '2 waiting on you — open Attention' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Needs You' })).toBeNull()
  })

  it('ignores a stored choice it does not know, and shows all three', async () => {
    localStorage.setItem('home-groups', 'idle')
    render(<HomeHarness />)
    expect(filter()).toHaveAttribute('aria-label', 'Show: All groups')
    expect(filter()).toHaveAttribute('data-header-filter', 'default')
  })
})
