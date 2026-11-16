// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { resetDataStore } from '../../lib/data'
import { ApiError, type InboxItem, type LearningProposalReview } from '../../lib/api'
import { NeedsYouHarness, waitingList } from '../../test/needsYouHarness'

// ── Needs you's panel is the proposal ───────────────────────────────────────────────────────────
//
// The panel beside a proposal's card is the proposal itself, drawn from its review: the thing it
// proposes, why, where it came from up to when it was proposed, its facts and one way out, and a
// decide bar that says what Accept does. It never draws the card again: the tag row is its header,
// the headline its heading, and no part of it is a region of its own.

const FIXTURE = JSON.parse(readFileSync(join(process.cwd(), 'src/test/fixtures/needsYouKinds.json'), 'utf8')) as {
  learning: { variant: string; card: Record<string, unknown> }[]
}

const waiting = vi.fn()
const inboxOpen = vi.fn()
const learningProposalReview = vi.fn()
const learningProposals = vi.fn()
const chatSessionDetail = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => inboxOpen(),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    status: () => Promise.resolve({}),
    running: () => Promise.resolve({ running: [], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    learningProposalReview: (...a: unknown[]) => learningProposalReview(...a),
    learningProposals: (...a: unknown[]) => learningProposals(...a),
    chatSessionDetail: (...a: unknown[]) => chatSessionDetail(...a),
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
  waiting.mockResolvedValue(waitingList())
  learningProposals.mockResolvedValue({ proposals: [] })
  chatSessionDetail.mockResolvedValue({ key: 'chat-7', title: '', messages: [], run_state: 'idle' })
})

const cardOf = (variant: string) => FIXTURE.learning.find((e) => e.variant === variant)!.card

function row(variant: string, pid = `lp-${variant}`): InboxItem {
  return {
    id: `row-${pid}`, item_kind: 'proposal', status: 'pending', message: 'A proposal', context_summary: '', created_at: 1,
    sender_name: 'learning', channel_name: 'learning', refs: { learning_proposal: pid, card: cardOf(variant) },
  } as unknown as InboxItem
}

function review(variant: string, over: Partial<LearningProposalReview> = {}): LearningProposalReview {
  const card = cardOf(variant) as { headline: string; accept: LearningProposalReview['accept'] }
  return {
    id: `lp-${variant}`, kind: 'template', variant, status: 'pending',
    created_at: '2026-11-14T09:00:00Z', updated_at: '2026-11-14T09:00:00Z',
    headline: card.headline, detail: '', name: '', label: '', why: '', content: { type: 'markdown', text: 'x' },
    accept: card.accept, reject: { verb: 'Reject', sentence: '' }, session_key: '', source_gone: false, runs: [],
    ...over,
  }
}

/** The pane opened on *variant*'s row by its address. */
async function open(variant: string, r: LearningProposalReview) {
  inboxOpen.mockResolvedValue([row(variant)])
  learningProposalReview.mockResolvedValue(r)
  render(<NeedsYouHarness query={{ open: `note:row-lp-${variant}` }} />)
  const headline = String(cardOf(variant).headline)
  return screen.findByRole('region', { name: (name) => name.startsWith(headline.slice(0, 20)) })
}

