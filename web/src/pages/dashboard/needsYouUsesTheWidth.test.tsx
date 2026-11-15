// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { NeedsYouHarness, waitingItem, waitingList } from '../../test/needsYouHarness'
import { REVIEW_WHILE_ASKS } from './NeedsYouPage'

// ── Needs you uses the width it has ─────────────────────────────────────────────────────────────
//
// Home's Attention held its cards in one column of 720 px, a third of a 1920 px tab, with the rest
// empty. Needs you takes the tab's whole width while nothing is open, and each group's cards sit in
// a grid that reads left to right, then down, as many columns of at least 24rem as fit (one on a
// phone and at 200% text). With a panel open, the list is one column beside it. To review shows
// three beside what waits, rounded up to fill its last row of columns, so no row ends half empty.
// The all-clear stands centred on the list's area. A focused card keeps an outline under forced
// colours, which drop its ring.

const COLUMNS = 'grid-cols-[repeat(auto-fill,minmax(min(100%,24rem),1fr))]'
const waiting = vi.fn()
const skillProposals = vi.fn()
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => skillProposals(),
    projects: () => Promise.resolve([]),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))

const proposals = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `p${i}`, slug: `skill-${i}`, description: `Proposal ${i}`, created_at: `2026-11-13T09:0${i}:00Z` }))

/** jsdom draws no grid: the review grid's computed tracks say how many columns it draws. */
function columns(n: number) {
  const real = window.getComputedStyle
  vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element, pseudo?: string | null) => {
    const style = real.call(window, el, pseudo)
    if (!(el as HTMLElement).matches?.('[data-need-group]')) return style
    return { ...style, gridTemplateColumns: Array(n).fill('384px').join(' ') } as CSSStyleDeclaration
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  waiting.mockResolvedValue(waitingList([waitingItem({ consequence: 'asks', risk: '' })]))
  skillProposals.mockResolvedValue({ proposals: proposals(5), lastReview: null })
})
afterEach(() => { vi.restoreAllMocks() })

const grid = (group: 'waiting' | 'review') => document.querySelector<HTMLElement>(`[data-need-group="${group}"]`)!
const reviewCards = () => within(grid('review')).getAllByRole('button', { name: /^Show the details of / })

describe('the width', () => {
  it('with nothing open, each group’s cards sit in columns across the whole width', async () => {
    render(<NeedsYouHarness />)
    await waitFor(() => expect(grid('waiting')).not.toBeNull())
    expect(grid('waiting').className.split(/\s+/)).toContain(COLUMNS)
    expect(grid('review').className.split(/\s+/)).toContain(COLUMNS)
    expect(screen.getByRole('region', { name: 'Needs you' }).className.split(/\s+/)).toContain('w-full')
  })

  it('with a panel open, the list is one column beside it', async () => {
    render(<NeedsYouHarness query={{ open: 'waiting:approval:appr-1' }} />)
    await screen.findByRole('region', { name: 'shell.run' })
    expect(grid('waiting').className.split(/\s+/)).toContain('grid-cols-1')
    expect(grid('waiting').className).not.toContain(COLUMNS)
  })

  it.each([[1, 3], [2, 4], [3, 3], [4, 4]])('in %i columns, To review shows %i beside what waits, filling its last row', async (n, shown) => {
    expect(REVIEW_WHILE_ASKS).toBe(3)
    columns(n)
    render(<NeedsYouHarness />)
    await waitFor(() => expect(reviewCards()).toHaveLength(shown))
  })

  it('the all-clear stands centred on the list’s area', async () => {
    waiting.mockResolvedValue(waitingList())
    skillProposals.mockResolvedValue({ proposals: [], lastReview: null })
    render(<NeedsYouHarness />)
    const said = await screen.findByText('Nothing is waiting on you.')
    const centred = said.closest('.place-items-center')
    expect(centred, 'the all-clear is in its centred area').not.toBeNull()
    expect(centred!.className.split(/\s+/)).toEqual(expect.arrayContaining(['grid', 'flex-1']))
  })
})

describe('forced colours', () => {
  it('a focused card keeps an outline, since forced colours drop its ring', async () => {
    render(<NeedsYouHarness />)
    await waitFor(() => expect(grid('waiting')).not.toBeNull())
    for (const card of document.querySelectorAll('[data-need-card]')) {
      expect(card.className.split(/\s+/)).toEqual(expect.arrayContaining([
        'forced-colors:has-[>button:focus-visible]:outline-2', 'forced-colors:has-[>button:focus-visible]:outline-[Highlight]',
      ]))
    }
  })
})
