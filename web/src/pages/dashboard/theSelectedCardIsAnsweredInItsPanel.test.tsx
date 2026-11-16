// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { NeedsYouHarness, gateItem, waitingItem, waitingList } from '../../test/needsYouHarness'

// ── The selected card is answered in its panel, once ─────────────────────────────────────────────
//
// With a card's panel open, its answers were drawn twice, on the card and in the panel, and either
// answered: two Allows for one ask, side by side. The selected card's answers leave it, so an ask is
// answered in one place, and their space holds the line that says where: "Answer it in the panel",
// or "Decide it in the panel" for a proposal or a note. Every other card keeps its own. Closing the
// panel gives the card its answers back.

const resolveApproval = vi.fn()
const waiting = vi.fn()
const skillProposals = vi.fn()
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => skillProposals(),
    projects: () => Promise.resolve([]),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  resolveApproval.mockResolvedValue({ ok: true })
  waiting.mockResolvedValue(waitingList([waitingItem({ consequence: 'asks', risk: '' }), gateItem()]))
  skillProposals.mockResolvedValue({ proposals: [{ id: 'p1', slug: 'tidy-notes', description: 'Tidy the notes', created_at: '2026-11-13T09:00:00Z' }], lastReview: null })
})

const list = () => screen.getByRole('region', { name: 'Needs you' })
const card = (key: string) => list().querySelector<HTMLElement>(`[data-need-key="${key}"]`)!

describe('with a card’s panel open', () => {
  it('its answers are in the panel alone, and its card says to answer it there', async () => {
    render(<NeedsYouHarness query={{ open: 'waiting:approval:appr-1' }} />)
    const pane = await screen.findByRole('region', { name: 'shell.run' })
    expect(within(pane).getByRole('button', { name: /^Allow/ })).toBeInTheDocument()
    await waitFor(() => expect(within(card('waiting:approval:appr-1')).getByText('Answer it in the panel')).toBeInTheDocument())
    expect(within(card('waiting:approval:appr-1')).queryByRole('button', { name: /^Allow|^Deny/ })).toBeNull()
    expect(within(list()).queryAllByRole('button', { name: /^Allow/ }), 'one Allow for the ask, none on its card').toHaveLength(0)
  })

  it('every other card keeps its own answers', async () => {
    render(<NeedsYouHarness query={{ open: 'waiting:approval:appr-1' }} />)
    await screen.findByRole('region', { name: 'shell.run' })
    await waitFor(() => expect(within(card('waiting:gate:run-77:gate.push:1')).getByRole('button', { name: /^Ship it: / })).toBeInTheDocument())
    expect(within(card('waiting:gate:run-77:gate.push:1')).queryByText('Answer it in the panel')).toBeNull()
  })

  it('a proposal’s card says to decide it there', async () => {
    render(<NeedsYouHarness query={{ open: 'proposal:p1' }} />)
    await screen.findByRole('region', { name: /tidy-notes/ })
    await waitFor(() => expect(within(card('proposal:p1')).getByText('Decide it in the panel')).toBeInTheDocument())
    expect(within(card('proposal:p1')).queryByRole('button', { name: /^Accept/ })).toBeNull()
  })

  it('an answer in the panel is the one answer, and closing the panel gives the card its answers back', async () => {
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-77:gate.push:1' }} />)
    const pane = await screen.findByRole('region', { name: 'Which branch should I push?' })
    expect(within(card('waiting:gate:run-77:gate.push:1')).queryByRole('button', { name: /^Ship it: / })).toBeNull()
    await userEvent.click(within(pane).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(within(card('waiting:gate:run-77:gate.push:1')).getByRole('button', { name: /^Ship it: / })).toBeInTheDocument())
    expect(within(card('waiting:gate:run-77:gate.push:1')).queryByText('Answer it in the panel')).toBeNull()
  })
})
