import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { api, type SkillProposal } from '../../lib/api'
import { SkillProposals } from './SkillProposals'
import { acceptedLabel } from './skillMeta'

// ── Issue 433: the collapsed proposal card must name the skill it REFINES ─────────────────────
//
// The card header printed `proposal.slug` and nothing read `proposal.refine_target`. A refine's
// slug is the proposal's own name and need NOT be the entity being changed, so when the two differ
// the card was titled after something that is not an installed skill at all — while the inbox's
// panel, reading the same proposal, did name the target. The two surfaces disagreed.
//
// Why it stayed hidden for so long: measured live, 28 of 29 pending proposals happened to use the
// target AS the slug, so they read plausibly. That is also why the first rail below uses a
// MISMATCHED pair — a fixture whose slug equals its target cannot fail the old code.
//
// And why the collapsed row specifically: it carries Accept/Reject. A reviewer who accepts without
// expanding acts on a name that was not the one affected. The expanded body already named the
// target (`Change to …`) — that was never the gap.

const proposal = (over: Partial<SkillProposal> = {}): SkillProposal => ({
  id: 'verify-defaults-and-avoid-double-banking-abc123',
  slug: 'verify-defaults-and-avoid-double-banking',
  description: 'Refined after you corrected this turn',
  triggers: '',
  kind: 'refine',
  refine_target: 'knowledge-grounding',
  trigger: 'correction',
  session_key: 'sess:1',
  created_at: '2026-08-25T12:00:00+00:00',
  status: 'pending',
  procedure_preview: 'When this skill applies…',
  ...over,
})

describe('a refine proposal card names its target (issue 433)', () => {
  afterEach(() => vi.restoreAllMocks())

  it('names the refined skill on the COLLAPSED row, from the list payload alone', async () => {
    // No `skillProposalDetail` mock on purpose: the summary already carries `refine_target`
    // (`skills/proposals.py:summary`), so naming the target must not cost a detail fetch — a card
    // that had to expand to become honest would leave the Accept button beside the wrong name.
    const detailSpy = vi.spyOn(api, 'skillProposalDetail')
    vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [proposal()], lastReview: null })

    render(<SkillProposals />)

    await waitFor(() => expect(screen.getByText('refines knowledge-grounding')).toBeTruthy())
    // The slug stays the row's identity — whether the target is still installed is only knowable
    // from the detail fetch, so the collapsed row reports the claim, not a resolved outcome.
    expect(screen.getByText('verify-defaults-and-avoid-double-banking')).toBeTruthy()
    expect(detailSpy).not.toHaveBeenCalled()
  })

  it('makes two refinements of DIFFERENT skills read differently', async () => {
    // The user-visible defect: two cards that read identically. Same slug, different target — the
    // pair the old header collapsed into one sentence.
    vi.spyOn(api, 'skillProposals').mockResolvedValue({
      proposals: [
        proposal({ id: 'p1', slug: 'shared-slug', refine_target: 'knowledge-grounding' }),
        proposal({ id: 'p2', slug: 'shared-slug', refine_target: 'release-flow' }),
      ],
      lastReview: null,
    })

    render(<SkillProposals />)

    await waitFor(() => expect(screen.getByText('refines knowledge-grounding')).toBeTruthy())
    expect(screen.getByText('refines release-flow')).toBeTruthy()
  })

  it('says nothing extra for a kind=new proposal, which refines nothing', async () => {
    // The vacuity floor: the phrase must be gated on being a refine WITH a target, not printed
    // unconditionally — "refines" on a proposal that creates a skill would be a false claim.
    vi.spyOn(api, 'skillProposals').mockResolvedValue({
      proposals: [proposal({ kind: 'new', refine_target: '', trigger: '' })],
      lastReview: null,
    })

    render(<SkillProposals />)

    await screen.findByText('verify-defaults-and-avoid-double-banking')
    expect(screen.queryByText(/^refines /)).toBeNull()
  })

  it('omits the phrase for a refine that carries no target', async () => {
    vi.spyOn(api, 'skillProposals').mockResolvedValue({
      proposals: [proposal({ refine_target: '' })],
      lastReview: null,
    })

    render(<SkillProposals />)

    await screen.findByText('verify-defaults-and-avoid-double-banking')
    expect(screen.queryByText(/^refines /)).toBeNull()
  })
})

describe('accepted-label is shared by both accepting surfaces', () => {
  it('carries the version when there is one, and omits it when there is not', () => {
    // One sentence for one decision. The inbox panel and the Skills card answer the same proposal
    // through the same endpoint, so this is the thing that keeps them from drifting apart.
    expect(acceptedLabel('release-flow', 2)).toBe('Accepted → release-flow · refinement v2')
    expect(acceptedLabel('auto/release-flow', 0)).toBe('Accepted → auto/release-flow')
    expect(acceptedLabel('auto/release-flow')).toBe('Accepted → auto/release-flow')
  })
})
