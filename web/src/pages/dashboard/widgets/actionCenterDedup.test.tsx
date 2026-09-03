import { describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'

// ── One proposal, ONE row; the inbox pill counts messages (#816) ─────────────────────────────────
//
// The Action Center concatenated approvals + inbox + proposals with no cross-slice dedup, but
// most pending inbox items ARE proposal mirrors (item_kind 'proposal', refs.skill_proposal naming
// the proposal) of the same proposals the third slice lists directly. Measured on a seeded
// instance: 31 inbox items of which 30 were mirrors of 28/29 open proposals — every proposal
// rendered twice, "+N more to triage" over-reported ~2x, and HeroPulse badged "31 inbox" for one
// real channel message. These rails mount the REAL DashboardLiveProvider around the real widgets
// against a seeded api and pin:
//   1. a mirrored proposal renders ONCE (as the actionable proposal row, not the inert mirror);
//   2. an ORPHANED mirror (its proposal gone from the slice) stays visible — degrade, not vanish;
//   3. the HeroPulse inbox pill counts non-mirror messages only.

const PROPOSALS = [
  { id: 'prop-1', slug: 'summarize-logs', description: 'Summarize long logs' },
  { id: 'prop-2', slug: 'triage-errors', description: 'Triage error clusters' },
]
const INBOX = [
  // mirrors of prop-1 / prop-2
  { id: 'inb-m1', channel: 'skills', channel_name: 'skills', message: 'proposal mirror 1',
    sender_id: 's', sender_name: 'skills', classification: 'fyi', confidence: 'high',
    status: 'pending', item_kind: 'proposal', can_reply: false, refs: { skill_proposal: 'prop-1' } },
  { id: 'inb-m2', channel: 'skills', channel_name: 'skills', message: 'proposal mirror 2',
    sender_id: 's', sender_name: 'skills', classification: 'fyi', confidence: 'high',
    status: 'pending', item_kind: 'proposal', can_reply: false, refs: { skill_proposal: 'prop-2' } },
  // an ORPHANED mirror — its proposal is not in the proposals slice
  { id: 'inb-orphan', channel: 'skills', channel_name: 'skills', message: 'orphan mirror',
    sender_id: 's', sender_name: 'skills', classification: 'fyi', confidence: 'high',
    status: 'pending', item_kind: 'proposal', can_reply: false, refs: { skill_proposal: 'prop-gone' } },
  // one REAL channel message
  { id: 'inb-real', channel: 'slack', channel_name: 'general', message: 'hey, can you check the deploy?',
    sender_id: 'u1', sender_name: 'Jordan', classification: 'needs_reply', confidence: 'high',
    status: 'pending', item_kind: 'message', can_reply: true, refs: {} },
]

function mockApi() {
  vi.resetModules()
  vi.doMock('../../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve({ update_available: false }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ items: [] }),
      approvals: () => Promise.resolve([]),
      inboxPending: () => Promise.resolve(INBOX),
      skillProposals: () => Promise.resolve({ proposals: PROPOSALS, lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
    },
  }))
}

async function mount(Widget: 'ActionCenter' | 'HeroPulse') {
  const { DashboardLiveProvider } = await import('../DashboardLive')
  const mod = Widget === 'ActionCenter' ? await import('./ActionCenter') : await import('./HeroPulse')
  const C = (mod as Record<string, any>)[Widget]
  await act(async () => {
    render(
      <DashboardLiveProvider>
        <C navigate={() => {}} />
      </DashboardLiveProvider>,
    )
    await new Promise((res) => setTimeout(res, 0))
  })
}

describe('Action Center dedups proposal mirrors (#816)', () => {
  it('a mirrored proposal renders once, as the actionable proposal row', async () => {
    mockApi()
    await mount('ActionCenter')
    // The proposal rows are present…
    expect(screen.getByText('Skill: summarize-logs')).toBeTruthy()
    expect(screen.getByText('Skill: triage-errors')).toBeTruthy()
    // …their inert mirrors are NOT (mirror rows title as sender/channel 'skills').
    expect(screen.queryByText('proposal mirror 1')).toBeNull()
    expect(screen.queryByText('proposal mirror 2')).toBeNull()
  })

  it('an orphaned mirror stays visible, and the real message is untouched', async () => {
    mockApi()
    await mount('ActionCenter')
    expect(screen.getByText('orphan mirror')).toBeTruthy() // degrade, not vanish
    expect(screen.getByText(/can you check the deploy/)).toBeTruthy()
  })
})

describe('HeroPulse inbox pill counts messages, not proposal mirrors (#816)', () => {
  it('badges 1 for one real message among three mirrors', async () => {
    mockApi()
    await mount('HeroPulse')
    const pill = screen.getByText('inbox').closest('button')
    expect(pill?.textContent).toContain('1')
    expect(pill?.textContent).not.toContain('4')
  })
})
