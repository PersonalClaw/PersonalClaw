import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { LEARNING_KINDS, SKILL_KINDS, WAITING_KINDS, kindOf } from './needsYouKinds'
import type { NeedsYouRow } from './needsYou'

// Every kind's tag is the one the shared fixture pins (`tests/test_needs_you_kind_tags.py` holds the
// server to it too): a card, Learning, Skills and the Proposals view name a kind one way, and none
// draws an empty tag row.

const FIXTURE = JSON.parse(readFileSync(join(process.cwd(), 'src/test/fixtures/needsYouKinds.json'), 'utf8')) as {
  tags: Record<'learning' | 'skills' | 'waiting', Record<string, string>>
  learning: { variant: string; card: Record<string, unknown> }[]
}

const words = (reg: Record<string, { tag: string }>) =>
  Object.fromEntries(Object.entries(reg).map(([key, value]) => [key, value.tag]))

describe('each kind says one noun, the fixture’s', () => {
  it('learning, skills and the waiting list match the fixture word for word', () => {
    expect(words(LEARNING_KINDS)).toEqual(FIXTURE.tags.learning)
    expect(words(SKILL_KINDS)).toEqual(FIXTURE.tags.skills)
    expect(words(WAITING_KINDS)).toEqual(FIXTURE.tags.waiting)
  })

  it('every pinned card reads its tag off its own variant', () => {
    for (const example of FIXTURE.learning) {
      const row = {
        key: `note:${example.variant}`, origin: 'note', title: '', raisedBy: '', subtitle: '', at: null,
        note: { id: example.variant, item_kind: 'proposal', status: 'pending', message: '', context_summary: '', created_at: 0,
          refs: { learning_proposal: 'lp-1', card: example.card }, sender_name: '', channel_name: '' },
      } as unknown as NeedsYouRow
      expect(kindOf(row).tag, example.variant).toBe(FIXTURE.tags.learning[String(example.card.variant)])
    }
  })

  it('a merge and a conflict a loop asks about are their own kinds, not "Loop"', () => {
    const loop = (brief: Record<string, unknown>) => ({
      key: 'waiting:x', origin: 'waiting', title: '', raisedBy: '', subtitle: '',
      item: { id: 'x', kind: 'loop_question', brief },
    }) as unknown as NeedsYouRow
    expect(kindOf(loop({ merge: {} })).tag).toBe('Ready to merge')
    expect(kindOf(loop({ conflict: {} })).tag).toBe('Conflict')
    expect(kindOf(loop({ question: 'May I?' })).tag).toBe('Loop')
  })
})
