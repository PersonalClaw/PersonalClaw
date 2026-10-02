import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { TriggerReviewCard } from '../../lib/api'

// ── What a restart leaves for you to decide, on the page the notice sends you to ─────────────────
//
// The boot's "Missed scheduled runs" notice said "Review them and choose what to run now" and
// pointed at `#/triggers`, where there was nothing to review: `missed.resolve_missed`, the
// review's run-now and dismiss, had no caller, and a run a restart cut off was recorded as a
// `timeout` and dropped. So this drives the real `TriggersSection` with the cards
// `GET /api/triggers/review` serves and asserts that a user can see each one and decide it, and
// that the decision is the request the backend records.
//
// The vacuity leg is the last `describe`: with no cards the section must be absent, so the
// positive assertions cannot be met by a page that prints the heading unconditionally.

const HOUR = 3600
const now = () => Date.now() / 1000

const MISSED: TriggerReviewCard = {
  trigger_id: 'clock:hourly', kind: 'missed', count: 3, latest: now() - 2 * HOUR, oldest: now() - 4 * HOUR,
  reason: '', count_is_floor: false, cause: 'stopped', name: 'Hourly digest', open_id: 'schedule:clock:hourly',
}
const INTERRUPTED: TriggerReviewCard = {
  trigger_id: 'clock:backup', kind: 'interrupted', count: 1, latest: now() - HOUR, oldest: now() - HOUR,
  reason: 'Interrupted by a gateway restart: the process running this (pid 4242) is gone. It ran 12s.',
  count_is_floor: false, cause: 'stopped', name: 'Nightly backup', open_id: 'schedule:clock:backup',
}
// A slot the gateway slept through: it was running, so "while PersonalClaw was not running" is false.
const SLEPT: TriggerReviewCard = {
  trigger_id: 'clock:pack', kind: 'missed', count: 1, latest: now() - 9 * 60, oldest: now() - 9 * 60,
  reason: '', count_is_floor: false, cause: 'paused', name: 'Pack the soccer bag', open_id: 'schedule:clock:pack',
}

const { STATE } = vi.hoisted(() => ({
  STATE: {
    cards: [] as unknown[],
    reviewError: null as Error | null,
    decisions: [] as unknown[],
    reviewReads: 0,
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    schedules: () => Promise.resolve({ jobs: [] }),
    hooks: () => Promise.resolve([]),
    storeTriggers: () => Promise.resolve([]),
    callbacks: () => Promise.resolve([]),
    triggerReview: () => {
      STATE.reviewReads += 1
      return STATE.reviewError ? Promise.reject(STATE.reviewError) : Promise.resolve(STATE.cards)
    },
    decideTriggerReview: (body: { trigger_id: string; kind: string; action: string }) => {
      STATE.decisions.push(body)
      // The backend takes the card off the review as it records the decision.
      STATE.cards = STATE.cards.filter((c) => {
        const card = c as TriggerReviewCard
        return !(card.trigger_id === body.trigger_id && card.kind === body.kind)
      })
      return Promise.resolve(body.action === 'dismiss'
        ? { ok: true, outcome: 'skipped_missed' }
        : { ok: true, outcome: 'ran_late', result: 'echoed' })
    },
    actionProviders: () => Promise.resolve([]),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
  },
}))

const { TriggersSection } = await import('./TriggersSection')

const setQuery = vi.fn()
const mount = () =>
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={setQuery} />)

const toasts: string[] = []
window.addEventListener('ne:toast', (e) => toasts.push((e as CustomEvent).detail.message))

beforeEach(() => {
  sessionStorage.clear()
  STATE.cards = []
  STATE.reviewError = null
  STATE.decisions = []
  STATE.reviewReads = 0
  toasts.length = 0
  setQuery.mockClear()
})

const cardFor = async (name: string) => {
  const cards = await screen.findAllByTestId('trigger-review-card')
  const card = cards.find((c) => within(c).queryByText(name))
  if (!card) throw new Error(`no review card for ${name}`)
  return card
}

