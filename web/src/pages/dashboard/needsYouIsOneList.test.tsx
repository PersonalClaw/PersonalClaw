// @vitest-environment jsdom
// @module-tag tree-scan
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { join } from 'node:path'
import { resetDataStore } from '../../lib/data'
import type { WsMessage } from '../../lib/useChatSocket'
import { readSource } from '../../test/sourceTree'
import { NeedsYouHarness, waitingItem, waitingList } from '../../test/needsYouHarness'

// ── Needs you is one list (#6149) ───────────────────────────────────────────────────────────────
//
// Needs you is the one place to make the decisions waiting on you: its own page, `#/mission-control`,
// with one h1 and nothing but what waits. Every ask from every source sits in "Waiting on your
// answer", in the order `GET /api/waiting` serves it; then "To review", the proposals and notes
// nothing is blocked on, oldest first, three of them beside the asks. What runs, what finished and
// your chats are Home's and the sidebar's. Every state the page can be in says what it is: loading,
// a failed read, a source it could not read, offline, all clear, past 25, an ask answered
// elsewhere, one that expired, and an `?open=` that is gone. A stalled or blocked loop and the asks
// only the Inbox raised are cards like any other, each with its title, its second line and its way
// to answer.

const waiting = vi.fn()
const inboxOpen = vi.fn()
const skillProposals = vi.fn()
const resolveApproval = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => inboxOpen(),
    skillProposals: () => skillProposals(),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
    projects: () => Promise.resolve([]),
  },
}))

const frames: Array<(m: WsMessage) => void> = []
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { frames.push(onMessage) },
}))

const connection = vi.hoisted(() => ({ state: 'connected' as 'connecting' | 'connected' | 'disconnected', lastReachedAt: null as number | null }))
vi.mock('../../app/gatewayConnection', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useGatewayConnection: () => connection,
}))

const ApiErrorOf = async () => (await import('../../lib/api')).ApiError

/** A skill proposal waiting on a yes or no, made *at* seconds into the day (the list serves ISO). */
const proposal = (id: string, at = 1) => ({ id, slug: `skill-${id}`, description: `Proposal ${id}`, created_at: new Date(Date.UTC(2026, 10, 13, 0, 0, at)).toISOString() })
/** One of your notes, open in the Inbox, made *at* seconds into the same day (the Inbox serves epoch seconds). */
const note = (id: string, at = 1) => ({ id, item_kind: 'user_note', status: 'pending', message: `Note ${id}`, created_at: Date.UTC(2026, 10, 13, 0, 0, at) / 1000 })

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  frames.length = 0
  connection.state = 'connected'
  connection.lastReachedAt = null
  waiting.mockResolvedValue(waitingList())
  inboxOpen.mockResolvedValue([])
  skillProposals.mockResolvedValue({ proposals: [], lastReview: null })
  resolveApproval.mockResolvedValue({ ok: true })
})
afterEach(() => { vi.restoreAllMocks() })

const page = () => screen.getByRole('region', { name: 'Needs you' })
const asks = () => screen.getByRole('region', { name: /^Waiting on your answer/ })
const review = () => screen.getByRole('region', { name: /^To review/ })
/** The first group, once the list has been read and drawn. */
const asksDrawn = () => screen.findByRole('region', { name: /^Waiting on your answer/ })
const cards = (group: HTMLElement) => within(group).getAllByRole('button', { name: /^Show the details of / })