describe('a template’s panel is the template', () => {
  const STEPS = ['Fetch the changes merged in {{week}}', 'Group the result by project', 'Draft the status email']

  it('its steps, why the agent proposed it, its facts, a way out, and a bar that says what Accept does', async () => {
    const pane = await open('template.named', review('template.named', {
      content: { type: 'steps', steps: STEPS }, why: 'You send it every Friday by hand', runs: ['run-0a1b2c3d4e'],
    }))
    const part = (await within(pane).findByRole('heading', { level: 3, name: 'The steps' })).closest('section')!
    expect([...part.querySelectorAll('ol > li')].map((li) => li.textContent)).toEqual(STEPS)
    expect(within(pane).getByText('The agent says: You send it every Friday by hand')).toBeInTheDocument()
    expect(within(pane).getByRole('link', { name: 'Run run-0a1b' })).toHaveAttribute('href', '#/workflows/runs/run-0a1b2c3d4e')
    expect(within(pane).getByRole('link', { name: 'Open in Learning' })).toHaveAttribute('href', '#/learning?open=lp-template.named')
    const bar = pane.querySelector('[data-decide-bar]') as HTMLElement
    expect(within(bar).getByText('Saves the template weekly-status-report to Workflows')).toBeInTheDocument()
    expect(within(bar).getByRole('button', { name: /^Save template: / })).toBeInTheDocument()
    // The bar is the body's last part.
    expect(bar.parentElement!.lastElementChild).toBe(bar)
  })

  it('the pane is the one region, and its header is the tag row, not the card again', async () => {
    const pane = await open('template.named', review('template.named', { content: { type: 'steps', steps: STEPS } }))
    await within(pane).findByRole('heading', { level: 3, name: 'The steps' })
    expect(within(pane).queryAllByRole('region')).toEqual([])
    expect(within(pane).getAllByText('New template')).toHaveLength(1)
    expect(within(pane).queryByText(/^Asked by/)).toBeNull()
  })

  it('where it came from is the chat up to when it was proposed: the 4 lines at or before it, not its newest', async () => {
    const at = (h: number) => `2026-11-14T0${h}:00:00Z`
    chatSessionDetail.mockResolvedValue({ key: 'chat-7', title: '', run_state: 'idle', messages: [
      { role: 'user', content: 'one', ts: at(4) }, { role: 'assistant', content: 'two', ts: at(5) },
      { role: 'user', content: 'three', ts: at(6) }, { role: 'assistant', content: 'four', ts: at(7) },
      { role: 'user', content: 'five', ts: at(8) }, { role: 'assistant', content: 'six', ts: at(9) },
      { role: 'user', content: 'after it', ts: '2026-11-14T10:00:00Z' },
    ] })
    const pane = await open('template.named', review('template.named', {
      content: { type: 'steps', steps: STEPS }, session_key: 'dashboard:chat-7',
    }))
    await within(pane).findByText('six')
    expect(within(pane).queryByText('after it')).toBeNull()
    expect(within(pane).queryByText('two')).toBeNull()
    expect(within(pane).getByText('three')).toBeInTheDocument()
    expect(within(pane).getByRole('link', { name: 'Open the chat' })).toHaveAttribute('href', '#/chat/chat-7')
  })

  it('a chat deleted since says so and shows no lines', async () => {
    const pane = await open('template.named', review('template.named', {
      content: { type: 'steps', steps: STEPS }, session_key: 'dashboard:chat-7', source_gone: true,
    }))
    expect(await within(pane).findByText('The chat it came from was deleted.')).toBeInTheDocument()
    expect(chatSessionDetail).not.toHaveBeenCalled()
  })

  it('an empty body says to reject it', async () => {
    const pane = await open('template.named', review('template.named', { content: { type: 'steps', steps: [] } }))
    expect(await within(pane).findByText('The proposer left this empty; reject it.')).toBeInTheDocument()
  })
})