describe('the review above the Triggers list', () => {
  beforeEach(() => { STATE.cards = [MISSED, INTERRUPTED, SLEPT] })

  it('shows each card with what did not happen and that nothing runs on its own', async () => {
    mount()
    const section = await screen.findByTestId('trigger-review')
    expect(within(section).getByRole('heading', { name: 'Waiting for your decision' })).toBeInTheDocument()
    expect(section).toHaveTextContent('because PersonalClaw was stopped or restarting, or the computer was asleep. None of them runs on its own')
    const missed = await cardFor('Hourly digest')
    expect(missed).toHaveTextContent('Missed 3 scheduled runs while PersonalClaw was not running; the latest was 2h ago. Run now runs it once.')
    const cut = await cardFor('Nightly backup')
    expect(cut).toHaveTextContent('A run was interrupted by a restart 1h ago. It is not run again on its own, because it may already have done part of its work.')
  })

  it('says a slot the computer slept through was missed while it was asleep, not while it was stopped', async () => {
    mount()
    const slept = await cardFor('Pack the soccer bag')
    expect(slept).toHaveTextContent('Missed 1 scheduled run 9m ago, while PersonalClaw was paused or the computer was asleep.')
    expect(slept).not.toHaveTextContent('not running')
  })

  it('Run now sends the decision for that card and the card leaves the review', async () => {
    mount()
    const missed = await cardFor('Hourly digest')
    fireEvent.click(within(missed).getByRole('button', { name: /run now/i }))
    await waitFor(() => expect(STATE.decisions).toEqual([{ trigger_id: 'clock:hourly', kind: 'missed', action: 'run_now' }]))
    await waitFor(() => expect(screen.queryByText('Hourly digest')).not.toBeInTheDocument())
    expect(screen.getByText('Nightly backup')).toBeInTheDocument()
    expect(toasts).toContain('Hourly digest ran now. Its history notes that it ran late.')
  })

  it('Dismiss records that you chose not to run it', async () => {
    mount()
    const cut = await cardFor('Nightly backup')
    fireEvent.click(within(cut).getByRole('button', { name: /dismiss/i }))
    await waitFor(() => expect(STATE.decisions).toEqual([{ trigger_id: 'clock:backup', kind: 'interrupted', action: 'dismiss' }]))
    await waitFor(() => expect(screen.queryByText('Nightly backup')).not.toBeInTheDocument())
    expect(toasts).toContain("Dismissed. Nightly backup's history records that you chose not to run it.")
  })

  it("the card's name opens that automation in the list view", async () => {
    mount()
    const missed = await cardFor('Hourly digest')
    fireEvent.click(within(missed).getByText('Hourly digest'))
    expect(setQuery).toHaveBeenCalledWith({ open: 'schedule:clock:hourly', edit: null, view: 'list' })
  })
})

describe('a review that could not be read', () => {
  it('says so and offers a retry, rather than an empty slot that reads as nothing waiting', async () => {
    STATE.reviewError = new Error('gateway down')
    mount()
    const failed = await screen.findByTestId('trigger-review-error')
    // The reason the read gave, where it gave one; `loadErrorMessage` names what failed otherwise.
    expect(within(failed).getByRole('alert')).toHaveTextContent('gateway down')
    STATE.reviewError = null
    STATE.cards = [MISSED]
    const reads = STATE.reviewReads
    fireEvent.click(within(failed).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(STATE.reviewReads).toBeGreaterThan(reads))
    expect(await cardFor('Hourly digest')).toBeInTheDocument()
  })
})

describe('the vacuity control', () => {
  it('renders no review when nothing is waiting', async () => {
    mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No triggers' })).toBeInTheDocument())
    expect(screen.queryByTestId('trigger-review')).not.toBeInTheDocument()
    expect(screen.queryByTestId('trigger-review-error')).not.toBeInTheDocument()
  })
})