describe('one page, one list', () => {
  it('has one h1, and nothing but what waits: no greeting, View switch, New chat, runs, chats or suggestions', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    render(<NeedsYouHarness />)
    await within(page()).findByRole('group', { name: /Permission needed to run/ })
    const h1s = screen.getAllByRole('heading', { level: 1 })
    expect(h1s.map((h) => h.textContent)).toEqual(['Needs you'])
    expect(screen.queryByRole('heading', { name: /^Good (morning|afternoon|evening)/ })).toBeNull()
    expect(screen.queryByRole('radiogroup', { name: 'View' })).toBeNull()
    expect(screen.queryByRole('button', { name: /New chat/ })).toBeNull()
    expect(screen.queryByRole('textbox', { name: /Start something/ })).toBeNull()
    for (const name of ['Running now', 'Recent and completed', 'Suggestions']) expect(screen.queryByRole('heading', { name })).toBeNull()
    expect(screen.queryByRole('list', { name: 'Your chats' })).toBeNull()
  })

  it('holds two groups: what waits on your answer in the order served, then what there is to review, oldest first', async () => {
    // The broad-reach approval is newer than the question; the list serves it first, and the page
    // never sorts the list again.
    waiting.mockResolvedValue(waitingList([
      waitingItem({ id: 'approval:new', consequence: 'destructive', risk: 'destructive', age_secs: 5,
        brief: { tool: 'rm -rf build', source_label: 'loop “Tidy”', confirms: [] },
        answer: { method: 'POST', path: '/api/approvals/new/{answer}', takes: ['approve', 'reject'], body: {} } }),
      waitingItem({ id: 'question:old', kind: 'question', consequence: 'asks', age_secs: 9000,
        brief: { questions: [{ question: 'Which branch?' }], asked_by: 'Release notes' } }),
    ]))
    inboxOpen.mockResolvedValue([note('n-new', 20)])
    skillProposals.mockResolvedValue({ proposals: [proposal('p-old', 10)], lastReview: null })
    render(<NeedsYouHarness />)
    await within(page()).findByText('Which branch?')
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['Waiting on your answer 2', 'To review 2'])
    const text = asks().textContent ?? ''
    expect(text.indexOf('rm -rf build')).toBeGreaterThan(-1)
    expect(text.indexOf('rm -rf build')).toBeLessThan(text.indexOf('Which branch?'))
    const reviewText = review().textContent ?? ''
    expect(reviewText.indexOf('Skill: skill-p-old')).toBeGreaterThan(-1)
    expect(reviewText.indexOf('Skill: skill-p-old')).toBeLessThan(reviewText.indexOf('Note n-new'))
  })

  it('To review shows three beside what waits, the rest one press away, and all of them alone', async () => {
    skillProposals.mockResolvedValue({ proposals: [1, 2, 3, 4, 5].map((n) => proposal(`p${n}`, n)), lastReview: null })
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    const { unmount } = render(<NeedsYouHarness />)
    await within(page()).findByRole('group', { name: /Permission needed to run/ })
    expect(cards(review())).toHaveLength(3)
    await userEvent.click(within(review()).getByRole('button', { name: 'Show all 5 to review' }))
    expect(cards(review())).toHaveLength(5)
    unmount()
    resetDataStore()
    waiting.mockResolvedValue(waitingList())
    render(<NeedsYouHarness />)
    await waitFor(() => expect(cards(review())).toHaveLength(5))
    expect(within(review()).queryByRole('button', { name: /^Show all/ })).toBeNull()
  })

  it('past 25 waiting, the count is all of it, the first 25 show, and the rest are one press away', async () => {
    waiting.mockResolvedValue(waitingList(Array.from({ length: 30 }, (_, i) => waitingItem({
      id: `question:q${i}`, kind: 'question', consequence: 'asks', brief: { questions: [{ question: `Question ${i}` }] },
    }))))
    render(<NeedsYouHarness />)
    await within(page()).findByText('Question 0')
    expect(within(asks()).getByText('30')).toHaveAttribute('data-group-count')
    expect(within(asks()).queryByText('Question 29')).toBeNull()
    await userEvent.click(within(asks()).getByRole('button', { name: 'Show all 30' }))
    expect(within(asks()).getByText('Question 29')).toBeInTheDocument()
  })
})

