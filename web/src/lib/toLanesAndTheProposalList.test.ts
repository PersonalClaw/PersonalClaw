import { describe, it, expect } from 'vitest'
import type { InboxItem, SkillProposal } from './api'
import { toLanes } from './attentionLanes'

// ── toLanes and the proposal list ─────────────────────────────────────────────────────────────
//
// The sidebar's pips fold the Inbox and the skill proposals through `toLanes`: a proposal is carded
// as itself while it is in the list, and its own Inbox row stands in for it when it is not, because
// a stale row is better than a missing one on a surface that says what waits on you.

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

describe('toLanes and the proposal list', () => {
  it('keeps a row whose proposal is not in the list — stale degrades to visible', () => {
    const lanes = toLanes([mirror(WRITEUP), mirror(AUTHORING)], [], [], [], [AUTHORING])
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