describe('what other proposals’ panels show', () => {
  it('a failed run’s lesson: what went wrong, and the error as the run recorded it, folding past 40 lines', async () => {
    const error = Array.from({ length: 45 }, (_, i) => `line ${i + 1}`).join('\n')
    const pane = await open('lesson.failed_run', review('lesson.failed_run', {
      content: { type: 'lesson', lesson: 'When fetch-metrics gets an empty result, stop and say so.', capsule: '', error },
    }))
    expect(await within(pane).findByText('When fetch-metrics gets an empty result, stop and say so.')).toBeInTheDocument()
    const shown = within(pane).getByLabelText('The error')
    expect(shown.textContent!.split('\n')).toHaveLength(40)
    await userEvent.click(within(pane).getByRole('button', { name: 'Show all 45 lines' }))
    expect(within(pane).getByLabelText('The error').textContent!.split('\n')).toHaveLength(45)
    // A record's two verbs are both secondary: nothing here is a primary.
    const bar = pane.querySelector('[data-decide-bar]') as HTMLElement
    expect(within(bar).getByText(/^Accepting records that you agree/)).toBeInTheDocument()
  })

  it('a lesson conflict: yours beside the learned one', async () => {
    const pane = await open('lesson.conflict', review('lesson.conflict', {
      content: { type: 'choice', yours: ['Use tabs in Makefiles'], memory: 'Home Lab’s memory', learned: 'Indent Makefiles with spaces',
        negative: '', who: 'learned from your correction in a chat' },
    }))
    expect(await within(pane).findByRole('heading', { level: 4, name: 'Yours, in Home Lab’s memory' })).toBeInTheDocument()
    expect(within(pane).getByText('Use tabs in Makefiles')).toBeInTheDocument()
    expect(within(pane).getByRole('heading', { level: 4, name: 'Learned, learned from your correction in a chat' })).toBeInTheDocument()
  })

  it('captures: a cut one reads whole on "Show all of it", and one no longer kept says so', async () => {
    const pane = await open('lesson.captures', review('lesson.captures', {
      content: { type: 'lessons', total: 2, captures: [
        { uid: 'c1', text: 'Prefers squash merges', cut: true, day: '2026-11-13', cadence: 'nightly', kind: 'preference', kept: true },
        { uid: 'c2', text: 'Gone one', cut: false, day: '', cadence: '', kind: '', kept: false },
      ] },
    }))
    expect(await within(pane).findByText('This capture is no longer kept')).toBeInTheDocument()
    learningProposalReview.mockResolvedValueOnce(review('lesson.captures', {
      content: { type: 'lessons', total: 2, captures: [{ uid: 'c1', text: 'Prefers squash merges for doc-only changes, every time', cut: false, kept: true }] },
    }))
    await userEvent.click(within(pane).getByRole('button', { name: 'Show all of it' }))
    expect(await within(pane).findByText('Prefers squash merges for doc-only changes, every time')).toBeInTheDocument()
    expect(learningProposalReview).toHaveBeenLastCalledWith('lp-lesson.captures', 'c1')
  })
})

describe('the panel’s states, in words', () => {
  it('learning off: its sentence, how to turn it on, no verbs', async () => {
    inboxOpen.mockResolvedValue([row('template.named')])
    learningProposalReview.mockRejectedValue(new ApiError('Learning is off.', 404, 'learning_disabled'))
    render(<NeedsYouHarness query={{ open: 'note:row-lp-template.named' }} />)
    const pane = await screen.findByRole('region', { name: /^Draft the Friday/ })
    expect(await within(pane).findByText(/^Learning is off, so this can't be decided here\./)).toBeInTheDocument()
    expect(within(pane).getByRole('link', { name: 'Turn learning on in Learning' })).toHaveAttribute('href', '#/learning')
    expect(within(pane).queryByRole('button', { name: /^Save template/ })).toBeNull()
  })

  it('gone: decided or ended somewhere else', async () => {
    inboxOpen.mockResolvedValue([row('template.named')])
    learningProposalReview.mockRejectedValue(new ApiError('Gone.', 404, 'learning_proposal_gone'))
    render(<NeedsYouHarness query={{ open: 'note:row-lp-template.named' }} />)
    const pane = await screen.findByRole('region', { name: /^Draft the Friday/ })
    expect(await within(pane).findByText('It is no longer waiting: it was decided or ended somewhere else.')).toBeInTheDocument()
  })

  it('a failed read says so with Try again, and the bar still answers', async () => {
    inboxOpen.mockResolvedValue([row('template.named')])
    learningProposalReview.mockRejectedValue(new Error('the gateway is restarting'))
    render(<NeedsYouHarness query={{ open: 'note:row-lp-template.named' }} />)
    const pane = await screen.findByRole('region', { name: /^Draft the Friday/ })
    expect(await within(pane).findByRole('button', { name: /Try again|Retry/ })).toBeInTheDocument()
    await waitFor(() => expect(pane.querySelector('[data-decide-bar]')).not.toBeNull())
  })
})