describe('every state', () => {
  it('loading: a skeleton that says it is busy, and no all-clear before every read has answered', async () => {
    let answer: (v: unknown) => void = () => {}
    inboxOpen.mockReturnValue(new Promise((r) => { answer = r }))
    render(<NeedsYouHarness />)
    const busy = await within(page()).findByRole('status', { busy: true })
    expect(busy).toHaveAttribute('aria-busy', 'true')
    await waitFor(() => expect(waiting).toHaveBeenCalled())
    expect(screen.queryByText('Nothing is waiting on you.'), 'the waiting list alone is not the whole set').toBeNull()
    await act(async () => { answer([]) })
    expect(await screen.findByText('Nothing is waiting on you.')).toBeInTheDocument()
  })

  it('all clear: says so, centred, with the way to Home, and draws no group', async () => {
    render(<NeedsYouHarness />)
    expect(await screen.findByText('Nothing is waiting on you.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See what your agents are doing on Home' })).toHaveAttribute('href', '#/dashboard')
    expect(screen.queryByRole('region', { name: /^Waiting on your answer|^To review/ })).toBeNull()
  })

  it('failed: a read that fails draws the load error, and never says nothing is waiting', async () => {
    waiting.mockRejectedValue(new Error('gateway unreachable'))
    render(<NeedsYouHarness />)
    expect(await within(page()).findByRole('alert')).toHaveTextContent(/gateway unreachable/)
    expect(screen.queryByText('Nothing is waiting on you.')).toBeNull()
  })

  it('recovered: Try again reads it again, and the list comes back', async () => {
    waiting.mockRejectedValueOnce(new Error('gateway unreachable'))
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    render(<NeedsYouHarness />)
    await userEvent.click(within(await within(page()).findByRole('alert')).getByRole('button', { name: /Try again|Retry/ }))
    // Its press was in the list, so the list holds still, but it held no card: the read shows at once.
    expect(await within(page()).findByRole('group', { name: /Permission needed to run/ })).toBeInTheDocument()
  })

  it('a source the gateway could not read is named, with nothing else said to wait, and no all-clear', async () => {
    waiting.mockResolvedValue(waitingList([], ['gate', 'approval']))
    render(<NeedsYouHarness />)
    expect(await within(await asksDrawn()).findByText(/Could not read workflow runs, approvals: what they hold may be waiting on you/)).toBeInTheDocument()
    expect(within(asks()).getByText('Nothing else is waiting on you.')).toBeInTheDocument()
    expect(screen.queryByText('Nothing is waiting on you.')).toBeNull()
    // A count that leaves out what could not be read is no count.
    expect(within(asks()).queryByText(/\d/, { selector: '[data-group-count]' })).toBeNull()
  })

  it('beside a listed card, an unread source is named and nothing else is said to wait', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem()], ['gate']))
    render(<NeedsYouHarness />)
    expect(await within(await asksDrawn()).findByText(/Could not read workflow runs: what they hold may be waiting on you/)).toBeInTheDocument()
    expect(within(asks()).getByRole('group', { name: /Permission needed to run/ })).toBeInTheDocument()
    expect(within(asks()).queryByText('Nothing else is waiting on you.')).toBeNull()
  })

  it('offline: says what it shows was loaded before, and turns every answer off with why', async () => {
    connection.state = 'disconnected'
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    render(<NeedsYouHarness />)
    expect(await within(page()).findByText(/^Can't reach PersonalClaw\. Showing what was loaded/)).toBeInTheDocument()
    const allow = within(page()).getByRole('button', { name: /^Allow/ })
    expect(allow).toBeDisabled()
  })

  it('concurrent: an ask answered somewhere else says so, and offers nothing to retry', async () => {
    const ApiError = await ApiErrorOf()
    resolveApproval.mockRejectedValue(new ApiError('not found', 404))
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(page()).findByRole('button', { name: /^Allow/ }))
    expect(await within(page()).findByText('It is no longer waiting: it was answered or ended somewhere else.')).toBeInTheDocument()
    expect(within(page()).queryByRole('button', { name: /^Allow/ })).toBeNull()
  })

  it('expired: an ask held past its expiry can no longer be answered here, and says when it stopped', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'question:late', kind: 'question', consequence: 'asks', expires_at: '2020-01-01T00:00:00Z',
      brief: { questions: [{ question: 'Too late?' }] },
    }), waitingItem({ id: 'approval:late', expires_at: '2020-01-01T00:00:00Z' })]))
    render(<NeedsYouHarness />)
    const said = await within(page()).findAllByText(/stopped waiting at .+, so it can no longer be answered here/)
    expect(said).toHaveLength(2)
    expect(within(page()).queryByRole('button', { name: /^Allow|^Deny/ })).toBeNull()
  })

  it('a gone ?open= says it is no longer waiting, in the panel', async () => {
    render(<NeedsYouHarness query={{ open: 'waiting:approval:gone' }} />)
    const pane = await screen.findByRole('region', { name: 'This is no longer waiting on you' })
    expect(within(pane).getByText(/It was answered or ended somewhere else/)).toBeInTheDocument()
  })

  it('a frame that something waits reads the list again, and the new ask shows', async () => {
    render(<NeedsYouHarness />)
    await screen.findByText('Nothing is waiting on you.')
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      act(() => { for (const f of [...frames]) f({ type: 'waiting_changed', data: {} } as WsMessage) })
      await act(async () => { vi.advanceTimersByTime(350) })
    } finally {
      vi.useRealTimers()
    }
    expect(await within(page()).findByRole('group', { name: /Permission needed to run/ })).toBeInTheDocument()
  })
})

