import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TRIGGER_PRESETS } from './triggerPresets'

// ── "On a fresh dev home the Triggers empty state shows preset cards" ──────────────────
//
// The done_when clause, driven through the REAL surface rather than through the primitive:
// `TriggersSection` at `#/triggers` with every list endpoint answering empty — which is
// exactly what a fresh home returns. Then the on-ramp itself: clicking a card must
// navigate to the seeded create URL, because the seed rides in the URL (like `kind` and
// `pattern` already do) so a seeded flow is deep-linkable and survives a reload.
//
// The second half is the guardrail: a list that is merely FILTERED to nothing must NOT
// show presets. Offering a starter to someone with a full library who mistyped a search
// answers a question they did not ask — the `emptyStateNoMatch` contract.

const { EMPTY } = vi.hoisted(() => ({ EMPTY: [] as unknown[] }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    schedules: () => Promise.resolve({ jobs: EMPTY }),
    hooks: () => Promise.resolve(EMPTY),
    storeTriggers: () => Promise.resolve(EMPTY),
    eventTriggers: () => Promise.resolve(EMPTY),
    actionProviders: () => Promise.resolve(EMPTY),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
  },
}))

const { TriggersSection } = await import('./TriggersSection')

const mount = (query: Record<string, string> = {}) => {
  const navigate = vi.fn()
  const view = render(
    <TriggersSection sub="" navigate={navigate} navEpoch={0} query={query} setQuery={() => {}} />,
  )
  return { ...view, navigate }
}

beforeEach(() => { sessionStorage.clear() })

describe('the Triggers empty state on a fresh home', () => {
  it('shows a card per preset instead of a bare "New trigger" button', async () => {
    mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No triggers' })).toBeInTheDocument())
    for (const p of TRIGGER_PRESETS) {
      const card = screen.getByRole('button', { name: `${p.title} — ${p.summary}` })
      expect(card, `${p.id} card`).toBeInTheDocument()
      expect(screen.getByText(p.description)).toBeInTheDocument()
    }
  })

  it('keeps the expert blank path on the empty state AND in the top bar', async () => {
    mount()
    await waitFor(() => expect(screen.getByRole('button', { name: /Start from scratch/ })).toBeInTheDocument())
    // The top bar's action is untouched; `HeaderActions` renders it at several responsive
    // tiers, so this is a node count, not a control count.
    expect(screen.getAllByRole('button', { name: /New trigger/ }).length).toBeGreaterThan(0)
  })

  it('navigates a picked preset into the SEEDED create URL', async () => {
    const { navigate } = mount()
    const card = await waitFor(() => screen.getByRole('button', { name: /^Morning briefing/ }))
    await userEvent.click(card)
    expect(navigate).toHaveBeenCalledWith('triggers/new?kind=schedule&preset=morning-briefing')
  })

  it('navigates the blank path to the create URL with NO preset', async () => {
    const { navigate } = mount()
    const blank = await waitFor(() => screen.getByRole('button', { name: /Start from scratch/ }))
    await userEvent.click(blank)
    expect(navigate).toHaveBeenCalledWith('triggers/new')
  })

  it('offers no presets when the list is merely filtered to nothing', async () => {
    mount({ q: 'nothing matches this' })
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No matching triggers' })).toBeInTheDocument())
    for (const p of TRIGGER_PRESETS)
      expect(screen.queryByRole('button', { name: new RegExp(`^${p.title}`) })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Start from scratch/ })).not.toBeInTheDocument()
    expect(screen.getByText('Try a different search term.')).toBeInTheDocument()
  })
})
