// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { NeedsYouHarness, gateItem, waitingItem, waitingList } from '../../test/needsYouHarness'

// ── What waits: its panel shows what its card can't, once ──────────────────────────────────────
//
// An ask's panel never draws its card again. The pane's header is the card's tag row and its
// heading the ask's headline, so the body holds the ask's own lines, where it came from, its facts
// and one way out, and its answers in the decide bar at its foot. A hosted approval announces
// nothing (no alert), draws no header row or "From …" line, and draws its command once.

const waiting = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    status: () => Promise.resolve({}),
    running: () => Promise.resolve({ running: [], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    chatSessionDetail: () => Promise.resolve({ key: 'chat-1', title: '', run_state: 'idle', messages: [] }),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
})

/** The detail pane: the region its heading names. */
const pane = async () => {
  const headings = await screen.findAllByRole('heading', { level: 2 })
  const heading = headings.find((h) => h.getAttribute('data-type') === 'title-l')!
  return heading.closest('[role="region"]') as HTMLElement
}

describe('a hosted approval in its panel', () => {
  it('mounts no alert, draws no header row or "From …" line, and draws its command once', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({ expires_at: new Date(Date.now() + 60 * 60_000).toISOString() })]))
    render(<NeedsYouHarness query={{ open: 'waiting:approval:appr-1' }} />)
    const p = await pane()
    const approval = await within(p).findByRole('group', { name: 'Permission needed to run shell.run' })
    expect(approval.getAttribute('data-approval-form')).toBe('hosted')
    expect(within(p).queryAllByRole('alert')).toEqual([])
    expect(within(approval).queryByText('Permission needed')).toBeNull()
    expect(within(p).queryByText(/^From chat/)).toBeNull()
    expect(within(approval).getAllByText(/^shell\.run\(/)).toHaveLength(1)
    // Its own Allow and Deny, and no second bar.
    expect(within(approval).getByRole('button', { name: /^Allow/ })).toBeInTheDocument()
    expect(p.querySelectorAll('[data-decide-bar]')).toHaveLength(0)
  })

  it('the header is the tag row: "Permission needed" once, there', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    render(<NeedsYouHarness query={{ open: 'waiting:approval:appr-1' }} />)
    const p = await pane()
    await within(p).findByRole('group', { name: 'Permission needed to run shell.run' })
    expect(within(p).getAllByText('Permission needed')).toHaveLength(1)
  })
})

describe('any other ask’s panel', () => {
  it('its lines, where it came from, its facts and a way out, then its answers in the bar at its foot', async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'choice', prompt: 'Which branch should I push?\n\nThe release branch is cut.', choices: ['Ship it', 'Hold'] }, {
      asked_at: '2026-11-15T10:00:00Z',
    })]))
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-77:gate.push:1' }} />)
    const p = await pane()
    const bar = await within(p).findByRole('button', { name: /^Ship it: / }).then((b) => b.closest('[data-decide-bar]') as HTMLElement)
    expect(bar).not.toBeNull()
    expect(bar.parentElement!.lastElementChild).toBe(bar)
    expect(within(p).getByText('The release branch is cut.')).toBeInTheDocument()
    expect(within(p).getByText('Asked')).toBeInTheDocument()
    expect(within(p).getByRole('link', { name: 'Open where it asks' })).toHaveAttribute('href', '#/workflows/runs/run-77')
    // The card is not drawn again: no second tag row, no "Asked by".
    expect(within(p).queryByText(/^Asked by/)).toBeNull()
    expect(within(p).getAllByText('Workflow')).toHaveLength(1)
  })

  it('a chat’s question takes your own words in the panel, named for it, with no "Escape closes" promise', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'question:q1', kind: 'question', consequence: 'asks', brief: { questions: [{ question: 'Which version?' }], asked_by: 'Release notes' },
      answer: { method: 'POST', path: '/api/chat/sessions/chat-1/questions/q1/answer', takes: ['answers', 'skip'], body: {} },
    })]))
    render(<NeedsYouHarness query={{ open: 'waiting:question:q1' }} />)
    const p = await pane()
    const field = await within(p).findByRole('textbox', { name: /^Reply to Which version\?/ })
    expect(field.getAttribute('placeholder')).toBe('Reply in one line: Enter sends')
  })
})
