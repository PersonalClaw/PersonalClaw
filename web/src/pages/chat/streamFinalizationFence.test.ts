import { describe, expect, it } from 'vitest'
import type { HistMsg } from './chatTypes'
import { StreamFinalizationFence } from './streamFinalizationFence'

const user = (content: string, ts?: string): HistMsg => ({ role: 'user', content, ts })
const assistant = (content: string, ts?: string): HistMsg => ({ role: 'assistant', content, ts })

describe('StreamFinalizationFence', () => {
  it('rejects terminal text replay when refresh finalizes the same latest user turn', () => {
    const fence = new StreamFinalizationFence()
    const seed = [
      user('earlier question', '2026-09-24T08:00:00Z'),
      assistant('earlier answer', '2026-09-24T08:00:01Z'),
      user('current question', '2026-09-24T08:01:00Z'),
    ]
    const refreshed = [...seed, assistant('final answer', '2026-09-24T08:01:01Z')]

    expect(fence.allows('chat_chunk')).toBe(true)
    expect(fence.armFromRefresh(seed, refreshed)).toBe(true)
    expect(fence.allows('chat_chunk')).toBe(false)

    // Non-text frames still refine the hydrated turn. In particular, tool cards
    // and their results must survive a multi-segment answer's finalization race.
    expect(fence.allows('tool_call')).toBe(true)
    expect(fence.allows('tool_result')).toBe(true)
    expect(fence.allows('activity_event')).toBe(true)

    fence.finishTurn()
    expect(fence.allows('chat_chunk')).toBe(true)
  })

  it('does not arm for a stale-history backfill before the current user tail', () => {
    const fence = new StreamFinalizationFence()
    const firstUser = user('first question', '2026-09-24T08:00:00Z')
    const firstAnswer = assistant('first answer', '2026-09-24T08:00:01Z')
    const currentUser = user('current question', '2026-09-24T08:02:00Z')
    const seed = [firstUser, firstAnswer, currentUser]
    const refreshed = [
      firstUser,
      firstAnswer,
      { role: 'tool', content: 'late persisted tool result', ts: '2026-09-24T08:00:02Z' },
      currentUser,
      assistant('current answer', '2026-09-24T08:02:01Z'),
    ]

    expect(fence.armFromRefresh(seed, refreshed)).toBe(false)
    expect(fence.allows('chat_chunk')).toBe(true)
  })

  it('does not arm for same-count edits or variant changes', () => {
    const original = user('original', '2026-09-24T08:00:00Z')
    expect(new StreamFinalizationFence().armFromRefresh(
      [original],
      [{ ...original, content: 'edited' }],
    )).toBe(false)

    const variantSeed: HistMsg[] = [{
      role: 'user',
      content: 'original',
      ts: '2026-09-24T08:00:00Z',
      variants: [{ content: 'original', ts: '2026-09-24T08:00:00Z' }, { content: 'edited' }],
      variant_idx: 0,
    }]
    const selectedVariant = [{ ...variantSeed[0], content: 'edited', variant_idx: 1 }]

    const variantFence = new StreamFinalizationFence()
    expect(variantFence.armFromRefresh(variantSeed, selectedVariant)).toBe(false)
    expect(variantFence.allows('chat_chunk')).toBe(true)
  })

  it('does not arm when an older assistant appears while a newer user turn streams', () => {
    const fence = new StreamFinalizationFence()
    const firstUser = user('first question', '2026-09-24T08:00:00Z')
    const currentUser = user('current question', '2026-09-24T08:02:00Z')
    const seed = [firstUser, currentUser]
    const refreshed = [
      firstUser,
      assistant('answer to first question', '2026-09-24T08:00:01Z'),
      currentUser,
    ]

    expect(fence.armFromRefresh(seed, refreshed)).toBe(false)
    expect(fence.allows('chat_chunk')).toBe(true)
  })

  it('does not arm when refresh added no finalized assistant after the user tail', () => {
    const fence = new StreamFinalizationFence()
    const seed = [user('question', '2026-09-24T08:00:00Z')]

    expect(fence.armFromRefresh(seed, [...seed])).toBe(false)
    expect(fence.armFromRefresh(seed, [...seed, { role: 'tool', content: 'working' }])).toBe(false)
    expect(fence.allows('chat_chunk')).toBe(true)
  })

  it('tracks sequential turns independently', () => {
    const fence = new StreamFinalizationFence()
    const firstUser = user('one', '2026-09-24T08:00:00Z')
    const firstAnswer = assistant('answer one', '2026-09-24T08:00:01Z')
    expect(fence.armFromRefresh([firstUser], [firstUser, firstAnswer])).toBe(true)
    fence.finishTurn()

    fence.startTurn()

    expect(fence.allows('chat_chunk')).toBe(true)
    const secondUser = user('two', '2026-09-24T08:01:00Z')
    const secondAnswer = assistant('answer two', '2026-09-24T08:01:01Z')
    const secondSeed = [firstUser, firstAnswer, secondUser]
    expect(fence.armFromRefresh(secondSeed, [...secondSeed, secondAnswer])).toBe(true)
    expect(fence.allows('chat_chunk')).toBe(false)
  })
})
