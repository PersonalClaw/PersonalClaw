// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import type { InboxItem, SkillProposal } from '../../lib/api'
import { proposalSummary, proposalTitle, toLanes } from '../../lib/attentionLanes'

// ── Mission Control names each skill proposal, and decides it on its card ─────────────────────────
//
// Measured on a real week: Your turn held three skill proposals as three identical "Refine a skill"
// cards — the Inbox row's first line — with no skill named and nothing to press, under a page that
// says "Approve, reject, and answer from here". Home's To triage named each skill and offered Accept
// and Reject, because it reads the proposal list. Both surfaces now read that list, and name and
// decide a proposal the same way. The lane derivation here is the REAL one, so what is asserted is
// the card a user sees.

const inboxOpen = vi.fn()
const skillProposals = vi.fn()
const acceptSkillProposal = vi.fn()
const rejectSkillProposal = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    inboxOpen: (...a: unknown[]) => inboxOpen(...a),
    approvals: () => Promise.resolve([]),
    chatSessions: () => Promise.resolve([]),
    uLoops: () => Promise.resolve([]),
    workflowRuns: () => Promise.resolve({ runs: [], total: 0, limit: 200, offset: 0 }),
    skillProposals: (...a: unknown[]) => skillProposals(...a),
    acceptSkillProposal: (...a: unknown[]) => acceptSkillProposal(...a),
    rejectSkillProposal: (...a: unknown[]) => rejectSkillProposal(...a),
  },
}))

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

import { MissionControl } from './MissionControl'

function proposal(id: string, slug: string, description: string): SkillProposal {
  return {
    id, slug, description, triggers: '', kind: 'refine', refine_target: slug, session_key: 's',
    created_at: '2026-09-30T06:38:15Z', status: 'pending', procedure_preview: '',
  }
}

/** The Inbox row a proposal raises, as `skills.proposals._surface_in_inbox` writes it. */
function mirror(p: SkillProposal): InboxItem {
  return {
    id: `row_${p.id}`, channel: 'skills', channel_name: 'skills', sender_id: 'skills',
    sender_name: 'skills', message: `Refine a skill\n\n${p.slug} — ${p.description}`,
    classification: 'needs_reply', confidence: 'high', status: 'pending', item_kind: 'proposal',
    created_at: 100, refs: { skill_proposal: p.id, session: p.session_key },
  } as InboxItem
}

const WRITEUP = proposal('proposal_a', 'incident-writeup', 'Refined after you corrected this turn')
const AUTHORING = proposal('proposal_b', 'document-authoring', 'Refined after a step failed')

function yourTurn(): HTMLElement {
  return screen.getByRole('region', { name: 'Mission Control' }).querySelector(
    '[aria-labelledby="mission-control-lane-your-turn"]',
  ) as HTMLElement
}

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  inboxOpen.mockResolvedValue([mirror(WRITEUP), mirror(AUTHORING)])
  skillProposals.mockResolvedValue({ proposals: [WRITEUP, AUTHORING], lastReview: null })
  acceptSkillProposal.mockResolvedValue({ ok: true })
  rejectSkillProposal.mockResolvedValue({ ok: true })
})

describe("Mission Control's Your turn and a skill proposal", () => {
  it('🔴 names each skill, as Home does, and shows each proposal once', async () => {
    render(<MissionControl />)
    const lane = await waitFor(() => {
      const el = yourTurn()
      expect(within(el).getByText(proposalTitle(WRITEUP))).toBeTruthy()
      return el
    })
    expect(within(lane).getByText(proposalTitle(AUTHORING))).toBeTruthy()
    expect(within(lane).getByText(proposalSummary(WRITEUP))).toBeTruthy()
    // The rows the proposals raised are not a second card each.
    expect(within(lane).queryByText('Refine a skill')).toBeNull()
    expect(within(lane).getAllByRole('listitem')).toHaveLength(2)
  })

  it('🔴 accepts and rejects on the card, through the calls Home makes', async () => {
    const user = userEvent.setup()
    render(<MissionControl />)
    await waitFor(() => expect(within(yourTurn()).getByText(proposalTitle(WRITEUP))).toBeTruthy())

    await user.click(screen.getByRole('button', { name: new RegExp(`^Accept ${proposalTitle(WRITEUP)}`) }))
    await waitFor(() => expect(acceptSkillProposal).toHaveBeenCalledWith('proposal_a'))
    expect(await screen.findByText('Accepted — the skill is saved.')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: new RegExp(`^Reject ${proposalTitle(AUTHORING)}`) }))
    await waitFor(() => expect(rejectSkillProposal).toHaveBeenCalledWith('proposal_b'))
  })

  it('says why an Accept failed, and keeps the card to try again', async () => {
    const user = userEvent.setup()
    acceptSkillProposal.mockRejectedValue(new Error('the skill folder is read-only'))
    render(<MissionControl />)
    await waitFor(() => expect(within(yourTurn()).getByText(proposalTitle(WRITEUP))).toBeTruthy())

    await user.click(screen.getByRole('button', { name: new RegExp(`^Accept ${proposalTitle(WRITEUP)}`) }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('the skill folder is read-only')
    expect(screen.getByRole('button', { name: new RegExp(`^Accept ${proposalTitle(WRITEUP)}`) })).toBeTruthy()
  })
})

describe('toLanes and the proposal list', () => {
  it('keeps a row whose proposal is not in the list — stale degrades to visible', () => {
    const lanes = toLanes([mirror(WRITEUP), mirror(AUTHORING)], [], [], [], [], [AUTHORING])
    const cards = lanes['your-turn']
    expect(cards.map((c) => c.origin).sort()).toEqual(['inbox', 'proposal'])
    const card = cards.find((c) => c.origin === 'proposal')
    expect(card?.title).toBe('Skill: document-authoring')
    expect(card?.subtitle).toBe('Refined after a step failed')
  })

  it('with no proposal list, every proposal still shows as its row', () => {
    expect(toLanes([mirror(WRITEUP)], [])['your-turn'].map((c) => c.origin)).toEqual(['inbox'])
  })
})
