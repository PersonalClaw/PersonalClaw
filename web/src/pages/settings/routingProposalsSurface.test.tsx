import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RoutingPanel } from './RoutingPanel'

// ── The propose-don't-write queue needs a SURFACE, not just an endpoint ──────────────────────────
//
// MRT-5's proposal queue shipped complete and inert: `accept`/`reject`/`pending` were library
// functions reachable from one Python test file, `model_telemetry.py` registered only the policy
// GET/PUT, and this panel had no proposals surface at all. A queue a user cannot see or decide is
// not a propose-don't-write mechanism — it is a mechanism with no surface, and the whole product
// claim ("the machine proposes, you decide") is unverifiable from the app.
//
// What these rails assert, beyond "it renders":
//
//   · the DECISION reaches the API — a row whose Apply button called nothing would still look
//     right, so each test asserts the client call, and the list reloads afterwards (the server is
//     the authority on what the table now says);
//   · a REFUSAL is reported. Accept answers 200 with `applied:false` when the cell's order was set
//     by hand; a surface that treated that as success would tell the user their table changed when
//     it did not;
//   · the empty state still teaches the property. The section renders with nothing pending, because
//     one that appeared only when there was something to accept would never explain that routing
//     proposes rather than rewrites;
//   · the outcome is ANNOUNCED. The row disappears on reload, so the status line is the only
//     confirmation any user gets — and it is always mounted, empty at rest.

const PROPOSAL = {
  id: 'rp-abc123',
  use_case: 'reasoning',
  query_class: 'summarize',
  current: ['cloudy:big', 'ollama:small'],
  proposed: ['ollama:small', 'cloudy:big'],
  created_at: '2026-08-25T00:00:00Z',
  status: 'pending',
  evidence: {
    n: { 'cloudy:big': 20, 'ollama:small': 22 },
    scores: { 'cloudy:big': 0.4, 'ollama:small': 0.95 },
    min_samples: 5,
    hysteresis: 0.05,
    p50_delta_ms: -780,
    cost_delta_usd: -0.004,
    sample_audit_ids: ['aud-l', 'aud-c'],
  },
}

const routingProposals = vi.fn(() => Promise.resolve({ count: 1, proposals: [PROPOSAL] }))
const acceptRoutingProposal = vi.fn((_id: string) =>
  Promise.resolve({ ok: true, applied: true, id: PROPOSAL.id }),
)
const rejectRoutingProposal = vi.fn((_id: string) => Promise.resolve({}))

vi.mock('../../lib/api', () => ({
  api: {
    routingPolicy: () => Promise.resolve({ enabled: true, use_cases: [] }),
    setRoutingPolicy: () => Promise.resolve({}),
    modelsTelemetry: () => Promise.resolve({ rows: [] }),
    routingProposals: () => routingProposals(),
    acceptRoutingProposal: (id: string) => acceptRoutingProposal(id),
    rejectRoutingProposal: (id: string) => rejectRoutingProposal(id),
  },
}))
vi.mock('../../lib/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [d, setD] = require('react').useState(undefined)
    require('react').useEffect(() => { void fn().then(setD) }, [])
    return { data: d, refresh: () => {} }
  },
}))

function renderPanel() {
  return render(<RoutingPanel query={{ uc: 'reasoning', qc: 'summarize' }} setQuery={() => {}} />)
}

describe('the routing proposal queue is reviewable in the Routing tab', () => {
  beforeEach(() => {
    routingProposals.mockClear()
    routingProposals.mockImplementation(() => Promise.resolve({ count: 1, proposals: [PROPOSAL] }))
    acceptRoutingProposal.mockClear()
    acceptRoutingProposal.mockImplementation(() =>
      Promise.resolve({ ok: true, applied: true, id: PROPOSAL.id }),
    )
    rejectRoutingProposal.mockClear()
    rejectRoutingProposal.mockImplementation(() => Promise.resolve({}))
  })

  it('names the section, the count, and what the proposal would do', async () => {
    renderPanel()
    expect(await screen.findByRole('heading', { name: /Proposed routing changes/ })).toBeTruthy()
    expect(screen.getByText(/1 proposed change waiting on you/)).toBeTruthy()
    // The sentence carries both refs and the bucket, so the row is legible without expanding it.
    const row = screen.getByRole('listitem')
    expect(row.textContent).toContain('reasoning / summarize')
    expect(row.textContent).toContain('ollama:small')
    expect(row.textContent).toContain('cloudy:big')
  })

  it('shows the evidence in the same units as the table above', async () => {
    renderPanel()
    const row = await waitFor(() => screen.getByRole('listitem'))
    // Scores as percents, sample counts, and the deltas WORDED — a bare "-780" would need a legend.
    expect(row.textContent).toContain('scored 95% vs 40%')
    expect(row.textContent).toContain('over 22 and 20 calls')
    expect(row.textContent).toContain('780ms faster')
    expect(row.textContent).toContain('cheaper per call')
  })

  it('applying calls accept and reloads the queue', async () => {
    renderPanel()
    const apply = await screen.findByRole('button', { name: /^Apply/ })
    await userEvent.click(apply)
    await waitFor(() => expect(acceptRoutingProposal).toHaveBeenCalledWith('rp-abc123'))
    // Two reads: the mount, and the reload after the decision. Without the reload the surface would
    // keep offering a decision that has already been made.
    await waitFor(() => expect(routingProposals.mock.calls.length).toBeGreaterThanOrEqual(2))
    expect(screen.getByRole('status').textContent).toMatch(/Applied/)
  })

  it('reports a refusal instead of claiming the table changed', async () => {
    acceptRoutingProposal.mockImplementation(() =>
      Promise.resolve({
        ok: true, applied: false, id: PROPOSAL.id,
        reason: 'a hand-set order owns this cell; set it by hand to change it',
      }),
    )
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /^Apply/ }))
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toMatch(/Not applied — a hand-set order/),
    )
  })

  it('dismissing calls reject and says the suggestion will not come back', async () => {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /^Dismiss/ }))
    await waitFor(() => expect(rejectRoutingProposal).toHaveBeenCalledWith('rp-abc123'))
    expect(acceptRoutingProposal).not.toHaveBeenCalled()
    expect(screen.getByRole('status').textContent).toMatch(/Dismissed/)
  })

  it('a failed decision interrupts and says nothing changed', async () => {
    acceptRoutingProposal.mockImplementation(() => Promise.reject(new Error('boom')))
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /^Apply/ }))
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toMatch(/nothing changed/),
    )
  })

  it('an empty queue still explains that routing proposes rather than rewrites', async () => {
    routingProposals.mockImplementation(() => Promise.resolve({ count: 0, proposals: [] }))
    renderPanel()
    expect(await screen.findByText(/never rewrites your table on its own/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Apply/ })).toBeNull()
  })

  it('an unreadable queue says so without blanking the section', async () => {
    routingProposals.mockImplementation(() => Promise.reject(new Error('nope')))
    renderPanel()
    expect(await screen.findByText(/Couldn't read the proposal queue/)).toBeTruthy()
    expect(screen.getByRole('heading', { name: /Proposed routing changes/ })).toBeTruthy()
  })

  it('the status region is mounted and empty before any decision', async () => {
    renderPanel()
    await waitFor(() => screen.getByRole('listitem'))
    // A live region created at the moment its text appears is not reliably announced, so it exists
    // from the first render with no content.
    expect(screen.getByRole('status').textContent).toBe('')
  })
})