describe('the asks only some sources raised are cards like any other', () => {
  /** A loop's ask: its question, or its stall or its block with the reason. */
  const loopAsk = (status: string, why: string) => waitingItem({
    id: `loop_question:${status}`, kind: 'loop_question', work: `loop:${status}`, session: '', consequence: 'asks', risk: '',
    brief: { name: 'Docs', question: '', status, why },
    answer: { method: 'POST', path: `/api/loops/${status}/nudge`, takes: ['text'], body: {} }, route: `#/loops/${status}`,
  })
  /** An ask only the Inbox raised, served as the waiting list serves it. */
  const inboxAsk = (kind: string, brief: Record<string, unknown>) => waitingItem({
    id: `${kind}:i1`, kind: kind as never, work: '', session: '', consequence: 'asks', risk: '', brief,
    answer: { method: 'POST', path: '', takes: [], body: {} }, route: '#/inbox?open=i1',
  })

  it.each([
    ['stagnant', 'No new findings in 3 cycles', 'Docs has stalled'],
    ['blocked', 'The tests need a key', 'Docs is blocked'],
  ])('a %s loop says so, why, and takes its line on the card', async (status, why, title) => {
    waiting.mockResolvedValue(waitingList([loopAsk(status, why)]))
    render(<NeedsYouHarness />)
    const card = (await within(await asksDrawn()).findByRole('button', { name: new RegExp(`^Show the details of ${title}`) })).parentElement!
    expect(within(card).getByText(title)).toBeInTheDocument()
    expect(within(card).getByText(why)).toBeInTheDocument()
    expect(within(card).getByText('Loop · Docs')).toBeInTheDocument()
    expect(within(card).getByRole('textbox', { name: new RegExp(`^Reply to ${title}`) })).toBeInTheDocument()
  })

  it('a folder to trust names the folder from home, says why, and is trusted on its card', async () => {
    waiting.mockResolvedValue(waitingList([inboxAsk('folder_trust', { dir: '/Users/sam/code/garden', body: 'A chat wants to work in this folder.' })]))
    render(<NeedsYouHarness />)
    const card = (await within(await asksDrawn()).findByRole('button', { name: /^Show the details of Trust ~\/code\/garden\?/ })).parentElement!
    expect(within(card).getByText('Folder trust')).toBeInTheDocument()
    expect(within(card).getByText('A chat wants to work in this folder.')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Trust this folder' })).toBeInTheDocument()
    expect(within(card).queryByRole('link', { name: /^Open it in the Inbox/ })).toBeNull()
  })

  it.each([
    ['held_action', { title: 'Send the invoice to Acme', body: 'Held until you say yes.', action_type: 'email' }, 'Send the invoice to Acme', 'Held until you say yes.', 'Held for your OK · email'],
    ['declined_wait', { body: 'Release — waits after you declined its push' }, 'Release waits after you declined one of its steps', 'waits after you declined its push', 'Workflow run'],
    ['escalated_run', { body: 'Release — investigate escalated', loop_kind: 'goal' }, 'Release stopped before it finished', 'investigate escalated', 'Loop'],
  ])('%s says what it is, what happened, and opens in the Inbox, where it is answered', async (kind, brief, title, subtitle, raisedBy) => {
    waiting.mockResolvedValue(waitingList([inboxAsk(kind, brief)]))
    render(<NeedsYouHarness />)
    const card = (await within(await asksDrawn()).findByRole('button', { name: new RegExp(`^Show the details of ${title}`) })).parentElement!
    expect(within(card).getByText(title)).toBeInTheDocument()
    expect(within(card).getByText(subtitle)).toBeInTheDocument()
    expect(within(card).getByText(raisedBy)).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: new RegExp(`^Open it in the Inbox: ${title} — `) })).toHaveAttribute('href', '#/inbox?open=i1')
  })

  it('a browser grant, answered here, draws no link to where it asks: that is this page', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'browse_grant:g1', kind: 'browse_grant', work: '', session: '', consequence: 'asks', risk: '',
      brief: { task: 'Find the train times', scope: ['trains.example'] },
      answer: { method: 'POST', path: '/api/browse/grants/g1/{answer}', takes: ['allow', 'refuse'], body: {} }, route: '#/mission-control',
    })]))
    render(<NeedsYouHarness query={{ open: 'waiting:browse_grant:g1' }} />)
    const pane = await screen.findByRole('region', { name: 'Find the train times' })
    expect(within(pane).queryByRole('link', { name: 'Open where it asks' })).toBeNull()
    expect(screen.queryByRole('link', { name: /where it asks/ })).toBeNull()
  })
})

describe('the route and the preset', () => {
  const app = () => readSource(join(process.cwd(), 'src/app/App.tsx'))

  it('#/mission-control is Needs you, and the palette reaches it by name', () => {
    const src = app()
    expect(src).toMatch(/lazy\(\(\) => import\('\.\.\/pages\/dashboard\/NeedsYouPage'\)/)
    expect(src).toMatch(/case 'mission-control': return <NeedsYouPage \{\.\.\.r\} \/>/)
    expect(src).toMatch(/const ROUTABLE = new Set\(\[[^\]]*'mission-control'/)
    expect(src).toMatch(/id: 'go:mission-control', label: 'Needs you'[^\n]*navigate\('mission-control'\)/)
  })

  it('GROUP_REFS, the one reconciliation point with views_store, holds the one group', async () => {
    const { GROUP_REFS, MISSION_CONTROL_VIEW_ID } = await import('./NeedsYouPage')
    expect(GROUP_REFS).toEqual({ 'needs-you': 'core:group-needs-you' })
    expect(MISSION_CONTROL_VIEW_ID).toBe('mission-control')
  })
})
